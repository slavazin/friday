"""The goal minter, the contract stub check validation (real Chromium) and the goals/*.json adapters (SPEC § 4)."""

import inspect
import json
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

from friday.adapters import goals_json
from friday.adapters.goals_json import JsonGoalSource, JsonTagVocabulary
from friday.algorithms.language import word_hits
from friday.app.goals import StubBench, dsl_validator, mint_params
from friday.config import load_spec
from friday.domain.types import Check, Regime, TagGloss
from friday.eval import dsl
from friday.ports import goals
from friday.roles.ask import Rejected
from friday.roles.goal_minter import (
    GoalMinter,
    MintedGoals,
    MintFailed,
    Slot,
    StubReading,
    element_kind,
    grid_cells,
    plan_slots,
    quotas,
    request_text,
    stub_html,
    validate_draft,
)
from friday.roles.prompts import PromptBook
from tests.fakes import ScriptedClient

ROOT = Path(__file__).resolve().parents[2]
SPEC = load_spec(ROOT / "run.yml")
BOOK = PromptBook(ROOT / "prompts")
PARAMS = mint_params(SPEC)
DSL = dsl_validator(SPEC.eval.max_wait_ms)

COUNTER = {"#add": "button that adds one", "#count": "display of the running count"}
BEHAVIOUR = {"steps": [{"click": "#add"}], "expect": {"text_changed": "#count"}}
BEHAVIOUR_2 = {"steps": [{"click": "#add"}, {"click": "#add"}], "expect": {"text_matches": "#count", "regex": r"\b2\b"}}
ON_STUB = {"steps": [], "expect": {"exists": "#count"}}
ON_STUB_2 = {"steps": [], "expect": {"visible": "#count"}}


def draft(text="a tally counter that goes up by one each time I press add", checks=(BEHAVIOUR, ON_STUB),
          contract=COUNTER, timed=False) -> str:
    return json.dumps({"text": text, "timed": timed, "contract": contract, "checks": list(checks)})


def regen(check) -> str:
    return json.dumps({"check": check})


R1_SLOT = Slot("G-001", 1, 0, Regime.R1, SPEC.stream.families[0], SPEC.stream.modes[0])
R2_SLOT = Slot("G-071", SPEC.drift.round, 0, Regime.R2, SPEC.stream.families[1])


def no_sink(_: MintedGoals) -> None:
    raise AssertionError("mint() not expected")


def minter(script, stub_reading, params=PARAMS):
    return GoalMinter(ScriptedClient(list(script)), BOOK, params, stub_reading, DSL, no_sink)


def fails_on_stub(*_):
    return StubReading(False)


# --- the family draw ---------------------------------------------------------------------------------------


def test_family_shares_per_regime_match_the_mix_within_one_request_on_the_full_stream():
    slots = plan_slots(PARAMS)
    stream = [s for s in slots if s.round is not None]
    assert len(stream) == SPEC.stream.per_round * SPEC.run.rounds == 140
    for regime in (Regime.R1, Regime.R2):
        mine = [s for s in stream if s.regime is regime]
        counts = Counter(s.family for s in mine)
        for fam, share in zip(SPEC.stream.families, SPEC.stream.mix[regime.value], strict=True):
            assert abs(counts[fam] - share * len(mine)) <= 1, (regime, fam, counts[fam])


def test_regime_follows_the_drift_round_and_the_held_out_set_is_one_request_per_withheld_cell():
    slots = plan_slots(PARAMS)
    for s in slots:
        if s.round is not None:
            assert s.regime is (Regime.R2 if s.round >= SPEC.drift.round else Regime.R1)
    held = [s for s in slots if s.round is None]
    train, withheld = grid_cells(PARAMS)
    assert len(held) == SPEC.stream.held_out and all(s.regime is Regime.R2 for s in held)
    assert sorted(s.cell for s in held) == sorted(withheld) and len(set(withheld)) == len(withheld)
    stream_cells = {s.cell for s in slots if s.round is not None}
    assert not stream_cells & set(withheld)                         # training never draws a withheld cell
    assert {f for f, _ in stream_cells} == set(SPEC.stream.families)
    assert {m for _, m in stream_cells} == set(SPEC.stream.modes)


