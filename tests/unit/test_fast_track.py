"""Fast-track in the recurrence bar (SPEC § 7 nomination) and its interaction with the refused-pool block (§ 8)."""

import inspect
from pathlib import Path

from friday.algorithms import gate, recurrence
from friday.algorithms.recurrence import RecurrenceBar, Route, nomination_queue, nominations
from friday.config import load_spec
from friday.domain.types import Observation, ObservationStatus, Pool, PoolStatus

ROOT = Path(__file__).resolve().parents[2]
SHARE = load_spec(ROOT / "run.yml").pool.fast_track_share
BAR = RecurrenceBar(3, 2, SHARE)
_n = iter(range(1, 10_000))


def obs(rnd, label, pool="P-0001", status=ObservationStatus.OPEN, request=None):
    i = next(_n)                                    # each observation from its own request unless one is given
    return Observation(f"O-{i:04d}", f"x{i}", rnd, "L-03", request or f"cat{i}", "shape", "dir", "q", status, pool,
                       label)


def pool(pid, status=PoolStatus.OPEN, refused_at=None, labels=("a", "b")):
    return Pool(pid, "L-03", "shape", labels, status, refused_at_round=refused_at)


def test_a_unanimous_pool_is_fast_tracked_in_its_first_round():
    p = pool("P-0001")
    ob = [obs(1, "a"), obs(1, "a"), obs(1, "a")]
    assert BAR.label_at_bar(p, ob) is None                      # one round: the bar is not reached
    assert BAR.fast_tracked_label(p, ob) == "a"
    assert nominations([p], ob, BAR, room=2)[0].route is Route.FAST_TRACK


def test_fast_tracked_pool_goes_ahead_of_an_older_bar_reached_pool():
    old, young = pool("P-0001"), pool("P-0007")
    ob = [obs(1, "a", "P-0001"), obs(2, "a", "P-0001"), obs(2, "b", "P-0001"), obs(2, "b", "P-0001"),
          obs(2, "a", "P-0001"),                                  # P-0001: a 3 of 5 over two rounds, b 2
          obs(2, "a", "P-0007"), obs(2, "a", "P-0007"), obs(2, "a", "P-0007")]
    assert BAR.fast_tracked_label(old, ob) is None              # 3/5 < 2/3
    got = nominations([old, young], ob, BAR, room=2)
    assert [(n.pool.id, n.label, n.route) for n in got] == [
        ("P-0007", "a", Route.FAST_TRACK), ("P-0001", "a", Route.BAR)]
    assert [p.id for p, _ in nomination_queue([old, young], ob, BAR, room=1)] == ["P-0007"]


def test_oldest_first_within_each_route():
    pools = [pool(f"P-000{k}") for k in (4, 2, 3, 1)]
    ob = [obs(1, "a", p.id) for p in pools for _ in range(3)]
    assert [n.pool.id for n in nominations(pools, ob, BAR, room=10)] == ["P-0001", "P-0002", "P-0003", "P-0004"]


def test_leading_label_below_the_share_is_not_fast_tracked():
    p = pool("P-0001")
    ob = [obs(1, "a"), obs(1, "a"), obs(1, "b"), obs(1, "b")]
    assert BAR.fast_tracked_label(p, ob) is None
    assert nominations([p], ob, BAR, room=2) == []


def test_unlabelled_open_observations_count_against_the_share():
    p = pool("P-0001")
    ob = [obs(1, "a"), obs(1, "a"), obs(1, None), obs(1, None)]
    assert BAR.fast_tracked_label(p, ob) is None


def test_fewer_than_min_observations_is_never_fast_tracked():
    p = pool("P-0001")
    assert BAR.fast_tracked_label(p, [obs(1, "a"), obs(1, "a")]) is None


def test_closed_observations_do_not_count_for_fast_track():
    p = pool("P-0001")
    ob = [obs(1, "a"), obs(1, "a"), obs(1, "a", status=ObservationStatus.CLOSED)]
    assert BAR.fast_tracked_label(p, ob) is None


