"""Lens curation at a round close (SPEC § 6): value, floor, fold, residual view, cap.

Pure: takes the window and the lens set, returns the flips to apply and the residual view. The caller
(the round close) applies flips and runs the minter.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import combinations

from friday.domain.lifecycle import LIVE_LENS, FlipCause
from friday.domain.types import Lens, LensStatus
from friday.stats.ranks import BinaryScore, Correlation
from friday.stats.resampling import percentile, permutation_quantile
from friday.stats.rng import derive


@dataclass(frozen=True)
class WindowPass:
    pass_id: str
    loss: float
    p: Mapping[str, float]          # lens id -> p from the lens call; absent when unanswered


@dataclass(frozen=True)
class LensFlip:
    lens_id: str
    new: LensStatus
    cause: FlipCause
    superseded_by: str | None = None
    figures: Mapping[str, float | int | None] = field(default_factory=dict)


@dataclass(frozen=True)
class CurationParams:
    n_min: int
    flag_p: float
    floor_quantile: float
    floor_shuffles: int
    floor_strikes: int
    fold_rho: float
    residual_loss: float
    cap: int


@dataclass(frozen=True)
class CurationResult:
    flips: tuple[LensFlip, ...]
    values: Mapping[str, float | None]
    floors: Mapping[str, float | None]
    strikes: Mapping[str, int]
    residual: tuple[str, ...]       # pass ids of the residual view


def high_loss_labels(losses: Sequence[float]) -> list[bool]:
    """loss > median(losses); a loss equal to the median is negative."""
    med = percentile(losses, 0.5)
    return [x > med for x in losses]


def _answered(window: Sequence[WindowPass], lens_id: str) -> tuple[list[float], list[float]]:
    rows = [(w.p[lens_id], w.loss) for w in window if lens_id in w.p]
    return [r[0] for r in rows], [r[1] for r in rows]


def curate(
    lenses: Sequence[Lens],
    window: Sequence[WindowPass],
    strikes: Mapping[str, int],
    params: CurationParams,
    scorer: BinaryScore,
    correlation: Correlation,
    seed: int,
    round_: int,
) -> CurationResult:
    """Values, floors and folds over `window`; returns flips and the residual view against surviving lenses."""
    live = sorted((x for x in lenses if x.status in LIVE_LENS), key=lambda x: x.id)
    labels_all = high_loss_labels([w.loss for w in window]) if window else []
    label_of = {w.pass_id: y for w, y in zip(window, labels_all, strict=True)}
    values: dict[str, float | None] = {}
    floors: dict[str, float | None] = {}
    new_strikes = dict(strikes)
    flips: list[LensFlip] = []
    gone: set[str] = set()

    for lens in live:
        ps, _ = _answered(window, lens.id)
        if len(ps) < params.n_min:
            values[lens.id] = floors[lens.id] = None
            continue
        labels = [label_of[w.pass_id] for w in window if lens.id in w.p]
        value = scorer.score(ps, labels)
        floor = permutation_quantile(
            lambda ys, ps=ps: scorer.score(ps, ys),
            labels, params.floor_shuffles, params.floor_quantile, derive(seed, "lens-floor", round_, lens.id),
        )
        values[lens.id], floors[lens.id] = value, floor
        figures = {"value": value, "floor": floor, "n": len(ps)}
        if value is None or floor is None:
            continue
        if value > floor:
            new_strikes[lens.id] = 0
            if lens.status is LensStatus.HIDDEN:
                flips.append(LensFlip(lens.id, LensStatus.ACTIVE, FlipCause.LENS_CLEAR_FLOOR, figures=figures))
        else:
            new_strikes[lens.id] = new_strikes.get(lens.id, 0) + 1
            if new_strikes[lens.id] >= params.floor_strikes:
                flips.append(LensFlip(lens.id, LensStatus.RETIRED, FlipCause.LENS_FLOOR, figures=figures))
                gone.add(lens.id)

    rhos: list[tuple[float, str, str]] = []
    for a, b in combinations([x for x in live if x.id not in gone], 2):
        both = [w for w in window if a.id in w.p and b.id in w.p]
        rho = correlation.corr([w.p[a.id] for w in both], [w.p[b.id] for w in both]) if len(both) >= 2 else None
        if rho is not None and rho > params.fold_rho:
            rhos.append((rho, a.id, b.id))
    for rho, a_id, b_id in sorted(rhos, key=lambda t: (-t[0], t[1], t[2])):
        if a_id in gone or b_id in gone:
            continue
        keep, drop = _fold_order(a_id, b_id, values)
        flips.append(LensFlip(drop, LensStatus.SUPERSEDED, FlipCause.LENS_FOLD, keep, {"rho": rho}))
        gone.add(drop)

    kept = [x.id for x in live if x.id not in gone]
    residual = residual_view(window, kept, params.residual_loss, params.flag_p)
    return CurationResult(tuple(flips), values, floors, new_strikes, residual)


def _fold_order(a: str, b: str, values: Mapping[str, float | None]) -> tuple[str, str]:
    """(kept, superseded): the higher value is kept; an unvalued lens loses; ties keep the lower id."""
    va, vb = values.get(a), values.get(b)
    if va is None and vb is None:
        return (a, b) if a < b else (b, a)
    if vb is None or (va is not None and (va > vb or (va == vb and a < b))):
        return a, b
    return b, a


def residual_view(
    window: Sequence[WindowPass], kept: Sequence[str], residual_loss: float, flag_p: float
) -> tuple[str, ...]:
    """Pass ids with loss >= residual_loss where no kept lens has p >= flag_p."""
    return tuple(
        w.pass_id for w in window
        if w.loss >= residual_loss and not any(w.p.get(k, 0.0) >= flag_p for k in kept)
    )


def cap_evictions(
    live: Sequence[Lens], values: Mapping[str, float | None], answered: Mapping[str, int],
    proposals: int, cap: int, n_min: int,
) -> tuple[list[str], int]:
    """Lenses to retire to make room for `proposals`, and how many proposals may be added.

    Over the cap, the lowest-value lens past `n_min` answered passes is retired per proposal;
    with none past it, the proposal is not added.
    """
    room = cap - len(live)
    evict: list[str] = []
    admitted = 0
    pool = sorted(
        (x.id for x in live if answered.get(x.id, 0) >= n_min and values.get(x.id) is not None),
        key=lambda i: (values[i], i),
    )
    for _ in range(proposals):
        if room > 0:
            room -= 1
            admitted += 1
        elif pool:
            evict.append(pool.pop(0))
            admitted += 1
        else:
            break
    return evict, admitted
