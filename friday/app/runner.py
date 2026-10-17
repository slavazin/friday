"""The runner (SPEC § 11): drives the rounds in order, then the readout (SPEC § 12) after the halt.

Per round, in order: tag the round's requests; `pipeline.plan_round` (the exposure coins, persisted); the
coordinator's turns (it advises: writes the round plan, may ping, may ask to halt); `pipeline.run_round`;
the Ledger rows and the report-call observations, persisted before the close; the round close; the notes
file; the close record. The round's passes always come from `plan_round`, never from the coordinator.

Resume: the close log names the closed rounds. A restarted runner enters the first round without a close.
When that round's passes already ran (its artifacts exist) it re-runs none: it recomputes the round's
deltas from the stored passes and evals, appends only the Ledger rows not yet appended, reads the stored or
journaled observations, and closes the round.
"""

from __future__ import annotations

import dataclasses
import json
import multiprocessing
import traceback
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from friday.algorithms.composition import compose_pair, select_composed
from friday.algorithms.exposure import PairKind
from friday.algorithms.gate import select_in_flight
from friday.algorithms.halt import RoundSummary
from friday.app import export as export_doc
from friday.app import pipeline
from friday.app.goals import tag_held_out, tag_round
from friday.app.ledger import pair_deltas
from friday.app.notes import append_free, approx_tokens, notes_text
from friday.app.readout import ReadoutStores, held_out_ids
from friday.app.request_text import regime_of
from friday.app.request_text import request_text as compose_request_text
from friday.app.round_close import CloseSummary, RoundClose
from friday.app.wiring import with_optional
from friday.config import RunSpec
from friday.domain.events import Steer
from friday.domain.types import (
    Arm,
    Composition,
    Decision,
    DecisionStatus,
    DeltaKind,
    LedgerDelta,
    PairPlan,
    Pass,
    Regime,
    Request,
)
from friday.ports.goals import GoalSource, TagVocabulary
from friday.ports.stores import (
    EventLog,
    ExposureStore,
    Journal,
    JudgeStore,
    Ledger,
    LensSet,
    ObservationStore,
    PassStore,
    PoolStore,
    SpendLog,
)
from friday.ports.telemetry import Telemetry
from friday.roles.coordinator import Coordinator, RoundState
from friday.roles.judge import DraftObservation
from friday.roles.tagger import Tagger
from friday.roles.worker import render_composition

DRAFTS_FILE = "observations.json"     # artifacts/<pass_id>/: the report call's drafts, written as the pass ends
NOTES_FILE = "notes.md"               # the notes arm's shared file under the run root (no paths.notes knob yet)
EXPORT_FILE = "export.json"


# ---------------------------------------------------------------------------------------------------------
# Stores and the close record


@dataclass(frozen=True)
class Stores:
    """The run stores the runner reads and writes (`adapters.sqlite.process.RunStores` has these fields)."""

    passes: PassStore
    judge: JudgeStore
    observations: ObservationStore
    pools: PoolStore
    exposures: ExposureStore
    ledger: Ledger
    events: EventLog
    spend: SpendLog


@dataclass(frozen=True)
class ClosedRound:
    summary: RoundSummary
    halt: str | None


class CloseLog(Protocol):
    """Which round closes completed, with each close's RoundSummary for the halt rule."""

    def closed(self) -> Sequence[ClosedRound]: ...
    def record(self, summary: RoundSummary, halt: str | None) -> None: ...


@dataclass
class MemoryCloseLog:
    rows: list[ClosedRound] = field(default_factory=list)

    def closed(self) -> Sequence[ClosedRound]:
        return list(self.rows)

    def record(self, summary: RoundSummary, halt: str | None) -> None:
        self.rows.append(ClosedRound(summary, halt))


# ---------------------------------------------------------------------------------------------------------
# The report call's drafts: journaled per pass, so a restart before the close loses none


def write_drafts(out_dir: Path, drafts: Sequence[DraftObservation] | None) -> None:
    """Writes a pass's report-call drafts to `out_dir/observations.json`; None records a dropped call."""
    body: Any = None if drafts is None else [dataclasses.asdict(d) for d in drafts]
    (out_dir / DRAFTS_FILE).write_text(json.dumps({"drafts": body}, indent=1))


