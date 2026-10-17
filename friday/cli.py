"""`friday` command line: doctor, mint-goals, tag-round, run, steer, telemetry, web, readout, export."""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from friday.config import load_spec


def _doctor(args: argparse.Namespace) -> int:
    """Reports key presence (never values) and how each role's pin resolves against /v1/models."""
    from friday.adapters.crusoe import CrusoeClient
    from friday.adapters.memory import MemorySpendLog
    from friday.adapters.telemetry import MemoryTelemetry
    from friday.app.pins import resolve
    from friday.ports.telemetry import Scope

    spec = load_spec(args.spec)
    for env in (spec.models.endpoint.api_key_env, spec.search.api_key_env):
        print(f"{env}: {'present' if os.environ.get(env) else 'MISSING'}")
    # doctor makes no chat call (list_models opens no span), so no telemetry row is written or lost
    client = CrusoeClient(spec.models, spec.transport, MemorySpendLog(), telemetry=MemoryTelemetry(Scope("main")))
    served = client.list_models()
    for role, res in resolve(spec.models.roles, served).items():
        note = f"  (fallback; wanted {', '.join(res.wanted)})" if res.replaced else ""
        print(f"{role:12s} -> {res.model}{note}")
    print(f"run.yml hash {spec.spec_hash}")
    return 0


def _goal_paths(spec, goals_dir: str | None) -> tuple[Path, Path, Path, Path]:
    """(stream, held-out, tags, stubs dir): run.yml's paths, or the same file names under `goals_dir`."""
    s = spec.stream
    paths = [Path(s.path), Path(s.held_out_path), Path(s.tags_path)]
    if goals_dir is not None:
        paths = [Path(goals_dir) / p.name for p in paths]
    return paths[0], paths[1], paths[2], paths[0].parent / "stubs"


def _open_telemetry(spec):
    """This process's run telemetry log over `paths.db` (docs/telemetry.md)."""
    from friday.adapters.telemetry import open_run_telemetry

    return open_run_telemetry(spec.paths.db, "main", busy_timeout_ms=spec.telemetry.busy_timeout_ms)


def _bound_client(spec, roles: tuple[str, ...], telemetry):
    """A metered Crusoe client with `roles` bound to their resolved pins, and its spend log."""
    from friday.adapters.crusoe import CrusoeClient
    from friday.adapters.memory import MemorySpendLog
    from friday.app.pins import resolve

    spend = MemorySpendLog()
    client = CrusoeClient(spec.models, spec.transport, spend, telemetry=telemetry)
    resolved = resolve({r: spec.models.roles[r] for r in roles}, client.list_models())
    client.bind({r: res.model for r, res in resolved.items()})
    return client, spend


