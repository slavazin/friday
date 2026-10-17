"""Paired significance tests over per-category means (SPEC § 12)."""

from __future__ import annotations

from collections.abc import Sequence
from itertools import product
from typing import Protocol

from friday.registry import Registry


class OneSidedTest(Protocol):
    def p_value(self, xs: Sequence[float]) -> float:
        """One-sided p for H1: mean(xs) > 0."""
        ...

    def lower_bound(self, xs: Sequence[float], alpha: float) -> float | None:
        """The one-sided (1 - alpha) lower confidence bound on the shift, by inverting this same test: the
        largest d such that the test still rejects H0 on xs - d at alpha. None when no d is rejected at
        this n and alpha (the bound is -inf). Contract: p_value(xs) <= alpha exactly when the bound is > 0.
        """
        ...


PAIRED_TESTS: Registry[OneSidedTest] = Registry("readout_test")

_EPS = 1e-12


@PAIRED_TESTS.register("sign_flip_exact")
class SignFlipExact:
    """Enumerates all 2^n sign flips; p is the share whose mean is at least the observed mean."""

    max_n = 20

    def p_value(self, xs: Sequence[float]) -> float:
        n = len(xs)
        if n == 0:
            raise ValueError("no observations")
        if n > self.max_n:
            raise ValueError(f"exact enumeration over {n} values; register a Monte Carlo test instead")
        observed = sum(xs)
        hits = sum(
            1 for signs in product((1, -1), repeat=n)
            if sum(s * abs(x) for s, x in zip(signs, xs, strict=True)) >= observed - _EPS
        )
        return hits / 2**n

    def lower_bound(self, xs: Sequence[float], alpha: float) -> float | None:
        # Flipping the subset F of xs reaches the observed sum of (x - d) exactly when sum_F (x - d) <= 0, that is
        # when mean_F(x) <= d. So p(d) = (1 + #{nonempty F : mean_F <= d}) / 2^n rises with d, and the test
        # rejects at alpha while at most k = floor(alpha * 2^n) - 1 subset means lie at or below d: the bound
        # is the (k+1)-th smallest subset mean. Sums within the tolerance p_value uses count as 0, so the
        # contract holds on ties.
        n = len(xs)
        if n == 0:
            raise ValueError("no observations")
        if n > self.max_n:
            raise ValueError(f"exact enumeration over {n} values; register a Monte Carlo test instead")
        if not 0.0 < alpha < 1.0:
            raise ValueError("alpha must lie in (0, 1)")
        k = int(alpha * 2**n + _EPS) - 1
        if k < 0:
            return None
        means = []
        for mask in range(1, 2**n):
            members = [x for i, x in enumerate(xs) if mask >> i & 1]
            total = sum(members)
            means.append(0.0 if abs(total) <= _EPS / 2 else total / len(members))
        means.sort()
        return means[k]
