"""The pass pipeline: round planning, one pass, the round (SPEC §§ 5, 6, 8, 11)."""

from __future__ import annotations

import dataclasses
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

from friday.adapters.crusoe import TransportFailure
from friday.adapters.memory import (
    MemoryExposureStore,
    MemoryJournal,
    MemoryJudgeStore,
    MemoryLedger,
    MemoryPassStore,
)
from friday.adapters.telemetry import MemoryTelemetry
from friday.algorithms.language import blinding_hits
from friday.app import pipeline
from friday.app.pipeline import (
    PassEnv,
    PassSettings,
    PassTask,
    RoundCrashed,
    eval_report_text,
    neutral_location,
    plan_round,
    rerun_task,
    round_limits,
    round_tasks,
    round_tasks_for,
    run_pass,
    run_round,
)
from friday.config import load_spec
from friday.domain.types import (
    Arm,
    Check,
    CheckResult,
    Composition,
    Decision,
    DecisionStatus,
    DeltaKind,
    Disposition,
    EvalReport,
    Hook,
    LedgerDelta,
    LensAnswer,
    LensStatus,
    PairKind,
    Regime,
    Request,
)
from friday.ports.telemetry import Scope
from friday.roles.judge import DraftObservation, Judge
from friday.roles.prompts import PromptBook
from friday.roles.worker import Worker
from friday.stats.rng import coin
from tests.fakes import FakeDom, FakePage, ScriptedClient
from tests.unit.builders import lens

ROOT = Path(__file__).resolve().parents[2]
BOOK = PromptBook(ROOT / "prompts")
SPEC = load_spec(ROOT / "run.yml")
HELP = "Bind the start control to a click listener that writes HELPED into the status line."
HURT = "Draw everything on one canvas with no element ids."
TEL = MemoryTelemetry(Scope("main"))


def spec_with(**sections):
    return dataclasses.replace(SPEC, **{
        name: dataclasses.replace(getattr(SPEC, name), **values) for name, values in sections.items()})


def request(id_: str, *tags: str, round_: int = 1, held_out: bool = False, text: str | None = None) -> Request:
    checks = {Regime.R1: (Check("c1", (), {"exists": "#start"}),), Regime.R2: (Check("c2", (), {"exists": "#start"}),)}
    return Request(id_, text or f"Build a small app {id_} with a #start button and a #status line.",
                   frozenset(tags), {"#start": "starts", "#status": "shows state"}, checks,
                   held_out=held_out, round=None if held_out else round_)


def decision(id_: str, status: DecisionStatus, fires: str, payload: str = HELP) -> Decision:
    return Decision(id_, status, "P-0001", Hook(frozenset({fires})), payload)


def journal(*decisions: Decision) -> MemoryJournal:
    return MemoryJournal(_decisions={d.id: d for d in decisions})


# --- worker, browser, eval and judge stand-ins -----------------------------------------------------------


def html_for(messages) -> str:
    """A worker reply: HELPED when the help payload is composed; the contract ids unless told to drop them."""
    user = messages[1].content
    body = "HELPED" if HELP in user else "plain"
    ids = "" if "DROP-IDS" in user else '<button id="start">go</button><p id="status">x</p>'
    if "HARDCRASH" in user:
        os._exit(3)
    if "HANG" in user:
        time.sleep(120)
    if "SOFTCRASH" in user:
        raise RuntimeError("a programming error inside the pass")
    return f"```html\n<!doctype html><html><body>{ids}<p>{body}</p></body></html>\n```"


def replies(n: int = 200, fn=html_for):
    return [fn] * n


class FileBrowser:
    """A Browser over FakePage: the page holds every id the artifact file carries."""

    def __init__(self, errors: tuple[str, ...] = ()) -> None:
        self.errors = errors
        self.loads: list[tuple[str, tuple[int, int]]] = []

    @contextmanager
    def page(self, artifact_path, viewport):
        self.loads.append((artifact_path, viewport))
        html = Path(artifact_path).read_text()
        ids = re.findall(r'id="([^"]+)"', html)
        errors = list(self.errors) if "ERRORS" in html or self.errors else []
        yield FakePage(FakeDom(texts={f"#{i}": "" for i in ids}, errors=errors))


def page_ids_missing(page, contract):
    return [c for c in contract if not page.exists(c)]


def scripted_eval(pass_, request_, regime, text) -> EvalReport:
    """Loss 0.2 for an artifact showing HELPED, 0.6 otherwise; Q 1 for every artifact."""
    html = Path(pass_.artifact_path).read_text()
    loss = 0.2 if "HELPED" in html else 0.6
    return EvalReport(pass_.id, regime, (CheckResult("c1", "HELPED" in html, "" if "HELPED" in html else "step 1"),),
                      (), 1.0 - loss, 1.0 - loss, (3, 3, 3), 1, loss)


@dataclass
class JudgeLog:
    lens: list[dict]
    report: list[dict]


