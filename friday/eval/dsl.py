"""The check DSL (SPEC § 4): validation and an interpreter over the `Page` port.

Steps: {"goto": query} {"click": sel} {"type": sel, "text": s} {"key": name, "times": n} {"focus": sel}
{"drag": sel, "dx": px, "dy": py} {"wait": ms}. Expectations: {"exists": sel} {"visible": sel} {"focused": sel}
{"text_matches": sel, "regex": r} {"text_changed": sel} {"canvas_changed": sel} {"no_console_errors": true}.

"Before" snapshots for *_changed are taken after the check's leading `goto` steps, so the initial
navigation is part of the setup, not the change.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from typing import Any

from friday.domain.types import Check, CheckResult
from friday.ports.browser import Page

STEP_KEYS: dict[str, frozenset[str]] = {
    "goto": frozenset(), "click": frozenset(), "type": frozenset({"text"}),
    "key": frozenset({"times"}), "focus": frozenset(), "drag": frozenset({"dx", "dy"}), "wait": frozenset(),
}
EXPECT_KEYS: dict[str, frozenset[str]] = {
    "exists": frozenset(), "visible": frozenset(), "focused": frozenset(), "text_matches": frozenset({"regex"}),
    "text_changed": frozenset(), "canvas_changed": frozenset(), "no_console_errors": frozenset(),
}


class InvalidCheck(ValueError):
    pass


def _verb(obj: dict[str, Any], table: dict[str, frozenset[str]], where: str) -> str:
    verbs = [k for k in obj if k in table]
    if len(verbs) != 1:
        raise InvalidCheck(f"{where}: exactly one of {sorted(table)} expected, got {sorted(obj)}")
    verb = verbs[0]
    extra = set(obj) - {verb} - table[verb]
    if extra:
        raise InvalidCheck(f"{where}: unknown keys {sorted(extra)} for {verb}")
    return verb


def _int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate(check: Check, max_wait_ms: int) -> None:
    """Raises InvalidCheck when `check` is outside the DSL."""
    for i, step in enumerate(check.steps):
        where = f"{check.id}.steps[{i}]"
        verb = _verb(step, STEP_KEYS, where)
        arg = step[verb]
        if verb == "wait":
            if not (_int(arg) and 0 <= arg <= max_wait_ms):
                raise InvalidCheck(f"{where}: wait must be an int in 0..{max_wait_ms}")
        elif not isinstance(arg, str):
            raise InvalidCheck(f"{where}: {verb} takes a string")
        if verb == "type" and not isinstance(step.get("text"), str):
            raise InvalidCheck(f"{where}: type needs a string text")
        if verb == "drag":
            for axis in ("dx", "dy"):
                if not _int(step.get(axis)):
                    raise InvalidCheck(f"{where}: drag needs an int {axis} (CSS px)")
            if step["dx"] == 0 and step["dy"] == 0:
                raise InvalidCheck(f"{where}: a drag moves the pointer: dx or dy is not 0")
        if verb == "key" and "times" in step and not (_int(step["times"]) and step["times"] >= 1):
            raise InvalidCheck(f"{where}: times must be an int >= 1")
    where = f"{check.id}.expect"
    verb = _verb(check.expect, EXPECT_KEYS, where)
    arg = check.expect[verb]
    if verb == "no_console_errors":
        if arg is not True:
            raise InvalidCheck(f"{where}: no_console_errors takes true")
    elif not isinstance(arg, str):
        raise InvalidCheck(f"{where}: {verb} takes a selector string")
    if verb == "text_matches":
        if not isinstance(check.expect.get("regex"), str):
            raise InvalidCheck(f"{where}: text_matches needs a regex")
        try:
            re.compile(check.expect["regex"])
        except re.error as e:
            raise InvalidCheck(f"{where}: bad regex: {e}") from None


def validate_set(checks: Sequence[Check], max_wait_ms: int) -> None:
    """Validates each check and the at-most-one `no_console_errors` rule."""
    for c in checks:
        validate(c, max_wait_ms)
    if sum("no_console_errors" in c.expect for c in checks) > 1:
        raise InvalidCheck("no_console_errors counts as one check at most per request")


def _run_step(page: Page, step: dict[str, Any]) -> None:
    if "goto" in step:
        page.goto(step["goto"])
    elif "click" in step:
        page.click(step["click"])
    elif "type" in step:
        page.type(step["type"], step["text"])
    elif "key" in step:
        page.key(step["key"], int(step.get("times", 1)))
    elif "focus" in step:
        page.focus(step["focus"])
    elif "drag" in step:
        page.drag(step["drag"], int(step["dx"]), int(step["dy"]))
    elif "wait" in step:
        page.wait(int(step["wait"]))


def _total_wait(steps: Sequence[dict[str, Any]]) -> int:
    return sum(int(s["wait"]) for s in steps if "wait" in s)


def run_check(open_page: Callable[[], AbstractContextManager[Page]], check: Check) -> CheckResult:
    """Runs `check` on a fresh page; any exception in a step fails the check at that step."""
    expect = check.expect
    verb = next(k for k in expect if k in EXPECT_KEYS)
    sel = expect[verb]
    steps = list(check.steps)
    lead = 0
    while lead < len(steps) and "goto" in steps[lead]:
        lead += 1
    step_label = "load"
    try:
        with open_page() as page:
            step_label = "setup"
            for s in steps[:lead]:
                _run_step(page, s)
            before_text = page.text(sel) if verb == "text_changed" else None
            before_hash = page.canvas_hash(sel) if verb == "canvas_changed" else None
            for i, s in enumerate(steps[lead:], start=lead):
                step_label = f"step {i}: {s}"
                _run_step(page, s)
            step_label = f"expect {verb}"
            passed = _expect(page, verb, sel, expect, before_text, before_hash)
        if passed and verb == "canvas_changed":
            step_label = "control"
            if not _canvas_still(open_page, steps[:lead], sel, _total_wait(steps)):
                return CheckResult(check.id, False, "control: canvas changes with no input")
    except Exception as e:  # noqa: BLE001 - a failing load or step is a failed check, by the DSL's definition
        return CheckResult(check.id, False, f"{step_label}: {type(e).__name__}: {e}")
    return CheckResult(check.id, passed, "" if passed else f"expect {verb}")


def _expect(page: Page, verb: str, sel: Any, expect: dict[str, Any],
            before_text: str | None, before_hash: str | None) -> bool:
    if verb == "exists":
        return page.exists(sel)
    if verb == "visible":
        return page.visible(sel)
    if verb == "focused":
        return page.focused(sel)
    if verb == "text_matches":
        text = page.text(sel)
        return text is not None and re.search(expect["regex"], text) is not None
    if verb == "text_changed":
        after = page.text(sel)
        return after is not None and after != before_text
    if verb == "canvas_changed":
        after = page.canvas_hash(sel)
        return after is not None and before_hash is not None and after != before_hash
    if verb == "no_console_errors":
        return not page.console_errors()
    raise InvalidCheck(verb)


def _canvas_still(open_page: Callable[[], AbstractContextManager[Page]],
                  lead: Sequence[dict[str, Any]], sel: str, wait_ms: int) -> bool:
    """The control for canvas_changed: with no input over the same wait, the hash must not change."""
    with open_page() as page:
        for s in lead:
            _run_step(page, s)
        h0 = page.canvas_hash(sel)
        page.wait(wait_ms)
        return h0 == page.canvas_hash(sel)
