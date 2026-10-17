import random
import statistics

import pytest

from friday.algorithms.gate import GateAction, GateRule, select_in_flight
from friday.domain.types import Decision, DecisionStatus, Hook
from friday.stats.bounds import StudentTUpperBound
from friday.stats.eprocess import AGrapa


def rule(**kw):
    base = dict(alpha=0.2, n_min=4, n_max=24, quality_margin=2.0, quality_level=0.9,
                betting=AGrapa(0.25, 0.9, 1e-9), bound=StudentTUpperBound())
    base.update(kw)
    return GateRule(**base)


def test_no_pairs_holds():
    r = rule().read([], [])
    assert r.action is GateAction.HOLD and r.wealth == 1.0 and r.mean_d is None


def test_strong_help_admits_once_n_min_reached():
    g = rule(n_min=6)
    early = g.read([0.9] * 5, [0.0] * 5)
    assert early.wealth >= 5 and early.action is GateAction.HOLD       # wealth past the bar, n < n_min
    r = g.read([0.9] * 6, [0.0] * 6)
    assert r.action is GateAction.ADMIT and r.wealth >= 5


def test_quality_guard_blocks_admission():
    r = rule().read([0.6] * 6, [5.0, 4.0, 6.0, 5.0, 5.0, 4.0])
    assert r.action is GateAction.HOLD and r.q_upper > 2.0


def test_harm_refuses():
    assert rule().read([-0.6] * 5, [0.0] * 5).action is GateAction.REFUSE


def test_expires_at_n_max_without_a_verdict():
    ds = [0.05, -0.05] * 12
    assert rule().read(ds, [0.0] * 24).action is GateAction.EXPIRE


def test_admit_takes_precedence_over_expire_at_n_max():
    r = rule(n_max=5).read([0.6] * 5, [0.0] * 5)
    assert r.action is GateAction.ADMIT


def test_in_flight_is_drafting_order():
    mk = lambda i, r: Decision(i, DecisionStatus.CANDIDATE, None, Hook(frozenset({"a"})), "p", drafted_round=r)  # noqa: E731
    got = select_in_flight([mk("D-0003", 1), mk("D-0001", 2), mk("D-0002", 1)], 2)
    assert [d.id for d in got] == ["D-0002", "D-0003"]


def _simulate(effect: float, runs: int, seed: int) -> tuple[float, float | None]:
    g = rule()
    rng = random.Random(seed)
    admitted_at = []
    for _ in range(runs):
        ds: list[float] = []
        for _ in range(24):
            ds.append(max(-1.0, min(1.0, rng.gauss(effect, 0.2))))
            a = g.read(ds, [0.0] * len(ds)).action
            if a is GateAction.ADMIT:
                admitted_at.append(len(ds))
                break
            if a is GateAction.REFUSE:
                break
    rate = len(admitted_at) / runs
    return rate, (statistics.median(admitted_at) if admitted_at else None)


@pytest.mark.slow
@pytest.mark.parametrize(("effect", "rate", "median"), [
    (0.10, 0.51, None), (0.15, 0.89, 16), (0.20, 0.99, 12), (0.30, None, 8),
])
def test_resolving_power_matches_the_spec_simulation(effect, rate, median):
    """SPEC § 8: Gaussian deltas sd 0.2 clipped to [-1, 1], admitted within 24 pairs.

    Tolerance: 3 binomial standard errors at 4,000 runs (at most 0.024), and 2 pairs on the median.
    """
    got_rate, got_median = _simulate(effect, 4000, seed=int(effect * 1000) + 1)
    if rate is not None:
        assert got_rate == pytest.approx(rate, abs=3 * (rate * (1 - rate) / 4000) ** 0.5 + 0.005)
    if median is not None:
        assert abs(got_median - median) <= 2


@pytest.mark.slow
def test_false_admission_rate_at_no_effect():
    """SPEC § 8 reports 1.4 % at effect 0; measured here about 1 % (seed-dependent). Pre-registered bound:
    at most 2 % within 24 pairs, far below alpha = 20 %."""
    rate, _ = _simulate(0.0, 4000, seed=1)
    assert rate <= 0.02
