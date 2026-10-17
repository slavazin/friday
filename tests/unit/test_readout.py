"""The readout's readings (SPEC § 12) over in-memory stores with planted passes, evals and events."""

import dataclasses
import tempfile
from pathlib import Path

import pytest

from friday.adapters.git import RecordingVcs
from friday.adapters.lens_md import MarkdownLensSet
from friday.adapters.memory import (
    MemoryEventLog,
    MemoryGoalSource,
    MemoryJournal,
    MemoryLedger,
    MemoryPassStore,
    MemorySpendLog,
    MemoryTagVocabulary,
)
from friday.app.readout import (
    FORGETS,
    IMPROVES,
    JUDGE_LEARNS,
    LEANER,
    NOT_WORSE,
    UNEARNED,
    UNRESOLVED,
    ReadoutStores,
    lines,
    readings,
)
from friday.config import load_spec
from friday.domain.events import StatusFlip
from friday.domain.lifecycle import FlipCause
from friday.domain.types import (
    Arm,
    Check,
    CheckResult,
    Composition,
    DecisionStatus,
    DeltaKind,
    EvalReport,
    HowToParse,
    HowToReading,
    HowToRung,
    LedgerDelta,
    LensStatus,
    Pass,
    Regime,
    Request,
)
from friday.ports.models import ChatResult
from tests.unit.builders import dec, lens

ROOT = Path(__file__).resolve().parents[2]
SPEC = load_spec(ROOT / "run.yml")
CATS = [f"H-{i:02d}" for i in range(1, 7)]


def lens_set(lenses=()):
    ls = MarkdownLensSet(tempfile.mkdtemp(prefix="friday-lenses-"), RecordingVcs())
    for x in lenses:
        ls.add(x)
    return ls


def held_request(id_, tags=("chart",)):
    return Request(id_, f"request {id_}", frozenset(tags), {"#a": "a", "#b": "b"}, {Regime.R2: ()}, held_out=True)


def make_stores(journal=None, lenses=(), tokens=None, requests=None, tagged=True):
    reqs = list(requests) if requests is not None else [held_request(c) for c in CATS]
    vocab = MemoryTagVocabulary()
    if tagged:
        vocab.set_tags({r.id: r.tags for r in reqs})
    return ReadoutStores(MemoryPassStore(), MemoryGoalSource(reqs), journal or MemoryJournal(), lens_set(lenses),
                         MemoryEventLog(), MemoryLedger(), MemorySpendLog(), dict(tokens or {}), vocab)


def add(stores, cat, arm, rep, loss, q=0, decisions=(), how_to=(), silent=0, failed=False, **kw):
    pid = f"V-{cat}-{arm.value}-{rep}"
    comp = Composition(cat, tuple(decisions), notes=arm is Arm.NOTES)
    p = Pass(pid, 99, cat, arm, "fake/worker", comp, kw.pop("regime", Regime.R2), transport_failed=failed, **kw)
    stores.passes.add_pass(p)
    if not failed:
        stores.passes.add_eval(EvalReport(pid, Regime.R2, (), (), 0.5, 0.5, (3,), q, loss, how_to=how_to,
                                          silent_controls=silent))
    return pid


def plant(stores, diffs, baseline=Arm.EMPTY, reps=4, base_loss=0.6):
    """Per category, `reps` passes in `baseline` and journal arms whose mean difference is diffs[i]."""
    for cat, d in zip(CATS, diffs, strict=True):
        for k in range(reps):
            add(stores, cat, baseline, k, base_loss + 0.01 * k)
            add(stores, cat, Arm.JOURNAL, k, base_loss + 0.01 * k - d)


def test_r1_p_is_one_in_64_when_all_six_category_diffs_are_positive():
    s = make_stores()
    plant(s, [0.1, 0.2, 0.05, 0.3, 0.15, 0.12])
    r = readings(s, SPEC).r1
    assert r.p == pytest.approx(1 / 64)
    assert r.n_categories == 6
    assert r.population == "6 held-out categories × 4 reps"
    assert r.verdict == IMPROVES
    assert r.per_category["H-01"] == pytest.approx(0.1)


