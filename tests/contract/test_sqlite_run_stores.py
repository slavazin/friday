"""The SQLite pass, judge, observation, pool and exposure stores: port conformance, field-for-field round
trips, insert-only evidence, pass-owned writes, and the process model (concurrent writers, a killed writer)."""

import inspect
import multiprocessing
import os
import signal
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from friday.adapters import memory
from friday.adapters.sqlite import process as proc
from friday.adapters.sqlite.process import (
    ForeignRowError,
    OwnJudgeStore,
    OwnPassStore,
    open_pass_stores,
    open_run_stores,
)
from friday.adapters.sqlite.run_stores import (
    SqliteExposureStore,
    SqliteJudgeStore,
    SqliteObservationStore,
    SqlitePassStore,
    SqlitePoolStore,
    StoreConflict,
)
from friday.adapters.sqlite.stores import ForeignProcessConnection, connect, transaction
from friday.config import load_spec
from friday.domain.types import (
    Arm,
    Check,
    CheckResult,
    Composition,
    Disposition,
    EvalReport,
    Fire,
    HowToParse,
    HowToReading,
    HowToRung,
    LensAnswer,
    Observation,
    ObservationStatus,
    PairKind,
    PairPlan,
    Pass,
    Pool,
    PoolStatus,
    ProbeReading,
    Regime,
)
from friday.ports import stores

ROOT = Path(__file__).resolve().parents[2]


def methods(proto):
    return {n: inspect.signature(f) for n, f in vars(proto).items() if callable(f) and not n.startswith("_")}


@pytest.mark.parametrize(("impl", "port"), [
    (SqlitePassStore, stores.PassStore), (SqliteJudgeStore, stores.JudgeStore),
    (SqliteObservationStore, stores.ObservationStore), (SqlitePoolStore, stores.PoolStore),
    (SqliteExposureStore, stores.ExposureStore), (OwnPassStore, stores.PassStore), (OwnJudgeStore, stores.JudgeStore),
])
def test_sqlite_store_matches_its_port(impl, port):
    for name, sig in methods(port).items():
        assert hasattr(impl, name), f"{impl.__name__} lacks {name}"
        got = [p for p in inspect.signature(getattr(impl, name)).parameters if p != "self"]
        assert got == [p for p in sig.parameters if p != "self"], f"{impl.__name__}.{name}"


# ---- builders ----------------------------------------------------------------------------------------

def a_pass(id_="X-1", round_=3, slot: int | None = 0, category="canvas", request="R-1", failed=False) -> Pass:
    return Pass(id_, round_, category, Arm.JOURNAL, "glm-5.3@crusoe",
                Composition(request, ("D-0001", "D-0004"), exposed="D-0004", ablated=None, notes=False),
                Regime.R2, artifact_path=None, repaired=True, transport_failed=failed, pair_slot=slot)


def a_report(pass_id="X-1") -> EvalReport:
    check = Check("c1", ({"click": "#go"}, {"wait": 200}), {"text_changed": "#out", "nested": {"k": [1, 2]}})
    parse = HowToParse(HowToRung.SALVAGED, (check,), 2, ("#go",), ("#reset",), "salvaged after truncation")
    return EvalReport(
        pass_id=pass_id, regime=Regime.R2,
        checks=(CheckResult("c1", True), CheckResult("c2", False, "step 2: #missing not found")),
        console_errors=("TypeError: x is undefined",), A=0.1 + 0.2, R=1.0, rubric=(4, 0, 5), Q=9,
        loss=0.30000000000000004 / 3, eslint_errors=0, axe_violations=7, P=2 / 3, H=1e-17, C=0.0,
        probes=(ProbeReading(pass_id, (390, 844), True, (), 12, 5, ("#go", "#reset"), 0, 0.8),
                ProbeReading(pass_id, (1280, 800), False, ("boom",), 0, 0, (), 0, 0.0)),
        how_to=(HowToReading(pass_id, (390, 844), parse, (CheckResult("c1", False, "s1"),), 1 / 7),),
        silent_controls=3,
    )


@pytest.fixture
def db(tmp_path) -> Path:
    path = tmp_path / "friday.db"
    open_run_stores(path).close()
    return path


# ---- round trips --------------------------------------------------------------------------------------

