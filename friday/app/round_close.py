"""The round close (SPEC § 11): `DecisionClose` holds the gate and detector steps; `RoundClose` runs one whole
close in order (steers, lens curation, observation writes and expiry, pooling, nomination, gate, detector,
pings).

The round close is the single writer. Each status flip is validated against the lifecycle table first,
then appended to the event log (the append-only history), then written to the Journal file and committed."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from friday.algorithms.detector import ConfirmAction, Detector
from friday.algorithms.gate import GateAction, GateRule, select_in_flight
from friday.algorithms.halt import HaltReason, RoundSummary, halt_reason
from friday.algorithms.recurrence import RecurrenceBar, expired, nomination_queue
from friday.app.lens_close import LensClose, LensCloseResult, ResidualSource
from friday.app.render import FlipRenderer
from friday.app.steer import SteerResult, apply_steers
from friday.app.wiring import build_mechanics, flip_constants
from friday.config import RunSpec
from friday.domain.events import Ping, StatusFlip
from friday.domain.ids import IdMinter
from friday.domain.lifecycle import FlipCause, check_decision_flip
from friday.domain.types import (
    FREE_LENS,
    Arm,
    Decision,
    DecisionStatus,
    DeltaKind,
    Disposition,
    LensStatus,
    Observation,
    ObservationStatus,
    Pool,
    PoolStatus,
)
from friday.ports.stores import (
    EventLog,
    Journal,
    JudgeStore,
    Ledger,
    LensSet,
    ObservationStore,
    PassStore,
    PoolStore,
    SpendLog,
)
from friday.roles.judge import DraftObservation
from friday.roles.lens_minter import LensMinter
from friday.roles.nominator import Draft, NoDecision, Nominator
from friday.roles.pooling import PoolingCall, PoolingResult

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

    def flip(self, d: Decision, new: DecisionStatus, cause: FlipCause, round_: int,
             evidence: Mapping[str, Any], successors: Sequence[str] = ()) -> Decision:
        """Validates, renders, appends to the event log, then writes the Journal file and its commit."""
        return self._flip(d, new, cause, round_, evidence, successors)

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

    def refuse_pool(self, pool_id: str | None, round_: int) -> None:
        """Marks the pool refused at `round_` (the refused-pool block)."""
        self._refuse_pool(pool_id, round_)

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


# ---------------------------------------------------------------------------------------------------------------
# The whole close, in order (SPEC § 11)
# ---------------------------------------------------------------------------------------------------------------

NEW_POOL = "new:"


@dataclass(frozen=True)
class CloseParams:
    """run.yml figures the close reads beyond the ones its algorithms already hold."""

    expiry_rounds: int
    in_flight: int
    max_direction_labels: int
    detect_window: int
    ping_transport_share: float
    rounds: int
    spend_cap_usd: float
    drift_round: int
    convergence_rounds: int

    @classmethod
    def from_spec(cls, spec: RunSpec) -> CloseParams:
        return cls(
            expiry_rounds=spec.pool.expiry_rounds, in_flight=spec.gate.in_flight,
            max_direction_labels=spec.pool.max_direction_labels, detect_window=spec.detect.window,
            ping_transport_share=spec.run.ping_transport_share, rounds=spec.run.rounds,
            spend_cap_usd=spec.run.spend_cap_usd, drift_round=spec.drift.round,
            convergence_rounds=spec.run.convergence_rounds,
        )


@dataclass(frozen=True)
class NoDecisionRecord:
    pool_id: str
    reason: str


@dataclass(frozen=True)
class CloseSummary:
    """What one close wrote and read, for the runner, the coordinator and the halt rule."""

    round: int
    flips: tuple[StatusFlip, ...]               # every flip this close appended, in commit order
    steers: SteerResult
    lens: LensCloseResult
    observations: tuple[str, ...]               # O- ids minted this close
    expired: tuple[str, ...]                    # O- ids closed unused by expiry
    pools: tuple[str, ...]                      # P- ids minted this close
    nominations: tuple[str, ...]                # D- ids drafted this close
    no_decisions: tuple[NoDecisionRecord, ...]
    invitations: tuple[str, ...]                # decisions newly suspect: successor drafting invited
    pings: tuple[Ping, ...]
    summary: RoundSummary
    halt: HaltReason | None


@dataclass
class RoundClose:
    """One round close, run in the parent process alone: the single writer of statuses, pools and lenses."""

    decisions: DecisionClose
    lenses: LensClose
    passes: PassStore
    observations: ObservationStore
    pooling: PoolingCall
    nominator: Nominator
    bar: RecurrenceBar
    spend: SpendLog
    params: CloseParams

    @property
    def journal(self) -> Journal:
        return self.decisions.journal

    @property
    def pool_store(self) -> PoolStore:
        return self.decisions.pools

    @property
    def events(self) -> EventLog:
        return self.decisions.events

    def close(self, round_: int, drafts: Mapping[str, Sequence[DraftObservation]],
              history: Sequence[RoundSummary] = ()) -> CloseSummary:
        """Runs the close of `round_` over the round's drafted observations (pass id -> the report call's).

        Order: (1) steers; (2) lens curation, minter, cap; (3) observation writes and expiry; (4) pooling per
        kept lens and `free`; (5) nomination and drafting; (6) gate; (7) detector and successor invitations;
        (8) pings. `history` holds the earlier closes' RoundSummary rows for the halt rule.
        """
        start = len(self.events.flips())
        steers = apply_steers(self.events, self.journal, self.decisions.flip, round_,
                              lambda d: self.decisions.refuse_pool(d.pool_id, round_))
        lens = self.lenses.close(round_)
        kept = sorted(x.id for x in self.lenses.lens_set.lenses() if x.status is LensStatus.ACTIVE)
        new_obs = self._write_observations(round_, drafts)
        gone = self._expire(round_)
        minted_pools = self._pool(kept, new_obs)
        nominated, no_dec = self._nominate(round_, kept, since=None)
        self.decisions.close_gate(round_)
        detected = self.decisions.close_detector(round_)
        invited = tuple(d.id for d in detected if d.status is DecisionStatus.SUSPECT)
        if invited:
            more, more_no = self._nominate(round_, kept, since=round_)
            nominated += more
            no_dec += more_no
        summary = self._round_summary(round_, kept)
        halt = halt_reason([*history, summary], self.params.rounds, self.spend.total_usd(),
                           self.params.spend_cap_usd, self.params.drift_round, self.params.convergence_rounds)
        pings = self._pings(round_, invited, halt)
        return CloseSummary(
            round=round_, flips=tuple(self.events.flips()[start:]), steers=steers, lens=lens,
            observations=tuple(o.id for o in new_obs), expired=tuple(gone), pools=tuple(minted_pools),
            nominations=tuple(nominated), no_decisions=tuple(no_dec), invitations=invited, pings=tuple(pings),
            summary=summary, halt=halt,
        )

    # -- (3) observations ------------------------------------------------------------------------------

    def _write_observations(self, round_: int, drafts: Mapping[str, Sequence[DraftObservation]],
                            ) -> list[Observation]:
        """Mints O- ids (pass id order, then report order) and writes the round's observations, open."""
        by_id = {p.id: p for p in self.passes.passes(round_)}
        lens_ids = {x.id for x in self.lenses.lens_set.lenses()} | {FREE_LENS}
        minter = IdMinter.resume("observation", [o.id for o in self.observations.observations()])
        out: list[Observation] = []
        for pass_id in sorted(drafts):
            p = by_id.get(pass_id)
            if p is None:
                raise LookupError(f"observations name pass {pass_id}, which is not a pass of round {round_}")
            if p.arm is not Arm.JOURNAL:
                raise ValueError(f"pass {pass_id} is a {p.arm.value}-arm pass; only journal-arm passes are judged")
            for d in drafts[pass_id]:
                if d.lens_id not in lens_ids:
                    raise ValueError(f"observation on pass {pass_id} names unknown lens {d.lens_id!r}")
                out.append(Observation(minter.next(), pass_id, round_, d.lens_id, p.category, d.shape,
                                       d.direction, d.quote))
        self.observations.add_observations(out)
        return out

    def _expire(self, round_: int) -> list[str]:
        ids = expired(self.observations.observations(), round_, self.params.expiry_rounds)
        by_id = {o.id: o for o in self.observations.observations()}
        for i in ids:
            self.observations.put_observation(replace(by_id[i], status=ObservationStatus.CLOSED))
        return ids

    # -- (4) pooling -----------------------------------------------------------------------------------

    def _pool(self, kept: Sequence[str], new_obs: Sequence[Observation]) -> list[str]:
        """One pooling call per kept lens and `free` with new observations; returns the P- ids minted.

        A hidden lens is not kept: its observations are written but never reach a pool."""
        minted: list[str] = []
        for lens_id in [*kept, FREE_LENS]:
            mine = [o for o in new_obs if o.lens_id == lens_id]
            if not mine:
                continue
            pools = [p for p in self.pool_store.pools() if p.lens_id == lens_id and p.status is not PoolStatus.MERGED]
            result = self.pooling.assign(lens_id, sorted(pools, key=lambda p: p.id), mine)
            minted += self._apply_pooling(lens_id, pools, mine, result)
        return minted

    def _apply_pooling(self, lens_id: str, pools: Sequence[Pool], new: Sequence[Observation],
                       result: PoolingResult) -> list[str]:
        """Validates the whole result first, then writes new pools, assignments and merges."""
        by_id = {p.id: p for p in pools}
        new_keys = {np.key: np for np in result.new_pools}
        if len(new_keys) != len(result.new_pools):
            raise ValueError(f"pooling for {lens_id}: a new pool key repeats")
        want = sorted(o.id for o in new)
        got = sorted(a.observation_id for a in result.assignments)
        if got != want:
            raise ValueError(f"pooling for {lens_id}: assignments {got} do not cover the new observations {want}")
        for np in result.new_pools:
            if not 1 <= len(np.direction_labels) <= self.params.max_direction_labels:
                raise ValueError(f"pooling for {lens_id}: new pool {np.key} needs 1 to "
                                 f"{self.params.max_direction_labels} direction labels")
        for a in result.assignments:
            if a.pool_ref.startswith(NEW_POOL):
                np = new_keys.get(a.pool_ref[len(NEW_POOL):])
                if np is None:
                    raise ValueError(f"pooling for {lens_id}: {a.pool_ref} names no new pool")
                labels = np.direction_labels
            else:
                if a.pool_ref not in by_id:
                    raise ValueError(f"pooling for {lens_id}: {a.pool_ref} is not a pool of this lens")
                labels = by_id[a.pool_ref].direction_labels
            if a.direction_label not in labels:
                raise ValueError(f"pooling for {lens_id}: label {a.direction_label!r} is not one of {labels}")
        seen: set[str] = set()
        for keep, gone in result.merges:
            for pid in (keep, gone):
                if pid not in by_id or by_id[pid].status is not PoolStatus.OPEN:
                    raise ValueError(f"pooling for {lens_id}: merge names {pid}, which is not an open pool of it")
                if pid in seen:
                    raise ValueError(f"pooling for {lens_id}: {pid} takes part in two merges")
                seen.add(pid)
            if not keep < gone:
                raise ValueError(f"pooling for {lens_id}: the lower id survives a merge ({keep}, {gone})")

        referenced = {a.pool_ref[len(NEW_POOL):] for a in result.assignments if a.pool_ref.startswith(NEW_POOL)}
        minter = IdMinter.resume("pool", [p.id for p in self.pool_store.pools()])
        new_ids: dict[str, str] = {}
        for np in result.new_pools:
            if np.key not in referenced:
                continue
            pid = minter.next()
            new_ids[np.key] = pid
            self.pool_store.put_pool(Pool(pid, lens_id, np.shape, tuple(np.direction_labels)))
        obs = {o.id: o for o in new}
        for a in result.assignments:
            pid = new_ids[a.pool_ref[len(NEW_POOL):]] if a.pool_ref.startswith(NEW_POOL) else a.pool_ref
            self.observations.put_observation(replace(obs[a.observation_id], pool_id=pid,
                                                      direction_label=a.direction_label))
        for keep, gone in result.merges:
            self.pool_store.put_pool(replace(by_id[gone], status=PoolStatus.MERGED, merged_into=keep))
            for o in self.observations.observations():
                if o.pool_id == gone and o.status is ObservationStatus.OPEN:
                    self.observations.put_observation(replace(o, pool_id=keep))
        return list(new_ids.values())

    # -- (5) nomination and drafting -------------------------------------------------------------------

    def _opened_round(self, pool_id: str, observations: Sequence[Observation]) -> int | None:
        rounds = [o.round for o in observations if o.pool_id == pool_id]
        return min(rounds) if rounds else None

    def _warrant(self, pool: Pool, observations: Sequence[Observation]) -> list[Observation]:
        """The pool's open observations the bar counts (after the refusal, for a refused pool)."""
        after = pool.refused_at_round if pool.status is PoolStatus.REFUSED else None
        return sorted((o for o in observations if o.pool_id == pool.id and o.status is ObservationStatus.OPEN
                       and (after is None or o.round > after)), key=lambda o: o.id)

    def _room(self) -> int:
        cands = [d for d in self.journal.decisions() if d.status is DecisionStatus.CANDIDATE]
        return max(0, self.params.in_flight - len(cands))

    def _nominate(self, round_: int, kept: Sequence[str], since: int | None,
                  ) -> tuple[list[str], list[NoDecisionRecord]]:
        """Nominates pools of kept lenses and `free` while `gate.in_flight` has room; drafts each.

        With `since`, only pools opened at or after round `since` are read (a successor invitation). A draft
        from a pool opened at or after a suspect decision's suspicion lists that decision in `supersedes`.
        """
        room = self._room()
        if room == 0:
            return [], []
        observations = list(self.observations.observations())
        lens_ok = {*kept, FREE_LENS}
        eligible = [p for p in self.pool_store.pools() if p.lens_id in lens_ok]
        if since is not None:
            eligible = [p for p in eligible if (self._opened_round(p.id, observations) or -1) >= since]
        suspects = [(d.id, self.decisions._last_flip_round(d.id, DecisionStatus.SUSPECT))
                    for d in self.journal.decisions() if d.status is DecisionStatus.SUSPECT]
        drafted: list[str] = []
        none: list[NoDecisionRecord] = []
        for pool, label in nomination_queue(eligible, observations, self.bar, room):
            warrant = self._warrant(pool, observations)
            result = self.nominator.draft(pool, label, warrant)
            if isinstance(result, NoDecision):
                none.append(NoDecisionRecord(pool.id, result.reason))   # its observations stay open
                continue
            if not isinstance(result, Draft):
                raise TypeError(f"nominator returned {type(result).__name__}, not a Draft or NoDecision")
            opened = self._opened_round(pool.id, observations)
            supersedes = tuple(sorted(i for i, r in suspects if r is not None and opened is not None and opened >= r))
            drafted.append(self._draft(round_, pool, warrant, result, supersedes))
        return drafted, none

    def _draft(self, round_: int, pool: Pool, warrant: Sequence[Observation], draft: Draft,
               supersedes: Sequence[str]) -> str:
        """Mints the D- id, writes the candidate, closes its warrant observations, marks the pool nominated."""
        did = IdMinter.resume("decision", [d.id for d in self.journal.decisions()]).next()
        rounds = sorted({o.round for o in warrant})
        span = f"{rounds[0]}–{rounds[-1]}" if rounds and rounds[0] != rounds[-1] else (str(rounds[0]) if rounds else "")
        decision = Decision(
            id=did, status=DecisionStatus.CANDIDATE, pool_id=pool.id, hook=draft.hook, payload=draft.payload,
            warrant={"observations": [o.id for o in warrant], "lens": pool.lens_id, "rounds": span,
                     "sources": [c.url for c in draft.sources], "ledger": "no pairs yet"},
            supersedes=tuple(supersedes), drafted_round=round_,
        )
        self.journal.draft(decision)
        for o in warrant:
            self.observations.put_observation(replace(o, status=ObservationStatus.CLOSED))
        self.pool_store.put_pool(replace(pool, status=PoolStatus.NOMINATED))
        return did

    # -- (8) summary and pings -------------------------------------------------------------------------

    def _round_summary(self, round_: int, kept: Sequence[str]) -> RoundSummary:
        lens_ok = {*kept, FREE_LENS}
        pools = [p for p in self.pool_store.pools() if p.lens_id in lens_ok]
        at_bar = nomination_queue(pools, list(self.observations.observations()), self.bar, len(pools))
        return RoundSummary(
            round=round_, candidates_in_flight=len(self.decisions.in_flight_candidates()),
            suspects=sum(d.status is DecisionStatus.SUSPECT for d in self.journal.decisions()),
            pools_at_bar=len(at_bar),
        )

    def _ping_text(self, name: str, **fields: Any) -> str:
        return self.decisions.renderer.templates["ping"][name].format_map(fields)

    def _pings(self, round_: int, suspected: Sequence[str], halt: HaltReason | None) -> list[Ping]:
        pings: list[Ping] = []
        w = self.params.detect_window
        for did in suspected:
            since = self.decisions._last_flip_round(did, DecisionStatus.ADMITTED) or 0
            disposed = [f.disposition for f in self.decisions.judge_store.fires_of(did)
                        if f.round > since and f.disposition is not Disposition.UNDISPOSED][-w:]
            contradicted = sum(x is Disposition.CONTRADICTED for x in disposed)
            if contradicted:
                pings.append(Ping(self._ping_text("suspect_contradicted", id=did, contradicted=contradicted,
                                                  n=len(disposed)), round_))
        units: dict[tuple[str, str, int | None], list[bool]] = {}
        for p in self.passes.passes(round_):
            units.setdefault((p.category, p.arm.value, p.pair_slot), []).append(p.transport_failed)
        failed = sum(all(v) for v in units.values())
        if units and failed / len(units) > self.params.ping_transport_share:
            pings.append(Ping(self._ping_text("transport", round=round_, failed=failed, units=len(units),
                                              share=self.params.ping_transport_share), round_))
        if halt is not None:
            pings.append(Ping(self._ping_text("halt", round=round_, reason=halt.value), round_))
        return pings