def read_drafts(out_dir: Path) -> list[DraftObservation] | None:
    """The drafts `write_drafts` recorded (empty for a dropped call), or None when no file was written."""
    path = out_dir / DRAFTS_FILE
    if not path.exists():
        return None
    body = json.loads(path.read_text())["drafts"]
    return [] if body is None else [DraftObservation(**d) for d in body]


@dataclass
class DraftRecordingJudge:
    """Wraps a Judge: the report call's drafts are written to the pass's artifact folder as the call returns."""

    inner: Any
    artifacts: Path

    def lens_call(self, pass_id, request_text, composed, source, lenses):
        return self.inner.lens_call(pass_id, request_text, composed, source, lenses)

    def report_call(self, pass_id, request_text, composed, source, answers, eval_report_text, lens_ids):
        result = self.inner.report_call(pass_id, request_text, composed, source, answers, eval_report_text,
                                        lens_ids)
        write_drafts(self.artifacts / pass_id, None if result is None else result[0])
        return result


# ---------------------------------------------------------------------------------------------------------
# The coordinator's tools


@dataclass
class RoundTools:
    """`CoordinatorTools` for one round. Advisory only: no tool changes the pair plan, a status or a figure."""

    round_: int
    plan: pipeline.RoundPlan
    requests: Sequence[Request]
    journal: Journal
    ledger: Ledger
    plans_dir: Path
    failed_last_round: tuple[str, ...]
    on_ping: Callable[[str], None]
    advice: list[str] = field(default_factory=list)
    written: Path | None = None

    def plan_round(self) -> str:
        """Writes plans/round-NN.md: what runs, what failed last round, what the round is set to read."""
        self.plans_dir.mkdir(parents=True, exist_ok=True)
        path = self.plans_dir / f"round-{self.round_:02d}.md"
        lines = [f"# Round {self.round_} plan", "", f"Requests: {', '.join(r.id for r in self.requests)}",
                 f"In flight: {', '.join(self.plan.in_flight) or 'none'}", "", "## Pairs"]
        lines += [f"- {p.category}: {pair_line(p)}" for p in self.plan.plans] or ["- none"]
        lines += ["", "## Re-runs",
                  f"- last round's failed passes: {', '.join(self.failed_last_round) or 'none'}",
                  "- a pass failed for transport this round is re-run once by the pipeline", "",
                  "## Expect"]
        exposed = sorted({p.decision_id for p in self.plan.plans if p.kind is PairKind.EXPOSURE and p.decision_id})
        ablated = sorted({p.decision_id for p in self.plan.plans if p.kind is PairKind.ABLATION and p.decision_id})
        lines.append(f"- new exposure pairs for {', '.join(exposed) or 'none'}; ablation pairs for "
                     f"{', '.join(ablated) or 'none'}")
        lines += ["", "## Coordinator advice"] + ([f"- {a}" for a in self.advice] or ["- none"])
        path.write_text("\n".join(lines) + "\n")
        self.written = path
        return f"wrote {path.name}"

    def spawn_pass(self, category: str, arm: Arm, worker: int) -> str:
        if category not in {r.id for r in self.requests}:
            raise LookupError(f"{category} is not a request of round {self.round_}")
        self.advice.append(f"spawn_pass {category} {arm.value} {worker}")
        return "recorded as advice; this round's passes come from the pair plan"

    def rerun(self, pass_id: str) -> str:
        self.advice.append(f"rerun {pass_id}")
        return "recorded; a pass failed for transport is re-run once by the pipeline"

    def read_ledger(self, decision_id: str) -> Mapping[str, Any]:
        d = self.journal.get(decision_id)
        return {"id": d.id, "status": d.status.value,
                "exposure_pairs": len(self.ledger.deltas(d.id, DeltaKind.EXPOSURE)),
                "ablation_pairs": len(self.ledger.deltas(d.id, DeltaKind.ABLATION))}

    def read_journal(self) -> Mapping[str, Any]:
        return {d.id: d.status.value for d in self.journal.decisions()}

    def ping(self, reason: str) -> None:
        self.on_ping(reason)

    def halt(self, reason: str) -> None:
        # The run halts only on SPEC § 11's rule (rounds, spend cap, convergence), read at each close. A
        # coordinator's halt is a request: it is pinged to the human and the run continues.
        self.advice.append(f"halt requested: {reason}")
        self.on_ping(f"coordinator asked to halt: {reason}; the halt rule decides")


