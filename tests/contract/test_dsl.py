import pytest

from friday.domain.types import Check
from friday.eval.dsl import InvalidCheck, run_check, validate, validate_set
from tests.fakes import FakeDom, page_factory


def counter_dom():
    def inc(d):
        d.texts["#sessions"] = str(int(d.texts["#sessions"]) + 1)

    return FakeDom(texts={"#start": "Start", "#sessions": "0"}, visible={"#start"}, on_click={"#start": inc},
                   tab_order=["#start"], canvas={"#c": 0}, on_key={"ArrowUp": lambda d: d.canvas.update({"#c": 9})})


def chk(steps, expect):
    return Check("c", tuple(steps), expect)


def run(steps, expect, dom=counter_dom):
    return run_check(page_factory(dom), chk(steps, expect))


def test_text_changed_after_click():
    assert run([{"goto": "?test=1"}, {"click": "#start"}, {"wait": 100}], {"text_changed": "#sessions"}).passed
    assert not run([{"goto": "?test=1"}, {"wait": 100}], {"text_changed": "#sessions"}).passed


def test_text_matches_value_not_label():
    assert run([{"click": "#start"}], {"text_matches": "#sessions", "regex": r"\b1\b"}).passed


def test_failing_step_fails_the_check_with_its_step():
    r = run([{"click": "#missing"}], {"exists": "#start"})
    assert not r.passed and "step 0" in r.failing_step


def test_keyboard_focus_and_tab():
    assert run([{"key": "Tab"}], {"focused": "#start"}).passed
    assert run([], {"visible": "#start"}).passed


def test_canvas_changed_requires_a_still_control():
    assert run([{"key": "ArrowUp"}], {"canvas_changed": "#c"}).passed

    def animated():
        d = counter_dom()
        d.animated.add("#c")
        return d

    r = run([{"wait": 500}], {"canvas_changed": "#c"}, animated)
    assert not r.passed and "control" in r.failing_step


def test_no_console_errors():
    assert run([], {"no_console_errors": True}).passed
    assert not run([], {"no_console_errors": True}, lambda: FakeDom(errors=["boom"])).passed


def test_contract_stub_fails_a_valid_check():
    stub = lambda: FakeDom(texts={"#start": "", "#sessions": ""})  # noqa: E731
    assert not run([{"click": "#start"}], {"text_changed": "#sessions"}, stub).passed


@pytest.mark.parametrize("bad", [
    chk([{"wait": 5000}], {"exists": "#a"}),
    chk([{"hover": "#a"}], {"exists": "#a"}),
    chk([{"click": "#a", "goto": ""}], {"exists": "#a"}),
    chk([], {"exists": "#a", "visible": "#a"}),
    chk([{"type": "#a"}], {"exists": "#a"}),
    chk([], {"text_matches": "#a", "regex": "("}),
])
def test_validation_refuses_outside_the_dsl(bad):
    with pytest.raises((InvalidCheck, Exception)):
        validate(bad, 3000)


def test_one_console_check_per_request():
    c = chk([], {"no_console_errors": True})
    with pytest.raises(InvalidCheck):
        validate_set([c, c], 3000)
