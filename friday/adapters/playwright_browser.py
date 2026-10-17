"""Playwright implementation of the Browser port. Every request other than the artifact's own file is aborted,
and Chromium sends all other traffic (WebSockets, which request routing does not see) to a proxy address that
refuses connections: the artifact is untrusted generated code and must load nothing (SPEC § 5).

Every call into the page is bounded by a timeout, so a page whose main thread hangs fails that call instead of
blocking the eval. A Playwright failure inside `PlaywrightBrowser.page` leaves it as the builtin
`TimeoutError` (the page did not answer in time) or `ConnectionError` (the page failed: a load error, a crash),
so callers can score a browser failure without importing Playwright, and every other exception still raises.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from friday.domain.types import Control, ControlKind


def _wrapped(js: str) -> str:
    """`js` as a function whose result is always truthy ({v: value}), for `wait_for_function`.

    In-page reads go through `wait_for_function` (bounded), never `evaluate` (unbounded), so a page whose main
    thread hangs (an infinite loop started by a click) times out instead of blocking the eval forever.
    """
    return "(arg) => ({ v: (" + js + ")(arg) })"


_CONTROLS_JS = """() => {
  const sel = 'button, input, select, textarea, canvas, a[href], [contenteditable], [tabindex], [onclick], '
            + '[role=button], [role=checkbox], [role=tab], [role=link], [role=menuitem], [role=switch]';
  const textTypes = new Set(['', 'text', 'search', 'email', 'number', 'password', 'tel', 'url']);
  const buttonTypes = new Set(['button', 'submit', 'reset', 'image']);
  const choiceTypes = new Set(['checkbox', 'radio', 'range']);
  const all = Array.from(document.body ? document.body.querySelectorAll('*') : []);
  const seen = new Set(Array.from(document.querySelectorAll(sel)));
  for (const e of all) { if (typeof e.onclick === 'function') seen.add(e); }
  const idCount = {};
  for (const e of all) { if (e.id) idCount[e.id] = (idCount[e.id] || 0) + 1; }
  const safeId = (e) => e.id && idCount[e.id] === 1 && /^[A-Za-z][\\w-]*$/.test(e.id);
  const path = (e) => {
    const parts = [];
    let n = e;
    while (n && n.nodeType === 1 && n !== document.documentElement) {
      if (safeId(n)) { parts.unshift('#' + n.id); return parts.join(' > '); }
      const i = Array.prototype.indexOf.call(n.parentElement.children, n) + 1;
      parts.unshift(n.tagName.toLowerCase() + ':nth-child(' + i + ')');
      n = n.parentElement;
    }
    parts.unshift('html');
    return parts.join(' > ');
  };
  const kindOf = (e) => {
    const t = e.tagName;
    if (t === 'BUTTON') return 'button';
    if (t === 'SELECT') return 'select';
    if (t === 'TEXTAREA') return 'text';
    if (t === 'CANVAS') return 'canvas';
    if (t === 'INPUT') {
      const ty = (e.getAttribute('type') || '').toLowerCase();
      if (buttonTypes.has(ty)) return 'button';
      if (choiceTypes.has(ty)) return 'checkbox';
      if (textTypes.has(ty)) return 'text';
      return 'other';
    }
    if (e.isContentEditable) return 'text';
    return 'other';
  };
  const out = [];
  for (const e of all) {
    if (!seen.has(e)) continue;
    if (e.tagName === 'INPUT' && (e.getAttribute('type') || '').toLowerCase() === 'hidden') continue;
    if (e.disabled) continue;
    if (e.parentElement && e.parentElement.isContentEditable) continue;
    const visible = e.checkVisibility ? e.checkVisibility() : e.getClientRects().length > 0;
    if (!visible) continue;
    out.push({selector: path(e), kind: kindOf(e), id: (e.id && idCount[e.id] === 1) ? '#' + e.id : null});
  }
  return out;
}"""

# The visible state: text of visible nodes (editable fields excluded: what is typed into a field is the
# input, not its effect), values of fields the user does not edit directly (readonly or disabled), each
# element's visibility, and each canvas's pixels.
_SNAPSHOT_JS = """() => {
  const vis = (e) => e.checkVisibility ? e.checkVisibility() : e.getClientRects().length > 0;
  const texts = [];
  if (document.body) {
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      const p = n.parentElement;
      if (!p || ['SCRIPT', 'STYLE', 'TEXTAREA', 'NOSCRIPT', 'TEMPLATE'].includes(p.tagName)) continue;
      if (p.isContentEditable || !vis(p)) continue;
      const t = n.textContent.trim();
      if (t) texts.push(t);
    }
  }
  const all = Array.from(document.body ? document.body.querySelectorAll('*') : []);
  const shown = all.map((e) => vis(e) ? '1' : '0').join('');
  const fixed = all.filter((e) => (e.tagName === 'INPUT' || e.tagName === 'TEXTAREA') && (e.readOnly || e.disabled))
                   .map((e) => e.value);
  const canvases = all.filter((e) => e.tagName === 'CANVAS').map((c) => {
    try { return c.toDataURL(); } catch (err) { return 'unreadable'; }
  });
  return JSON.stringify([texts, shown, fixed, canvases]);
}"""

# Visible as Playwright reads it (rendered, not visibility:hidden, a non-empty box) and inside the viewport.
_VISIBLE_JS = """(s) => {
  const e = document.querySelector(s);
  if (!e) return false;
  const shown = e.checkVisibility ? e.checkVisibility({visibilityProperty: true}) : e.getClientRects().length > 0;
  if (!shown) return false;
  const r = e.getBoundingClientRect();
  return r.width > 0 && r.height > 0 && r.top < innerHeight && r.left < innerWidth && r.bottom > 0 && r.right > 0;
}"""

_HAS_FOCUS_JS = "() => !!document.activeElement && document.activeElement !== document.body"

_CHANGE_JS = """(s) => {
  const e = document.querySelector(s);
  if (e) e.dispatchEvent(new Event('change', {bubbles: true}));
  return true;
}"""

# Starts `source` once (an expression, or a function that is called), keeps its settled result under `key`,
# and reads null until it settles: `wait_for_function` polls this, so an async script (axe.run) is bounded too.
_SCRIPT_JS = """() => {{
  const k = {key};
  if (!(k in window)) {{
    window[k] = null;
    Promise.resolve()
      .then(() => {{ const f = ({source}); return typeof f === 'function' ? f() : f; }})
      .then((v) => {{ window[k] = {{ok: true, v: v === undefined ? null : v}}; }},
            (e) => {{ window[k] = {{ok: false, e: String((e && e.stack) || e)}}; }});
  }}
  return window[k];
}}"""

_CHOOSE_JS = """(s) => {
  const e = document.querySelector(s);
  if (!e) return 'absent';
  if (e.tagName === 'SELECT') {
    return e.options.length ? 'select:' + ((e.selectedIndex + 1) % e.options.length) : 'empty';
  }
  if (e.tagName === 'INPUT') {
    const ty = (e.getAttribute('type') || '').toLowerCase();
    if (ty === 'range') return 'range';
    if (ty === 'checkbox' || ty === 'radio') return 'toggle';
  }
  return 'click';
}"""


class PlaywrightPage:
    def __init__(self, page: Any, url: str, errors: list[str], action_timeout_ms: int,
                 script_timeout_ms: int | None = None) -> None:
        self._page, self._url, self._errors, self._t = page, url, errors, action_timeout_ms
        self._script_t = script_timeout_ms if script_timeout_ms is not None else action_timeout_ms

    def _eval(self, js: str, arg: Any = None) -> Any:
        """`js(arg)` evaluated in the page; raises a timeout instead of hanging on a stuck page."""
        handle = self._page.wait_for_function(_wrapped(js), arg=arg, timeout=self._t)
        try:
            return handle.json_value()["v"]
        finally:
            handle.dispose()

    def goto(self, query: str) -> None:
        self._errors.clear()
        self._page.goto(self._url + query)

    def click(self, selector: str) -> None:
        self._page.click(selector, timeout=self._t)

    def type(self, selector: str, text: str) -> None:
        """Clears the field, types `text` key by key (key events fire) and fires `change` on it. The field
        keeps focus, so a following `key` step reaches it (SPEC § 4: keys go to the focused element)."""
        loc = self._page.locator(selector).first
        loc.fill("", timeout=self._t)
        loc.press_sequentially(text, timeout=self._t)
        self._eval(_CHANGE_JS, selector)

    def key(self, name: str, times: int = 1) -> None:
        """Presses `name` on the focused element (the body when nothing has focus), each press bounded by the
        action timeout: `keyboard.press` has no timeout and hangs on a key handler that never returns."""
        for _ in range(times):
            target = self._page.locator(":focus" if self._eval(_HAS_FOCUS_JS) else "body").first
            target.press(name, timeout=self._t)

    def focus(self, selector: str) -> None:
        self._page.focus(selector, timeout=self._t)

    def wait(self, ms: int) -> None:
        self._page.wait_for_timeout(ms)

    def exists(self, selector: str) -> bool:
        return bool(self._eval("(s) => document.querySelector(s) !== null", selector))

    def visible(self, selector: str) -> bool:
        """Rendered with a non-empty box that intersects the viewport."""
        return bool(self._eval(_VISIBLE_JS, selector))

    def focused(self, selector: str) -> bool:
        return bool(self._eval(
            "(s) => { const e = document.querySelector(s); return !!e && e === document.activeElement; }", selector))

    def text(self, selector: str) -> str | None:
        return self._eval(
            "(s) => { const e = document.querySelector(s); if (!e) return null;"
            " return ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.tagName) ? e.value : e.innerText; }", selector)

    def canvas_hash(self, selector: str) -> str | None:
        data = self._eval(
            "(s) => { const c = document.querySelector(s);"
            " if (!c || !c.toDataURL) return null; try { return c.toDataURL(); } catch (e) { return 'unreadable'; } }",
            selector)
        return hashlib.sha256(data.encode()).hexdigest() if data else None

    def console_errors(self) -> list[str]:
        return list(self._errors)

    def screenshot(self) -> bytes:
        return self._page.screenshot(full_page=False, timeout=self._t)

    def controls(self) -> list[Control]:
        """Visible, enabled operable elements in document order, each with a selector unique to it."""
        rows = self._eval(_CONTROLS_JS)
        return [Control(r["selector"], ControlKind(r["kind"]), r["id"]) for r in rows]

    def snapshot(self) -> str:
        """A sha256 of the visible state (what `_SNAPSHOT_JS` collects)."""
        state = self._eval(_SNAPSHOT_JS)
        return hashlib.sha256(json.dumps(state).encode()).hexdigest()

    def choose(self, selector: str) -> None:
        how = self._eval(_CHOOSE_JS, selector)
        if how == "absent":
            raise LookupError(selector)
        if how.startswith("select:"):
            self._page.select_option(selector, index=int(how.split(":")[1]), timeout=self._t)
        elif how == "range":
            self._page.locator(selector).first.press("ArrowRight", timeout=self._t)
        elif how in ("toggle", "click"):
            self._page.click(selector, timeout=self._t)
        else:
            raise LookupError(f"{selector}: nothing to choose ({how})")

    def run_script(self, source: str) -> object:
        """`source` (an expression, or a function that is called and may be async) evaluated in the page; its
        JSON-able result. Bounded by the script timeout; a script that throws raises RuntimeError."""
        key = json.dumps(f"__friday_script_{uuid.uuid4().hex}")
        handle = self._page.wait_for_function(_SCRIPT_JS.format(key=key, source=source),
                                              timeout=self._script_t, polling=50)
        try:
            out = handle.json_value()
        finally:
            handle.dispose()
        self._eval("(k) => { delete window[k]; return true; }", json.loads(key))
        if not out["ok"]:
            raise RuntimeError(f"eval-side script failed: {out['e']}")
        return out.get("v")


def _bare(url: str) -> str:
    return url.split("#", 1)[0].split("?", 1)[0]


def _router(artifact_url: str):
    """A route handler that lets the artifact's own URL through (any query) and aborts every other request,
    other local files included: the artifact may not render other files into its screenshot."""
    def route_(route: Any) -> None:
        if _bare(route.request.url) == artifact_url:
            route.continue_()
        else:
            route.abort()
    return route_


# Every connection Chromium makes goes to this refusing proxy, loopback included (`<-loopback>` removes the
# implicit loopback bypass). `route.abort` covers HTTP(S) and file requests; this covers WebSockets.
# (`route_web_socket` with `ws.close()` deadlocks the sync API, and without the close it mocks an open socket.)
_REFUSING_PROXY = ("--proxy-server=127.0.0.1:9", "--proxy-bypass-list=<-loopback>")


class PlaywrightBrowser:
    """One browser per process: the sync API is not thread-safe, so parallel passes use one process each."""

    def __init__(self, action_timeout_ms: int, load_timeout_ms: int, headless: bool = True) -> None:
        from playwright.sync_api import sync_playwright

        self._action_ms, self._load_ms = action_timeout_ms, load_timeout_ms
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(headless=headless, args=list(_REFUSING_PROXY))
        except BaseException:
            self._pw.stop()
            raise

    @contextmanager
    def page(self, artifact_path: str, viewport: tuple[int, int]) -> Iterator[PlaywrightPage]:
        """A fresh context and page per call (no state shared between pages), closed on every path. A
        Playwright failure while it is open raises as TimeoutError or ConnectionError (module docstring)."""
        from playwright.sync_api import Error
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        ctx = self._browser.new_context(viewport={"width": viewport[0], "height": viewport[1]},
                                        service_workers="block")
        try:
            url = Path(artifact_path).resolve().as_uri()
            ctx.route("**/*", _router(url))
            ctx.set_default_navigation_timeout(self._load_ms)
            ctx.set_default_timeout(self._action_ms)
            page = ctx.new_page()
            errors: list[str] = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            try:
                page.goto(url)
                yield PlaywrightPage(page, url, errors, self._action_ms, self._load_ms)
            except PlaywrightTimeout as e:
                raise TimeoutError(str(e)) from e
            except Error as e:
                raise ConnectionError(str(e)) from e
        finally:
            ctx.close()

    def close(self) -> None:
        self._browser.close()
        self._pw.stop()
