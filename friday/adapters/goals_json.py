"""GoalSource and TagVocabulary over `goals/*.json` (SPEC § 4, § 14).

`stream.json` and `held_out.json` are lists of requests, frozen at minting. The held-out file is opened only
when `held_out()` is called, so nothing at training time reads it. `tags.json` holds the tag vocabulary (tag,
gloss, minted round, in minting order) and the tags frozen on each request; it is rewritten on every change.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from friday.domain.types import Check, Regime, Request, TagGloss


def request_to_json(r: Request) -> dict[str, Any]:
    """The JSON object a request is stored as (tags live in tags.json, not here)."""
    return {
        "id": r.id, "text": r.text, "round": r.round, "held_out": r.held_out, "contract": dict(r.contract),
        "checks": {reg.value: [{"id": c.id, "steps": list(c.steps), "expect": dict(c.expect)} for c in cs]
                   for reg, cs in r.checks.items()},
    }


def request_from_json(obj: Mapping[str, Any], tags: frozenset[str] = frozenset()) -> Request:
    """The request a stored object holds; raises KeyError or ValueError on a malformed object."""
    checks = {Regime(reg): tuple(Check(str(c["id"]), tuple(c["steps"]), dict(c["expect"])) for c in cs)
              for reg, cs in obj["checks"].items()}
    return Request(str(obj["id"]), str(obj["text"]), tags, {str(k): str(v) for k, v in obj["contract"].items()},
                   checks, held_out=bool(obj["held_out"]), round=obj["round"])


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def write_requests(path: str | Path, requests: Sequence[Request]) -> None:
    """Writes `requests` in order as a JSON list; raises if the file exists (requests are frozen once minted)."""
    p = Path(path)
    if p.exists():
        raise FileExistsError(f"{p} exists; minted requests are frozen")
    _write_atomic(p, json.dumps([request_to_json(r) for r in requests], indent=1) + "\n")


def write_stub(stub_dir: str | Path, request_id: str, html: str) -> Path:
    """Writes one contract stub as `<stub_dir>/<request_id>.html` and returns its path."""
    p = Path(stub_dir) / f"{request_id}.html"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html)
    return p


def _load(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text())
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected a list of requests")
    return raw


class JsonTagVocabulary:
    """The tag vocabulary in `tags.json`."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._glosses: list[TagGloss] = []
        self._tags: dict[str, frozenset[str]] = {}
        if self._path.exists():
            raw = json.loads(self._path.read_text())
            self._glosses = [TagGloss(str(g["tag"]), str(g["gloss"]), int(g["minted_round"])) for g in raw["tags"]]
            self._tags = {k: frozenset(v) for k, v in raw["requests"].items()}

    def glosses(self) -> Sequence[TagGloss]:
        return list(self._glosses)

    def add(self, gloss: TagGloss) -> None:
        if any(g.tag == gloss.tag for g in self._glosses):
            raise ValueError(f"tag {gloss.tag!r} exists; tags are never renamed or merged")
        self._glosses.append(gloss)
        self._save()

    def tags_of(self, request_id: str) -> frozenset[str] | None:
        return self._tags.get(request_id)

    def set_tags(self, tags: Mapping[str, frozenset[str]]) -> None:
        clash = sorted(set(tags) & set(self._tags))
        if clash:
            raise ValueError(f"requests already tagged: {clash}")
        known = {g.tag for g in self._glosses}
        unknown = sorted({t for ts in tags.values() for t in ts} - known)
        if unknown:
            raise ValueError(f"tags not in the vocabulary: {unknown}")
        self._tags.update({k: frozenset(v) for k, v in tags.items()})
        self._save()

    def _save(self) -> None:
        body = {"tags": [{"tag": g.tag, "gloss": g.gloss, "minted_round": g.minted_round} for g in self._glosses],
                "requests": {k: sorted(v) for k, v in sorted(self._tags.items())}}
        _write_atomic(self._path, json.dumps(body, indent=1) + "\n")


class JsonGoalSource:
    """The frozen goal stream and held-out set, with tags as stored so far in the vocabulary."""

    def __init__(self, stream_path: str | Path, held_out_path: str | Path,
                 vocabulary: JsonTagVocabulary | None = None) -> None:
        self._stream_path, self._held_path = Path(stream_path), Path(held_out_path)
        self._vocab = vocabulary
        self._stream: list[dict[str, Any]] | None = None

    def _tags(self, request_id: str) -> frozenset[str]:
        got = self._vocab.tags_of(request_id) if self._vocab is not None else None
        return got if got is not None else frozenset()

    def round(self, n: int) -> Sequence[Request]:
        if self._stream is None:
            self._stream = _load(self._stream_path)
        return [request_from_json(o, self._tags(o["id"])) for o in self._stream
                if o["round"] == n and not o["held_out"]]

    def held_out(self) -> Sequence[Request]:
        return [request_from_json(o, self._tags(o["id"])) for o in _load(self._held_path) if o["held_out"]]


class JsonlMintJournal:
    """The goal minter's journal: one JSON line per minted position (request, family, drafted text, stub,
    report rows), appended and flushed as the position completes. A rerun reads it and skips those positions.
    Conforms to `friday.roles.goal_minter.MintJournal`; holds no import of it (adapters sit below roles)."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def minted(self) -> Mapping[str, Any]:
        """The journaled positions by request id; raises ValueError on a malformed line or a repeated id."""
        from types import SimpleNamespace

        out: dict[str, Any] = {}
        if not self._path.exists():
            return out
        for n, line in enumerate(self._path.read_text().splitlines(), start=1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
                req = request_from_json(obj["request"])
                row = SimpleNamespace(request=req, family=str(obj["family"]), draft_text=str(obj["draft_text"]),
                                      stub=str(obj["stub"]), report=tuple(obj["report"]))
            except (ValueError, KeyError, TypeError) as e:
                raise ValueError(f"{self._path}:{n}: malformed journal line ({e})") from None
            if req.id in out:
                raise ValueError(f"{self._path}:{n}: {req.id} journaled twice")
            out[req.id] = row
        return out

    def record(self, minted: Any) -> None:
        """Appends one minted position as a single line and flushes it to disk."""
        line = json.dumps({"request": request_to_json(minted.request), "family": minted.family,
                           "draft_text": minted.draft_text, "stub": minted.stub,
                           "report": list(minted.report)}) + "\n"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