def test_pass_and_eval_round_trip_every_field(db):
    s = open_run_stores(db)
    p = a_pass()
    p2 = replace(a_pass("X-2", slot=None, failed=False), arm=Arm.NOTES, regime=Regime.R1, artifact_path="artifacts/X-2",
                 repaired=False, composition=Composition("R-2", (), None, "D-0001", True))
    s.passes.add_pass(p)
    s.passes.add_pass(p2)
    report = a_report()
    s.passes.add_eval(report)
    got = s.passes.eval_of("X-1")
    assert got == report
    assert type(got.A) is float and type(got.R) is float and type(got.Q) is int
    assert got.how_to[0].parse.rung is HowToRung.SALVAGED and got.regime is Regime.R2
    assert isinstance(got.probes[0].viewport, tuple) and isinstance(got.how_to[0].parse.declared[0].steps, tuple)
    assert s.passes.passes() == [p, p2]
    assert s.passes.passes(3) == [p, p2] and s.passes.passes(4) == []
    back = s.passes.passes()[1]
    assert back.pair_slot is None and back.composition.exposed is None and back.composition.decision_ids == ()
    assert s.passes.passes()[0].pair_slot == 0 and s.passes.passes()[0].artifact_path is None


def test_absent_eval_reads_none_not_a_default(db):
    s = open_run_stores(db)
    s.passes.add_pass(a_pass())
    assert s.passes.eval_of("X-1") is None
    assert s.passes.eval_of("nope") is None


def test_eval_refuses_nan_rather_than_storing_null(db):
    s = open_run_stores(db)
    s.passes.add_pass(a_pass())
    with pytest.raises(ValueError):
        s.passes.add_eval(replace(a_report(), probes=(replace(a_report().probes[0], P=float("nan")),)))
    assert s.passes.eval_of("X-1") is None


def test_eval_of_an_unrecorded_pass_is_refused(db):
    s = open_run_stores(db)
    with pytest.raises(sqlite3.IntegrityError):
        s.passes.add_eval(a_report("ghost"))
    assert s.conn.execute("SELECT COUNT(*) FROM eval_details").fetchone() == (0,)


def test_lens_answers_and_fires_round_trip_with_judge_model_and_round(db):
    s = open_run_stores(db)
    answers = [LensAnswer("X-1", "L-01", 0.125, "q one", "deepseek-v4"), LensAnswer("X-2", "free", 1.0, "q", "")]
    s.judge.add_lens_answers(answers)
    assert s.judge.lens_answers(["X-1", "X-2"]) == answers
    assert s.judge.lens_answers(["X-2"]) == [answers[1]] and s.judge.lens_answers([]) == []
    fires = [Fire("X-1", "D-0001", 3, Disposition.CONTRADICTED, "quote"), Fire("X-2", "D-0001", 4)]
    s.judge.add_fires(fires)
    got = s.judge.fires_of("D-0001")
    assert got == fires and got[1].disposition is Disposition.UNDISPOSED and got[1].round == 4
    assert s.judge.fires_of("D-0009") == []


def test_observation_register_corrects_in_place_and_keeps_order(db):
    s = open_run_stores(db)
    o1 = Observation("O-1", "X-1", 3, "L-01", "canvas", "shape a", "do x", "quote 1")
    o2 = Observation("O-2", "X-2", 3, "free", "form", "shape b", "do y", "quote 2")
    s.observations.add_observations([o1, o2])
    o1c = replace(o1, status=ObservationStatus.CLOSED, pool_id="P-1", direction_label="label")
    s.observations.put_observation(o1c)
    assert s.observations.observations() == [o1c, o2]
    o3 = Observation("O-3", "X-3", 4, "L-02", "canvas", "s", "d", "q")
    s.observations.put_observation(o3)
    assert [o.id for o in s.observations.observations()] == ["O-1", "O-2", "O-3"]
    with pytest.raises(StoreConflict):
        s.observations.add_observations([o3])
    with pytest.raises(sqlite3.DatabaseError):
        s.observations.put_observation(replace(o1c, quote="rewritten"))
    with pytest.raises(sqlite3.DatabaseError):
        s.conn.execute("DELETE FROM observations")


