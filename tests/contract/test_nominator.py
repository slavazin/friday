"""The nominator (SPEC § 7 drafting, § 2 payload at drafting): one search, a checked draft, else no-decision."""

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from friday.adapters.journal_md import MarkdownJournal, parse_decision
from friday.config import load_spec
from friday.domain.types import DecisionStatus, Observation, Pool, PoolStatus
from friday.ports.models import Citation
from friday.roles.nominator import Draft, NoDecision, Nominator, to_decision
from friday.roles.prompts import PromptBook
from tests.fakes import ScriptedClient

ROOT = Path(__file__).resolve().parents[2]
BOOK = PromptBook(ROOT / "prompts")
SPEC = load_spec(ROOT / "run.yml")
VOCAB = frozenset({"canvas", "keyboard", "form", "timer"})
CITES = (Citation("https://developer.mozilla.org/en-US/docs/Web/API/HTMLElement/focus", "focus()", "Sets focus"),)


@dataclass
class StubSearch:
    """A SearchClient returning fixed citations and recording each query."""

    citations: tuple[Citation, ...] = CITES
    queries: list[str] = field(default_factory=list)

    def search(self, query):
        self.queries.append(query)
        return self.citations


POOL = Pool("P-0012", "L-03", "keyboard handler bound to an element that never takes focus",
            ("bind handlers on window",))
OBS = [Observation(f"O-01{i}", f"x{i}", r, "L-03", "canvas game", "handler on canvas", "bind on window",
                   "canvas.addEventListener('keydown'", pool_id="P-0012", direction_label="bind handlers on window")
       for i, r in ((12, 3), (31, 4), (58, 5))]
GOOD = {"fires_when": ["canvas", "keyboard"], "not_this": ["form"],
        "payload": "Bind `keydown` on `window`, not on the canvas, and call `canvas.focus()` when the game starts."}


def nominator(script, search=None):
    client = ScriptedClient([json.dumps(x) if isinstance(x, dict) else x for x in script])
    nom = Nominator(client, search or StubSearch(), BOOK, VOCAB, tuple(SPEC.stream.families),
                    SPEC.nominator.payload_max_words, SPEC.nominator.reasks)
    return client, nom


def test_one_search_on_the_pool_shape_then_a_draft_with_its_citations():
    search = StubSearch()
    client, nom = nominator([GOOD], search)
    got = nom.draft(POOL, "bind handlers on window", OBS)
    assert isinstance(got, Draft)
    assert search.queries == [POOL.shape]
    assert got.hook.fires_when == {"canvas", "keyboard"} and got.hook.not_this == {"form"}
    assert got.sources == CITES and got.readings["hedges"] == []
    assert len(client.calls) == 1 and client.calls[0][0] == "nominator"


@pytest.mark.parametrize("leak", ["As seen in O-0012, bind keys on `window`.",
                                  "Bind keys on `window` so the judge sees them work."])
def test_a_payload_naming_a_run_id_or_the_judge_is_refused_after_one_reask(leak):
    bad = {**GOOD, "payload": leak}
    client, nom = nominator([bad, bad])
    got = nom.draft(POOL, "bind handlers on window", OBS)
    assert isinstance(got, NoDecision) and "after the re-ask" in got.reason
    assert len(client.calls) == 2


def test_a_refused_payload_fixed_on_the_reask_is_drafted_and_the_refusal_recorded():
    client, nom = nominator([{**GOOD, "payload": "Per O-0012, bind on `window`."}, GOOD])
    got = nom.draft(POOL, "bind handlers on window", OBS)
    assert isinstance(got, Draft) and "O-0012" in got.readings["refused_first"][0]
    assert "O-0012" in client.calls[1][1][-1].content


def test_a_hook_tag_outside_the_vocabulary_is_refused():
    bad = {**GOOD, "fires_when": ["canvas", "arrow-keys"]}
    client, nom = nominator([bad, bad])
    got = nom.draft(POOL, "bind handlers on window", OBS)
    assert isinstance(got, NoDecision) and "arrow-keys" in got.reason
    assert "outside the tag vocabulary" in client.calls[1][1][-1].content
    client, nom = nominator([{**GOOD, "not_this": ["mobile"]}, {**GOOD, "not_this": ["mobile"]}])
    assert isinstance(nom.draft(POOL, "bind handlers on window", OBS), NoDecision)