def _mint_goals(args: argparse.Namespace) -> int:
    """Mints the goal stream and held-out set (or only the `--only` ids, into `--goals-dir`) and writes them
    with their stubs. Each minted position is journaled as it completes; a rerun after a failure resumes from
    the journal, and the frozen stream and held-out files are written only when every position minted."""
    from friday.adapters import goals_json
    from friday.adapters.playwright_browser import PlaywrightBrowser
    from friday.app.goals import build_minter
    from friday.roles.goal_minter import MintedGoals, MintFailed, grid_cells, mark_incoherent, plan_slots
    from friday.roles.prompts import PromptBook

    spec = load_spec(args.spec)
    if args.only and args.goals_dir is None:
        raise SystemExit("--only needs --goals-dir: a partial mint never writes run.yml's frozen goal files")
    stream_p, held_p, _, stubs = _goal_paths(spec, args.goals_dir)
    report_p = stream_p.parent / "mint_report.json"
    for p in (stream_p, held_p):
        if p.exists():
            raise SystemExit(f"{p} exists; minted requests are frozen")
    telemetry = _open_telemetry(spec)
    client, spend = _bound_client(spec, ("goal_minter",), telemetry)
    browser = PlaywrightBrowser(spec.eval.action_timeout_ms, spec.eval.load_timeout_ms, telemetry=telemetry)

    def write_stub(request_id: str, html: str) -> str:
        return str(goals_json.write_stub(stubs, request_id, html))

    def sink(minted: MintedGoals) -> None:
        for rid, html in minted.stubs.items():
            goals_json.write_stub(stubs, rid, html)
        goals_json.write_requests(stream_p, minted.stream)
        goals_json.write_requests(held_p, minted.held_out)
        grid = json.loads(cells_p.read_text())
        grid["requests"] = {r.id: list(r.cell) for r in (*minted.stream, *minted.held_out) if r.cell is not None}
        cells_p.write_text(json.dumps(grid, indent=1) + "\n")
        report_p.write_text(json.dumps(minted.report, indent=1) + "\n")

    book = PromptBook(Path(spec.paths.prompts))
    cells_p = stream_p.parent / "cells.json"          # the grid as minted: dropped cells, then the withheld draw
    if cells_p.exists():
        incoherent = [tuple(c["cell"]) for c in json.loads(cells_p.read_text())["incoherent"]]
    else:
        marked = mark_incoherent(client, book, spec.stream.families, spec.stream.modes, spec.json.reasks)
        incoherent = list(marked)
    try:
        minter = build_minter(spec, client, book, browser, write_stub, sink, incoherent)
        train, withheld = grid_cells(minter.params)
        if not cells_p.exists():
            cells_p.parent.mkdir(parents=True, exist_ok=True)
            cells_p.write_text(json.dumps({
                "incoherent": [{"cell": list(c), "why": marked[c]} for c in sorted(marked)],
                "training": [list(c) for c in train], "withheld": [list(c) for c in withheld],
            }, indent=1) + "\n")
        minter.journal = goals_json.JsonlMintJournal(stream_p.parent / "mint_journal.jsonl")
        if args.only:
            wanted = set(args.only.split(","))
            slots = [s for s in plan_slots(minter.params) if s.id in wanted]
            if {s.id for s in slots} != wanted:
                raise SystemExit(f"unknown ids: {sorted(wanted - {s.id for s in slots})}")
            sink(minter.mint_requests(slots))
        else:
            minter.mint()
    except MintFailed as e:
        if e.partial is not None:
            report_p.parent.mkdir(parents=True, exist_ok=True)
            report_p.with_suffix(".partial.json").write_text(json.dumps(e.partial.report, indent=1) + "\n")
        raise SystemExit(f"{e}; the minted positions are journaled, rerun to resume") from None
    finally:
        browser.close()
        print(f"spend ${spend.total_usd():.4f} over {len(spend.rows)} calls")
    print(f"wrote {stream_p}, {held_p}, {stubs}/")
    return 0


def _tag_round(args: argparse.Namespace) -> int:
    """Tags one round's requests (or the held-out set, minting nothing) and stores tags and glosses."""
    from friday.adapters.goals_json import JsonGoalSource, JsonTagVocabulary
    from friday.app.goals import build_tagger, tag_held_out, tag_round
    from friday.roles.prompts import PromptBook

    spec = load_spec(args.spec)
    stream_p, held_p, tags_p, _ = _goal_paths(spec, args.goals_dir)
    vocab = JsonTagVocabulary(tags_p)
    source = JsonGoalSource(stream_p, held_p, vocab)
    telemetry = _open_telemetry(spec)
    client, spend = _bound_client(spec, ("tagger",), telemetry)
    tagger = build_tagger(spec, client, PromptBook(Path(spec.paths.prompts)))
    try:
        if args.which == "held-out":
            tags = tag_held_out(tagger, source, vocab, spec.run.rounds, telemetry=telemetry)
            new = []
        else:
            tags, new = tag_round(tagger, source, vocab, int(args.which), spec.stream.max_new_tags,
                                  telemetry=telemetry)
    finally:
        print(f"spend ${spend.total_usd():.4f} over {len(spend.rows)} calls")
    for rid in sorted(tags):
        print(f"{rid}: {', '.join(sorted(tags[rid]))}")
    for g in new:
        print(f"minted {g.tag}: {g.gloss}")
    return 0


