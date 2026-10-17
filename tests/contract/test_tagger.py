"""The tagger and its minting bar (SPEC § 4 Tags), over a scripted client."""

import json
from pathlib import Path

import pytest

from friday.adapters.memory import MemoryGoalSource, MemoryTagVocabulary
from friday.algorithms.language import blinding_hits
from friday.app.goals import build_tagger, tag_held_out, tag_round
from friday.config import load_spec
from friday.domain.types import Check, Regime, Request, TagGloss
from friday.roles.prompts import PromptBook
from friday.roles.tagger import Tagger, TaggingRefused, near_duplicate
from tests.fakes import ScriptedClient

ROOT = Path(__file__).resolve().parents[2]
SPEC = load_spec(ROOT / "run.yml")
BOOK = PromptBook(ROOT / "prompts")
MAX_NEW = SPEC.stream.max_new_tags


def request(id_, text, round_=1, held_out=False):
    return Request(id_, text, frozenset(), {"#start": "button"},
                   {Regime.R1: (Check("c1", (), {"exists": "#start"}),)}, held_out=held_out,
                   round=None if held_out else round_)


SNAKE = request("G-001", "a snake game I steer with the arrow keys")
TIMER = request("G-002", "a pomodoro timer that plays a sound when a session ends")
VOCAB = [TagGloss("keyboard", "operated from the keyboard", 1), TagGloss("timer", "counts time down or up", 1)]


def reply(tags, new=()):
    return json.dumps({"tags": tags, "new": [{"tag": t, "gloss": f"requests with {t}"} for t in new]})


def tagger(script):
    return build_tagger(SPEC, ScriptedClient(list(script)), BOOK)


def test_tagger_reuses_an_existing_tag_and_mints_none_when_one_fits():
    t = tagger([reply({"G-001": ["keyboard", "canvas"], "G-002": ["timer", "sound"]}, new=["canvas", "sound"])])
    tags, new = t.tag([SNAKE, TIMER], VOCAB, 3, MAX_NEW)
    assert tags["G-001"] == {"keyboard", "canvas"} and tags["G-002"] == {"timer", "sound"}
    assert [(g.tag, g.minted_round) for g in new] == [("canvas", 3), ("sound", 3)]
    user = t.client.calls[0][1][1].content
    assert "- keyboard: operated from the keyboard" in user and "G-001: a snake game" in user


