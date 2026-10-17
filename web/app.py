"""The demo page's server (SPEC § 13): FastAPI over the export document and the telemetry log.

Endpoints:
- `GET /` and `GET /stage`: the page shell (`/stage` opens the Stage, `?deck=` its
  playlist); `GET /static/...`: its scripts and styles (from `web/static/` only).
- `GET /api/export`: the export document (`docs/export-schema.md`), read from the export file.
- `GET /api/telemetry`: raw telemetry rows, filtered; a live view polls it with `after_seq`.
- `GET /api/spans`: one row per span (`TelemetryReader.spans`).
- `GET /api/times`: per span name, count and durations (`friday.app.telemetry_report.step_times`).
- `GET /api/config`: the run constants the live view reads, from the RunSpec.
- `GET /apps/{pass_id}`: one pass's built HTML, under a strict CSP that also sandboxes it. Model-written
  code: `pass_id` must be a pass of the export with an artifact, and the file must sit inside its folder.
  The served HTML carries one prepended script (ESC_RELAY) that hands Escape back to the Stage.

Run: `uv run --extra web python -m web.app [--spec run.yml --db friday.db --export export.json
--artifacts artifacts]` or `--root <run root>`; with no run path it serves the fixtures under `web/fixtures/`.
"""

from __future__ import annotations

import argparse
import json
import re
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from friday.adapters.telemetry import SqliteTelemetry, connect_telemetry
from friday.app.telemetry_report import step_times
from friday.config import RunSpec, load_spec
from friday.ports.telemetry import Scope, TelemetryRow
from web.posters import Posters

WEB = Path(__file__).resolve().parent
STATIC = WEB / "static"
FIXTURES = WEB / "fixtures"

# Model-written code runs under this policy: no network, no fetch, no frames, an opaque origin.
APP_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; "
           "sandbox allow-scripts; form-action 'none'; base-uri 'none'; frame-ancestors 'self'")
# The page itself loads only its own files.
PAGE_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
            "frame-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
# Prepended to each served app: forwards Escape to the page (a fixed message, nothing else), so a presenter
# who focused an app on the Stage gets the page's keys back. The app keeps the event; the sandbox is unchanged.
ESC_RELAY = (b"<script>window.addEventListener('keydown',function(e){if(e.key==='Escape')"
             b"window.parent.postMessage('friday:stage-escape','*');},true);</script>")
_HEAD = re.compile(rb"<head[^>]*>|<html[^>]*>|<!doctype[^>]*>", re.IGNORECASE)
PASS_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
MAX_ROWS = 5000   # one page of /api/telemetry; a reader pages on with after_seq


def row_json(r: TelemetryRow) -> dict[str, Any]:
    """One telemetry row as JSON-ready data (scope flattened, duration added for closed spans)."""
    return {"seq": r.seq, "kind": r.kind, "name": r.name, "run": r.scope.run, "round": r.scope.round,
            "pass_id": r.scope.pass_id, "at": r.at, "span_id": r.span_id, "parent_id": r.parent_id,
            "ended_at": r.ended_at, "ok": r.ok, "error": r.error, "value": r.value, "unit": r.unit,
            "attrs": r.attrs, "duration_s": r.duration_s}


class _ExportFile:
    """The export document, re-read when the file changes (the dry run rewrites it)."""

    def __init__(self, path: Path) -> None:
        self.path, self._stamp, self._doc = path, None, None
        self._lock = threading.Lock()

    def doc(self) -> dict[str, Any]:
        """The current document; raises HTTPException 404 while it is not written."""
        with self._lock:
            try:
                st = self.path.stat()
            except FileNotFoundError:
                raise HTTPException(404, "export not written yet") from None
            stamp = (st.st_mtime_ns, st.st_size)
            if stamp != self._stamp:
                self._doc = json.loads(self.path.read_text())
                self._stamp = stamp
            return self._doc


