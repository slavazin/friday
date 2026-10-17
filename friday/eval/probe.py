"""The probe suite (SPEC § 5): the request-independent share of the deterministic loss.

P is the mean of three probes: the page loaded; it logged no console error; the share of its controls whose
default action changes the visible state within `settle_ms`. A control that changes nothing scores 0, and a
page with no controls has responsive share 0, never "not applicable".

Each control carries a control of its own: the page is watched for `settle_ms` with no input just before the
control is operated, and every control is operated. On a page that held still, the control is responsive when
the visible state then changes. On a page that was changing by itself (a running timer), a change proves
nothing, so the control is responsive only when its action brings the page to rest: the state then holds over
a further `settle_ms` (a stopwatch's stop button). A control that leaves a moving page moving reads
unresponsive, even one that also changed something: the opaque snapshot cannot tell its change from the
timer's (residue).
"""

from __future__ import annotations

from collections.abc import Collection

from friday.domain.types import Control, ControlKind, ProbeReading, Viewport
from friday.ports.browser import Page

CANVAS_KEYS = ("ArrowUp", "ArrowRight", "Space")


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


def _snap(page: Page) -> str:
    try:
        return page.snapshot()
    except Exception as e:  # noqa: BLE001 - re-raised as _Stuck: the probe scores a page that stops answering
        raise _Stuck(f"{type(e).__name__}: {e}") from e


def _responsive(page: Page, control: Control, settle_ms: int, sample_text: str) -> bool:
    """True when the control's action changes a still page, or brings a page that was moving to rest.

    Raises _Stuck when a snapshot fails: the page no longer answers, so no later control can be read.
    """
    idle = _snap(page)
    page.wait(settle_ms)
    before = _snap(page)
    try:
        _operate(page, control, sample_text)
        page.wait(settle_ms)
    except Exception:  # noqa: BLE001 - SPEC § 5: a control whose action throws is unresponsive (scored 0)
        return False
    after = _snap(page)
    if before == idle:
        return after != before
    page.wait(settle_ms)
    return _snap(page) == after


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
    for c in controls:
        try:
            if _responsive(page, c, settle_ms, sample_text):
                responsive.append(c)
        except _Stuck as e:
            stuck = (f"page stopped answering at {c.selector}: {e}",)
            break  # every control not yet read stays unresponsive
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
