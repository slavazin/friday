"""The judge's two calls, the eval-report text and the lens minter (SPEC §§ 2, 6)."""

import json
from pathlib import Path

import pytest

from friday.algorithms.language import blinding_hits
from friday.config import load_spec
from friday.domain.types import (
    Check,
    CheckResult,
    Disposition,
    EvalReport,
    HowToParse,
    HowToReading,
    HowToRung,
    LensAnswer,
    LensStatus,
    Observation,
    ProbeReading,
    Regime,
)
from friday.roles import eval_text
from friday.roles.judge import Judge, fill, fires_for, validate_report
from friday.roles.lens_minter import LensMinter, LensProposal, ResidualItem, lens_form_problem
from friday.roles.prompts import PromptBook
from tests.fakes import ScriptedClient
from tests.unit.builders import lens

ROOT = Path(__file__).resolve().parents[2]
BOOK = PromptBook(ROOT / "prompts")
SPEC = load_spec(ROOT / "run.yml")
FAMILIES = tuple(SPEC.stream.families)
SOURCE = (ROOT / "tests/data/judge_fixture.html").read_text()
REQUEST = "A 25-minute countdown with #start, #reset and #time. Reset returns it to 25:00."
COMPOSED = {"D-0002": "Drive countdowns from `Date.now()` differences.", "D-0001": "Bind `keydown` on `window`."}
RESET_LINE = "document.getElementById('reset').addEventListener('click', () => {"


def judge(script, model="judge/served"):
    client = ScriptedClient(list(script), model=model)
    return Judge(client, BOOK, SPEC.lens.flag_p, SPEC.judge.max_observations, SPEC.judge.quote_reasks), client


def marked_report(regime=Regime.R1) -> EvalReport:
    parse = HowToParse(HowToRung.REPAIRED, (Check("how-to-1", (), {}), Check("how-to-2", (), {})), 0,
                       ("#start", "#reset"), ("#zz-uncovered",))
    how_to = HowToReading("x1", (1280, 800), parse, (
        CheckResult("how-to-1", True), CheckResult("how-to-2", False, "zz-howto-step: text of #time unchanged")), 0.4)
    probe = ProbeReading("x1", (1280, 800), True, ("zz-console-marker",), 2, 1, ("#start",), 0, 0.6)
    return EvalReport(
        "x1", regime, (CheckResult("zz-marker-check", False, "zz-failing-step: click #reset"),
                       CheckResult("c-ok", True)),
        ("zz-console-marker",), 0.5, 0.5, (2, 3, 4), 7, 0.61, eslint_errors=5, axe_violations=2,
        probes=(probe,), how_to=(how_to,), silent_controls=1)


MARKERS = ("zz-marker-check", "zz-failing-step", "zz-howto-step", "zz-console-marker", "zz-uncovered")


def lens_reply(quote=RESET_LINE, p=0.8):
    return json.dumps({"lenses": [{"id": "L-04", "p": p, "quote": quote}, {"id": "L-09", "p": 0.1, "quote": ""}]})


LENSES = [lens("L-04"), lens("L-09", LensStatus.HIDDEN)]


def report_reply(**over):
    body = {"observations": [{"lens_id": "L-04", "shape": "reset handler that leaves the remaining time unchanged",
                              "direction": "restore the starting state in the reset handler", "quote": RESET_LINE}],
            "dispositions": {"D-0001": {"disposition": "not-applicable", "quote": ""},
                             "D-0002": {"disposition": "contradicted", "quote": "left -= 1"}}}
    body.update(over)
    return json.dumps(body)


# --- leakage -----------------------------------------------------------------------------------------------

def test_lens_call_input_carries_no_eval_report_text_while_the_report_call_does():
    report_text = eval_text.render(marked_report())
    assert all(m in report_text for m in MARKERS)
    j, client = judge([lens_reply(), report_reply()])
    answers = j.lens_call("x1", REQUEST, COMPOSED, SOURCE, LENSES)
    j.report_call("x1", REQUEST, COMPOSED, SOURCE, answers, report_text, ["L-04", "L-09"])
    lens_text = "\n".join(m.content for m in client.calls[0][1])
    report_call_text = "\n".join(m.content for m in client.calls[1][1])
    assert [m for m in MARKERS if m in lens_text] == []
    assert all(m in report_call_text for m in MARKERS)
    assert "how-to-data" in lens_text and "Reset stops it" in lens_text     # the how-to is artifact source
    assert "Eval report" not in lens_text and "Acceptance checks" not in lens_text


