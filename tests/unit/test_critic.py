"""The critic run (SPEC § 17): build, critic, revision; requests honored; the pin check; the overlay spec;
the critic readings."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from friday.adapters.memory import MemoryJournal, MemoryPassStore
from friday.algorithms.language import blinding_hits
from friday.app import pipeline
from friday.app.pins import CriticNotIndependent, check_critic_independence
from friday.app.readout import critic_lines, critic_readings
from friday.app.runner import CRITIC_RUN, MAIN_RUN, load_run_spec, run_scope
from friday.config import ConfigError
from friday.domain.types import Arm, CheckResult, DecisionStatus, EvalReport, HowToReading, Pass, Regime
from friday.eval import how_to
from friday.roles.ask import Rejected
from friday.roles.critic import Critic, CriticUnread, match_request, revision_message, validate_reply
from tests.fakes import ScriptedClient
from tests.unit.builders import dec
from tests.unit.test_pipeline import (
    BOOK,
    SPEC,
    TEL,
    JudgeLog,
    env,
    factory,
    html_for,
    scripted_judge,
    spec_with,
    two_request_round,
)

ROOT = Path(__file__).resolve().parents[2]
WANT = "I want a reset button that puts the counter back to zero."
UNRELATED = "Let me export my scores to a spreadsheet file."
CRITIC_REPLY = json.dumps({"criticism": "The start button gives no sign it was pressed.",
                           "requests": [WANT, UNRELATED]})
REVISED = ('```html\n<!doctype html><html><body><button id="start">go</button><p id="status">x</p>'
           '<button id="reset">reset</button><p>REVISED</p>'
           '<section id="how-to">Reset puts the counter to zero.</section>'
           '<script type="application/json" id="how-to-data">{"summary": "s", "controls": ['
           '{"id": "#reset", "does": "puts the counter back to zero", "action": {"click": "#reset"}, '
           '"effect": {"text_changed": "#status"}}]}</script></body></html>\n```')


def is_critic(messages) -> bool:
    return messages[0].content.startswith(BOOK.text("critic/system.md").split("\n", 1)[0][:40])


def is_revision(messages) -> bool:
    return messages[-1].role == "user" and BOOK.text("critic/criticism_heading.md").strip() in messages[-1].content


def reply(messages) -> str:
    if is_critic(messages):
        return CRITIC_REPLY
    if is_revision(messages):
        return REVISED
    return html_for(messages)


class HowToEval:
    """An evaluator reading the how-to for real (every declared control passes unless `failing`)."""

    def __init__(self, failing: frozenset[str] = frozenset()) -> None:
        self.seen: list[tuple[str, str, str]] = []       # (pass id, html evaluated, request text)
        self.failing = failing

    def __call__(self, pass_, request_, regime, text) -> EvalReport:
        html = Path(pass_.artifact_path).read_text()
        self.seen.append((pass_.id, html, text))
        parse = how_to.parse(html, request_.contract, SPEC.how_to.max_controls, SPEC.eval.test_query,
                             SPEC.probe.settle_ms)
        results = tuple(CheckResult(c.id, c.id not in self.failing) for c in parse.declared)
        loss = 0.2 if "REVISED" in html else 0.6
        return EvalReport(pass_.id, regime, (), (), 1 - loss, 1 - loss, (3, 3, 3), 1, loss,
                          how_to=(HowToReading(pass_.id, (1280, 800), parse, results, 1.0),))


def critic_env(tmp_path, client, evaluate):
    e = env(tmp_path, client, evaluate=evaluate)
    e.critic = Critic(client, BOOK, SPEC.critic.max_requests, SPEC.json.reasks)
    return e


# --- one scripted critic-run round on fakes ---------------------------------------------------------------


def test_a_critic_round_gives_every_pass_one_critic_call_one_revision_and_evaluates_the_revision(
        tmp_path, monkeypatch):
    log = JudgeLog([], [])
    scripted_judge(monkeypatch, log)
    spec = spec_with(detect={"ablate_p": 1.0})
    _, _, _, store, _, tasks = two_request_round(tmp_path, spec)
    client = ScriptedClient([reply] * 200)
    ev = HowToEval()
    e = critic_env(tmp_path, client, ev)
    result = pipeline.run_round(1, store.plans(1), tasks, factory(e), parallel=1, reruns=1, in_process=True,
                                telemetry=TEL)
    assert result.crashed == {} and len(result.outcomes) == len(tasks) == 6
    assert {t.arm for t in tasks} == {Arm.JOURNAL, Arm.NOTES}
    for t in tasks:
        mine = [c for c in client.calls if c[2].startswith(t.pass_id)]
        assert sum(c[0] == "critic" for c in mine) == 1
        assert sum(c[2] == f"{t.pass_id}:revision" for c in mine) == 1
        out = tmp_path / "artifacts" / t.pass_id
        assert "REVISED" in (out / "index.html").read_text() and "REVISED" not in (out / "built.html").read_text()
        honored = json.loads((out / pipeline.HONORED_FILE).read_text())
        assert (honored["honored"], honored["of"], honored["share"]) == (1, 2, 0.5)
        assert [r["control"] for r in honored["requests"]] == ["how-to.0:#reset", None]
    assert {pid for pid, _, _ in ev.seen} == {t.pass_id for t in tasks}
    assert all("REVISED" in html for _, html, _ in ev.seen)                 # the revised artifact is evaluated
    assert log.lens and all("REVISED" in r["source"] for r in log.lens)       # and judged


def test_the_loss_is_read_against_the_original_request_text(tmp_path, monkeypatch):
    scripted_judge(monkeypatch, JudgeLog([], []))
    _, _, _, store, _, tasks = two_request_round(tmp_path)
    ev = HowToEval()
    e = critic_env(tmp_path, ScriptedClient([reply] * 200), ev)
    pipeline.run_round(1, store.plans(1), tasks, factory(e), parallel=1, reruns=0, in_process=True, telemetry=TEL)
    by_id = {t.pass_id: t for t in tasks}
    for pid, _, text in ev.seen:
        assert text == by_id[pid].request_text
        assert WANT not in text and "start button gives no sign" not in text


def test_each_critic_call_reads_only_its_own_artifact_and_no_eval_reading(tmp_path, monkeypatch):
    scripted_judge(monkeypatch, JudgeLog([], []))
    _, _, _, store, _, tasks = two_request_round(tmp_path)
    client = ScriptedClient([reply] * 200)
    ev = HowToEval()
    evaluated_before: list[int] = []

    def watching(messages):
        if is_critic(messages):
            evaluated_before.append(len(ev.seen))
        return reply(messages)

    client.script = [watching] * 200
    e = critic_env(tmp_path, client, ev)
    pipeline.run_round(1, store.plans(1), tasks, factory(e), parallel=1, reruns=0, in_process=True, telemetry=TEL)
    critic_calls = [(c[2], c[1]) for c in client.calls if c[0] == "critic"]
    for tag, messages in critic_calls:
        pid = tag.removesuffix(":critic")
        built = (tmp_path / "artifacts" / pid / "built.html").read_text()
        user = messages[1].content
        assert built in user
        assert messages[1].images                               # the screenshot is attached
        others = [t.pass_id for t in tasks if t.pass_id != pid]
        assert all(o not in user for o in others) and pid not in user
        assert "Acceptance checks" not in user and "Rubric scores" not in user
    # the critic of a pass runs before that pass is evaluated: no eval reading exists for it yet
    seen_ids = [pid for pid, _, _ in ev.seen]
    for i, (tag, _) in enumerate(critic_calls):
        assert evaluated_before[i] == i
        assert tag.removesuffix(":critic") not in seen_ids[:i]


def test_a_pairs_two_critic_prompts_differ_only_by_their_own_artifact(tmp_path, monkeypatch):
    scripted_judge(monkeypatch, JudgeLog([], []))
    _, _, _, store, _, tasks = two_request_round(tmp_path, spec_with(detect={"ablate_p": 1.0}))
    client = ScriptedClient([reply] * 200)
    e = critic_env(tmp_path, client, HowToEval())
    pipeline.run_round(1, store.plans(1), tasks, factory(e), parallel=1, reruns=0, in_process=True, telemetry=TEL)
    pair = [t.pass_id for t in tasks if t.request.id == "Q-0001" and t.arm is Arm.JOURNAL]
    sent = {c[2].removesuffix(":critic"): c[1] for c in client.calls if c[0] == "critic"}
    a, b = (sent[p] for p in pair)
    assert a[0].content == b[0].content
    built = [(tmp_path / "artifacts" / p / "built.html").read_text() for p in pair]
    assert a[1].content.replace(built[0], "") == b[1].content.replace(built[1], "")


def test_an_unusable_critic_reply_crashes_the_pass_rather_than_skipping_the_revision(tmp_path, monkeypatch):
    scripted_judge(monkeypatch, JudgeLog([], []))
    _, _, _, store, _, tasks = two_request_round(tmp_path)

    def bad(messages):
        return "not json at all" if is_critic(messages) else reply(messages)

    e = critic_env(tmp_path, ScriptedClient([bad] * 200), HowToEval())
    with pytest.raises(CriticUnread):
        pipeline.run_pass(tasks[0], e)
    assert e.passes.passes(1) == []                        # no eval of an unrevised artifact is stored


def test_a_revision_without_a_document_leaves_the_built_file_evaluated(tmp_path, monkeypatch):
    scripted_judge(monkeypatch, JudgeLog([], []))
    _, _, _, _, _, tasks = two_request_round(tmp_path)

    def no_doc(messages):
        return "Sorry, here are my thoughts." if is_revision(messages) else reply(messages)

    ev = HowToEval()
    e = critic_env(tmp_path, ScriptedClient([no_doc] * 200), ev)
    pipeline.run_pass(tasks[0], e)
    out = tmp_path / "artifacts" / tasks[0].pass_id
    assert (out / "index.html").read_text() == (out / "built.html").read_text()
    honored = json.loads((out / pipeline.HONORED_FILE).read_text())
    assert honored["honored"] == 0 and json.loads((out / pipeline.CRITIC_FILE).read_text())["revised"] is False


def test_a_main_run_pass_takes_no_critic_turn(tmp_path, monkeypatch):
    scripted_judge(monkeypatch, JudgeLog([], []))
    _, _, _, _, _, tasks = two_request_round(tmp_path)
    client = ScriptedClient([reply] * 200)
    pipeline.run_pass(tasks[0], env(tmp_path, client, evaluate=HowToEval()))
    assert [c[0] for c in client.calls] == ["worker"]
    assert not (tmp_path / "artifacts" / tasks[0].pass_id / pipeline.HONORED_FILE).exists()


# --- requests honored ---------------------------------------------------------------------------------------


def test_a_matched_control_whose_how_to_check_fails_is_not_honored(tmp_path, monkeypatch):
    scripted_judge(monkeypatch, JudgeLog([], []))
    _, _, _, _, _, tasks = two_request_round(tmp_path)
    e = critic_env(tmp_path, ScriptedClient([reply] * 200), HowToEval(failing=frozenset({"how-to.0:#reset"})))
    pipeline.run_pass(tasks[0], e)
    honored = json.loads((tmp_path / "artifacts" / tasks[0].pass_id / pipeline.HONORED_FILE).read_text())
    assert honored["requests"][0]["control"] == "how-to.0:#reset"
    assert honored["requests"][0]["how_to_passed"] is False and honored["honored"] == 0


def test_a_request_matches_by_its_words_not_by_any_control():
    controls = [("how-to.0:#start", "starts the timer start"),
                ("how-to.1:#reset", "puts the counter back to zero reset")]
    assert match_request(WANT, controls).control == "how-to.1:#reset"
    assert match_request(UNRELATED, controls).control is None
    assert match_request("I want it.", controls).control is None          # no content word: nothing to match
    assert match_request(WANT, []).control is None


def test_no_feature_request_reads_unread_not_zero():
    report = EvalReport("p", Regime.R1, (), (), 0.5, 0.5, (3,), 1, 0.5)
    assert pipeline.requests_honored("<!doctype html>", report, [])["share"] is None


# --- the critic's reply ----------------------------------------------------------------------------------


def test_the_reply_is_bounded_in_words_requests_and_sentences():
    ok = validate_reply({"criticism": "Too plain.", "requests": [WANT]}, 2)
    assert ok.requests == (WANT,)
    for bad in ({"criticism": "word " * 61, "requests": []}, {"criticism": "x", "requests": [WANT, WANT, WANT]},
                {"criticism": "x", "requests": ["Add a reset. Then add a timer."]}, {"criticism": "", "requests": []},
                {"criticism": "x"}, ["x"]):
        with pytest.raises(Rejected):
            validate_reply(bad, 2)


def test_a_page_that_does_not_load_gives_the_critic_no_image():
    c = Critic(ScriptedClient([CRITIC_REPLY]), BOOK, 2, 1)
    msgs = c.messages("a timer", "<!doctype html>", None)
    assert msgs[1].images == () and BOOK.text("critic/no_screenshot.md").strip() in msgs[1].content


# --- blinding --------------------------------------------------------------------------------------------


def test_critic_templates_name_no_blinded_word_and_no_goal_family():
    words = (*BOOK.blinding["critic"], *SPEC.stream.families, *SPEC.stream.modes)
    for name in ("system.md", "user.md", "no_screenshot.md"):
        assert blinding_hits(BOOK.text(f"critic/{name}"), words) == [], name
    for w in ("check", "loss", "score", "regime", "drift", "candidate", "pair", "arm"):
        assert w in BOOK.blinding["critic"]


def test_the_revision_turn_template_names_no_word_blinded_for_the_worker():
    words = (*BOOK.blinding["worker"], *SPEC.stream.families, *SPEC.stream.modes)
    msg = revision_message(BOOK, "", validate_reply({"criticism": "x", "requests": []}, 2))
    template = msg.content.replace("x", "")
    assert blinding_hits(template, words) == []
    for name in ("revision.md", "criticism_heading.md", "requests_heading.md"):
        assert blinding_hits(BOOK.text(f"critic/{name}"), words) == [], name


# --- the pin check ---------------------------------------------------------------------------------------


def test_the_pin_check_refuses_a_critic_bound_to_the_rubrics_model():
    bound = {"rubric": "m/vision", "judge": "m/judge", "nominator": "m/draft", "critic": "m/vision"}
    with pytest.raises(CriticNotIndependent, match="rubric"):
        check_critic_independence(bound)
    for role in ("judge", "nominator"):
        with pytest.raises(CriticNotIndependent, match=role):
            check_critic_independence({**bound, "critic": bound[role]})
    check_critic_independence({**bound, "critic": "m/other"})


def test_the_critic_pin_is_a_vision_model_distinct_from_the_rubric_judge_and_nominator_pins():
    roles = SPEC.models.roles
    critic = roles["critic"]
    assert critic.requires_image
    assert critic.pins[0] not in (roles["rubric"].pins[0], roles["judge"].pins[0], roles["nominator"].pins[0])


# --- the overlay spec --------------------------------------------------------------------------------------


def test_run_critic_yml_overrides_only_paths_rounds_and_critic():
    base, crit = SPEC, load_run_spec(ROOT / "run-critic.yml")
    assert run_scope(ROOT / "run-critic.yml") == CRITIC_RUN and run_scope(ROOT / "run.yml") == MAIN_RUN
    assert crit.run.rounds == 4 and base.drift.round <= crit.run.rounds
    assert crit.run.spend_cap_usd == crit.critic.spend_cap_usd != base.run.spend_cap_usd
    assert crit.run.max_parallel_passes == crit.critic.max_parallel_passes
    assert crit.paths.db != base.paths.db
    same = {f.name for f in dataclasses.fields(base)} - {"run", "paths", "critic", "spec_hash"}
    assert all(getattr(crit, n) == getattr(base, n) for n in same)
    assert dataclasses.replace(crit.run, rounds=base.run.rounds, spend_cap_usd=base.run.spend_cap_usd,
                               max_parallel_passes=base.run.max_parallel_passes) == base.run
    assert crit.spec_hash != base.spec_hash


def test_an_overlay_that_overrides_anything_else_is_refused(tmp_path):
    (tmp_path / "run.yml").write_text((ROOT / "run.yml").read_text())
    for body in ("eval: {w_a: 0.5}\n", "run: {seed: 1}\n", "run: {spend_cap_usd: 99.0}\n", "paths: {nope: x}\n"):
        p = tmp_path / "over.yml"
        p.write_text("extends: run.yml\nscope: critic\n" + body)
        with pytest.raises(ConfigError):
            load_run_spec(p)


# --- the critic readings ------------------------------------------------------------------------------------


def _scored(store: MemoryPassStore, pid: str, round_: int, category: str, loss: float) -> None:
    from friday.domain.types import Composition

    store.add_pass(Pass(pid, round_, category, Arm.JOURNAL, "w", Composition(category, ()), Regime.R1))
    store.add_eval(EvalReport(pid, Regime.R1, (), (), 1 - loss, 1 - loss, (3,), 1, loss))


def test_critic_readings_compare_only_requests_both_runs_built_and_read_unread_when_absent():
    main, crit = MemoryPassStore(), MemoryPassStore()
    _scored(main, "m1", 1, "Q-0001", 0.4)
    _scored(main, "m2", 1, "Q-0001", 0.6)
    _scored(main, "m3", 5, "Q-0050", 0.9)               # a round the critic run never reached
    _scored(main, "m4", 8, "H-01", 0.1)                 # the main run's readout
    _scored(crit, "c1", 1, "Q-0001", 0.3)
    _scored(crit, "c2", 2, "Q-0002", 0.3)
    honored = {"c1": {"honored": 1, "of": 2}}
    j_main = MemoryJournal(_decisions={"D-0001": dec("D-0001"), "D-0002": dec("D-0002", DecisionStatus.CANDIDATE)})
    r = critic_readings(main, crit, honored, j_main, MemoryJournal(), frozenset({"H-01"}))
    assert [(b.category, b.main, b.critic) for b in r.loss] == [("Q-0001", pytest.approx(0.5), pytest.approx(0.3))]
    assert r.mean_diff == pytest.approx(-0.2)
    assert [(h.round, h.honored, h.requests, h.share) for h in r.honored] == [(1, 1, 2, 0.5)]
    assert r.honored_unread == ("c2",)
    assert [i for i, _ in r.admitted_main] == ["D-0001"] and r.admitted_critic == ()
    empty = critic_readings(MemoryPassStore(), MemoryPassStore(), {}, MemoryJournal(), MemoryJournal(), frozenset())
    assert empty.mean_diff is None and empty.honored == ()
    assert any("unread" in line for line in critic_lines(empty))
