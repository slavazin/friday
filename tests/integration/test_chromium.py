"""The eval in real Chromium (CARRY_FORWARD S1 done-when). Skips when Playwright or Chromium is unavailable."""

import inspect
import time
from pathlib import Path

import pytest

from friday.algorithms.loss import LossModel
from friday.domain.types import Arm, Check, Composition, ControlKind, HowToRung, Pass, Regime, Request
from friday.eval import how_to, probe
from friday.eval.evaluate import Evaluator
from friday.ports import browser as port

APPS = Path(__file__).resolve().parents[1] / "data" / "apps"
VP = (1280, 800)
CONTRACT = {"#inc": "adds one", "#count": "shows the count"}
SETTLE = 150


def _open(load_ms):
    try:
        from friday.adapters.playwright_browser import PlaywrightBrowser
        return PlaywrightBrowser(action_timeout_ms=1500, load_timeout_ms=load_ms)
    except Exception as e:  # noqa: BLE001 - no Playwright or no Chromium: the browser tests skip
        pytest.skip(f"Chromium unavailable: {type(e).__name__}: {e}")


@pytest.fixture(scope="module")
def chromium():
    b = _open(load_ms=3000)
    yield b
    b.close()


class Rubric:
    def __init__(self):
        self.calls = []

    def score(self, screenshot, request_text, artifact_hash, viewport):
        assert screenshot[:8] == b"\x89PNG\r\n\x1a\n"
        self.calls.append(viewport)
        return (4, 4, 4)


class Quality:
    def eslint_errors(self, html):
        return 0

    def axe_violations(self, page):
        return 0


def evaluator(b, rubric=None):
    return Evaluator(
        browser=b, rubric=rubric or Rubric(), quality=Quality(),
        loss_model=LossModel(0.75, 0.25, (1, 5), 3, {"probe": 0.3, "how_to": 0.3, "checks": 0.4}),
        viewports={"R1": ((1280, 800),), "R2": ((1280, 800), (375, 812))}, test_query="?test=1",
        probe_max_controls=12, settle_ms=SETTLE, sample_text="abc 123", how_to_max_controls=12, repair_penalty=0.1)


def case(name, regime=Regime.R1):
    p = Pass("P1", 1, "c", Arm.JOURNAL, "w", Composition("Q1", ()), regime, artifact_path=str(APPS / name))
    checks = (Check("c1", ({"goto": "?test=1"}, {"click": "#inc"}), {"text_matches": "#count", "regex": r"\b1\b"}),)
    return p, Request("Q1", "a counter", frozenset(), CONTRACT, {Regime.R1: checks, Regime.R2: checks})


def test_playwright_page_conforms_to_the_page_port():
    from friday.adapters.playwright_browser import PlaywrightPage
    for name, fn in vars(port.Page).items():
        if callable(fn) and not name.startswith("_"):
            ours = [p for p in inspect.signature(getattr(PlaywrightPage, name)).parameters if p != "self"]
            assert ours == [p for p in inspect.signature(fn).parameters if p != "self"], name


def test_a_correct_how_to_scores_h_one(chromium):
    rep = evaluator(chromium).run(*case("counter.html"), Regime.R1, "a counter")
    assert pytest.approx(1.0) == rep.H
    assert rep.how_to[0].parse.rung is HowToRung.STRICT
    assert rep.C == 1.0 and pytest.approx(1.0) == rep.P


@pytest.mark.parametrize(("name", "rung", "h"), [
    ("counter_truncated.html", HowToRung.SALVAGED, 0.9),
    ("counter_trailing_comma.html", HowToRung.REPAIRED, 0.9),
    ("counter_no_howto.html", HowToRung.NONE, 0.0),
    ("counter_overclaim.html", HowToRung.STRICT, 0.0),
])
def test_each_rung_and_the_overclaim_in_a_real_page(chromium, name, rung, h):
    rep = evaluator(chromium).run(*case(name), Regime.R1, "a counter")
    assert rep.how_to[0].parse.rung is rung
    assert pytest.approx(h) == rep.H


def test_the_probe_scores_zero_for_a_control_that_changes_nothing(chromium):
    with chromium.page(str(APPS / "dead_button.html"), VP) as page:
        page.goto("?test=1")
        r = probe.run(page, VP, "P1", ("#inc",), 12, SETTLE, "abc 123")
    assert (r.controls, r.responsive) == (1, 0)
    assert pytest.approx(2 / 3) == r.P


