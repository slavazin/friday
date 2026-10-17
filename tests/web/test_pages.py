"""The demo page in Chromium at 1280×800: each page renders its figures from the fixture export and telemetry log
with no console error; the live view fits without scrolling and reads the log by after_seq; the replay runs end
to end at 60×; built apps are sandboxed away from the page and the network; unread readings read as unread."""

import copy
import json
import sqlite3

import pytest

from friday.adapters.telemetry import SqliteTelemetry, connect_telemetry
from friday.ports.telemetry import Scope
from tests.web.conftest import Served, fixture_paths

sync_api = pytest.importorskip("playwright.sync_api")

P = fixture_paths()
DOC = json.loads(P["export"].read_text())
VIEW = {"width": 1280, "height": 800}


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport=VIEW)
    pg = ctx.new_page()
    pg.errors = []
    pg.on("console", lambda m: pg.errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    yield pg
    ctx.close()


def open_tab(page, base, tab):
    page.goto(f"{base}/#{tab}")
    page.wait_for_selector("body[data-ready='1']")
    page.wait_for_selector(f"#page-{tab}.on")


def f3(x):
    return f"{x:.3f}"


def test_live_view_renders_the_latest_round_from_the_log_and_fits_1280x800(page, served):
    open_tab(page, served.url, "live")
    page.wait_for_function("() => Number(document.querySelector('.poll-status')?.dataset.seq) > 0")
    page.wait_for_selector("#page-live .card.is-scored")
    readout = [p for p in DOC["passes"] if p["readout"] and p["loss"] is not None]
    for p in readout[:4]:
        assert page.inner_text(f"#page-live .card[data-pass='{p['id']}'] .card-loss") == f3(p["loss"])
    failed = next(p for p in DOC["passes"] if p["readout"] and p["transport_failed"])
    assert "failed for transport" in page.inner_text(f"#page-live .card[data-pass='{failed['id']}']")
    # Spend against the cap: the last run.spend reading of the log.
    conn = connect_telemetry(P["db"], busy_timeout_ms=1000)
    spend = SqliteTelemetry(conn, Scope("main")).rows(name_prefix="run.spend")[-1].value
    assert f"${spend:.2f} of $30.00" in page.inner_text("#page-live .spend")
    # Round 1's close phases with their counts.
    page.click("#page-live .dot[data-round='1']")
    page.wait_for_selector("#page-live .phase.is-done")
    assert len(page.query_selector_all("#page-live .phase.is-done")) == 8
    assert "pools new 2" in page.inner_text("#page-live .phases")
    r1 = [p for p in DOC["passes"] if p["round"] == 1 and p["loss"] is not None]
    assert page.inner_text(f"#page-live .card[data-pass='{r1[0]['id']}'] .card-loss") == f3(r1[0]["loss"])
    assert page.inner_text("#page-live .dot.is-drift").startswith(str(DOC["run"]["drift_round"]))
    over = page.evaluate("[document.documentElement.scrollHeight, document.querySelector('#page-live').scrollHeight,"
                         " document.querySelector('#page-live').clientHeight]")
    assert over[0] <= VIEW["height"] and over[1] <= over[2], over
    assert page.errors == []


def test_replay_runs_the_recorded_log_end_to_end_at_60x(page, served):
    open_tab(page, served.url, "replay")
    page.wait_for_selector("#page-replay[data-state='ready']")
    page.click("#page-replay .btn.seg[data-speed='60']")
    page.click("#page-replay .btn.primary")
    page.wait_for_selector("#page-replay[data-state='ended']", timeout=60_000)
    text = page.inner_text("#page-replay .replay-state")
    total = page.evaluate("fetch('/api/telemetry?run=main&limit=1').then(r => r.json())")
    assert "end of the recorded run" in text
    last = [p for p in DOC["passes"] if p["readout"] and p["loss"] is not None][-1]
    assert page.inner_text(f"#page-replay .card[data-pass='{last['id']}'] .card-loss") == f3(last["loss"])
    # Scrub to round 2: its cards, and round 3's not yet begun.
    page.click("#page-replay [data-jump='2']")
    r2 = next(p for p in DOC["passes"] if p["round"] == 2)
    page.wait_for_selector(f"#page-replay .card[data-pass='{r2['id']}']")
    assert "Round 2 of 3" in page.inner_text("#page-replay .round-title")
    assert total["available"] is True
    assert page.errors == []


def test_gallery_shows_every_built_app_sandboxed_with_its_how_to_and_decisions(page, served):
    open_tab(page, served.url, "gallery")
    built = [p for p in DOC["passes"] if p["artifact_path"]]
    assert page.inner_text("#page-gallery .page-head").count(f"showing {len(built)} apps") == 1
    # every app shows frozen (a poster, no live iframe) until hovered for PLAY_AFTER_MS; then it runs sandboxed
    assert len(page.query_selector_all("#page-gallery .app-card .frozen")) == len(built)
    assert page.query_selector_all("#page-gallery .app-card iframe") == []
    first = page.query_selector("#page-gallery .app-card .frozen")
    first.hover()
    page.wait_for_selector("#page-gallery .app-card .frozen iframe", timeout=3000)
    frames = page.query_selector_all("#page-gallery .app-card iframe")
    assert len(frames) == 1 and frames[0].get_attribute("sandbox") == "allow-scripts"
    p = next(x for x in built if x["how_to_summary"] and x["exposed"])
    card = f"#page-gallery .app-card[data-pass='{p['id']}']"
    assert p["how_to_summary"] in page.inner_text(card)
    assert f"loss {f3(p['loss'])}" in page.inner_text(card)
    assert f"{p['exposed']} under test" in page.inner_text(card)
    rung4 = next((x for x in built if x["how_to_summary"] is None), None)
    if rung4:
        assert "no how-to read" in page.inner_text(f"#page-gallery .app-card[data-pass='{rung4['id']}']")
    # Filter by arm: only notes-arm cards remain.
    page.select_option("#page-gallery select[data-filter='arm']", "notes")
    n_notes = sum(x["arm"] == "notes" for x in built)
    assert f"showing {n_notes} apps" in page.inner_text("#page-gallery .page-head")
    page.select_option("#page-gallery select[data-filter='arm']", "")
    # A decision's passes, each with its pair partner and the paired delta from its Ledger account.
    page.click(f"{card} button[data-decision='{p['exposed']}']")
    page.wait_for_selector("#page-gallery .drawer:not([hidden]) .pair")
    d = next(x for x in DOC["decisions"] if x["id"] == p["exposed"])
    drawer = page.inner_text("#page-gallery .drawer")
    for delta in d["ledger"]["exposure"]["deltas"]:
        assert f"{delta['category']}" in drawer and f3(delta["d"]) in drawer
    pairs = d["ledger"]["exposure"]["n"] + d["ledger"]["ablation"]["n"]
    assert len(page.query_selector_all("#page-gallery .drawer .pair")) == pairs
    assert page.errors == []


def test_a_built_app_cannot_reach_the_page_its_api_or_the_network(page, served):
    open_tab(page, served.url, "gallery")
    pid = page.eval_on_selector(".app-card", "e => e.dataset.pass")   # the first card shown
    page.hover(f".app-card[data-pass='{pid}'] .frozen")             # hovering plays it in its sandboxed frame
    page.wait_for_selector(f".app-card[data-pass='{pid}'] iframe", timeout=3000)
    frame = None
    for _ in range(100):
        frame = next((f for f in page.frames if f.url.endswith(f"/apps/{pid}")), None)
        if frame is not None:
            break
        page.wait_for_timeout(50)
    assert frame is not None
    frame.wait_for_load_state()
    reach = frame.evaluate("""async () => {
      const out = {};
      try { out.parent = String(window.parent.document.title); } catch (e) { out.parent = 'blocked'; }
      try { out.cookie = document.cookie; out.cookieOk = true; } catch (e) { out.cookie = 'blocked'; }
      try { await fetch('/api/export'); out.api = 'reached'; } catch (e) { out.api = 'blocked'; }
      try { await fetch('https://example.com/'); out.net = 'reached'; } catch (e) { out.net = 'blocked'; }
      try { localStorage.setItem('x', '1'); out.storage = 'reached'; } catch (e) { out.storage = 'blocked'; }
      out.origin = window.origin;
      return out;
    }""")
    assert reach["parent"] == "blocked" and reach["api"] == "blocked" and reach["net"] == "blocked"
    assert reach["storage"] == "blocked" and reach["origin"] == "null"


def test_timeline_draws_wealth_paths_against_one_over_alpha_with_the_drift_round(page, served):
    open_tab(page, served.url, "timeline")
    tested = [d for d in DOC["decisions"] if d["ledger"]["exposure"]["wealth_path"]]
    assert len(page.query_selector_all("#page-timeline .wline")) == len(tested)
    titles = page.eval_on_selector_all("#page-timeline .wpt title", "els => els.map(e => e.textContent)")
    for d in tested:
        for j, w in enumerate(d["ledger"]["exposure"]["wealth_path"]):
            assert any(t.startswith(f"{d['id']} W_{j + 1} = {w:.2f}") for t in titles), (d["id"], j)
    assert "1/α = 3.33" in page.inner_text("#page-timeline")
    assert f"drift round {DOC['run']['drift_round']}" in page.inner_text("#page-timeline")
    for c in DOC["git_log"]:
        assert c["subject"] in page.inner_text("#page-timeline .gitlog")
    d = tested[0]
    page.click(f"#page-timeline .lane[data-decision='{d['id']}']")
    detail = page.inner_text("#page-timeline .tl-detail")
    assert d["payload"] in detail and f"{d['ledger']['exposure']['wealth']:.2f}" in detail
    lens = next(x for x in DOC["lenses"] if x["minted_round"] is not None)
    lts = page.eval_on_selector_all(f"#page-timeline .lline[data-lens='{lens['id']}'] title",
                                    "els => els.map(e => e.textContent)")
    for v in lens["value_history"]:
        want = f"unread ({v['n']} answers)" if v["value"] is None else f"{v['value']:.3f} over {v['n']} answers"
        assert any(want in t for t in lts), (lens["id"], v)
    assert page.errors == []


def test_readout_shows_mean_p_and_lower_bound_against_the_claim_threshold(page, served):
    open_tab(page, served.url, "readout")
    r = DOC["readout"]
    for key in ("r1", "r2"):
        c = r[key]
        rid = c["reading"]
        assert page.inner_text(f"[data-fig='{rid}-mean']") == f3(c["mean"])
        assert page.inner_text(f"[data-fig='{rid}-p']") == f"{c['p']:.4f}"
        bound = "−∞" if c["lower_bound"] is None else f3(c["lower_bound"])
        assert page.inner_text(f"[data-fig='{rid}-bound']") == bound
        card = page.inner_text(f".rd-card[data-reading='{rid}']")
        assert f"claim above {f3(c['claim_above'])}" in card
        assert c["population"] in card
        assert page.get_attribute(f".rd-card[data-reading='{rid}'] .verdict", "data-verdict") == c["verdict"]
    assert page.query_selector("#page-readout .bound-line") is not None
    t = r["tokens_composed"]
    assert page.inner_text("[data-fig='token-ratio']") == f3(t["journal"]["mean"] / t["notes"]["mean"])
    assert page.get_attribute(".rd-card[data-reading='R3'] .verdict", "data-verdict") == r["r3"]["verdict"]
    assert page.get_attribute(".rd-card[data-reading='R4'] .verdict", "data-verdict") == r["r4"]["verdict"]
    text = page.inner_text("#page-readout")
    assert "interval" not in text.lower()
    assert page.errors == []


def test_unread_readings_and_a_null_bound_read_as_unread_and_minus_infinity_never_zero(browser, tmp_path):
    doc = copy.deepcopy(DOC)
    r = doc["readout"]
    r["r1"]["lower_bound"] = None
    r["r2"].update(population=None, n_categories=None, mean=None, p=None, lower_bound=None, verdict=None,
                   unread_categories=list(r["held_out"]))
    r["r3"].update(verdict=None, population=None, c=None, alpha_c=None)
    r["tokens_composed"]["journal"]["mean"] = None
    r["hook_transfer"].update(population=None, requests_fired=[], untagged=list(r["held_out"]))
    (tmp_path / "export.json").write_text(json.dumps(doc))
    srv = Served(P["spec"], P["db"], tmp_path / "export.json", P["artifacts"])
    try:
        ctx = browser.new_context(viewport=VIEW)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        open_tab(page, srv.url, "readout")
        assert page.inner_text("[data-fig='R1-bound']") == "−∞"
        assert "bound −∞" in page.text_content(".rd-card[data-reading='R1'] .bound-line")
        r2 = page.inner_text(".rd-card[data-reading='R2']")
        assert "unread" in r2 and "0.000" not in r2.split("tokens")[0]
        assert page.get_attribute(".rd-card[data-reading='R2'] .verdict", "data-verdict") == "unread"
        assert page.get_attribute(".rd-card[data-reading='R3'] .verdict", "data-verdict") == "unread"
        assert page.inner_text("[data-fig='token-ratio']") == "unread"
        assert "untagged" in page.inner_text(".rd-card[data-reading='Hook transfer']")
        assert page.query_selector(".rd-card[data-reading='R2'] .v-earned") is None
        assert errors == []
        ctx.close()
    finally:
        srv.close()


def test_live_view_applies_a_span_that_opened_and_closed_between_two_polls_without_rebuilding(browser, tmp_path):
    db = tmp_path / "live.db"
    conn = connect_telemetry(db, busy_timeout_ms=1000)
    tel = SqliteTelemetry(conn, Scope("main"))
    srv = Served(P["spec"], db, P["export"], P["artifacts"])
    try:
        ctx = browser.new_context(viewport=VIEW)
        page = ctx.new_page()
        requests = []
        page.on("request", lambda q: requests.append(q.url) if "/api/telemetry" in q.url else None)
        open_tab(page, srv.url, "live")
        rt = tel.scoped(round=1)
        with rt.span("round", regime="R1", requests=1, pairs_planned=1):
            pa = rt.scoped(pass_id="r01-C-900-journal-0")
            with pa.span("pass", request_id="C-900", cell="data tool / typing", arm="journal", pair_slot=0), \
                    pa.span("pass.worker", model="m"):
                pass
            page.wait_for_selector("#page-live .card[data-pass='r01-C-900-journal-0']")
            card = "#page-live .card[data-pass='r01-C-900-journal-0']"
            page.eval_on_selector(card, "el => { el.dataset.mark = 'same'; }")
            # Opened and closed between two polls: the eval and the outcome land in one burst.
            with pa.span("pass", request_id="C-900", cell="data tool / typing", arm="journal", pair_slot=1):
                pass
            with pa.span("eval.probe"):
                pass
            pa.metric("pass.loss", 0.4321, "score")
            pa.event("pass.outcome", outcome="scored", error="")
        page.wait_for_selector("#page-live .card[data-pass='r01-C-900-journal-0'].is-scored")
        assert page.inner_text("#page-live .card[data-pass='r01-C-900-journal-0'] .card-loss") == "0.432"
        assert page.get_attribute("#page-live .card[data-pass='r01-C-900-journal-0']", "data-mark") == "same"
        seqs = [int(u.split("after_seq=")[1].split("&")[0]) for u in requests]
        assert seqs == sorted(seqs) and seqs[-1] > 0
        # Leaving the tab stops the poll timer.
        page.goto(f"{srv.url}/#gallery")
        page.wait_for_timeout(300)
        n = len(requests)
        page.wait_for_timeout(2500)
        assert len(requests) == n
        ctx.close()
    finally:
        srv.close()
        conn.close()


@pytest.mark.parametrize("readout_pass_round", [4, None])
def test_live_view_reads_a_readout_span_at_run_scope_as_the_readout_not_round_n_plus_one(
        browser, tmp_path, readout_pass_round):
    db = tmp_path / "live.db"
    conn = connect_telemetry(db, busy_timeout_ms=1000)
    tel = SqliteTelemetry(conn, Scope("main"))
    srv = Served(P["spec"], db, P["export"], P["artifacts"])
    try:
        for n in (1, 2, 3):
            rt = tel.scoped(round=n)
            pa = rt.scoped(pass_id=f"r0{n}-C-900-journal-0")
            with rt.span("round", regime="R1", requests=1, pairs_planned=1), \
                    pa.span("pass", request_id="C-900", arm="journal"):
                pass
        # The readout span at run scope (round unknown); its passes carry round 4 or no round.
        with tel.span("readout", passes=1, reps=1):
            pa = tel.scoped(round=readout_pass_round, pass_id="ro-C-901-empty-0")
            with pa.span("pass", request_id="C-901", arm="empty"):
                pass
            pa.metric("pass.loss", 0.25, "score")
            pa.event("pass.outcome", outcome="scored", error="")
        ctx = browser.new_context(viewport=VIEW)
        page = ctx.new_page()
        open_tab(page, srv.url, "live")
        page.wait_for_selector("#page-live .card[data-pass='ro-C-901-empty-0'].is-scored")
        assert page.inner_text("#page-live .round-title") == "Readout"
        assert "finished" in page.inner_text("#page-live .round-sub")
        dots = page.eval_on_selector_all("#page-live .dot", "els => els.map(e => e.textContent)")
        assert dots[:3] == ["1", "2", "3drift"] and dots[3] == "readout" and len(dots) == 4, dots
        page.click("#page-live .dot[data-round='2']")
        page.wait_for_selector("#page-live .card[data-pass='r02-C-900-journal-0']")
        assert "Round 2 of 3" in page.inner_text("#page-live .round-title")
        ctx.close()
    finally:
        srv.close()
        conn.close()


def test_gallery_reads_cells_from_pass_spans_and_reads_unread_without_the_log(browser, tmp_path):
    empty = tmp_path / "none.db"
    srv = Served(P["spec"], empty, P["export"], P["artifacts"])
    try:
        ctx = browser.new_context(viewport=VIEW)
        page = ctx.new_page()
        open_tab(page, srv.url, "gallery")
        assert "cell unread" in page.inner_text("#page-gallery .gallery-grid")
        ctx.close()
    finally:
        srv.close()
    conn = sqlite3.connect(P["db"])
    assert conn.execute("SELECT COUNT(*) FROM telemetry WHERE name='pass' AND attrs LIKE '%cell%'").fetchone()[0] > 0


def test_gallery_filters_by_a_cell_the_pass_span_records_as_a_list(page, served):
    conn = sqlite3.connect(P["db"])
    rows = conn.execute("SELECT pass_id, attrs FROM telemetry WHERE name='pass' AND kind='span'").fetchall()
    cells = {pid: json.loads(a)["cell"] for pid, a in rows if json.loads(a).get("cell")}
    assert cells and all(isinstance(c, list) for c in cells.values())
    doc = json.loads(P["export"].read_text())
    built = {p["id"] for p in doc["passes"] if p.get("artifact_path")}
    family, mode = next(c for pid, c in cells.items() if pid in built)
    want = sum(1 for pid, c in cells.items() if pid in built and c == [family, mode])
    open_tab(page, served.url, "gallery")
    options = page.eval_on_selector_all("#page-gallery select[data-filter='cell'] option",
                                        "els => els.map(e => e.value)")
    assert len(options) == len(set(options)) and f"{family} / {mode}" in options
    page.select_option("#page-gallery select[data-filter='cell']", f"{family} / {mode}")
    assert f"showing {want} apps" in page.inner_text("#page-gallery .page-head")
    assert want > 0


def test_live_view_keeps_a_finished_readout_done_when_a_second_readout_span_opens(browser, tmp_path):
    db = tmp_path / "live.db"
    conn = connect_telemetry(db, busy_timeout_ms=1000)
    tel = SqliteTelemetry(conn, Scope("main"))
    srv = Served(P["spec"], db, P["export"], P["artifacts"])
    opened = []
    try:
        with tel.span("readout", passes=1, reps=1):
            pa = tel.scoped(round=4, pass_id="ro-C-901-empty-0")
            with pa.span("pass", request_id="C-901", arm="empty"):
                pass
            pa.event("pass.outcome", outcome="scored", error="")
        second = tel.span("readout", passes=0, reps=1)
        second.__enter__()          # run_readout's own readout span, still open
        opened.append(second)
        ctx = browser.new_context(viewport=VIEW)
        page = ctx.new_page()
        open_tab(page, srv.url, "live")
        page.wait_for_selector("#page-live .card[data-pass='ro-C-901-empty-0']")
        page.wait_for_timeout(500)
        assert "finished" in page.inner_text("#page-live .round-sub")
        ctx.close()
    finally:
        for sp in opened:
            sp.__exit__(None, None, None)
        srv.close()
        conn.close()
