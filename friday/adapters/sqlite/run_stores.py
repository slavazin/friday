"""SQLite implementations of the PassStore, JudgeStore, ObservationStore, PoolStore and ExposureStore ports.

Every write runs in a `BEGIN IMMEDIATE` transaction (`stores.transaction`). Rows come back in insertion
order, field for field as they were written. A duplicate key on an add raises `StoreConflict`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from friday.adapters.sqlite import codec
from friday.adapters.sqlite.stores import transaction
from friday.domain.types import (
    Arm,
    Disposition,
    EvalReport,
    Fire,
    LensAnswer,
    Observation,
    ObservationStatus,
    PairKind,
    PairPlan,
    Pass,
    Pool,
    PoolStatus,
    Regime,
)


class StoreConflict(ValueError):
    """An add would write a row whose key is already recorded (or a pair slot already held)."""


class StoreCorrupt(RuntimeError):
    """A row the stores wrote together is missing its partner (never read as a default)."""


_KEY_CLASHES = frozenset({"SQLITE_CONSTRAINT_PRIMARYKEY", "SQLITE_CONSTRAINT_UNIQUE"})


@contextmanager
def _adding(conn: sqlite3.Connection, what: str) -> Iterator[None]:
    """A write transaction whose key clashes surface as `StoreConflict`."""
    try:
        with transaction(conn):
            yield
    except sqlite3.IntegrityError as e:
        if e.sqlite_errorname not in _KEY_CLASHES:
            raise
        raise StoreConflict(f"{what}: {e}") from e


_PASS_COLS = ("id, round, category, arm, worker, regime, composition, pair_slot, artifact_path, repaired, "
              "transport_failed, failed, composed_tokens")


def _pass_of(r: tuple) -> Pass:
    return Pass(id=r[0], round=r[1], category=r[2], arm=Arm(r[3]), worker=r[4],
                composition=codec.decode_composition(r[6]), regime=Regime(r[5]), artifact_path=r[8],
                repaired=bool(r[9]), transport_failed=bool(r[10]), pair_slot=r[7], failed=r[11],
                composed_tokens=r[12])


class SqlitePassStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def add_pass(self, pass_: Pass) -> None:
        """Writes the pass row; a recorded id or a held pair slot raises `StoreConflict`."""
        p = pass_
        with _adding(self.conn, f"pass {p.id}"):
            self.conn.execute(
                f"INSERT INTO passes ({_PASS_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (p.id, p.round, p.category, p.arm.value, p.worker, p.regime.value,
                 codec.encode_composition(p.composition), p.pair_slot, p.artifact_path, int(p.repaired),
                 int(p.transport_failed), p.failed, p.composed_tokens),
            )

    def add_eval(self, report: EvalReport) -> None:
        """Writes the report to `evals` and `eval_details` in one transaction; its pass must be recorded,
        and a pass's report is written once."""
        e = report
        with _adding(self.conn, f"eval of {e.pass_id}"):
            self.conn.execute(
                "INSERT INTO evals (pass_id, regime, checks, console_errors, a, r, rubric, q, eslint_errors, "
                "axe_violations, loss) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (e.pass_id, e.regime.value, codec.encode_check_results(e.checks),
                 codec.encode_strings(e.console_errors), e.A, e.R, codec.encode_ints(e.rubric), e.Q,
                 e.eslint_errors, e.axe_violations, e.loss),
            )
            self.conn.execute(
                "INSERT INTO eval_details (pass_id, p, h, c, silent_controls, probes, how_to) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (e.pass_id, e.P, e.H, e.C, e.silent_controls, codec.encode_probes(e.probes),
                 codec.encode_how_to(e.how_to)),
            )

    def passes(self, round_: int | None = None) -> Sequence[Pass]:
        """The recorded passes (of `round_` when given) in insertion order."""
        sql = f"SELECT {_PASS_COLS} FROM passes"
        args: tuple[object, ...] = ()
        if round_ is not None:
            sql += " WHERE round = ?"
            args = (round_,)
        return [_pass_of(r) for r in self.conn.execute(sql + " ORDER BY rowid", args).fetchall()]

    def eval_of(self, pass_id: str) -> EvalReport | None:
        """The pass's report as written, or None when none is recorded."""
        r = self.conn.execute(
            "SELECT e.pass_id, e.regime, e.checks, e.console_errors, e.a, e.r, e.rubric, e.q, e.loss, "
            "e.eslint_errors, e.axe_violations, d.p, d.h, d.c, d.probes, d.how_to, d.silent_controls, "
            "d.pass_id FROM evals e LEFT JOIN eval_details d ON d.pass_id = e.pass_id WHERE e.pass_id = ?",
            (pass_id,),
        ).fetchone()
        if r is None:
            return None
        if r[17] is None:
            raise StoreCorrupt(f"eval of {pass_id} has no eval_details row")
        return EvalReport(
            pass_id=r[0], regime=Regime(r[1]), checks=codec.decode_check_results(r[2]),
            console_errors=codec.decode_strings(r[3]), A=r[4], R=r[5], rubric=codec.decode_ints(r[6]), Q=r[7],
            loss=r[8], eslint_errors=r[9], axe_violations=r[10], P=r[11], H=r[12], C=r[13],
            probes=codec.decode_probes(r[14]), how_to=codec.decode_how_to(r[15]), silent_controls=r[16],
        )


class SqliteJudgeStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def add_lens_answers(self, answers: Sequence[LensAnswer]) -> None:
        """Inserts the answers in one transaction; a recorded (pass, lens) raises and writes none of them."""
        with _adding(self.conn, "lens answers"):
            self.conn.executemany(
                "INSERT INTO lens_answers (pass_id, lens_id, p, quote, judge_model) VALUES (?, ?, ?, ?, ?)",
                [(a.pass_id, a.lens_id, a.p, a.quote, a.judge_model) for a in answers],
            )

    def add_fires(self, fires: Sequence[Fire]) -> None:
        """Inserts the fires in one transaction; a recorded (pass, decision) raises and writes none of them."""
        with _adding(self.conn, "fires"):
            self.conn.executemany(
                "INSERT INTO fires (pass_id, decision_id, round, disposition, quote) VALUES (?, ?, ?, ?, ?)",
                [(f.pass_id, f.decision_id, f.round, f.disposition.value, f.quote) for f in fires],
            )

    def lens_answers(self, pass_ids: Sequence[str]) -> Sequence[LensAnswer]:
        """The answers on any of `pass_ids`, in insertion order."""
        rows = self.conn.execute(
            "SELECT pass_id, lens_id, p, quote, judge_model FROM lens_answers "
            "WHERE pass_id IN (SELECT value FROM json_each(?)) ORDER BY rowid",
            (codec.dumps(list(pass_ids)),),
        ).fetchall()
        return [LensAnswer(r[0], r[1], r[2], r[3], r[4]) for r in rows]

    def fires_of(self, decision_id: str) -> Sequence[Fire]:
        """The decision's fires, in insertion order."""
        rows = self.conn.execute(
            "SELECT pass_id, decision_id, round, disposition, quote FROM fires WHERE decision_id = ? ORDER BY rowid",
            (decision_id,),
        ).fetchall()
        return [Fire(r[0], r[1], r[2], Disposition(r[3]), r[4]) for r in rows]


_OBS_COLS = "id, pass_id, round, lens_id, category, shape, direction, quote, status, pool_id, direction_label"


def _obs_row(o: Observation) -> tuple:
    return (o.id, o.pass_id, o.round, o.lens_id, o.category, o.shape, o.direction, o.quote, o.status.value,
            o.pool_id, o.direction_label)


class SqliteObservationStore:
    """The observation register: ids reserved once, fields corrected in place by the round close."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def add_observations(self, observations: Sequence[Observation]) -> None:
        """Inserts new observations in one transaction; a recorded id raises and writes none of them."""
        with _adding(self.conn, "observations"):
            self.conn.executemany(
                f"INSERT INTO observations ({_OBS_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [_obs_row(o) for o in observations],
            )

    def put_observation(self, observation: Observation) -> None:
        """Writes the observation, correcting a recorded one in place (its position in the order is kept).
        Its pass, round, category and quote are frozen; changing one raises."""
        with transaction(self.conn):
            self.conn.execute(
                f"INSERT INTO observations ({_OBS_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET pass_id = excluded.pass_id, round = excluded.round, "
                "lens_id = excluded.lens_id, category = excluded.category, shape = excluded.shape, "
                "direction = excluded.direction, quote = excluded.quote, status = excluded.status, "
                "pool_id = excluded.pool_id, direction_label = excluded.direction_label",
                _obs_row(observation),
            )

    def observations(self) -> Sequence[Observation]:
        """Every observation, in the order their ids were first recorded."""
        rows = self.conn.execute(f"SELECT {_OBS_COLS} FROM observations ORDER BY rowid").fetchall()
        return [Observation(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], ObservationStatus(r[8]), r[9], r[10])
                for r in rows]


class SqlitePoolStore:
    """The pool register: ids reserved once, fields corrected in place by the round close."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def put_pool(self, pool: Pool) -> None:
        """Writes the pool, correcting a recorded one in place (its position in the order is kept)."""
        p = pool
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO pools (id, lens_id, shape, direction_labels, status, merged_into, refused_at_round) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET lens_id = excluded.lens_id, "
                "shape = excluded.shape, direction_labels = excluded.direction_labels, status = excluded.status, "
                "merged_into = excluded.merged_into, refused_at_round = excluded.refused_at_round",
                (p.id, p.lens_id, p.shape, codec.encode_strings(p.direction_labels), p.status.value, p.merged_into,
                 p.refused_at_round),
            )

    def pools(self) -> Sequence[Pool]:
        """Every pool, in the order their ids were first recorded."""
        rows = self.conn.execute(
            "SELECT id, lens_id, shape, direction_labels, status, merged_into, refused_at_round FROM pools "
            "ORDER BY rowid"
        ).fetchall()
        return [Pool(r[0], r[1], r[2], codec.decode_strings(r[3]), PoolStatus(r[4]), r[5], r[6]) for r in rows]


class SqliteExposureStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def add_plans(self, round_: int, plans: Sequence[PairPlan]) -> None:
        """Records a round's pair plans before its passes run (the exposure coins, as drawn). A round is
        recorded once, an empty one included; a second call for it raises and writes nothing."""
        with _adding(self.conn, f"round {round_} plans"):
            self.conn.execute("INSERT INTO exposure_rounds (round) VALUES (?)", (round_,))
            self.conn.executemany(
                "INSERT INTO exposures (round, seq, category, kind, decision_id, treated_slot) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(round_, i, p.category, p.kind.value, p.decision_id, p.treated_slot) for i, p in enumerate(plans)],
            )

    def plans(self, round_: int) -> Sequence[PairPlan]:
        """The round's plans in the order drawn; empty when none are recorded."""
        rows = self.conn.execute(
            "SELECT category, kind, decision_id, treated_slot FROM exposures WHERE round = ? ORDER BY seq",
            (round_,),
        ).fetchall()
        return [PairPlan(r[0], PairKind(r[1]), r[2], r[3]) for r in rows]