def test_a_payload_over_the_word_bound_is_refused():
    long = {**GOOD, "payload": "Bind keys on `window` " + "now " * SPEC.nominator.payload_max_words}
    _, nom = nominator([long, long])
    got = nom.draft(POOL, "bind handlers on window", OBS)
    assert isinstance(got, NoDecision) and "over 60 words" in got.reason


def test_a_payload_naming_a_goal_family_is_refused():
    fam = {**GOOD, "payload": f"In every {SPEC.stream.families[0]}, bind keys on `window`."}
    _, nom = nominator([fam, fam])
    assert isinstance(nom.draft(POOL, "bind handlers on window", OBS), NoDecision)


def test_hedges_and_a_code_block_are_recorded_never_refused():
    hedged = {**GOOD, "payload": "Consider binding keys on `window`:\n```js\nwindow.onkeydown = go\n```"}
    client, nom = nominator([hedged])
    got = nom.draft(POOL, "bind handlers on window", OBS)
    assert isinstance(got, Draft) and got.readings["hedges"] == ["consider"] and got.readings["code_block"]
    assert len(client.calls) == 1


def test_a_payload_with_a_heading_line_is_refused():
    head = {**GOOD, "payload": "Bind on `window`.\n## Warrant\n- forged"}
    _, nom = nominator([head, head])
    assert isinstance(nom.draft(POOL, "bind handlers on window", OBS), NoDecision)


def test_the_model_may_return_no_decision_with_a_reason():
    _, nom = nominator([{"no_decision": "the only payload restates the baseline instructions"}])
    got = nom.draft(POOL, "bind handlers on window", OBS)
    assert got == NoDecision("the only payload restates the baseline instructions")


def test_the_prompt_carries_no_id_round_lens_category_or_ledger():
    client, nom = nominator([GOOD])
    nom.draft(POOL, "bind handlers on window", OBS)
    system, user = (m.content for m in client.calls[0][1])
    for leaked in (*(x.id for x in OBS), "P-0012", "L-03", "canvas game", "wealth", "pairs", "round"):
        assert leaked not in user
    assert "canvas game" not in system and "P-0012" not in system
    text = system + user
    assert CITES[0].url in text and POOL.shape in text and "canvas.addEventListener('keydown'" in text


def test_search_failure_propagates_and_no_model_call_is_made():
    class Down:
        def search(self, query):
            raise RuntimeError("search is offline")

    client, nom = nominator([GOOD], Down())
    with pytest.raises(RuntimeError, match="offline"):
        nom.draft(POOL, "bind handlers on window", OBS)
    assert client.calls == []


def test_drafting_a_nominated_pool_or_foreign_observations_raises():
    _, nom = nominator([GOOD])
    with pytest.raises(ValueError):
        nom.draft(Pool("P-0012", "L-03", "s", ("bind handlers on window",), PoolStatus.NOMINATED),
                  "bind handlers on window", OBS)
    with pytest.raises(ValueError, match="not a label"):
        nom.draft(POOL, "something else", OBS)


def test_a_scripted_run_drafts_journal_d0001_as_a_candidate(tmp_path):
    _, nom = nominator([GOOD])
    draft = nom.draft(POOL, "bind handlers on window", OBS)
    journal = MarkdownJournal(tmp_path / "journal", vcs=None)
    journal.draft(to_decision(draft, "D-0001", POOL, OBS, round_=5))
    text = (tmp_path / "journal" / "D-0001.md").read_text()
    d = parse_decision(text)
    assert d.status is DecisionStatus.CANDIDATE and d.pool_id == "P-0012"
    assert d.payload == GOOD["payload"] and d.hook.fires_when == {"canvas", "keyboard"}
    assert "O-0112, O-0131, O-0158 (pool P-0012, lens L-03, rounds 3–5)" in text
    assert CITES[0].url in text