def test_refused_pool_with_only_pre_refusal_observations_is_never_fast_tracked():
    p = pool("P-0001", PoolStatus.REFUSED, refused_at=4)
    old = [obs(r, "a") for r in (2, 3, 3, 4, 4, 4)]             # unanimous, plentiful, all dated <= refusal
    assert BAR.fast_tracked_label(p, old) is None
    assert BAR.label_at_bar(p, old) is None
    assert nominations([p], old, BAR, room=2) == []
    # two fresh ones are not enough on their own, however many old ones agree
    fresh2 = [*old, obs(5, "a"), obs(5, "a")]
    assert nominations([p], fresh2, BAR, room=2) == []
    fresh3 = [*fresh2, obs(5, "a")]
    got = nominations([p], fresh3, BAR, room=2)
    assert [(n.pool.id, n.route) for n in got] == [("P-0001", Route.FAST_TRACK)]


def test_old_disagreement_does_not_block_fresh_agreement_after_refusal():
    p = pool("P-0001", PoolStatus.REFUSED, refused_at=4)
    ob = [obs(3, "b"), obs(3, "b"), obs(3, "b"), obs(5, "a"), obs(5, "a"), obs(5, "a")]
    assert BAR.fast_tracked_label(p, ob) == "a"


def test_refused_pool_without_a_refusal_round_raises():
    p = pool("P-0001", PoolStatus.REFUSED, refused_at=None)
    try:
        BAR.fast_tracked_label(p, [obs(1, "a")] * 3)
    except ValueError:
        return
    raise AssertionError("a refused pool with no refusal round must raise, not count every observation")


def test_nominated_and_merged_pools_are_skipped_by_both_routes():
    pools = [pool("P-0001", PoolStatus.NOMINATED), Pool("P-0002", "L-03", "s", ("a",), PoolStatus.MERGED,
                                                         merged_into="P-0001")]
    ob = [obs(r, "a", p.id) for p in pools for r in (1, 2, 2)]
    assert nominations(pools, ob, BAR, room=5) == []


def test_without_a_share_no_pool_is_fast_tracked():
    p = pool("P-0001")
    ob = [obs(1, "a")] * 3
    assert RecurrenceBar(3, 2).fast_tracked_label(p, ob) is None
    assert nominations([p], ob, RecurrenceBar(3, 2), room=1) == []


def test_room_bounds_the_queue_across_both_routes():
    pools = [pool(f"P-000{k}") for k in range(1, 5)]
    ob = [obs(1, "a", p.id) for p in pools for _ in range(3)]
    assert len(nominations(pools, ob, BAR, room=2)) == 2
    assert nominations(pools, ob, BAR, room=0) == []


def test_fast_track_reads_no_gate_code():
    src = inspect.getsource(recurrence)
    assert "gate" not in {m.split(".")[-1] for m in src.split() if m.startswith("friday.")}
    assert "from friday.algorithms.gate" not in src and "eprocess" not in src
    assert not hasattr(gate, "fast_track_share")


SPEC_BAR = RecurrenceBar(3, 2, SHARE, min_requests=2, fast_track_min_requests=3)


def test_two_passes_of_one_request_never_reach_the_bar_or_the_fast_track():
    p = pool("P-0001")
    ob = [obs(1, "a", request="Q-1"), obs(1, "a", request="Q-1"), obs(2, "a", request="Q-1"),
          obs(2, "a", request="Q-1")]                               # four passes, one request, two rounds
    assert SPEC_BAR.label_at_bar(p, ob) is None
    assert SPEC_BAR.fast_tracked_label(p, ob) is None
    ob.append(obs(2, "a", request="Q-2"))                           # a second request reaches the bar
    assert SPEC_BAR.label_at_bar(p, ob) == "a" and SPEC_BAR.fast_tracked_label(p, ob) is None


def test_fast_track_share_is_over_distinct_requests_not_passes():
    p = pool("P-0001")
    # label a: 4 passes from 2 requests; label b: 2 passes from 2 requests -> 4/6 of passes, 2/4 of requests
    ob = [obs(1, "a", request="Q-1"), obs(1, "a", request="Q-1"), obs(1, "a", request="Q-2"),
          obs(1, "a", request="Q-2"), obs(1, "b", request="Q-3"), obs(1, "b", request="Q-4")]
    assert SPEC_BAR.fast_tracked_label(p, ob) is None
    ob.append(obs(1, "a", request="Q-5"))                           # a holds 3 of 5 requests: still under 2/3
    assert SPEC_BAR.fast_tracked_label(p, ob) is None
    ob.append(obs(1, "a", request="Q-6"))                           # 4 of 6 requests
    assert SPEC_BAR.fast_tracked_label(p, ob) == "a"
