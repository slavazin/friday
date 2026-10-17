"""The quality reading Q (SPEC § 5): ESLint errors over the inline script plus axe-core violations on the
loaded page, from the local Node install, never read as 0 when a tool is missing or fails."""

import json
import subprocess
from pathlib import Path

import pytest

from friday.adapters import quality
from friday.adapters.quality import NodeQualityProbe, QualityToolFailed, QualityUnavailable, inline_code, inline_script
from friday.adapters.telemetry import MemoryTelemetry
from friday.config import load_spec
from friday.ports.telemetry import Scope
from tests.fakes import FakeDom, FakePage

TEL = MemoryTelemetry(Scope("main"))

ROOT = Path(__file__).resolve().parents[2]
SPEC = load_spec(ROOT / "run.yml")
DATA = ROOT / "tests/data/quality"

# Hand count of tests/data/quality/flawed.html.
# ESLint (config/eslint.json): no-unused-vars `unused`; no-constant-condition `if (true)`; no-empty `catch { }`;
# no-undef `missingFn` inside the onclick attribute. The how-to JSON script is not linted.
FLAWED_ESLINT = 4
# axe-core rules violated: html-has-lang, document-title, image-alt, label, button-name.
FLAWED_AXE = 5


@pytest.fixture(scope="module")
def probe():
    return NodeQualityProbe.from_spec(SPEC, ROOT)


@pytest.fixture(scope="module")
def browser():
    pytest.importorskip("playwright")
    from friday.adapters.playwright_browser import PlaywrightBrowser

    b = PlaywrightBrowser(SPEC.eval.action_timeout_ms, SPEC.eval.load_timeout_ms, telemetry=TEL)
    yield b
    b.close()


def _with_run_script(page):
    """The Playwright page, given `run_script` as `page.evaluate` when the adapter does not implement it yet."""
    if not hasattr(page, "run_script"):
        page.run_script = page._page.evaluate
    return page


def _axe(probe, browser, name):
    with browser.page(str(DATA / name), (1280, 800)) as page:
        return probe.axe_violations(_with_run_script(page))


def test_eslint_count_on_the_flawed_fixture_matches_the_hand_count(probe):
    assert probe.eslint_errors((DATA / "flawed.html").read_text()) == FLAWED_ESLINT


def test_eslint_count_on_the_clean_fixture_is_zero(probe):
    assert probe.eslint_errors((DATA / "clean.html").read_text()) == 0


def test_q_on_the_flawed_fixture_matches_the_hand_count(probe, browser):
    q = probe.eslint_errors((DATA / "flawed.html").read_text()) + _axe(probe, browser, "flawed.html")
    assert q == FLAWED_ESLINT + FLAWED_AXE
    assert _axe(probe, browser, "clean.html") == 0


def test_axe_count_is_stable_across_two_runs(probe, browser):
    assert _axe(probe, browser, "flawed.html") == _axe(probe, browser, "flawed.html") == FLAWED_AXE


def test_inline_script_skips_src_scripts_and_json_data_and_keeps_handlers():
    html = ('<script>var a = 1;</script><script src="x.js">ignored()</script>'
            '<script type="application/json" id="how-to-data">{"summary": 1}</script>'
            '<button onclick="go(event)">x</button><img src=x onerror="fail()"/><script type="module">b()</script>')
    code = inline_script(html)
    assert "var a = 1;" in code and "go(event)" in code and "fail()" in code
    assert "ignored" not in code and "summary" not in code and "b()" not in code
    assert inline_code(html).modules == ("b()",)                       # a module is its own source


def test_a_function_called_only_from_a_handler_attribute_is_not_unused(probe):
    html = '<button onclick="start()">Go</button><script>function start() { return 1; }</script>'
    assert probe.eslint_errors(html) == 0
    assert probe.eslint_errors("<script>function start() { return 1; }</script>") == 1


def test_inline_eslint_comments_cannot_switch_a_rule_off(probe):
    html = "<script>/* eslint-disable */\n// eslint-disable-next-line no-undef\nmissing();</script>"
    assert probe.eslint_errors(html) == 1


def test_a_parse_error_counts_as_an_error_not_as_zero(probe):
    assert probe.eslint_errors("<script>var = ;</script>") == 1


def test_a_user_eslint_config_in_the_root_is_never_read(tmp_path):
    (tmp_path / "node_modules").symlink_to(ROOT / "node_modules")
    (tmp_path / "config").mkdir()
    (tmp_path / "config/eslint.json").write_text((ROOT / "config/eslint.json").read_text())
    (tmp_path / "eslint.config.js").write_text("export default [{ rules: { 'no-undef': 'off' } }];\n")
    (tmp_path / ".eslintrc.json").write_text('{"rules": {"no-undef": "off"}}')
    p = NodeQualityProbe(tmp_path, Path("config/eslint.json"))
    assert p.eslint_errors("<script>missing();</script>") == 1


def test_missing_node_raises_at_construction(monkeypatch):
    monkeypatch.setattr(quality.shutil, "which", lambda name: None)
    with pytest.raises(QualityUnavailable, match="node"):
        NodeQualityProbe.from_spec(SPEC, ROOT)


def test_missing_node_modules_raises_at_construction(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/eslint.json").write_text("{}")
    with pytest.raises(QualityUnavailable, match="npm ci"):
        NodeQualityProbe(tmp_path, Path("config/eslint.json"))


def test_missing_config_raises_at_construction():
    with pytest.raises(QualityUnavailable, match="config"):
        NodeQualityProbe(ROOT, Path("config/absent.json"))


def test_an_eslint_crash_raises_instead_of_counting_zero(probe, monkeypatch):
    def crashed(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 2, stdout="", stderr="Error: boom")

    monkeypatch.setattr(quality.subprocess, "run", crashed)
    with pytest.raises(QualityToolFailed, match="boom"):
        probe.eslint_errors("<script>var a;</script>")


def test_eslint_output_without_a_count_raises(probe, monkeypatch):
    monkeypatch.setattr(quality.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout='{"messages": []}', stderr=""))
    with pytest.raises(QualityToolFailed):
        probe.eslint_errors("<script>var a;</script>")


