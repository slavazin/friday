"""Exposure assignment per pair (SPEC §§ 8, 9).

Per training category, in category-id order: an in-flight candidate whose hook fires (fewest pairs so
far, ties by id) gets a coin; else a composed admitted or suspect decision is ablated with probability
`ablate_p` (fewest ablation pairs so far, ties by id); else the pair runs unexposed. Counts update as
pairs are assigned within the round, so two categories in one candidate's reach split fairly.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from friday.domain.hooks import fires
from friday.domain.types import Decision, PairKind, PairPlan, Request
from friday.stats.rng import bernoulli, coin

__all__ = ["PairKind", "PairPlan", "assign_exposures"]


def assign_exposures(
    round_: int,
    requests: Sequence[Request],
    in_flight: Sequence[Decision],
    composed: Mapping[str, tuple[str, ...]],
    pair_counts: Mapping[str, int],
    ablation_counts: Mapping[str, int],
    seed: int,
    ablate_p: float,
) -> list[PairPlan]:
    """One PairPlan per request. `composed[category]` holds the admitted/suspect ids composed there."""
    pairs = dict(pair_counts)
    ablations = dict(ablation_counts)
    plans: list[PairPlan] = []
    for req in sorted(requests, key=lambda r: r.id):
        firing = [c for c in in_flight if fires(c.hook, req.tags)]
        if firing:
            pick = min(firing, key=lambda c: (pairs.get(c.id, 0), c.id))
            pairs[pick.id] = pairs.get(pick.id, 0) + 1
            plans.append(PairPlan(req.id, PairKind.EXPOSURE, pick.id, coin(seed, "exposure", round_, req.id)))
            continue
        base = composed.get(req.id, ())
        if base:
            pick_id = min(base, key=lambda i: (ablations.get(i, 0), i))
            if bernoulli(ablate_p, seed, "ablate", round_, req.id):
                ablations[pick_id] = ablations.get(pick_id, 0) + 1
                slot = coin(seed, "ablation-slot", round_, req.id)
                plans.append(PairPlan(req.id, PairKind.ABLATION, pick_id, slot))
                continue
        plans.append(PairPlan(req.id, PairKind.NONE))
    return plans
