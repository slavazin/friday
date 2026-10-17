"""The probe suite (SPEC § 5): the request-independent share of the deterministic loss.

P is the mean of three probes: the page loaded; it logged no console error; the share of its controls whose
default action changes the visible state within `settle_ms`. A control that changes nothing scores 0, and a
page with no controls has responsive share 0, never "not applicable".

`Page.snapshot()` is a JSON object mapping each node of the visible state to a digest of it, so the probe
compares node by node. Before any control is operated, the page is watched with no input over a window longer
than the slowest common timer tick (`TICK_WINDOW_MS`, a clock's 1 Hz), sampled every half `settle_ms`; every
node that changes there is the page's own (a clock, a countdown). Each control then gets a control of its own:
the page is watched for `settle_ms` with no input just before it is operated, and nodes that change there are
moving too. A control is responsive when its action changes a node that is not moving. When it changes only
moving nodes, or nothing, on a page that was moving, it is responsive only when its action brings the page to
rest: no node changes over a further full watch window (a stopwatch's stop button). A control whose only effect
is on a node the page also changes by itself reads unresponsive (residue), and so does a dead control on a page
whose timer ticks slower than `TICK_WINDOW_MS` and first ticks inside its action window (residue).
"""

from __future__ import annotations

import json
from collections.abc import Collection

from friday.domain.types import Control, ControlKind, ProbeReading, Viewport
from friday.ports.browser import Page

CANVAS_KEYS = ("ArrowUp", "ArrowRight", "Space")
TICK_WINDOW_MS = 1000   # the slowest common page timer (a clock's or a countdown's 1 Hz tick)
BROWSER_FAILURES = (TimeoutError, ConnectionError)


def _operate(page: Page, control: Control, sample_text: str) -> None:
    """The default action for `control`'s kind."""
    sel = control.selector
    if control.kind is ControlKind.TEXT:
        page.type(sel, sample_text)
    elif control.kind in (ControlKind.SELECT, ControlKind.CHECKBOX):
        page.choose(sel)
    elif control.kind is ControlKind.CANVAS:
        page.focus(sel)
        page.click(sel)
        for k in CANVAS_KEYS:
            page.key(k)
    else:
        page.click(sel)


class _Stuck(Exception):
    """The page stopped answering a snapshot."""


def _snap(page: Page) -> dict[str, str]:
    """The page's snapshot as {node: digest}. A browser failure (the Page port raises TimeoutError or
    ConnectionError) becomes _Stuck; any other exception, a malformed snapshot included, raises."""
    try:
        raw = page.snapshot()
    except BROWSER_FAILURES as e:  # the probe scores a page that stops answering
        raise _Stuck(f"{type(e).__name__}: {e}") from e
    state = json.loads(raw)   # adapter output, not model output
    if not isinstance(state, dict):
        raise TypeError(f"Page.snapshot must be a JSON object of node digests, got {type(state).__name__}")
    return state


def _changed(a: dict[str, str], b: dict[str, str]) -> set[str]:
    """Nodes whose digest differs between `a` and `b`, or present in only one."""
    return {k for k in a.keys() | b.keys() if a.get(k) != b.get(k)}


def _watch(page: Page, start: dict[str, str], settle_ms: int) -> tuple[set[str], dict[str, str]]:
    """Watches the page with no input over more than `TICK_WINDOW_MS` and 2 x `settle_ms`, sampling every half
    `settle_ms`: the nodes that changed between any two samples, and the last sample."""
    step = max(1, settle_ms // 2)
    window = max(TICK_WINDOW_MS, 2 * settle_ms) + step
    moved: set[str] = set()
    prev, waited = start, 0
    while waited < window:
        page.wait(step)
        waited += step
        cur = _snap(page)
        moved |= _changed(prev, cur)
        prev = cur
    return moved, prev


def _responsive(page: Page, control: Control, own: set[str], settle_ms: int, sample_text: str) -> bool:
    """True when the control's action changes a node that is not moving, or brings a moving page to rest.

    `own` holds the nodes the page changes by itself. Raises _Stuck when a snapshot fails: the page no longer
    answers, so no later control can be read.
    """
    idle = _snap(page)
    page.wait(settle_ms)
    before = _snap(page)
    moving = own | _changed(idle, before)
    try:
        _operate(page, control, sample_text)
        page.wait(settle_ms)
    except Exception:  # noqa: BLE001 - SPEC § 5: a control whose action throws is unresponsive (scored 0)
        return False
    after = _snap(page)
    if _changed(before, after) - moving:
        return True
    if not moving:
        return False
    moved, _ = _watch(page, after, settle_ms)
    return not moved


def _p(loaded: bool, errors: Collection[str], responsive: int, controls: int) -> float:
    share = responsive / controls if controls else 0.0
    return (float(loaded) + float(loaded and not errors) + share) / 3.0


def run(page: Page, viewport: Viewport, pass_id: str, declared_ids: Collection[str],
        max_controls: int, settle_ms: int, sample_text: str) -> ProbeReading:
    """The probe reading of a page already loaded with the test query: operates its first `max_controls`
    controls in document order and counts those that change the visible state."""
    controls = page.controls()[:max_controls]
    declared = {_hash_id(i) for i in declared_ids}
    responsive: list[Control] = []
    stuck: tuple[str, ...] = ()
    at = "the first watch"
    try:
        own, _ = _watch(page, _snap(page), settle_ms) if controls else (set(), {})
        for c in controls:
            at = c.selector
            if _responsive(page, c, own, settle_ms, sample_text):
                responsive.append(c)
    except _Stuck as e:
        stuck = (f"page stopped answering at {at}: {e}",)   # every control not yet read stays unresponsive
    errors = tuple(page.console_errors()) + stuck
    silent = sum(1 for c in responsive if c.element_id is None or _hash_id(c.element_id) not in declared)
    return ProbeReading(
        pass_id=pass_id, viewport=viewport, loaded=True, console_errors=errors, controls=len(controls),
        responsive=len(responsive), responsive_ids=tuple(c.element_id for c in responsive if c.element_id),
        silent=silent, P=_p(True, errors, len(responsive), len(controls)),
    )


def unloaded(pass_id: str, viewport: Viewport, error: str) -> ProbeReading:
    """The probe reading of a page that did not load: every probe fails, P = 0."""
    return ProbeReading(pass_id=pass_id, viewport=viewport, loaded=False, console_errors=(error,), controls=0,
                        responsive=0, responsive_ids=(), silent=0, P=0.0)


def failed(pass_id: str, viewport: Viewport, error: str) -> ProbeReading:
    """The probe reading of a page that loaded and then stopped answering: the load probe passes, the rest fail."""
    return ProbeReading(pass_id=pass_id, viewport=viewport, loaded=True, console_errors=(error,), controls=0,
                        responsive=0, responsive_ids=(), silent=0, P=_p(True, (error,), 0, 0))


def _hash_id(i: str) -> str:
    return i if i.startswith("#") else "#" + i


def missing_contract_ids(page: Page, contract: Collection[str]) -> list[str]:
    """Contract ids (with '#') absent from the loaded page, in contract order; drives the self-repair turn."""
    return [_hash_id(i) for i in contract if not page.exists(_hash_id(i))]