def build_round_close(
    spec: RunSpec, *, journal: Journal, ledger: Ledger, events: EventLog, judge_store: JudgeStore,
    pools: PoolStore, passes: PassStore, observations: ObservationStore, lens_set: LensSet, spend: SpendLog,
    pooling: PoolingCall, nominator: Nominator, minter: LensMinter | None, renderer: FlipRenderer,
    residual_source: ResidualSource | None = None, bar: RecurrenceBar | None = None,
) -> RoundClose:
    """A RoundClose over the given stores and roles, every constant read from `spec`."""
    m = build_mechanics(spec)
    constants = flip_constants(spec)
    ln = spec.lens
    decisions = DecisionClose(journal=journal, ledger=ledger, events=events, judge_store=judge_store, pools=pools,
                              gate=m.gate, detector=m.detector, renderer=renderer, in_flight=spec.gate.in_flight,
                              constants=constants)
    lenses = LensClose(
        lens_set=lens_set, events=events, passes=passes, judge_store=judge_store, observations=observations,
        renderer=renderer, params=m.curation, scorer=m.lens_value, correlation=m.fold_correlation,
        seed=spec.run.seed, window=ln.window, mint_every=ln.mint_every, residual_min_passes=ln.residual_min_passes,
        mint_max=ln.mint_max, minter=minter, residual_source=residual_source, constants=constants,
    )
    return RoundClose(decisions=decisions, lenses=lenses, passes=passes, observations=observations,
                      pooling=pooling, nominator=nominator, bar=bar or m.bar, spend=spend,
                      params=CloseParams.from_spec(spec))
