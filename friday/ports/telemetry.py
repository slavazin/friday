"""Telemetry seam: every timed step, reading and run event of a run, recorded where it happens.

Telemetry is the operational record (how long, how many, what failed). It never decides anything: no
gate, detector, lens or readout reads it. The evidence stores (Ledger, event log, pass and eval stores)
stay the only inputs to a decision.

Three row kinds share one append-only log:
- `span`: a timed step, nested by `parent_id`. A span appends two rows with one `span_id`: one when it
  opens (`ended_at` None, so a live view sees work in flight) and one when it closes (`ended_at`, `ok`,
  `error` and every attribute set while open). Rows are never updated, so a reader tails by `seq`.
- `metric`: one named number with a unit (`value` None reads unread, never 0).
- `event`: a point-in-time record with attributes (the run manifest, a fallback taken, a ping).

Every row carries its `Scope` (run, round, pass), so a reading can be recalled by any of the three.
Attribute values must be JSON-serialisable. No row holds a prompt, a model reply, a key or an
environment value; adapters refuse attribute names that look like secrets.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any, Protocol

ROW_KINDS = ("span", "metric", "event")


@dataclass(frozen=True)
class Scope:
    """Where a row belongs: `run` is `main` or `critic` (SPEC § 17), plus the round and pass when known."""

    run: str
    round: int | None = None
    pass_id: str | None = None

    def at(self, *, round: int | None = None, pass_id: str | None = None) -> Scope:  # noqa: A002 - SPEC term
        """This scope narrowed to `round` and/or `pass_id`; fields not given are kept."""
        return Scope(self.run, self.round if round is None else round, self.pass_id if pass_id is None else pass_id)


@dataclass(frozen=True)
class TelemetryRow:
    seq: int
    kind: str                       # span | metric | event
    name: str                       # dotted, e.g. "pass.worker", "eval.probe", "model.call", "close.gate"
    scope: Scope
    at: float                       # unix seconds: span start (both rows), metric or event time
    span_id: str | None = None
    parent_id: str | None = None
    ended_at: float | None = None   # spans only; None while in flight
    ok: bool | None = None          # spans only; None while in flight
    error: str = ""
    value: float | None = None      # metrics only
    unit: str = ""                  # metrics only: s, ms, usd, tokens, count, share, bytes, score
    attrs: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float | None:
        return None if self.ended_at is None else self.ended_at - self.at


class SpanHandle(Protocol):
    id: str

    def set(self, **attrs: Any) -> None:
        """Adds attributes, written when the span closes."""
        ...


class Telemetry(Protocol):
    scope: Scope

    def scoped(self, *, round: int | None = None, pass_id: str | None = None) -> Telemetry:  # noqa: A002
        """The same log, writing rows under this scope narrowed to `round` and/or `pass_id`."""
        ...

    def span(self, name: str, **attrs: Any) -> AbstractContextManager[SpanHandle]:
        """Opens a span (nested under the innermost open span of this process) and closes it on exit.

        An exception inside closes the span with ok=False and the exception's type and message, then
        propagates unchanged. Telemetry never swallows an error.
        """
        ...

    def metric(self, name: str, value: float | None, unit: str, **attrs: Any) -> None:
        """Writes one reading. `value` None records an unread reading."""
        ...

    def event(self, name: str, **attrs: Any) -> None:
        """Writes one point-in-time record."""
        ...


class TelemetryReader(Protocol):
    def rows(
        self, *, after_seq: int = 0, kind: str | None = None, name_prefix: str | None = None,
        run: str | None = None, round: int | None = None, pass_id: str | None = None,  # noqa: A002
        limit: int | None = None,
    ) -> Sequence[TelemetryRow]:
        """Raw rows in seq order matching every filter given. A live view polls with `after_seq`."""
        ...

    def spans(
        self, *, name_prefix: str | None = None, run: str | None = None, round: int | None = None,  # noqa: A002
        pass_id: str | None = None,
    ) -> Sequence[TelemetryRow]:
        """One row per span, in open order: its close row, or its open row while still in flight."""
        ...