def test_r1_estimator_reads_per_category_means_not_pooled_reps():
    # One category with many reps must weigh as one category: the test runs over 6 category means.
    s = make_stores()
    plant(s, [0.1, 0.1, 0.1, 0.1, 0.1, -0.05])
    for k in range(4, 40):
        add(s, "H-01", Arm.EMPTY, k, 0.9)
        add(s, "H-01", Arm.JOURNAL, k, 0.0)
    r = readings(s, SPEC).r1
    assert r.n_categories == 6
    assert r.reps[Arm.EMPTY.value] == (4, 40)
    assert r.population == "6 held-out categories × 4-40 reps"
    cat_means = [r.per_category[c] for c in CATS]
    assert cat_means[0] == pytest.approx((4 * 0.1 + 36 * 0.9) / 40)
    assert r.mean == pytest.approx(sum(cat_means) / 6)
    observed = sum(cat_means)
    flips = [sum(m * (1 if (k >> i) & 1 else -1) for i, m in enumerate(cat_means)) for k in range(64)]
    assert r.p == pytest.approx(sum(f >= observed - 1e-12 for f in flips) / 64)


def test_r1_with_one_negative_category_reads_its_exact_sign_flip_p():
    s = make_stores()
    plant(s, [0.1, 0.2, 0.3, 0.4, 0.5, -0.6])
    r = readings(s, SPEC).r1
    assert r.p > SPEC.readout.r1_p
    assert r.verdict == UNRESOLVED


def test_r1_verdict_follows_the_r1_p_threshold_exactly():
    s = make_stores()
    plant(s, [0.1, 0.2, 0.3, 0.4, 0.5, -0.05])     # flips whose sum >= observed: p = 2/64 or so
    r = readings(s, SPEC).r1
    tight = dataclasses.replace(SPEC, readout=dataclasses.replace(SPEC.readout, r1_p=r.p - 1e-9))
    assert readings(s, SPEC).r1.verdict == IMPROVES
    assert readings(s, tight).r1.verdict == UNRESOLVED


def test_an_empty_arm_reads_unread_never_zero_or_unresolved():
    s = make_stores()
    for cat in CATS:                              # journal arm only: R1 has no empty-arm population
        add(s, cat, Arm.JOURNAL, 0, 0.4)
    out = readings(s, SPEC)
    assert out.r1.verdict is None and out.r1.p is None and out.r1.mean is None and out.r1.population is None
    assert out.r2.verdict is None
    assert out.quality[Arm.EMPTY.value].mean is None and out.quality[Arm.EMPTY.value].n == 0
    assert out.how_to[Arm.EMPTY.value].declared_passed_share.mean is None
    assert any("R1" in line and "unread" in line for line in lines(out))


def test_transport_failed_passes_leave_the_population_and_are_counted_unscored():
    s = make_stores()
    plant(s, [0.1] * 6)
    add(s, "H-01", Arm.EMPTY, 9, 0.0, failed=True)
    out = readings(s, SPEC)
    assert out.unscored[Arm.EMPTY.value] == 1
    assert out.r1.reps[Arm.EMPTY.value] == (4, 4)


def test_categories_missing_from_one_arm_narrow_the_population_and_are_named():
    s = make_stores()
    for cat in CATS[:5]:
        add(s, cat, Arm.EMPTY, 0, 0.6)
    for cat in CATS:
        add(s, cat, Arm.JOURNAL, 0, 0.5)
    r = readings(s, SPEC).r1
    assert r.n_categories == 5 and r.unread_categories == ("H-06",)


def test_a_training_pair_on_a_held_out_category_raises():
    s = make_stores()
    plant(s, [0.1] * 6)
    add(s, "H-02", Arm.JOURNAL, 7, 0.3, pair_slot=0)
    with pytest.raises(ValueError, match="training pair"):
        readings(s, SPEC)


def test_a_held_out_pass_under_the_wrong_regime_raises():
    s = make_stores()
    add(s, "H-02", Arm.EMPTY, 0, 0.3, regime=Regime.R1)
    with pytest.raises(ValueError, match="readout.regime"):
        readings(s, SPEC)