def pair_line(p: PairPlan) -> str:
    if p.kind is PairKind.NONE or p.decision_id is None:
        return "plain pair"
    return f"{p.kind.value} of {p.decision_id} (slot {p.treated_slot})"


# ---------------------------------------------------------------------------------------------------------
# Readout passes (SPEC § 12): held-out requests only, three arms x readout.reps, no judge, no Ledger


@dataclass(frozen=True)
class ReadoutTask:
    """One readout build. Never paired, never judged: it gives no delta, no fire and no lens answer."""

    pass_id: str
    round_: int
    regime: Regime
    request: Request
    request_text: str
    arm: Arm
    composition: Composition
    decisions: tuple[Decision, ...]
    notes: str | None
    rerun_of: str | None = None

    def __post_init__(self) -> None:
        if not self.request.held_out:
            raise ValueError(f"{self.pass_id}: a readout pass builds a held-out request")
        c = self.composition
        if c.exposed or c.ablated:
            raise ValueError(f"{self.pass_id}: a readout pass carries no exposure or ablation")
        if self.arm is Arm.EMPTY and (c.decision_ids or c.notes):
            raise ValueError(f"{self.pass_id}: the empty arm composes nothing")
        if self.arm is Arm.NOTES and (c.decision_ids or not c.notes):
            raise ValueError(f"{self.pass_id}: the notes arm composes the notes file only")
        if self.arm is Arm.JOURNAL and c.notes:
            raise ValueError(f"{self.pass_id}: the journal arm composes no notes")
        if {d.id for d in self.decisions} != set(c.decision_ids):
            raise ValueError(f"{self.pass_id}: decisions differ from the composition's ids")


def run_readout_pass(task: ReadoutTask, env: pipeline.PassEnv) -> Pass:
    """Builds (with the self-repair rule) and evaluates one readout pass; writes its pass and eval rows."""
    out_dir = env.artifacts / task.pass_id
    out_dir.mkdir(parents=True, exist_ok=False)
    artifact = out_dir / "index.html"
    messages = render_composition(env.book, task.request_text, task.decisions,
                                  task.notes if task.arm is Arm.NOTES else None)
    results: list = []
    base = Pass(task.pass_id, task.round_, task.request.id, task.arm, "", task.composition, task.regime)
    try:
        pipeline._turns(env, task, messages, artifact, results)  # type: ignore[arg-type]  # same fields
        pass_ = dataclasses.replace(base, worker=results[0].model, artifact_path=str(artifact),
                                    repaired=len(results) > 1)
        report = env.evaluate(pass_, task.request, task.regime, task.request_text)
    except env.transport_errors:
        failed = dataclasses.replace(base, worker=results[0].model if results else "",
                                     artifact_path=str(artifact) if artifact.exists() else None,
                                     transport_failed=True)
        env.passes.add_pass(failed)
        return failed
    env.passes.add_pass(pass_)
    env.passes.add_eval(report)
    return pass_


def _readout_child(env_factory: pipeline.EnvFactory, task: ReadoutTask) -> tuple[str, Any]:
    try:
        with env_factory() as env:
            return "ok", run_readout_pass(task, env)
    except Exception:  # noqa: BLE001 - the process boundary: the parent records the crash, loses only this pass
        return "crash", traceback.format_exc()


# ---------------------------------------------------------------------------------------------------------
# The runner


