import math
import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from friday.stats.bounds import StudentTUpperBound, t_cdf, t_interval, t_quantile
from friday.stats.eprocess import AGrapa, FixedBet, mirror_wealth, wealth, wealth_path
from friday.stats.ranks import auc, average_ranks, spearman
from friday.stats.resampling import percentile, permutation_quantile
from friday.stats.rng import bernoulli, coin, derive
from friday.stats.significance import SignFlipExact

# Student t quantiles from standard tables.
T_TABLE = [(0.90, 3, 1.6377), (0.80, 3, 0.9785), (0.95, 5, 2.0150), (0.975, 10, 2.2281), (0.90, 1, 3.0777)]


@pytest.mark.parametrize(("p", "df", "want"), T_TABLE)
def test_t_quantile_matches_tables(p, df, want):
    assert t_quantile(p, df) == pytest.approx(want, abs=2e-4)


def test_t_cdf_is_symmetric_and_centred():
    assert t_cdf(0.0, 4) == pytest.approx(0.5)
    assert t_cdf(1.3, 7) + t_cdf(-1.3, 7) == pytest.approx(1.0)


def test_upper_bound_needs_two_points_and_sits_above_the_mean():
    ub = StudentTUpperBound()
    assert ub.upper([0.3], 0.8) is None
    xs = [0.1, -0.2, 0.05, 0.0]
    assert ub.upper(xs, 0.8) > sum(xs) / 4
    assert ub.upper([0.0, 0.0, 0.0], 0.9) == pytest.approx(0.0)


def test_t_interval_is_centred_on_the_mean():
    lo, hi = t_interval([1.0, 2.0, 3.0], 0.9)
    assert (lo + hi) / 2 == pytest.approx(2.0)
    assert hi - lo == pytest.approx(2 * 2.919986 * 1.0 / math.sqrt(3), rel=1e-4)


def test_agrapa_wealth_matches_hand_computation():
    strat = AGrapa(0.25, 0.9, 1e-9)
    # t=1: lambda 0.25 -> 1 + 0.25*0.2 = 1.05; t=2: past [0.2], m=0.2, v=0 -> lambda=5 -> clip 0.9
    path = wealth_path([0.2, -0.1], strat)
    assert path[0] == pytest.approx(1.05)
    assert path[1] == pytest.approx(1.05 * (1 - 0.09))


def test_bets_use_only_past_deltas():
    strat = AGrapa()
    a = wealth_path([0.3, 0.1, -0.5], strat)
    b = wealth_path([0.3, 0.1, 0.9], strat)
    assert a[:2] == b[:2]


@given(st.lists(st.floats(-1, 1, allow_nan=False), max_size=40))
def test_wealth_stays_positive(ds):
    assert wealth(ds, AGrapa()) > 0
    assert mirror_wealth(ds, AGrapa()) > 0


def test_delta_outside_unit_interval_raises():
    with pytest.raises(ValueError):
        wealth([1.5], FixedBet())


def test_lambda_max_at_one_is_refused():
    with pytest.raises(ValueError):
        AGrapa(lambda_max=1.0)


def test_null_crossing_rate_stays_below_alpha():
    """Ville: P(sup W >= 1/alpha) <= alpha under E[d] = 0."""
    rng = random.Random(7)
    crossed = 0
    runs = 2000
    for _ in range(runs):
        ds = [max(-1.0, min(1.0, rng.gauss(0, 0.3))) for _ in range(60)]
        crossed += any(w >= 5 for w in wealth_path(ds, AGrapa()))
    assert crossed / runs <= 0.2


def test_average_ranks_handle_ties():
    assert average_ranks([3, 1, 3, 2]) == [3.5, 1.0, 3.5, 2.0]


def test_auc_perfect_inverse_tied_and_undefined():
    assert auc([0.9, 0.8, 0.1, 0.2], [True, True, False, False]) == 1.0
    assert auc([0.1, 0.2, 0.9, 0.8], [True, True, False, False]) == 0.0
    assert auc([0.5, 0.5, 0.5, 0.5], [True, False, True, False]) == 0.5
    assert auc([0.1, 0.2], [True, True]) is None


def test_spearman_monotone_and_constant():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 45]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert spearman([1, 1, 1], [1, 2, 3]) is None


def test_percentile_interpolates():
    assert percentile([1, 2, 3, 4], 0.5) == 2.5
    assert percentile([5], 0.95) == 5
    assert percentile([0, 10], 0.95) == pytest.approx(9.5)


def test_permutation_quantile_is_seeded():
    labels = [True, False] * 10
    scores = list(range(20))

    def stat(ys):
        return auc(scores, ys)

    a = permutation_quantile(stat, labels, 50, 0.95, random.Random(1))
    b = permutation_quantile(stat, labels, 50, 0.95, random.Random(1))
    assert a == b and 0.5 < a < 1.0


def test_sign_flip_smallest_p_and_symmetry():
    t = SignFlipExact()
    assert t.p_value([0.1] * 6) == pytest.approx(1 / 64)
    assert t.p_value([-0.1] * 6) == pytest.approx(1.0)
    assert t.p_value([0.2, -0.2]) == pytest.approx(0.75)


def test_draws_depend_only_on_their_keys():
    assert coin(1, "exposure", 3, "cat-a") == coin(1, "exposure", 3, "cat-a")
    assert derive(1, "x").random() != derive(2, "x").random()
    hits = sum(bernoulli(0.75, 9, "ablate", r, "c") for r in range(4000))
    assert 0.72 < hits / 4000 < 0.78
