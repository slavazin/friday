"""Blinding and candidate-indistinguishability (SPEC § 2 Checks)."""

from pathlib import Path

from friday.algorithms.language import blinding_hits
from friday.domain.types import DecisionStatus
from friday.roles.prompts import PromptBook
from friday.roles.worker import extract_html, missing_contract_ids, render_composition, repair_message
from tests.unit.builders import dec

ROOT = Path(__file__).resolve().parents[2]
BOOK = PromptBook(ROOT / "prompts")


def test_worker_templates_name_no_blinded_word():
    templates = [p.read_text() for p in (ROOT / "prompts" / "worker").glob("*.md")]
    hits = {w for t in templates for w in blinding_hits(t, BOOK.blinding["worker"])}
    assert hits == set()


def test_blinding_scan_reads_templates_not_request_text():
    msgs = render_composition(BOOK, "A game with a #score counter. Use ?test=1 to test it.", [])
    template_only = render_composition(BOOK, "", [])
    assert blinding_hits(template_only[1].content + template_only[0].content, BOOK.blinding["worker"]) == []
    assert "score" in msgs[1].content


def test_candidate_renders_exactly_as_admitted():
    cand = dec("D-0009", DecisionStatus.CANDIDATE, payload="Bind `keydown` on `window`.")
    adm = dec("D-0009", DecisionStatus.ADMITTED, payload="Bind `keydown` on `window`.")
    other = dec("D-0002")
    a = render_composition(BOOK, "a timer", [other, cand])
    b = render_composition(BOOK, "a timer", [adm, other])
    assert a == b
    assert "D-0009" not in a[1].content and "candidate" not in a[1].content.lower()


def test_repair_turn_shows_errors_and_ids_never_checks():
    m = repair_message(BOOK, ["TypeError: x is undefined"], ["start"])
    assert "#start" in m.content and "TypeError" in m.content
    assert blinding_hits(m.content, BOOK.blinding["worker"]) == []


def test_html_extraction_and_missing_ids():
    html = extract_html("Here:\n```html\n<!doctype html><div id='start'></div>\n```")
    assert html.startswith("<!doctype html>")
    assert extract_html("no file") is None
    assert missing_contract_ids(html, {"#start": "", "#score": ""}) == ["score"]


def test_blinding_scan_catches_inflected_forms():
    text = "reviewed by judges and the scores of both candidates in each pair; the losses across arms and exposures"
    assert set(blinding_hits(text, BOOK.blinding["worker"])) >= {
        "judge", "score", "candidate", "pair", "loss", "arm", "exposure"}
    assert blinding_hits("a checkbox and a scoreboard", ["check", "score"]) == []