def scripted_judge(monkeypatch, log: JudgeLog, report_result="ok"):
    def lens_call(self, pass_id, request_text, composed, source, lenses):
        log.lens.append({"pass_id": pass_id, "request_text": request_text, "composed": dict(composed),
                         "source": source, "lenses": [x.id for x in lenses]})
        return [LensAnswer(pass_id, x.id, 0.1, "") for x in lenses]

    def report_call(self, pass_id, request_text, composed, source, answers, eval_report_text, lens_ids):
        log.report.append({"pass_id": pass_id, "eval": eval_report_text, "answers": list(answers)})
        if report_result is None:
            return None
        obs = [DraftObservation("free", f"shape of {pass_id}", "do the direction", "<p>")]
        return obs, {d: (Disposition.APPLIED, "") for d in composed}

    monkeypatch.setattr(Judge, "lens_call", lens_call)
    monkeypatch.setattr(Judge, "report_call", report_call)


def env(tmp_path, client, *, browser=None, evaluate=scripted_eval, spec=SPEC) -> PassEnv:
    return PassEnv(
        worker=Worker(client, BOOK), book=BOOK, browser=browser or FileBrowser(), evaluate=evaluate,
        judge=Judge(client, BOOK, spec.lens.flag_p, spec.judge.max_observations, spec.judge.quote_reasks,
                    TEL),
        passes=MemoryPassStore(), judge_store=MemoryJudgeStore(), artifacts=tmp_path / "artifacts",
        settings=PassSettings.from_spec(spec), transport_errors=(TransportFailure,), missing_ids=page_ids_missing,
        telemetry=TEL,
    )


def factory(e: PassEnv):
    @contextmanager
    def open_env():
        yield e
    return open_env


def two_request_round(tmp_path, spec=SPEC):
    """Round 1: Q-0001 (canvas) in the reach of candidate D-0001; Q-0002 (timer) under admitted D-0002."""
    reqs = [request("Q-0001", "canvas"), request("Q-0002", "timer")]
    j = journal(decision("D-0001", DecisionStatus.CANDIDATE, "canvas"),
                decision("D-0002", DecisionStatus.ADMITTED, "timer", payload="Show the state in #status."))
    ledger, store = MemoryLedger(), MemoryExposureStore()
    plan = plan_round(1, reqs, j, ledger, spec.run.seed, spec, store)
    lenses = [lens("L-01"), lens("L-02", LensStatus.HIDDEN), lens("L-03", LensStatus.RETIRED)]
    tasks = round_tasks_for(plan, reqs, spec, {d.id: d for d in j.decisions()}, lenses, tmp_path / "notes.md")
    return reqs, j, ledger, store, plan, tasks


# --- the round end to end --------------------------------------------------------------------------------


def test_a_round_of_two_requests_leaves_ledger_rows(tmp_path, monkeypatch):
    log = JudgeLog([], [])
    scripted_judge(monkeypatch, log)
    spec = spec_with(detect={"ablate_p": 1.0})
    reqs, j, ledger, store, plan, tasks = two_request_round(tmp_path, spec)
    e = env(tmp_path, ScriptedClient(replies()), spec=spec)
    parallel, reruns = round_limits(spec)
    result = run_round(1, store.plans(1), tasks, factory(e), parallel=parallel, reruns=reruns, in_process=True,
                       telemetry=TEL)

    assert result.crashed == {} and result.transport_failed == ()
    assert len(e.passes.passes(1)) == 6 and all(e.passes.eval_of(p.id) for p in e.passes.passes(1))
    assert ledger.deltas("D-0001") == []                    # the pipeline writes no Ledger row
    for delta in result.deltas:                             # the round close's append
        ledger.append(delta)
    exposure = ledger.deltas("D-0001", DeltaKind.EXPOSURE)
    assert [(r.category, r.d, r.dQ) for r in exposure] == [("Q-0001", pytest.approx(0.4), 0.0)]
    ablation = ledger.deltas("D-0002", DeltaKind.ABLATION)
    assert [(r.category, r.d) for r in ablation] == [("Q-0002", 0.0)]
    assert {o.pass_id for o in result.observations} == {t.pass_id for t in tasks if t.arm is Arm.JOURNAL}
    assert {f.pass_id for f in e.judge_store._fires} <= {t.pass_id for t in tasks if t.arm is Arm.JOURNAL}
    assert all(f.round == 1 for f in e.judge_store._fires)
    assert (tmp_path / "artifacts" / "r01-Q-0001-journal-0" / "index.html").exists()
    assert (tmp_path / "artifacts" / "r01-Q-0001-journal-0" / "eval.json").exists()


def test_plans_are_persisted_before_any_pass_runs(tmp_path):
    _, _, _, store, plan, tasks = two_request_round(tmp_path)
    assert list(store.plans(1)) == list(plan.plans)
    assert [t.pass_id for t in tasks] == [
        "r01-Q-0001-journal-0", "r01-Q-0001-journal-1", "r01-Q-0001-notes-0",
        "r01-Q-0002-journal-0", "r01-Q-0002-journal-1", "r01-Q-0002-notes-0",
    ]


