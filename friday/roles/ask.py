"""One path for every model call that must return JSON: decode through the repairing parser,
validate, re-ask once with the problem stated, then report failure (never a silent default)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from friday.ports.models import ChatResult, Message, ModelClient
from friday.ports.telemetry import Telemetry
from friday.support.json_repair import Decoded, decode

T = TypeVar("T")


class Rejected(ValueError):
    """Raised by a validator with a reason the re-ask states to the model."""


@dataclass(frozen=True)
class Asked[T]:
    value: T | None
    attempts: int
    results: tuple[ChatResult, ...]
    failures: tuple[str, ...]
    # How the last reply decoded: `strict`, the json_repair repairs applied joined by `+`, or `failed`
    repair_rung: str = ""

    @property
    def ok(self) -> bool:
        return self.value is not None


def _rung(decoded: Decoded) -> str:
    if not decoded.ok:
        return "failed"
    return "+".join(decoded.repairs) or "strict"


def record_json(telemetry: Telemetry, role: str, tag: str, asked: Asked[Any]) -> None:
    """Writes the `model.json` event of one `ask_json` call: its repair rung, its re-asks and whether it held."""
    telemetry.event("model.json", role=role, tag=tag, repair_rung=asked.repair_rung,
                    reasks=max(0, asked.attempts - 1), ok=asked.ok)


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
    rung = ""
    for attempt in range(reasks + 1):
        res = client.chat(role, convo, json_schema=schema, tag=tag if attempt == 0 else f"{tag}:reask{attempt}")
        results.append(res)
        decoded = decode(res.text)
        try:
            if not decoded.ok:
                raise Rejected(f"invalid JSON ({decoded.error})")
            return Asked(validate(decoded.value), attempt + 1, tuple(results), tuple(failures), _rung(decoded))
        except Rejected as why:
            failures.append(str(why))
            rung = _rung(decoded)
            convo = [*convo, Message("assistant", res.text), Message("user", reask_text(str(why)))]
    return Asked(None, reasks + 1, tuple(results), tuple(failures), rung)
