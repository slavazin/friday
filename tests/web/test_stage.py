"""The Stage in Chromium at 1280×800 and 1920×1080: it opens on the first deck entry built from the export,
→ shows a preloaded app at once, Enter hands the keyboard to the app and Esc takes it back, P shows a pair
(or a readout's three arms) with the export's losses and d, `?deck=` loads exactly its ids in order with
unknown ids noted, and no more than three apps are live at once."""

import json
import shutil
import sqlite3
import time

import pytest

from friday.adapters.telemetry import SqliteTelemetry, connect_telemetry
from friday.ports.telemetry import Scope
from tests.web.conftest import Served, fixture_paths

sync_api = pytest.importorskip("playwright.sync_api")

P = fixture_paths()
DOC = json.loads(P["export"].read_text())
BY_ID = {p["id"]: p for p in DOC["passes"]}
VIEWS = [{"width": 1280, "height": 800}, {"width": 1920, "height": 1080}]


def f3(x):
    return f"{x:.3f}"


def cells_of(db):
    spans = [s for s in SqliteTelemetry(connect_telemetry(db, busy_timeout_ms=1000), Scope("main"))
             .spans(name_prefix="pass") if s.name == "pass"]
    return {s.scope.pass_id: " / ".join(s.attrs["cell"]) for s in spans if s.attrs.get("cell")}


def scored(p):
    return bool(p["artifact_path"]) and not p["transport_failed"] and p["loss"] is not None


def expected_first_entry():
    cells = cells_of(P["db"])
    best = {}
    for p in DOC["passes"]:
        if not scored(p):
            continue
        c = cells.get(p["id"], "cell unread")
        if c not in best or (p["loss"], p["id"]) < (best[c]["loss"], best[c]["id"]):
            best[c] = p
    return best[min(best)]["id"]


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


