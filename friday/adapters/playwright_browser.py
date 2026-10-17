"""Playwright implementation of the Browser port. Every request other than the artifact's own file is aborted;
Chromium sends all other TCP traffic (WebSockets, which request routing does not see) to a proxy address that
refuses connections; WebRTC may use no UDP that bypasses that proxy, and its constructors are removed from every
frame before the artifact runs: the artifact is untrusted generated code and must load nothing (SPEC § 5).

Every call into the page is bounded by a timeout, so a page whose main thread hangs fails that call instead of
blocking the eval. Every Playwright failure, in a page method or while `PlaywrightBrowser.page` is open, raises
as the builtin `TimeoutError` (the page did not answer in time) or `ConnectionError` (the page failed: a load
error, a crash), so callers can score a browser failure without importing Playwright, and every other exception
still raises.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Any

from friday.domain.types import Control, ControlKind


def _wrapped(js: str) -> str:
    """`js` as a function whose result is a JSON string ('{"v": value}', always truthy), for `wait_for_function`.

    In-page reads go through `wait_for_function` (bounded), never `evaluate` (unbounded), so a page whose main
    thread hangs (an infinite loop started by a click) times out instead of blocking the eval forever. The
    result is a primitive, so Playwright holds its value with the handle: reading and disposing the handle make
    no further call into the page, which may have hung since the predicate returned. Residue: a hang that
    starts within the few milliseconds between the predicate returning and `wait_for_function` itself
    returning (a `setTimeout(loop, 0)` queued by the predicate) still blocks that call, past its timeout.
    """
    return "(arg) => JSON.stringify({ v: (" + js + ")(arg) })"


def _translated(fn: Callable) -> Callable:
    """`fn` with a Playwright timeout raised as TimeoutError and any other Playwright error as ConnectionError."""
    @wraps(fn)
    def call(*args: Any, **kwargs: Any) -> Any:
        from playwright.sync_api import Error
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        try:
            return fn(*args, **kwargs)
        except PlaywrightTimeout as e:
            raise TimeoutError(str(e)) from e
        except Error as e:
            raise ConnectionError(str(e)) from e
    return call


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

# The visible state, node by node: {key: value}. Text of visible text nodes (editable fields excluded: what is
# typed into a field is the input, not its effect), values of fields the user does not edit directly (readonly
# or disabled), each element's visibility, and each canvas's pixels. A key names its node by its element path.
_SNAPSHOT_JS = """() => {
  const vis = (e) => e.checkVisibility ? e.checkVisibility() : e.getClientRects().length > 0;
  const all = Array.from(document.body ? document.body.querySelectorAll('*') : []);
  const idCount = {};
  for (const e of all) { if (e.id) idCount[e.id] = (idCount[e.id] || 0) + 1; }
  const keys = new Map();
  const path = (e) => {
    if (keys.has(e)) return keys.get(e);
    let k;
    if (e === document.body || !e.parentElement) k = 'body';
    else if (e.id && idCount[e.id] === 1) k = '#' + e.id;
    else k = path(e.parentElement) + '>' + (Array.prototype.indexOf.call(e.parentElement.children, e) + 1);
    keys.set(e, k);
    return k;
  };
  const out = {};
  if (document.body) {
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      const p = n.parentElement;
      if (!p || ['SCRIPT', 'STYLE', 'TEXTAREA', 'NOSCRIPT', 'TEMPLATE'].includes(p.tagName)) continue;
      if (p.isContentEditable || !vis(p)) continue;
      const t = n.textContent.trim();
      if (t) out['t:' + path(p) + '/' + Array.prototype.indexOf.call(p.childNodes, n)] = t;
    }
  }
  for (const e of all) {
    const k = path(e);
    out['v:' + k] = vis(e) ? '1' : '0';
    if ((e.tagName === 'INPUT' || e.tagName === 'TEXTAREA') && (e.readOnly || e.disabled)) out['f:' + k] = e.value;
    if (e.tagName === 'CANVAS') {
      try { out['c:' + k] = e.toDataURL(); } catch (err) { out['c:' + k] = 'unreadable'; }
    }
  }
  return out;
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

