"""The probe suite (SPEC § 5): the request-independent share of the deterministic loss.

P is the mean of three probes: the page loaded; it logged no console error; the share of its controls whose
default action changes the visible state within `settle_ms`. A control that changes nothing scores 0, and a
page with no controls has responsive share 0, never "not applicable".

`Page.snapshot()` is a JSON object mapping each node of the visible state to a digest of it (a canvas is a grid of
tiles, one node each), so the probe compares node by node. Before any control is operated, the page is watched
with no input over `SLOW_WINDOW_MS` (the slowest page timer the probe tells apart: live data, a carousel),
sampled every half `settle_ms`; every node that changes there is the page's own (a clock, a ticker, an animated
tile), and every node ever seen changing with no input stays in a history with its shortest observed period.
Before each control the page is watched again with no input: over a full tick window (`TICK_WINDOW_MS`, a
clock's 1 Hz) when it has nodes of its own or the last control read responsive (that control may have started a
timer), and the nodes that changed there are its own from then on while nodes still for the whole window leave
`own` (an animation that ended); else over `settle_ms`. A control is responsive when its action changes a node
that is not its own, unless every such node is one the page changed by itself before and it changes again by
itself within its period (a 2 s ticker). When it changes only the page's own nodes, or nothing: a canvas is
responsive when its keys or click ran a page handler (its effect lies under the canvas's own animation); on a
page at rest, a control is responsive when a node starts changing within a further tick window (a Start whose
first visible change is the timer's first tick); on a moving page, when its action brings the page to rest
(a stopwatch's stop button). Residue: a dead control on a page whose timer ticks slower than `SLOW_WINDOW_MS`
and first ticks inside its action window reads responsive; a resume control on a timer slower than the watch
before it reads unresponsive; a canvas handler that ignores the probe's keys still counts as having run.
"""

from __future__ import annotations

import json
from collections.abc import Collection

from friday.domain.types import Control, ControlKind, ProbeReading, Viewport
from friday.ports.browser import Page

CANVAS_KEYS = ("ArrowUp", "ArrowRight", "Space")
TICK_WINDOW_MS = 1000   # the slowest common page timer (a clock's or a countdown's 1 Hz tick)
SLOW_WINDOW_MS = 5000   # the slowest page timer the probe tells from a control's effect (live data, a carousel)
BROWSER_FAILURES = (TimeoutError, ConnectionError)
# Reads the adapter's count of trusted key and click events a page handler on window, document or a canvas
# returned from (`friday.adapters.playwright_browser._COUNT_HANDLERS_JS`); null when the page has no count.
HANDLER_RUNS_JS = "() => (typeof window.__fridayHandlerRuns === 'number' ? window.__fridayHandlerRuns : null)"


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


class _Watch:
    """What a watch with no input saw: the nodes that changed between any two samples, the last sample, the
    time from the last change to the end of the watch, the longest gap between two changes (None when at most
    one change was seen), the watch's length, and each changed node's shortest gap between two of its own
    changes (None when it changed once)."""

    def __init__(self, moved: set[str], last: dict[str, str], quiet_ms: int, max_gap_ms: int | None,
                 length_ms: int, periods: dict[str, int | None]):
        self.moved, self.last, self.quiet_ms, self.max_gap_ms = moved, last, quiet_ms, max_gap_ms
        self.length_ms, self.periods = length_ms, periods

    def was_moving_at_end(self) -> bool:
        """True when a node was still due to change when the watch ended: the time since its last change is
        shorter than the longest gap seen between changes (`TICK_WINDOW_MS` when one change was seen). A page
        whose own animation ended inside the watch reads False."""
        if not self.moved:
            return False
        return self.quiet_ms < (self.max_gap_ms if self.max_gap_ms is not None else TICK_WINDOW_MS)


