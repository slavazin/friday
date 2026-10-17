"""Model and search seams. Every model call in the run goes through `ModelClient` (metered)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ModelInfo:
    id: str
    image_input: bool
    structured_output: bool
    price_in_per_mtok: float | None
    price_out_per_mtok: float | None


@dataclass(frozen=True)
class Message:
    role: str                       # system | user | assistant
    content: str
    images: tuple[bytes, ...] = ()  # PNG bytes, for image-input roles


@dataclass(frozen=True)
class ChatResult:
    text: str
    model: str                      # the model string the service echoed
    tokens_in: int
    tokens_out: int
    cost_usd: float | None
    cached: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


class ModelCatalog(Protocol):
    def list_models(self) -> Sequence[ModelInfo]:
        """The models the endpoint serves now."""
        ...


class ModelClient(Protocol):
    def chat(
        self, role: str, messages: Sequence[Message], *, json_schema: dict[str, Any] | None = None,
        max_tokens: int | None = None, tag: str = "",
    ) -> ChatResult:
        """One metered call for `role`; the role's pin, temperature and seed come from the run spec."""
        ...


@dataclass(frozen=True)
class Citation:
    url: str
    title: str
    snippet: str


class ResponseCache(Protocol):
    def get(self, key: str) -> str | None:
        """The cached reply text for a request-body digest, or None."""
        ...

    def put(self, key: str, value: str) -> None: ...


class SearchClient(Protocol):
    def search(self, query: str) -> Sequence[Citation]:
        """One search call; results cited in a decision's warrant."""
        ...