def test_exposure_coin_is_the_counter_rng_keyed_by_round_and_request(tmp_path):
    _, _, _, _, plan, _ = two_request_round(tmp_path)
    exp = next(p for p in plan.plans if p.kind is PairKind.EXPOSURE)
    assert exp.treated_slot == coin(SPEC.run.seed, "exposure", 1, exp.category)
    _, _, _, _, again, _ = two_request_round(tmp_path / "again")
    assert again.plans == plan.plans and again.pairs == plan.pairs


# --- adversarial: what each pair may and may not differ by --------------------------------------------------


def test_the_two_compositions_of_a_pair_differ_only_by_the_exposure(tmp_path):
    spec = spec_with(detect={"ablate_p": 1.0})
    _, _, _, _, plan, tasks = two_request_round(tmp_path, spec)
    by = {p.category: p for p in plan.plans}
    for req_id, (a, b) in plan.pairs.items():
        diff = set(a.decision_ids) ^ set(b.decision_ids)
        assert diff == {by[req_id].decision_id}
        assert {a.exposed, b.exposed} - {None} == ({by[req_id].decision_id} if by[req_id].kind is PairKind.EXPOSURE
                                                    else set())
        assert {a.ablated, b.ablated} - {None} == ({by[req_id].decision_id} if by[req_id].kind is PairKind.ABLATION
                                                    else set())
    pair = [t for t in tasks if t.request.id == "Q-0001" and t.arm is Arm.JOURNAL]
    assert pair[0].request_text == pair[1].request_text and pair[0].lenses == pair[1].lenses


def test_a_pairs_worker_prompts_differ_only_by_the_exposed_payload_line(tmp_path, monkeypatch):
    scripted_judge(monkeypatch, JudgeLog([], []))
    reqs = [request("Q-0001", "canvas")]
    j = journal(decision("D-0001", DecisionStatus.CANDIDATE, "canvas"),
                decision("D-0006", DecisionStatus.ADMITTED, "canvas", payload="Show the state in #status."))
    plan = plan_round(1, reqs, j, MemoryLedger(), SPEC.run.seed, SPEC, MemoryExposureStore())
    tasks = round_tasks(plan, reqs, Regime.R1, SPEC.drift.constraint, {d.id: d for d in j.decisions()}, [], None)
    client = ScriptedClient(replies())
    e = env(tmp_path, client)
    for t in tasks:
        if t.arm is Arm.JOURNAL:
            run_pass(t, e)
    prompts = [c[1][1].content.splitlines() for c in client.calls if c[0] == "worker"]
    assert len(prompts) == 2
    with_, without = (prompts[0], prompts[1]) if HELP in "\n".join(prompts[0]) else (prompts[1], prompts[0])
    assert [line for line in with_ if line not in without] == [f"- {HELP}"]
    assert [line for line in without if line not in with_] == []


def test_a_candidates_pass_renders_the_same_prompt_bytes_as_if_admitted(tmp_path, monkeypatch):
    scripted_judge(monkeypatch, JudgeLog([], []))
    req = request("Q-0001", "canvas")
    cand = decision("D-0001", DecisionStatus.CANDIDATE, "canvas")
    adm = dataclasses.replace(cand, status=DecisionStatus.ADMITTED)
    sent = []
    for i, d in enumerate((cand, adm)):
        client = ScriptedClient(replies())
        comp = Composition(req.id, (d.id,), exposed=d.id if d is cand else None)
        task = PassTask(f"p{i}", 1, Regime.R1, req, req.text, Arm.JOURNAL, 0, comp, (d,), (lens("L-01"),))
        run_pass(task, env(tmp_path, client))
        sent.append([(m.role, m.content) for m in client.calls[0][1]])
    assert sent[0] == sent[1]
    assert "D-0001" not in sent[0][1][1] and "candidate" not in sent[0][1][1].lower()


def test_worker_facing_template_text_names_no_blinded_word(tmp_path, monkeypatch):
    scripted_judge(monkeypatch, JudgeLog([], []))
    _, _, _, _, _, tasks = two_request_round(tmp_path)
    client = ScriptedClient(replies())
    e = env(tmp_path, client, browser=FileBrowser(errors=("TypeError: x is undefined",)))
    for t in tasks:
        run_pass(t, e)
    words = (*BOOK.blinding["worker"], *SPEC.stream.families)
    for _, messages, _ in client.calls:
        for m in messages:
            text = m.content
            for t in tasks:                              # request text and payloads are not template text
                text = text.replace(t.request_text, "")
            text = text.replace(HELP, "").replace("Show the state in #status.", "")
            text = re.sub(r"```html.*?```", "", text, flags=re.DOTALL)
            assert blinding_hits(text, words) == [], text


