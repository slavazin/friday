"""What if the gate had tested every nominated pool? A showcase, not evidence.

Replays the main run's nomination rules over its recorded observations with no `gate.in_flight` cap, drafts the
held-back pools with the run's own nominator, and builds each chosen held-out request twice with the run's
worker: bare, and with every would-be decision composed. Writes `<out>/whatif.json` and
`<out>/apps/<build id>/index.html`. None of these decisions passed the gate, and no build here is scored.

    uv run --env-file .env python scripts/whatif.py --run-root ../friday-runs/main --spec ../friday-run/run.yml \
        --out ../friday-runs/whatif
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from friday.adapters.crusoe import CrusoeClient
from friday.adapters.goals_json import JsonGoalSource
from friday.adapters.journal_md import parse_decision
from friday.adapters.memory import MemorySpendLog
from friday.adapters.sqlite.run_stores import SqliteObservationStore, SqlitePoolStore
from friday.adapters.telemetry import MemoryTelemetry
from friday.adapters.youcom import YouSearchClient
from friday.algorithms.recurrence import RecurrenceBar, nominations
from friday.app.request_text import request_text
from friday.config import load_spec
from friday.domain.types import Decision, DecisionStatus, ObservationStatus, PoolStatus, Regime
from friday.ports.telemetry import Scope
from friday.roles.nominator import Draft, Nominator
from friday.roles.prompts import PromptBook
from friday.roles.worker import extract_html, render_composition

REPO = Path(__file__).resolve().parents[1]


def would_be(obs, pools, spec, rounds):
    """{pool id: (round, label)} for every pool the nomination rules reach by each close, with no in-flight cap."""
    p = spec.pool
    bar = RecurrenceBar(p.recurrence_min_observations, p.recurrence_min_rounds, p.fast_track_share,
                        min_requests=p.recurrence_min_requests, fast_track_min_requests=p.fast_track_min_requests)
    out = {}
    for r in range(1, rounds + 1):
        seen = [dataclasses.replace(o, status=ObservationStatus.OPEN) for o in obs if o.round <= r]
        cand = [dataclasses.replace(x, status=PoolStatus.OPEN) for x in pools if x.id not in out]
        for n in nominations(cand, seen, bar, room=10**6):
            out[n.pool.id] = (r, n.label)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-root", type=Path, required=True)
    ap.add_argument("--spec", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--requests", default="H-01,H-02,H-03,H-04,H-05")
    ap.add_argument("--rounds", type=int, default=4)
    args = ap.parse_args()
    spec = load_spec(args.spec)
    conn = sqlite3.connect(args.run_root / spec.paths.db)
    obs = list(SqliteObservationStore(conn).observations())
    pools = {x.id: x for x in SqlitePoolStore(conn).pools()}
    nominated = would_be(obs, list(pools.values()), spec, args.rounds)
    actual = {d.pool_id: d for d in (parse_decision(f.read_text())
                                     for f in sorted((args.run_root / spec.paths.journal).glob("D-*.md")))}

    spend = MemorySpendLog()
    client = CrusoeClient(spec.models, spec.transport, spend, telemetry=MemoryTelemetry(Scope("whatif")))
    client.list_models()
    client.bind({"nominator": spec.models.roles["nominator"].pins[0], "worker": spec.models.roles["worker"].pins[0]})
    book = PromptBook(REPO / spec.paths.prompts)
    tags = json.loads((args.run_root / "tags.json").read_text())
    vocab = frozenset(g["tag"] for g in tags.get("tags", []))
    nominator = Nominator(client, YouSearchClient(spec.search), book, vocab, tuple(spec.stream.families),
                          spec.nominator.payload_max_words, spec.nominator.reasks)

    def draft(pid):
        r, label = nominated[pid]
        if pid in actual:
            return pid, r, label, actual[pid]
        pool = dataclasses.replace(pools[pid], status=PoolStatus.OPEN)
        mine = [dataclasses.replace(o, status=ObservationStatus.OPEN) for o in obs if o.pool_id == pid and o.round <= r]
        d = nominator.draft(pool, label, mine)
        if not isinstance(d, Draft):
            return pid, r, label, None
        return pid, r, label, Decision(f"W-{pid[2:]}", DecisionStatus.CANDIDATE, pid, d.hook, d.payload,
                                       warrant={"sources": [c.url for c in d.sources]}, drafted_round=r)

    with ThreadPoolExecutor(8) as ex:
        drafted = list(ex.map(draft, sorted(nominated)))
    decisions = [d for *_, d in drafted if d is not None]

    goals = JsonGoalSource(REPO / spec.stream.path, REPO / spec.stream.held_out_path)
    held = {r.id: r for r in goals.held_out()}
    cells = json.loads((REPO / Path(spec.stream.path).parent / "cells.json").read_text()).get("requests", {})
    wanted = [held[i] for i in args.requests.split(",")]
    apps = args.out / "apps"

    def build(job):
        req, arm = job
        text = request_text(req, Regime(spec.readout.regime), spec.drift.constraint)
        msgs = render_composition(book, text, decisions if arm == "with" else [])
        res = client.chat("worker", msgs, tag=f"whatif:{req.id}:{arm}")
        html = extract_html(res.text)
        bid = f"{req.id}-{arm}"
        if html:
            (apps / bid).mkdir(parents=True, exist_ok=True)
            (apps / bid / "index.html").write_text(html)
        return {"id": bid, "request": req.id, "arm": arm, "built": html is not None, "model": res.model}

    with ThreadPoolExecutor(10) as ex:
        builds = list(ex.map(build, [(r, a) for r in wanted for a in ("bare", "with")]))

    doc = {
        "note": "Illustrative and unscored: the decisions below were nominated by the run's own rules with no "
                "in-flight cap, but never tested or admitted by the gate.",
        "worker": spec.models.roles["worker"].pins[0],
        "decisions": [{"id": d.id, "pool": pid, "nominated_at_round": r, "label": label,
                       "actual": pid in actual, "payload": d.payload if d else None,
                       "fires_when": sorted(d.hook.fires_when) if d else []}
                      for pid, r, label, d in drafted],
        "requests": [{"id": r.id, "text": r.text, "cell": " / ".join(cells.get(r.id, []))} for r in wanted],
        "builds": builds,
        "spend_usd": spend.total_usd(),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "whatif.json").write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps({k: doc[k] for k in ("decisions", "spend_usd")}, indent=1)[:3000])
    print([(b["id"], b["built"]) for b in builds])


if __name__ == "__main__":
    main()
