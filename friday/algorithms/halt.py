"""When the run halts (SPEC § 11)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class HaltReason(StrEnum):
    ROUNDS = "rounds"
    SPEND = "spend"
    CONVERGED = "converged"


@dataclass(frozen=True)
class RoundSummary:
    round: int
    candidates_in_flight: int
    suspects: int
    pools_at_bar: int


def halt_reason(
    completed: list[RoundSummary], rounds: int, spend_usd: float, spend_cap_usd: float,
    drift_round: int, convergence_rounds: int,
) -> HaltReason | None:
    """The first halt condition met after the last completed round, or None to keep going."""
    if completed and completed[-1].round >= rounds:
        return HaltReason.ROUNDS
    if spend_usd >= spend_cap_usd:
        return HaltReason.SPEND
    quiet = [
        s for s in completed[-convergence_rounds:]
        if s.round > drift_round and not (s.candidates_in_flight or s.suspects or s.pools_at_bar)
    ]
    if len(quiet) == convergence_rounds:
        return HaltReason.CONVERGED
    return None
