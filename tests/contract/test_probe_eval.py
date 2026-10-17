"""Probe suite, how-to controls and the Evaluator over FakePage (SPEC § 5, § 10)."""

from contextlib import contextmanager

import pytest

from friday.algorithms.loss import LossModel
from friday.domain.types import Arm, Check, Composition, ControlKind, HowToRung, Pass, Regime, Request
from friday.eval import how_to, probe
from friday.eval.evaluate import Evaluator
from tests.fakes import FakeDom, FakePage, page_factory

VP = (1280, 800)


def inc(d):
    d.texts["#count"] = str(int(d.texts["#count"]) + 1)


def counter_dom(**kw):
    base = dict(texts={"#inc": "Add", "#count": "0"}, visible={"#inc", "#count"}, on_click={"#inc": inc},
                kinds={"#inc": ControlKind.BUTTON})
    base.update(kw)
    return FakeDom(**base)


def run_probe(dom, declared=("#inc",)):
    return probe.run(FakePage(dom), VP, "P1", declared, 12, 500, "abc 123")


def test_a_control_that_changes_nothing_scores_zero():
    r = run_probe(counter_dom(on_click={}))
    assert (r.controls, r.responsive) == (1, 0)
    assert pytest.approx(2 / 3) == r.P


def test_a_responsive_control_scores_one():
    r = run_probe(counter_dom())
    assert r.responsive == 1 and pytest.approx(1.0) == r.P and r.responsive_ids == ("#inc",)


def test_no_controls_is_a_zero_share_never_not_applicable():
    r = run_probe(FakeDom(texts={"#count": "0"}))
    assert r.controls == 0 and pytest.approx(2 / 3) == r.P


def test_a_control_whose_action_throws_is_unresponsive():
    def boom(d):
        raise RuntimeError("handler threw")
    r = run_probe(counter_dom(on_click={"#inc": boom}))
    assert r.responsive == 0


def test_a_page_that_changes_by_itself_makes_no_control_look_responsive():
    dom = counter_dom(on_click={}, ticking={"#count"})
    assert run_probe(dom).responsive == 0
    dom = counter_dom(ticking={"#count"})       # even a working control cannot be read on a moving page
    assert run_probe(dom).responsive == 0


def test_a_stop_button_that_brings_a_ticking_page_to_rest_is_responsive():
    """Finding: the idle pre-check never operated a control once a timer ran, so stop and reset scored 0."""
    def start(d):
        d.ticking.add("#time")

    def stop(d):
        d.ticking.discard("#time")

    def reset(d):
        d.texts["#time"] = "0"
    dom = FakeDom(texts={"#start": "", "#stop": "", "#reset": "", "#time": "0"},
                  on_click={"#start": start, "#stop": stop, "#reset": reset},
                  kinds={"#start": ControlKind.BUTTON, "#stop": ControlKind.BUTTON, "#reset": ControlKind.BUTTON})
    r = run_probe(dom, ())
    assert r.responsive_ids == ("#start", "#stop", "#reset")


def test_typing_into_a_field_with_no_effect_is_unresponsive_and_an_effect_counts():
    dom = FakeDom(texts={"#name": "", "#greet": ""}, kinds={"#name": ControlKind.TEXT})
    assert run_probe(dom, ()).responsive == 0
    dom.on_type["#name"] = lambda d: d.texts.update({"#greet": d.texts["#name"]})
    assert run_probe(dom, ()).responsive == 1


def test_console_errors_fail_the_error_probe():
    r = run_probe(counter_dom(errors=["TypeError: x"]))
    assert pytest.approx(2 / 3) == r.P


def test_silent_counts_responsive_undeclared_controls_including_idless_ones():
    dom = counter_dom(texts={"#inc": "Add", "#count": "0", "b2": ""}, on_click={"#inc": inc, "b2": inc},
                      kinds={"#inc": ControlKind.BUTTON, "b2": ControlKind.BUTTON})
    assert run_probe(dom, declared=()).silent == 2
    assert run_probe(dom, declared=("#inc",)).silent == 1


def test_probe_is_deterministic_for_the_same_page():
    assert run_probe(counter_dom()) == run_probe(counter_dom())


