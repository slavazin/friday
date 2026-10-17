"""The recurrence bar, nomination order and observation expiry (SPEC §§ 7, 8)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from friday.domain.types import Observation, ObservationStatus, Pool, PoolStatus


class Route(StrEnum):
    """Which rule sent a pool to the nominator (SPEC § 7)."""

    FAST_TRACK = "fast-track"
    BAR = "bar"


@dataclass(frozen=True)
class Nomination:
    pool: Pool
    label: str
    route: Route


@dataclass(frozen=True)
class RecurrenceBar:
    """The recurrence bar and the fast-track rule. With `fast_track_share` None no pool is fast-tracked.

    Both rules decide only when a pool goes to the nominator; neither reads or moves a gate constant.
    """

    min_observations: int
    min_rounds: int
    fast_track_share: float | None = None
    min_requests: int = 1               # distinct requests (categories) behind the label's observations
    fast_track_min_requests: int = 1    # distinct requests behind a fast-tracked pool's observations

    def label_at_bar(self, pool: Pool, observations: Sequence[Observation]) -> str | None:
        """The direction label whose open observations in `pool` reach the bar; None when none does.

        For a refused pool only observations dated after the refusal count (the refused-pool block).
        Ties between labels go to the label with more observations, then the label minted first.
        """
        if pool.status is PoolStatus.MERGED:
            return None
        for label, obs in _ranked_labels(pool, _counted(pool, observations)):
            if (len(obs) >= self.min_observations and len({o.category for o in obs}) >= self.min_requests
                    and len({o.round for o in obs}) >= self.min_rounds):
                return label
        return None

    def fast_tracked_label(self, pool: Pool, observations: Sequence[Observation]) -> str | None:
        """The leading direction label when `pool` is fast-tracked; None when it is not.

        Fast-tracked: at least `min_observations` counted open observations from at least
        `fast_track_min_requests` distinct requests, and the leading label holds at least `fast_track_share` of
        those requests (SPEC § 7 Counting: shares are over distinct requests, so the two passes of a pair count
        once; a request with only unlabeled observations counts in the denominator only).
        No round spread is required. For a refused pool only observations dated after the refusal count,
        exactly as for the bar.
        """
        if self.fast_track_share is None or pool.status is PoolStatus.MERGED:
            return None
        counted = _counted(pool, observations)
        requests = {o.category for o in counted}
        if len(counted) < self.min_observations or len(requests) < self.fast_track_min_requests:
            return None
        by_requests = sorted(((lbl, {o.category for o in obs}) for lbl, obs in _ranked_labels(pool, counted)),
                             key=lambda kv: (-len(kv[1]), _label_index(pool, kv[0])))
        if not by_requests:
            return None
        label, reqs = by_requests[0]
        return label if len(reqs) / len(requests) >= self.fast_track_share else None


def _counted(pool: Pool, observations: Sequence[Observation]) -> list[Observation]:
    """The open observations of `pool` that count toward nomination (after the refusal, for a refused pool)."""
    after = pool.refused_at_round if pool.status is PoolStatus.REFUSED else None
    if pool.status is PoolStatus.REFUSED and after is None:
        raise ValueError(f"{pool.id} is refused but records no refusal round")
    return [
        o for o in observations
        if o.pool_id == pool.id and o.status is ObservationStatus.OPEN and (after is None or o.round > after)
    ]


def _ranked_labels(pool: Pool, counted: Sequence[Observation]) -> list[tuple[str, list[Observation]]]:
    """Labels by observation count, then the label minted first."""
    by_label: dict[str, list[Observation]] = defaultdict(list)
    for o in counted:
        if o.direction_label is not None:
            by_label[o.direction_label].append(o)
    return sorted(by_label.items(), key=lambda kv: (-len(kv[1]), _label_index(pool, kv[0])))


def _label_index(pool: Pool, label: str) -> int:
    return pool.direction_labels.index(label) if label in pool.direction_labels else len(pool.direction_labels)


def nominations(
    pools: Sequence[Pool], observations: Sequence[Observation], bar: RecurrenceBar, room: int,
) -> list[Nomination]:
    """At most `room` pools to nominate this close, with the route that sent each.

    Fast-tracked pools come first, then pools at the bar; oldest id first within each. A pool that
    qualifies by both routes is fast-tracked. Nominated and merged pools are skipped.
    """
    fast: list[Nomination] = []
    slow: list[Nomination] = []
    for pool in sorted(pools, key=lambda p: p.id):
        if pool.status in (PoolStatus.NOMINATED, PoolStatus.MERGED):
            continue
        label = bar.fast_tracked_label(pool, observations)
        if label is not None:
            fast.append(Nomination(pool, label, Route.FAST_TRACK))
            continue
        label = bar.label_at_bar(pool, observations)
        if label is not None:
            slow.append(Nomination(pool, label, Route.BAR))
    return [*fast, *slow][:max(room, 0)]


def nomination_queue(
    pools: Sequence[Pool], observations: Sequence[Observation], bar: RecurrenceBar, room: int,
) -> list[tuple[Pool, str]]:
    """(pool, label) pairs of `nominations`, in its order: fast-tracked first, then at the bar."""
    return [(n.pool, n.label) for n in nominations(pools, observations, bar, room)]


def expired(observations: Sequence[Observation], round_: int, expiry_rounds: int) -> list[str]:
    """Ids of open observations older than `expiry_rounds` rounds at `round_`."""
    return [
        o.id for o in observations
        if o.status is ObservationStatus.OPEN and round_ - o.round > expiry_rounds
    ]
