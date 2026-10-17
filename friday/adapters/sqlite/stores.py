"""SQLite implementations of the Ledger, EventLog and SpendLog ports."""

from __future__ import annotations

import dataclasses
import itertools
import json
import os
import sqlite3
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from friday.domain.events import StatusFlip, Steer
from friday.domain.lifecycle import FlipCause
from friday.domain.types import DeltaKind, LedgerDelta
from friday.ports.models import ChatResult

SCHEMA = Path(__file__).with_name("schema.sql")

# How long a writer waits for another process's write lock before SQLite reports the database locked.
# Not a run constant (run.yml holds none for it): a pass's writes take milliseconds.
BUSY_TIMEOUT_MS = 60_000
_SAVEPOINTS = itertools.count()


class ForeignProcessConnection(RuntimeError):
    """A connection was used in a process other than the one that opened it (one connection per process)."""


class ProcessConnection(sqlite3.Connection):
    """A connection that refuses use from any process but the one that opened it (a fork inherits it)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.owner_pid = os.getpid()

    def _check_owner(self) -> None:
        if os.getpid() != self.owner_pid:
            raise ForeignProcessConnection(
                f"connection opened by process {self.owner_pid} used in process {os.getpid()}; open one per process"
            )

    def cursor(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        self._check_owner()
        return super().cursor(*args, **kwargs)

    def execute(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        self._check_owner()
        return super().execute(*args, **kwargs)

    def executemany(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        self._check_owner()
        return super().executemany(*args, **kwargs)

    def executescript(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        self._check_owner()
        return super().executescript(*args, **kwargs)


def connect(path: str | Path, busy_timeout_ms: int = BUSY_TIMEOUT_MS) -> sqlite3.Connection:
    """A connection to `path` with the schema applied: WAL, `busy_timeout`, foreign keys on, autocommit
    outside explicit `transaction` blocks. Open one per process; it refuses use from another process."""
    conn = sqlite3.connect(
        str(path), timeout=busy_timeout_ms / 1000, isolation_level=None, check_same_thread=False,
        factory=ProcessConnection,
    )
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA.read_text())
    (mode,) = conn.execute("PRAGMA journal_mode").fetchone()
    if str(mode).lower() != "wal":
        raise sqlite3.OperationalError(f"{path} is in journal mode {mode!r}, not WAL")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """A `BEGIN IMMEDIATE` transaction: the write lock is taken at the start, so a writer waits on
    `busy_timeout` instead of failing at its first write. Commits on exit, rolls back on any raise.
    Inside an open transaction it runs as a savepoint: a raise undoes only this block's writes, and the
    outer block commits."""
    if conn.in_transaction:
        name = f"sp_{next(_SAVEPOINTS)}"
        conn.execute(f"SAVEPOINT {name}")
        try:
            yield conn
        except BaseException:
            conn.execute(f"ROLLBACK TO {name}")
            conn.execute(f"RELEASE {name}")
            raise
        conn.execute(f"RELEASE {name}")
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


class SqliteLedger:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def append(self, delta: LedgerDelta) -> None:
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO ledger_deltas (decision_id, round, category, kind, d, dq) VALUES (?, ?, ?, ?, ?, ?)",
                (delta.decision_id, delta.round, delta.category, delta.kind.value, delta.d, delta.dQ),
            )

    def deltas(self, decision_id: str, kind: str | None = None) -> Sequence[LedgerDelta]:
        sql = "SELECT decision_id, round, category, kind, d, dq FROM ledger_deltas WHERE decision_id = ?"
        args: tuple[object, ...] = (decision_id,)
        if kind is not None:
            sql += " AND kind = ?"
            args += (str(kind),)
        rows = self.conn.execute(sql + " ORDER BY seq", args).fetchall()
        return [LedgerDelta(r[0], r[1], r[2], DeltaKind(r[3]), r[4], r[5]) for r in rows]


class SqliteEventLog:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def append_flip(self, flip: StatusFlip) -> None:
        body = dataclasses.asdict(flip)
        body["cause"] = flip.cause.value
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO events (type, subject_id, round, body) VALUES ('flip', ?, ?, ?)",
                (flip.subject_id, flip.round, json.dumps(body, default=str)),
            )

    def append_steer(self, steer: Steer) -> None:
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO events (type, subject_id, round, body) VALUES ('steer', ?, ?, ?)",
                (steer.decision_id, steer.issued_round, json.dumps(dataclasses.asdict(steer))),
            )

    def flips(self, subject_id: str | None = None) -> Sequence[StatusFlip]:
        sql = "SELECT body FROM events WHERE type = 'flip'"
        args: tuple[object, ...] = ()
        if subject_id is not None:
            sql += " AND subject_id = ?"
            args = (subject_id,)
        out = []
        for (body,) in self.conn.execute(sql + " ORDER BY seq", args):
            d = json.loads(body)
            d["cause"] = FlipCause(d["cause"])
            d["successors"] = tuple(d.get("successors", ()))
            out.append(StatusFlip(**d))
        return out

    def pending_steers(self) -> Sequence[Steer]:
        rows = self.conn.execute(
            "SELECT body FROM events WHERE type = 'steer' AND applied_round IS NULL ORDER BY seq"
        ).fetchall()
        return [Steer(**json.loads(b)) for (b,) in rows]

    def mark_steer_applied(self, steer: Steer, round_: int) -> None:
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE events SET applied_round = ? WHERE seq = (SELECT seq FROM events WHERE type = 'steer' "
                "AND applied_round IS NULL AND body = ? ORDER BY seq LIMIT 1)",
                (round_, json.dumps(dataclasses.asdict(steer))),
            )


class SqliteSpendLog:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def record(self, role: str, tag: str, result: ChatResult | None, ok: bool, error: str = "") -> None:
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO spend (at, role, tag, model, tokens_in, tokens_out, cost_usd, ok, error, sent) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    time.time(), role, tag,
                    result.model if result else None,
                    result.tokens_in if result else None,
                    result.tokens_out if result else None,
                    result.cost_usd if result else None,
                    int(ok), error,
                    json.dumps(result.raw.get("sent")) if result else None,
                ),
            )

    def total_usd(self) -> float:
        (total,) = self.conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM spend").fetchone()
        return float(total)