# ---------------------------------------------------------------------------------------------------------
# The run: composition root for adapters (app/wiring.py builds the pure parts)


CACHE_DIR = "cache"          # response and rubric caches under the run root (no paths.cache knob yet)
MAIN_RUN = "main"            # SPEC § 17: the main run's telemetry scope; the critic run is `critic`


def _run_root(spec, args) -> tuple[Path, Path, Path]:
    """(repo, run root, database): `--root` holds the run's own db, journal, lenses, plans and artifacts."""
    repo = Path.cwd()
    root = Path(args.root).resolve() if getattr(args, "root", None) else repo
    root.mkdir(parents=True, exist_ok=True)
    db = Path(args.db).resolve() if getattr(args, "db", None) else root / spec.paths.db
    return repo, root, db


@dataclass(frozen=True)
class PassEnvFactory:
    """Opens one pass process's own client, browser and store connection (picklable for spawn)."""

    spec_path: str
    repo: str
    root: str
    db: str
    role_models: tuple[tuple[str, str], ...]

    @contextmanager
    def __call__(self):
        from friday.adapters.cache import FileResponseCache
        from friday.adapters.crusoe import CrusoeClient, TransportFailure
        from friday.adapters.playwright_browser import PlaywrightBrowser
        from friday.adapters.quality import NodeQualityProbe
        from friday.adapters.rubric import ModelRubric
        from friday.adapters.sqlite.run_stores import SqliteJudgeStore, SqlitePassStore
        from friday.adapters.sqlite.stores import SqliteSpendLog, connect
        from friday.adapters.telemetry import open_run_telemetry
        from friday.app.pipeline import PassEnv, PassSettings
        from friday.app.runner import DraftRecordingJudge
        from friday.app.wiring import evaluator_settings, with_optional
        from friday.eval.evaluate import Evaluator
        from friday.roles.ask import Rejected, ask_json
        from friday.roles.judge import Judge
        from friday.roles.prompts import PromptBook
        from friday.roles.worker import Worker

        spec = load_spec(self.spec_path)
        repo, root = Path(self.repo), Path(self.root)
        conn = connect(self.db, spec.telemetry.busy_timeout_ms)
        spend = SqliteSpendLog(conn)
        tel = open_run_telemetry(self.db, MAIN_RUN, busy_timeout_ms=spec.telemetry.busy_timeout_ms)
        browser = None
        try:
            client = with_optional(CrusoeClient, spec.models, spec.transport, spend,
                                   cache=FileResponseCache(root / CACHE_DIR / "responses"), telemetry=tel)
            client.list_models()
            models = dict(self.role_models)
            client.bind(models)
            book = PromptBook(repo / spec.paths.prompts)
            browser = PlaywrightBrowser(spec.eval.action_timeout_ms, spec.eval.load_timeout_ms, telemetry=tel)
            rubric = ModelRubric.from_spec(spec, client, ask_json, Rejected, model=models["rubric"],
                                           prompts=book.root, telemetry=tel,
                                           cache=FileResponseCache(root / CACHE_DIR / "rubric"),
                                           spend=spend)
            evaluator = with_optional(Evaluator, browser=browser, rubric=rubric,
                                      quality=NodeQualityProbe.from_spec(spec, repo), **evaluator_settings(spec),
                                      telemetry=tel)
            artifacts = root / spec.paths.artifacts
            judge = Judge(client, book, spec.lens.flag_p, spec.judge.max_observations, spec.judge.quote_reasks, tel)
            yield PassEnv(
                worker=Worker(client, book), book=book, browser=browser, evaluate=evaluator.run,
                judge=DraftRecordingJudge(judge, artifacts), passes=SqlitePassStore(conn),
                judge_store=SqliteJudgeStore(conn), artifacts=artifacts, settings=PassSettings.from_spec(spec),
                transport_errors=(TransportFailure,), telemetry=tel,
            )
        finally:
            if browser is not None:
                browser.close()
            conn.close()
            tel.conn.close()


