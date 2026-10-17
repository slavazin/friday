"""Seeded held-out draws: withheld grid cells for the goal stream (SPEC § 4), and a request split for a
fixed-set experiment whose held-out tags lie within training tags."""

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


Cell = tuple[str, str]


def draw_held_out_cells(
    families: Sequence[str], modes: Sequence[str], coherent: Sequence[Cell], k: int, seed: int, max_draws: int,
) -> tuple[tuple[Cell, ...], tuple[Cell, ...], int] | None:
    """(training cells, withheld cells, draws used): `k` coherent cells withheld so that every family and every
    mode still appears in some training cell; None when no draw within `max_draws` qualifies (SPEC § 4)."""
    cells = sorted(set(coherent))
    if not 0 < k < len(cells):
        raise ValueError("held_out_cells must leave training cells")
    for draw in range(1, max_draws + 1):
        held = set(derive(seed, "held-out-cells", draw).sample(cells, k))
        train = [c for c in cells if c not in held]
        if {f for f, _ in train} >= set(families) and {m for _, m in train} >= set(modes):
            return tuple(train), tuple(sorted(held)), draw
    return None
