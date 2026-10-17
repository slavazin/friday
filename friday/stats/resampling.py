"""Percentiles and permutation nulls (SPEC § 6 floor)."""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence
from typing import TypeVar

T = TypeVar("T")


def percentile(xs: Sequence[float], q: float) -> float:
    """The `q` quantile (q in [0, 1]) of `xs` by linear interpolation between order statistics."""
    if not xs:
        raise ValueError("percentile of nothing")
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"q out of [0, 1]: {q}")
    s = sorted(xs)
    pos = q * (len(s) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def permutation_quantile[T](
    statistic: Callable[[Sequence[T]], float | None],
    labels: Sequence[T],
    shuffles: int,
    q: float,
    rng: random.Random,
) -> float | None:
    """The `q` quantile of `statistic` over `shuffles` random permutations of `labels`.

    Shuffles where the statistic is undefined are skipped; None when none is defined.
    """
    values: list[float] = []
    work = list(labels)
    for _ in range(shuffles):
        rng.shuffle(work)
        v = statistic(work)
        if v is not None:
            values.append(v)
    return percentile(values, q) if values else None
