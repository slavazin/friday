"""Turns a round's paired passes into Ledger rows (SPEC § 8 paired delta)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from friday.algorithms.exposure import PairKind, PairPlan
from friday.domain.types import DeltaKind, EvalReport, LedgerDelta, Pass


def pair_deltas(
    round_: int, plans: Sequence[PairPlan], passes: Sequence[Pass], evals: Mapping[str, EvalReport],
) -> list[LedgerDelta]:
    """One delta per exposed or ablated pair whose two passes both evaluated.

    d = loss(without) - loss(with); dQ = Q(with) - Q(without). A pair with a transport failure gives none.
    """
    slots: dict[tuple[str, int], Pass] = {}
    for p in passes:
        if p.round != round_ or p.pair_slot is None:
            continue
        key = (p.category, p.pair_slot)
        if key not in slots or slots[key].transport_failed:   # a successful re-run replaces a failed original
            slots[key] = p
    out: list[LedgerDelta] = []
    for plan in plans:
        if plan.kind is PairKind.NONE or plan.decision_id is None:
            continue
        with_p = slots.get((plan.category, plan.treated_slot))
        without_p = slots.get((plan.category, 1 - plan.treated_slot))
        if with_p is None or without_p is None or with_p.transport_failed or without_p.transport_failed:
            continue
        e_with, e_without = evals.get(with_p.id), evals.get(without_p.id)
        if e_with is None or e_without is None:
            continue
        kind = DeltaKind.EXPOSURE if plan.kind is PairKind.EXPOSURE else DeltaKind.ABLATION
        out.append(LedgerDelta(plan.decision_id, round_, plan.category, kind,
                               e_without.loss - e_with.loss, float(e_with.Q - e_without.Q)))
    return out