def test_tagger_mints_up_to_the_bar():
    new = [f"trait{i}" for i in range(MAX_NEW)]
    tags = {"G-001": new[: len(new) // 2] + ["keyboard"], "G-002": new[len(new) // 2:] + ["timer"]}
    _, minted = tagger([reply(tags, new)]).tag([SNAKE, TIMER], VOCAB, 2, MAX_NEW)
    assert len(minted) == MAX_NEW


def test_a_reply_minting_over_the_bar_is_re_asked_once_then_refused():
    over = [f"trait{i}" for i in range(MAX_NEW + 1)]
    bad = reply({"G-001": over[:2] + ["keyboard"], "G-002": over[2:]}, over)
    t = tagger([bad, bad])
    with pytest.raises(TaggingRefused, match="at most"):
        t.tag([SNAKE, TIMER], VOCAB, 2, MAX_NEW)
    assert len(t.client.calls) == SPEC.json.reasks + 1


def test_a_reply_over_the_bar_then_within_it_is_accepted():
    over = [f"trait{i}" for i in range(MAX_NEW + 1)]
    good = reply({"G-001": ["keyboard", "canvas"], "G-002": ["timer", "sound"]}, ["canvas", "sound"])
    tags, _ = tagger([reply({"G-001": over[:2], "G-002": over[2:]}, over), good]).tag(
        [SNAKE, TIMER], VOCAB, 2, MAX_NEW)
    assert tags["G-002"] == {"timer", "sound"}


@pytest.mark.parametrize(("new", "why"), [
    (["timers"], "renames or merges"), (["key-board"], "renames or merges"), (["keyboard"], "exists"),
])
def test_a_near_duplicate_or_re_glossed_tag_is_refused(new, why):
    bad = reply({"G-001": [new[0], "keyboard"] if new[0] != "keyboard" else ["keyboard", "arrows"],
                 "G-002": ["timer", "sound"]}, new)
    with pytest.raises(TaggingRefused, match=why):
        tagger([bad, bad]).tag([SNAKE, TIMER], VOCAB, 2, MAX_NEW)


def test_two_new_tags_naming_one_trait_are_refused():
    bad = reply({"G-001": ["keyboard", "arrow"], "G-002": ["timer", "arrows"]}, ["arrow", "arrows"])
    with pytest.raises(TaggingRefused, match="name one trait"):
        tagger([bad, bad]).tag([SNAKE, TIMER], VOCAB, 2, MAX_NEW)


def test_near_duplicate_reads_spelling_not_meaning():
    assert near_duplicate("timer", "timers") and near_duplicate("drag-drop", "dragdrop")
    assert near_duplicate("entry", "entries") and not near_duplicate("drag", "drop")


@pytest.mark.parametrize(("tags", "why"), [
    ({"G-001": ["Keyboard", "x"], "G-002": ["timer", "keyboard"]}, "neither existing"),
    ({"G-001": ["keyboard"], "G-002": ["timer", "keyboard"]}, "distinct tags"),
    ({"G-001": ["keyboard", "timer"]}, "cover exactly"),
    ({"G-001": ["keyboard", "timer"], "G-002": ["timer", "unknown"]}, "neither existing"),
])
def test_a_reply_outside_the_form_is_refused(tags, why):
    with pytest.raises(TaggingRefused, match=why):
        tagger([reply(tags), reply(tags)]).tag([SNAKE, TIMER], VOCAB, 2, MAX_NEW)


def test_a_new_tag_must_be_lowercase_words_and_given_to_a_request():
    bad_form = reply({"G-001": ["keyboard", "Arrow Keys"], "G-002": ["timer", "keyboard"]}, ["Arrow Keys"])
    with pytest.raises(TaggingRefused, match="lowercase"):
        tagger([bad_form, bad_form]).tag([SNAKE, TIMER], VOCAB, 2, MAX_NEW)
    unused = reply({"G-001": ["keyboard", "timer"], "G-002": ["timer", "keyboard"]}, ["sound"])
    with pytest.raises(TaggingRefused, match="given to no request"):
        tagger([unused, unused]).tag([SNAKE, TIMER], VOCAB, 2, MAX_NEW)


def test_a_tag_spelling_a_goal_family_is_refused():
    fam = SPEC.stream.families[0].replace(" ", "-")
    bad = reply({"G-001": ["keyboard", fam], "G-002": ["timer", "keyboard"]}, [fam])
    with pytest.raises(TaggingRefused, match="kind label"):
        tagger([bad, bad]).tag([SNAKE, TIMER], VOCAB, 2, MAX_NEW)


def test_held_out_tagging_mints_nothing():
    held = [request("H-01", "a budget table with totals per month", held_out=True)]
    t = tagger([])
    with pytest.raises(ValueError, match="mints nothing"):
        t.tag(held, VOCAB, SPEC.run.rounds, 1)
    vocab = MemoryTagVocabulary(list(VOCAB))
    source = MemoryGoalSource(held)
    minting = reply({"H-01": ["table", "keyboard"]}, ["table"])
    with pytest.raises(TaggingRefused, match="at most 0"):
        tag_held_out(tagger([minting, minting]), source, vocab, SPEC.run.rounds)
    assert vocab.glosses() == VOCAB and vocab.tags_of("H-01") is None
    tags = tag_held_out(tagger([reply({"H-01": ["keyboard", "timer"]})]), source, vocab, SPEC.run.rounds)
    assert tags["H-01"] == {"keyboard", "timer"} and vocab.glosses() == VOCAB


def test_the_minting_bar_is_per_round_across_calls():
    earlier = [TagGloss(f"t{i}", "minted earlier this round", 4) for i in range(MAX_NEW - 1)]
    vocab = MemoryTagVocabulary(list(VOCAB) + earlier)
    late = request("G-031", "a drum pad I play with the keyboard", round_=4)
    source = MemoryGoalSource([late])
    two_new = reply({"G-031": ["sound", "pads", "keyboard"]}, ["sound", "pads"])
    t = tagger([two_new, two_new])
    with pytest.raises(TaggingRefused, match="at most 1"):
        tag_round(t, source, vocab, 4, MAX_NEW)
    assert "at most 1 new tags" in t.client.calls[0][1][1].content
    tags, new = tag_round(tagger([reply({"G-031": ["sound", "keyboard"]}, ["sound"])]), source, vocab, 4, MAX_NEW)
    assert [g.tag for g in new] == ["sound"] and vocab.tags_of("G-031") == {"sound", "keyboard"}


def test_tags_are_frozen_once_given():
    vocab = MemoryTagVocabulary(list(VOCAB))
    source = MemoryGoalSource([SNAKE])
    tag_round(tagger([reply({"G-001": ["keyboard", "timer"]})]), source, vocab, 1, MAX_NEW)
    with pytest.raises(ValueError, match="already tagged"):
        tag_round(tagger([]), source, vocab, 1, MAX_NEW)
    tagged = Request(**{**SNAKE.__dict__, "tags": frozenset({"keyboard"})})
    with pytest.raises(ValueError, match="frozen"):
        tagger([]).tag([tagged], VOCAB, 1, MAX_NEW)


def test_tagger_templates_pass_the_tagger_blinding_row_and_name_no_goal_family():
    templates = " ".join(p.read_text() for p in (ROOT / "prompts" / "tagger").glob("*.md"))
    assert blinding_hits(templates, BOOK.blinding["tagger"]) == []
    assert blinding_hits(templates, SPEC.stream.families) == []


def test_the_rendered_tagger_prompt_carries_no_family_regime_or_constraint_label():
    t = tagger([reply({"G-001": ["keyboard", "timer"]})])
    t.tag([SNAKE], VOCAB, 1, MAX_NEW)
    _, msgs, _ = t.client.calls[0]
    scaffold = msgs[0].content + msgs[1].content.replace(SNAKE.text, "")
    assert blinding_hits(scaffold, BOOK.blinding["tagger"]) == []
    assert blinding_hits(scaffold, SPEC.stream.families) == []
    assert "R1" not in scaffold and "R2" not in scaffold


def test_tagger_family_list_is_never_sent():
    t = Tagger(ScriptedClient([reply({"G-001": ["keyboard", "timer"]})]), BOOK, (2, 4), 1, ("secret family",))
    t.tag([SNAKE], VOCAB, 1, MAX_NEW)
    assert all("secret family" not in m.content for m in t.client.calls[0][1])


@pytest.mark.parametrize("make", [lambda f: f.replace(" ", "-") + "s", lambda f: f.replace(" ", "") + "s",
                                  lambda f: f.replace(" ", "-")])
def test_a_plural_hyphenated_or_joined_family_tag_is_refused(make):
    for fam in ("canvas game", "data tool", "text tool"):
        assert fam in SPEC.stream.families
        bad_tag = make(fam)
        bad = reply({"G-001": ["keyboard", bad_tag], "G-002": ["timer", "keyboard"]}, [bad_tag])
        with pytest.raises(TaggingRefused, match="kind label"):
            tagger([bad, bad]).tag([SNAKE, TIMER], VOCAB, 2, MAX_NEW)