def test_pool_register_round_trips_none_and_tuples(db):
    s = open_run_stores(db)
    p = Pool("P-1", "L-01", "shape", ("a", "b"))
    s.pools.put_pool(p)
    s.pools.put_pool(Pool("P-2", "free", "s2", ()))
    refused = replace(p, status=PoolStatus.REFUSED, refused_at_round=0)
    s.pools.put_pool(refused)
    got = s.pools.pools()
    assert got == [refused, Pool("P-2", "free", "s2", ())]
    assert got[0].refused_at_round == 0 and got[1].refused_at_round is None and got[1].merged_into is None
    assert isinstance(got[0].direction_labels, tuple)


def test_exposure_plans_are_recorded_once_per_round_in_drawn_order(db):
    s = open_run_stores(db)
    plans = [PairPlan("form", PairKind.EXPOSURE, "D-0004", 1), PairPlan("canvas", PairKind.NONE),
             PairPlan("audio", PairKind.ABLATION, "D-0001", 0)]
    s.exposures.add_plans(5, plans)
    assert s.exposures.plans(5) == plans and s.exposures.plans(6) == []
    with pytest.raises(StoreConflict):
        s.exposures.add_plans(5, [PairPlan("x", PairKind.NONE)])
    assert s.exposures.plans(5) == plans
    s.exposures.add_plans(7, [])
    with pytest.raises(StoreConflict):            # an empty round is still recorded once
        s.exposures.add_plans(7, plans)
    assert s.exposures.plans(7) == []


# ---- insert-only evidence -----------------------------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "UPDATE passes SET worker = 'other'", "DELETE FROM passes",
    "UPDATE evals SET loss = 0", "DELETE FROM evals",
    "UPDATE eval_details SET p = 0", "DELETE FROM eval_details",
    "UPDATE lens_answers SET p = 0", "DELETE FROM lens_answers",
    "UPDATE fires SET disposition = 'applied'", "DELETE FROM fires",
    "UPDATE exposures SET treated_slot = 0", "DELETE FROM exposures",
    "UPDATE exposure_rounds SET round = 99", "DELETE FROM exposure_rounds",
    "DELETE FROM pools",
])
def test_evidence_rows_cannot_be_rewritten_or_deleted(db, sql):
    s = open_run_stores(db)
    s.passes.add_pass(a_pass())
    s.passes.add_eval(a_report())
    s.judge.add_lens_answers([LensAnswer("X-1", "L-01", 0.5, "q", "m")])
    s.judge.add_fires([Fire("X-1", "D-0001", 3)])
    s.exposures.add_plans(3, [PairPlan("canvas", PairKind.EXPOSURE, "D-0001", 1)])
    s.pools.put_pool(Pool("P-1", "L-01", "s", ()))
    with pytest.raises(sqlite3.DatabaseError):
        s.conn.execute(sql)


def test_duplicate_pass_eval_answer_and_fire_are_refused(db):
    s = open_run_stores(db)
    s.passes.add_pass(a_pass())
    s.passes.add_eval(a_report())
    with pytest.raises(StoreConflict):
        s.passes.add_pass(a_pass())
    with pytest.raises(StoreConflict):
        s.passes.add_eval(replace(a_report(), loss=0.0))
    assert s.passes.eval_of("X-1") == a_report()
    s.judge.add_lens_answers([LensAnswer("X-1", "L-01", 0.5, "q", "m")])
    with pytest.raises(StoreConflict):            # the batch is atomic: the new L-02 row is not kept either
        s.judge.add_lens_answers([LensAnswer("X-1", "L-02", 0.1, "q", "m"), LensAnswer("X-1", "L-01", 0.9, "q", "m")])
    assert [a.lens_id for a in s.judge.lens_answers(["X-1"])] == ["L-01"]
    s.judge.add_fires([Fire("X-1", "D-0001", 3)])
    with pytest.raises(StoreConflict):
        s.judge.add_fires([Fire("X-1", "D-0001", 3, Disposition.APPLIED)])


