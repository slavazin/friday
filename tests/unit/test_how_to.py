"""The how-to repair chain and H (SPEC § 5): rungs per fixture, totality, and the ways H could be gamed."""

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from friday.domain.types import CheckResult, HowToRung
from friday.eval import how_to

APPS = Path(__file__).resolve().parents[1] / "data" / "apps"
CONTRACT = {"#inc": "adds one", "#count": "shows the count"}
VP = (1280, 800)


def parse(html, contract=CONTRACT, max_controls=12):
    return how_to.parse(html, contract, max_controls, "?test=1", 500)


def app(name):
    return (APPS / name).read_text()


def all_pass(p):
    return [CheckResult(c.id, True) for c in p.declared]


def h(p, results=None, contract=CONTRACT, penalty=0.1):
    return how_to.score(p, all_pass(p) if results is None else results, contract, penalty, "P1", VP).H


def doc(json_text, attrs='type="application/json" id="how-to-data"'):
    return f"<!doctype html><body><script {attrs}>{json_text}</script></body>"


ENTRY = '{"id": "#inc", "does": "adds", "action": {"click": "#inc"}, "effect": {"text_changed": "#count"}}'


@pytest.mark.parametrize(("name", "rung", "declared", "h_all_pass"), [
    ("counter.html", HowToRung.STRICT, 1, 1.0),
    ("counter_trailing_comma.html", HowToRung.REPAIRED, 1, 0.9),
    ("counter_truncated.html", HowToRung.SALVAGED, 1, 0.9),
    ("counter_no_howto.html", HowToRung.NONE, 0, 0.0),
])
def test_each_fixture_reads_at_its_rung(name, rung, declared, h_all_pass):
    p = parse(app(name))
    assert p.rung is rung
    assert len(p.declared) == declared
    assert h(p) == pytest.approx(h_all_pass)


def test_declared_control_compiles_to_goto_action_wait_effect():
    (c,) = parse(app("counter.html")).declared
    assert c.steps == ({"goto": "?test=1"}, {"click": "#inc"}, {"wait": 500})
    assert c.expect == {"text_changed": "#count"}


def test_declared_ids_include_entry_and_selectors_and_contract_gaps_are_listed():
    p = parse(app("counter.html"), contract={**CONTRACT, "#reset": "resets"})
    assert p.declared_ids == ("#inc", "#count")
    assert p.undeclared_contract_ids == ("#reset",)


def test_strict_json_with_wrong_shape_falls_through_to_rung_four():
    for text in ("[" + ENTRY + "]", '{"summary": "x"}', '{"controls": {"a": 1}}', "42", "null"):
        assert parse(doc(text)).rung is HowToRung.NONE


def test_controls_list_at_rung_one_with_no_entries_scores_zero():
    p = parse(doc('{"summary": "x", "controls": []}'))
    assert p.rung is HowToRung.STRICT and h(p) == 0.0


def test_parse_never_raises_on_hostile_input():
    for text in ("[" * 100_000, '{"controls": [' + "{" * 50_000, "1" * 10_000, "\x00�", "</script>"):
        assert parse(doc(text)).rung in set(HowToRung)
    assert parse("").rung is HowToRung.NONE


@settings(max_examples=200, deadline=None)
@given(st.text())
def test_parse_is_total_on_arbitrary_script_text(text):
    p = parse(doc(text))
    assert p.rung in set(HowToRung)


def test_script_found_whatever_the_attribute_order_and_quotes():
    for attrs in ("id='how-to-data' type='application/json'", "ID=how-to-data TYPE=application/json",
                  'data-x="1" id="how-to-data"'):
        assert parse(doc('{"controls": [' + ENTRY + "]}", attrs)).rung is HowToRung.STRICT
    assert parse(doc('{"controls": [' + ENTRY + "]}", 'id="other"')).rung is HowToRung.NONE


def test_declaring_nothing_scores_zero():
    assert h(parse(doc('{"summary": "nothing", "controls": []}'))) == 0.0


def test_declaring_ids_outside_the_contract_earns_no_coverage():
    e = '{"id": "#x", "action": {"click": "#x"}, "effect": {"text_changed": "#y"}}'
    p = parse(doc('{"controls": [' + e + "]}"))
    assert p.declared and h(p) == 0.0


def test_partial_coverage_lowers_h():
    contract = {**CONTRACT, "#reset": "", "#total": ""}
    assert h(parse(app("counter.html"), contract=contract), contract=contract) == pytest.approx(0.5)


def test_invalid_entries_count_as_failed_controls():
    bad = '{"id": "#inc", "action": {"wait": 100}, "effect": {"text_changed": "#count"}}'
    exists = '{"id": "#inc", "action": {"click": "#inc"}, "effect": {"exists": "#count"}}'
    p = parse(doc('{"controls": [' + ",".join([ENTRY, bad, exists]) + "]}"))
    assert len(p.declared) == 1 and p.invalid_entries == 2
    assert h(p) == pytest.approx(1 / 3)


def test_salvage_counts_decoded_but_invalid_entries_too():
    bad = '{"id": "#inc", "action": {"goto": "?x"}, "effect": {"visible": "#count"}}'
    p = parse(doc('{"controls": [' + ENTRY + "," + bad + ', {"id": "#co'))
    assert p.rung is HowToRung.SALVAGED and p.invalid_entries == 1
    assert h(p) == pytest.approx(0.5 * 0.9)


def test_typing_into_a_field_claimed_as_its_own_change_is_invalid():
    e = '{"id": "#inc", "action": {"type": "#inc", "text": "a"}, "effect": {"text_changed": "#inc"}}'
    p = parse(doc('{"controls": [' + e + "]}"))
    assert p.declared == () and p.invalid_entries == 1


def test_failed_controls_lower_h():
    p = parse(app("counter.html"))
    assert h(p, [CheckResult(c.id, False, "expect") for c in p.declared]) == 0.0


def test_only_max_controls_entries_are_read():
    p = parse(doc('{"controls": [' + ",".join([ENTRY] * 20) + "]}"), max_controls=12)
    assert len(p.declared) + p.invalid_entries == 12


def test_score_refuses_mismatched_results_and_empty_contract():
    p = parse(app("counter.html"))
    with pytest.raises(ValueError):
        how_to.score(p, [], CONTRACT, 0.1, "P1", VP)
    with pytest.raises(ValueError):
        how_to.score(p, all_pass(p), {}, 0.1, "P1", VP)