def test_a_ticking_page_makes_no_control_look_responsive(chromium):
    with chromium.page(str(APPS / "ticking.html"), VP) as page:
        r = probe.run(page, VP, "P1", (), 12, SETTLE, "abc 123")
    assert r.controls == 1 and r.responsive == 0


def test_controls_are_found_in_document_order_and_read_by_kind(chromium):
    with chromium.page(str(APPS / "controls.html"), VP) as page:
        controls = page.controls()
        r = probe.run(page, VP, "P1", ("#name",), 12, SETTLE, "abc 123")
    assert [c.kind for c in controls] == [ControlKind.TEXT, ControlKind.TEXT, ControlKind.SELECT,
                                          ControlKind.CHECKBOX, ControlKind.CANVAS, ControlKind.BUTTON]
    assert [c.element_id for c in controls] == ["#name", "#silent", "#pick", "#flag", "#c", None]
    assert r.responsive_ids == ("#name", "#pick", "#flag", "#c")
    assert r.silent == 3


def test_probe_is_deterministic_across_loads(chromium):
    def once():
        with chromium.page(str(APPS / "controls.html"), VP) as page:
            r = probe.run(page, VP, "P1", (), 12, SETTLE, "abc 123")
            return (r.controls, r.responsive, r.responsive_ids, r.P)
    assert once() == once()


def test_r2_runs_the_375_viewport_with_its_own_screenshot_and_rubric_call(chromium):
    rubric = Rubric()
    rep = evaluator(chromium, rubric).run(*case("counter.html", Regime.R2), Regime.R2, "a counter")
    assert rubric.calls == [(1280, 800), (375, 812)]
    assert [p.viewport for p in rep.probes] == [(1280, 800), (375, 812)]


def test_network_is_aborted(chromium):
    with chromium.page(str(APPS / "network.html"), VP) as page:
        page.wait(300)
        assert page.text("#out") == "blocked"


def test_missing_contract_ids_reads_the_loaded_page(chromium):
    with chromium.page(str(APPS / "counter.html"), VP) as page:
        assert probe.missing_contract_ids(page, {**CONTRACT, "#reset": ""}) == ["#reset"]


def test_how_to_run_over_real_pages(chromium):
    html = (APPS / "counter.html").read_text()
    p = how_to.parse(html, CONTRACT, 12, "?test=1", SETTLE)
    (r,) = how_to.run(lambda: chromium.page(str(APPS / "counter.html"), VP), p)
    assert r.passed


def test_an_unloadable_page_still_yields_an_eval_report_and_the_browser_recovers(chromium):
    rubric = Rubric()
    t0 = time.monotonic()
    rep = evaluator(chromium, rubric).run(*case("infinite_loop.html"), Regime.R1, "a loop")
    assert time.monotonic() - t0 < 60
    assert rubric.calls == []
    assert rep.rubric == (1, 1, 1) and rep.R == 0.0
    assert rep.probes[0].loaded is False and rep.P == 0.0 and rep.C == 0.0
    assert rep.loss == pytest.approx(1.0 - 0.75 * rep.A)
    after = evaluator(chromium).run(*case("counter.html"), Regime.R1, "a counter")
    assert pytest.approx(1.0) == after.H


# --- review regressions -------------------------------------------------------------------------------


def test_type_keeps_focus_so_a_following_key_step_reaches_the_field(chromium):
    """Finding: the type step blurred the field, so a `key` after it pressed the key on <body>."""
    from friday.eval.dsl import run_check
    check = Check("todo", ({"goto": "?test=1"}, {"type": "#new", "text": "milk"}, {"key": "Enter"}, {"wait": 100}),
                  {"text_matches": "#list", "regex": "milk"})
    r = run_check(lambda: chromium.page(str(APPS / "todo.html"), VP), check)
    assert r.passed, r.failing_step


def test_type_still_fires_change_on_the_field(chromium, tmp_path):
    page_html = tmp_path / "change.html"
    page_html.write_text('<input id="f" onchange="document.getElementById(\'o\').textContent = this.value">'
                         '<p id="o"></p>')
    with chromium.page(str(page_html), VP) as page:
        page.type("#f", "abc")
        assert page.text("#o") == "abc" and page.focused("#f")


