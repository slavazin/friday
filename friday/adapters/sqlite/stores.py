"""SQLite implementations of the Ledger, EventLog and SpendLog ports."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import time
from collections.abc import Sequence
from pathlib import Path

from friday.domain.events import StatusFlip, Steer
from friday.domain.lifecycle import FlipCause
from friday.domain.types import DeltaKind, LedgerDelta
from friday.ports.models import ChatResult

SCHEMA = Path(__file__).with_name("schema.sql")


def connect(path: str | Path) -> sqlite3.Connection:
    """A connection to `path` with the schema applied."""
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    conn.executescript(SCHEMA.read_text())
    return conn


class SqliteLedger:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def append(self, delta: LedgerDelta) -> None:
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
        self.conn.execute(
            "INSERT INTO events (type, subject_id, round, body) VALUES ('flip', ?, ?, ?)",
            (flip.subject_id, flip.round, json.dumps(body, default=str)),
        )

    def append_steer(self, steer: Steer) -> None:
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
        self.conn.execute(
            "UPDATE events SET applied_round = ? WHERE seq = (SELECT seq FROM events WHERE type = 'steer' "
            "AND applied_round IS NULL AND body = ? ORDER BY seq LIMIT 1)",
            (round_, json.dumps(dataclasses.asdict(steer))),
        )


class SqliteSpendLog:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def record(self, role: str, tag: str, result: ChatResult | None, ok: bool, error: str = "") -> None:
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
