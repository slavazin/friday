"""Betting e-processes over bounded paired deltas (SPEC § 8).

W_0 = 1, W_t = W_{t-1} * (1 + lambda_t * d_t), with d_t in [-1, 1] and lambda_t chosen from d_1..d_{t-1}
only. With lambda_t in [0, lambda_max] and lambda_max < 1, every factor is >= 1 - lambda_max > 0, so W is
a nonnegative supermartingale under E[d] <= 0 and W >= 1/alpha rejects at level alpha at any stopping time.
The wealth is always recomputed from the deltas, never stored.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from friday.registry import Registry


class BettingStrategy(Protocol):
    def bet(self, past: Sequence[float]) -> float:
        """The bet lambda_t in [0, lambda_max] for the next delta, from past deltas only."""
        ...


BETTING: Registry[BettingStrategy] = Registry("betting")


@BETTING.register("agrapa")
class AGrapa:
    """lambda_1 = lambda_first; lambda_t = clip(m / (v + m^2 + eps), 0, lambda_max), m and v the mean and
    population variance of the past deltas."""

    def __init__(self, lambda_first: float = 0.25, lambda_max: float = 0.9, eps: float = 1e-9) -> None:
        if not 0.0 <= lambda_max < 1.0:
            raise ValueError("lambda_max must lie in [0, 1) to keep wealth positive")
        if not 0.0 <= lambda_first <= lambda_max:
            raise ValueError("lambda_first must lie in [0, lambda_max]")
        self.lambda_first, self.lambda_max, self.eps = lambda_first, lambda_max, eps

    def bet(self, past: Sequence[float]) -> float:
        if not past:
            return min(max(self.lambda_first, 0.0), self.lambda_max)
        n = len(past)
        m = sum(past) / n
        v = sum((x - m) ** 2 for x in past) / n
        return min(max(m / (v + m * m + self.eps), 0.0), self.lambda_max)


@BETTING.register("fixed")
class FixedBet:
    def __init__(self, lam: float = 0.25) -> None:
        if not 0.0 <= lam < 1.0:
            raise ValueError("lam must lie in [0, 1)")
        self.lam = lam

    def bet(self, past: Sequence[float]) -> float:
        return self.lam


def wealth_path(deltas: Sequence[float], strategy: BettingStrategy) -> list[float]:
    """W_1..W_n for `deltas` (W_0 = 1 is implied). Raises on a delta outside [-1, 1]."""
    path: list[float] = []
    w = 1.0
    for t, d in enumerate(deltas):
        if not -1.0 <= d <= 1.0:
            raise ValueError(f"delta {d} outside [-1, 1]")
        w *= 1.0 + strategy.bet(deltas[:t]) * d
        path.append(w)
    return path


def wealth(deltas: Sequence[float], strategy: BettingStrategy) -> float:
    """W_n for `deltas`; 1.0 for no deltas."""
    path = wealth_path(deltas, strategy)
    return path[-1] if path else 1.0


def mirror_wealth(deltas: Sequence[float], strategy: BettingStrategy) -> float:
    """Wealth of the harm e-process: the same strategy betting on -d."""
    return wealth([-d for d in deltas], strategy)
