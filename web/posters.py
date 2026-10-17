"""Still posters of built apps, so the gallery shows every game frozen and runs only the one being hovered.

One background thread renders each built pass once in headless Chromium, under the eval's network refusal
(every connection goes to a refusing proxy, WebRTC is off), and keeps a JPEG per pass id. Nothing it writes is
read by the run: posters sit in their own folder beside the export.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from pathlib import Path

from friday.adapters.playwright_browser import _NO_UDP, _NO_WEBRTC_JS, _REFUSING_PROXY

VIEWPORT = {"width": 1280, "height": 800}
SETTLE_MS = 1200          # let the app draw its first frames before the still is taken
QUALITY = 70
RESCAN_S = 20.0           # how often the export is re-read for passes that have no poster yet


class Posters:
    """Renders missing posters for the pass ids `wanted()` returns, each from `<artifacts>/<id>/index.html`."""

    def __init__(self, folder: Path, artifacts: Path, wanted: Callable[[], list[str]]) -> None:
        self.folder, self.artifacts, self.wanted = folder, artifacts, wanted
        self.failed: set[str] = set()
        self._thread: threading.Thread | None = None

    def path(self, pass_id: str) -> Path:
        return self.folder / f"{pass_id}.jpg"

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="posters", daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        from playwright.sync_api import sync_playwright

        self.folder.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as pw:
            launch = lambda: pw.chromium.launch(headless=True, args=[*_REFUSING_PROXY, *_NO_UDP])  # noqa: E731
            browser = launch()
            while True:
                try:
                    todo = [i for i in self.wanted() if i not in self.failed and not self.path(i).exists()]
                except Exception:  # noqa: BLE001 - no export yet: try again on the next scan
                    todo = []
                for pass_id in todo:
                    if not browser.is_connected():      # an app took the browser down: start a fresh one
                        browser = launch()
                    try:
                        self._render(browser, pass_id)
                    except Exception:  # noqa: BLE001 - the browser died under this app: skip it, relaunch next
                        self.failed.add(pass_id)
                time.sleep(RESCAN_S if not todo else 0.5)

    def _render(self, browser, pass_id: str) -> None:  # noqa: ANN001 - a Playwright Browser
        src = self.artifacts / pass_id / "index.html"
        if not src.is_file():
            self.failed.add(pass_id)
            return
        ctx = browser.new_context(viewport=VIEWPORT)
        try:
            ctx.add_init_script(_NO_WEBRTC_JS)
            page = ctx.new_page()
            page.set_default_timeout(5000)
            page.set_content(src.read_text(errors="replace"), wait_until="load")
            page.wait_for_timeout(SETTLE_MS)
            tmp = self.path(pass_id).with_suffix(".tmp")
            page.screenshot(path=str(tmp), type="jpeg", quality=QUALITY)
            tmp.replace(self.path(pass_id))
        except Exception:  # noqa: BLE001 - a page that hangs or crashes gets no poster; the card shows its placeholder
            self.failed.add(pass_id)
        finally:
            with contextlib.suppress(Exception):   # a context the app crashed is already gone
                ctx.close()
