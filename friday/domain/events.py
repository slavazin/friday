"""Domain events. The event log is the canonical record of every status flip and steer; Journal
files, lens files and git commits are projections written from these events by the round close."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from friday.domain.lifecycle import FlipCause


@dataclass(frozen=True)
class StatusFlip:
    subject_id: str              # D-NNNN or L-NN
    old: str
    new: str
    cause: FlipCause
    round: int
    evidence: dict[str, Any] = field(default_factory=dict)   # figures rendered into the commit
    successors: tuple[str, ...] = ()


@dataclass(frozen=True)
class Steer:
    decision_id: str
    action: str                  # retire | veto | note
    reason: str
    issued_round: int


@dataclass(frozen=True)
class Ping:
    reason: str
    round: int
