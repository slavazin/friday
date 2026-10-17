"""The gate (SPEC § 8): admit, refuse, expire or hold a candidate from its Ledger account."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from friday.domain.types import Decision
from friday.stats.bounds import UpperBound
from friday.stats.eprocess import BettingStrategy, mirror_wealth, wealth


class GateAction(StrEnum):
    ADMIT = "admit"
    REFUSE = "refuse"
    EXPIRE = "expire"
    HOLD = "hold"


@dataclass(frozen=True)
class GateReading:
    action: GateAction
    n: int
    wealth: float
    mirror_wealth: float
    mean_d: float | None
    q_upper: float | None


@dataclass(frozen=True)
class GateRule:
    """Precedence when several fire on one reading: admit, then refuse, then expire."""

    alpha: float
    n_min: int
    n_max: int
    quality_margin: float
    quality_level: float
    betting: BettingStrategy
    bound: UpperBound

    @property
    def threshold(self) -> float:
        return 1.0 / self.alpha

    def read(self, d: Sequence[float], dq: Sequence[float]) -> GateReading:
        """The gate's verdict on paired deltas `d` (without - with) and quality changes `dq` (with - without)."""
        if len(d) != len(dq):
            raise ValueError("d and dq differ in length")
        n = len(d)
        w = wealth(d, self.betting)
        wm = mirror_wealth(d, self.betting)
        q_up = self.bound.upper(dq, self.quality_level)
        mean_d = sum(d) / n if n else None
        quality_ok = q_up is not None and q_up <= self.quality_margin
        if w >= self.threshold and n >= self.n_min and quality_ok:
            action = GateAction.ADMIT
        elif wm >= self.threshold:
            action = GateAction.REFUSE
        elif n >= self.n_max:
            action = GateAction.EXPIRE
        else:
            action = GateAction.HOLD
        return GateReading(action, n, w, wm, mean_d, q_up)


def select_in_flight(candidates: Sequence[Decision], in_flight: int) -> list[Decision]:
    """The first `in_flight` candidates in drafting order (drafted round, then id)."""
    return sorted(candidates, key=lambda c: (c.drafted_round, c.id))[:in_flight]
