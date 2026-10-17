"""The demo export (SPEC § 13): JSON round-trip, schema doc coverage, recomputed figures, ordering."""

import json
import re
import tempfile
from pathlib import Path

import pytest

from friday.adapters.git import RecordingVcs
from friday.adapters.lens_md import MarkdownLensSet
from friday.adapters.memory import (
    MemoryEventLog,
    MemoryGoalSource,
    MemoryJournal,
    MemoryJudgeStore,
    MemoryLedger,
    MemoryObservationStore,
    MemoryPassStore,
    MemoryPoolStore,
    MemorySpendLog,
)
from friday.app.export import TOP_LEVEL, Commit, ExportStores, build, dumps
from friday.config import load_spec
from friday.domain.events import StatusFlip
from friday.domain.lifecycle import FlipCause
from friday.domain.types import (
    Arm,
    Composition,
    DecisionStatus,
    DeltaKind,
    EvalReport,
    LedgerDelta,
    LensAnswer,
    Observation,
    Pass,
    Pool,
    Regime,
    Request,
)
from friday.ports.models import ChatResult
from friday.stats.eprocess import BETTING, wealth
from tests.unit.builders import dec, lens

ROOT = Path(__file__).resolve().parents[2]
SPEC = load_spec(ROOT / "run.yml")