class SqliteCloseLog:
    """The close record in the event log: one `close` event per completed round close, with its summary.
    `flips()` and `pending_steers()` read only their own event types, so no other reader sees these rows."""

    def __init__(self, conn) -> None:
        self.conn = conn

    def closed(self):
        from friday.algorithms.halt import RoundSummary
        from friday.app.runner import ClosedRound

        rows = self.conn.execute("SELECT body FROM events WHERE type = 'close' ORDER BY seq").fetchall()
        out = []
        for (body,) in rows:
            b = json.loads(body)
            out.append(ClosedRound(RoundSummary(**b["summary"]), b["halt"]))
        return out

    def record(self, summary, halt) -> None:
        import dataclasses

        from friday.adapters.sqlite.stores import transaction

        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO events (type, subject_id, round, body) VALUES ('close', 'round', ?, ?)",
                (summary.round, json.dumps({"summary": dataclasses.asdict(summary), "halt": halt})),
            )


def _spend_by_role(conn):
    def read() -> dict[str, float]:
        rows = conn.execute("SELECT role, COALESCE(SUM(cost_usd), 0) FROM spend GROUP BY role").fetchall()
        return {r: float(v) for r, v in rows}
    return read


def _git_log(root: Path):
    def read():
        from friday.adapters.git import GitVcs
        from friday.app.export import Commit

        vcs = GitVcs(root)
        try:
            out = vcs._git("log", "--format=%H%x09%s", "--", "journal", "lenses")
        except Exception:  # noqa: BLE001 - a run root with no commit yet has no log; the export lists none
            return []
        return [Commit(*line.split("\t", 1)) for line in out.splitlines() if "\t" in line]
    return read


def _versions() -> dict[str, str]:
    import platform
    from importlib import metadata

    out = {"python": platform.python_version()}
    try:
        out["playwright"] = metadata.version("playwright")
    except metadata.PackageNotFoundError:
        out["playwright"] = "absent"
    return out


