"""A seeded training / held-out split of a minted request set whose held-out tags lie within training tags.

The goal stream mints its held-out set directly (SPEC § 4); this split serves a fixed-set experiment.
"""

from __future__ import annotations

from collections.abc import Sequence

from friday.domain.types import Request
from friday.stats.rng import derive


def draw_split(
    requests: Sequence[Request], held_out: int, seed: int, max_draws: int,
) -> tuple[tuple[str, ...], tuple[str, ...], int] | None:
    """(training ids, held-out ids, draws used) where every held-out request's tags lie within the
    union of training tags; None when no draw within `max_draws` qualifies."""
    ids = sorted(r.id for r in requests)
    by_id = {r.id: r for r in requests}
    if not 0 < held_out < len(ids):
        raise ValueError("held_out must leave both sides non-empty")
    for draw in range(1, max_draws + 1):
        held = set(derive(seed, "split", draw).sample(ids, held_out))
        train = [i for i in ids if i not in held]
        seen = set().union(*(by_id[i].tags for i in train))
        if all(by_id[i].tags <= seen for i in held):
            return tuple(train), tuple(sorted(held)), draw
    return None