def test_two_passes_cannot_both_hold_one_pair_slot(db):
    s = open_run_stores(db)
    s.passes.add_pass(a_pass("X-1", slot=1))
    with pytest.raises(StoreConflict):
        s.passes.add_pass(a_pass("X-9", slot=1))
    s.passes.add_pass(a_pass("X-2", slot=0))                       # the pair's other slot
    s.passes.add_pass(a_pass("X-3", slot=1, request="R-2"))        # another request's pair
    s.passes.add_pass(a_pass("X-4", slot=1, round_=4))             # another round
    assert [p.id for p in s.passes.passes()] == ["X-1", "X-2", "X-3", "X-4"]


def test_a_transport_failed_pass_leaves_its_slot_to_the_rerun(db):
    s = open_run_stores(db)
    s.passes.add_pass(a_pass("X-1", slot=0, failed=True))
    s.passes.add_pass(a_pass("X-1r", slot=0))
    with pytest.raises(StoreConflict):
        s.passes.add_pass(a_pass("X-1rr", slot=0))


# ---- a pass writes only its own rows ------------------------------------------------------------------

def test_pass_process_cannot_write_another_pass_rows(db):
    ps = open_pass_stores(db, "X-1")
    with pytest.raises(ForeignRowError):
        ps.passes.add_pass(a_pass("X-2"))
    ps.passes.add_pass(a_pass("X-1"))
    with pytest.raises(ForeignRowError):
        ps.passes.add_eval(a_report("X-2"))
    with pytest.raises(ForeignRowError):
        ps.judge.add_lens_answers([LensAnswer("X-1", "L-01", 0.5, "q"), LensAnswer("X-2", "L-01", 0.5, "q")])
    with pytest.raises(ForeignRowError):
        ps.judge.add_fires([Fire("X-2", "D-0001", 3)])
    assert ps.judge.lens_answers(["X-1", "X-2"]) == [] and ps.judge.fires_of("D-0001") == []
    assert not hasattr(ps, "observations") and not hasattr(ps, "ledger") and not hasattr(ps, "events")


def test_record_writes_a_pass_rows_all_or_none(db):
    ps = open_pass_stores(db, "X-1")
    with pytest.raises(ForeignRowError):
        ps.record(a_pass("X-1"), a_report("X-1"), [LensAnswer("X-1", "L-01", 0.5, "q")], [Fire("X-9", "D-1", 3)])
    run = open_run_stores(db)
    assert run.passes.passes() == [] and run.passes.eval_of("X-1") is None and run.judge.lens_answers(["X-1"]) == []
    ps.record(a_pass("X-1"), a_report("X-1"), [LensAnswer("X-1", "L-01", 0.5, "q")], [Fire("X-1", "D-1", 3)])
    assert run.passes.eval_of("X-1") == a_report("X-1") and len(run.judge.fires_of("D-1")) == 1


def test_nested_failure_rolls_back_only_its_savepoint(db):
    s = open_run_stores(db)
    with transaction(s.conn):
        s.passes.add_pass(a_pass("X-1"))
        with pytest.raises(StoreConflict):
            s.judge.add_lens_answers([LensAnswer("X-1", "L-02", 0.1, "q"), LensAnswer("X-1", "L-02", 0.2, "q")])
    assert [p.id for p in s.passes.passes()] == ["X-1"] and s.judge.lens_answers(["X-1"]) == []


def test_pass_stores_need_the_parent_database(tmp_path):
    with pytest.raises(FileNotFoundError):
        open_pass_stores(tmp_path / "absent.db", "X-1")


def test_db_path_reads_paths_db_from_the_run_spec():
    spec = load_spec(ROOT / "run.yml")
    assert proc.db_path(spec, ROOT) == ROOT / spec.paths.db


def test_connection_is_wal_with_busy_timeout(db):
    conn = connect(db)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] > 0
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


# ---- process model ------------------------------------------------------------------------------------

def _use_inherited_connection(conn, q) -> None:
    try:
        conn.execute("SELECT 1")
        q.put("used")
    except ForeignProcessConnection:
        q.put("refused")


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork to inherit a connection")
def test_a_forked_child_cannot_use_the_parent_connection(db):
    ctx = multiprocessing.get_context("fork")
    conn = connect(db)
    q = ctx.Queue()
    child = ctx.Process(target=_use_inherited_connection, args=(conn, q))
    child.start()
    child.join(30)
    assert q.get(timeout=5) == "refused"
    assert conn.execute("SELECT 1").fetchone() == (1,)


