"""Telemetry log adapters: SQLite (the run's) and in-memory (tests, dry runs without a database).

Both write the rows of `friday.ports.telemetry`. The SQLite log uses its own table in the run database,
in WAL mode, so pass processes, the round close and a live web view read and write it at once. Rows are
append-only (triggers refuse UPDATE and DELETE).
"""

from __future__ import annotations

import contextvars
import json
import math
import os
import re
import sqlite3
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from friday.ports.telemetry import ROW_KINDS, Scope, TelemetryRow

DDL = """
CREATE TABLE IF NOT EXISTS telemetry (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL CHECK (kind IN ('span', 'metric', 'event')),
  name TEXT NOT NULL, run TEXT NOT NULL, round INTEGER, pass_id TEXT, at REAL NOT NULL,
  span_id TEXT, parent_id TEXT, ended_at REAL, ok INTEGER, error TEXT NOT NULL DEFAULT '',
  value REAL, unit TEXT NOT NULL DEFAULT '', attrs TEXT NOT NULL DEFAULT '{}', pid INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS telemetry_by_pass ON telemetry(pass_id, seq);
CREATE INDEX IF NOT EXISTS telemetry_by_round ON telemetry(run, round, seq);
CREATE INDEX IF NOT EXISTS telemetry_by_span ON telemetry(span_id, seq);
CREATE TRIGGER IF NOT EXISTS telemetry_no_update BEFORE UPDATE ON telemetry
  BEGIN SELECT RAISE(ABORT, 'telemetry is append-only'); END;
CREATE TRIGGER IF NOT EXISTS telemetry_no_delete BEFORE DELETE ON telemetry
  BEGIN SELECT RAISE(ABORT, 'telemetry is append-only'); END;
"""

# Attribute names refused outright: secrets, and the text of prompts and replies (stored elsewhere or not at all).
_SECRET_KEY = re.compile(r"(api_?key|secret|passw|authori[sz]ation|bearer|cookie|credential)", re.IGNORECASE)
_TEXT_KEYS = frozenset({"prompt", "messages", "content", "reply", "html"})
_SECRET_VALUE = re.compile(r"\bBearer\s+\S", re.IGNORECASE)


class TelemetryError(ValueError):
    """A row the log refuses: a secret-like or text attribute, a non-JSON value, or an unknown kind."""


