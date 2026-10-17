from friday.algorithms.detector import ConfirmAction, Detector, SuspectReason
from friday.domain.types import Disposition as Dp
from friday.stats.bounds import StudentTUpperBound
from friday.stats.eprocess import AGrapa


def det(**kw):
    base = dict(window=4, level=0.8, alpha=0.2, confirm_pairs=3, contradicted_share=0.5,
                betting=AGrapa(), bound=StudentTUpperBound())
    base.update(kw)
    return Detector(**base)


def test_helpful_decision_is_not_suspect():
    r = det().read([0.2, 0.1, 0.15, 0.2], [Dp.APPLIED] * 4)
    assert not r.suspect


def test_short_window_needs_a_full_window():
    assert det().read([-0.3, -0.3, -0.3], []).window_upper is None


def test_stale_decision_trips_the_short_window():
    r = det().read([0.2, 0.2, -0.2, -0.25, -0.15, -0.2], [])
    assert SuspectReason.SHORT_WINDOW in r.reasons


def test_slow_harm_trips_the_long_run():
    r = det(window=50).read([-0.3] * 8, [])
    assert r.reasons == (SuspectReason.LONG_RUN,)


def test_contradicted_majority_suspects_and_undisposed_is_skipped():
    r = det().read([], [Dp.CONTRADICTED, Dp.UNDISPOSED, Dp.CONTRADICTED, Dp.CONTRADICTED, Dp.APPLIED])
    assert r.reasons == (SuspectReason.CONTRADICTED,) and r.contradicted_share == 0.75
    assert not det().read([], [Dp.CONTRADICTED, Dp.CONTRADICTED, Dp.APPLIED, Dp.APPLIED]).suspect


def test_confirmation_waits_retires_or_clears():
    d = det()
    assert d.confirm([-0.2, -0.2]).action is ConfirmAction.WAIT
    assert d.confirm([-0.2, -0.25, -0.15, 0.9]).action is ConfirmAction.RETIRE   # only the first 3 count
    assert d.confirm([-0.2, 0.3, 0.1]).action is ConfirmAction.CLEAR