def test_training_passes_stay_out_of_the_readout_population():
    s = make_stores()
    plant(s, [0.1] * 6)
    train = Pass("T-1", 3, "C-0001", Arm.JOURNAL, "w", Composition("C-0001", ()), Regime.R1, pair_slot=0)
    s.passes.add_pass(train)
    s.passes.add_eval(EvalReport("T-1", Regime.R1, (), (), 0, 0, (1,), 0, 1.0))
    out = readings(s, SPEC)
    assert out.r1.n_categories == 6 and "C-0001" not in out.r1.per_category


def test_a_notes_arm_pass_without_the_notes_file_raises():
    s = make_stores()
    s.passes.add_pass(Pass("V-x", 99, "H-01", Arm.NOTES, "w", Composition("H-01", ()), Regime.R2))
    with pytest.raises(ValueError, match="notes-arm"):
        readings(s, SPEC)


def _tokens_for(stores, journal_tokens, notes_tokens):
    out = {}
    for p in stores.passes.passes():
        out[p.id] = journal_tokens if p.arm is Arm.JOURNAL else notes_tokens if p.arm is Arm.NOTES else 0
    return out


def test_r2_reads_leaner_only_when_the_bound_clears_and_the_journal_arm_composes_fewer_tokens():
    s = make_stores()
    plant(s, [0.02, -0.01, 0.03, 0.0, 0.01, -0.02], baseline=Arm.NOTES)
    s = dataclasses.replace(s, composed_tokens=_tokens_for(s, 300, 8000))
    r = readings(s, SPEC)
    assert -SPEC.readout.r2_margin < r.r2.lower_bound < 0 and r.r2.claim_above == -SPEC.readout.r2_margin
    assert r.r2.verdict == LEANER
    heavier = dataclasses.replace(s, composed_tokens=_tokens_for(s, 9000, 8000))
    assert readings(heavier, SPEC).r2.verdict == NOT_WORSE       # bound clears, journal tokens >= notes: not leaner
    equal = dataclasses.replace(s, composed_tokens=_tokens_for(s, 8000, 8000))
    assert readings(equal, SPEC).r2.verdict == NOT_WORSE


def test_r2_bound_below_minus_margin_is_unearned():
    s = make_stores()
    plant(s, [-0.2, -0.3, -0.25, -0.2, -0.22, -0.28], baseline=Arm.NOTES)
    s = dataclasses.replace(s, composed_tokens=_tokens_for(s, 300, 8000))
    r = readings(s, SPEC).r2
    assert r.lower_bound < -SPEC.readout.r2_margin and r.verdict == UNEARNED


def test_r2_an_uninformative_reading_never_earns_non_inferiority_by_default():
    # A wide spread: no harm is shown, but the bound sits far below -margin, so the claim is unearned.
    s = make_stores()
    plant(s, [0.4, -0.4, 0.3, -0.3, 0.2, -0.2], baseline=Arm.NOTES)
    r = readings(s, SPEC).r2
    assert r.lower_bound < -SPEC.readout.r2_margin and r.verdict == UNEARNED


def test_tokens_composed_read_unread_when_any_pass_lacks_a_count():
    s = make_stores()
    plant(s, [0.1] * 6, baseline=Arm.NOTES)
    tokens = _tokens_for(s, 300, 8000)
    tokens.pop(next(iter(tokens)))
    s = dataclasses.replace(s, composed_tokens=tokens)
    out = readings(s, SPEC)
    assert None in (out.tokens_composed[Arm.JOURNAL.value].mean, out.tokens_composed[Arm.NOTES.value].mean)
    assert out.r2.verdict == NOT_WORSE


def test_r2_line_shows_tokens_unread_without_changing_its_verdict():
    s = make_stores()                                  # composed_tokens left at its default: no counts stored
    plant(s, [0.1, 0.2, 0.05, 0.3, 0.15, 0.12], baseline=Arm.NOTES)
    out = readings(s, SPEC)
    assert out.r2.p == pytest.approx(1 / 64) and out.r2.verdict == NOT_WORSE
    r2 = next(line for line in lines(out) if line.startswith("R2"))
    assert "ratio unread" in r2 and r2.endswith(NOT_WORSE)


