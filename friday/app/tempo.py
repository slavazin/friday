"""Decision turnaround on the critic run (SPEC § 17 Tempo): how many rounds and wall seconds each status step of a
decision (drafted, admitted, rejected, retired) came after its pool's first observation.

The runner writes one `decision.turnaround` telemetry event per step at each close, from the close's own flips
and drafts (the event log's record), the observation store's pool membership and the telemetry `pass.outcome`
times of the observing passes. The readings read those events back and nothing else.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from friday.domain.events import StatusFlip
from friday.domain.lifecycle import FlipCause
from friday.domain.types import DecisionStatus, Observation

TURNAROUND = "decision.turnaround"
PASS_OUTCOME = "pass.outcome"
DRAFTED, ADMITTED, REJECTED, RETIRED = "drafted", "admitted", "rejected", "retired"
STEPS = (DRAFTED, ADMITTED, REJECTED, RETIRED)


def _step_of(flip: StatusFlip) -> str | None:
    """The turnaround step a decision flip is, or None for a flip that is none of them (suspect, cleared)."""
    if flip.new == DecisionStatus.ADMITTED.value and flip.cause is FlipCause.GATE_ADMIT:
        return ADMITTED
    if flip.new in (DecisionStatus.REFUSED.value, DecisionStatus.EXPIRED.value):
        return REJECTED
    if flip.new == DecisionStatus.RETIRED.value:
        return RETIRED
    return None


def turnaround_events(
    round_: int, flips: Sequence[StatusFlip], drafted: Sequence[str], pool_of: Mapping[str, str | None],
    observations: Sequence[Observation], observed_at: Callable[[Sequence[str]], float | None], now: float,
) -> list[dict[str, Any]]:
    """The `decision.turnaround` attributes for the steps one close took: each decision drafted at it (`drafted`),
    then each decision flip it appended, in commit order.

    `pool_of` maps a decision id to its pool id; `observed_at(pass_ids)` is the earliest telemetry time of those
    passes' `pass.outcome` events (None when none is recorded). A decision with no pool, or a pool with no
    observation, reads its first observation, rounds and wall seconds unread (None), never 0."""
    def row(decision_id: str, step: str, status: str, cause: str, n: int | None) -> dict[str, Any]:
        pool_id = pool_of[decision_id]
        mine = [o for o in observations if pool_id is not None and o.pool_id == pool_id]
        first = min((o.round for o in mine), default=None)
        at = observed_at(sorted({o.pass_id for o in mine if o.round == first})) if first is not None else None
        return {"decision_id": decision_id, "pool_id": pool_id, "step": step, "status": status, "cause": cause,
                "round": round_, "first_observation_round": first,
                "rounds": None if first is None else round_ - first,
                "wall_s": None if at is None else now - at, "n": n}

    out = [row(d, DRAFTED, DecisionStatus.CANDIDATE.value, "nominate", None) for d in drafted]
    for f in flips:
        if not f.subject_id.startswith("D-"):
            continue
        step = _step_of(f)
        if step is None:
            continue
        n = f.evidence.get("n")
        out.append(row(f.subject_id, step, f.new, f.cause.value, None if n is None else int(n)))
    return out


# ---------------------------------------------------------------------------------------------------------
# The readings: read back from the `decision.turnaround` events alone


@dataclass(frozen=True)
class Turnaround:
    """One decision's steps. Rounds are round numbers; minutes are wall minutes since its pool's first observation."""

    decision_id: str
    pool_id: str | None
    first_observation_round: int | None
    nominated_round: int | None
    admitted_round: int | None
    rejected_round: int | None
    retired_round: int | None
    n_at_admission: int | None              # the gate's exposure deltas at admission (the flip's evidence n)
    minutes: Mapping[str, float | None]     # step -> wall minutes since the pool's first observation


def turnaround_readings(rows: Sequence[Any]) -> tuple[Turnaround, ...]:
    """Per decision, its turnaround steps from `decision.turnaround` telemetry rows (`TelemetryRow`s), ordered by
    decision id. A step not taken reads None; a repeated step keeps its first row."""
    by: dict[str, dict[str, Mapping[str, Any]]] = {}
    for r in rows:
        if r.name != TURNAROUND:
            continue
        a = r.attrs
        if a["step"] not in STEPS:
            raise ValueError(f"{TURNAROUND} row {r.seq}: unknown step {a['step']!r}")
        by.setdefault(a["decision_id"], {}).setdefault(a["step"], a)
    out = []
    for d in sorted(by):
        steps = by[d]
        first = next((s for s in steps.values() if s.get("first_observation_round") is not None), None)
        minutes = {k: (None if v.get("wall_s") is None else float(v["wall_s"]) / 60.0) for k, v in steps.items()}
        adm = steps.get(ADMITTED)
        out.append(Turnaround(
            d, next(iter(steps.values())).get("pool_id"),
            None if first is None else int(first["first_observation_round"]),
            *(None if steps.get(s) is None else int(steps[s]["round"]) for s in STEPS),
            None if adm is None or adm.get("n") is None else int(adm["n"]), minutes))
    return tuple(out)


def _r(x: int | None) -> str:
    return "unread" if x is None else str(x)


def _m(x: float | None) -> str:
    return "unread" if x is None else f"{x:.1f} min"


def quirk_turnaround(ts: Sequence[Turnaround], quirk_decisions: Mapping[str, str | None]) -> list[dict[str, Any]]:
    """Per quirk, the turnaround of the first decision that taught it (`quirk_decisions`: quirk id -> decision id,
    None when not learned). A quirk not learned, or taught by a decision with no turnaround row, reads None."""
    by = {t.decision_id: t for t in ts}
    out = []
    for q, d in quirk_decisions.items():
        t = by.get(d) if d is not None else None
        out.append({"quirk": q, "decision_id": d, "learned": d is not None,
                    "first_observation_round": None if t is None else t.first_observation_round,
                    "nominated_round": None if t is None else t.nominated_round,
                    "admitted_round": None if t is None else t.admitted_round,
                    "n_at_admission": None if t is None else t.n_at_admission,
                    "minutes": {} if t is None else dict(t.minutes)})
    return out


def turnaround_lines(ts: Sequence[Turnaround], quirk_decisions: Mapping[str, str | None] | None = None) -> list[str]:
    """The turnaround table as printed lines: per decision, then per quirk (`quirk_decisions` as in
    `quirk_turnaround`)."""
    out = ["Turnaround (SPEC § 17 Tempo): rounds and wall minutes from a pool's first observation"]
    if not ts:
        out.append("  no decision drafted")
    for t in ts:
        steps = ", ".join(f"{s} {_m(t.minutes.get(s))}" for s in STEPS if s in t.minutes)
        out.append(f"  {t.decision_id} (pool {t.pool_id or 'none'}): first observation round "
                   f"{_r(t.first_observation_round)}, nominated round {_r(t.nominated_round)}, admitted round "
                   f"{_r(t.admitted_round)}, n {_r(t.n_at_admission)} at admission; {steps}")
    for q in quirk_turnaround(ts, quirk_decisions or {}):
        if not q["learned"]:
            out.append(f"  quirk {q['quirk']}: not learned")
            continue
        steps = ", ".join(f"{s} {_m(q['minutes'].get(s))}" for s in STEPS if s in q["minutes"]) or "no turnaround row"
        out.append(f"  quirk {q['quirk']}: {q['decision_id']}, first observation round "
                   f"{_r(q['first_observation_round'])}, nominated round {_r(q['nominated_round'])}, admitted round "
                   f"{_r(q['admitted_round'])}, n {_r(q['n_at_admission'])} at admission; {steps}")
    return out
