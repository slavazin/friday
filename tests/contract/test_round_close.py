"""The decision lifecycle end to end over in-memory stores: exposure deltas -> admission -> ablation ->
suspect -> retire, and admission of a successor superseding its predecessor."""

from pathlib import Path

import pytest

from friday.adapters.memory import MemoryEventLog, MemoryJournal, MemoryJudgeStore, MemoryLedger, MemoryPoolStore
from friday.adapters.telemetry import MemoryTelemetry
from friday.algorithms.exposure import PairKind, PairPlan
from friday.app.ledger import pair_deltas
from friday.app.render import FlipRenderer
from friday.app.round_close import DecisionClose
from friday.app.wiring import build_mechanics, flip_constants
from friday.config import load_spec
from friday.domain.lifecycle import FlipCause, IllegalFlip
from friday.domain.types import (
    Arm,
    Composition,
    DecisionStatus,
    DeltaKind,
    Disposition,
    EvalReport,
    Fire,
    LedgerDelta,
    Pass,
    Pool,
    PoolStatus,
    Regime,
)
from friday.ports.telemetry import Scope
from tests.unit.builders import dec

ROOT = Path(__file__).resolve().parents[2]
SPEC = load_spec(ROOT / "run.yml")


def close():
    m = build_mechanics(SPEC)
    return DecisionClose(
        journal=MemoryJournal(), ledger=MemoryLedger(), events=MemoryEventLog(), judge_store=MemoryJudgeStore(),
        pools=MemoryPoolStore(),
        gate=m.gate, detector=m.detector, renderer=FlipRenderer.load(ROOT / "templates/flips.yml"),
        in_flight=SPEC.gate.in_flight, constants=flip_constants(SPEC), telemetry=MemoryTelemetry(Scope("main")),
    )


def add(c, did, kind, ds, start_round=1):
    for i, d in enumerate(ds):
        c.ledger.append(LedgerDelta(did, start_round + i, "cat", kind, d, 0.0))


def test_pair_deltas_sign_and_transport_rule():
    def p(i, slot, failed=False):
        return Pass(i, 1, "cat", Arm.JOURNAL, "w", Composition("cat", ()), Regime.R1, pair_slot=slot,
                    transport_failed=failed)

    def ev(i, loss, q):
        return EvalReport(i, Regime.R1, (), (), 0, 0, (), q, loss)

    plan = [PairPlan("cat", PairKind.EXPOSURE, "D-0001", treated_slot=1)]
    rows = pair_deltas(1, plan, [p("a", 0), p("b", 1)], {"a": ev("a", 0.6, 3), "b": ev("b", 0.4, 5)})
    assert len(rows) == 1 and rows[0].d == 0.6 - 0.4 and rows[0].dQ == 2.0 and rows[0].kind is DeltaKind.EXPOSURE
    assert pair_deltas(1, plan, [p("a", 0), p("b", 1, True)], {"a": ev("a", 0.6, 3), "b": ev("b", 0.4, 5)}) == []


def test_candidate_is_admitted_with_a_rendered_commit():
    c = close()
    c.journal.draft(dec("D-0001", DecisionStatus.CANDIDATE))
    add(c, "D-0001", DeltaKind.EXPOSURE, [0.3, 0.25, 0.35, 0.3, 0.3, 0.3, 0.3, 0.35])
    flipped = c.close_gate(8)
    assert [d.status for d in flipped] == [DecisionStatus.ADMITTED]
    msg = c.journal.commits[-1]
    assert msg.startswith("D-0001 candidate → admitted: W ") and "≥ 3.33, n 8 pairs" in msg and "≤ 2" in msg
    assert c.events.flips("D-0001")[0].cause.value == "gate.admit"


def test_held_candidate_does_not_flip():
    c = close()
    c.journal.draft(dec("D-0001", DecisionStatus.CANDIDATE))
    add(c, "D-0001", DeltaKind.EXPOSURE, [0.1, -0.1])
    assert c.close_gate(2) == [] and c.journal.commits == []


def test_admitted_successor_supersedes_its_predecessor():
    c = close()
    c.journal.draft(dec("D-0001", DecisionStatus.CANDIDATE))
    add(c, "D-0001", DeltaKind.EXPOSURE, [0.3] * 8)
    c.close_gate(8)
    c.journal.draft(dec("D-0002", DecisionStatus.CANDIDATE, supersedes=("D-0001",), drafted_round=9))
    add(c, "D-0002", DeltaKind.EXPOSURE, [0.3] * 8, start_round=9)
    c.close_gate(16)
    old = c.journal.get("D-0001")
    assert old.status is DecisionStatus.RETIRED and old.successors == ("D-0002",)
    assert c.journal.commits[-1] == "D-0001 admitted → retired: superseded by D-0002"


