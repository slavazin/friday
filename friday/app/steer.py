"""Steers at the round close (SPEC § 11): a pending `retire`, `veto` or `note` is applied as one step each.

Every pending steer is validated against the lifecycle table before any is applied, so an illegal steer
(a veto on an admitted decision, a retire on a candidate) raises and writes nothing at all."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from friday.domain.events import Steer
from friday.domain.lifecycle import FlipCause, IllegalFlip, check_decision_flip
from friday.domain.types import Decision, DecisionStatus
from friday.ports.stores import EventLog, Journal

STEER_FLIPS: dict[str, tuple[DecisionStatus, FlipCause]] = {
    "retire": (DecisionStatus.RETIRED, FlipCause.STEER_RETIRE),
    "veto": (DecisionStatus.REFUSED, FlipCause.STEER_VETO),
}
NOTE = "note"

# (decision, new status, cause, round, evidence) -> the flipped decision; the round close's single flip step
FlipStep = Callable[[Decision, DecisionStatus, FlipCause, int, dict[str, str]], Decision]


@dataclass(frozen=True)
class SteerResult:
    flipped: tuple[Decision, ...]
    notes: tuple[Steer, ...]           # applied notes: recorded, no status change, no commit


SteerPlan = tuple[Steer, DecisionStatus | None, FlipCause | None]


def plan_steers(steers: Sequence[Steer], journal: Journal) -> list[SteerPlan]:
    """Each pending steer with the flip it drives (None for a note); raises IllegalFlip or ValueError first.

    Two steers on one decision are validated in order, the second against the status the first leaves.
    """
    status: dict[str, DecisionStatus] = {}
    out: list[SteerPlan] = []
    for s in steers:
        current = status.get(s.decision_id) or journal.get(s.decision_id).status
        if s.action == NOTE:
            out.append((s, None, None))
            continue
        if s.action not in STEER_FLIPS:
            raise ValueError(f"steer action {s.action!r} is not retire, veto or note")
        new, cause = STEER_FLIPS[s.action]
        check_decision_flip(current, new, cause)
        status[s.decision_id] = new
        out.append((s, new, cause))
    return out


def apply_steers(events: EventLog, journal: Journal, flip: FlipStep, round_: int,
                 on_refused: Callable[[Decision], None]) -> SteerResult:
    """Applies every pending steer at the close of `round_`; returns the flipped decisions and the notes.

    Writes, per steer: the flip (event log, then Journal file and commit) and the steer's applied round.
    `on_refused` runs after a veto (the refused-pool block).
    """
    plan = plan_steers(events.pending_steers(), journal)
    flipped: list[Decision] = []
    notes: list[Steer] = []
    for steer, new, cause in plan:
        if new is None or cause is None:
            notes.append(steer)
        else:
            d = flip(journal.get(steer.decision_id), new, cause, round_, {"reason": steer.reason})
            flipped.append(d)
            if new is DecisionStatus.REFUSED:
                on_refused(d)
        events.mark_steer_applied(steer, round_)
    return SteerResult(tuple(flipped), tuple(notes))


__all__ = ["IllegalFlip", "NOTE", "STEER_FLIPS", "SteerResult", "apply_steers", "plan_steers"]
