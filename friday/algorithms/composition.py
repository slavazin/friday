"""Which decisions are composed into one worker's prompt (SPEC § 2 composition, cap)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from friday.domain.hooks import fires
from friday.domain.lifecycle import COMPOSABLE_DECISION
from friday.domain.types import Composition, Decision, Request


def select_composed(
    request: Request,
    decisions: Sequence[Decision],
    rank: Mapping[str, float],
    cap: int,
) -> tuple[str, ...]:
    """Ids of the composable decisions whose hooks fire on `request`, at most `cap`, sorted by id.

    Over the cap the lowest-ranked are left out (ties: the higher id is left out first).
    """
    firing = [d for d in decisions if d.status in COMPOSABLE_DECISION and fires(d.hook, request.tags)]
    firing.sort(key=lambda d: (-rank.get(d.id, 0.0), d.id))
    return tuple(sorted(d.id for d in firing[:cap]))


def compose_pair(
    request: Request,
    base: tuple[str, ...],
    exposed: str | None = None,
    ablated: str | None = None,
    treated_slot: int = 0,
) -> tuple[Composition, Composition]:
    """The two compositions of one pair, differing only by the one exposure.

    With `exposed`, slot `treated_slot` gets the candidate on top of `base`. With `ablated`, slot
    `treated_slot` keeps the decision and the other slot omits it. No slot is backfilled.
    """
    if exposed and ablated:
        raise ValueError("one pair carries at most one exposure")
    if ablated and ablated not in base:
        raise ValueError(f"{ablated} is not composed on {request.id}; it cannot be ablated")
    if treated_slot not in (0, 1):
        raise ValueError("treated_slot must be 0 or 1")
    plain = Composition(request.id, base)
    if exposed:
        treated = Composition(request.id, tuple(sorted((*base, exposed))), exposed=exposed)
        control = plain
    elif ablated:
        treated = plain
        control = Composition(request.id, tuple(i for i in base if i != ablated), ablated=ablated)
    else:
        return plain, plain
    return (treated, control) if treated_slot == 0 else (control, treated)
