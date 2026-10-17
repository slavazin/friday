"""The lens minter (SPEC § 6 step 4): proposes at most `mint_max` lenses from the residual view."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from friday.domain.types import Lens, Observation
from friday.ports.models import ModelClient
from friday.roles.prompts import PromptBook

ROLE = "lens_minter"


@dataclass(frozen=True)
class ResidualItem:
    request_text: str
    source_excerpt: str
    observations: tuple[Observation, ...]


@dataclass(frozen=True)
class LensProposal:
    angle: str
    counterfactual: str


@dataclass
class LensMinter:
    client: ModelClient
    book: PromptBook

    def propose(self, residual: Sequence[ResidualItem], lenses: Sequence[Lens], limit: int) -> list[LensProposal]:
        raise NotImplementedError("block 12:30: lens minter prompt")
