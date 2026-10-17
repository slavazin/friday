"""The pooling call (SPEC § 7): assigns a lens's new observations to stable pools. No embeddings."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from friday.domain.types import Observation, Pool
from friday.ports.models import ModelClient
from friday.roles.prompts import PromptBook

ROLE = "pooling"


@dataclass(frozen=True)
class Assignment:
    observation_id: str
    pool_ref: str          # an existing pool id, or "new:<k>" naming an entry of `new_pools`
    direction_label: str


@dataclass(frozen=True)
class NewPool:
    key: str
    shape: str
    direction_labels: tuple[str, ...]


@dataclass(frozen=True)
class PoolingResult:
    assignments: tuple[Assignment, ...]
    new_pools: tuple[NewPool, ...]
    merges: tuple[tuple[str, str], ...]   # (surviving lower id, merged id)


@dataclass
class PoolingCall:
    client: ModelClient
    book: PromptBook

    def assign(self, lens_id: str, pools: Sequence[Pool], new: Sequence[Observation]) -> PoolingResult:
        raise NotImplementedError("block 12:30: pooling prompt")