def check_attrs(attrs: dict[str, Any]) -> str:
    """The attributes as JSON; raises TelemetryError on a refused name or value."""
    for k, v in attrs.items():
        if _SECRET_KEY.search(k) or k in _TEXT_KEYS:
            raise TelemetryError(f"telemetry refuses attribute {k!r}")
        if isinstance(v, str) and _SECRET_VALUE.search(v):
            raise TelemetryError(f"telemetry refuses a credential-like value under {k!r}")
        if isinstance(v, float) and not math.isfinite(v):
            raise TelemetryError(f"telemetry refuses non-finite {k!r}={v}")
    try:
        return json.dumps(attrs, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as e:
        raise TelemetryError(f"telemetry attributes are not JSON: {e}") from e


@dataclass
class _Handle:
    id: str
    attrs: dict[str, Any] = field(default_factory=dict)

    def set(self, **attrs: Any) -> None:
        check_attrs(attrs)
        self.attrs.update(attrs)


class _Sink:
    """Where rows go. Subclasses store `TelemetryRow`s (without seq) and assign seq."""

    def write(self, row: TelemetryRow, pid: int) -> None:
        raise NotImplementedError


class _Log:
    """Telemetry over a sink, bound to a scope. Nesting is tracked per process (and per thread/task)."""

    def __init__(
        self, sink: _Sink, scope: Scope, *, clock: Callable[[], float], current: contextvars.ContextVar[str | None],
        new_id: Callable[[], str], pid: int,
    ) -> None:
        self._sink, self.scope, self._clock, self._current, self._new_id, self._pid = (
            sink, scope, clock, current, new_id, pid)

    def scoped(self, *, round: int | None = None, pass_id: str | None = None) -> _Log:  # noqa: A002
        return _Log(self._sink, self.scope.at(round=round, pass_id=pass_id), clock=self._clock,
                    current=self._current, new_id=self._new_id, pid=self._pid)

    def _row(self, kind: str, name: str, at: float, **kw: Any) -> TelemetryRow:
        if kind not in ROW_KINDS:
            raise TelemetryError(f"unknown row kind {kind!r}")
        if not name:
            raise TelemetryError("a row needs a name")
        return TelemetryRow(seq=0, kind=kind, name=name, scope=self.scope, at=at, **kw)

    @contextmanager
    def span(self, name: str, **attrs: Any) -> Iterator[_Handle]:
        check_attrs(attrs)
        handle = _Handle(self._new_id(), dict(attrs))
        parent = self._current.get()
        start = self._clock()
        self._sink.write(self._row("span", name, start, span_id=handle.id, parent_id=parent,
                                   attrs=dict(handle.attrs)), self._pid)
        token = self._current.set(handle.id)
        ok, error = True, ""
        try:
            yield handle
        except BaseException as e:
            ok, error = False, f"{type(e).__name__}: {e}"[:500]
            raise
        finally:
            self._current.reset(token)
            self._sink.write(self._row("span", name, start, span_id=handle.id, parent_id=parent,
                                       ended_at=self._clock(), ok=ok, error=error, attrs=dict(handle.attrs)),
                             self._pid)

    def metric(self, name: str, value: float | None, unit: str, **attrs: Any) -> None:
        if value is not None and (isinstance(value, bool) or not math.isfinite(float(value))):
            raise TelemetryError(f"metric {name} needs a finite number or None, got {value!r}")
        if not unit:
            raise TelemetryError(f"metric {name} needs a unit")
        check_attrs(attrs)
        self._sink.write(self._row("metric", name, self._clock(), parent_id=self._current.get(),
                                   value=None if value is None else float(value), unit=unit, attrs=dict(attrs)),
                         self._pid)

    def event(self, name: str, **attrs: Any) -> None:
        check_attrs(attrs)
        self._sink.write(self._row("event", name, self._clock(), parent_id=self._current.get(), attrs=dict(attrs)),
                         self._pid)


def _matches(row: TelemetryRow, kind: str | None, name_prefix: str | None, run: str | None,
             round_: int | None, pass_id: str | None) -> bool:
    return ((kind is None or row.kind == kind)
            and (name_prefix is None or row.name.startswith(name_prefix))
            and (run is None or row.scope.run == run)
            and (round_ is None or row.scope.round == round_)
            and (pass_id is None or row.scope.pass_id == pass_id))


def _merge_spans(rows: Sequence[TelemetryRow]) -> list[TelemetryRow]:
    latest: dict[str, TelemetryRow] = {}
    for r in rows:
        if r.span_id is not None and (r.span_id not in latest or r.ended_at is not None):
            latest[r.span_id] = r
    return sorted(latest.values(), key=lambda r: (r.at, r.seq))


class _MemorySink(_Sink):
    def __init__(self) -> None:
        self.rows: list[TelemetryRow] = []

    def write(self, row: TelemetryRow, pid: int) -> None:
        self.rows.append(TelemetryRow(**{**row.__dict__, "seq": len(self.rows) + 1}))


class MemoryTelemetry(_Log):
    """Telemetry and TelemetryReader over a list; one shared list across `scoped` copies."""

    def __init__(self, scope: Scope, *, clock: Callable[[], float] = time.time) -> None:
        self._mem = _MemorySink()
        counter = iter(range(1, 1 << 62))
        super().__init__(self._mem, scope, clock=clock, current=contextvars.ContextVar("friday_span", default=None),
                         new_id=lambda: f"s{next(counter)}", pid=0)

    def rows(self, *, after_seq: int = 0, kind: str | None = None, name_prefix: str | None = None,
             run: str | None = None, round: int | None = None, pass_id: str | None = None,  # noqa: A002
             limit: int | None = None) -> Sequence[TelemetryRow]:
        out = [r for r in self._mem.rows if r.seq > after_seq and _matches(r, kind, name_prefix, run, round, pass_id)]
        return out[:limit] if limit is not None else out

    def spans(self, *, name_prefix: str | None = None, run: str | None = None, round: int | None = None,  # noqa: A002
              pass_id: str | None = None) -> Sequence[TelemetryRow]:
        return _merge_spans(self.rows(kind="span", name_prefix=name_prefix, run=run, round=round, pass_id=pass_id))


def connect_telemetry(path: str | Path, *, busy_timeout_ms: int) -> sqlite3.Connection:
    """A connection to the run database with the telemetry table applied, in WAL mode."""
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(DDL)
    return conn


class _SqliteSink(_Sink):
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def write(self, row: TelemetryRow, pid: int) -> None:
        self.conn.execute(
            "INSERT INTO telemetry (kind, name, run, round, pass_id, at, span_id, parent_id, ended_at, ok, error, "
            "value, unit, attrs, pid) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row.kind, row.name, row.scope.run, row.scope.round, row.scope.pass_id, row.at, row.span_id,
             row.parent_id, row.ended_at, None if row.ok is None else int(row.ok), row.error, row.value, row.unit,
             check_attrs(row.attrs), pid),
        )


