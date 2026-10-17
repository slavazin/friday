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
