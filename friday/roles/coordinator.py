"""The coordinator agent (SPEC § 11): plans rounds, spawns passes, re-runs, pings, halts.

Its tool surface is this Protocol and nothing else. No tool sets an exposure, a status or a constant:
the planner cannot move the bar.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from friday.domain.types import Arm

ROLE = "coordinator"


class CoordinatorTools(Protocol):
    def plan_round(self) -> str:
        """Writes plans/round-NN.md from in-flight candidates, due ablations and last round's failures."""
        ...

    def spawn_pass(self, category: str, arm: Arm, worker: int) -> str: ...
    def rerun(self, pass_id: str) -> str:
        """Re-runs a pass that failed for transport, once."""
        ...

    def read_ledger(self, decision_id: str) -> Mapping[str, Any]: ...
    def read_journal(self) -> Mapping[str, Any]: ...
    def ping(self, reason: str) -> None: ...
    def halt(self, reason: str) -> None: ...


TOOL_NAMES = ("plan_round", "spawn_pass", "rerun", "read_ledger", "read_journal", "ping", "halt")
