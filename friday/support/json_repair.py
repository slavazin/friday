"""One repairing decoder for every model JSON (SPEC § 5 Model JSON).

Strict `json.loads` first; on failure, syntax-only repairs in a fixed order, each recorded by name.
Repairs rewrite syntax, never meaning: no key is invented, no value is guessed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Decoded:
    value: Any | None
    repairs: tuple[str, ...]
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.value is not None


def _strip_fences(s: str) -> str:
    m = re.search(r"```(?:json)?\s*(.*?)```", s, flags=re.DOTALL)
    return m.group(1) if m else s


def _strip_think(s: str) -> str:
    return re.sub(r"<think>.*?</think>", "", s, flags=re.DOTALL)


def _outermost(s: str) -> str:
    """The first balanced {...} or [...] span, honouring strings."""
    start = next((i for i, c in enumerate(s) if c in "{["), None)
    if start is None:
        return s
    stack: list[str] = []
    in_str = esc = False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c in "{[":
            stack.append("}" if c == "{" else "]")
        elif c in "}]":
            if not stack or stack.pop() != c:
                return s[start:]
            if not stack:
                return s[start : i + 1]
    return s[start:]


def _trailing_commas(s: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", s)


def _smart_quotes(s: str) -> str:
    return s.replace("“", '"').replace("”", '"')


REPAIRS: tuple[tuple[str, Callable[[str], str]], ...] = (
    ("strip_think", _strip_think),
    ("strip_fences", _strip_fences),
    ("outermost", _outermost),
    ("trailing_commas", _trailing_commas),
    ("smart_quotes", _smart_quotes),
)


def decode(text: str) -> Decoded:
    """The JSON value in `text` and the repairs applied; value None with the last error when undecodable."""
    try:
        return Decoded(json.loads(text), ())
    except json.JSONDecodeError as e:
        err = str(e)
    applied: list[str] = []
    s = text
    for name, fn in REPAIRS:
        t = fn(s)
        if t == s:
            continue
        s = t
        applied.append(name)
        try:
            return Decoded(json.loads(s), tuple(applied))
        except json.JSONDecodeError as e:
            err = str(e)
    return Decoded(None, tuple(applied), err)