def test_lens_call_templates_name_no_judge_lens_blinded_word():
    rendered = Judge(ScriptedClient([]), BOOK, 0.5, 3, 1).lens_messages("", {}, "", [])
    text = "\n".join(m.content for m in rendered)
    raw = BOOK.text("judge/lens_system.md") + BOOK.text("judge/lens_user.md")
    assert blinding_hits(text + raw, (*BOOK.blinding["judge_lens"], *FAMILIES)) == []


def test_report_call_templates_and_eval_text_labels_name_no_judge_report_blinded_word():
    rendered = Judge(ScriptedClient([]), BOOK, 0.5, 3, 1).report_messages("", {}, "", [], "")
    neutral = EvalReport("x", Regime.R2, (CheckResult("c", False, "s"),), ("e",), 0, 0, (1,), 0, 1.0,
                         probes=marked_report().probes, how_to=marked_report().how_to)
    labels = eval_text.render(neutral)
    text = "\n".join(m.content for m in rendered) + labels
    raw = BOOK.text("judge/report_system.md") + BOOK.text("judge/report_user.md")
    assert blinding_hits(text + raw, (*BOOK.blinding["judge_report"], *FAMILIES)) == []
    assert "R2" not in labels and "R1" not in labels


def test_lens_minter_templates_name_no_lens_minter_blinded_word():
    raw = BOOK.text("lens_minter/system.md") + BOOK.text("lens_minter/user.md")
    assert blinding_hits(raw, (*BOOK.blinding["lens_minter"], *FAMILIES)) == []


def test_lens_minter_prompt_carries_the_lens_language_subsection_verbatim():
    spec = (ROOT / "SPEC.md").read_text()
    sub = spec.split("### Lens language\n", 1)[1].split("### Observation and pool language", 1)[0].strip()
    assert sub in BOOK.text("lens_minter/system.md")


def test_lens_minter_reads_no_round_category_or_arm_of_the_residual_passes():
    obs = Observation("O-0001", "zz-pass-id", 97, "L-03", "zz-category", "handler on an unfocused element",
                      "bind handlers on window", "canvas.addEventListener")
    m = LensMinter(ScriptedClient([]), BOOK, reasks=1, families=FAMILIES)
    text = "\n".join(x.content for x in m.messages([ResidualItem("a timer", "<div>", (obs,))], [lens("L-01")], 2))
    assert "zz-category" not in text and "zz-pass-id" not in text and "97" not in text
    assert "handler on an unfocused element" in text
    assert blinding_hits(text, ("regime", "arm", "R1", "R2")) == []


# --- eval text ---------------------------------------------------------------------------------------------

def test_eval_text_renders_every_reading_the_report_call_receives():
    text = eval_text.render(marked_report())
    assert "- zz-marker-check: fail at step: zz-failing-step: click #reset" in text
    assert "- c-ok: pass" in text
    assert "how-to-2: fail at step: zz-howto-step" in text and "how-to-1: pass" in text
    assert "uncovered contract ids: #zz-uncovered" in text
    assert "repair rung 2 (repaired)" in text
    assert "Silent controls (responsive, not declared in the how-to): 1" in text
    assert "zz-console-marker" in text and "Q: 7 (ESLint errors 5, axe violations 2)" in text
    assert "Rubric scores: 2, 3, 4" in text
    assert "0.61" not in text                                               # the loss is not rendered


def test_eval_text_is_deterministic_and_never_names_the_regime():
    a, b = eval_text.render(marked_report(Regime.R2)), eval_text.render(marked_report(Regime.R2))
    assert a == b == eval_text.render(marked_report(Regime.R1))


def test_eval_text_reads_absent_readings_as_not_read_never_zero():
    text = eval_text.render(EvalReport("x", Regime.R1, (), (), 0, 0, (), 0, 1.0))
    assert "Probe results: not read" in text and "How-to controls: not read" in text


# --- lens call ---------------------------------------------------------------------------------------------

def test_lens_call_answers_active_and_hidden_lenses_and_records_the_served_model():
    j, client = judge([lens_reply()], model="zai/served-echo")
    got = j.lens_call("x1", REQUEST, COMPOSED, SOURCE, LENSES)
    assert [(a.lens_id, a.p, a.judge_model) for a in got] == [("L-04", 0.8, "zai/served-echo"),
                                                              ("L-09", 0.1, "zai/served-echo")]
    assert got[1].quote == "" and j.calls[0].ok and j.calls[0].models == ("zai/served-echo",)
    assert "L-09" in client.calls[0][1][1].content


def test_lens_quote_from_the_request_text_is_not_verbatim_from_the_source():
    j, client = judge([lens_reply(quote="Reset returns it to 25:00."), lens_reply()])
    got = j.lens_call("x1", REQUEST, COMPOSED, SOURCE, LENSES)
    assert got is not None and j.calls[0].attempts == 2
    assert "verbatim" in client.calls[1][1][-1].content