def test_family_mix_holds_per_regime_and_modes_are_even_within_each_family():
    slots = [s for s in plan_slots(PARAMS) if s.round is not None]
    train, _ = grid_cells(PARAMS)
    for regime in (Regime.R1, Regime.R2):
        mine = [s for s in slots if s.regime is regime]
        counts = Counter(s.family for s in mine)
        for fam, share in zip(SPEC.stream.families, SPEC.stream.mix[regime.value], strict=True):
            assert abs(counts[fam] - share * len(mine)) <= 1
            modes = Counter(s.mode for s in mine if s.family == fam)
            allowed = {m for f, m in train if f == fam}
            assert set(modes) <= allowed
            if counts[fam] >= len(allowed):
                assert max(modes.values()) - min(modes[m] for m in allowed) <= 1


def test_an_incoherent_cell_is_never_drawn():
    dropped = (SPEC.stream.families[4], SPEC.stream.modes[2])
    slots = plan_slots(replace(PARAMS, incoherent=frozenset({dropped})))
    assert dropped not in {s.cell for s in slots}


def test_the_written_stream_carries_no_cell_while_the_journal_does(tmp_path):
    out = minter([draft(checks=(BEHAVIOUR, BEHAVIOUR_2))], fails_on_stub).mint_requests([R1_SLOT])
    assert out.stream[0].cell == R1_SLOT.cell
    goals_json.write_requests(tmp_path / "s.json", out.stream)
    assert "cell" not in json.loads((tmp_path / "s.json").read_text())[0]


def test_family_sequence_is_a_function_of_the_seed_alone():
    a, b = plan_slots(PARAMS), plan_slots(PARAMS)
    assert a == b
    other = plan_slots(replace(PARAMS, seed=PARAMS.seed + 1))
    assert [s.family for s in other] != [s.family for s in a]
    assert len({s.id for s in a}) == len(a)


def test_family_sequence_does_not_move_when_model_calls_fail_and_retry():
    slots = plan_slots(PARAMS)[:3]
    script = ["not json", "still not json",           # slot 1: draft refused after its re-ask
              draft(checks=(BEHAVIOUR, BEHAVIOUR_2)),               # slot 1: whole regeneration
              draft(text="a shopping list I can add items to", checks=(BEHAVIOUR, BEHAVIOUR_2)),
              draft(text="a mood log with one button per day", checks=(BEHAVIOUR, BEHAVIOUR_2))]
    m = minter(script, fails_on_stub)
    out = m.mint_requests(slots)
    assert [r.id for r in out.stream] == [s.id for s in slots]
    user_msgs = [msgs[1].content for _, msgs, tag in m.client.calls if ":reask" not in tag]
    asked_family = [next(f for f in SPEC.stream.families if f"app: {f}." in u) for u in user_msgs]
    assert asked_family == [slots[0].family, slots[0].family, slots[1].family, slots[2].family]


def test_quotas_sum_to_n_and_reject_a_mix_that_does_not_sum_to_one():
    assert quotas(70, [0.5, 0.15, 0.15, 0.1, 0.1]) == [35, 11, 10, 7, 7]
    assert sum(quotas(6, [0.15, 0.5, 0.15, 0.1, 0.1])) == 6
    with pytest.raises(ValueError, match="sum to 1"):
        quotas(10, [0.5, 0.4])


# --- request text, leakage, the constraint ----------------------------------------------------------------


def test_stored_text_never_carries_the_constraint_and_only_the_r2_prompt_states_it():
    m = minter([draft(checks=(BEHAVIOUR, BEHAVIOUR_2)), draft(checks=(BEHAVIOUR, BEHAVIOUR_2))], fails_on_stub)
    out = m.mint_requests([R1_SLOT, R2_SLOT])
    r1, r2 = out.stream
    c = SPEC.drift.constraint
    assert c not in r1.text and r1.checks.keys() == {Regime.R1}
    assert c not in r2.text and r2.checks.keys() == {Regime.R2}           # the pipeline appends it under R2
    # the R2 draft prompt states the constraint; the R1 prompt does not
    assert c in m.client.calls[1][1][1].content and c not in m.client.calls[0][1][1].content


def test_a_draft_that_includes_the_constraint_itself_is_rejected():
    with pytest.raises(Rejected, match="phone-screen"):
        validate_draft(json.loads(draft(text="a counter. " + SPEC.drift.constraint)), Regime.R2, PARAMS, DSL)


