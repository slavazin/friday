"""One path for every model call that must return JSON: decode through the repairing parser,
validate, re-ask once with the problem stated, then report failure (never a silent default)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from friday.ports.models import ChatResult, Message, ModelClient
from friday.support.json_repair import decode

T = TypeVar("T")


class Rejected(ValueError):
    """Raised by a validator with a reason the re-ask states to the model."""


@dataclass(frozen=True)
class Asked[T]:
    value: T | None
    attempts: int
    results: tuple[ChatResult, ...]
    failures: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.value is not None


def ask_json[T](
    client: ModelClient,
    role: str,
    messages: Sequence[Message],
    validate: Callable[[Any], T],
    *,
    reasks: int,
    tag: str,
    schema: dict[str, Any] | None = None,
    reask_text: Callable[[str], str] = lambda why: f"Your reply was not usable: {why}. Reply again with JSON only.",
) -> Asked[T]:
    """The validated value, or value None after `reasks` re-asks, with every failure reason recorded."""
    convo = list(messages)
    results: list[ChatResult] = []
    failures: list[str] = []
    for attempt in range(reasks + 1):
        res = client.chat(role, convo, json_schema=schema, tag=tag if attempt == 0 else f"{tag}:reask{attempt}")
        results.append(res)
        decoded = decode(res.text)
        try:
            if not decoded.ok:
                raise Rejected(f"invalid JSON ({decoded.error})")
            return Asked(validate(decoded.value), attempt + 1, tuple(results), tuple(failures))
        except Rejected as why:
            failures.append(str(why))
            convo = [*convo, Message("assistant", res.text), Message("user", reask_text(str(why)))]
    return Asked(None, reasks + 1, tuple(results), tuple(failures))