def test_lens_call_is_dropped_after_its_reask_and_the_drop_is_recorded():
    j, _ = judge([lens_reply(quote="paraphrased reset code"), lens_reply(quote="still not there")])
    assert j.lens_call("x1", REQUEST, COMPOSED, SOURCE, LENSES) is None
    assert not j.calls[0].ok and j.calls[0].attempts == 2 and len(j.calls[0].failures) == 2


def test_lens_call_refuses_a_retired_lens_and_rejects_an_unasked_lens_id():
    j, _ = judge([])
    with pytest.raises(ValueError, match="not active or hidden"):
        j.lens_call("x1", REQUEST, COMPOSED, SOURCE, [lens("L-01", LensStatus.RETIRED)])
    extra = json.dumps({"lenses": [*json.loads(lens_reply())["lenses"], {"id": "L-77", "p": 0, "quote": ""}]})
    j, client = judge([extra, extra])
    assert j.lens_call("x1", REQUEST, COMPOSED, SOURCE, LENSES) is None
    assert "unknown lens id" in j.calls[0].failures[0]


def test_fill_never_rescans_inserted_text():
    assert fill("{a} and {b}", a="{b}", b="x") == "{b} and x"
    with pytest.raises(KeyError):
        fill("{missing}")


# --- report call -------------------------------------------------------------------------------------------

def test_report_call_disposes_every_composed_decision():
    j, _ = judge([report_reply()])
    obs, disp = j.report_call("x1", REQUEST, COMPOSED, SOURCE, [], "text", ["L-04"])
    assert obs[0].lens_id == "L-04"
    assert disp == {"D-0001": (Disposition.NOT_APPLICABLE, ""), "D-0002": (Disposition.CONTRADICTED, "left -= 1")}
    fires = fires_for("x1", 3, sorted(COMPOSED), disp)
    assert [(f.decision_id, f.disposition) for f in fires] == [("D-0001", Disposition.NOT_APPLICABLE),
                                                               ("D-0002", Disposition.CONTRADICTED)]


def test_quote_taken_from_the_eval_text_is_refused_and_the_dropped_call_leaves_fires_undisposed():
    report_text = eval_text.render(marked_report())
    bad_obs = [{"lens_id": "free", "shape": "s", "direction": "d", "quote": "zz-failing-step: click #reset"}]
    j, _ = judge([report_reply(observations=bad_obs)] * 2)
    got = j.report_call("x1", REQUEST, COMPOSED, SOURCE, [], report_text, ["L-04"])
    assert got is None and not j.calls[-1].ok
    fires = fires_for("x1", 3, sorted(COMPOSED), None)
    assert {f.disposition for f in fires} == {Disposition.UNDISPOSED} and len(fires) == len(COMPOSED)


def test_report_rejects_an_uncomposed_decision_id_and_an_unquoted_contradiction():
    good = json.loads(report_reply())
    extra = {**good, "dispositions": {**good["dispositions"], "D-0099": "applied"}}
    with pytest.raises(Exception, match="not composed"):
        validate_report(extra, ["L-04"], ["D-0001", "D-0002"], SOURCE, 3)
    unquoted = {**good, "dispositions": {**good["dispositions"], "D-0002": {"disposition": "contradicted"}}}
    with pytest.raises(Exception, match="contradicted needs"):
        validate_report(unquoted, ["L-04"], ["D-0001", "D-0002"], SOURCE, 3)
    undisposed = {**good, "dispositions": {**good["dispositions"], "D-0001": "undisposed"}}
    with pytest.raises(Exception, match="must be applied"):
        validate_report(undisposed, ["L-04"], ["D-0001", "D-0002"], SOURCE, 3)


def test_report_caps_observations_at_max_observations():
    many = [json.loads(report_reply())["observations"][0]] * (SPEC.judge.max_observations + 1)
    j, _ = judge([report_reply(observations=many)] * 2)
    assert j.report_call("x1", REQUEST, COMPOSED, SOURCE, [], "", ["L-04"]) is None


def test_report_call_renders_the_lens_answers_it_is_given():
    j, client = judge([report_reply()])
    answers = [LensAnswer("x1", "L-04", 0.8, RESET_LINE, "m")]
    j.report_call("x1", REQUEST, COMPOSED, SOURCE, answers, "", ["L-04"])
    assert f"- L-04: p 0.80, quote: {RESET_LINE}" in client.calls[0][1][1].content


# --- lens minter -------------------------------------------------------------------------------------------

