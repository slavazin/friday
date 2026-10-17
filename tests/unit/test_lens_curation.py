import random

from friday.algorithms.lens_curation import (
    CurationParams,
    WindowPass,
    cap_evictions,
    curate,
    high_loss_labels,
    residual_view,
)
from friday.domain.lifecycle import FlipCause
from friday.domain.types import LensStatus
from friday.stats.ranks import AucScore, SpearmanCorrelation

from .builders import lens

P = CurationParams(n_min=32, flag_p=0.5, floor_quantile=0.95, floor_shuffles=200, floor_strikes=2,
                   fold_rho=0.85, residual_loss=0.5, cap=10)


def window(n=64, seed=0):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        loss = rng.random()
        out.append(WindowPass(f"x{i}", loss, {
            "L-01": loss,                          # perfectly predictive
            "L-02": rng.random(),                  # noise
            "L-03": min(1.0, loss + 0.01),         # a near copy of L-01: folds
            "L-09": loss * 0.9,                    # minted, hidden, predictive
        }))
    return out


def run(lenses, w, strikes=None):
    return curate(lenses, w, strikes or {}, P, AucScore(), SpearmanCorrelation(), seed=1, round_=4)


def test_median_ties_are_negative():
    assert high_loss_labels([0.1, 0.5, 0.5, 0.9]) == [False, False, False, True]


def test_predictive_lens_beats_its_floor_and_noise_takes_a_strike():
    res = run([lens("L-01"), lens("L-02")], window())
    assert res.values["L-01"] > res.floors["L-01"]
    assert res.strikes["L-02"] == 1 and res.strikes["L-01"] == 0
    assert not [f for f in res.flips if f.cause is FlipCause.LENS_FLOOR]


def test_second_consecutive_strike_retires():
    res = run([lens("L-02")], window(), {"L-02": 1})
    assert [(f.lens_id, f.new) for f in res.flips] == [("L-02", LensStatus.RETIRED)]


def test_hidden_lens_becomes_active_on_clearing_its_floor():
    res = run([lens("L-09", LensStatus.HIDDEN)], window())
    assert [(f.lens_id, f.cause) for f in res.flips] == [("L-09", FlipCause.LENS_CLEAR_FLOOR)]


def test_correlated_lenses_fold_into_the_higher_value():
    res = run([lens("L-01"), lens("L-03")], window())
    folds = [f for f in res.flips if f.cause is FlipCause.LENS_FOLD]
    assert len(folds) == 1 and folds[0].new is LensStatus.SUPERSEDED
    kept = folds[0].superseded_by
    assert res.values[kept] >= res.values[folds[0].lens_id]


def test_too_few_answers_leave_a_lens_unvalued():
    res = run([lens("L-01")], window(n=20))
    assert res.values["L-01"] is None and res.flips == ()


def test_residual_view_is_high_loss_nobody_flagged():
    w = [WindowPass("a", 0.9, {"L-01": 0.1}), WindowPass("b", 0.9, {"L-01": 0.8}),
         WindowPass("c", 0.2, {"L-01": 0.0})]
    assert residual_view(w, ["L-01"], 0.5, 0.5) == ("a",)


def test_cap_evicts_lowest_value_past_n_min_or_refuses():
    live = [lens(f"L-0{i}") for i in range(1, 4)]
    values = {"L-01": 0.7, "L-02": 0.55, "L-03": None}
    evict, added = cap_evictions(live, values, {"L-01": 40, "L-02": 40, "L-03": 10}, proposals=2, cap=3, n_min=32)
    assert evict == ["L-02", "L-01"] and added == 2
    evict, added = cap_evictions(live, values, {"L-01": 5, "L-02": 5}, proposals=1, cap=3, n_min=32)
    assert evict == [] and added == 0
    assert cap_evictions(live, values, {}, proposals=1, cap=5, n_min=32) == ([], 1)
