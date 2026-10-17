"""One scripted round end to end (pass pipeline and round close, real Chromium, a metered client over a scripted
HTTP transport, in-memory stores) writes every Pass, Eval, Judge, Model call and Round close row the telemetry
catalogue lists, and no row the catalogue lacks."""

from __future__ import annotations

import dataclasses
import json
import re
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from friday.adapters.crusoe import CrusoeClient, TransportFailure
from friday.adapters.memory import MemoryExposureStore, MemorySpendLog
from friday.adapters.rubric import ModelRubric
from friday.adapters.telemetry import MemoryTelemetry
from friday.app.pipeline import PassEnv, PassSettings, plan_round, round_tasks_for, run_round
from friday.app.telemetry_catalogue import load_catalogue
from friday.app.wiring import build_mechanics
from friday.domain.events import Steer
from friday.domain.types import Check, DecisionStatus, Regime, Request
from friday.eval.evaluate import Evaluator
from friday.ports.telemetry import Scope
from friday.roles.ask import Rejected, ask_json
from friday.roles.judge import Judge
from friday.roles.prompts import PromptBook
from friday.roles.worker import Worker
from tests.contract.test_round_close_order import SPEC, World
from tests.unit.builders import dec

ROOT = Path(__file__).resolve().parents[2]
CATALOGUE = load_catalogue(ROOT / "docs/telemetry.md")
CHECKED = ("Pass", "Eval", "Judge", "Model call", "Round close")
# Rows another step owns: the runner (S11) opens `run` and `round`; minting, tagging, the readout runner and the
# critic run write theirs outside a training round (tagging and the readout are tested in tests/unit).
EXPECTED_LATER = sorted(n for n, r in CATALOGUE.items() if r.section not in CHECKED)

WORKER, JUDGE, RUBRIC = "w/worker", "j/judge", "r/rubric"
FIRST = '<!doctype html><html><body><button id="start">go</button><p>x</p></body></html>'
FIXED = ('<!doctype html><html><body><button id="start">go</button><p id="status">idle</p>'
         "<script>document.getElementById('start').onclick=()=>{document.getElementById('status').textContent="
         "'on'}</script></body></html>")


def _completion(model: str, text: str) -> httpx.Response:
    return httpx.Response(200, json={"id": "c", "model": model, "choices": [{"message": {"content": text}}],
                                     "usage": {"prompt_tokens": 100, "completion_tokens": 50}})


def handler(req: httpx.Request) -> httpx.Response:
    """The scripted service: the worker's first reply misses `#status` (so the repair turn runs), the judge
    answers each lens and disposes each composed decision, the rubric scores 4."""
    body = json.loads(req.content)
    model, msgs = body["model"], body["messages"]
    user = msgs[-1]["content"] if isinstance(msgs[-1]["content"], str) else msgs[-1]["content"][0]["text"]
    if model == WORKER:
        return _completion(model, f"```html\n{FIXED if len(msgs) > 2 else FIRST}\n```")
    if model == RUBRIC:
        return _completion(model, '{"resembles": 4, "controls_visible": 4, "legible": 4}')
    if "Acceptance checks:" in user:      # the report call reads the eval report; the lens call never does
        composed = re.findall(r"^- (D-\d{4}): ", user, flags=re.MULTILINE)
        return _completion(model, json.dumps({
            "observations": [{"lens_id": "free", "shape": "status line", "direction": "label it",
                              "quote": 'id="start"'}],
            "dispositions": {d: "applied" for d in composed}}))
    lenses = re.findall(r"^- (L-\d+)$", user, flags=re.MULTILINE)
    return _completion(model, json.dumps({"lenses": [{"id": i, "p": 0.1} for i in lenses]}))


class Quality:
    def eslint_errors(self, html):
        return 0

    def axe_violations(self, page):
        return 1


def client(monkeypatch, spec, telemetry, transport=handler) -> CrusoeClient:
    monkeypatch.setenv(spec.models.endpoint.api_key_env, "test-key")
    prices = {m: {"prompt": 0.1, "completion": 0.2} for m in (WORKER, JUDGE, RUBRIC)}
    models = dataclasses.replace(spec.models, price_overrides=prices)
    http = httpx.Client(base_url="https://x/v1", transport=httpx.MockTransport(transport))
    c = CrusoeClient(models, spec.transport, MemorySpendLog(), http=http, sleep=lambda s: None, telemetry=telemetry)
    c.bind({"worker": WORKER, "judge": JUDGE, "rubric": RUBRIC})
    return c


@pytest.fixture(scope="module")
def browser_cls():
    try:
        from friday.adapters.playwright_browser import PlaywrightBrowser
        PlaywrightBrowser(1500, 3000, telemetry=MemoryTelemetry(Scope("main"))).close()
    except Exception as e:  # noqa: BLE001 - no Playwright or no Chromium: the browser tests skip
        pytest.skip(f"Chromium unavailable: {type(e).__name__}: {e}")
    return PlaywrightBrowser


