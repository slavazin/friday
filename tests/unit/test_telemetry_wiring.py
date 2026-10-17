"""Telemetry wiring (docs/telemetry.md): spans close failed and re-raise, no decision path reads the log, pass
processes and the round close share one SQLite log, and the new knobs and pass fields round-trip."""

from __future__ import annotations

import ast
import dataclasses
import json
import multiprocessing
import statistics
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from friday.adapters.crusoe import CrusoeClient, TransportFailure
from friday.adapters.memory import MemoryGoalSource, MemorySpendLog, MemoryTagVocabulary
from friday.adapters.sqlite.run_stores import SqlitePassStore
from friday.adapters.sqlite.stores import connect
from friday.adapters.telemetry import MemoryTelemetry, SqliteTelemetry, connect_telemetry, open_run_telemetry
from friday.app import pipeline
from friday.app.goals import tag_round
from friday.app.notes import approx_tokens, notes_text
from friday.app.readout import readings, run_readout
from friday.config import load_spec
from friday.domain.types import Arm, Composition, DecisionStatus, DeltaKind, Pass, Regime
from friday.ports.models import Message
from friday.ports.telemetry import Scope
from tests.contract import test_probe_eval as probe_eval
from tests.contract import test_round_close as decision_close
from tests.contract.test_round_close_order import World
from tests.unit import test_pipeline as tp
from tests.unit import test_readout as tr
from tests.unit.builders import dec

ROOT = Path(__file__).resolve().parents[2]
SPEC = load_spec(ROOT / "run.yml")


class ReadRaisingTelemetry(MemoryTelemetry):
    """A log that writes like MemoryTelemetry and raises on every read: a decision that read it would fail."""

    def rows(self, **kw):
        raise AssertionError("a decision path read telemetry")

    def spans(self, **kw):
        raise AssertionError("a decision path read telemetry")


# --- no decision path reads telemetry ---------------------------------------------------------------------


def test_algorithms_stats_and_domain_never_import_telemetry():
    hits = []
    for layer in ("algorithms", "stats", "domain"):
        for f in sorted((ROOT / "friday" / layer).rglob("*.py")):
            for node in ast.walk(ast.parse(f.read_text())):
                names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                         else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
                hits += [f"{f.name}: {n}" for n in names if "telemetry" in n]
    assert hits == []


def _gate_and_detector(log):
    c = decision_close.close()
    c.telemetry = log
    c.journal.draft(dec("D-0001", DecisionStatus.CANDIDATE))
    c.journal.draft(dec("D-0002", DecisionStatus.CANDIDATE, fires=("timer",)))
    decision_close.add(c, "D-0001", DeltaKind.EXPOSURE, [0.3, 0.25, 0.35, 0.3, 0.3, 0.3, 0.3, 0.35])
    decision_close.add(c, "D-0002", DeltaKind.EXPOSURE, [0.01, -0.02])
    gated = c.close_gate(8)
    detected = c.close_detector(8)
    return [(d.id, d.status) for d in gated], [(d.id, d.status) for d in detected], c.events.flips()


def test_gate_detector_and_readout_decide_the_same_with_a_log_that_raises_on_every_read():
    assert _gate_and_detector(MemoryTelemetry(Scope("main"))) == _gate_and_detector(
        ReadRaisingTelemetry(Scope("main")))
    s = tr.make_stores()
    tr.plant(s, [0.1, 0.2, 0.05, 0.3, 0.15, 0.12])
    quiet = run_readout(s, SPEC, telemetry=ReadRaisingTelemetry(Scope("main")))
    assert quiet == readings(s, SPEC) == run_readout(s, SPEC, telemetry=MemoryTelemetry(Scope("main")))


def test_readout_runner_writes_one_reading_event_per_preregistered_reading():
    s = tr.make_stores()
    tr.plant(s, [0.1, 0.2, 0.05, 0.3, 0.15, 0.12])
    log = MemoryTelemetry(Scope("main"))
    r = run_readout(s, SPEC, telemetry=log)
    events = [x.attrs for x in log.rows(name_prefix="readout.reading")]
    assert [e["reading"] for e in events] == ["R1", "R2", "R3", "R4"]
    assert events[0]["p"] == r.r1.p and events[0]["estimate"] == r.r1.mean and events[0]["verdict"] == r.r1.verdict
    span = log.spans(name_prefix="readout")[0]
    assert span.attrs["reps"] == SPEC.readout.reps and span.attrs["passes"] > 0


# --- an exception closes its span failed and propagates ---------------------------------------------------