N_PROCS, PER_PROC = 16, 25


def _write_passes(db: str, worker: int, start, errors) -> None:
    start.wait()
    try:
        for i in range(PER_PROC):
            pid = f"W{worker}-{i:02d}"
            ps = open_pass_stores(Path(db), pid)
            ps.record(a_pass(pid, round_=worker, slot=None), a_report(pid), [LensAnswer(pid, "L-01", i / 7, "q", "m")],
                      [Fire(pid, f"D-{worker}", worker)])
            ps.spend.record("worker", pid, None, ok=True)
            ps.close()
    except Exception as e:  # noqa: BLE001 - the test reports every child error to the parent, then fails
        errors.put(f"{worker}: {type(e).__name__}: {e}")


def test_concurrent_pass_processes_lose_no_row_and_never_report_locked(db):
    ctx = multiprocessing.get_context("spawn")
    start, errors = ctx.Event(), ctx.Queue()
    children = [ctx.Process(target=_write_passes, args=(str(db), w, start, errors)) for w in range(N_PROCS)]
    for c in children:
        c.start()
    start.set()
    run = open_run_stores(db)
    seen = len(run.passes.passes())                 # the parent reads while the children write (WAL)
    for c in children:
        c.join(120)
        assert c.exitcode == 0
    errs = []
    while not errors.empty():
        errs.append(errors.get())
    assert errs == []
    want = N_PROCS * PER_PROC
    assert seen <= want
    passes = run.passes.passes()
    assert len(passes) == want and len({p.id for p in passes}) == want
    assert all(run.passes.eval_of(p.id) == a_report(p.id) for p in passes)
    assert len(run.judge.lens_answers([p.id for p in passes])) == want
    assert sum(len(run.judge.fires_of(f"D-{w}")) for w in range(N_PROCS)) == want
    assert run.conn.execute("SELECT COUNT(*) FROM spend").fetchone() == (want,)


def _write_then_hang(db: str, ready) -> None:
    for i in range(3):
        done = open_pass_stores(Path(db), f"F-{i}")
        done.record(a_pass(f"F-{i}", slot=None), a_report(f"F-{i}"), [LensAnswer(f"F-{i}", "L-01", 0.5, "q")])
        done.close()
    partial = open_pass_stores(Path(db), "P-partial")
    with transaction(partial.conn):
        partial.passes.add_pass(a_pass("P-partial", slot=None))
        partial.passes.add_eval(a_report("P-partial"))
        ready.set()
        signal.pause()                       # killed here, mid-transaction


def test_a_killed_writer_keeps_finished_passes_and_drops_the_partial_one(db):
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Event()
    child = ctx.Process(target=_write_then_hang, args=(str(db), ready))
    child.start()
    assert ready.wait(60)
    os.kill(child.pid, signal.SIGKILL)
    child.join(30)
    assert child.exitcode == -signal.SIGKILL
    run = open_run_stores(db)
    assert [p.id for p in run.passes.passes()] == ["F-0", "F-1", "F-2"]
    assert all(run.passes.eval_of(f"F-{i}") == a_report(f"F-{i}") for i in range(3))
    assert run.passes.eval_of("P-partial") is None
    assert run.conn.execute("SELECT COUNT(*) FROM eval_details WHERE pass_id = 'P-partial'").fetchone() == (0,)
    after = open_pass_stores(db, "P-partial")                 # the dead writer's lock is gone
    after.record(a_pass("P-partial", slot=None), a_report("P-partial"))
    assert run.passes.eval_of("P-partial") == a_report("P-partial")


def test_memory_and_sqlite_pass_stores_agree_on_reads(db):
    mem, sq = memory.MemoryPassStore(), open_run_stores(db).passes
    for store in (mem, sq):
        store.add_pass(a_pass("X-1"))
        store.add_pass(a_pass("X-2", round_=4, slot=None))
        store.add_eval(a_report("X-1"))
    assert list(mem.passes()) == list(sq.passes()) and list(mem.passes(4)) == list(sq.passes(4))
    assert mem.eval_of("X-1") == sq.eval_of("X-1") and mem.eval_of("X-2") is sq.eval_of("X-2") is None
