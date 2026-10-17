"""The process model over `friday.db` (SPEC § 11 Single writer; ARCHITECTURE.md Concurrency).

Each pass runs in its own process and opens its own connection with `open_pass_stores`: it can write only
its own rows (its pass, its eval, its lens answers, its fires) and spend rows. The round close runs alone
in the parent with `open_run_stores`, the one writer of observations, pools, exposures, Ledger and events.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from friday.adapters.sqlite.run_stores import (
    SqliteExposureStore,
    SqliteJudgeStore,
    SqliteObservationStore,
    SqlitePassStore,
    SqlitePoolStore,
)
from friday.adapters.sqlite.stores import SqliteEventLog, SqliteLedger, SqliteSpendLog, connect, transaction
from friday.config import RunSpec
from friday.domain.types import EvalReport, Fire, LensAnswer, Pass


class ForeignRowError(ValueError):
    """A pass process tried to write a row that belongs to another pass."""


def db_path(spec: RunSpec, root: Path) -> Path:
    """The run's database file: `paths.db` under `root`."""
    return root / spec.paths.db


class OwnPassStore:
    """A PassStore for one pass's process: reads everything, writes only the pass `pass_id`'s rows."""

    def __init__(self, conn: sqlite3.Connection, pass_id: str) -> None:
        self.pass_id = pass_id
        self._store = SqlitePassStore(conn)

    def _own(self, pass_id: str) -> None:
        if pass_id != self.pass_id:
            raise ForeignRowError(f"the process of pass {self.pass_id} cannot write rows of pass {pass_id}")

    def add_pass(self, pass_: Pass) -> None:
        self._own(pass_.id)
        self._store.add_pass(pass_)

    def add_eval(self, report: EvalReport) -> None:
        self._own(report.pass_id)
        self._store.add_eval(report)

    def passes(self, round_: int | None = None) -> Sequence[Pass]:
        return self._store.passes(round_)

    def eval_of(self, pass_id: str) -> EvalReport | None:
        return self._store.eval_of(pass_id)


class OwnJudgeStore:
    """A JudgeStore for one pass's process: reads everything, writes only answers and fires on `pass_id`."""

    def __init__(self, conn: sqlite3.Connection, pass_id: str) -> None:
        self.pass_id = pass_id
        self._store = SqliteJudgeStore(conn)

    def _own(self, pass_ids: Sequence[str]) -> None:
        foreign = sorted({p for p in pass_ids if p != self.pass_id})
        if foreign:
            raise ForeignRowError(f"the process of pass {self.pass_id} cannot write rows of passes {foreign}")

    def add_lens_answers(self, answers: Sequence[LensAnswer]) -> None:
        self._own([a.pass_id for a in answers])
        self._store.add_lens_answers(answers)

    def add_fires(self, fires: Sequence[Fire]) -> None:
        self._own([f.pass_id for f in fires])
        self._store.add_fires(fires)

    def lens_answers(self, pass_ids: Sequence[str]) -> Sequence[LensAnswer]:
        return self._store.lens_answers(pass_ids)

    def fires_of(self, decision_id: str) -> Sequence[Fire]:
        return self._store.fires_of(decision_id)


@dataclass(frozen=True)
class PassStores:
    """What one pass's process may write: its own rows, and a spend row per model call."""

    conn: sqlite3.Connection
    pass_id: str
    passes: OwnPassStore
    judge: OwnJudgeStore
    spend: SqliteSpendLog

    def record(self, pass_: Pass, report: EvalReport | None, answers: Sequence[LensAnswer] = (),
               fires: Sequence[Fire] = ()) -> None:
        """Writes the finished pass's rows in one transaction: all of them are recorded, or none is."""
        with transaction(self.conn):
            self.passes.add_pass(pass_)
            if report is not None:
                self.passes.add_eval(report)
            if answers:
                self.judge.add_lens_answers(answers)
            if fires:
                self.judge.add_fires(fires)

    def close(self) -> None:
        self.conn.close()


@dataclass(frozen=True)
class RunStores:
    """Every store over `friday.db`, for the round close in the parent process (the single writer)."""

    conn: sqlite3.Connection
    passes: SqlitePassStore
    judge: SqliteJudgeStore
    observations: SqliteObservationStore
    pools: SqlitePoolStore
    exposures: SqliteExposureStore
    ledger: SqliteLedger
    events: SqliteEventLog
    spend: SqliteSpendLog

    def close(self) -> None:
        self.conn.close()


def open_pass_stores(db: Path, pass_id: str) -> PassStores:
    """A new connection to an existing `db` and the stores of the pass `pass_id`. Call it inside the pass's
    own process; the connection refuses use from any other."""
    if not db.exists():
        raise FileNotFoundError(f"{db} does not exist; the parent opens it with open_run_stores first")
    conn = connect(db)
    return PassStores(conn, pass_id, OwnPassStore(conn, pass_id), OwnJudgeStore(conn, pass_id), SqliteSpendLog(conn))


def open_run_stores(db: Path) -> RunStores:
    """A connection to `db` (created with the schema when absent) and every store over it."""
    conn = connect(db)
    return RunStores(
        conn, SqlitePassStore(conn), SqliteJudgeStore(conn), SqliteObservationStore(conn), SqlitePoolStore(conn),
        SqliteExposureStore(conn), SqliteLedger(conn), SqliteEventLog(conn), SqliteSpendLog(conn),
    )