def test_a_draft_with_a_partial_copy_of_the_constraint_is_rejected():
    """Finding: the constraint guard matched only the full constraint, so a partial copy doubled it."""
    with pytest.raises(Rejected, match="phone-screen"):
        validate_draft(json.loads(draft(text="a tip splitter. It must work on a PHONE screen")),
                       Regime.R2, PARAMS, DSL)


@pytest.mark.parametrize(("contract", "where"), [
    ({"#add": "button that adds one", "#count": "display of the canvas game score"}, "#count"),
    ({"#add": "button that loads the data tools", "#count": "display of the running count"}, "#add"),
    ({"#add": "button that adds one", "#count": "display of the running count", "#timer-or-tracker":
      "display of the elapsed time"}, "#timer-or-tracker"),
])
def test_a_contract_role_or_id_naming_a_goal_family_is_rejected(contract, where):
    """Finding: contract roles were not screened for family names, so a label reached the worker."""
    with pytest.raises(Rejected, match=f"{where}.*kind label"):
        validate_draft(json.loads(draft(contract=contract)), Regime.R1, PARAMS, DSL)


def test_a_draft_naming_a_goal_family_is_rejected_and_no_family_reaches_the_written_request(tmp_path):
    fam = SPEC.stream.families[1]
    with pytest.raises(Rejected, match="kind label"):
        validate_draft(json.loads(draft(text=f"a {fam} for my budget")), Regime.R1, PARAMS, DSL)
    out = minter([draft(checks=(BEHAVIOUR, BEHAVIOUR_2))], fails_on_stub).mint_requests([R1_SLOT])
    goals_json.write_requests(tmp_path / "s.json", out.stream)
    written = (tmp_path / "s.json").read_text()
    assert word_hits(written, SPEC.stream.families, inflected=True) == []
    assert "R1" not in json.dumps([r["text"] for r in json.loads(written)])


def test_a_check_naming_an_element_outside_the_contract_is_rejected_as_unpassable():
    bad = {"steps": [{"click": "#add"}], "expect": {"text_changed": "#total"}}
    with pytest.raises(Rejected, match="not in the contract"):
        validate_draft(json.loads(draft(checks=(BEHAVIOUR, bad))), Regime.R1, PARAMS, DSL)
    no_id = {"steps": [{"click": "button"}], "expect": {"text_changed": "#count"}}
    with pytest.raises(Rejected, match="names no contract id"):
        validate_draft(json.loads(draft(checks=(BEHAVIOUR, no_id))), Regime.R1, PARAMS, DSL)


def test_a_canvas_check_with_no_input_step_is_rejected_as_unpassable():
    contract = {**COUNTER, "#board": "canvas where the rocks fall"}
    waits_only = {"steps": [{"goto": ""}, {"wait": 1000}], "expect": {"canvas_changed": "#board"}}
    with pytest.raises(Rejected, match="needs an input step"):
        validate_draft(json.loads(draft(checks=(BEHAVIOUR, waits_only), contract=contract)), Regime.R1, PARAMS, DSL)
    steered = {"steps": [{"focus": "#board"}, {"key": "ArrowLeft"}, {"wait": 500}],
               "expect": {"canvas_changed": "#board"}}
    validate_draft(json.loads(draft(checks=(BEHAVIOUR, steered), contract=contract)), Regime.R1, PARAMS, DSL)


def test_a_check_using_the_test_query_needs_the_request_text_to_state_it():
    timed = {"steps": [{"goto": SPEC.eval.test_query}, {"click": "#add"}, {"wait": 2500}],
             "expect": {"text_changed": "#count"}}
    with pytest.raises(Rejected, match="does not state"):
        validate_draft(json.loads(draft(checks=(BEHAVIOUR, timed))), Regime.R1, PARAMS, DSL)
    with pytest.raises(Rejected, match="timed request"):
        validate_draft(json.loads(draft(checks=(BEHAVIOUR, BEHAVIOUR_2), timed=True)), Regime.R1, PARAMS, DSL)
    text = f"a countdown that adds one when it ends. With {SPEC.eval.test_query} it takes 2 seconds."
    got = validate_draft(json.loads(draft(text=text, checks=(BEHAVIOUR, timed), timed=True)),
                         Regime.R1, PARAMS, DSL)
    assert [c.id for c in got.checks] == ["c1", "c2"]


