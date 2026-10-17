"""Drift detection over an admitted decision's ablation deltas (SPEC § 9)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from friday.domain.types import Disposition
from friday.stats.bounds import UpperBound
from friday.stats.eprocess import BettingStrategy, mirror_wealth


class SuspectReason(StrEnum):
    SHORT_WINDOW = "short_window"
    LONG_RUN = "long_run"
    CONTRADICTED = "contradicted"


class ConfirmAction(StrEnum):
    RETIRE = "retire"
    CLEAR = "clear"
    WAIT = "wait"


@dataclass(frozen=True)
class SuspectReading:
    reasons: tuple[SuspectReason, ...]
    window_upper: float | None
    harm_wealth: float
    contradicted_share: float | None

    @property
    def suspect(self) -> bool:
        return bool(self.reasons)


@dataclass(frozen=True)
class ConfirmReading:
    action: ConfirmAction
    n: int
    mean_d: float | None
    upper: float | None


@dataclass(frozen=True)
class Detector:
    window: int
    level: float
    alpha: float
    confirm_pairs: int
    contradicted_share: float
    betting: BettingStrategy
    bound: UpperBound

    def read(self, ablation_d: Sequence[float], dispositions: Sequence[Disposition]) -> SuspectReading:
        """Readings over deltas since admission and the dispositions of the decision's composed passes."""
        reasons: list[SuspectReason] = []
        recent = list(ablation_d[-self.window:])
        upper = self.bound.upper(recent, self.level) if len(recent) == self.window else None
        if upper is not None and upper < 0:
            reasons.append(SuspectReason.SHORT_WINDOW)
        harm = mirror_wealth(ablation_d, self.betting)
        if harm >= 1.0 / self.alpha:
            reasons.append(SuspectReason.LONG_RUN)
        disposed = [x for x in dispositions if x is not Disposition.UNDISPOSED][-self.window:]
        share = None
        if len(disposed) == self.window:
            share = sum(x is Disposition.CONTRADICTED for x in disposed) / len(disposed)
            if share > self.contradicted_share:
                reasons.append(SuspectReason.CONTRADICTED)
        return SuspectReading(tuple(reasons), upper, harm, share)

    def confirm(self, since_suspect_d: Sequence[float]) -> ConfirmReading:
        """Retire when the first `confirm_pairs` ablation deltas since suspicion keep their upper bound below 0."""
        if len(since_suspect_d) < self.confirm_pairs:
            return ConfirmReading(ConfirmAction.WAIT, len(since_suspect_d), None, None)
        xs = list(since_suspect_d[: self.confirm_pairs])
        upper = self.bound.upper(xs, self.level)
        action = ConfirmAction.RETIRE if upper is not None and upper < 0 else ConfirmAction.CLEAR
        return ConfirmReading(action, len(xs), sum(xs) / len(xs), upper)
