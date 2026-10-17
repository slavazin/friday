"""`friday` command line: doctor, mint-goals, tag-round, steer, run."""

from __future__ import annotations

import argparse
import json
import os
import sys
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


def _telemetry(spec):
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
    telemetry = _telemetry(spec)
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
    telemetry = _telemetry(spec)
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


def _steer(args: argparse.Namespace) -> int:
    raise NotImplementedError("steer: writes a Steer event applied at the next round close")


def _run(args: argparse.Namespace) -> int:
    raise NotImplementedError("run: the coordinator loop (block 12:30)")


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
    st.set_defaults(fn=_steer)
    sub.add_parser("run").set_defaults(fn=_run)
    args = parser.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