GOOD = {"angle": "Where does a reset handler leave a running timer or counter value unchanged?",
        "counterfactual": "Which reset restores more state than the request names?"}


def minter(script):
    client = ScriptedClient(list(script))
    return LensMinter(client, BOOK, reasks=SPEC.json.reasks, families=FAMILIES), client


def blinded():
    return minter([])[0].blinded()


def test_every_baseline_lens_is_in_lens_form():
    from friday.adapters.lens_md import parse_lens

    for p in sorted((ROOT / "lenses").glob("L-*.md")):
        x = parse_lens(p.read_text())
        assert lens_form_problem(x.angle, x.counterfactual, blinded(), BOOK.hedges, []) is None, x.id


@pytest.mark.parametrize(("angle", "counter", "why"), [
    ("Does the code reset the timer?", GOOD["counterfactual"], "open with Which"),
    (GOOD["angle"], "Which reset is wrong, or is it fine?", "vacuous"),
    ("Which handler breaks under the R2 regime?", GOOD["counterfactual"], "blinded"),
    ("Which check does the artifact miss?", GOOD["counterfactual"], "blinded"),
    ("Which code would the eval flag?", GOOD["counterfactual"], "blinded"),
    ("Which canvas game loop stalls?", GOOD["counterfactual"], "blinded"),
    ("Which handler might fail?", GOOD["counterfactual"], "hedges"),
    ("Which handler fails? Which state leaks?", GOOD["counterfactual"], "one question"),
    ("", GOOD["counterfactual"], "empty"),
    ("Where does an input event fail to reach the handler meant for it?",
     "Which handler is reached but changes no visible state?", "near-copy"),
])
def test_lens_form_refuses_each_broken_proposal(angle, counter, why):
    existing = [("Where does an input event fail to reach the handler meant for it?",
                 "Which handler is reached but changes no visible state?")]
    assert why in (lens_form_problem(angle, counter, blinded(), BOOK.hedges, existing) or "")


def test_minter_reasks_once_and_returns_what_validated_across_both_replies():
    first = json.dumps({"lenses": [GOOD, {"angle": "Does it work?", "counterfactual": "Is it fine?"}]})
    second = json.dumps({"lenses": [{"angle": "Which value read from storage is never parsed back to a number?",
                                     "counterfactual": "Which stored value the request never asked to keep?"}]})
    m, client = minter([first, second])
    got = m.propose([ResidualItem("a timer", "<div>", ())], [lens("L-01")], 2)
    assert got[0] == LensProposal(GOOD["angle"], GOOD["counterfactual"]) and len(got) == 2
    assert len(client.calls) == 2 and "refused" in client.calls[1][1][-1].content
    assert len(m.failures) == 1


def test_minter_returns_at_most_limit_and_refuses_near_copies_within_one_reply():
    reply = json.dumps({"lenses": [GOOD, GOOD, {**GOOD, "angle": GOOD["angle"].replace("Where", "Which")}]})
    m, client = minter([reply, reply])
    assert m.propose([], [], 1) == [LensProposal(GOOD["angle"], GOOD["counterfactual"])]
    assert len(client.calls) == 1
    m, _ = minter([reply, "not json"])
    assert m.propose([], [], 2) == [LensProposal(GOOD["angle"], GOOD["counterfactual"])]
    assert any("near-copy" in f for f in m.failures) and any("invalid JSON" in f for f in m.failures)


def test_minter_with_no_valid_proposal_returns_none_after_its_reask():
    bad = json.dumps({"lenses": [{"angle": "Is the arm ok?", "counterfactual": "or not?"}]})
    m, client = minter([bad, bad])
    assert m.propose([], [], 2) == [] and len(client.calls) == 2


# --- recorded live call ------------------------------------------------------------------------------------

def test_recorded_live_judge_replies_on_the_fixture_artifact_validate():
    from friday.adapters.lens_md import parse_lens

    rec = json.loads((ROOT / "tests/data/judge_live_replies.json").read_text())
    lenses = [parse_lens(p.read_text()) for p in sorted((ROOT / "lenses").glob("L-*.md"))]
    model = rec["replies"][0]["model"]
    j, _ = judge([r["text"] for r in rec["replies"]], model=model)
    answers = j.lens_call("smoke", rec["request"], rec["composed"], SOURCE, lenses)
    assert answers is not None and {a.lens_id for a in answers} == {x.id for x in lenses}
    assert all(a.judge_model == model for a in answers)
    out = j.report_call("smoke", rec["request"], rec["composed"], SOURCE, answers, rec["report"],
                        [x.id for x in lenses])
    assert out is not None and set(out[1]) == set(rec["composed"])
    assert [c.attempts for c in j.calls] == [1, 1]
