"""Decision steps of the round close (SPEC §§ 8, 9, 11): the gate and the detector.

The round close is the single writer. Each status flip is validated against the lifecycle table first,
then appended to the event log (the append-only history), then written to the Journal file and committed.
The full close order (steers, lens curation, pooling, nomination, gate, detector) is not wired yet."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from friday.algorithms.detector import ConfirmAction, Detector
from friday.algorithms.gate import GateAction, GateRule, select_in_flight
from friday.app.render import FlipRenderer
from friday.domain.events import StatusFlip
from friday.domain.lifecycle import FlipCause, check_decision_flip
from friday.domain.types import Decision, DecisionStatus, DeltaKind, PoolStatus
from friday.ports.stores import EventLog, Journal, JudgeStore, Ledger, PoolStore

_GATE_FLIP = {
    GateAction.ADMIT: (DecisionStatus.ADMITTED, FlipCause.GATE_ADMIT),
    GateAction.REFUSE: (DecisionStatus.REFUSED, FlipCause.GATE_REFUSE),
    GateAction.EXPIRE: (DecisionStatus.EXPIRED, FlipCause.GATE_EXPIRE),
}


def _ledger_line(n: int, mean_d: float | None, w: float) -> str:
    return f"{n} pairs, mean paired delta {mean_d:.2f}, e-process wealth {w:.2f}" if mean_d is not None else "no pairs"


@dataclass
class DecisionClose:
    """Gate and detector steps of the round close, over the Journal, the Ledger and the event log."""

    journal: Journal
    ledger: Ledger
    events: EventLog
    judge_store: JudgeStore
    pools: PoolStore
    gate: GateRule
    detector: Detector
    renderer: FlipRenderer
    in_flight: int
    constants: Mapping[str, Any] = field(default_factory=dict)

    def _flip(self, d: Decision, new: DecisionStatus, cause: FlipCause, round_: int,
              evidence: Mapping[str, Any], successors: Sequence[str] = ()) -> Decision:
        check_decision_flip(d.status, new, cause)
        flip = StatusFlip(d.id, d.status.value, new.value, cause, round_, dict(evidence), tuple(successors))
        message = self.renderer.render("decision", flip, self.constants)
        self.events.append_flip(flip)
        return self.journal.flip(flip, message)

    def in_flight_candidates(self) -> list[Decision]:
        cands = [d for d in self.journal.decisions() if d.status is DecisionStatus.CANDIDATE]
        return select_in_flight(cands, self.in_flight)

    def close_gate(self, round_: int) -> list[Decision]:
        """Reads every in-flight candidate's exposure account; flips the ones the gate decides."""
        flipped = []
        for cand in self.in_flight_candidates():
            rows = self.ledger.deltas(cand.id, DeltaKind.EXPOSURE)
            reading = self.gate.read([r.d for r in rows], [r.dQ for r in rows])
            if reading.action is GateAction.HOLD:
                continue
            new, cause = _GATE_FLIP[reading.action]
            evidence = {
                "n": reading.n, "wealth": reading.wealth, "mirror_wealth": reading.mirror_wealth,
                "mean_d": reading.mean_d, "q_upper": reading.q_upper,
                "ledger": _ledger_line(reading.n, reading.mean_d, reading.wealth),
            }
            flipped.append(self._flip(cand, new, cause, round_, evidence))
            if new in (DecisionStatus.REFUSED, DecisionStatus.EXPIRED):
                self._refuse_pool(cand.pool_id, round_)
            if new is DecisionStatus.ADMITTED:
                for pred_id in cand.supersedes:
                    pred = self.journal.get(pred_id)
                    if pred.status in (DecisionStatus.ADMITTED, DecisionStatus.SUSPECT):
                        flipped.append(self._flip(pred, DecisionStatus.RETIRED, FlipCause.SUPERSEDE, round_,
                                                  {"successor": cand.id}, [cand.id]))
        return flipped

    def _refuse_pool(self, pool_id: str | None, round_: int) -> None:
        """The refused-pool block (SPEC § 8): the pool re-nominates only on observations dated after `round_`."""
        if pool_id is None:
            return
        pool = next((p for p in self.pools.pools() if p.id == pool_id), None)
        if pool is None:
            raise LookupError(f"candidate's pool {pool_id} is not in the pool store")
        self.pools.put_pool(replace(pool, status=PoolStatus.REFUSED, refused_at_round=round_))

    def _last_flip_round(self, decision_id: str, status: DecisionStatus) -> int | None:
        rounds = [f.round for f in self.events.flips(decision_id) if f.new == status.value]
        return rounds[-1] if rounds else None

    def close_detector(self, round_: int) -> list[Decision]:
        """Suspects admitted decisions the detector reads as stale; confirms or clears suspect ones."""
        flipped = []
        for d in self.journal.decisions():
            ablation = self.ledger.deltas(d.id, DeltaKind.ABLATION)
            if d.status is DecisionStatus.ADMITTED:
                # Both readings restart at the last flip to admitted (admission or a clear), so a cleared
                # decision is read on fresh evidence instead of re-tripping on the harm that suspected it.
                since = self._last_flip_round(d.id, DecisionStatus.ADMITTED) or 0
                fresh = [r.d for r in ablation if r.round > since]
                fires = [f.disposition for f in self.judge_store.fires_of(d.id) if f.round > since]
                reading = self.detector.read(fresh, fires)
                if reading.suspect:
                    evidence = {
                        "reasons": ", ".join(reading.reasons),
                        "window_upper": "n/a" if reading.window_upper is None else f"{reading.window_upper:.2f}",
                        "harm_wealth": reading.harm_wealth,
                        "contradicted": "n/a" if reading.contradicted_share is None
                        else f"{reading.contradicted_share:.2f}",
                    }
                    flipped.append(self._flip(d, DecisionStatus.SUSPECT, FlipCause.DETECT_SUSPECT, round_, evidence))
            elif d.status is DecisionStatus.SUSPECT:
                since = self._last_flip_round(d.id, DecisionStatus.SUSPECT)
                later = [r.d for r in ablation if since is not None and r.round > since]
                conf = self.detector.confirm(later)
                if conf.action is ConfirmAction.WAIT:
                    continue
                evidence = {"n": conf.n, "mean_d": conf.mean_d, "upper": conf.upper, "successor": "none"}
                if conf.action is ConfirmAction.RETIRE:
                    flipped.append(self._flip(d, DecisionStatus.RETIRED, FlipCause.DETECT_RETIRE, round_, evidence))
                else:
                    flipped.append(self._flip(d, DecisionStatus.ADMITTED, FlipCause.DETECT_CLEAR, round_, evidence))
        return flipped