def test_a_draft_outside_the_dsl_or_the_check_count_is_rejected():
    long_wait = {"steps": [{"wait": SPEC.eval.max_wait_ms + 1}], "expect": {"text_changed": "#count"}}
    with pytest.raises(Rejected, match="wait"):
        validate_draft(json.loads(draft(checks=(BEHAVIOUR, long_wait))), Regime.R1, PARAMS, DSL)
    too_many = (BEHAVIOUR,) * (SPEC.stream.checks_per_regime[1] + 1)
    with pytest.raises(Rejected, match="checks"):
        validate_draft(json.loads(draft(checks=too_many)), Regime.R1, PARAMS, DSL)
    with pytest.raises(Rejected, match="'#'"):
        validate_draft(json.loads(draft(contract={"add": "button"})), Regime.R1, PARAMS, DSL)


def test_held_out_requests_carry_r2_checks_no_round_and_no_stored_constraint():
    held = next(s for s in plan_slots(PARAMS) if s.round is None)
    out = minter([draft(checks=(BEHAVIOUR, BEHAVIOUR_2))], fails_on_stub).mint_requests([held])
    (r,) = out.held_out
    assert out.stream == [] and r.held_out and r.round is None
    assert r.checks.keys() == {Regime.R2} and SPEC.drift.constraint not in r.text


# --- the contract stub ------------------------------------------------------------------------------------


def test_stub_is_deterministic_holds_every_contract_id_and_no_script():
    contract = {"#board": "canvas where the game draws", "#name": "input for the player name",
                "#go": "button that starts", "#mode": "select of the speed", "#out": "display of the result"}
    html = stub_html(contract)
    assert html == stub_html(dict(contract))
    assert "<script" not in html.lower()
    for sel in contract:
        assert f'id="{sel[1:]}"' in html
    assert [element_kind(r) for r in contract.values()] == ["canvas", "input", "button", "select", "display"]


def test_draft_prompt_templates_name_no_goal_family_literally():
    templates = " ".join(p.read_text() for p in (ROOT / "prompts" / "goal_minter").glob("*.md"))
    assert word_hits(templates, SPEC.stream.families, inflected=True) == []


# --- check validation on the stub (scripted stub readings) ------------------------------------------------


def reading_by_expect(passing_verbs):
    def read(_rid, _contract, check, _regime):
        return StubReading(next(iter(check.expect)) in passing_verbs)
    return read


def test_a_check_passing_on_its_stub_is_regenerated_then_dropped():
    m = minter([draft(), regen(ON_STUB_2)], reading_by_expect({"exists", "visible"}))
    out = m.mint_requests([R1_SLOT])
    (r,) = out.stream
    assert [c.id for c in r.checks[Regime.R1]] == ["c1"]
    events = [(e["check"], e["event"]) for e in out.report]
    assert events == [("c2", "check_passed_on_stub"), ("c2", "check_regenerated"),
                      ("c2", "check_passed_on_stub"), ("c2", "check_dropped")]


def test_a_regenerated_check_that_fails_on_its_stub_is_kept():
    m = minter([draft(), regen(BEHAVIOUR_2)], reading_by_expect({"exists"}))
    (r,) = m.mint_requests([R1_SLOT]).stream
    assert [c.expect for c in r.checks[Regime.R1]] == [BEHAVIOUR["expect"], BEHAVIOUR_2["expect"]]


def test_a_request_with_no_surviving_check_is_regenerated_whole():
    script = [draft(checks=(ON_STUB, ON_STUB_2)), regen({"steps": [], "expect": {"visible": "#add"}}),
              regen({"steps": [], "expect": {"exists": "#add"}}),
              draft(text="a counter that goes up when I press add", checks=(BEHAVIOUR, BEHAVIOUR_2))]
    out = minter(script, reading_by_expect({"exists", "visible"})).mint_requests([R1_SLOT])
    (r,) = out.stream
    assert r.text == "a counter that goes up when I press add"
    assert "request_regenerated" in [e["event"] for e in out.report]


def test_a_position_with_no_request_after_every_regeneration_raises():
    drafts = [draft(checks=(ON_STUB, ON_STUB_2)), regen({"steps": [], "expect": {"visible": "#add"}}),
              regen({"steps": [], "expect": {"exists": "#add"}})] * (PARAMS.request_regenerations + 1)
    with pytest.raises(MintFailed):
        minter(drafts, reading_by_expect({"exists", "visible"})).mint_requests([R1_SLOT])