# Commits a typed value the way a user leaving the field does: blur fires the browser's own `change` (once),
# then focus returns to the field unless a handler moved it elsewhere or removed the field.
_COMMIT_JS = """(s) => {
  const e = document.querySelector(s);
  if (e && document.activeElement === e) {
    e.blur();
    const a = document.activeElement;
    if (e.isConnected && (!a || a === document.body)) e.focus();
  }
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
  return window[k] === null ? null : JSON.stringify(window[k]);
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
        return json.loads(self._read(_wrapped(js), arg, self._t))["v"]

    def _read(self, js: str, arg: Any, timeout_ms: int, **kw: Any) -> str:
        """The JSON string `js` returns once truthy, in one bounded call (`_wrapped`)."""
        handle = self._page.wait_for_function(js, arg=arg, timeout=timeout_ms, **kw)
        try:
            out = handle.json_value()
        finally:
            handle.dispose()
        if not isinstance(out, str):
            raise TypeError(f"in-page read returned {type(out).__name__}, not a JSON string")
        return out

    @_translated
    def goto(self, query: str) -> None:
        self._errors.clear()
        self._page.goto(self._url + query)

    @_translated
    def click(self, selector: str) -> None:
        self._page.click(selector, timeout=self._t)

    @_translated
    def type(self, selector: str, text: str) -> None:
        """Clears the field, types `text` key by key (key events fire) and commits it: the browser fires
        `change` once, as on leaving the field. The field then has focus again, so a following `key` step
        reaches it (SPEC § 4: keys go to the focused element) and no second `change` fires later."""
        loc = self._page.locator(selector).first
        loc.fill("", timeout=self._t)
        loc.press_sequentially(text, timeout=self._t)
        self._eval(_COMMIT_JS, selector)

    @_translated
    def key(self, name: str, times: int = 1) -> None:
        """Presses `name` on the focused element (the body when nothing has focus), each press bounded by the
        action timeout: `keyboard.press` has no timeout and hangs on a key handler that never returns."""
        for _ in range(times):
            target = self._page.locator(":focus" if self._eval(_HAS_FOCUS_JS) else "body").first
            target.press(name, timeout=self._t)

    @_translated
    def focus(self, selector: str) -> None:
        self._page.focus(selector, timeout=self._t)

    @_translated
    def drag(self, selector: str, dx: int, dy: int) -> None:
        """Pointer down at the element's centre, a stepped move by (dx, dy), pointer up: mouse events and the
        pointer events the browser derives from them fire on the way."""
        box = self._page.locator(selector).first.bounding_box(timeout=self._t)
        if box is None:
            raise LookupError(f"{selector} has no box to drag")
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        mouse = self._page.mouse
        mouse.move(x, y)
        mouse.down()
        mouse.move(x + dx, y + dy, steps=8)
        mouse.up()

    @_translated
    def wait(self, ms: int) -> None:
        self._page.wait_for_timeout(ms)

    @_translated
    def exists(self, selector: str) -> bool:
        return bool(self._eval("(s) => document.querySelector(s) !== null", selector))

    @_translated
    def visible(self, selector: str) -> bool:
        """Rendered with a non-empty box that intersects the viewport."""
        return bool(self._eval(_VISIBLE_JS, selector))

    @_translated
    def focused(self, selector: str) -> bool:
        return bool(self._eval(
            "(s) => { const e = document.querySelector(s); return !!e && e === document.activeElement; }", selector))

    @_translated
    def text(self, selector: str) -> str | None:
        return self._eval(
            "(s) => { const e = document.querySelector(s); if (!e) return null;"
            " return ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.tagName) ? e.value : e.innerText; }", selector)

    @_translated
    def canvas_hash(self, selector: str) -> str | None:
        data = self._eval(
            "(s) => { const c = document.querySelector(s);"
            " if (!c || !c.toDataURL) return null; try { return c.toDataURL(); } catch (e) { return 'unreadable'; } }",
            selector)
        return hashlib.sha256(data.encode()).hexdigest() if data else None

    @_translated
    def console_errors(self) -> list[str]:
        return list(self._errors)

    @_translated
    def screenshot(self) -> bytes:
        return self._page.screenshot(full_page=False, timeout=self._t)

    @_translated
    def controls(self) -> list[Control]:
        """Visible, enabled operable elements in document order, each with a selector unique to it."""
        rows = self._eval(_CONTROLS_JS)
        return [Control(r["selector"], ControlKind(r["kind"]), r["id"]) for r in rows]

    @_translated
    def snapshot(self) -> str:
        """The visible state as a JSON object of node key -> sha256 of that node's state (`_SNAPSHOT_JS`)."""
        state = self._eval(_SNAPSHOT_JS)
        return json.dumps({k: hashlib.sha256(v.encode()).hexdigest()[:16] for k, v in state.items()},
                          sort_keys=True)

    @_translated
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

    @_translated
    def run_script(self, source: str) -> object:
        """`source` (an expression, or a function that is called and may be async) evaluated in the page; its
        JSON-able result. Bounded by the script timeout; a script that throws raises RuntimeError."""
        key = json.dumps(f"__friday_script_{uuid.uuid4().hex}")
        out = json.loads(self._read(_SCRIPT_JS.format(key=key, source=source), None, self._script_t, polling=50))
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
# WebRTC sends UDP (STUN, ICE) that no proxy sees: Chromium may use only proxied WebRTC traffic, and the
# constructors are gone from every frame before any artifact script runs.
_NO_UDP = ("--force-webrtc-ip-handling-policy=disable_non_proxied_udp",)
_NO_WEBRTC_JS = """for (const k of ['RTCPeerConnection', 'webkitRTCPeerConnection', 'RTCDataChannel',
                       'RTCIceCandidate', 'RTCSessionDescription']) {
  try { delete window[k]; } catch (e) {}
  try { Object.defineProperty(window, k, {value: undefined, configurable: false, writable: false}); } catch (e) {}
}"""


class PlaywrightBrowser:
    """One browser per process: the sync API is not thread-safe, so parallel passes use one process each."""

    def __init__(self, action_timeout_ms: int, load_timeout_ms: int, headless: bool = True) -> None:
        from playwright.sync_api import sync_playwright

        self._action_ms, self._load_ms = action_timeout_ms, load_timeout_ms
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(headless=headless, args=[*_REFUSING_PROXY, *_NO_UDP])
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
            ctx.add_init_script(_NO_WEBRTC_JS)
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