def flip(events, sid, old, new, cause, round_):
    events.append_flip(StatusFlip(sid, old.value, new.value, cause, round_))


A, S, R, C = DecisionStatus.ADMITTED, DecisionStatus.SUSPECT, DecisionStatus.RETIRED, DecisionStatus.CANDIDATE


def r3_stores():
    j = MemoryJournal()
    for i in range(1, 7):
        j.draft(dec(f"D-000{i}", status=C))
    return make_stores(journal=j)


def test_r3_windows_split_at_the_drift_round_without_off_by_one():
    drift = SPEC.drift.round
    s = r3_stores()
    ev = s.events
    # D-0001: admitted at drift-1, retired by the detector at the drift round: counted.
    flip(ev, "D-0001", C, A, FlipCause.GATE_ADMIT, drift - 1)
    flip(ev, "D-0001", A, S, FlipCause.DETECT_SUSPECT, drift)
    flip(ev, "D-0001", S, R, FlipCause.DETECT_RETIRE, drift)
    # D-0002: admitted at the drift round: counts as admitted after, never as before.
    flip(ev, "D-0002", C, A, FlipCause.GATE_ADMIT, drift)
    # D-0003: admitted and retired before the drift round: neither count.
    flip(ev, "D-0003", C, A, FlipCause.GATE_ADMIT, 2)
    flip(ev, "D-0003", A, R, FlipCause.SUPERSEDE, drift - 1)
    # D-0004: admitted before, retired after by a steer: reported apart, not counted.
    flip(ev, "D-0004", C, A, FlipCause.GATE_ADMIT, 3)
    flip(ev, "D-0004", A, R, FlipCause.STEER_RETIRE, drift + 2)
    # D-0005: admitted before, superseded after: counted.
    flip(ev, "D-0005", C, A, FlipCause.GATE_ADMIT, 4)
    flip(ev, "D-0005", A, R, FlipCause.SUPERSEDE, drift + 1)
    for did in ("D-0001", "D-0002", "D-0006"):
        s.ledger.append(LedgerDelta(did, 2, "c", DeltaKind.EXPOSURE, 0.1, 0.0))
    s.ledger.append(LedgerDelta("D-0001", 2, "c", DeltaKind.EXPOSURE, 0.1, 0.0))
    r3 = readings(s, SPEC).r3
    assert r3.admitted_before_retired_after == ("D-0001", "D-0005")
    assert r3.admitted_after == ("D-0002",)
    assert r3.steered_retired_after == ("D-0004",)
    assert r3.suspected_after == ("D-0001",)
    assert r3.c == 3 and r3.alpha_c == pytest.approx(SPEC.gate.alpha * 3)
    assert r3.verdict == FORGETS


def test_r3_without_an_admission_after_the_drift_is_unearned():
    s = r3_stores()
    flip(s.events, "D-0001", C, A, FlipCause.GATE_ADMIT, 1)
    flip(s.events, "D-0001", A, R, FlipCause.SUPERSEDE, SPEC.drift.round + 1)
    r3 = readings(s, SPEC).r3
    assert r3.verdict == UNEARNED and r3.c == 0 and r3.alpha_c == 0.0


def test_r3_with_no_decision_drafted_reads_unread():
    r3 = readings(make_stores(), SPEC).r3
    assert r3.verdict is None and r3.c is None and r3.alpha_c is None


def test_r4_counts_only_active_minted_lenses():
    minted_active = dataclasses.replace(lens("L-03"), minted_round=4)
    minted_hidden = dataclasses.replace(lens("L-04", LensStatus.HIDDEN), minted_round=6)
    minted_retired = dataclasses.replace(lens("L-05", LensStatus.RETIRED), minted_round=2)
    s = make_stores(lenses=[lens("L-01"), minted_active, minted_hidden, minted_retired])
    r4 = readings(s, SPEC).r4
    assert r4.kept_minted == ("L-03",) and r4.hidden_minted == ("L-04",) and r4.verdict == JUDGE_LEARNS
    only_baseline = make_stores(lenses=[lens("L-01"), minted_hidden])
    assert readings(only_baseline, SPEC).r4.verdict == UNEARNED
    assert readings(make_stores(), SPEC).r4.verdict is None


