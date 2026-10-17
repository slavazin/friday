"""Steers at the round close (SPEC § 11): a pending `retire`, `veto` or `note` is applied as one step each.

Every pending steer is validated against the lifecycle table before any is applied, so an illegal steer
(a veto on an admitted decision, a retire on a candidate) raises and writes nothing at all."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from friday.domain.events import StatusFlip, Steer
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
    notes: tuple[Steer, ...]           # applied notes: marked applied, no status change; no commit yet (seam request)


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


def landed_flip(events: EventLog, flip: StatusFlip) -> StatusFlip | None:
    """The subject's last logged flip when it is `flip` (same old, new, cause and round), else None.

    The projection lags the event log only when a write failed between the two; then the subject's last event
    is the flip whose projection is missing, and the subject's projection still holds its `old` status."""
    logged = events.flips(flip.subject_id)
    last = logged[-1] if logged else None
    key = (flip.old, flip.new, flip.cause, flip.round)
    if last is not None and (last.old, last.new, last.cause, last.round) == key:
        return last
    return None


def _flip_landed(events: EventLog, journal: Journal, steer: Steer, round_: int) -> bool:
    """True when this steer's flip at the close of `round_` is in the event log and in the Journal both.

    A flip whose event landed but whose Journal write failed is not landed: the re-run writes its projection
    from the logged event (the flip step appends no second event)."""
    if steer.action not in STEER_FLIPS:
        return False
    new, cause = STEER_FLIPS[steer.action]
    logged = any(f.cause is cause and f.new == new.value and f.round == round_
                 and f.evidence.get("reason") == steer.reason for f in events.flips(steer.decision_id))
    return logged and journal.get(steer.decision_id).status is new


def apply_steers(events: EventLog, journal: Journal, flip: FlipStep, round_: int,
                 on_refused: Callable[[Decision], None]) -> SteerResult:
    """Applies every pending steer at the close of `round_`; returns the flipped decisions and the notes.

    Writes, per steer: the flip (event log, then Journal file and commit) and the steer's applied round. The
    `flip` step must append no second event for a flip already logged (see `landed_flip`).
    `on_refused` runs after a veto (the refused-pool block).
    """
    pending = list(events.pending_steers())
    landed = [s for s in pending if _flip_landed(events, journal, s, round_)]
    for steer in landed:            # a close re-run after a failure between the flip and its applied mark
        if STEER_FLIPS[steer.action][0] is DecisionStatus.REFUSED:
            on_refused(journal.get(steer.decision_id))       # idempotent: the pool is marked refused at round_
        events.mark_steer_applied(steer, round_)
    plan = plan_steers([s for s in pending if s not in landed], journal)
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


__all__ = ["IllegalFlip", "NOTE", "STEER_FLIPS", "SteerResult", "apply_steers", "landed_flip", "plan_steers"]