@pytest.mark.parametrize("result", [None, "3", -1, True, 2.5, {"violations": []}])
def test_an_axe_result_that_is_not_a_count_raises(probe, result):
    dom = FakeDom(scripts={probe.axe_script(): result})
    with pytest.raises(QualityToolFailed):
        probe.axe_violations(FakePage(dom))


def test_axe_runs_eval_side_through_run_script_after_load(probe):
    src = probe.axe_script()
    dom = FakeDom(scripts={src: 3})
    assert probe.axe_violations(FakePage(dom)) == 3
    assert src.startswith("async () => {") and "readyState !== 'complete'" in src
    assert (ROOT / "node_modules/axe-core/axe.min.js").read_text()[:200] in src


def test_node_modules_is_not_committed():
    tracked = subprocess.run(["git", "ls-files", "node_modules"], cwd=ROOT, capture_output=True, text=True,
                             check=True)
    assert tracked.stdout == ""


# Review regressions: each piece the browser compiles on its own fails on its own.

def _flawed_plus(extra: str) -> str:
    return (DATA / "flawed.html").read_text().replace("</body>", extra + "\n</body>")


def test_one_broken_handler_counts_once_and_leaves_the_rest_of_the_page_linted(probe):
    # A mis-quoted handler: onclick="alert(" and the rest becomes junk attributes.
    assert probe.eslint_errors(_flawed_plus('<button onclick="alert("hi")">x</button>')) == FLAWED_ESLINT + 1


def test_a_valid_module_script_is_linted_as_a_module_and_does_not_collapse_the_count(probe):
    assert probe.eslint_errors(_flawed_plus('<script type="module">export {};</script>')) == FLAWED_ESLINT
    assert probe.eslint_errors(_flawed_plus('<script type="module">await Promise.resolve(); let q;</script>')) \
        == FLAWED_ESLINT + 1


def test_a_broken_module_or_classic_script_counts_once_and_the_others_are_still_linted(probe):
    assert probe.eslint_errors(_flawed_plus('<script type="module">var = ;</script>')) == FLAWED_ESLINT + 1
    assert probe.eslint_errors(_flawed_plus("<script>var = ;</script>")) == FLAWED_ESLINT + 1
    # A redeclared `let` fails the second script in the browser, not the first.
    assert probe.eslint_errors("<script>let a = 1; console.log(a);</script><script>let a = 2; missing();</script>") == 1


def test_a_module_sees_classic_globals_and_a_classic_function_it_calls_reads_as_used(probe):
    html = "<script>function go() { return 1; }</script><script type=module>go(); const unused = 1;</script>"
    assert probe.eslint_errors(html) == 1


def test_standard_browser_globals_are_not_counted_as_undefined(probe):
    html = ("<script>console.log(atob('aGk='), btoa('x'), scrollY, scrollX, outerWidth);"
            "console.log(document.body instanceof HTMLButtonElement);"
            "speechSynthesis.speak(new SpeechSynthesisUtterance('hi'));"
            "console.log(WheelEvent, DOMRect, Worker, customElements);</script>")
    assert probe.eslint_errors(html) == 0
    assert probe.eslint_errors("<script>console.log(notABrowserGlobal);</script>") == 1


@pytest.mark.parametrize("attrs", [
    "type='application/x-javascript'", "type='text/jscript'", "type='application/x-ecmascript'",
    "type='text/livescript'", "type='text/javascript1.5'", "type=' Text/JavaScript '", "language='javascript'",
])
def test_legacy_javascript_mime_types_are_linted(probe, attrs):
    assert probe.eslint_errors(f"<script {attrs}>var unused = 1;</script>") == 1


@pytest.mark.parametrize("attrs", ["type='application/json'", "type='text/plain'", "type='text/javascript; x=1'",
                                   "language='vbscript'"])
def test_non_javascript_script_types_are_not_linted(probe, attrs):
    assert probe.eslint_errors(f"<script {attrs}>var unused = 1;</script>") == 0


def test_the_globals_package_is_a_pinned_dev_dependency():
    pkg = json.loads((ROOT / "package.json").read_text())
    assert pkg["devDependencies"]["globals"] == json.loads((ROOT / "node_modules/globals/package.json").read_text())[
        "version"]


def test_no_redeclare_does_not_count_top_level_names_that_shadow_browser_globals(probe):
    html = ("<script>const status = document.getElementById('s'); const name = 'x'; function stop(){}"
            " let top = 1, history = 2, event = 3; var length = 4; function close(){} function print(){}"
            " function open(){} console.log(status, name, top, history, event, length); stop(); close(); print();"
            " open();</script>")
    assert probe.eslint_errors(html) == 0
    assert probe.eslint_errors("<script>var a; var a; console.log(a);</script>") == 1


def test_a_fatal_script_counts_once_and_its_own_lint_errors_are_not_counted_accepted(probe):
    # Accepted (SPEC § 5 does not weigh a fatal piece): breaking a lint-heavy single script lowers Q; P and C
    # catch the dead page. Pinned so a change here is a deliberate one.
    noisy = "var a1,a2,a3,a4,a5; if(true){}"
    assert probe.eslint_errors(f"<script>{noisy}</script>") == 7
    assert probe.eslint_errors(f"<script>{noisy} let = ;</script>") == 1