def test_cost_per_admitted_decision_reads_unread_with_no_admission():
    s = r3_stores()
    s.spend.record("worker", "t", ChatResult("x", "m", 1, 1, 1.5), True)
    assert readings(s, SPEC).cost.usd_per_admitted is None
    flip(s.events, "D-0001", C, A, FlipCause.GATE_ADMIT, 1)
    flip(s.events, "D-0002", C, A, FlipCause.GATE_ADMIT, 1)
    c = readings(s, SPEC).cost
    assert c.total_usd == 1.5 and c.admitted == 2 and c.usd_per_admitted == pytest.approx(0.75)


def _how_to(rung, n_declared, passed, invalid=0, undeclared=()):
    declared = tuple(Check(f"h{i}", (), {}) for i in range(n_declared))
    parse = HowToParse(rung, declared, invalid, (), tuple(undeclared), "summary")
    results = tuple(CheckResult(f"h{i}", i < passed) for i in range(n_declared))
    return HowToReading("x", (1280, 800), parse, results, 0.0)


def test_how_to_honesty_shares_per_arm():
    s = make_stores()
    add(s, "H-01", Arm.EMPTY, 0, 0.5, how_to=(_how_to(HowToRung.STRICT, 3, 2, invalid=1),), silent=2)
    add(s, "H-02", Arm.EMPTY, 0, 0.5, how_to=(_how_to(HowToRung.SALVAGED, 2, 2, undeclared=("#b",)),))
    add(s, "H-03", Arm.EMPTY, 0, 0.5)
    h = readings(s, SPEC).how_to[Arm.EMPTY.value]
    assert h.n_passes == 3 and h.no_how_to == 1
    assert h.declared_passed_share.mean == pytest.approx((2 / 4 + 1.0) / 2)
    assert h.contract_declared_share.mean == pytest.approx((1.0 + 0.5) / 2)
    assert h.silent_per_pass.mean == pytest.approx(2 / 3)
    assert h.rung_share == {"strict": 0.5, "repaired": 0.0, "salvaged": 0.5, "none": 0.0}


def test_hook_transfer_counts_held_out_requests_an_s_star_hook_fires_on():
    j = MemoryJournal()
    j.draft(dec("D-0001", status=C, fires=("chart",)))
    j.draft(dec("D-0002", status=C, fires=("timer",)))
    flip(s_events := MemoryEventLog(), "D-0001", C, A, FlipCause.GATE_ADMIT, 1)
    j.flip(s_events.flips()[0], "admit")
    reqs = [held_request("H-01", ("chart",)), held_request("H-02", ("timer",)), held_request("H-03", ("form",))]
    s = make_stores(journal=j, requests=reqs)
    h = readings(s, SPEC).hook_transfer
    assert h.s_star == ("D-0001",) and h.requests_fired == ("H-01",)
    assert readings(make_stores(requests=[]), SPEC).hook_transfer.population is None


def _admitted_canvas_journal():
    j = MemoryJournal()
    j.draft(dec("D-0001", status=C, fires=("canvas",)))
    flip(ev := MemoryEventLog(), "D-0001", C, A, FlipCause.GATE_ADMIT, 1)
    j.flip(ev.flips()[0], "admit")
    return j