def test_stale_decision_goes_suspect_then_retires_on_confirmation():
    c = close()
    c.journal.draft(dec("D-0001", DecisionStatus.CANDIDATE))
    add(c, "D-0001", DeltaKind.EXPOSURE, [0.3] * 8)
    c.close_gate(8)
    add(c, "D-0001", DeltaKind.ABLATION, [0.2, -0.2, -0.25, -0.2, -0.3], start_round=9)
    assert [d.status for d in c.close_detector(13)] == [DecisionStatus.SUSPECT]
    assert c.close_detector(14) == []                                   # waits for confirmation pairs
    add(c, "D-0001", DeltaKind.ABLATION, [-0.2, -0.3, -0.25], start_round=14)
    assert [d.status for d in c.close_detector(16)] == [DecisionStatus.RETIRED]
    assert "suspect → retired: 3 confirmation pairs" in c.journal.commits[-1]


def test_suspect_clears_when_confirmation_does_not_hold():
    c = close()
    c.journal.draft(dec("D-0001", DecisionStatus.CANDIDATE))
    add(c, "D-0001", DeltaKind.EXPOSURE, [0.3] * 8)
    c.close_gate(8)
    add(c, "D-0001", DeltaKind.ABLATION, [-0.2, -0.25, -0.2, -0.3], start_round=9)
    assert [d.status for d in c.close_detector(12)] == [DecisionStatus.SUSPECT]
    add(c, "D-0001", DeltaKind.ABLATION, [0.2, 0.1, -0.05], start_round=13)
    assert [d.status for d in c.close_detector(15)] == [DecisionStatus.ADMITTED]


def test_refused_candidate_marks_its_pool_refused():
    c = close()
    c.pools.put_pool(Pool("P-0001", "L-03", "shape", ("bind on window",), PoolStatus.NOMINATED))
    c.journal.draft(dec("D-0001", DecisionStatus.CANDIDATE))
    add(c, "D-0001", DeltaKind.EXPOSURE, [-0.5] * 6)
    assert [d.status for d in c.close_gate(6)] == [DecisionStatus.REFUSED]
    pool = c.pools.pools()[0]
    assert pool.status is PoolStatus.REFUSED and pool.refused_at_round == 6


def test_cleared_decision_is_read_on_fresh_evidence():
    """Regression: the harm e-process restarts at a clear, so one more mild delta cannot re-suspect."""
    c = close()
    c.journal.draft(dec("D-0001", DecisionStatus.CANDIDATE))
    add(c, "D-0001", DeltaKind.EXPOSURE, [0.3] * 8)
    c.close_gate(8)
    add(c, "D-0001", DeltaKind.ABLATION, [-0.3] * 8, start_round=9)
    assert [d.status for d in c.close_detector(16)] == [DecisionStatus.SUSPECT]
    add(c, "D-0001", DeltaKind.ABLATION, [0.02, 0.0, 0.03], start_round=17)
    assert [d.status for d in c.close_detector(19)] == [DecisionStatus.ADMITTED]
    add(c, "D-0001", DeltaKind.ABLATION, [0.01], start_round=20)
    assert c.close_detector(20) == []


def test_fires_from_before_admission_do_not_count():
    c = close()
    c.journal.draft(dec("D-0001", DecisionStatus.CANDIDATE))
    c.judge_store.add_fires([Fire(f"x{i}", "D-0001", 2, Disposition.CONTRADICTED) for i in range(6)])
    add(c, "D-0001", DeltaKind.EXPOSURE, [0.3] * 8)
    c.close_gate(8)
    assert c.close_detector(8) == []


def test_illegal_flip_writes_no_event():
    c = close()
    c.journal.draft(dec("D-0001", DecisionStatus.CANDIDATE))
    with pytest.raises(IllegalFlip):
        c._flip(c.journal.get("D-0001"), DecisionStatus.RETIRED, FlipCause.STEER_RETIRE, 3, {"reason": "x"})
    assert c.events.flips() == []


def test_suspect_commit_names_the_contradicted_share():
    c = close()
    c.journal.draft(dec("D-0001", DecisionStatus.CANDIDATE))
    add(c, "D-0001", DeltaKind.EXPOSURE, [0.3] * 8)
    c.close_gate(8)
    c.judge_store.add_fires([Fire(f"x{i}", "D-0001", 9, Disposition.CONTRADICTED) for i in range(4)])
    c.close_detector(9)
    assert "contradicted share 1.00" in c.journal.commits[-1]
