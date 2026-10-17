"""The recurrence bar, nomination order and observation expiry (SPEC §§ 7, 8)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from friday.domain.types import Observation, ObservationStatus, Pool, PoolStatus


@dataclass(frozen=True)
class RecurrenceBar:
    min_observations: int
    min_rounds: int

    def label_at_bar(self, pool: Pool, observations: Sequence[Observation]) -> str | None:
        """The direction label whose open observations in `pool` reach the bar; None when none does.

        For a refused pool only observations dated after the refusal count (the refused-pool block).
        Ties between labels go to the label with more observations, then the label minted first.
        """
        if pool.status is PoolStatus.MERGED:
            return None
        after = pool.refused_at_round if pool.status is PoolStatus.REFUSED else None
        by_label: dict[str, list[Observation]] = defaultdict(list)
        for o in observations:
            if o.pool_id != pool.id or o.direction_label is None:
                continue
            if o.status is not ObservationStatus.OPEN or (after is not None and o.round <= after):
                continue
            by_label[o.direction_label].append(o)
        ranked = sorted(
            by_label.items(),
            key=lambda kv: (-len(kv[1]), _label_index(pool, kv[0])),
        )
        for label, obs in ranked:
            if len(obs) >= self.min_observations and len({o.round for o in obs}) >= self.min_rounds:
                return label
        return None


def _label_index(pool: Pool, label: str) -> int:
    return pool.direction_labels.index(label) if label in pool.direction_labels else len(pool.direction_labels)


def nomination_queue(
    pools: Sequence[Pool], observations: Sequence[Observation], bar: RecurrenceBar, room: int,
) -> list[tuple[Pool, str]]:
    """Pools to nominate this close, oldest id first, at most `room`; nominated pools are skipped."""
    out: list[tuple[Pool, str]] = []
    for pool in sorted(pools, key=lambda p: p.id):
        if len(out) >= room:
            break
        if pool.status in (PoolStatus.NOMINATED, PoolStatus.MERGED):
            continue
        label = bar.label_at_bar(pool, observations)
        if label is not None:
            out.append((pool, label))
    return out


def expired(observations: Sequence[Observation], round_: int, expiry_rounds: int) -> list[str]:
    """Ids of open observations older than `expiry_rounds` rounds at `round_`."""
    return [
        o.id for o in observations
        if o.status is ObservationStatus.OPEN and round_ - o.round > expiry_rounds
    ]
