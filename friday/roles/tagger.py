"""The tagger (SPEC § 4): tags each round's requests at round start, reusing the tag vocabulary and minting
at most `stream.max_new_tags` new tags a round. Tags are frozen on a request once given."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from friday.domain.types import Request, TagGloss
from friday.ports.models import ModelClient
from friday.roles.prompts import PromptBook

ROLE = "tagger"


@dataclass
class Tagger:
    client: ModelClient
    book: PromptBook

    def tag(self, requests: Sequence[Request], vocabulary: Sequence[TagGloss], round_: int,
            max_new: int) -> tuple[Mapping[str, frozenset[str]], list[TagGloss]]:
        """Tags per request id, and the tags minted this round."""
        raise NotImplementedError("slice S3: tagger prompt and minting bar")