def test_a_notes_arm_pass_makes_no_judge_call(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("judge called on a notes-arm pass")

    monkeypatch.setattr(Judge, "lens_call", boom)
    monkeypatch.setattr(Judge, "report_call", boom)
    (tmp_path / "notes.md").write_text("- the start button did nothing Bind the click on #start.\n")
    _, _, _, _, plan, tasks = two_request_round(tmp_path)
    client = ScriptedClient(replies())
    e = env(tmp_path, client)
    notes_tasks = [t for t in tasks if t.arm is Arm.NOTES]
    outs = [run_pass(t, e) for t in notes_tasks]
    assert all(o.report is not None and o.observations == () for o in outs)
    assert {c[0] for c in client.calls} == {"worker"}
    assert e.judge_store._fires == [] and e.judge_store._answers == []
    assert all("Team notes:" in c[1][1].content and "Bind the click on #start." in c[1][1].content
               for c in client.calls)
    assert all(HELP not in c[1][1].content for c in client.calls)   # the notes arm composes no decision


def test_a_transport_failed_slot_yields_no_delta(tmp_path, monkeypatch):
    scripted_judge(monkeypatch, JudgeLog([], []))
    _, _, _, store, _, tasks = two_request_round(tmp_path)

    def flaky(messages):
        if HELP in messages[1].content:
            raise TransportFailure("5 tries: 503")
        return html_for(messages)

    e = env(tmp_path, ScriptedClient(replies(fn=flaky)))
    result = run_round(1, store.plans(1), tasks, factory(e), parallel=1, reruns=1, in_process=True, telemetry=TEL)
    failed = [p for p in e.passes.passes(1) if p.transport_failed]
    assert sorted(p.id for p in failed) == sorted([f"{failed[0].id.removesuffix('-rr')}",
                                                   f"{failed[0].id.removesuffix('-rr')}-rr"])
    assert all(e.passes.eval_of(p.id) is None for p in failed)
    assert result.transport_failed == (f"{failed[0].id.removesuffix('-rr')}-rr",)
    assert [d for d in result.deltas if d.category == "Q-0001"] == []


def test_a_rerun_reuses_the_plan_and_composition_and_gives_the_delta(tmp_path, monkeypatch):
    scripted_judge(monkeypatch, JudgeLog([], []))
    _, _, _, store, _, tasks = two_request_round(tmp_path)
    seen = []

    def once_flaky(messages):
        if HELP in messages[1].content:
            seen.append(messages[1].content)
            if len(seen) == 1:
                raise TransportFailure("5 tries: 429")
        return html_for(messages)

    e = env(tmp_path, ScriptedClient(replies(fn=once_flaky)))
    result = run_round(1, store.plans(1), tasks, factory(e), parallel=1, reruns=1, in_process=True, telemetry=TEL)
    assert len(seen) == 2 and seen[0] == seen[1]
    by_id = {p.id: p for p in e.passes.passes(1)}
    rr = next(p for p in by_id.values() if p.id.endswith("-rr"))
    orig = by_id[rr.id.removesuffix("-rr")]
    assert orig.transport_failed and not rr.transport_failed
    assert rr.composition == orig.composition and rr.pair_slot == orig.pair_slot
    assert [d.d for d in result.deltas if d.category == "Q-0001"] == [pytest.approx(0.4)]


def test_rerun_task_changes_only_the_id():
    req = request("Q-0001", "canvas")
    task = PassTask("r01-Q-0001-journal-0", 1, Regime.R1, req, req.text, Arm.JOURNAL, 0, Composition(req.id, ()), ())
    rr = rerun_task(task)
    assert rr.pass_id == "r01-Q-0001-journal-0-rr" and rr.rerun_of == task.pass_id
    assert dataclasses.replace(rr, pass_id=task.pass_id, rerun_of=None) == task


def test_r2_request_text_carries_the_constraint_exactly_once(tmp_path, monkeypatch):
    scripted_judge(monkeypatch, JudgeLog([], []))
    d = SPEC.drift.round
    reqs = [request("Q-0101", "canvas", round_=d)]
    plan = plan_round(d, reqs, journal(), MemoryLedger(), SPEC.run.seed, SPEC, MemoryExposureStore())
    tasks = round_tasks_for(plan, reqs, SPEC, {}, [lens("L-01")], tmp_path / "notes.md")
    assert all(t.regime is Regime.R2 and t.request_text.count(SPEC.drift.constraint) == 1 for t in tasks)
    assert SPEC.drift.constraint not in reqs[0].text
    client = ScriptedClient(replies())
    run_pass(tasks[0], env(tmp_path, client))
    assert client.calls[0][1][1].content.count(SPEC.drift.constraint) == 1
    early = plan_round(d - 1, [request("Q-0100", "canvas", round_=d - 1)], journal(), MemoryLedger(),
                       SPEC.run.seed, SPEC, MemoryExposureStore())
    r1 = round_tasks_for(early, [request("Q-0100", "canvas", round_=d - 1)], SPEC, {}, [], tmp_path / "notes.md")
    assert all(t.regime is Regime.R1 and SPEC.drift.constraint not in t.request_text for t in r1)


def test_held_out_requests_are_never_planned():
    store = MemoryExposureStore()
    with pytest.raises(ValueError, match="held out"):
        plan_round(1, [request("Q-0001", "canvas"), request("V-0001", "canvas", held_out=True)], journal(),
                   MemoryLedger(), SPEC.run.seed, SPEC, store)
    assert store.plans(1) == []
    with pytest.raises(ValueError, match="belongs to round 2"):
        plan_round(1, [request("Q-0002", "canvas", round_=2)], journal(), MemoryLedger(), SPEC.run.seed, SPEC, store)


def test_cap_rank_is_ledger_wealth_with_ties_broken_by_id():
    spec = spec_with(composition={"cap": 1})
    reqs = [request("Q-0001", "timer")]
    j = journal(decision("D-0003", DecisionStatus.ADMITTED, "timer"),
                decision("D-0004", DecisionStatus.ADMITTED, "timer"),
                decision("D-0005", DecisionStatus.ADMITTED, "timer"))
    ledger = MemoryLedger()
    plan = plan_round(1, reqs, j, ledger, spec.run.seed, spec, MemoryExposureStore())
    assert set(plan.pairs["Q-0001"][0].decision_ids) | set(plan.pairs["Q-0001"][1].decision_ids) == {"D-0003"}
    for _ in range(3):
        ledger.append(LedgerDelta("D-0005", 1, "Q-0000", DeltaKind.EXPOSURE, 0.3, 0.0))
    plan = plan_round(2, [request("Q-0002", "timer", round_=2)], j, ledger, spec.run.seed, spec,
                      MemoryExposureStore())
    assert plan.rank["D-0005"] > plan.rank["D-0003"] == plan.rank["D-0004"] == 1.0
    assert set(plan.pairs["Q-0002"][0].decision_ids) | set(plan.pairs["Q-0002"][1].decision_ids) == {"D-0005"}


# --- the judge calls -------------------------------------------------------------------------------------


def test_the_lens_call_gets_no_eval_text_and_the_report_call_gets_it(tmp_path, monkeypatch):
    log = JudgeLog([], [])
    scripted_judge(monkeypatch, log)
    _, _, _, _, _, tasks = two_request_round(tmp_path)
    e = env(tmp_path, ScriptedClient(replies()))
    task = tasks[0]
    out = run_pass(task, e)
    text = eval_report_text(out.report)
    lens_args = log.lens[0]
    assert set(lens_args) == {"pass_id", "request_text", "composed", "source", "lenses"}
    assert lens_args["source"] == Path(out.pass_.artifact_path).read_text()
    assert lens_args["lenses"] == ["L-01", "L-02"]           # retired lenses are not answered
    for value in (lens_args["request_text"], lens_args["source"], *lens_args["composed"].values()):
        assert "Acceptance checks" not in value and "failing step" not in value and "Rubric" not in value
    assert log.report[0]["eval"] == text and "c1" in text


def test_the_eval_report_text_names_no_regime_or_drift():
    report = EvalReport("p", Regime.R2, (CheckResult("c1", False, "click #start"),), ("TypeError: x",),
                        0.5, 0.5, (2, 3, 4), 3, 0.5, eslint_errors=1, axe_violations=2)
    text = eval_report_text(report)
    assert blinding_hits(text, BOOK.blinding["judge_report"]) == []
    assert "R2" not in text and "loss" not in text.lower()
    assert "failing step: click #start" in text and "Q = 3" in text and "2, 3, 4" in text


def test_a_dropped_report_call_leaves_the_fires_undisposed(tmp_path, monkeypatch):
    log = JudgeLog([], [])
    scripted_judge(monkeypatch, log, report_result=None)
    _, _, _, _, _, tasks = two_request_round(tmp_path)
    e = env(tmp_path, ScriptedClient(replies()))
    task = next(t for t in tasks if t.composition.decision_ids)
    out = run_pass(task, e)
    assert out.observations == () and out.dropped_calls == ("report",)
    fires = [f for f in e.judge_store._fires if f.pass_id == task.pass_id]
    assert {f.decision_id for f in fires} == set(task.composition.decision_ids)
    assert all(f.disposition is Disposition.UNDISPOSED for f in fires)


def test_a_judge_transport_failure_drops_the_call_and_keeps_the_eval(tmp_path, monkeypatch):
    scripted_judge(monkeypatch, JudgeLog([], []))

    def down(self, *a, **k):
        raise TransportFailure("5 tries: 503")

    monkeypatch.setattr(Judge, "lens_call", down)
    monkeypatch.setattr(Judge, "report_call", down)
    _, _, _, _, _, tasks = two_request_round(tmp_path)
    e = env(tmp_path, ScriptedClient(replies()))
    task = next(t for t in tasks if t.composition.decision_ids)
    out = run_pass(task, e)
    assert not out.pass_.transport_failed and e.passes.eval_of(task.pass_id) is not None
    assert out.dropped_calls == ("lens", "report")
    assert all(f.disposition is Disposition.UNDISPOSED for f in e.judge_store._fires)


# --- building the artifact -------------------------------------------------------------------------------


def test_the_repair_turn_shows_console_errors_and_missing_ids_only(tmp_path, monkeypatch):
    scripted_judge(monkeypatch, JudgeLog([], []))
    req = request("Q-0001", "canvas", text="Build a timer with #start and #status. DROP-IDS")
    task = PassTask("p-repair", 1, Regime.R1, req, req.text, Arm.JOURNAL, 0, Composition(req.id, ()), (),
                    (lens("L-01"),))
    client = ScriptedClient(replies())
    browser = FileBrowser(errors=("TypeError: x is undefined",))
    out = run_pass(task, env(tmp_path, client, browser=browser))
    assert out.pass_.repaired and len(client.calls) - 0 >= 2
    worker_calls = [c for c in client.calls if c[0] == "worker"]
    assert len(worker_calls) == 1 + SPEC.worker.repair_turns
    repair = worker_calls[1][1][-1].content
    assert "TypeError: x is undefined" in repair and "#start" in repair and "#status" in repair
    assert "c1" not in repair and blinding_hits(repair, BOOK.blinding["worker"]) == []
    assert browser.loads[0][1] == tuple(SPEC.eval.viewports["R1"][0])


def test_no_repair_turn_for_a_clean_page(tmp_path, monkeypatch):
    scripted_judge(monkeypatch, JudgeLog([], []))
    req = request("Q-0001", "canvas")
    task = PassTask("p-clean", 1, Regime.R1, req, req.text, Arm.JOURNAL, 0, Composition(req.id, ()), (), ())
    client = ScriptedClient(replies())
    out = run_pass(task, env(tmp_path, client))
    assert not out.pass_.repaired and [c[0] for c in client.calls] == ["worker"]


def test_an_oversize_artifact_is_kept_whole_and_recorded(tmp_path, monkeypatch):
    scripted_judge(monkeypatch, JudgeLog([], []))
    big = "<!doctype html><html><body><button id=\"start\"></button><p id=\"status\"></p>" + "x" * 40000 + "</body>"
    req = request("Q-0001", "canvas")
    task = PassTask("p-big", 1, Regime.R1, req, req.text, Arm.JOURNAL, 0, Composition(req.id, ()), (), ())
    out = run_pass(task, env(tmp_path, ScriptedClient([big])))
    assert out.oversize and out.artifact_bytes == len(big.encode()) > SPEC.worker.max_artifact_bytes
    assert Path(out.pass_.artifact_path).read_text() == big and out.report is not None
    meta = (Path(out.pass_.artifact_path).parent / "pass.json").read_text()
    assert '"oversize": true' in meta


def test_a_programming_error_in_a_pass_loses_only_that_pass(tmp_path, monkeypatch):
    scripted_judge(monkeypatch, JudgeLog([], []))
    reqs = [request("Q-0001", "canvas", text="Build it. SOFTCRASH"), request("Q-0002", "timer")]
    plan = plan_round(1, reqs, journal(), MemoryLedger(), SPEC.run.seed, SPEC, MemoryExposureStore())
    tasks = round_tasks(plan, reqs, Regime.R1, SPEC.drift.constraint, {}, [], None)
    e = env(tmp_path, ScriptedClient(replies()))
    result = run_round(1, plan.plans, tasks, factory(e), parallel=1, reruns=1, in_process=True, telemetry=TEL)
    assert sorted(result.crashed) == [t.pass_id for t in tasks if t.request.id == "Q-0001"]
    assert all("RuntimeError" in tb for tb in result.crashed.values())
    assert {o.pass_.category for o in result.outcomes} == {"Q-0002"}


# --- real processes --------------------------------------------------------------------------------------


class ProcessJudge(Judge):
    def lens_call(self, pass_id, request_text, composed, source, lenses):
        return [LensAnswer(pass_id, x.id, 0.1, "") for x in lenses]

    def report_call(self, pass_id, request_text, composed, source, answers, eval_report_text, lens_ids):
        return [DraftObservation("free", f"shape of {pass_id}", "dir", "<p>")], {
            d: (Disposition.APPLIED, "") for d in composed}


@dataclass(frozen=True)
class ProcessEnvFactory:
    """Opens a pass's own client, browser and stores inside its process (picklable for spawn)."""

    artifacts: str

    @contextmanager
    def __call__(self):
        client = ScriptedClient(replies())
        tel = MemoryTelemetry(Scope("main"))
        yield PassEnv(
            worker=Worker(client, BOOK), book=BOOK, browser=FileBrowser(), evaluate=scripted_eval,
            judge=ProcessJudge(client, BOOK, 0.5, 3, 1, tel), passes=MemoryPassStore(), judge_store=MemoryJudgeStore(),
            artifacts=Path(self.artifacts), settings=PassSettings.from_spec(SPEC),
            transport_errors=(TransportFailure,), missing_ids=page_ids_missing, telemetry=tel,
        )


def test_passes_run_in_their_own_processes_and_a_hard_crash_loses_only_that_pass(tmp_path):
    reqs = [request("Q-0001", "canvas"), request("Q-0002", "timer", text="Build it. HARDCRASH")]
    j = journal(decision("D-0001", DecisionStatus.CANDIDATE, "canvas"))
    plan = plan_round(1, reqs, j, MemoryLedger(), SPEC.run.seed, SPEC, MemoryExposureStore())
    tasks = round_tasks(plan, reqs, Regime.R1, SPEC.drift.constraint, {d.id: d for d in j.decisions()},
                        [lens("L-01")], None)
    result = run_round(1, plan.plans, tasks, ProcessEnvFactory(str(tmp_path / "artifacts")), parallel=4, reruns=1,
                       telemetry=TEL)
    assert sorted(result.crashed) == [t.pass_id for t in tasks if t.request.id == "Q-0002"]
    assert all("exit code 3" in tb for tb in result.crashed.values())
    assert sorted(o.pass_.id for o in result.outcomes) == [t.pass_id for t in tasks if t.request.id == "Q-0001"]
    assert [(d.decision_id, d.d) for d in result.deltas] == [("D-0001", pytest.approx(0.4))]
    assert {o.pass_id for o in result.observations} == {"r01-Q-0001-journal-0", "r01-Q-0001-journal-1"}


def test_pipeline_module_reads_its_knobs_from_the_spec():
    s = PassSettings.from_spec(SPEC)
    assert (s.repair_turns, s.max_artifact_bytes) == (SPEC.worker.repair_turns, SPEC.worker.max_artifact_bytes)
    assert round_limits(SPEC) == (SPEC.run.max_parallel_passes, SPEC.run.transport_reruns)
    with pytest.raises(ValueError, match="rank_by"):
        pipeline.composition_rank([], MemoryLedger(), spec_with(composition={"rank_by": "mean_d"}))


# --- review fixes: blinding of the repair turn, crashed passes, hung passes, re-entry ---------------------


class UrlErrorBrowser(FileBrowser):
    """A Browser whose page logs a stack that names the artifact's file URL and path, as Chromium does."""

    @contextmanager
    def page(self, artifact_path, viewport):
        self.loads.append((artifact_path, viewport))
        f = Path(artifact_path)
        errors = [f"bad Error: boom\n    at {f.resolve().as_uri()}:1:57",
                  f"ReferenceError at {f.absolute()}:3", f"see artifacts/{f.parent.name}/index.html"]
        yield FakePage(FakeDom(texts={"#start": "", "#status": ""}, errors=errors))


class FailingLoadBrowser(FileBrowser):
    """A Browser whose page load times out with a message naming the artifact's file URL."""

    @contextmanager
    def page(self, artifact_path, viewport):
        self.loads.append((artifact_path, viewport))
        raise TimeoutError(f'Page.goto: Timeout 15000ms exceeded navigating to "{Path(artifact_path).as_uri()}"')
        yield  # pragma: no cover


def _repair_prompts(tmp_path, browser, ids):
    req = request("Q-0001", "canvas")
    out = []
    for pid, rerun_of in ids:
        client = ScriptedClient(replies())
        task = PassTask(pid, 1, Regime.R1, req, req.text, Arm.JOURNAL, int(pid.split("-")[4]), Composition(req.id, ()),
                        (), (), rerun_of=rerun_of)
        run_pass(task, env(tmp_path, client, browser=browser))
        out.append([c for c in client.calls if c[0] == "worker"][1][1][-1].content)
    return out


def test_repair_turn_console_error_urls_do_not_leak_the_pass_id_so_a_pairs_repair_prompts_match(tmp_path, monkeypatch):
    scripted_judge(monkeypatch, JudgeLog([], []))
    a, b = _repair_prompts(tmp_path, UrlErrorBrowser(),
                           [("r01-Q-0001-journal-0", None), ("r01-Q-0001-journal-1-rr", "r01-Q-0001-journal-1")])
    assert a == b
    for leak in ("journal", "r01", "-rr", "artifacts", str(tmp_path), str(tmp_path.resolve())):
        assert leak not in a, leak
    assert "boom" in a and "index.html:1:57" in a


def test_load_failure_text_does_not_leak_the_pass_id_and_reports_no_missing_ids(tmp_path, monkeypatch):
    scripted_judge(monkeypatch, JudgeLog([], []))
    a, b = _repair_prompts(tmp_path, FailingLoadBrowser(),
                           [("r01-Q-0001-journal-0", None), ("r01-Q-0001-journal-1", None)])
    assert a == b and "the page did not load: TimeoutError" in a and 'navigating to "index.html"' in a
    assert "missing element id" not in a and "journal" not in a and str(tmp_path) not in a


def test_neutral_location_replaces_every_form_of_the_artifact_location(tmp_path):
    f = tmp_path / "artifacts" / "r03-Q-0009-notes-0-rr" / "index.html"
    text = f"{f.resolve().as_uri()} {f} artifacts/r03-Q-0009-notes-0-rr/ r03-Q-0009-notes-0-rr"
    out = neutral_location(text, f, ["r03-Q-0009-notes-0-rr"])
    assert "r03" not in out and "notes" not in out and str(tmp_path) not in out


def test_a_rubric_failure_leaves_a_pass_row_with_no_eval_and_no_delta(tmp_path, monkeypatch):
    log = JudgeLog([], [])
    scripted_judge(monkeypatch, log)
    _, _, _, store, _, tasks = two_request_round(tmp_path)

    def evaluate(pass_, request_, regime, text):
        if pass_.id == "r01-Q-0001-journal-0":
            raise RuntimeError("rubric answer unread after the re-ask")
        return scripted_eval(pass_, request_, regime, text)

    e = env(tmp_path, ScriptedClient(replies()), evaluate=evaluate)
    result = run_round(1, store.plans(1), tasks, factory(e), parallel=1, reruns=1, in_process=True, telemetry=TEL)
    assert list(result.crashed) == ["r01-Q-0001-journal-0"] and "rubric" in result.crashed["r01-Q-0001-journal-0"]
    rows = {p.id: p for p in e.passes.passes(1)}
    assert set(rows) == {t.pass_id for t in tasks}            # the store keeps the whole population
    assert e.passes.eval_of("r01-Q-0001-journal-0") is None and not rows["r01-Q-0001-journal-0"].transport_failed
    assert [d for d in result.deltas if d.category == "Q-0001"] == []
    meta = (tmp_path / "artifacts" / "r01-Q-0001-journal-0" / "pass.json").read_text()
    assert "rubric answer unread" in meta


def test_a_report_call_breaking_its_contract_leaves_no_orphan_eval_answers_or_fires(tmp_path, monkeypatch):
    scripted_judge(monkeypatch, JudgeLog([], []))
    monkeypatch.setattr(Judge, "report_call", lambda self, *a: ([], {}))
    spec = spec_with(detect={"ablate_p": 1.0})
    _, _, _, store, _, tasks = two_request_round(tmp_path, spec)
    e = env(tmp_path, ScriptedClient(replies()), spec=spec)
    result = run_round(1, store.plans(1), tasks, factory(e), parallel=1, reruns=1, in_process=True, telemetry=TEL)
    lost = {t.pass_id for t in tasks if t.composition.decision_ids}
    assert lost and set(result.crashed) == lost and result.deltas == ()
    assert all(e.passes.eval_of(i) is None for i in lost)
    assert not {a.pass_id for a in e.judge_store._answers} & lost
    assert not {f.pass_id for f in e.judge_store._fires} & lost
    assert {p.id for p in e.passes.passes(1)} == {t.pass_id for t in tasks}


def test_a_round_where_every_pass_crashes_raises(tmp_path, monkeypatch):
    scripted_judge(monkeypatch, JudgeLog([], []))
    _, _, _, store, _, tasks = two_request_round(tmp_path)

    def evaluate(*a):
        raise RuntimeError("the evaluator is broken")

    e = env(tmp_path, ScriptedClient(replies()), evaluate=evaluate)
    with pytest.raises(RoundCrashed, match="every pass crashed"):
        run_round(1, store.plans(1), tasks, factory(e), parallel=1, reruns=1, in_process=True, telemetry=TEL)
    assert {p.id for p in e.passes.passes(1)} == {t.pass_id for t in tasks}


def test_a_crashed_rerun_is_counted_as_still_failed(tmp_path, monkeypatch):
    scripted_judge(monkeypatch, JudgeLog([], []))
    _, _, _, store, _, tasks = two_request_round(tmp_path)
    seen = []

    def flaky_then_crash(messages):
        if HELP in messages[1].content:
            seen.append(1)
            if len(seen) == 1:
                raise TransportFailure("5 tries: 503")
            raise RuntimeError("the re-run crashed")
        return html_for(messages)

    e = env(tmp_path, ScriptedClient(replies(fn=flaky_then_crash)))
    result = run_round(1, store.plans(1), tasks, factory(e), parallel=1, reruns=1, in_process=True, telemetry=TEL)
    (rr,) = result.transport_failed
    assert rr.endswith("-rr") and rr in result.crashed
    assert [d for d in result.deltas if d.category == "Q-0001"] == []


def test_re_entering_a_round_raises_before_any_pass_runs(tmp_path, monkeypatch):
    scripted_judge(monkeypatch, JudgeLog([], []))
    _, _, _, store, _, tasks = two_request_round(tmp_path)
    e = env(tmp_path, ScriptedClient(replies()))
    run_round(1, store.plans(1), tasks, factory(e), parallel=1, reruns=1, in_process=True, telemetry=TEL)
    client = ScriptedClient(replies())
    again = env(tmp_path, client)
    with pytest.raises(ValueError, match="entered before"):
        run_round(1, store.plans(1), tasks, factory(again), parallel=1, reruns=1, in_process=True, telemetry=TEL)
    assert client.calls == [] and again.passes.passes(1) == []


def test_a_hung_pass_process_is_terminated_after_pass_timeout_s(tmp_path):
    reqs = [request("Q-0001", "canvas"), request("Q-0002", "timer", text="Build it. HANG")]
    j = journal(decision("D-0001", DecisionStatus.CANDIDATE, "canvas"))
    plan = plan_round(1, reqs, j, MemoryLedger(), SPEC.run.seed, SPEC, MemoryExposureStore())
    tasks = round_tasks(plan, reqs, Regime.R1, SPEC.drift.constraint, {d.id: d for d in j.decisions()},
                        [lens("L-01")], None)
    t0 = time.monotonic()
    result = run_round(1, plan.plans, tasks, ProcessEnvFactory(str(tmp_path / "artifacts")), parallel=6, reruns=1,
                       telemetry=TEL, pass_timeout_s=8)
    assert time.monotonic() - t0 < 60
    assert sorted(result.crashed) == [t.pass_id for t in tasks if t.request.id == "Q-0002"]
    assert all("timed out" in tb for tb in result.crashed.values())
    assert [(d.decision_id, d.d) for d in result.deltas] == [("D-0001", pytest.approx(0.4))]