class _Log:
    """The telemetry log, opened on first use (the run may create it after the page starts)."""

    def __init__(self, path: Path, busy_timeout_ms: int) -> None:
        self.path, self.busy_timeout_ms = path, busy_timeout_ms
        self._reader: SqliteTelemetry | None = None
        self.lock = threading.Lock()

    def reader(self) -> SqliteTelemetry | None:
        if self._reader is None and self.path.exists():
            conn = connect_telemetry(self.path, busy_timeout_ms=self.busy_timeout_ms)
            self._reader = SqliteTelemetry(conn, Scope("main"))
        return self._reader


def with_esc_relay(html: bytes) -> bytes:
    """`html` with ESC_RELAY placed after its first <head>, <html> or doctype tag (else at the start)."""
    m = _HEAD.search(html)
    at = m.end() if m else 0
    return html[:at] + ESC_RELAY + html[at:]


def create_app(spec: RunSpec, db_path: str | Path, export_path: str | Path, artifacts_dir: str | Path,
               *, render_posters: bool = False) -> FastAPI:
    """The demo app over one run's telemetry log, export file and artifacts folder. With `render_posters`, a
    background thread renders a still poster per built app (`web/posters.py`) for the gallery's frozen cards."""
    export = _ExportFile(Path(export_path))
    log = _Log(Path(db_path), spec.telemetry.busy_timeout_ms)
    artifacts = Path(artifacts_dir).resolve()
    app = FastAPI(title="Friday", docs_url=None, redoc_url=None, openapi_url=None)

    def built_ids() -> list[str]:
        return [p["id"] for p in export.doc()["passes"] if p.get("artifact_path")]

    # newest passes first: the gallery leads with the latest round
    posters = Posters(Path(export_path).parent / "posters", artifacts, lambda: built_ids()[::-1])
    if render_posters:
        posters.start()

    @app.middleware("http")
    async def headers(request: Request, call_next):  # noqa: ANN001, ANN202 - FastAPI middleware signature
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if not request.url.path.startswith("/apps/"):
            response.headers["Content-Security-Policy"] = PAGE_CSP
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", media_type="text/html")

    @app.get("/stage")
    def stage() -> FileResponse:
        return FileResponse(STATIC / "index.html", media_type="text/html")

    @app.get("/api/export")
    def get_export() -> JSONResponse:
        return JSONResponse(export.doc())

    @app.get("/api/cells")
    def get_cells() -> dict[str, str]:
        """Request id -> "family / mode" from the goal minter's grid file (generator-side; the UI is no agent of
        the loop, so it may show the cell). Empty when the grid file is absent (the fixtures)."""
        path = WEB.parent / Path(spec.stream.path).parent / "cells.json"
        if not path.is_file():
            return {}
        grid = json.loads(path.read_text()).get("requests", {})
        return {rid: " / ".join(cell) for rid, cell in grid.items() if isinstance(cell, list)}

    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        return {"live_poll_ms": spec.telemetry.live_poll_ms, "alpha": spec.gate.alpha,
                "admit_wealth": 1.0 / spec.gate.alpha, "drift_round": spec.drift.round, "rounds": spec.run.rounds,
                "spend_cap_usd": spec.run.spend_cap_usd}

    @app.get("/api/telemetry")
    def get_telemetry(after_seq: int = Query(0, ge=0), run: str | None = None, round: int | None = None,  # noqa: A002
                      pass_id: str | None = None, name_prefix: str | None = None,
                      limit: int = Query(MAX_ROWS, ge=1, le=MAX_ROWS)) -> dict[str, Any]:
        with log.lock:
            reader = log.reader()
            if reader is None:
                return {"available": False, "rows": [], "last_seq": after_seq, "more": False}
            rows = reader.rows(after_seq=after_seq, run=run, round=round, pass_id=pass_id, name_prefix=name_prefix,
                               limit=limit)
        return {"available": True, "rows": [row_json(r) for r in rows],
                "last_seq": rows[-1].seq if rows else after_seq, "more": len(rows) == limit}

    @app.get("/api/spans")
    def get_spans(run: str | None = None, round: int | None = None, pass_id: str | None = None,  # noqa: A002
                  name_prefix: str | None = None) -> dict[str, Any]:
        with log.lock:
            reader = log.reader()
            spans = [] if reader is None else reader.spans(run=run, round=round, pass_id=pass_id,
                                                           name_prefix=name_prefix)
        return {"available": reader is not None, "spans": [row_json(s) for s in spans]}

    @app.get("/api/times")
    def get_times(run: str | None = None, round: int | None = None) -> dict[str, Any]:  # noqa: A002
        with log.lock:
            reader = log.reader()
            spans = [] if reader is None else reader.spans(run=run, round=round)
        return {"available": reader is not None, "times": step_times(spans)}

    @app.get("/apps/{pass_id}")
    def get_app(pass_id: str) -> Response:
        if not PASS_ID.match(pass_id) or pass_id in (".", ".."):
            raise HTTPException(404, "unknown pass")
        known = {p["id"] for p in export.doc()["passes"] if p.get("artifact_path")}
        if pass_id not in known:
            raise HTTPException(404, "unknown pass")
        path = (artifacts / pass_id / "index.html").resolve()
        if not path.is_relative_to(artifacts) or not path.is_file():
            raise HTTPException(404, "no artifact for this pass")
        return Response(with_esc_relay(path.read_bytes()), media_type="text/html; charset=utf-8",
                        headers={"Content-Security-Policy": APP_CSP, "Cross-Origin-Resource-Policy": "same-origin"})

    @app.get("/posters/{pass_id}")
    def get_poster(pass_id: str) -> Response:
        if not PASS_ID.match(pass_id) or pass_id in (".", ".."):
            raise HTTPException(404, "unknown pass")
        if pass_id not in set(built_ids()):
            raise HTTPException(404, "unknown pass")
        path = posters.path(pass_id)
        if not path.is_file():
            raise HTTPException(404, "no poster yet")
        return FileResponse(path, media_type="image/jpeg")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