@pytest.fixture(params=VIEWS, ids=lambda v: f"{v['width']}x{v['height']}")
def page(browser, request):
    ctx = browser.new_context(viewport=request.param)
    pg = ctx.new_page()
    pg.errors = []
    pg.on("console", lambda m: pg.errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    yield pg
    ctx.close()


def open_stage(page, base, deck=None):
    page.goto(f"{base}/stage" + (f"?deck={','.join(deck)}" if deck is not None else ""))
    page.wait_for_selector("body[data-ready='1']")
    page.wait_for_selector("#page-stage.on[data-pass]")


def main_frame(page):
    return page.get_attribute("#page-stage iframe.slot-main", "data-pass")


def app_frame(page, pid):
    for _ in range(100):
        f = next((f for f in page.frames if f.url.endswith(f"/apps/{pid}")), None)
        if f is not None:
            f.wait_for_load_state()
            return f
        page.wait_for_timeout(50)
    raise AssertionError(f"no frame for {pid}")


def live_frames(page):
    return page.eval_on_selector_all("#page-stage iframe", "els => els.length")


def test_stage_opens_on_the_first_deck_entry_with_its_figures_from_the_export(page, served):
    open_stage(page, served.url)
    first = expected_first_entry()
    assert main_frame(page) == first
    p = BY_ID[first]
    assert page.get_attribute("#page-stage .stage-cap .loss-chip", "data-loss") == f3(p["loss"])
    assert p["category"] in page.inner_text("#page-stage .stage-cap")
    assert page.get_attribute("#page-stage iframe.slot-main", "sandbox") == "allow-scripts"
    assert page.inner_text("#page-stage .stage-pos").startswith("1 / ")
    assert page.errors == []


def test_arrow_right_shows_the_preloaded_next_app_at_once_and_at_most_three_apps_are_live(page, served):
    open_stage(page, served.url)
    page.wait_for_selector("#page-stage iframe.slot-main[data-loaded='1']")
    for step in range(4):
        nxt = page.get_attribute("#page-stage iframe.slot-off", "data-pass") if step == 0 else None
        page.wait_for_selector("#page-stage iframe.slot-off[data-loaded='1']", state="attached")
        offs = page.eval_on_selector_all("#page-stage iframe.slot-off", "els => els.map(e => e.dataset.pass)")
        before = main_frame(page)
        t0 = time.perf_counter()
        page.keyboard.press("ArrowRight")
        page.wait_for_function("(b) => { const f = document.querySelector('#page-stage iframe.slot-main');"
                               " return f && f.dataset.pass !== b && f.dataset.loaded === '1'; }", arg=before)
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.3, elapsed
        assert main_frame(page) in offs
        if nxt is not None:
            assert main_frame(page) == nxt
        assert live_frames(page) <= 3
    assert page.errors == []


def test_enter_hands_the_keys_to_the_app_and_escape_hands_them_back(page, served):
    game = "r01-C-101-journal-0"                     # the canvas game: ArrowRight moves its paddle 20 px
    other = "r01-C-102-journal-0"
    open_stage(page, served.url, [game, other])
    page.wait_for_selector("#page-stage iframe.slot-main[data-loaded='1']")
    frame = app_frame(page, game)
    assert frame.evaluate("px") == 180
    page.keyboard.press("Enter")
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(100)
    assert frame.evaluate("px") == 200
    assert main_frame(page) == game                  # the stage did not move while the app held the keys
    page.keyboard.press("Escape")
    page.wait_for_function("() => document.activeElement && document.activeElement.id === 'page-stage'")
    page.keyboard.press("ArrowRight")
    page.wait_for_function(f"() => document.querySelector('#page-stage iframe.slot-main')?.dataset.pass === '{other}'")
    assert page.errors == []


def test_p_shows_both_pair_partners_with_their_losses_and_d_from_the_export(page, served):
    pid = "r02-C-201-journal-1"                      # exposed D-0001; its partner is slot 0
    open_stage(page, served.url, [pid])
    page.keyboard.press("p")
    page.wait_for_selector("#page-stage[data-mode='pair']")
    partner = next(q for q in DOC["passes"] if q["round"] == 2 and q["category"] == "C-201"
                   and q["arm"] == "journal" and q["pair_slot"] == 0)
    for q in (BY_ID[pid], partner):
        head = f"#page-stage .col-head[data-pass='{q['id']}']"
        assert page.get_attribute(f"{head} .loss-chip", "data-loss") == f3(q["loss"])
        assert page.query_selector(f"#page-stage iframe[data-pass='{q['id']}']") is not None
    d = next(x for x in DOC["decisions"] if x["id"] == "D-0001")
    delta = next(x for x in d["ledger"]["exposure"]["deltas"] if x["round"] == 2 and x["category"] == "C-201")
    assert page.get_attribute("#page-stage .mid-d b", "data-d") == f3(delta["d"])
    assert d["payload"] in page.inner_text("#page-stage .stage-mid")
    assert "with D-0001" in page.inner_text(f"#page-stage .col-head[data-pass='{pid}']")
    assert "without D-0001" in page.inner_text(f"#page-stage .col-head[data-pass='{partner['id']}']")
    assert live_frames(page) == 2
    page.keyboard.press("p")
    page.wait_for_selector("#page-stage[data-mode='single']")
    assert page.errors == []


def test_p_on_a_readout_pass_shows_the_held_out_request_in_the_three_arms(page, served):
    pid = "r04-H-01-journal-0"
    open_stage(page, served.url, [pid])
    page.keyboard.press("p")
    page.wait_for_selector("#page-stage[data-mode='arms']")
    heads = page.eval_on_selector_all("#page-stage .col-head", "els => els.map(e => [e.dataset.arm, e.dataset.pass])")
    assert [a for a, _ in heads] == ["empty", "notes", "journal"]
    for arm, q in heads:
        p = BY_ID[q]
        assert p["category"] == "H-01" and p["arm"] == arm and p["readout"]
        assert page.get_attribute(f"#page-stage .col-head[data-pass='{q}'] .loss-chip", "data-loss") == f3(p["loss"])
    assert dict(heads)["journal"] == pid
    assert live_frames(page) == 3
    assert page.errors == []


def test_a_pass_with_no_pair_is_refused_with_a_note_not_a_made_up_partner(page, served):
    pid = "r01-C-101-notes-0"
    open_stage(page, served.url, [pid])
    page.keyboard.press("p")
    page.wait_for_selector("#page-stage .stage-toast:not([hidden])")
    assert page.get_attribute("#page-stage", "data-mode") == "single"
    assert "not one side of a journal-arm pair" in page.inner_text("#page-stage .stage-toast")


def test_deck_link_loads_exactly_its_ids_in_order_and_notes_unknown_ones(page, served):
    ids = ["r03-C-302-journal-1", "nope-1", "r01-C-101-journal-0", "r01-C-103-journal-1", "r04-H-02-empty-0"]
    open_stage(page, served.url, ids)
    kept = ["r03-C-302-journal-1", "r01-C-101-journal-0", "r04-H-02-empty-0"]
    assert main_frame(page) == kept[0]
    note = page.inner_text("#page-stage .stage-note")
    assert "nope-1" in note and "r01-C-103-journal-1" in note     # unknown, and known but never built
    seen = [main_frame(page)]
    for _ in range(len(kept)):
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(30)
        seen.append(main_frame(page))
    assert list(dict.fromkeys(seen)) == kept
    page.keyboard.press("2")
    assert main_frame(page) == kept[1]
    page.click("#page-stage .stage-btn.copy")
    page.wait_for_function("() => document.querySelector('#page-stage .stage-link').value !== ''")
    link = page.eval_on_selector("#page-stage .stage-link", "e => e.value")
    assert link == f"{served.url}/stage?deck={','.join(kept)}"
    page.goto(link)
    page.wait_for_selector("#page-stage.on[data-pass]")
    assert main_frame(page) == kept[0] and page.inner_text("#page-stage .stage-note") == ""


def test_f_hides_the_caption_bar_and_the_app_fills_the_viewport(page, served):
    open_stage(page, served.url)
    page.keyboard.press("f")
    box = page.eval_on_selector("#page-stage iframe.slot-main",
                                "e => { const r = e.getBoundingClientRect(); return [r.width, r.height]; }")
    vp = page.viewport_size
    assert box == [vp["width"], vp["height"]]
    page.keyboard.press("f")
    assert page.is_visible("#page-stage .stage-bar")


def test_a_readout_pass_whose_span_has_no_cell_takes_its_requests_cell(tmp_path, browser):
    db = tmp_path / "telemetry.db"
    shutil.copy(P["db"], db)
    conn = sqlite3.connect(db)
    conn.execute("DROP TRIGGER telemetry_no_update")
    target = "r04-H-01-journal-0"
    (attrs,) = conn.execute("SELECT attrs FROM telemetry WHERE name='pass' AND pass_id=?", (target,)).fetchone()
    a = json.loads(attrs)
    want = " / ".join(a.pop("cell"))
    conn.execute("UPDATE telemetry SET attrs=? WHERE name='pass' AND pass_id=?", (json.dumps(a), target))
    conn.commit()
    conn.close()
    assert target not in cells_of(db)
    s = Served(P["spec"], db, P["export"], P["artifacts"])
    try:
        ctx = browser.new_context(viewport=VIEWS[0])
        pg = ctx.new_page()
        open_stage(pg, s.url, [target])
        assert pg.inner_text("#page-stage .cap-cell") == want
        ctx.close()
    finally:
        s.close()
