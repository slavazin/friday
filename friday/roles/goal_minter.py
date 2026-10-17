"""The goal minter (SPEC § 4): mints the goal stream and the held-out set before round 1, with contracts,
acceptance checks and contract stubs, each check validated to fail on its stub."""

from __future__ import annotations

from dataclasses import dataclass

from friday.ports.models import ModelClient
from friday.roles.prompts import PromptBook

ROLE = "goal_minter"


@dataclass
class GoalMinter:
    client: ModelClient
    book: PromptBook

    def mint(self) -> None:
        raise NotImplementedError("slice S2: goal minting and check validation")
