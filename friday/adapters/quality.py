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
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from friday.config import RunSpec
from friday.ports.browser import Page

# The HTML spec's "JavaScript MIME type essence" list: a `<script>` of one of these types runs as a classic
# script; `module` runs as a module script; any other type (e.g. application/json) is data.
JS_TYPES = frozenset({
    "application/ecmascript", "application/javascript", "application/x-ecmascript", "application/x-javascript",
    "text/ecmascript", "text/javascript", "text/javascript1.0", "text/javascript1.1", "text/javascript1.2",
    "text/javascript1.3", "text/javascript1.4", "text/javascript1.5", "text/jscript", "text/livescript",
    "text/x-ecmascript", "text/x-javascript",
})
ESLINT_SCRIPT = Path(__file__).with_name("eslint_count.mjs")
AXE_RELATIVE = Path("node_modules/axe-core/axe.min.js")
NODE_PACKAGES = (Path("node_modules/eslint/package.json"), Path("node_modules/globals/package.json"))
NODE_TIMEOUT_S = 60


class QualityUnavailable(RuntimeError):
    """The Node tools behind Q are not installed or not runnable."""


class QualityToolFailed(RuntimeError):
    """ESLint or axe-core ran but did not return a count."""


def script_kind(attrs: dict[str, str]) -> str | None:
    """How the browser runs an inline `<script>` with these (lower-cased) attributes, as the HTML spec
    prepares a script: "classic", "module", or None when it is data or loaded from `src`."""
    if "src" in attrs:
        return None
    if "type" in attrs:
        kind = attrs["type"].strip(" \t\n\f\r").lower()
    elif attrs.get("language", ""):
        kind = "text/" + attrs["language"].lower()
    else:
        kind = ""
    if kind == "" or kind in JS_TYPES:
        return "classic"
    return "module" if kind == "module" else None


@dataclass(frozen=True)
class InlineCode:
    """A page's inline JavaScript as the browser compiles it: classic pieces in document order (inline
    classic scripts, then each `on*` handler body wrapped in its own function expression) and module bodies."""

    classic: tuple[str, ...]
    modules: tuple[str, ...]


class _InlineCode(HTMLParser):
    """Collects inline `<script>` bodies by kind (no `src`, JavaScript types only) and inline `on*` handler bodies."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[str] = []
        self.modules: list[str] = []
        self.handlers: list[str] = []
        self._kind: str | None = None
        self._buf: list[str] = []

    def _attrs(self, attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        a = {k.lower(): (v or "") for k, v in attrs}
        self.handlers += [v for k, v in a.items() if k.startswith("on") and v.strip()]
        return a

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = self._attrs(attrs)
        if tag == "script":
            self._kind = script_kind(a)
            self._buf = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._attrs(attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            if self._kind == "classic":
                self.scripts.append("".join(self._buf))
            elif self._kind == "module":
                self.modules.append("".join(self._buf))
            self._kind = None

    def handle_data(self, data: str) -> None:
        if self._kind is not None:
            self._buf.append(data)


def inline_code(html: str) -> InlineCode:
    """The artifact's inline JavaScript, split as the browser compiles it.

    Classic inline scripts (no `src`; JavaScript MIME types only, so the how-to JSON is left out) share one
    global scope. Each inline event-handler attribute (`onclick="start()"`) follows wrapped in its own
    function expression, as the browser compiles it (`event` is a browser global): a function only a handler
    calls reads as used, and handler code is linted too. Module scripts are kept apart, since each is its
    own scope and source type.
    """
    parser = _InlineCode()
    parser.feed(html)
    parser.close()
    handlers = [f"(function () {{\n{h}\n}});" for h in parser.handlers]
    return InlineCode(tuple(parser.scripts) + tuple(handlers), tuple(parser.modules))


def inline_script(html: str) -> str:
    """The artifact's classic inline JavaScript (scripts, then wrapped handlers) joined as one source."""
    return "\n;\n".join(inline_code(html).classic)


class NodeQualityProbe:
    def __init__(self, root: Path, eslint_config: Path) -> None:
        self._root = Path(root)
        node = shutil.which("node")
        if node is None:
            raise QualityUnavailable("node is not on PATH: Q needs a local Node install")
        self._node = node
        for rel in (*NODE_PACKAGES, AXE_RELATIVE):
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
        """ESLint error count (severity 2) over `inline_code(html)`. A piece the browser would fail to compile
        (one classic script, one handler, one module) counts once and is left out; the rest is still linted."""
        code = inline_code(html)
        payload = json.dumps({"classic": list(code.classic), "modules": list(code.modules), "config": self._config})
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
