"""Confidence bounds on a mean. `UpperBound` is the seam; `student_t` is the default (SPEC §§ 8, 9)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from functools import lru_cache
from typing import Protocol

from friday.registry import Registry


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (modified Lentz)."""
    tiny, eps = 1e-300, 3e-16
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def regularized_beta(a: float, b: float, x: float) -> float:
    """I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_front = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(ln_front) * _betacf(a, b, x) / a
    return 1.0 - math.exp(ln_front) * _betacf(b, a, 1.0 - x) / b


def t_cdf(t: float, df: float) -> float:
    """P(T <= t) for Student's t with `df` degrees of freedom."""
    x = df / (df + t * t)
    tail = 0.5 * regularized_beta(df / 2.0, 0.5, x)
    return 1.0 - tail if t >= 0 else tail


@lru_cache(maxsize=1024)
def t_quantile(p: float, df: float) -> float:
    """The `p` quantile of Student's t with `df` degrees of freedom (bisection on the cdf)."""
    if not 0.0 < p < 1.0:
        raise ValueError(f"p out of (0, 1): {p}")
    lo, hi = -1e3, 1e3
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def sample_sd(xs: Sequence[float]) -> float:
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


class UpperBound(Protocol):
    def upper(self, xs: Sequence[float], level: float) -> float | None:
        """One-sided `level` upper bound on the mean of `xs`; None when `xs` cannot support one."""
        ...


UPPER_BOUNDS: Registry[UpperBound] = Registry("upper_bound")


@UPPER_BOUNDS.register("student_t")
class StudentTUpperBound:
    def upper(self, xs: Sequence[float], level: float) -> float | None:
        n = len(xs)
        if n < 2:
            return None
        return mean(xs) + t_quantile(level, n - 1) * sample_sd(xs) / math.sqrt(n)


def t_interval(xs: Sequence[float], level: float) -> tuple[float, float] | None:
    """Two-sided `level` t-interval on the mean of `xs`; None when n < 2."""
    n = len(xs)
    if n < 2:
        return None
    half = t_quantile(0.5 + level / 2.0, n - 1) * sample_sd(xs) / math.sqrt(n)
    m = mean(xs)
    return (m - half, m + half)