def build_runner(args: argparse.Namespace, *, live: bool = True):
    """The Runner over the run root's stores, the bound Crusoe client, Chromium and the markdown Journal."""
    import shutil

    from friday.adapters.cache import FileResponseCache
    from friday.adapters.crusoe import CrusoeClient, TransportFailure
    from friday.adapters.git import GitVcs
    from friday.adapters.goals_json import JsonGoalSource, JsonTagVocabulary
    from friday.adapters.journal_md import MarkdownJournal
    from friday.adapters.lens_md import MarkdownLensSet
    from friday.adapters.sqlite.process import open_run_stores
    from friday.adapters.telemetry import open_run_telemetry
    from friday.adapters.youcom import YouSearchClient
    from friday.app.goals import build_tagger
    from friday.app.pins import resolve
    from friday.app.render import FlipRenderer
    from friday.app.request_text import request_text
    from friday.app.round_close import build_round_close
    from friday.app.runner import Runner, Stores
    from friday.app.wiring import build_mechanics, with_optional
    from friday.roles.coordinator import Coordinator
    from friday.roles.lens_minter import LensMinter
    from friday.roles.nominator import Nominator
    from friday.roles.pooling import PoolingCall
    from friday.roles.prompts import PromptBook

    spec = load_spec(args.spec)
    repo, root, db = _run_root(spec, args)
    stores = open_run_stores(db)
    tel = open_run_telemetry(db, MAIN_RUN, busy_timeout_ms=spec.telemetry.busy_timeout_ms)

    if root != repo:
        lenses_dir = root / spec.paths.lenses
        if not lenses_dir.exists():                      # the baseline lenses, as frozen in the repository
            lenses_dir.mkdir(parents=True)
            for f in sorted((repo / spec.paths.lenses).glob("L-*.md")):
                shutil.copy(f, lenses_dir / f.name)
    vcs = GitVcs(root)
    if root != repo and not (root / ".git").exists():   # the run root's own history: one commit per flip
        vcs._git("init", "-q")
        vcs.commit([spec.paths.lenses], "Baseline lenses")
    journal = MarkdownJournal(root / spec.paths.journal, vcs)
    lens_set = MarkdownLensSet(root / spec.paths.lenses, vcs)

    stream_p, held_p = repo / spec.stream.path, repo / spec.stream.held_out_path
    tags_p = repo / spec.stream.tags_path if root == repo else root / Path(spec.stream.tags_path).name
    vocab = JsonTagVocabulary(tags_p)
    goals = JsonGoalSource(stream_p, held_p, vocab)
    book = PromptBook(repo / spec.paths.prompts)

    client = with_optional(CrusoeClient, spec.models, spec.transport, stores.spend,
                           cache=FileResponseCache(root / CACHE_DIR / "responses"), telemetry=tel)
    roles = {r: pin for r, pin in spec.models.roles.items() if r != "critic"}   # the critic run binds its own
    resolved = resolve(roles, client.list_models())
    role_models = {r: res.model for r, res in resolved.items()}
    client.bind(role_models)
    fallbacks = [{"role": r, "from_model": res.wanted[0] if res.wanted else "", "to_model": res.model,
                  "reason": "pin rule: no pin served"} for r, res in sorted(resolved.items()) if res.replaced]

    search = YouSearchClient(spec.search)
    renderer = FlipRenderer.load(repo / spec.paths.templates / "flips.yml")
    mechanics = build_mechanics(spec)
    requests: dict[str, object] = {}

    def residual(p):
        if not requests:
            for n in range(1, spec.run.rounds + 1):
                requests.update({r.id: r for r in goals.round(n)})
        text = request_text(requests[p.category], p.regime, spec.drift.constraint)
        src = Path(p.artifact_path).read_text() if p.artifact_path and Path(p.artifact_path).exists() else ""
        return text, src

    def build_close():
        nominator = Nominator(client, search, book, frozenset(g.tag for g in vocab.glosses()),
                              tuple(spec.stream.families), spec.nominator.payload_max_words, spec.nominator.reasks)
        pooling = PoolingCall(client, book, spec.pool.max_direction_labels, spec.json.reasks)
        minter = LensMinter(client, book, reasks=spec.json.reasks, families=tuple(spec.stream.families))
        return with_optional(
            build_round_close, spec, journal=journal, ledger=stores.ledger, events=stores.events,
            judge_store=stores.judge, pools=stores.pools, passes=stores.passes, observations=stores.observations,
            lens_set=lens_set, spend=stores.spend, pooling=pooling, nominator=nominator, minter=minter,
            renderer=renderer, residual_source=residual, bar=mechanics.bar, telemetry=tel)

    git_sha = ""
    try:
        git_sha = GitVcs(repo)._git("rev-parse", "HEAD")
    except Exception:  # noqa: BLE001 - the manifest records an absent sha as empty; nothing reads it to decide
        git_sha = ""
    manifest = {"spec_hash": spec.spec_hash, "git_sha": git_sha, "seed": spec.run.seed, "role_models": role_models,
                "fallbacks_taken": [f["role"] for f in fallbacks], "host_cpus": os.cpu_count(),
                "max_parallel_passes": spec.run.max_parallel_passes, "chromium": "playwright bundled",
                **_versions()}
    factory = PassEnvFactory(str(Path(args.spec).resolve()), str(repo), str(root), str(db),
                             tuple(sorted(role_models.items())))
    runner = Runner(
        spec=spec, stores=Stores(stores.passes, stores.judge, stores.observations, stores.pools, stores.exposures,
                                 stores.ledger, stores.events, stores.spend),
        journal=journal, lens_set=lens_set, goals=goals, vocabulary=vocab, tagger=build_tagger(spec, client, book),
        coordinator=Coordinator(client, book, spec.json.reasks), build_close=build_close, env_factory=factory,
        close_log=SqliteCloseLog(stores.conn), telemetry=tel, root=root, transport_errors=(TransportFailure,),
        spend_by_role=_spend_by_role(stores.conn), git_log=_git_log(root), manifest=manifest, fallbacks=fallbacks,
    )
    return runner, client