REPO_SPEC = WEB.parent / "run.yml"
EXPORT_FILE = "export.json"           # the runner writes it under the run root


def resolve_paths(args: argparse.Namespace) -> tuple[Path, Path, Path, Path]:
    """Returns (spec, db, export, artifacts): the fixtures only when no run path is given; otherwise the
    repository run.yml (unless --spec) and, under --root, that run root's db, export.json and artifacts."""
    if args.root is None and args.db is None and args.export is None and args.artifacts is None:
        return (args.spec or FIXTURES / "run.yml", FIXTURES / "telemetry.db", FIXTURES / "export.json",
                FIXTURES / "artifacts")
    spec = args.spec or REPO_SPEC
    under: dict[str, Path] = {}
    if args.root is not None:
        paths = load_spec(spec).paths
        under = {"db": args.root / paths.db, "export": args.root / EXPORT_FILE,
                 "artifacts": args.root / paths.artifacts}
    out = []
    for what in ("db", "export", "artifacts"):
        given = getattr(args, what)
        if given is None and what not in under:
            raise SystemExit(f"--{what} is required without --root")
        out.append(given if given is not None else under[what])
    return (spec, *out)


def main(argv: list[str] | None = None) -> None:
    """Serves the demo page; with no run path given it serves the fixtures under web/fixtures/."""
    import uvicorn

    ap = argparse.ArgumentParser(description="Friday demo page")
    ap.add_argument("--spec", type=Path, default=None)
    ap.add_argument("--root", type=Path, default=None, help="a run root: its db, export.json and artifacts")
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--export", type=Path, default=None)
    ap.add_argument("--artifacts", type=Path, default=None)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args(argv)
    spec, db, export, artifacts = resolve_paths(args)
    app = create_app(load_spec(spec), db, export, artifacts, render_posters=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")

if __name__ == "__main__":
    main()