def test_a_check_failing_on_the_stub_before_its_expectation_is_kept_and_reported():
    def read(_rid, _c, check, _reg):
        return StubReading(False, "1280x800: step 0: TimeoutError")
    out = minter([draft(checks=(BEHAVIOUR, BEHAVIOUR_2))], read).mint_requests([R1_SLOT])
    assert out.stream[0].checks[Regime.R1][0].expect == BEHAVIOUR["expect"]
    assert out.report == [{"request": "G-001", "check": c, "event": "check_errored_on_stub",
                           "detail": "1280x800: step 0: TimeoutError"} for c in ("c1", "c2")]


# --- the real browser --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def chromium():
    from friday.adapters.playwright_browser import PlaywrightBrowser

    b = PlaywrightBrowser(SPEC.eval.action_timeout_ms, SPEC.eval.load_timeout_ms)
    yield b
    b.close()


def test_every_emitted_check_fails_on_its_stub_in_chromium_and_a_stub_passer_is_dropped(chromium, tmp_path):
    def write_stub(rid, html):
        return str(goals_json.write_stub(tmp_path, rid, html))

    bench = StubBench(chromium, write_stub, SPEC.eval.viewports)
    m = GoalMinter(ScriptedClient([draft(), regen(ON_STUB_2), draft(checks=(BEHAVIOUR, BEHAVIOUR_2))]),
                   BOOK, PARAMS, bench, DSL, no_sink)
    out = m.mint_requests([R1_SLOT, R2_SLOT])
    r1, r2 = out.stream
    assert [c.id for c in r1.checks[Regime.R1]] == ["c1"]
    assert ("c2", "check_dropped") in [(e["check"], e["event"]) for e in out.report]
    assert len(r2.checks[Regime.R2]) == 2
    for r in (r1, r2):
        regime = next(iter(r.checks))
        path = tmp_path / f"{r.id}.html"
        assert path.read_text() == out.stubs[r.id]
        for c in r.checks[regime]:
            for vp in SPEC.eval.viewports[regime.value]:
                assert not dsl.run_check(lambda vp=vp, path=path: chromium.page(str(path), vp), c).passed


def test_the_stub_bench_reads_a_check_as_passing_when_it_passes_at_any_viewport(chromium, tmp_path):
    bench = StubBench(chromium, lambda rid, html: str(goals_json.write_stub(tmp_path, rid, html)),
                      SPEC.eval.viewports)
    assert bench("G-1", COUNTER, Check("c", (), {"exists": "#count"}), Regime.R2).passed
    missing = bench("G-2", COUNTER, Check("c", ({"type": "#count", "text": "x"},), {"text_changed": "#count"}),
                    Regime.R1)
    assert not missing.passed and "step 0" in missing.detail


# --- the goals/*.json adapters -----------------------------------------------------------------------------


def _methods(proto):
    return {n: inspect.signature(f) for n, f in vars(proto).items() if callable(f) and not n.startswith("_")}


@pytest.mark.parametrize(("impl", "port"), [(JsonGoalSource, goals.GoalSource),
                                            (JsonTagVocabulary, goals.TagVocabulary)])
def test_json_adapters_match_their_ports(impl, port):
    for name, sig in _methods(port).items():
        assert hasattr(impl, name), f"{impl.__name__} lacks {name}"
        got = [p for p in inspect.signature(getattr(impl, name)).parameters if p != "self"]
        assert got == [p for p in sig.parameters if p != "self"]


def _minted():
    m = minter([draft(checks=(BEHAVIOUR, BEHAVIOUR_2))] * 3, fails_on_stub)
    held = next(s for s in plan_slots(PARAMS) if s.round is None)
    return m.mint_requests([R1_SLOT, R2_SLOT, held])


def test_goal_source_round_trips_requests_and_reads_the_held_out_file_only_when_asked(tmp_path):
    out = _minted()
    goals_json.write_requests(tmp_path / "stream.json", out.stream)
    src = JsonGoalSource(tmp_path / "stream.json", tmp_path / "missing_held_out.json")
    loop_view = [replace(r, cell=None) for r in out.stream]      # the stream the loop reads carries no cell
    assert list(src.round(1)) == [loop_view[0]]
    assert list(src.round(SPEC.drift.round)) == [loop_view[1]]
    with pytest.raises(FileNotFoundError):
        src.held_out()
    goals_json.write_requests(tmp_path / "held.json", out.held_out)
    held_view = [replace(r, cell=None) for r in out.held_out]
    assert list(JsonGoalSource(tmp_path / "stream.json", tmp_path / "held.json").held_out()) == held_view