def test_canvas_control_reads_arrow_keys():
    dom = FakeDom(canvas={"#c": 0}, kinds={"#c": ControlKind.CANVAS},
                  on_key={"ArrowUp": lambda d: d.canvas.update({"#c": 7})})
    assert run_probe(dom, ()).responsive == 1


def test_missing_contract_ids_reads_the_page():
    page = FakePage(counter_dom())
    assert probe.missing_contract_ids(page, {"#inc": "", "#reset": "", "total": ""}) == ["#reset", "#total"]


def test_how_to_control_fails_an_effect_that_holds_without_the_action():
    text = ('{"controls": [{"id": "#inc", "action": {"click": "#inc"}, "effect": {"visible": "#count"}},'
            ' {"id": "#inc", "action": {"click": "#inc"}, "effect": {"text_changed": "#count"}}]}')
    p = how_to.parse(f'<script id="how-to-data">{text}</script>', {"#inc": "", "#count": ""}, 12, "?test=1", 500)
    results = how_to.run(page_factory(counter_dom), p)
    assert [r.passed for r in results] == [False, True]
    assert results[0].failing_step.startswith("control")


def test_how_to_text_changed_on_a_ticking_element_is_an_overclaim():
    text = '{"controls": [{"id": "#inc", "action": {"click": "#inc"}, "effect": {"text_changed": "#count"}}]}'
    p = how_to.parse(f'<script id="how-to-data">{text}</script>', {"#inc": ""}, 12, "?test=1", 500)
    (r,) = how_to.run(page_factory(lambda: counter_dom(on_click={}, ticking={"#count"})), p)
    assert not r.passed


# --- Evaluator -----------------------------------------------------------------------------------------

HOW_TO = ('<script type="application/json" id="how-to-data">{"controls": [{"id": "#inc", "action": '
          '{"click": "#inc"}, "effect": {"text_changed": "#count"}}]}</script>')


class DomBrowser:
    """A Browser over FakeDom: one fresh DOM per page; `fail` viewports raise on load."""

    def __init__(self, make_dom, fail=()):
        self.make_dom, self.fail, self.opened = make_dom, set(fail), []

    @contextmanager
    def page(self, artifact_path, viewport):
        self.opened.append(viewport)
        if viewport in self.fail:
            raise TimeoutError("load timed out")
        yield FakePage(self.make_dom())


class Rubric:
    def __init__(self):
        self.calls = []

    def score(self, screenshot, request_text, artifact_hash, viewport):
        self.calls.append(viewport)
        return (5, 5, 5) if viewport[0] > 1000 else (1, 3, 5)


class Quality:
    def eslint_errors(self, html):
        return 1

    def axe_violations(self, page):
        return 2


def make_eval(browser, rubric=None):
    return Evaluator(
        browser=browser, rubric=rubric or Rubric(), quality=Quality(),
        loss_model=LossModel(0.75, 0.25, (1, 5), 3, {"probe": 0.3, "how_to": 0.3, "checks": 0.4}),
        viewports={"R1": ((1280, 800),), "R2": ((1280, 800), (375, 812))}, test_query="?test=1",
        probe_max_controls=12, settle_ms=500, sample_text="abc 123", how_to_max_controls=12, repair_penalty=0.1)


def fixture(tmp_path, html):
    path = tmp_path / "a.html"
    path.write_text(html)
    p = Pass("P1", 1, "c", Arm.JOURNAL, "w", Composition("Q1", ()), Regime.R2, artifact_path=str(path))
    checks = (Check("c1", ({"goto": "?test=1"}, {"click": "#inc"}), {"text_matches": "#count", "regex": r"\b1\b"}),
              Check("c2", (), {"exists": "#reset"}))
    req = Request("Q1", "a counter", frozenset(), {"#inc": "", "#count": ""}, {Regime.R1: checks, Regime.R2: checks})
    return p, req


