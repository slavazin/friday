import pytest

from friday.algorithms.halt import HaltReason, RoundSummary, halt_reason
from friday.algorithms.language import check_payload, quote_in, word_hits
from friday.algorithms.loss import LossModel
from friday.algorithms.readout import paired_reading, per_category_mean_diff
from friday.algorithms.recurrence import RecurrenceBar, expired, nomination_queue
from friday.algorithms.split import draw_split
from friday.domain.types import CheckResult, Observation, ObservationStatus, Pool, PoolStatus
from friday.stats.significance import SignFlipExact

from .builders import req


def test_loss_matches_the_formula():
    lm = LossModel(0.75, 0.25, (1, 5))
    a = lm.acceptance([CheckResult("a", True), CheckResult("b", False), CheckResult("c", True), CheckResult("d", True)])
    r = lm.rubric([5, 3, 1])
    assert a == 0.75 and r == 0.5
    assert lm.loss(a, r) == pytest.approx(1 - (0.75 * 0.75 + 0.25 * 0.5))
    with pytest.raises(ValueError):
        lm.rubric([6])


def obs(i, rnd, label, pool="P-0001", status=ObservationStatus.OPEN):
    return Observation(f"O-{i:04d}", f"x{i}", rnd, "L-03", "cat", "shape", "dir", "q", status, pool, label)


BAR = RecurrenceBar(3, 2)
POOL = Pool("P-0001", "L-03", "shape", ("bind on window", "focus canvas"))


def test_bar_needs_three_open_from_two_rounds_under_one_label():
    assert BAR.label_at_bar(POOL, [obs(1, 1, "bind on window"), obs(2, 1, "bind on window"),
                                   obs(3, 1, "bind on window")]) is None
    assert BAR.label_at_bar(POOL, [obs(1, 1, "bind on window"), obs(2, 2, "focus canvas"),
                                   obs(3, 2, "bind on window")]) is None
    got = [obs(1, 1, "bind on window"), obs(2, 2, "bind on window"), obs(3, 2, "bind on window")]
    assert BAR.label_at_bar(POOL, got) == "bind on window"
    closed = [*got[:2], obs(3, 2, "bind on window", status=ObservationStatus.CLOSED)]
    assert BAR.label_at_bar(POOL, closed) is None


def test_refused_pool_counts_only_observations_after_the_refusal():
    refused = Pool("P-0001", "L-03", "shape", ("bind on window",), PoolStatus.REFUSED, refused_at_round=4)
    old = [obs(i, r, "bind on window") for i, r in [(1, 3), (2, 4), (3, 4), (4, 5)]]
    assert BAR.label_at_bar(refused, old) is None
    new = [*old, obs(5, 6, "bind on window"), obs(6, 6, "bind on window")]
    assert BAR.label_at_bar(refused, new) == "bind on window"


def test_nomination_queue_oldest_first_and_bounded():
    pools = [Pool("P-0002", "L-03", "s", ("a",)), Pool("P-0001", "L-03", "s", ("a",)),
             Pool("P-0003", "L-03", "s", ("a",), PoolStatus.NOMINATED)]
    ob = [obs(i, 1 + i % 2, "a", pool=p) for p in ("P-0001", "P-0002", "P-0003") for i in range(3)]
    ob = [Observation(f"O-{k:04d}", o.pass_id, o.round, o.lens_id, o.category, o.shape, o.direction, o.quote,
                      o.status, o.pool_id, o.direction_label) for k, o in enumerate(ob)]
    q = nomination_queue(pools, ob, BAR, room=1)
    assert [p.id for p, _ in q] == ["P-0001"]


def test_open_observations_expire_after_the_window():
    o = [obs(1, 1, "a"), obs(2, 3, "a"), obs(3, 1, "a", status=ObservationStatus.CLOSED)]
    assert expired(o, 8, 6) == ["O-0001"]


def test_split_keeps_held_out_tags_inside_training_tags():
    reqs = [req(f"r{i:02d}", "a", "b") for i in range(10)] + [req("odd", "zzz")]
    train, held, draws = draw_split(reqs, 3, seed=5, max_draws=1000)
    assert "odd" in train and len(held) == 3 and draws >= 1
    assert draw_split(reqs, 3, 5, 1000) == (train, held, draws)
    assert draw_split([req("x", "p"), req("y", "q")], 1, 5, 50) is None


def test_halt_conditions():
    s = [RoundSummary(r, 0, 0, 0) for r in range(1, 12)]
    assert halt_reason(s[:5], 14, 1.0, 30.0, 8, 3) is None
    assert halt_reason(s[:5], 14, 31.0, 30.0, 8, 3) is HaltReason.SPEND
    assert halt_reason(s, 14, 1.0, 30.0, 8, 3) is HaltReason.CONVERGED
    busy = [*s[:10], RoundSummary(11, 1, 0, 0)]
    assert halt_reason(busy, 14, 1.0, 30.0, 8, 3) is None
    assert halt_reason([RoundSummary(14, 1, 1, 1)], 14, 0, 30, 8, 3) is HaltReason.ROUNDS


def test_payload_checks_bound_leak_reference_and_hedge():
    good = check_payload("Bind `keydown` on `window`, not on the canvas.", 60, ["check", "loss"], ["consider"])
    assert not good.refusable and good.hedges == ()
    bad = check_payload("Consider fixing what O-0112 showed; the check fails.", 60, ["check"], ["consider"])
    assert bad.refusable and bad.run_ids == ("O-0112",) and bad.blinded == ("check",) and bad.hedges == ("consider",)
    assert check_payload("word " * 61, 60, [], []).too_long


def test_word_hits_respect_boundaries():
    assert word_hits("the checkbox", ["check"]) == []
    assert word_hits("Re-check it", ["check"]) == []
    assert word_hits("one check.", ["check"]) == ["check"]
    assert quote_in("let x", "const a=1; let x = 2") and not quote_in("let y", "let x")


def test_readout_pairs_by_category():
    base = [("a", 0.6), ("a", 0.4), ("b", 0.5)]
    treated = [("a", 0.3), ("b", 0.4), ("b", 0.4)]
    with pytest.raises(ValueError, match="different categories"):
        per_category_mean_diff([*base, ("c", 0.9)], treated)
    diffs = per_category_mean_diff(base, treated)
    assert diffs == pytest.approx({"a": 0.2, "b": 0.1})
    r = paired_reading(diffs, SignFlipExact(), 0.9)
    assert r.n_categories == 2 and r.p == 0.25 and r.interval[0] < r.mean < r.interval[1]
