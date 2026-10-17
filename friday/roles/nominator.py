"""The nominator (SPEC § 7): drafts one candidate from one pool, grounded by one search call."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from friday.domain.types import Hook, Observation, Pool
from friday.ports.models import Citation, ModelClient, SearchClient
from friday.roles.prompts import PromptBook

ROLE = "nominator"


@dataclass(frozen=True)
class Draft:
    """A drafted candidate's text; the runner mints its id and writes the Journal file."""

    hook: Hook
    payload: str
    sources: tuple[Citation, ...]


@dataclass(frozen=True)
class NoDecision:
    reason: str


@dataclass
class Nominator:
    client: ModelClient
    search: SearchClient
    book: PromptBook
    tag_vocabulary: frozenset[str]

    def draft(self, pool: Pool, label: str, observations: Sequence[Observation]) -> Draft | NoDecision:
        """One search on the pool shape, then a Draft (payload checked by `check_payload`) or NoDecision."""
        raise NotImplementedError("block 12:30: nominator prompt and drafting")
