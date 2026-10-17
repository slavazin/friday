"""The goal stream and the tag vocabulary (SPEC § 4)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from friday.domain.types import Request, TagGloss


class GoalSource(Protocol):
    def round(self, n: int) -> Sequence[Request]:
        """The training requests of round `n` (1-based), tags as stored so far."""
        ...

    def held_out(self) -> Sequence[Request]:
        """The held-out set (V), read at the close only."""
        ...


class TagVocabulary(Protocol):
    def glosses(self) -> Sequence[TagGloss]:
        """Every tag minted so far, in minting order."""
        ...

    def add(self, gloss: TagGloss) -> None:
        """Mints one tag; raises if the tag exists (tags are never renamed or merged)."""
        ...

    def tags_of(self, request_id: str) -> frozenset[str] | None:
        """The tags given to a request, or None before it is tagged."""
        ...

    def set_tags(self, tags: Mapping[str, frozenset[str]]) -> None:
        """Freezes tags on requests; raises if a request already carries tags."""
        ...
