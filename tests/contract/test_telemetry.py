"""Telemetry log: append-only rows, span nesting and failure, scope recall, refusal of secrets and text."""

import sqlite3
import threading

import pytest

from friday.adapters.telemetry import MemoryTelemetry, SqliteTelemetry, TelemetryError, connect_telemetry
from friday.ports.telemetry import Scope


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        self.t += 1.0
        return self.t


@pytest.fixture(params=["memory", "sqlite"])
def log(request, tmp_path):
    scope = Scope("main")
    if request.param == "memory":
        return MemoryTelemetry(scope, clock=Clock())
    return SqliteTelemetry(connect_telemetry(tmp_path / "t.db", busy_timeout_ms=1000), scope, clock=Clock())


def test_span_writes_an_open_row_then_a_close_row_with_attributes_set_while_open(log):
    with log.span("pass.worker", model="m") as s:
        open_rows = log.rows(kind="span")
        assert len(open_rows) == 1 and open_rows[0].ended_at is None and open_rows[0].ok is None
        s.set(tokens_out=12)
    rows = log.rows(kind="span")
    assert len(rows) == 2 and rows[0].span_id == rows[1].span_id
    closed = log.spans()[0]
    assert closed.ok is True and closed.duration_s == 1.0 and closed.attrs == {"model": "m", "tokens_out": 12}


def test_a_live_reader_sees_an_in_flight_span_and_tails_by_seq(log):
    with log.span("pass"):
        assert log.spans()[0].ended_at is None
        seen = log.rows()[-1].seq
    tail = log.rows(after_seq=seen)
    assert [r.ended_at is not None for r in tail] == [True]


def test_nested_spans_record_their_parent_and_metrics_record_the_open_span(log):
    with log.span("pass") as outer, log.span("eval.probe") as inner:
        log.metric("eval.P", 0.5, "score")
    spans = {r.name: r for r in log.spans()}
    assert spans["eval.probe"].parent_id == outer.id and spans["pass"].parent_id is None
    assert log.rows(kind="metric")[0].parent_id == inner.id


def test_an_exception_closes_the_span_failed_and_propagates(log):
    with pytest.raises(KeyError), log.span("eval.load"):
        raise KeyError("boom")
    s = log.spans()[0]
    assert s.ok is False and s.error.startswith("KeyError") and s.ended_at is not None


def test_scope_narrows_and_rows_are_recalled_by_round_and_pass(log):
    log.scoped(round=3, pass_id="p1").metric("pass.loss", 0.4, "score")
    log.scoped(round=3).metric("round.wall", 60.0, "s")
    log.scoped(round=4, pass_id="p2").metric("pass.loss", None, "score")
    assert [r.value for r in log.rows(round=3)] == [0.4, 60.0]
    assert [r.scope.pass_id for r in log.rows(name_prefix="pass.")] == ["p1", "p2"]
    assert log.rows(pass_id="p2")[0].value is None          # unread stays None, never 0


def test_secret_and_text_attributes_are_refused(log):
    for bad in ({"api_key": "x"}, {"Authorization": "x"}, {"prompt": "do it"}, {"note": "Bearer abc123"},
                {"x": float("nan")}, {"x": object()}):
        with pytest.raises(TelemetryError):
            log.event("run.manifest", **bad)
    assert log.rows() == []


def test_a_metric_needs_a_unit_and_a_finite_number(log):
    for value, unit in ((1.0, ""), (float("inf"), "s"), (True, "count")):
        with pytest.raises(TelemetryError):
            log.metric("m", value, unit)


def test_sqlite_rows_cannot_be_updated_or_deleted(tmp_path):
    conn = connect_telemetry(tmp_path / "t.db", busy_timeout_ms=1000)
    SqliteTelemetry(conn, Scope("main")).event("run.manifest", git_sha="abc")
    for sql in ("UPDATE telemetry SET name = 'x'", "DELETE FROM telemetry"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute(sql)


def test_two_writers_on_one_database_interleave_without_loss(tmp_path):
    path = tmp_path / "t.db"
    connect_telemetry(path, busy_timeout_ms=5000)

    def write(run):
        t = SqliteTelemetry(connect_telemetry(path, busy_timeout_ms=5000), Scope(run))
        for i in range(50):
            t.metric("n", i, "count")

    threads = [threading.Thread(target=write, args=(r,)) for r in ("main", "critic")]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    reader = SqliteTelemetry(connect_telemetry(path, busy_timeout_ms=5000), Scope("main"))
    assert len(reader.rows(run="main")) == 50 and len(reader.rows(run="critic")) == 50


def test_time_table_and_span_tree_read_durations_and_failures():
    from friday.app.telemetry_report import span_tree, step_times, time_table

    log = MemoryTelemetry(Scope("main"), clock=Clock())
    p = log.scoped(round=1, pass_id="p1")
    with p.span("pass"), p.span("eval.probe"):
        pass
    with pytest.raises(RuntimeError), p.span("pass"):
        raise RuntimeError("x")
    t = step_times(log.spans(round=1))
    assert t["pass"]["count"] == 2 and t["pass"]["failed"] == 1 and t["eval.probe"]["median_s"] == 1.0
    tree = span_tree(log, run="main", round_=1)
    assert "  eval.probe  1.00s" in tree and "[failed: RuntimeError: x]" in tree
    assert time_table(log, run="main", round_=1).splitlines()[0].startswith("step")