def world(order=1):
    """A small run: training passes in rounds 1-2, readout passes on two held-out categories."""
    held = [Request(f"H-0{i}", "r", frozenset({"chart"}), {"#a": "a"}, {Regime.R2: ()}, held_out=True)
            for i in (1, 2)]
    passes, judge = MemoryPassStore(), MemoryJudgeStore()
    rows = []
    for r in (1, 2):
        for k in range(SPEC.lens.n_min):
            pid = f"T-{r}-{k:02d}"
            rows.append((Pass(pid, r, f"C-{r}{k:02d}", Arm.JOURNAL, "w", Composition(f"C-{r}{k:02d}", ("D-0001",)),
                              Regime.R1, artifact_path=f"artifacts/{pid}/index.html", pair_slot=k % 2),
                         0.1 + 0.02 * k))
    for cat in ("H-01", "H-02"):
        for arm in Arm:
            for rep in range(2):
                pid = f"V-{cat}-{arm.value}-{rep}"
                comp = Composition(cat, ("D-0001",) if arm is Arm.JOURNAL else (), notes=arm is Arm.NOTES)
                rows.append((Pass(pid, 15, cat, arm, "w", comp, Regime.R2), 0.5 if arm is Arm.EMPTY else 0.3))
    for p, loss in rows[::order]:
        passes.add_pass(p)
        passes.add_eval(EvalReport(p.id, p.regime, (), (), 0.5, 0.5, (3,), 1, loss))
        # the lens tracks loss on training passes; readout passes get an answer that would break that
        judge.add_lens_answers([LensAnswer(p.id, "L-01", loss if p.category.startswith("C") else 1 - loss, "q")])
    events, journal, ledger = MemoryEventLog(), MemoryJournal(), MemoryLedger()
    journal.draft(dec("D-0001", status=DecisionStatus.CANDIDATE))
    ds = [0.2, 0.3, -0.1, 0.25]
    for i, d in enumerate(ds):
        ledger.append(LedgerDelta("D-0001", 1 + i // 2, "C-x", DeltaKind.EXPOSURE, d, 0.0))
    ledger.append(LedgerDelta("D-0001", 3, "C-y", DeltaKind.ABLATION, -0.2, 0.0))
    admit = StatusFlip("D-0001", "candidate", "admitted", FlipCause.GATE_ADMIT, 2, {"wealth": 9.99})
    events.append_flip(admit)
    journal.flip(admit, "admit")
    pools, obs = MemoryPoolStore(), MemoryObservationStore()
    pools.put_pool(Pool("P-0002", "L-01", "s", ("x",)))
    pools.put_pool(Pool("P-0001", "L-01", "s", ("x",)))
    obs.add_observations([Observation("O-1", "T-1-00", 1, "L-01", "C-100", "s", "x", "q", pool_id="P-0001")])
    spend = MemorySpendLog()
    spend.record("worker", "t", ChatResult("x", "m", 1, 1, 0.42), True)
    stores = ExportStores(passes, MemoryGoalSource(held), judge, obs, pools, ledger, events, spend)
    lenses = MarkdownLensSet(tempfile.mkdtemp(prefix="friday-lenses-"), RecordingVcs())
    lenses.add(lens("L-01"))
    return stores, journal, lenses, [Commit("abc123", "D-0001 candidate → admitted")], ds


def test_export_round_trips_through_json():
    stores, journal, lenses, log, _ = world()
    doc = build(stores, journal, lenses, log, SPEC)
    assert json.loads(dumps(doc)) == json.loads(json.dumps(doc))
    assert tuple(doc) == TOP_LEVEL


def test_schema_doc_lists_every_top_level_field():
    stores, journal, lenses, log, _ = world()
    doc = build(stores, journal, lenses, log, SPEC)
    text = (ROOT / "docs" / "export-schema.md").read_text()
    listed = set(re.findall(r"^\| `([a-z_]+)` +\|", text, flags=re.M))
    assert set(doc) <= listed
    for key in ("passes", "decisions", "lenses", "pools", "rounds", "readout"):
        row = doc[key][0] if isinstance(doc[key], list) else doc[key]
        missing = [f for f in row if f"`{key}[].{f}`" not in text and f"`{key}.{f}`" not in text]
        assert missing == [], f"{key}: {missing}"


def test_export_is_deterministic_under_store_insertion_order():
    a = dumps(build(*world(order=1)[:4], SPEC))
    b = dumps(build(*world(order=-1)[:4], SPEC))
    assert a == b
    doc = json.loads(a)
    assert [p["id"] for p in doc["pools"]] == ["P-0001", "P-0002"]
    rounds = [p["round"] for p in doc["passes"]]
    assert rounds == sorted(rounds)


def test_wealth_is_recomputed_from_ledger_rows_not_read_from_flip_evidence():
    stores, journal, lenses, log, ds = world()
    d = build(stores, journal, lenses, log, SPEC)["decisions"][0]
    betting = BETTING.build(SPEC.gate.betting.name, **SPEC.gate.betting.params)
    assert d["ledger"]["exposure"]["wealth"] == pytest.approx(wealth(ds, betting))
    assert d["ledger"]["exposure"]["wealth"] != pytest.approx(9.99)
    assert d["ledger"]["exposure"]["n"] == 4
    assert d["ledger"]["ablation"]["n_since_admitted"] == 1
    assert [h["new"] for h in d["status_history"]] == ["admitted"]


def test_lens_value_history_reads_training_passes_only():
    stores, journal, lenses, log, _ = world()
    hist = build(stores, journal, lenses, log, SPEC)["lenses"][0]["value_history"]
    # Every window is training passes whose p equals loss: AUC 1. A readout pass in the window would lower it.
    assert [h["round"] for h in hist] == [1, 2]
    assert all(h["value"] == pytest.approx(1.0) for h in hist)
    assert hist[1]["n"] == min(SPEC.lens.window, 2 * SPEC.lens.n_min)


def test_passes_carry_artifact_path_composition_loss_and_readout_tag():
    stores, journal, lenses, log, _ = world()
    rows = {p["id"]: p for p in build(stores, journal, lenses, log, SPEC)["passes"]}
    t = rows["T-1-00"]
    assert t["artifact_path"] == "artifacts/T-1-00/index.html" and t["decisions_composed"] == ["D-0001"]
    assert t["readout"] is False and rows["V-H-01-empty-0"]["readout"] is True
    assert t["loss"] == pytest.approx(0.1) and t["how_to_summary"] is None


def test_export_carries_no_environment_secret(monkeypatch):
    sentinel = "sk-planted-secret-value-123"
    monkeypatch.setenv(SPEC.models.endpoint.api_key_env, sentinel)
    text = dumps(build(*world()[:4], SPEC))
    assert sentinel not in text and SPEC.models.endpoint.api_key_env not in text


def test_readout_inside_export_reads_r1_over_the_held_out_categories():
    doc = build(*world()[:4], SPEC)
    r1 = doc["readout"]["r1"]
    assert r1["n_categories"] == 2 and r1["population"] == "2 held-out categories × 2 reps"
    assert r1["mean"] == pytest.approx(0.2)
    assert doc["run"]["spend_usd"] == pytest.approx(0.42)
