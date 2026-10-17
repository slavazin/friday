"""Playwright implementation of the Browser port. Every network route is aborted: the artifact is
untrusted generated code and must load nothing (SPEC § 5)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class PlaywrightPage:
    def __init__(self, page: Any, url: str, errors: list[str], action_timeout_ms: int) -> None:
        self._page, self._url, self._errors, self._t = page, url, errors, action_timeout_ms

    def goto(self, query: str) -> None:
        self._errors.clear()
        self._page.goto(self._url + query)

    def click(self, selector: str) -> None:
        self._page.click(selector, timeout=self._t)

    def type(self, selector: str, text: str) -> None:
        self._page.fill(selector, text, timeout=self._t)

    def key(self, name: str, times: int = 1) -> None:
        for _ in range(times):
            self._page.keyboard.press(name)

    def focus(self, selector: str) -> None:
        self._page.focus(selector, timeout=self._t)

    def wait(self, ms: int) -> None:
        self._page.wait_for_timeout(ms)

    def exists(self, selector: str) -> bool:
        return self._page.query_selector(selector) is not None

    def visible(self, selector: str) -> bool:
        el = self._page.query_selector(selector)
        if el is None or not el.is_visible():
            return False
        box = el.bounding_box()
        vp = self._page.viewport_size
        return bool(box and vp and box["y"] < vp["height"] and box["x"] < vp["width"]
                    and box["y"] + box["height"] > 0 and box["x"] + box["width"] > 0)

    def focused(self, selector: str) -> bool:
        return bool(self._page.evaluate(
            "(s) => { const e = document.querySelector(s); return !!e && e === document.activeElement; }", selector))

    def text(self, selector: str) -> str | None:
        el = self._page.query_selector(selector)
        if el is None:
            return None
        is_field = el.evaluate("e => ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.tagName)")
        return el.input_value() if is_field else el.inner_text()

    def canvas_hash(self, selector: str) -> str | None:
        data = self._page.evaluate(
            "(s) => { const c = document.querySelector(s); return c && c.toDataURL ? c.toDataURL() : null; }", selector)
        return hashlib.sha256(data.encode()).hexdigest() if data else None

    def console_errors(self) -> list[str]:
        return list(self._errors)

    def screenshot(self) -> bytes:
        return self._page.screenshot(full_page=False)


class PlaywrightBrowser:
    """One browser per process: the sync API is not thread-safe, so parallel passes use one process each."""

    def __init__(self, action_timeout_ms: int, load_timeout_ms: int, headless: bool = True) -> None:
        from playwright.sync_api import sync_playwright

        self._action_ms, self._load_ms = action_timeout_ms, load_timeout_ms
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=headless)

    @contextmanager
    def page(self, artifact_path: str, viewport: tuple[int, int]) -> Iterator[PlaywrightPage]:
        ctx = self._browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
        try:
            ctx.route("**/*", lambda r: r.continue_() if r.request.url.startswith("file:") else r.abort())
            ctx.set_default_navigation_timeout(self._load_ms)
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
