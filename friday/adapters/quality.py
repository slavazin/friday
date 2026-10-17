"""The quality reading Q (SPEC § 5): ESLint errors over the inline script plus axe-core violations on the
loaded page. Implements `friday.ports.browser.QualityProbe`.

Both tools run from the repository's local Node install (`package.json` dev dependencies). A missing `node`,
a missing `node_modules` package or a missing config raises `QualityUnavailable` at construction, and a
tool that crashes or answers off-form raises at the call: Q is never read as 0 for want of a tool.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from friday.config import RunSpec
from friday.ports.browser import Page

# Script types the browser executes as JavaScript; any other type (e.g. application/json) is data.
JS_TYPES = frozenset({"", "text/javascript", "application/javascript", "application/ecmascript",
                      "text/ecmascript", "module"})
ESLINT_SCRIPT = Path(__file__).with_name("eslint_count.mjs")
AXE_RELATIVE = Path("node_modules/axe-core/axe.min.js")
ESLINT_RELATIVE = Path("node_modules/eslint/package.json")
NODE_TIMEOUT_S = 60


class QualityUnavailable(RuntimeError):
    """The Node tools behind Q are not installed or not runnable."""


class QualityToolFailed(RuntimeError):
    """ESLint or axe-core ran but did not return a count."""


class _InlineCode(HTMLParser):
    """Collects inline `<script>` bodies (no `src`, JavaScript types only) and inline `on*` handler bodies."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[str] = []
        self.handlers: list[str] = []
        self._in_js = False
        self._buf: list[str] = []

    def _attrs(self, attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        a = {k.lower(): (v or "") for k, v in attrs}
        self.handlers += [v for k, v in a.items() if k.startswith("on") and v.strip()]
        return a

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = self._attrs(attrs)
        if tag == "script":
            self._in_js = "src" not in a and a.get("type", "").strip().lower() in JS_TYPES
            self._buf = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._attrs(attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            if self._in_js:
                self.scripts.append("".join(self._buf))
            self._in_js = False

    def handle_data(self, data: str) -> None:
        if self._in_js:
            self._buf.append(data)


def inline_script(html: str) -> str:
    """The artifact's inline JavaScript as one source, in document order.

    Inline `<script>` bodies (no `src`; JavaScript types only, so the how-to JSON is left out) are
    concatenated, since they share one global scope in the page. Each inline event-handler attribute
    (`onclick="start()"`) follows wrapped in its own function expression, as the browser compiles it
    (`event` is a declared global in the config): a function only a handler calls reads as used, and
    handler code is linted too.
    """
    parser = _InlineCode()
    parser.feed(html)
    parser.close()
    parts = list(parser.scripts)
    parts += [f"(function () {{\n{h}\n}});" for h in parser.handlers]
    return "\n;\n".join(parts)


class NodeQualityProbe:
    def __init__(self, root: Path, eslint_config: Path) -> None:
        self._root = Path(root)
        node = shutil.which("node")
        if node is None:
            raise QualityUnavailable("node is not on PATH: Q needs a local Node install")
        self._node = node
        for rel in (ESLINT_RELATIVE, AXE_RELATIVE):
            if not (self._root / rel).is_file():
                raise QualityUnavailable(f"{self._root / rel} is missing: run `npm ci` in {self._root}")
        if not ESLINT_SCRIPT.is_file():
            raise QualityUnavailable(f"{ESLINT_SCRIPT} is missing")
        config_path = eslint_config if eslint_config.is_absolute() else self._root / eslint_config
        if not config_path.is_file():
            raise QualityUnavailable(f"ESLint config {config_path} is missing")
        config: dict[str, Any] = json.loads(config_path.read_text())
        config.pop("comment", None)
        self._config = config
        self._axe_source = (self._root / AXE_RELATIVE).read_text()

    @classmethod
    def from_spec(cls, spec: RunSpec, root: Path) -> NodeQualityProbe:
        """The probe under `eval.eslint_config`, with Node tools from `root`'s node_modules."""
        return cls(root, Path(spec.eval.eslint_config))

    def eslint_errors(self, html: str) -> int:
        """ESLint error count (severity 2, a parse error counts once) over `inline_script(html)`."""
        payload = json.dumps({"code": inline_script(html), "config": self._config})
        proc = subprocess.run(
            [self._node, str(ESLINT_SCRIPT)], input=payload, capture_output=True, text=True,
            cwd=self._root, timeout=NODE_TIMEOUT_S, check=False,
        )
        if proc.returncode != 0:
            raise QualityToolFailed(f"eslint exited {proc.returncode}: {proc.stderr.strip()[:500]}")
        try:
            out = json.loads(proc.stdout)
        except ValueError as e:
            raise QualityToolFailed(f"eslint wrote no JSON: {proc.stdout[:200]!r}") from e
        return _count(out.get("errors") if isinstance(out, dict) else None, "eslint")

    def axe_script(self) -> str:
        """The eval-side script `axe_violations` runs: an async function that injects axe-core, waits for
        the load event, and returns the number of violated axe rules."""
        return (
            "async () => {\n"
            "  if (document.readyState !== 'complete') {\n"
            "    await new Promise((r) => window.addEventListener('load', r, { once: true }));\n"
            "  }\n"
            f"{self._axe_source}\n"
            "  const result = await window.axe.run(document, { resultTypes: ['violations'] });\n"
            "  return result.violations.length;\n"
            "}"
        )

    def axe_violations(self, page: Page) -> int:
        """axe-core violation count on the loaded page: the number of axe rules with at least one violation."""
        return _count(page.run_script(self.axe_script()), "axe-core")


def _count(value: object, tool: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise QualityToolFailed(f"{tool} returned {value!r}, not a count")
    return value
