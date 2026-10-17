"""Lifecycle state machines for decisions and lenses.

A transition is legal only if it appears in the table with the cause that drives it. Every status
flip in the run goes through `check_decision_flip` / `check_lens_flip`, so an illegal flip raises at
its source instead of surfacing as a wrong timeline.
"""

from __future__ import annotations

from enum import StrEnum

from friday.domain.types import DecisionStatus as D
from friday.domain.types import LensStatus as L


class FlipCause(StrEnum):
    GATE_ADMIT = "gate.admit"
    GATE_REFUSE = "gate.refuse"
    GATE_EXPIRE = "gate.expire"
    DETECT_SUSPECT = "detect.suspect"
    DETECT_CLEAR = "detect.clear"
    DETECT_RETIRE = "detect.retire"
    SUPERSEDE = "supersede"
    STEER_RETIRE = "steer.retire"
    STEER_VETO = "steer.veto"
    LENS_CLEAR_FLOOR = "lens.clear_floor"
    LENS_FLOOR = "lens.floor"
    LENS_FOLD = "lens.fold"
    LENS_CAP = "lens.cap"


class IllegalFlip(ValueError):
    pass


DECISION_FLIPS: dict[tuple[D, D], frozenset[FlipCause]] = {
    (D.CANDIDATE, D.ADMITTED): frozenset({FlipCause.GATE_ADMIT}),
    (D.CANDIDATE, D.REFUSED): frozenset({FlipCause.GATE_REFUSE, FlipCause.STEER_VETO}),
    (D.CANDIDATE, D.EXPIRED): frozenset({FlipCause.GATE_EXPIRE}),
    (D.ADMITTED, D.SUSPECT): frozenset({FlipCause.DETECT_SUSPECT}),
    (D.SUSPECT, D.ADMITTED): frozenset({FlipCause.DETECT_CLEAR}),
    (D.SUSPECT, D.RETIRED): frozenset({FlipCause.DETECT_RETIRE, FlipCause.SUPERSEDE, FlipCause.STEER_RETIRE}),
    (D.ADMITTED, D.RETIRED): frozenset({FlipCause.SUPERSEDE, FlipCause.STEER_RETIRE}),
}

LENS_FLIPS: dict[tuple[L, L], frozenset[FlipCause]] = {
    (L.HIDDEN, L.ACTIVE): frozenset({FlipCause.LENS_CLEAR_FLOOR}),
    (L.HIDDEN, L.RETIRED): frozenset({FlipCause.LENS_FLOOR, FlipCause.LENS_CAP}),
    (L.ACTIVE, L.RETIRED): frozenset({FlipCause.LENS_FLOOR, FlipCause.LENS_CAP}),
    (L.HIDDEN, L.SUPERSEDED): frozenset({FlipCause.LENS_FOLD}),
    (L.ACTIVE, L.SUPERSEDED): frozenset({FlipCause.LENS_FOLD}),
}

TERMINAL_DECISION = frozenset({D.RETIRED, D.EXPIRED, D.REFUSED})
COMPOSABLE_DECISION = frozenset({D.ADMITTED, D.SUSPECT})   # a suspect decision stays composed (SPEC § 9)
LIVE_LENS = frozenset({L.HIDDEN, L.ACTIVE})


def check_decision_flip(old: D, new: D, cause: FlipCause) -> None:
    """Raises IllegalFlip unless `cause` may move a decision from `old` to `new`."""
    if cause not in DECISION_FLIPS.get((old, new), frozenset()):
        raise IllegalFlip(f"decision {old} -> {new} by {cause}")


def check_lens_flip(old: L, new: L, cause: FlipCause) -> None:
    """Raises IllegalFlip unless `cause` may move a lens from `old` to `new`."""
    if cause not in LENS_FLIPS.get((old, new), frozenset()):
        raise IllegalFlip(f"lens {old} -> {new} by {cause}")