def test_minted_requests_are_frozen_once_written(tmp_path):
    out = _minted()
    goals_json.write_requests(tmp_path / "stream.json", out.stream)
    with pytest.raises(FileExistsError):
        goals_json.write_requests(tmp_path / "stream.json", out.stream)


def test_tag_vocabulary_persists_refuses_renames_and_freezes_tags(tmp_path):
    path = tmp_path / "tags.json"
    v = JsonTagVocabulary(path)
    v.add(TagGloss("keyboard", "operated from the keyboard", 1))
    v.set_tags({"G-001": frozenset({"keyboard"})})
    again = JsonTagVocabulary(path)
    assert again.glosses() == [TagGloss("keyboard", "operated from the keyboard", 1)]
    assert again.tags_of("G-001") == frozenset({"keyboard"}) and again.tags_of("G-002") is None
    with pytest.raises(ValueError, match="never renamed"):
        again.add(TagGloss("keyboard", "another gloss", 2))
    with pytest.raises(ValueError, match="already tagged"):
        again.set_tags({"G-001": frozenset({"keyboard"})})
    with pytest.raises(ValueError, match="not in the vocabulary"):
        again.set_tags({"G-002": frozenset({"sound"})})


def test_goal_source_serves_tags_as_stored_so_far(tmp_path):
    out = _minted()
    goals_json.write_requests(tmp_path / "stream.json", out.stream)
    v = JsonTagVocabulary(tmp_path / "tags.json")
    src = JsonGoalSource(tmp_path / "stream.json", tmp_path / "held.json", v)
    assert src.round(1)[0].tags == frozenset()
    v.add(TagGloss("counter", "keeps a running count", 1))
    v.set_tags({"G-001": frozenset({"counter"})})
    assert src.round(1)[0].tags == frozenset({"counter"})


def test_request_text_helper_matches_the_minted_text():
    assert request_text("a", Regime.R1, "C.") == "a" and request_text("a", Regime.R2, "C.") == "a. C."
    assert request_text("a.", Regime.R2, "C.") == "a. C."


# --- review fixes: resume, family spellings, role kinds, reply forms, verb pick, --only ---------------------

REFUSED = json.dumps({"text": ""})
REFUSALS_PER_POSITION = (PARAMS.request_regenerations + 1) * (PARAMS.reasks + 1)
SMALL = replace(PARAMS, rounds=2, per_round=1, held_out=1, held_out_cells=1, drift_round=2)  # G-001, G-002, H-01


def test_a_failed_position_does_not_discard_the_minted_ones_and_a_rerun_resumes_from_the_journal(tmp_path):
    journal = goals_json.JsonlMintJournal(tmp_path / "mint_journal.jsonl")
    sunk: list[MintedGoals] = []
    first = GoalMinter(ScriptedClient([draft(checks=(BEHAVIOUR, BEHAVIOUR_2)), *[REFUSED] * REFUSALS_PER_POSITION,
                                       draft(text="a counter for the held-out set", checks=(BEHAVIOUR, BEHAVIOUR_2))]),
                       BOOK, SMALL, fails_on_stub, DSL, sunk.append, journal)
    with pytest.raises(MintFailed) as e:
        first.mint()
    assert sunk == []                                       # nothing frozen while a position is missing
    assert e.value.failed == ["G-002"]
    assert [r.id for r in e.value.partial.stream + e.value.partial.held_out] == ["G-001", "H-01"]
    assert ("G-002", "position_failed") in [(r["request"], r["event"]) for r in e.value.partial.report]
    assert sorted(journal.minted()) == ["G-001", "H-01"]
    again = GoalMinter(ScriptedClient([draft(text="a counter that resumes", checks=(BEHAVIOUR, BEHAVIOUR_2))]),
                       BOOK, SMALL, fails_on_stub, DSL, sunk.append, journal)
    again.mint()
    assert len(again.client.calls) == 1                     # only the failed position is re-minted
    (out,) = sunk
    assert [r.id for r in out.stream] == ["G-001", "G-002"] and [r.id for r in out.held_out] == ["H-01"]
    assert out.stream[0].text == "a tally counter that goes up by one each time I press add"
    assert set(out.stubs) == {"G-001", "G-002", "H-01"}