def test_hook_transfer_reads_tags_from_the_vocabulary_and_untagged_held_out_requests_read_unread():
    # Request.tags are empty on held-out requests before tagging; the vocabulary holds the tags.
    reqs = [Request(f"H-0{i}", "r", frozenset(), {}, {Regime.R2: ()}, held_out=True) for i in range(1, 7)]
    untagged = make_stores(journal=_admitted_canvas_journal(), requests=reqs, tagged=False)
    h = readings(untagged, SPEC).hook_transfer
    assert h.population is None and h.requests_fired == ()
    assert h.untagged == tuple(r.id for r in reqs)
    assert ("Hook transfer: unread (6 held-out requests untagged)" in lines(readings(untagged, SPEC)))
    no_vocab = dataclasses.replace(untagged, tags=None)
    assert readings(no_vocab, SPEC).hook_transfer.population is None
    tagged = make_stores(journal=_admitted_canvas_journal(), requests=reqs, tagged=False)
    tagged.tags.set_tags({"H-01": frozenset({"canvas"}), **{r.id: frozenset({"form"}) for r in reqs[1:]}})
    h = readings(tagged, SPEC).hook_transfer
    assert h.population == "6 held-out requests, 1 decisions in S*" and h.requests_fired == ("H-01",)


def test_one_untagged_held_out_request_leaves_hook_transfer_unread():
    reqs = [held_request("H-01", ("canvas",)), held_request("H-02", ("form",))]
    s = make_stores(journal=_admitted_canvas_journal(), requests=reqs, tagged=False)
    s.tags.set_tags({"H-01": frozenset({"canvas"})})
    h = readings(s, SPEC).hook_transfer
    assert h.population is None and h.untagged == ("H-02",)


@pytest.mark.parametrize("decision_id", ["D-0002", "D-9999"])
def test_a_journal_arm_pass_composing_a_decision_outside_s_star_raises(decision_id):
    j = _admitted_canvas_journal()
    j.draft(dec("D-0002", status=C, fires=("form",)))      # a candidate: outside S*
    s = make_stores(journal=j)
    plant(s, [0.1] * 6)
    add(s, "H-01", Arm.JOURNAL, 9, 0.4, decisions=("D-0001", decision_id))
    with pytest.raises(ValueError, match="outside S"):
        readings(s, SPEC)


def test_a_journal_arm_pass_composing_s_star_is_accepted():
    s = make_stores(journal=_admitted_canvas_journal())
    plant(s, [0.1] * 6)
    add(s, "H-01", Arm.JOURNAL, 9, 0.4, decisions=("D-0001",))
    assert readings(s, SPEC).r1.n_categories == 6


def test_r1_verdict_and_bound_never_disagree():
    for diffs in ([0.9, 0.01, 0.01, 0.01, 0.01, 0.01], [0.9, -0.01, 0.01, 0.01, 0.01, 0.01],
                  [0.1, -0.1, 0.1, -0.1, 0.1, 0.0], [0.0] * 6):
        s = make_stores()
        plant(s, diffs)
        r1 = readings(s, SPEC).r1
        assert (r1.verdict == IMPROVES) == (r1.lower_bound is not None and r1.lower_bound > 0), diffs
        line = next(x for x in lines(readings(s, SPEC)) if x.startswith("R1"))
        assert "interval" not in line and "claim needs the bound above 0" in line


def test_r2_line_carries_the_token_ratio_and_its_margin():
    s = make_stores()
    plant(s, [-0.2, -0.3, -0.25, -0.2, -0.22, -0.28], baseline=Arm.NOTES)
    s = dataclasses.replace(s, composed_tokens=_tokens_for(s, 300, 8000))
    r2 = next(line for line in lines(readings(s, SPEC)) if line.startswith("R2"))
    assert "journal 300.0, notes 8000.0, ratio 0.037" in r2
    assert "claim needs the bound above -0.05" in r2 and r2.endswith(UNEARNED)
    unread = make_stores()
    plant(unread, [-0.2] * 6, baseline=Arm.NOTES)
    assert "ratio unread" in next(x for x in lines(readings(unread, SPEC)) if x.startswith("R2"))


def test_every_reading_prints_with_population_and_interval_or_p():
    s = make_stores()
    plant(s, [0.1, 0.2, 0.05, 0.3, 0.15, 0.12])
    out = lines(readings(s, SPEC))
    r1 = next(line for line in out if line.startswith("R1"))
    assert "6 held-out categories × 4 reps" in r1 and "p 0.0156" in r1 and "90 % lower bound" in r1
    for tag in ("R2", "R3", "R4", "Cost", "Q empty", "Hook transfer"):
        assert any(line.startswith(tag) for line in out), tag