@dataclass
class Runner:
    """The run's single driver: the rounds, the closes (the single writer), then the readout."""

    spec: RunSpec
    stores: Stores
    journal: Journal
    lens_set: LensSet
    goals: GoalSource
    vocabulary: TagVocabulary
    tagger: Tagger
    coordinator: Coordinator | None
    build_close: Callable[[], RoundClose]
    env_factory: pipeline.EnvFactory
    close_log: CloseLog
    telemetry: Telemetry
    root: Path                                  # artifacts, plans and the notes file live under it
    transport_errors: tuple[type[BaseException], ...] = ()
    spend_by_role: Callable[[], Mapping[str, float]] | None = None   # None: one `all` row from the spend log
    git_log: Callable[[], Sequence[export_doc.Commit]] = lambda: ()
    manifest: Mapping[str, Any] = field(default_factory=dict)
    fallbacks: Sequence[Mapping[str, Any]] = ()
    in_process: bool = False
    say: Callable[[str], None] = print

    @property
    def artifacts(self) -> Path:
        return self.root / self.spec.paths.artifacts

    @property
    def plans_dir(self) -> Path:
        return self.root / self.spec.paths.plans

    @property
    def notes_path(self) -> Path:
        return self.root / NOTES_FILE

    # -- the run ------------------------------------------------------------------------------------------

    def run(self, stop_after: int | None = None) -> str | None:
        """Runs rounds until the halt rule holds (then the readout and the export) or `stop_after` rounds are
        closed. Returns the halt reason, or None when stopped by `stop_after`."""
        closed = list(self.close_log.closed())
        history = [c.summary for c in closed]
        halt = closed[-1].halt if closed else None
        with self.telemetry.span("run") as run_span:
            self.telemetry.event("run.manifest", **self.manifest)
            for f in self.fallbacks:
                self.telemetry.event("run.fallback", **f)
            while halt is None and (stop_after is None or len(history) < stop_after):
                n = len(history) + 1
                summary = self.round(n, history)
                history.append(summary.summary)
                halt = None if summary.halt is None else summary.halt.value
                self.telemetry.scoped(round=n).metric("run.spend", self.stores.spend.total_usd(), "usd")
                if halt is not None:
                    self.telemetry.scoped(round=n).event("run.halt", reason=halt, round=n)
                    self.say(f"halt after round {n}: {halt}")
            run_span.set(halt_reason=halt)
        if halt is not None:
            self.readout()
            self.write_export()
        return halt

    def round(self, n: int, history: Sequence[RoundSummary]) -> CloseSummary:
        """Runs (or resumes) round `n` through its close; returns the close summary."""
        tel = self.telemetry.scoped(round=n)
        regime = regime_of(n, self.spec.drift.round)
        spend_before = self._spend_by_role()
        with tel.span("round", regime=regime.value) as span:
            stored = list(self.stores.exposures.plans(n))
            requests = self._tagged_round(n, tel)
            span.set(requests=len(requests))
            if not stored:
                plan = pipeline.plan_round(n, requests, self.journal, self.stores.ledger, self.spec.run.seed,
                                           self.spec, self.stores.exposures)
            else:
                plan = self._rebuild_plan(n, requests, stored)
            span.set(pairs_planned=sum(p.kind is not PairKind.NONE for p in plan.plans))
            tasks = pipeline.round_tasks_for(plan, requests, self.spec, {d.id: d for d in self.journal.decisions()},
                                             self.lens_set.lenses(), self.notes_path)
            entered = any((self.artifacts / t.pass_id).exists() for t in tasks)
            result: pipeline.RoundResult | None = None
            if entered:
                span.set(resumed=True)
                self.say(f"round {n}: passes already ran; closing from the stored rows")
            else:
                self._coordinate(n, plan, requests, tel)
                result = self._run_passes(n, plan, tasks, tel)
            close = self.build_close()
            drafts = self._settle(n, plan.plans, result, close, tel)
            summary = with_optional(close.close, n, drafts, history, telemetry=tel)
            self._after_close(n, summary, tel)
        spent = self._spend_by_role()
        for role in sorted(set(spent) | set(spend_before)):
            tel.metric("round.spend", spent.get(role, 0.0) - spend_before.get(role, 0.0), "usd", role=role)
        return summary

    def _spend_by_role(self) -> dict[str, float]:
        if self.spend_by_role is None:
            return {"all": self.stores.spend.total_usd()}
        return dict(self.spend_by_role())

    # -- round steps --------------------------------------------------------------------------------------

    def _tagged_round(self, n: int, tel: Telemetry) -> list[Request]:
        requests = list(self.goals.round(n))
        if not requests:
            raise ValueError(f"round {n} has no requests in the goal stream")
        untagged = [r.id for r in requests if self.vocabulary.tags_of(r.id) is None]
        if untagged:
            if len(untagged) != len(requests):
                raise ValueError(f"round {n} is partly tagged; untagged: {untagged}")
            _, new = tag_round(self.tagger, self.goals, self.vocabulary, n, self.spec.stream.max_new_tags,
                               telemetry=tel)
            tel.event("round.tags", requests=len(requests), new_tags=[g.tag for g in new])
            requests = list(self.goals.round(n))
        return requests

    def _rebuild_plan(self, n: int, requests: Sequence[Request], stored: Sequence[PairPlan]) -> pipeline.RoundPlan:
        """The round's RoundPlan from its persisted pair plans (no coin is drawn again). The Journal and the
        Ledger have not moved since they were drawn: only a close moves them, and this round has none."""
        decisions = list(self.journal.decisions())
        cands = [d for d in decisions if d.status is DecisionStatus.CANDIDATE]
        in_flight = select_in_flight(cands, self.spec.gate.in_flight)
        rank = pipeline.composition_rank(decisions, self.stores.ledger, self.spec)
        by_id = {r.id: r for r in requests}
        pairs = {}
        for p in stored:
            req = by_id[p.category]
            base = select_composed(req, decisions, rank, self.spec.composition.cap)
            pairs[p.category] = compose_pair(
                req, base, exposed=p.decision_id if p.kind is PairKind.EXPOSURE else None,
                ablated=p.decision_id if p.kind is PairKind.ABLATION else None, treated_slot=p.treated_slot)
        notes = {r.id: Composition(r.id, (), notes=True) for r in requests}
        return pipeline.RoundPlan(n, tuple(stored), pairs, notes, tuple(c.id for c in in_flight), rank)

    def _ping(self, tel: Telemetry, reason: str) -> None:
        tel.event("round.ping", reason=reason)
        self.say(f"PING: {reason}")

    def _coordinate(self, n: int, plan: pipeline.RoundPlan, requests: Sequence[Request], tel: Telemetry) -> None:
        """The coordinator's turns; the round plan file is written whether or not it called plan_round."""
        prev = self.stores.passes.passes(n - 1) if n > 1 else []
        failed = tuple(sorted(p.id for p in prev if p.transport_failed or self.stores.passes.eval_of(p.id) is None))
        tools = RoundTools(n, plan, requests, self.journal, self.stores.ledger, self.plans_dir, failed,
                           lambda reason: self._ping(tel, reason))
        turns_taken: list[str] = []
        if self.coordinator is not None:
            state = RoundState(n, self.spec.run.rounds, tuple(r.id for r in requests),
                               "; ".join(f"{p.category}: {pair_line(p)}" for p in plan.plans), plan.in_flight, failed)
            try:
                turns = self.coordinator.run_round(state, tools)
            except self.transport_errors as e:
                tools.advice.append(f"coordinator unreachable: {type(e).__name__}")
                turns = []
            turns_taken = [t.call.tool if t.call else "unusable" for t in turns]
        if tools.written is None:
            tools.plan_round()
        tel.event("round.coordinator", turns=turns_taken, plan=str(tools.written))

    def _run_passes(self, n: int, plan: pipeline.RoundPlan, tasks: Sequence[pipeline.PassTask],
                    tel: Telemetry) -> pipeline.RoundResult:
        parallel, reruns = pipeline.round_limits(self.spec)
        with tel.span("round.passes", passes=len(tasks)):
            result = with_optional(pipeline.run_round, n, plan.plans, tasks, self.env_factory, parallel=parallel,
                                   reruns=reruns, in_process=self.in_process, telemetry=tel,
                                   pass_timeout_s=pipeline.pass_timeout(self.spec))
        reruns_run = [o.pass_.id for o in result.outcomes if pipeline.RERUN_SUFFIX in o.pass_.id]
        counts = {
            "scored": sum(o.report is not None for o in result.outcomes),
            "transport_failed": sum(o.pass_.transport_failed for o in result.outcomes),
            "crashed": len(result.crashed),
            "rerun": len(reruns_run) + sum(pipeline.RERUN_SUFFIX in i for i in result.crashed),
        }
        for outcome, count in counts.items():
            tel.metric("round.pass_count", count, "count", outcome=outcome)
        tel.metric("round.concurrency", 1 if self.in_process else min(parallel, len(tasks)), "count")
        for pid, why in sorted(result.crashed.items()):
            self.say(f"round {n}: pass {pid} crashed: {why.strip().splitlines()[-1] if why.strip() else why}")
        return result

    def _settle(self, n: int, plans: Sequence[PairPlan], result: pipeline.RoundResult | None, close: RoundClose,
                tel: Telemetry) -> dict[str, list[DraftObservation]]:
        """Appends the round's Ledger rows not yet appended and writes its observations; returns the drafts.

        Both are read from the stores, so a fresh round and a resumed one settle the same way."""
        passes = list(self.stores.passes.passes(n))
        evals = {p.id: e for p in passes if (e := self.stores.passes.eval_of(p.id)) is not None}
        deltas = pair_deltas(n, plans, passes, evals)
        if result is not None and _delta_keys(deltas) != _delta_keys(result.deltas):
            raise RuntimeError(f"round {n}: the stored rows give other deltas than the round returned")
        appended = 0
        for d in deltas:
            have = self.stores.ledger.deltas(d.decision_id, d.kind)
            if not any(r.round == n and r.category == d.category for r in have):
                self.stores.ledger.append(d)
                appended += 1
        drafts: dict[str, list[DraftObservation]] = {}
        returned: set[str] = set()                  # passes whose outcome reached this process
        if result is not None:
            returned = {o.pass_.id for o in result.outcomes}
            for o in result.observations:
                drafts.setdefault(o.pass_id, []).append(o.draft)
        stored = {o.pass_id for o in self.stores.observations.observations() if o.round == n}
        lost: list[str] = []
        for p in passes:
            if p.arm is not Arm.JOURNAL or p.id not in evals or p.id in returned or p.id in stored:
                continue
            got = read_drafts(self.artifacts / p.id)
            if got is None:
                lost.append(p.id)
            elif got:
                drafts[p.id] = got
        if lost:
            tel.event("round.observations_unread", passes=sorted(lost))
            self.say(f"round {n}: report-call observations unread for {sorted(lost)}")
        close._write_observations(n, drafts)   # persisted now; the close reads them back as already written
        tel.event("round.settled", deltas=len(deltas), appended=appended, observations=sum(map(len, drafts.values())))
        return drafts

    def _after_close(self, n: int, summary: CloseSummary, tel: Telemetry) -> None:
        arm_of = {p.id: p.arm for p in self.stores.passes.passes(n)}
        mine = [o for o in self.stores.observations.observations() if o.round == n and o.pass_id in arm_of]
        append_free(self.notes_path, mine, arm_of)
        self.close_log.record(summary.summary, None if summary.halt is None else summary.halt.value)
        for ping in summary.pings:
            self._ping(tel, ping.reason)
        self.say(f"round {n} closed: {len(summary.flips)} flips, {len(summary.nominations)} drafted, "
                 f"{len(summary.observations)} observations, spend ${self.stores.spend.total_usd():.2f}")

    # -- steer ---------------------------------------------------------------------------------------------

    def steer(self, decision_id: str, action: str, reason: str) -> Steer:
        """Writes a steer event, applied at the next round close."""
        self.journal.get(decision_id)                       # raises for an unknown decision
        steer = Steer(decision_id, action, reason, len(self.close_log.closed()) + 1)
        self.stores.events.append_steer(steer)
        return steer

    # -- readout -------------------------------------------------------------------------------------------

    def readout_tasks(self) -> list[ReadoutTask]:
        """Every readout build: each held-out request x `readout.reps` x (empty, notes, journal), regime
        `readout.regime`, S* and the notes file frozen as they stand now."""
        closed = self.close_log.closed()
        if not closed:
            raise ValueError("the readout runs after the halt; no round has closed")
        last = closed[-1].summary.round
        held = list(self.goals.held_out())
        if any(self.vocabulary.tags_of(r.id) is None for r in held):
            tag_held_out(self.tagger, self.goals, self.vocabulary, last, telemetry=self.telemetry)
            held = list(self.goals.held_out())
        regime = Regime(self.spec.readout.regime)
        decisions = list(self.journal.decisions())
        rank = pipeline.composition_rank(decisions, self.stores.ledger, self.spec)
        by_id = {d.id: d for d in decisions}
        notes = notes_text(self.notes_path, self.spec.notes.max_tokens, self.spec.notes.chars_per_token)
        round_ = last + 1
        tasks = []
        for req in sorted(held, key=lambda r: r.id):
            text = compose_request_text(req, regime, self.spec.drift.constraint)
            composed = select_composed(req, decisions, rank, self.spec.composition.cap)
            for rep in range(self.spec.readout.reps):
                for arm in (Arm.EMPTY, Arm.NOTES, Arm.JOURNAL):
                    ids = composed if arm is Arm.JOURNAL else ()
                    tasks.append(ReadoutTask(
                        pipeline.pass_id(round_, req.id, arm, rep), round_, regime, req, text, arm,
                        Composition(req.id, ids, notes=arm is Arm.NOTES), tuple(by_id[i] for i in ids),
                        notes if arm is Arm.NOTES else None))
        return tasks

    def readout(self) -> None:
        """Runs the readout builds not yet run (transport failures re-run `run.transport_reruns` times)."""
        tasks = self.readout_tasks()
        with self.telemetry.span("readout", reps=self.spec.readout.reps) as span:
            todo = [t for t in tasks if not (self.artifacts / t.pass_id).exists()]
            for attempt in range(self.spec.run.transport_reruns + 1):
                if not todo:
                    break
                done = self._run_readout(todo)
                failed = [t for t in todo if done.get(t.pass_id) is not None and done[t.pass_id].transport_failed]
                todo = [dataclasses.replace(t, pass_id=pipeline.rerun_id(t.rerun_of or t.pass_id, attempt + 1),
                                            rerun_of=t.rerun_of or t.pass_id) for t in failed]
            span.set(passes=len(tasks))
        for line in self.readout_lines():
            self.say(line)

    def _run_readout(self, tasks: Sequence[ReadoutTask]) -> dict[str, Pass | None]:
        out: dict[str, Pass | None] = {}
        if self.in_process:
            with self.env_factory() as env:
                for t in tasks:
                    try:
                        out[t.pass_id] = run_readout_pass(t, env)
                    except Exception:  # noqa: BLE001 - a crash loses only this pass; it reads unscored
                        self.say(f"readout pass {t.pass_id} crashed:\n{traceback.format_exc()}")
                        out[t.pass_id] = None
            return out
        parallel = self.spec.run.max_parallel_passes
        with ProcessPoolExecutor(max_workers=parallel, mp_context=multiprocessing.get_context("spawn")) as pool:
            futures = {t.pass_id: pool.submit(_readout_child, self.env_factory, t) for t in tasks}
            for pid, fut in futures.items():
                status, value = fut.result()
                if status == "ok":
                    out[pid] = value
                else:
                    self.say(f"readout pass {pid} crashed:\n{value}")
                    out[pid] = None
        return out

    def composed_tokens(self) -> dict[str, int]:
        """Tokens composed into each readout pass's worker prompt (decision payloads, notes text)."""
        held = held_out_ids(self.goals)
        notes = notes_text(self.notes_path, self.spec.notes.max_tokens, self.spec.notes.chars_per_token)
        by_id = {d.id: d for d in self.journal.decisions()}
        out = {}
        for p in self.stores.passes.passes():
            if p.category not in held:
                continue
            n = sum(approx_tokens(by_id[i].payload, self.spec.notes.chars_per_token) for i in p.composition.decision_ids)
            if p.composition.notes and notes:
                n += approx_tokens(notes, self.spec.notes.chars_per_token)
            out[p.id] = n
        return out

    def readout_stores(self) -> ReadoutStores:
        s = self.stores
        return ReadoutStores(s.passes, self.goals, self.journal, self.lens_set, s.events, s.ledger, s.spend,
                             self.composed_tokens(), self.vocabulary)

    def readout_lines(self) -> list[str]:
        from friday.app.readout import lines, run_readout
        return lines(run_readout(self.readout_stores(), self.spec, telemetry=self.telemetry))

    def write_export(self) -> Path:
        """Writes export.json under the run root from the stores as they stand."""
        s = self.stores
        stores = export_doc.ExportStores(s.passes, self.goals, s.judge, s.observations, s.pools, s.ledger, s.events,
                                         s.spend, self.composed_tokens(), self.vocabulary)
        doc = export_doc.build(stores, self.journal, self.lens_set, self.git_log(), self.spec)
        path = self.root / EXPORT_FILE
        path.write_text(export_doc.dumps(doc) + "\n")
        return path


def _delta_keys(deltas: Sequence[LedgerDelta]) -> list[tuple]:
    return sorted((d.decision_id, d.round, d.category, d.kind.value, round(d.d, 12), round(d.dQ, 12)) for d in deltas)
