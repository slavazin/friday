"""Playwright implementation of the Browser port. Every network route that is not a file URL is aborted: the
artifact is untrusted generated code and must load nothing (SPEC § 5)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from friday.domain.types import Control, ControlKind


def _wrapped(js: str) -> str:
    """`js` as a function whose result is always truthy ({v: value}), for `wait_for_function`.

    Every in-page read goes through `wait_for_function`, so a page whose main thread hangs (an infinite loop
    started by a click) times out after the action timeout instead of blocking the eval forever.
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
    def __init__(self, page: Any, url: str, errors: list[str], action_timeout_ms: int) -> None:
        self._page, self._url, self._errors, self._t = page, url, errors, action_timeout_ms

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
        """Clears the field, types `text` key by key (key events fire), then blurs it (change fires)."""
        loc = self._page.locator(selector).first
        loc.fill("", timeout=self._t)
        loc.press_sequentially(text, timeout=self._t)
        loc.blur(timeout=self._t)

    def key(self, name: str, times: int = 1) -> None:
        for _ in range(times):
            self._page.keyboard.press(name)

    def focus(self, selector: str) -> None:
        self._page.focus(selector, timeout=self._t)

    def wait(self, ms: int) -> None:
        self._page.wait_for_timeout(ms)

    def exists(self, selector: str) -> bool:
        return bool(self._eval("(s) => document.querySelector(s) !== null", selector))

    def visible(self, selector: str) -> bool:
        el = self._page.query_selector(selector)
        if el is None or not el.is_visible():
            return False
        box = el.bounding_box()
        vp = self._page.viewport_size
        return bool(box and vp and box["y"] < vp["height"] and box["x"] < vp["width"]
                    and box["y"] + box["height"] > 0 and box["x"] + box["width"] > 0)

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
            self._page.focus(selector, timeout=self._t)
            self._page.keyboard.press("ArrowRight")
        elif how in ("toggle", "click"):
            self._page.click(selector, timeout=self._t)
        else:
            raise LookupError(f"{selector}: nothing to choose ({how})")

    def run_script(self, source: str) -> object:
        """`source` (an expression or a function) evaluated in the page; its JSON-able result."""
        return self._page.evaluate(source)


def _route(route: Any) -> None:
    """Lets file URLs through and aborts every other request."""
    if route.request.url.startswith("file:"):
        route.continue_()
    else:
        route.abort()


class PlaywrightBrowser:
    """One browser per process: the sync API is not thread-safe, so parallel passes use one process each."""

    def __init__(self, action_timeout_ms: int, load_timeout_ms: int, headless: bool = True) -> None:
        from playwright.sync_api import sync_playwright

        self._action_ms, self._load_ms = action_timeout_ms, load_timeout_ms
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(headless=headless)
        except BaseException:
            self._pw.stop()
            raise

    @contextmanager
    def page(self, artifact_path: str, viewport: tuple[int, int]) -> Iterator[PlaywrightPage]:
        """A fresh context and page per call (no state shared between pages), closed on every path."""
        ctx = self._browser.new_context(viewport={"width": viewport[0], "height": viewport[1]},
                                        service_workers="block")
        try:
            ctx.route("**/*", _route)
            ctx.set_default_navigation_timeout(self._load_ms)
            ctx.set_default_timeout(self._action_ms)
            page = ctx.new_page()
            errors: list[str] = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            url = Path(artifact_path).resolve().as_uri()
            page.goto(url)
            yield PlaywrightPage(page, url, errors, self._action_ms)
        finally:
            ctx.close()

    def close(self) -> None:
        self._browser.close()
        self._pw.stop()