def test_a_failed_model_call_closes_its_span_failed_with_its_attempts_and_raises(monkeypatch):
    monkeypatch.setenv(SPEC.models.endpoint.api_key_env, "test-key")
    log = MemoryTelemetry(Scope("main"))

    def handler(req):
        return httpx.Response(503)

    models = dataclasses.replace(SPEC.models, price_overrides={"m": {"prompt": 0.1, "completion": 0.2}})
    http = httpx.Client(base_url="https://x/v1", transport=httpx.MockTransport(handler))
    c = CrusoeClient(models, SPEC.transport, MemorySpendLog(), http=http, sleep=lambda s: None, telemetry=log)
    c.bind({"worker": "m"})
    with pytest.raises(TransportFailure):
        c.chat("worker", [Message("user", "hi")], tag="t1")
    (span,) = log.spans(name_prefix="model.call")
    assert span.ok is False and "TransportFailure" in span.error
    assert span.attrs["attempts"] == SPEC.transport.max_tries
    assert span.attrs["retry_statuses"] == [503] * SPEC.transport.max_tries
    assert span.attrs["backoff_s"] >= 0 and span.attrs["requested"] == "m" and span.attrs["structured"] is False


def test_a_retried_model_call_records_the_backoff_it_slept(monkeypatch):
    monkeypatch.setenv(SPEC.models.endpoint.api_key_env, "test-key")
    log = MemoryTelemetry(Scope("main"))
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "2"})
        return httpx.Response(200, json={"id": "r", "model": "m-served", "choices": [{"message": {"content": "ok"}}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

    models = dataclasses.replace(SPEC.models, price_overrides={"m": {"prompt": 0.1, "completion": 0.2}})
    http = httpx.Client(base_url="https://x/v1", transport=httpx.MockTransport(handler))
    c = CrusoeClient(models, SPEC.transport, MemorySpendLog(), http=http, sleep=lambda s: None, telemetry=log)
    c.bind({"worker": "m"})
    c.chat("worker", [Message("user", "hi")], tag="t1")
    (span,) = log.spans(name_prefix="model.call")
    assert span.ok is True
    assert (span.attrs["attempts"], span.attrs["retry_statuses"], span.attrs["backoff_s"]) == (2, [429], 2.0)
    assert (span.attrs["model"], span.attrs["tokens_in"], span.attrs["tokens_out"]) == ("m-served", 10, 5)


def test_an_eval_step_that_raises_closes_its_span_and_the_eval_span_failed(tmp_path):
    class Broken(probe_eval.Quality):
        def eslint_errors(self, html):
            raise RuntimeError("eslint is broken")

    log = MemoryTelemetry(Scope("main"))
    ev = probe_eval.make_eval(probe_eval.DomBrowser(probe_eval.counter_dom))
    ev.quality, ev.telemetry = Broken(), log
    path = tmp_path / "a.html"
    path.write_text("<!doctype html><html><body><button id=start>go</button></body></html>")
    p = Pass("x1", 1, "cat", Arm.JOURNAL, "w", Composition("cat", ()), Regime.R1, artifact_path=str(path))
    with pytest.raises(RuntimeError, match="eslint is broken"):
        ev.run(p, tp.request("cat"), Regime.R1, "text")
    by = {s.name: s for s in log.spans()}
    assert by["eval.quality"].ok is False and "eslint is broken" in by["eval.quality"].error
    assert by["eval"].ok is False and by["eval.viewport"].ok is True


def test_a_close_phase_that_raises_closes_its_span_and_the_close_failed_and_propagates(tmp_path):
    w = World(tmp_path)
    log = MemoryTelemetry(Scope("main"))
    w.close.telemetry = log
    w.add_passes(1, 1)

    def broken(lens_id, pools, new):
        raise RuntimeError("pooling broke")

    w.pooling.assign = broken
    from friday.roles.judge import DraftObservation
    with pytest.raises(RuntimeError, match="pooling broke"):
        w.close.close(1, {"r1-journal-0": [DraftObservation("free", "a shape", "a direction", "q")]})
    by = {s.name: s for s in log.spans()}
    assert by["close.pooling"].ok is False and by["close"].ok is False
    assert by["close.observations"].ok is True and "close.nomination" not in by
    assert log.scope == Scope("main")


def test_a_pass_that_crashes_closes_its_pass_span_failed_and_the_parent_writes_crashed(tmp_path, monkeypatch):
    tp.scripted_judge(monkeypatch, tp.JudgeLog([], []))
    reqs, j, ledger, store, plan, tasks = tp.two_request_round(tmp_path)
    log = MemoryTelemetry(Scope("main"))
    e = tp.env(tmp_path, tp.ScriptedClient(tp.replies()))
    e.telemetry = log

    def evaluate(pass_, request_, regime, text):
        if request_.id == "Q-0001":
            raise RuntimeError("a programming error in the eval")
        return tp.scripted_eval(pass_, request_, regime, text)

    e.evaluate = evaluate
    result = pipeline.run_round(1, store.plans(1), tasks, tp.factory(e), parallel=1, reruns=1, in_process=True,
                                telemetry=log)
    crashed = sorted(result.crashed)
    outcomes = {r.scope.pass_id: r.attrs for r in log.rows(name_prefix="pass.outcome")}
    assert all(outcomes[p]["outcome"] == "crashed" and "programming error" in outcomes[p]["error"] for p in crashed)
    assert {outcomes[o.pass_.id]["outcome"] for o in result.outcomes} == {"scored"}
    spans = {s.scope.pass_id: s for s in log.spans(name_prefix="pass") if s.name == "pass"}
    assert all(spans[p].ok is False for p in crashed)
    rows = {p.id: p for p in e.passes.passes(1)}
    assert all("programming error" in rows[p].failed and rows[p].composed_tokens is not None for p in crashed)
    assert all(rows[o.pass_.id].failed == "" for o in result.outcomes)


def test_a_terminated_pass_is_recorded_timed_out_by_the_parent():
    log = MemoryTelemetry(Scope("main"))
    pipeline._record_outcomes(log, 2, {"r02-a": "timed out after 8 s (run.pass_timeout_s); terminated",
                                       "r02-b": "Traceback ...\nRuntimeError: boom\nexit code 1"})
    got = {r.scope.pass_id: (r.attrs["outcome"], r.attrs["error"], r.scope.round)
           for r in log.rows(name_prefix="pass.outcome")}
    assert got == {"r02-a": ("timed_out", "timed out after 8 s (run.pass_timeout_s); terminated", 2),
                   "r02-b": ("crashed", "exit code 1", 2)}


# --- one SQLite log, many processes -----------------------------------------------------------------------


def _writer(db: str, pass_id: str, n: int, busy_ms: int) -> None:
    log = open_run_telemetry(db, "main", busy_timeout_ms=busy_ms)
    with pipeline.narrowed(log, round_=1, pass_id=pass_id) as t:
        for i in range(n):
            with t.span("pass.worker", i=i) as span:
                t.metric("pass.loss", 0.5, "score")
                span.set(model="m")
            t.event("pass.outcome", outcome="scored", error="")


def test_two_pass_processes_and_the_round_close_write_one_database_at_once(tmp_path):
    db = tmp_path / "friday.db"
    connect(db).close()                                   # the stores' schema, as the parent opens it
    busy = SPEC.telemetry.busy_timeout_ms
    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_writer, args=(str(db), f"p{i}", 150, busy)) for i in range(2)]
    for p in procs:
        p.start()
    parent = open_run_telemetry(db, "main", busy_timeout_ms=busy)
    with pipeline.narrowed(parent, round_=1) as t:
        for i in range(150):
            with t.span("close.gate", i=i):
                t.metric("close.wealth", 1.0, "score", decision_id="D-0001", n=i, mean_d=None)
    for p in procs:
        p.join(60)
    assert [p.exitcode for p in procs] == [0, 0]
    reader = SqliteTelemetry(connect_telemetry(db, busy_timeout_ms=busy), Scope("main"))
    for pid in ("p0", "p1"):
        rows = reader.rows(pass_id=pid)
        assert sum(r.name == "pass.worker" and r.ended_at is not None for r in rows) == 150
        assert sum(r.name == "pass.outcome" for r in rows) == 150
    assert len(reader.rows(name_prefix="close.wealth", round=1)) == 150
    assert parent.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


# --- knobs and pass fields --------------------------------------------------------------------------------


def test_pass_timeout_and_chars_per_token_are_read_from_run_yml(tmp_path):
    assert pipeline.pass_timeout(SPEC) == SPEC.run.pass_timeout_s == 600
    assert pipeline.PassSettings.from_spec(SPEC).chars_per_token == SPEC.notes.chars_per_token == 4
    assert approx_tokens("x" * 9, 4) == 3 and approx_tokens("x" * 9, 3) == 3 and approx_tokens("x" * 9, 9) == 1
    path = tmp_path / "notes.md"
    path.write_text("- " + "a" * 50 + "\n")   # 53 characters: 14 tokens at 4, 7 at 8
    assert notes_text(path, 10, 4) is None and notes_text(path, 10, 8) is not None


def test_pass_failed_and_composed_tokens_round_trip_through_the_sqlite_store(tmp_path):
    store = SqlitePassStore(connect(tmp_path / "friday.db"))
    a = Pass("r01-a", 1, "cat", Arm.JOURNAL, "w", Composition("cat", ("D-0001",)), Regime.R1, pair_slot=0,
             failed="RuntimeError: boom", composed_tokens=37)
    b = Pass("r01-b", 1, "cat", Arm.NOTES, "w", Composition("cat", (), notes=True), Regime.R1)
    store.add_pass(a)
    store.add_pass(b)
    assert {p.id: p for p in store.passes(1)} == {"r01-a": a, "r01-b": b}
    assert store.passes(1)[1].composed_tokens is None           # unread stays None, never 0


def test_a_pass_records_the_tokens_composed_into_its_prompt(tmp_path, monkeypatch):
    tp.scripted_judge(monkeypatch, tp.JudgeLog([], []))
    reqs, j, ledger, store, plan, tasks = tp.two_request_round(tmp_path)
    e = tp.env(tmp_path, tp.ScriptedClient(tp.replies()))
    e.telemetry = MemoryTelemetry(Scope("main"))
    pipeline.run_round(1, store.plans(1), tasks, tp.factory(e), parallel=1, reruns=1, in_process=True,
                       telemetry=e.telemetry)
    by_task = {t.pass_id: t for t in tasks}
    for p in e.passes.passes(1):
        t = by_task[p.id]
        expected = sum(len(d.payload) for d in t.decisions)
        if t.decisions:
            assert p.composed_tokens >= expected // SPEC.notes.chars_per_token
        else:
            assert p.composed_tokens == 0
    span = next(s for s in e.telemetry.spans(name_prefix="pass.compose"))
    assert len(span.attrs["prompt_sha"]) == 64 and span.attrs["prompt_tokens_est"] > 0


def test_tagging_a_round_is_one_goals_tag_span():
    from tests.contract import test_tagger as tt
    log = MemoryTelemetry(Scope("main"))
    source, vocab = MemoryGoalSource([tt.SNAKE, tt.TIMER]), MemoryTagVocabulary()
    for g in tt.VOCAB:
        vocab.add(g)
    t = tt.tagger([tt.reply({"G-001": ["keyboard", "timer"], "G-002": ["timer", "keyboard"]})])
    tags, new = tag_round(t, source, vocab, 1, SPEC.stream.max_new_tags, telemetry=log)
    (span,) = log.spans(name_prefix="goals.tag")
    assert span.ok and span.attrs["requests"] == len(tags) == 2 and span.attrs["new_tags"] == len(new) == 0


# --- overhead ---------------------------------------------------------------------------------------------


class NullTelemetry:
    """Writes nothing: the baseline the overhead is measured against (never used in the run)."""

    scope = Scope("main")

    def scoped(self, **kw):
        return self

    @contextmanager
    def span(self, name, **attrs):
        class H:
            id = ""

            def set(self, **a):
                pass
        yield H()

    def metric(self, *a, **kw):
        pass

    def event(self, *a, **kw):
        pass


def _time_passes(tmp_path, monkeypatch, log, n=8) -> float:
    tp.scripted_judge(monkeypatch, tp.JudgeLog([], []))
    reqs, j, ledger, store, plan, tasks = tp.two_request_round(tmp_path)
    times = []
    for k in range(n):
        e = tp.env(tmp_path / f"{k}", tp.ScriptedClient(tp.replies()))
        e.telemetry = log
        t0 = time.perf_counter()
        pipeline.run_pass(tasks[0], e)
        times.append(time.perf_counter() - t0)
    return statistics.median(times)


def test_telemetry_adds_under_five_percent_or_50_ms_to_a_scripted_pass(tmp_path, monkeypatch):
    base = _time_passes(tmp_path / "null", monkeypatch, NullTelemetry())
    db = tmp_path / "friday.db"
    with_log = _time_passes(tmp_path / "log", monkeypatch,
                            open_run_telemetry(db, "main", busy_timeout_ms=SPEC.telemetry.busy_timeout_ms))
    overhead = with_log - base
    print(json.dumps({"pass_s_without": base, "pass_s_with_sqlite": with_log, "overhead_s": overhead}))
    assert overhead < max(0.05 * base, 0.050)