_COLS = "seq, kind, name, run, round, pass_id, at, span_id, parent_id, ended_at, ok, error, value, unit, attrs"


def _from_db(t: tuple[Any, ...]) -> TelemetryRow:
    seq, kind, name, run, round_, pass_id, at, span_id, parent_id, ended_at, ok, error, value, unit, attrs = t
    return TelemetryRow(seq=seq, kind=kind, name=name, scope=Scope(run, round_, pass_id), at=at, span_id=span_id,
                        parent_id=parent_id, ended_at=ended_at, ok=None if ok is None else bool(ok), error=error,
                        value=value, unit=unit, attrs=json.loads(attrs))


class SqliteTelemetry(_Log):
    """Telemetry and TelemetryReader over the run database's `telemetry` table.

    Open one per process (pass processes included): a span's nesting is tracked within its process,
    and each row records the writing process id.
    """

    def __init__(self, conn: sqlite3.Connection, scope: Scope, *, clock: Callable[[], float] = time.time,
                 pid: int | None = None) -> None:
        self.conn = conn
        super().__init__(_SqliteSink(conn), scope, clock=clock,
                         current=contextvars.ContextVar("friday_span", default=None),
                         new_id=lambda: uuid.uuid4().hex[:16], pid=os.getpid() if pid is None else pid)

    def rows(self, *, after_seq: int = 0, kind: str | None = None, name_prefix: str | None = None,
             run: str | None = None, round: int | None = None, pass_id: str | None = None,  # noqa: A002
             limit: int | None = None) -> Sequence[TelemetryRow]:
        where, args = ["seq > ?"], [after_seq]
        for col, v in (("kind", kind), ("run", run), ("round", round), ("pass_id", pass_id)):
            if v is not None:
                where.append(f"{col} = ?")
                args.append(v)
        if name_prefix is not None:
            where.append("substr(name, 1, ?) = ?")
            args += [len(name_prefix), name_prefix]
        sql = f"SELECT {_COLS} FROM telemetry WHERE {' AND '.join(where)} ORDER BY seq"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return [_from_db(t) for t in self.conn.execute(sql, args)]

    def spans(self, *, name_prefix: str | None = None, run: str | None = None, round: int | None = None,  # noqa: A002
              pass_id: str | None = None) -> Sequence[TelemetryRow]:
        return _merge_spans(self.rows(kind="span", name_prefix=name_prefix, run=run, round=round, pass_id=pass_id))


def open_run_telemetry(db_path: str | Path, run: str, *, busy_timeout_ms: int) -> SqliteTelemetry:
    """The run's telemetry log for this process: one connection to `db_path`, scoped to `run`
    (`main` or `critic`). Call once per process, pass processes included; narrow with `.scoped()`."""
    return SqliteTelemetry(connect_telemetry(db_path, busy_timeout_ms=busy_timeout_ms), Scope(run))