def _run(args: argparse.Namespace) -> int:
    """Runs the rounds (resuming after the last closed one) until the halt rule or `--stop-after` rounds."""
    runner, _ = build_runner(args)
    halt = runner.run(stop_after=args.stop_after)
    print(f"stopped: {halt or f'--stop-after {args.stop_after}'}; spend ${runner.stores.spend.total_usd():.4f}")
    return 0


def _steer(args: argparse.Namespace) -> int:
    """Writes a steer event (retire, veto or note), applied at the next round close."""
    from friday.adapters.git import GitVcs
    from friday.adapters.journal_md import MarkdownJournal
    from friday.adapters.sqlite.process import open_run_stores
    from friday.domain.events import Steer

    spec = load_spec(args.spec)
    _, root, db = _run_root(spec, args)
    stores = open_run_stores(db)
    MarkdownJournal(root / spec.paths.journal, GitVcs(root)).get(args.decision_id)   # raises when unknown
    issued = 1 + stores.conn.execute("SELECT COUNT(*) FROM events WHERE type = 'close'").fetchone()[0]
    stores.events.append_steer(Steer(args.decision_id, args.action, args.reason, issued))
    print(f"steer {args.action} {args.decision_id} written; applied at the close of round {issued}")
    return 0


def _telemetry(args: argparse.Namespace) -> int:
    """Prints a round's span tree (`--tree`) or the per-step time table."""
    from friday.adapters.telemetry import open_run_telemetry
    from friday.app.telemetry_report import span_tree, time_table

    spec = load_spec(args.spec)
    _, _, db = _run_root(spec, args)
    tel = open_run_telemetry(db, MAIN_RUN, busy_timeout_ms=spec.telemetry.busy_timeout_ms)
    fn = span_tree if args.tree else time_table
    print(fn(tel, run=MAIN_RUN, round_=args.round))
    return 0


def _web(args: argparse.Namespace) -> int:
    from web.app import main as web_main

    web_main(args.rest)
    return 0


def _readout(args: argparse.Namespace) -> int:
    """Runs the readout builds not yet run, then prints every reading."""
    runner, _ = build_runner(args)
    runner.readout()
    return 0


def _export(args: argparse.Namespace) -> int:
    runner, _ = build_runner(args)
    print(f"wrote {runner.write_export()}")
    return 0


def _root_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--db", default=None, help="the run database (default: paths.db under the run root)")
    p.add_argument("--root", default=None, help="the run root: its own db, journal, lenses, plans, artifacts")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="friday")
    parser.add_argument("--spec", default="run.yml")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor").set_defaults(fn=_doctor)
    mg = sub.add_parser("mint-goals")
    mg.add_argument("--goals-dir", default=None, help="write here instead of run.yml's stream paths")
    mg.add_argument("--only", default=None, help="comma-separated request ids to mint (a smoke run)")
    mg.set_defaults(fn=_mint_goals)
    tr = sub.add_parser("tag-round")
    tr.add_argument("which", help="a round number, or held-out")
    tr.add_argument("--goals-dir", default=None)
    tr.set_defaults(fn=_tag_round)
    st = sub.add_parser("steer")
    st.add_argument("decision_id")
    st.add_argument("action", choices=["retire", "veto", "note"])
    st.add_argument("reason")
    _root_args(st)
    st.set_defaults(fn=_steer)
    rn = sub.add_parser("run")
    rn.add_argument("--stop-after", type=int, default=None, help="stop once this many rounds are closed")
    _root_args(rn)
    rn.set_defaults(fn=_run)
    te = sub.add_parser("telemetry")
    te.add_argument("--round", type=int, default=None)
    te.add_argument("--tree", action="store_true")
    _root_args(te)
    te.set_defaults(fn=_telemetry)
    wb = sub.add_parser("web")
    wb.add_argument("rest", nargs=argparse.REMAINDER)
    wb.set_defaults(fn=_web)
    for name, fn in (("readout", _readout), ("export", _export)):
        p = sub.add_parser(name)
        _root_args(p)
        p.set_defaults(fn=fn)
    args = parser.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