def test_a_journal_written_for_another_plan_is_refused_on_resume(tmp_path):
    journal = goals_json.JsonlMintJournal(tmp_path / "j.jsonl")
    GoalMinter(ScriptedClient([draft(checks=(BEHAVIOUR, BEHAVIOUR_2))]), BOOK, PARAMS, fails_on_stub, DSL,
               no_sink, journal).mint_requests([R1_SLOT])
    moved = replace(R1_SLOT, family=SPEC.stream.families[2])
    with pytest.raises(ValueError, match="does not match the plan"):
        GoalMinter(ScriptedClient([]), BOOK, PARAMS, fails_on_stub, DSL, no_sink, journal).mint_requests([moved])


@pytest.mark.parametrize("text", ["a few data tools for my budget", "two canvas games in one page",
                                  "a pair of text-tools for my notes", "my canvasgames collection"])
def test_a_draft_naming_a_family_in_the_plural_hyphenated_or_joined_is_rejected(text):
    with pytest.raises(Rejected, match="kind label"):
        validate_draft(json.loads(draft(text=text)), Regime.R1, PARAMS, DSL)


def test_a_contract_role_not_beginning_with_its_element_kind_is_rejected():
    contract = {"#city": "text box for the city", "#count": "display of the running count"}
    check = {"steps": [{"type": "#city", "text": "Paris"}], "expect": {"text_matches": "#count", "regex": "Paris"}}
    with pytest.raises(Rejected, match="element kind"):
        validate_draft(json.loads(draft(contract=contract, checks=(check, BEHAVIOUR))), Regime.R1, PARAMS, DSL)


def test_a_text_matches_check_listing_regex_before_the_verb_is_accepted():
    check = {"steps": [{"click": "#add"}], "expect": {"regex": r"\b1\b", "text_matches": "#count"}}
    d = validate_draft(json.loads(draft(checks=(BEHAVIOUR, check))), Regime.R1, PARAMS, DSL)
    assert d.checks[1].expect == check["expect"]


def test_every_minter_call_states_exactly_one_reply_form():
    m = minter([draft(), regen(BEHAVIOUR_2)], reading_by_expect({"exists"}))
    m.mint_requests([R1_SLOT])
    (_, draft_msgs, _), (_, regen_msgs, _) = m.client.calls
    for msgs, form in ((draft_msgs, '"checks"'), (regen_msgs, '{"check"')):
        joined = "\n".join(x.content for x in msgs)
        assert joined.count("Reply in this form") == 1 and form in joined.split("Reply in this form")[1]


def test_the_retry_block_states_the_reason_the_previous_draft_was_retried():
    refused_then_ok = minter([REFUSED, REFUSED, draft(checks=(BEHAVIOUR, BEHAVIOUR_2))], fails_on_stub)
    refused_then_ok.mint_requests([R1_SLOT])
    retried = refused_then_ok.client.calls[2][1][1].content
    assert BOOK.text("goal_minter/retry_refused.md").strip() in retried
    assert BOOK.text("goal_minter/retry_block.md").strip() not in retried
    script = [draft(checks=(ON_STUB, ON_STUB_2)), regen({"steps": [], "expect": {"visible": "#add"}}),
              regen({"steps": [], "expect": {"exists": "#add"}}), draft(checks=(BEHAVIOUR, BEHAVIOUR_2))]
    no_behaviour = minter(script, reading_by_expect({"exists", "visible"}))
    no_behaviour.mint_requests([R1_SLOT])
    retried = no_behaviour.client.calls[-1][1][1].content
    assert BOOK.text("goal_minter/retry_block.md").strip() in retried
    assert BOOK.text("goal_minter/retry_refused.md").strip() not in retried


def test_mint_goals_only_refuses_to_write_into_the_frozen_goal_paths():
    from friday.cli import main

    with pytest.raises(SystemExit, match="--only needs --goals-dir"):
        main(["--spec", str(ROOT / "run.yml"), "mint-goals", "--only", "G-001"])


def test_stored_r2_text_carries_no_drift_constraint_so_the_pipeline_appends_it_once():
    from friday.app.request_text import request_text as compose

    out = _minted()
    for r in (*out.stream, *out.held_out):
        assert " ".join(SPEC.drift.constraint.split()) not in " ".join(r.text.split())
        for regime in r.checks:
            assert compose(r, regime, SPEC.drift.constraint).count("phone screen") == (regime is Regime.R2)