def run_scripted_round(tmp_path, monkeypatch, browser_cls):
    """Round 1 of one request under a candidate in flight and a retire steer; returns the shared log."""
    spec = SPEC
    tel = MemoryTelemetry(Scope("main"))
    w = World(tmp_path)
    for part in (w.close, w.close.decisions, w.close.lenses):
        part.telemetry = tel
    w.journal.draft(dec("D-0001", DecisionStatus.CANDIDATE, fires=("canvas",), payload="Label every control."))
    w.journal._decisions["D-0002"] = dec("D-0002", DecisionStatus.ADMITTED, fires=("timer",))
    w.events.append_steer(Steer("D-0002", "retire", "demo retire", 1))
    check = Check("c1", ({"goto": "?test=1"}, {"click": "#start"}), {"text_matches": "#status", "regex": "on"})
    req = Request("Q-0001", "Build a switch with a #start button and a #status line.", frozenset({"canvas"}),
                  {"#start": "turns it on", "#status": "shows the state"},
                  {Regime.R1: (check,), Regime.R2: (check,)}, round=1)
    plan = plan_round(1, [req], w.journal, w.ledger, spec.run.seed, spec, MemoryExposureStore())
    tasks = round_tasks_for(plan, [req], spec, {d.id: d for d in w.journal.decisions()}, w.lens_set.lenses(),
                            tmp_path / "notes.md")
    c = client(monkeypatch, spec, tel)
    browser = browser_cls(spec.eval.action_timeout_ms, spec.eval.load_timeout_ms, telemetry=tel)
    book = PromptBook(ROOT / "prompts")
    ev = Evaluator(
        browser=browser, quality=Quality(),
        loss_model=dataclasses.replace(build_mechanics(spec).loss, a_weights=dict(spec.eval.a_weights)),
        rubric=ModelRubric.from_spec(spec, c, ask_json, Rejected, model=RUBRIC, prompts=ROOT / "prompts",
                                     telemetry=tel),
        viewports=spec.eval.viewports, test_query=spec.eval.test_query, probe_max_controls=spec.probe.max_controls,
        settle_ms=spec.probe.settle_ms, sample_text=spec.probe.sample_text,
        how_to_max_controls=spec.how_to.max_controls, repair_penalty=spec.how_to.repair_penalty, telemetry=tel)
    env = PassEnv(
        worker=Worker(c, book), book=book, browser=browser, evaluate=ev.run,
        judge=Judge(c, book, spec.lens.flag_p, spec.judge.max_observations, spec.judge.quote_reasks, tel),
        passes=w.passes, judge_store=w.judge, artifacts=tmp_path / "artifacts",
        settings=PassSettings.from_spec(spec), transport_errors=(TransportFailure,), telemetry=tel,
    )
    @contextmanager
    def factory():
        yield env

    try:
        result = run_round(1, plan.plans, tasks, factory, parallel=1, reruns=1, in_process=True, telemetry=tel)
    finally:
        browser.close()
    assert result.crashed == {}, result.crashed
    drafts = defaultdict(list)
    for ob in result.observations:
        drafts[ob.pass_id].append(ob.draft)
    for delta in result.deltas:
        w.ledger.append(delta)
    w.close.close(1, dict(drafts))
    return tel, w, result


def test_one_scripted_round_emits_every_catalogued_row_and_none_the_catalogue_lacks(tmp_path, monkeypatch,
                                                                                    browser_cls):
    tel, w, result = run_scripted_round(tmp_path, monkeypatch, browser_cls)
    seen = {r.name for r in tel.rows()}
    wanted = {n for n, r in CATALOGUE.items() if r.section in CHECKED}
    assert sorted(wanted - seen) == []
    assert sorted(seen - set(CATALOGUE)) == []
    assert "run" in EXPECTED_LATER and "round" in EXPECTED_LATER and "readout" in EXPECTED_LATER
    # every row of a pass carries its round and pass id, the model calls inside it included
    for p in w.passes.passes(1):
        rows = tel.rows(pass_id=p.id)
        assert {"pass", "model.call", "eval.rubric", "pass.outcome"} <= {r.name for r in rows}
        assert all(r.scope.round == 1 for r in rows)
        assert p.composed_tokens is not None
    # the close's rows carry the round and no pass
    assert all(r.scope.round == 1 and r.scope.pass_id is None for r in tel.rows(name_prefix="close"))
    # the scope is restored after the pass and the close
    assert tel.scope == Scope("main")
    commits = [r.attrs for r in tel.rows(name_prefix="close.commit")]
    assert commits == [{"subject_id": "D-0002", "sha": None, "cause": "steer.retire"}]
    wealth = [r for r in tel.rows(name_prefix="close.wealth")]
    assert [r.attrs["decision_id"] for r in wealth] == ["D-0001"] and wealth[0].attrs["n"] == 1


def test_no_attribute_holds_a_prompt_a_reply_or_an_artifact_source(tmp_path, monkeypatch, browser_cls):
    tel, _, _ = run_scripted_round(tmp_path, monkeypatch, browser_cls)
    blob = json.dumps([r.attrs for r in tel.rows()] + [r.error for r in tel.rows()])
    for text in ("<!doctype", "Acceptance checks", "Build a switch", "test-key", "Label every control", "resembles"):
        assert text not in blob
