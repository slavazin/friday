"""Headless browser seam for the eval. The check DSL interpreter drives a `Page`; the adapter is Playwright."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

from friday.domain.types import Control, Viewport


class Page(Protocol):
    def goto(self, query: str) -> None:
        """Reloads the artifact with `query` appended (e.g. '?test=1')."""
        ...

    def click(self, selector: str) -> None: ...
    def type(self, selector: str, text: str) -> None: ...
    def key(self, name: str, times: int = 1) -> None:
        """Sends `name` `times` times to the focused element."""
        ...

    def focus(self, selector: str) -> None: ...
    def wait(self, ms: int) -> None: ...
    def exists(self, selector: str) -> bool: ...
    def visible(self, selector: str) -> bool: ...
    def focused(self, selector: str) -> bool: ...
    def text(self, selector: str) -> str | None:
        """The element's text, or None when absent."""
        ...

    def canvas_hash(self, selector: str) -> str | None:
        """A hash of the canvas pixels, or None when absent or not a canvas."""
        ...

    def console_errors(self) -> list[str]: ...
    def screenshot(self) -> bytes: ...

    def controls(self) -> list[Control]:
        """Operable elements on the page in document order: buttons, text inputs, selects, checkboxes,
        canvases, and other elements with a tab index or a click handler."""
        ...

    def snapshot(self) -> str:
        """A digest of the visible state: rendered text, input values, canvas pixels, element visibility."""
        ...

    def choose(self, selector: str) -> None:
        """Selects the next option of a <select> (wrapping), or toggles a checkbox or radio."""
        ...


class Browser(Protocol):
    def page(self, artifact_path: str, viewport: Viewport) -> AbstractContextManager[Page]:
        """A fresh page loaded from `artifact_path`, every network route aborted."""
        ...


class QualityProbe(Protocol):
    def eslint_errors(self, html: str) -> int:
        """ESLint error count over the extracted inline script, under the fixed config."""
        ...

    def axe_violations(self, page: Page) -> int:
        """axe-core violation count on the loaded page."""
        ...
