"""Each planted quirk's check in real Chromium (SPEC § 17 Taste): it fails on the contract stub of every request in
its scope over the critic run's rounds, as goal checks are validated (SPEC § 4), and passes on a one-file app that
meets the preference. Skips when Playwright or Chromium is unavailable."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from friday.adapters.telemetry import MemoryTelemetry
from friday.app.taste import TASTE_PATH, load_cells, load_quirks
from friday.config import load_spec
from friday.eval.dsl import run_check
from friday.ports.telemetry import Scope
from friday.roles.goal_minter import stub_html

ROOT = Path(__file__).resolve().parents[2]
SPEC = load_spec(ROOT / "run.yml")
QUIRKS = load_quirks(ROOT / TASTE_PATH, SPEC.stream.families, SPEC.stream.modes, SPEC.eval.max_wait_ms)
CRITIC_ROUNDS = 4

MEETS = {
    "pause": '<canvas id="board"></canvas><button id="pause">Pause</button><p id="pause-note">Running</p>'
             "<script>document.getElementById('pause').onclick = () => "
             "{ document.getElementById('pause-note').textContent = 'Paused'; };</script>",
    "row-count": '<ul id="list"><li>4</li><li>9</li></ul><p id="row-count">2 entries</p>',
    "dark-mode": '<textarea id="text"></textarea><button id="theme-toggle">Theme</button><p id="theme-name">Light</p>'
                 "<script>document.getElementById('theme-toggle').onclick = () => "
                 "{ document.getElementById('theme-name').textContent = 'Dark'; };</script>",
    "clear-all": '<input id="bill"><button id="clear-all">Clear all</button><p id="cleared-note"></p>'
                 "<script>document.getElementById('clear-all').onclick = () => { "
                 "document.getElementById('bill').value = ''; "
                 "document.getElementById('cleared-note').textContent = 'Cleared'; };</script>",
    "key-help": '<div id="key-help" hidden>Arrows move. ? shows this help.</div>'
                "<script>document.addEventListener('keydown', e => { if (e.key === '?') { "
                "const h = document.getElementById('key-help'); h.hidden = !h.hidden; } });</script>",
    "app-title": '<h1 id="app-title">Step counter</h1>',
}


@pytest.fixture(scope="module")
def chromium():
    try:
        from friday.adapters.playwright_browser import PlaywrightBrowser
        b = PlaywrightBrowser(action_timeout_ms=400, load_timeout_ms=3000, telemetry=MemoryTelemetry(Scope("main")))
    except Exception as e:  # noqa: BLE001 - no Playwright or no Chromium: the browser tests skip
        pytest.skip(f"Chromium unavailable: {type(e).__name__}: {e}")
    yield b
    b.close()


def _viewports():
    return [tuple(v) for vs in SPEC.eval.viewports.values() for v in vs]


def test_every_quirk_check_fails_on_the_stub_of_every_request_in_its_scope(chromium, tmp_path):
    rows = [r for r in json.loads((ROOT / SPEC.stream.path).read_text())
            if not r["held_out"] and r["round"] <= CRITIC_ROUNDS]
    cells = load_cells(ROOT / SPEC.stream.path)
    stubs: dict[str, Path] = {}
    for r in rows:
        html = stub_html(r["contract"])
        if html not in stubs:
            stubs[html] = tmp_path / f"{r['id']}.html"
            stubs[html].write_text(html)
    path_of = {r["id"]: stubs[stub_html(r["contract"])] for r in rows}
    for q in QUIRKS:
        scoped = {path_of[r["id"]] for r in rows if q.covers(cells[r["id"]])}
        assert scoped, q.id
        for path in sorted(scoped):
            for vp in _viewports()[:1]:      # a stub holds no script: what it lacks it lacks at every width
                res = run_check(lambda vp=vp, path=path: chromium.page(str(path), vp), q.check)
                assert not res.passed, (q.id, path.name)


def test_every_quirk_check_passes_on_an_app_that_meets_the_preference(chromium, tmp_path):
    assert set(MEETS) == {q.id for q in QUIRKS}
    for q in QUIRKS:
        path = tmp_path / f"{q.id}.html"
        path.write_text('<!doctype html><html><head><meta charset="utf-8">'
                        '<meta name="viewport" content="width=device-width, initial-scale=1"></head>'
                        f"<body>{MEETS[q.id]}</body></html>")
        for vp in _viewports():
            res = run_check(lambda vp=vp, path=path: chromium.page(str(path), vp), q.check)
            assert res.passed, (q.id, vp, res.failing_step)