def test_r2_runs_every_reading_at_both_viewports_with_its_own_rubric_call(tmp_path):
    rubric = Rubric()
    p, req = fixture(tmp_path, HOW_TO)
    rep = make_eval(DomBrowser(counter_dom), rubric).run(p, req, Regime.R2, "a counter")
    assert rubric.calls == [(1280, 800), (375, 812)]
    assert [x.viewport for x in rep.probes] == [(1280, 800), (375, 812)]
    assert [x.viewport for x in rep.how_to] == [(1280, 800), (375, 812)]
    assert [c.check_id for c in rep.checks] == ["c1@1280x800", "c2@1280x800", "c1@375x812", "c2@375x812"]
    assert rep.rubric == (5, 5, 5, 1, 3, 5)
    assert pytest.approx((1.0 + 0.5) / 2) == rep.R
    assert (pytest.approx(1.0), pytest.approx(1.0), pytest.approx(0.5)) == (rep.P, rep.H, rep.C)
    assert pytest.approx(0.3 + 0.3 + 0.4 * 0.5) == rep.A
    assert rep.loss == pytest.approx(1 - (0.75 * rep.A + 0.25 * rep.R))
    assert rep.Q == 3 and rep.silent_controls == 0
    assert rep.how_to[0].parse.rung is HowToRung.STRICT


def test_a_viewport_that_fails_to_load_scores_its_floor_without_a_rubric_call(tmp_path):
    rubric = Rubric()
    p, req = fixture(tmp_path, HOW_TO)
    rep = make_eval(DomBrowser(counter_dom, fail={(375, 812)}), rubric).run(p, req, Regime.R2, "a counter")
    assert rubric.calls == [(1280, 800)]
    assert rep.rubric[3:] == (1, 1, 1)
    assert rep.probes[1].loaded is False and rep.probes[1].P == 0.0
    assert rep.how_to[1].H == 0.0
    assert any("load failed@375x812" in e for e in rep.console_errors)


def test_an_unloadable_artifact_still_gives_a_report(tmp_path):
    p, req = fixture(tmp_path, HOW_TO)
    rep = make_eval(DomBrowser(counter_dom, fail={(1280, 800), (375, 812)}), Rubric()).run(
        p, req, Regime.R2, "a counter")
    assert (rep.P, rep.H, rep.C, rep.R) == (0.0, 0.0, 0.0, 0.0)
    assert rep.loss == pytest.approx(1.0)


def test_a_missing_how_to_costs_h_and_nothing_else(tmp_path):
    p, req = fixture(tmp_path, "<!doctype html>")
    rep = make_eval(DomBrowser(counter_dom)).run(p, req, Regime.R1, "a counter")
    assert rep.H == 0.0 and pytest.approx(1.0) == rep.P and rep.how_to[0].parse.rung is HowToRung.NONE
    assert rep.silent_controls == 1


def test_a_mix_raises_without_weights():
    with pytest.raises(ValueError):
        LossModel(0.75, 0.25).a_mix(1.0, 1.0, 1.0)


def test_a_programming_error_in_the_probe_raises_instead_of_scoring(tmp_path, monkeypatch):
    """Finding: the broad except in Evaluator._probe mapped any exception to a scored probe reading."""
    def broken(*a, **k):
        raise TypeError("bug in the probe")
    monkeypatch.setattr(probe, "run", broken)
    p, req = fixture(tmp_path, HOW_TO)
    with pytest.raises(TypeError, match="bug in the probe"):
        make_eval(DomBrowser(counter_dom)).run(p, req, Regime.R1, "a counter")


class QueryHangPage(FakePage):
    def goto(self, query):
        if query:
            raise TimeoutError("hangs on the test query")


class QueryHangBrowser(DomBrowser):
    @contextmanager
    def page(self, artifact_path, viewport):
        yield QueryHangPage(self.make_dom())


def test_a_page_that_fails_the_test_query_reload_reads_unloaded(tmp_path):
    """Finding: a page that loaded plainly but hung on the test-query reload was recorded loaded=True."""
    p, req = fixture(tmp_path, HOW_TO)
    rep = make_eval(QueryHangBrowser(counter_dom)).run(p, req, Regime.R1, "a counter")
    assert rep.probes[0].loaded is False and rep.probes[0].P == 0.0
