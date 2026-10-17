"""Shared fakes. One home, checked against their ports by tests/contract/test_fakes_conform.py."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from friday.domain.types import Control, ControlKind
from friday.ports.models import ChatResult, Message


@dataclass
class ScriptedClient:
    """A ModelClient replying from a script: each entry is a string or a function of the messages."""

    script: list[str | Callable[[Sequence[Message]], str]]
    calls: list[tuple[str, list[Message], str]] = field(default_factory=list)
    model: str = "fake/model"

    def chat(
        self, role: str, messages: Sequence[Message], *, json_schema: dict[str, Any] | None = None,
        max_tokens: int | None = None, tag: str = "",
    ) -> ChatResult:
        self.calls.append((role, list(messages), tag))
        if not self.script:
            raise AssertionError("ScriptedClient ran out of replies")
        nxt = self.script.pop(0)
        text = nxt(messages) if callable(nxt) else nxt
        return ChatResult(text=text, model=self.model, tokens_in=10, tokens_out=5, cost_usd=0.0)


@dataclass
class FakeDom:
    """A tiny DOM: element text, visibility, a focus pointer, canvas pixels, scripted reactions."""

    texts: dict[str, str] = field(default_factory=dict)
    visible: set[str] = field(default_factory=set)
    canvas: dict[str, int] = field(default_factory=dict)
    on_click: dict[str, Callable[[FakeDom], None]] = field(default_factory=dict)
    on_key: dict[str, Callable[[FakeDom], None]] = field(default_factory=dict)
    tab_order: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    animated: set[str] = field(default_factory=set)       # canvases that change with no input
    focus: str | None = None
    kinds: dict[str, ControlKind] = field(default_factory=dict)  # selector -> kind, for controls()
    on_choose: dict[str, Callable[[FakeDom], None]] = field(default_factory=dict)


class FakePage:
    def __init__(self, dom: FakeDom) -> None:
        self.dom = dom

    def goto(self, query: str) -> None:
        pass

    def click(self, selector: str) -> None:
        if selector not in self.dom.texts and selector not in self.dom.canvas:
            raise LookupError(selector)
        self.dom.on_click.get(selector, lambda d: None)(self.dom)

    def type(self, selector: str, text: str) -> None:
        self.dom.texts[selector] = text

    def key(self, name: str, times: int = 1) -> None:
        for _ in range(times):
            if name == "Tab" and self.dom.tab_order:
                i = self.dom.tab_order.index(self.dom.focus) + 1 if self.dom.focus in self.dom.tab_order else 0
                self.dom.focus = self.dom.tab_order[i % len(self.dom.tab_order)]
            self.dom.on_key.get(name, lambda d: None)(self.dom)

    def focus(self, selector: str) -> None:
        self.dom.focus = selector

    def wait(self, ms: int) -> None:
        for c in self.dom.animated:
            self.dom.canvas[c] += 1

    def exists(self, selector: str) -> bool:
        return selector in self.dom.texts or selector in self.dom.canvas

    def visible(self, selector: str) -> bool:
        return selector in self.dom.visible

    def focused(self, selector: str) -> bool:
        return self.dom.focus == selector

    def text(self, selector: str) -> str | None:
        return self.dom.texts.get(selector)

    def canvas_hash(self, selector: str) -> str | None:
        return str(self.dom.canvas[selector]) if selector in self.dom.canvas else None

    def console_errors(self) -> list[str]:
        return list(self.dom.errors)

    def screenshot(self) -> bytes:
        return b"png"

    def controls(self) -> list[Control]:
        return [Control(sel, kind, sel if sel.startswith("#") else None) for sel, kind in self.dom.kinds.items()]

    def snapshot(self) -> str:
        return repr((sorted(self.dom.texts.items()), sorted(self.dom.canvas.items()), sorted(self.dom.visible)))

    def choose(self, selector: str) -> None:
        if selector not in self.dom.kinds:
            raise LookupError(selector)
        self.dom.on_choose.get(selector, lambda d: None)(self.dom)


def page_factory(make_dom: Callable[[], FakeDom]):
    """An open_page callable giving a fresh FakePage (fresh DOM) per call, like a fresh page load."""

    @contextmanager
    def open_page() -> Iterator[FakePage]:
        yield FakePage(make_dom())

    return open_page