def test_a_page_that_hangs_after_a_click_fails_visible_key_and_scripts_instead_of_hanging(chromium):
    """Finding: visible(), run_script() and key() bypassed the bounded read and hung the eval forever."""
    from friday.eval.dsl import run_check
    open_page = lambda: chromium.page(str(APPS / "late_loop.html"), VP)  # noqa: E731
    t0 = time.monotonic()
    r = run_check(open_page, Check("v", ({"goto": "?test=1"}, {"click": "#go"}, {"wait": 100}), {"visible": "#x"}))
    assert not r.passed
    for call in (lambda p: p.key("a"), lambda p: p.run_script("() => 1")):
        with pytest.raises(TimeoutError), open_page() as page:
            page.click("#go")
            page.wait(100)
            call(page)
    with pytest.raises(TimeoutError), open_page() as page:
        page.focus("#k")
        page.key("a")          # the key handler itself never returns
    assert time.monotonic() - t0 < 30
    with chromium.page(str(APPS / "counter.html"), VP) as page:   # the browser still serves fresh pages
        page.click("#inc")
        assert page.text("#count") == "1"


def test_visible_reads_rendering_and_the_viewport(chromium, tmp_path):
    page_html = tmp_path / "vis.html"
    page_html.write_text('<p id="a">a</p><p id="h" style="visibility:hidden">h</p><p id="n" style="display:none">n'
                         '</p><p id="far" style="position:absolute; top:5000px">far</p><p id="e"></p>')
    with chromium.page(str(page_html), VP) as page:
        assert [page.visible(s) for s in ("#a", "#h", "#n", "#far", "#e", "#none")] == [
            True, False, False, False, False, False]


def test_run_script_returns_an_async_scripts_result_and_raises_on_its_error(chromium):
    with chromium.page(str(APPS / "counter.html"), VP) as page:
        assert page.run_script("async () => ({n: document.querySelectorAll('button').length})") == {"n": 1}
        assert page.run_script("1 + 1") == 2
        with pytest.raises(RuntimeError):
            page.run_script("() => { throw new Error('boom'); }")


def test_a_stopwatch_stop_button_reads_responsive(chromium):
    """Finding: the idle pre-check never operated a control once a timer ran, so stop and reset scored 0."""
    with chromium.page(str(APPS / "stopwatch.html"), VP) as page:
        page.goto("?test=1")
        r = probe.run(page, VP, "P1", (), 12, SETTLE, "abc 123")
    assert r.controls == 3
    assert r.responsive_ids == ("#start", "#stop", "#reset")


def test_websocket_connections_never_reach_the_network(chromium, tmp_path):
    """Finding: ctx.route does not intercept WebSockets, so a handshake reached a local listener."""
    import socket
    import threading
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    srv.settimeout(0.2)
    port_ = srv.getsockname()[1]
    seen = []
    stop = threading.Event()

    def accept():
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                continue
            conn.settimeout(1)
            try:
                seen.append(conn.recv(64))
            except OSError as e:
                seen.append(repr(e))
            conn.close()
    th = threading.Thread(target=accept, daemon=True)
    th.start()
    page_html = tmp_path / "ws.html"
    page_html.write_text(f'<p id="s">pending</p><script>const ws = new WebSocket("ws://127.0.0.1:{port_}/ws");'
                         'ws.onopen = () => { document.getElementById("s").textContent = "open"; };'
                         'ws.onerror = () => { document.getElementById("s").textContent = "refused"; };'
                         f'fetch("http://127.0.0.1:{port_}/fetch").catch(() => {{}});</script>')
    try:
        with chromium.page(str(page_html), VP) as page:
            page.wait(600)
            assert page.text("#s") == "refused"
    finally:
        stop.set()
        th.join(2)
        srv.close()
    assert seen == []


def test_other_local_files_are_not_loaded_into_the_artifact(chromium, tmp_path):
    """Finding: every file: URL was let through, so an iframe rendered another local file."""
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "secret.txt").write_text("SECRET")
    page_html = tmp_path / "iframe.html"
    page_html.write_text('<iframe id="f" src="other/secret.txt"></iframe>')
    with chromium.page(str(page_html), VP) as page:
        page.wait(300)
        frames = [f for f in page._page.frames if f != page._page.main_frame]
        assert len(frames) == 1
        child_text = frames[0].evaluate("() => document.body ? document.body.innerText : ''")
    assert "SECRET" not in child_text