def _watch(page: Page, start: dict[str, str], settle_ms: int, window_ms: int | None = None,
           stop_on: Collection[str] = ()) -> _Watch:
    """Watches the page with no input, sampling every half `settle_ms`, over `window_ms` (default: more than
    `TICK_WINDOW_MS` and 2 x `settle_ms`), ending early once a node in `stop_on` changes."""
    step = max(1, settle_ms // 2)
    window = window_ms if window_ms is not None else max(TICK_WINDOW_MS, 2 * settle_ms) + step
    moved: set[str] = set()
    changes: list[int] = []
    at: dict[str, list[int]] = {}
    prev, waited = start, 0
    while waited < window:
        page.wait(step)
        waited += step
        cur = _snap(page)
        diff = _changed(prev, cur)
        if diff:
            moved |= diff
            changes.append(waited)
            for n in diff:
                at.setdefault(n, []).append(waited)
        prev = cur
        if diff & set(stop_on):
            break
    gaps = [b - a for a, b in zip(changes, changes[1:], strict=False)]
    quiet = waited - changes[-1] if changes else waited
    periods = {n: min((b - a for a, b in zip(t, t[1:], strict=False)), default=None) for n, t in at.items()}
    return _Watch(moved, prev, quiet, max(gaps) if gaps else None, waited, periods)


class _State:
    """What the probe carries from control to control: the nodes the page changes by itself (`own`), whether
    the next control needs a full watch first (the last control read responsive, so it may have started a
    timer), and every node ever seen changing with no input, with its shortest observed period (`history`,
    never expired: a timer slower than a watch drops out of `own` but stays the page's own)."""

    def __init__(self, first: _Watch):
        self.own = set(first.moved)
        self.full_next = False
        self.history: dict[str, int | None] = {}
        self.note(first)

    def note(self, w: _Watch) -> None:
        """Adds the nodes `w` saw changing to `history`, keeping each node's shortest known period."""
        for n, p in w.periods.items():
            old = self.history.get(n)
            self.history[n] = p if old is None else (old if p is None else min(old, p))

    def still_before(self, n: str, idle: _Watch) -> bool:
        """True when a change of `n` right after the action is the action's: `n` never changed by itself, or
        the watch before the action covered `n`'s known period and `n` stayed still through it (a paused timer)."""
        if n not in self.history:
            return True
        p = self.history[n]
        return p is not None and idle.length_ms >= p and n not in idle.moved

    def recheck_window(self, nodes: Collection[str], step: int) -> int:
        """A no-input watch long enough to see each of `nodes` change again by itself: twice its known period
        (at least `TICK_WINDOW_MS`), `SLOW_WINDOW_MS` when its period is unknown, capped at `SLOW_WINDOW_MS`."""
        out = TICK_WINDOW_MS
        for n in nodes:
            p = self.history.get(n)
            out = max(out, SLOW_WINDOW_MS if p is None else min(SLOW_WINDOW_MS, max(TICK_WINDOW_MS, 2 * p)))
        return out + step


def _handler_runs(page: Page) -> int | None:
    """The page's count of key and click handler runs (`HANDLER_RUNS_JS`), None when the page keeps none."""
    try:
        n = page.run_script(HANDLER_RUNS_JS)
    except BROWSER_FAILURES as e:  # the probe scores a page that stops answering
        raise _Stuck(f"{type(e).__name__}: {e}") from e
    return n if isinstance(n, int) and not isinstance(n, bool) else None


def _responsive(page: Page, control: Control, state: _State, settle_ms: int, sample_text: str) -> bool:
    """True when the control's action changes a node that is not moving, or brings a moving page to rest, or
    (a canvas whose visible change lies under its own animation) runs a key or click handler of the page.

    Before the action the page is watched with no input: over a full tick window when it has nodes of its own
    or the last control read responsive (the nodes that changed there become `own`, and nodes still for that
    whole window leave it), else over `settle_ms`. A changed node the page has changed by itself before
    (`history`) counts only when it then stays still over its period (a reset on a paused timer, not a 2 s
    ticker). When nothing outside `own` changed and the page was at rest, a further tick window with no input
    credits a node the action started (a Start whose first visible change is the first 1 s tick). Rest is
    credited only when a node was still due to change at the end of the watch before the action and none
    changes over a further window after it. Raises _Stuck when the page no longer answers.
    """
    step = max(1, settle_ms // 2)
    if state.own or state.full_next:
        idle = _watch(page, _snap(page), settle_ms)
        state.own = set(idle.moved)
    else:
        idle = _watch(page, _snap(page), settle_ms, window_ms=settle_ms)
        state.own |= idle.moved
    state.note(idle)
    state.full_next = False
    before, moving = idle.last, set(state.own)
    runs = _handler_runs(page) if control.kind is ControlKind.CANVAS else None
    try:
        _operate(page, control, sample_text)
        page.wait(settle_ms)
    except Exception:  # noqa: BLE001 - SPEC § 5: a control whose action throws is unresponsive (scored 0)
        return False
    after = _snap(page)
    changed = _changed(before, after)
    new = changed - moving
    if new:
        doubt = {n for n in new if not state.still_before(n, idle)}
        if doubt == new:
            again = _watch(page, after, settle_ms, window_ms=state.recheck_window(doubt, step), stop_on=doubt)
            state.note(again)
            if again.moved & doubt:
                state.own |= again.moved
                return False
        state.full_next = True
        return True
    if runs is not None and any(k.startswith("c:") for k in changed):
        now = _handler_runs(page)
        if now is not None and now > runs:
            state.full_next = True
            return True
    if not idle.was_moving_at_end():
        late = _watch(page, after, settle_ms, window_ms=TICK_WINDOW_MS)
        started = {n for n in late.moved - moving if state.still_before(n, idle)}
        state.note(late)
        state.own |= late.moved
        if started:
            state.full_next = True
            return True
        return False
    slow = any(n in state.history and (state.history[n] or SLOW_WINDOW_MS) > TICK_WINDOW_MS for n in moving)
    rest = _watch(page, after, settle_ms, window_ms=state.recheck_window(moving, step) if slow else None,
                  stop_on=moving)
    state.note(rest)
    state.own |= rest.moved
    if rest.moved:
        return False
    state.full_next = True
    return True


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
        step = max(1, settle_ms // 2)
        state = _State(_watch(page, _snap(page), settle_ms, window_ms=SLOW_WINDOW_MS + step)) if controls else None
        for c in controls:
            at = c.selector
            if state is not None and _responsive(page, c, state, settle_ms, sample_text):
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
