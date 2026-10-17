"""The pass pipeline (SPEC §§ 5, 6, 8, 11): plan a round's pairs, run each pass, return the paired deltas.

`plan_round` draws the round's exposures and persists them before any pass runs. `run_pass` builds one
artifact (with the self-repair rule), evaluates it and, on the journal arm, runs the two judge calls; it writes
only its own rows (pass, eval, lens answers, fires) and `artifacts/<pass_id>/`. `run_round` runs the passes,
one process each, re-runs transport failures, and returns the paired deltas and the report call's
observations. The Ledger, observations, pools, events and the Journal belong to the round close: nothing here
writes them. A pass proposes; nothing it proposes is yet true.
"""

from __future__ import annotations

import dataclasses
import json
import multiprocessing
import traceback
from collections.abc import Callable, Collection, Mapping, Sequence
from contextlib import AbstractContextManager, ExitStack
from dataclasses import dataclass, field
from multiprocessing.connection import wait
from pathlib import Path
from typing import Any

from friday.algorithms.composition import compose_pair, select_composed
from friday.algorithms.exposure import assign_exposures
from friday.algorithms.gate import select_in_flight
from friday.app.ledger import pair_deltas
from friday.app.notes import notes_text
from friday.app.request_text import regime_of
from friday.app.request_text import request_text as compose_request_text
from friday.config import RunSpec
from friday.domain.lifecycle import COMPOSABLE_DECISION
from friday.domain.types import (
    Arm,
    Composition,
    Decision,
    DecisionStatus,
    DeltaKind,
    Disposition,
    EvalReport,
    Fire,
    LedgerDelta,
    Lens,
    LensStatus,
    PairKind,
    PairPlan,
    Pass,
    Regime,
    Request,
    Viewport,
)
from friday.eval import probe
from friday.ports.browser import Browser, Page
from friday.ports.models import ChatResult, Message
from friday.ports.stores import ExposureStore, Journal, JudgeStore, Ledger, PassStore
from friday.roles.judge import DraftObservation
from friday.roles.prompts import PromptBook
from friday.roles.worker import Worker, extract_html, render_composition, repair_message
from friday.stats.eprocess import BETTING, wealth

JUDGED_LENS = frozenset({LensStatus.ACTIVE, LensStatus.HIDDEN})  # hidden lenses are answered and valued (§ 6)
RERUN_SUFFIX = "-rr"


# ---------------------------------------------------------------------------------------------------------
# Pass ids


def pass_id(round_: int, request_id: str, arm: Arm, slot: int) -> str:
    """The deterministic pass id `r{round:02d}-{request_id}-{arm}-{slot}`."""
    return f"r{round_:02d}-{request_id}-{arm.value}-{slot}"


def rerun_id(original_id: str, attempt: int = 1) -> str:
    """The id of re-run `attempt` (1-based) of `original_id`: `-rr`, then `-rr2`, ..."""
    if attempt < 1:
        raise ValueError("re-run attempts start at 1")
    return original_id + RERUN_SUFFIX + ("" if attempt == 1 else str(attempt))


# ---------------------------------------------------------------------------------------------------------
# Round planning


@dataclass(frozen=True)
class RoundPlan:
    """A round's persisted pair plans and the compositions they imply, per request."""

    round_: int
    plans: tuple[PairPlan, ...]
    pairs: Mapping[str, tuple[Composition, Composition]]   # request id -> (slot 0, slot 1), journal arm
    notes: Mapping[str, Composition]                         # request id -> the notes-arm composition
    in_flight: tuple[str, ...]
    rank: Mapping[str, float]                                # composable decision id -> exposure-account wealth


def composition_rank(decisions: Sequence[Decision], ledger: Ledger, spec: RunSpec) -> dict[str, float]:
    """Each composable decision's current exposure-account e-process wealth, recomputed from the Ledger.

    The cap's rank (`composition.rank_by: e_value`); ties are broken by id in `select_composed`.
    """
    if spec.composition.rank_by != "e_value":
        raise ValueError(f"composition.rank_by {spec.composition.rank_by!r} is not implemented; only e_value")
    betting = BETTING.build(spec.gate.betting.name, **spec.gate.betting.params)
    return {
        d.id: wealth([r.d for r in ledger.deltas(d.id, DeltaKind.EXPOSURE)], betting)
        for d in decisions if d.status in COMPOSABLE_DECISION
    }


def plan_round(
    round_: int, requests: Sequence[Request], journal: Journal, ledger: Ledger, seed: int, cfg: RunSpec,
    exposures: ExposureStore,
) -> RoundPlan:
    """The round's pair plans, persisted to `exposures` before any pass runs, and both compositions per request.

    Raises on a held-out request or one from another round: held-out categories are read only at the close.
    """
    for r in requests:
        if r.held_out:
            raise ValueError(f"{r.id} is held out; it is never planned in a training round")
        if r.round != round_:
            raise ValueError(f"{r.id} belongs to round {r.round}, not {round_}")
    ids = [r.id for r in requests]
    if len(set(ids)) != len(ids):
        raise ValueError("a request appears twice in the round")
    decisions = list(journal.decisions())
    candidates = [d for d in decisions if d.status is DecisionStatus.CANDIDATE]
    in_flight = select_in_flight(candidates, cfg.gate.in_flight)
    rank = composition_rank(decisions, ledger, cfg)
    composed = {r.id: select_composed(r, decisions, rank, cfg.composition.cap) for r in requests}
    pair_counts = {c.id: len(ledger.deltas(c.id, DeltaKind.EXPOSURE)) for c in in_flight}
    ablation_counts = {i: len(ledger.deltas(i, DeltaKind.ABLATION)) for i in rank}
    plans = assign_exposures(round_, requests, in_flight, composed, pair_counts, ablation_counts, seed,
                             cfg.detect.ablate_p)
    by_id = {r.id: r for r in requests}
    pairs: dict[str, tuple[Composition, Composition]] = {}
    for plan in plans:
        req = by_id[plan.category]
        pairs[plan.category] = compose_pair(
            req, composed[plan.category],
            exposed=plan.decision_id if plan.kind is PairKind.EXPOSURE else None,
            ablated=plan.decision_id if plan.kind is PairKind.ABLATION else None,
            treated_slot=plan.treated_slot,
        )
    notes = {r.id: Composition(r.id, (), notes=True) for r in requests}
    exposures.add_plans(round_, plans)
    return RoundPlan(round_, tuple(plans), pairs, notes, tuple(c.id for c in in_flight), rank)


# ---------------------------------------------------------------------------------------------------------
# Pass tasks


@dataclass(frozen=True)
class PassTask:
    """Everything one pass reads, frozen at round start. Picklable: it crosses to the pass's process.

    A re-run is this task with a new id and nothing else changed, so it reuses the plan and composition.
    """

    pass_id: str
    round_: int
    regime: Regime
    request: Request
    request_text: str
    arm: Arm
    slot: int | None                      # pair slot on the journal arm; None on the notes arm
    composition: Composition
    decisions: tuple[Decision, ...]       # the composed decisions, the exposed candidate included
    lenses: tuple[Lens, ...] = ()         # the lenses the judge answers; empty on the notes arm
    notes: str | None = None              # the notes text the notes arm reads; None elsewhere
    rerun_of: str | None = None

    def __post_init__(self) -> None:
        if {d.id for d in self.decisions} != set(self.composition.decision_ids):
            raise ValueError(f"{self.pass_id}: decisions differ from the composition's ids")
        if self.composition.request_id != self.request.id:
            raise ValueError(f"{self.pass_id}: composition is for {self.composition.request_id}")
        if self.arm is Arm.JOURNAL and (self.slot not in (0, 1) or self.composition.notes or self.notes):
            raise ValueError(f"{self.pass_id}: a journal-arm pass has a pair slot and no notes")
        if self.arm is Arm.NOTES and (self.slot is not None or self.decisions or self.lenses
                                      or not self.composition.notes):
            raise ValueError(f"{self.pass_id}: a notes-arm pass composes the notes file only")
        if self.arm is Arm.EMPTY:
            raise ValueError(f"{self.pass_id}: the empty arm is built only by the readout")


def rerun_task(task: PassTask, attempt: int = 1) -> PassTask:
    """The re-run of `task`: the same request text, composition, decisions, lenses and notes, a new id."""
    original = task.rerun_of or task.pass_id
    return dataclasses.replace(task, pass_id=rerun_id(original, attempt), rerun_of=original)


def round_tasks(
    plan: RoundPlan, requests: Sequence[Request], regime: Regime, drift_constraint: str,
    decisions: Mapping[str, Decision], lenses: Sequence[Lens], notes: str | None,
) -> list[PassTask]:
    """The round's passes: two journal-arm passes per pair and one notes-arm pass per request.

    `decisions` is the Journal at round start by id; `lenses` the lens set (the judged ones are kept);
    `notes` the notes file's text at round start.
    """
    judged = tuple(sorted((x for x in lenses if x.status in JUDGED_LENS), key=lambda x: x.id))
    tasks: list[PassTask] = []
    for req in sorted(requests, key=lambda r: r.id):
        text = compose_request_text(req, regime, drift_constraint)
        for slot, comp in enumerate(plan.pairs[req.id]):
            tasks.append(PassTask(
                pass_id(plan.round_, req.id, Arm.JOURNAL, slot), plan.round_, regime, req, text, Arm.JOURNAL,
                slot, comp, tuple(decisions[i] for i in comp.decision_ids), judged,
            ))
        tasks.append(PassTask(
            pass_id(plan.round_, req.id, Arm.NOTES, 0), plan.round_, regime, req, text, Arm.NOTES, None,
            plan.notes[req.id], (), (), notes,
        ))
    return tasks


def round_tasks_for(
    plan: RoundPlan, requests: Sequence[Request], spec: RunSpec, decisions: Mapping[str, Decision],
    lenses: Sequence[Lens], notes_path: str | Path,
) -> list[PassTask]:
    """`round_tasks` with the regime, the drift constraint and the notes budget read from `spec`."""
    regime = regime_of(plan.round_, spec.drift.round)
    notes = notes_text(notes_path, spec.notes.max_tokens)
    return round_tasks(plan, requests, regime, spec.drift.constraint, decisions, lenses, notes)


def round_limits(spec: RunSpec) -> tuple[int, int]:
    """(`run.max_parallel_passes`, `run.transport_reruns`): the `parallel` and `reruns` of `run_round`."""
    return spec.run.max_parallel_passes, spec.run.transport_reruns


# ---------------------------------------------------------------------------------------------------------
# One pass


def page_missing_ids(page: Page, contract: Collection[str]) -> list[str]:
    """Contract ids (with '#') absent from the loaded page: the eval's probe-side check."""
    return probe.missing_contract_ids(page, contract)


@dataclass(frozen=True)
class PassSettings:
    """The run.yml knobs one pass reads."""

    repair_turns: int
    max_artifact_bytes: int
    viewports: Mapping[Regime, Viewport]   # the first viewport of each regime: where the repair load runs

    @classmethod
    def from_spec(cls, spec: RunSpec) -> PassSettings:
        """The pass settings `spec` gives."""
        return cls(
            repair_turns=spec.worker.repair_turns,
            max_artifact_bytes=spec.worker.max_artifact_bytes,
            viewports={Regime(k): tuple(v[0]) for k, v in spec.eval.viewports.items()},
        )


@dataclass
class PassEnv:
    """One process's collaborators: its own model client, browser and store connections."""

    worker: Worker
    book: PromptBook
    browser: Browser
    evaluate: Callable[[Pass, Request, Regime, str], EvalReport]   # Evaluator.run in the run
    judge: Any                                                     # roles.judge.Judge
    passes: PassStore
    judge_store: JudgeStore
    artifacts: Path
    settings: PassSettings
    transport_errors: tuple[type[BaseException], ...]              # adapters.crusoe.TransportFailure in the run
    missing_ids: Callable[[Page, Collection[str]], list[str]] = page_missing_ids


@dataclass(frozen=True)
class PendingObservation:
    """One report-call observation with its pass's context; the round close mints its O- id."""

    pass_id: str
    round_: int
    category: str
    draft: DraftObservation


@dataclass(frozen=True)
class PassOutcome:
    """What one pass recorded, returned to the parent process."""

    pass_: Pass
    report: EvalReport | None
    observations: tuple[PendingObservation, ...] = ()
    artifact_bytes: int | None = None
    oversize: bool = False
    dropped_calls: tuple[str, ...] = ()       # "lens" and/or "report": judge calls dropped after their re-ask
    transport_error: str = ""


def _turns(env: PassEnv, task: PassTask, messages: list[Message], artifact: Path, results: list[ChatResult],
           ) -> tuple[str, bool, bool]:
    """Builds the artifact: the first turn, then up to `repair_turns` self-repair turns.

    Appends each chat result to `results` as it arrives (so a transport failure keeps the turns before it).
    Returns the final HTML, whether a repair turn ran, and whether the first reply held an HTML document.
    A reply with no document is written as the worker sent it: it is what the worker built.
    A repair reply with no document leaves the previous file in place.
    """
    first = env.worker.first_turn(messages, tag=task.pass_id)
    results.append(first)
    found = extract_html(first.text)
    html = found if found is not None else first.text
    artifact.write_text(html)
    repaired = False
    convo, last = list(messages), first
    viewport = env.settings.viewports[task.regime]
    for turn in range(env.settings.repair_turns):
        errors, missing = _repair_reading(env, artifact, viewport, task.request.contract)
        if not errors and not missing:
            break
        msg = repair_message(env.book, errors, [i.lstrip("#") for i in missing])
        reply = env.worker.repair_turn(convo, last, msg, tag=f"{task.pass_id}:{turn + 1}")
        results.append(reply)
        repaired = True
        convo = [*convo, Message("assistant", last.text), msg]
        last = reply
        fixed = extract_html(reply.text)
        if fixed is not None:
            html = fixed
            artifact.write_text(html)
    return html, repaired, found is not None


def _repair_reading(env: PassEnv, artifact: Path, viewport: Viewport, contract: Collection[str],
                    ) -> tuple[list[str], list[str]]:
    """The loaded page's console errors and missing contract ids: what the self-repair turn shows."""
    with ExitStack() as stack:
        try:
            page = stack.enter_context(env.browser.page(str(artifact), viewport))
        except Exception as e:  # noqa: BLE001 - a page that does not load is the worker's defect; it earns the turn
            return [f"the page did not load: {type(e).__name__}: {e}"], sorted(contract)
        return list(page.console_errors()), list(env.missing_ids(page, contract))


def _meta(task: PassTask, results: Sequence[ChatResult], **extra: Any) -> dict[str, Any]:
    return {
        "pass_id": task.pass_id, "round": task.round_, "category": task.request.id, "arm": task.arm.value,
        "slot": task.slot, "rerun_of": task.rerun_of,
        "turns": [{"model": r.model, "tokens_in": r.tokens_in, "tokens_out": r.tokens_out, "cost_usd": r.cost_usd,
                   "sent": r.raw.get("sent")} for r in results],
        **extra,
    }


def run_pass(task: PassTask, env: PassEnv) -> PassOutcome:
    """Runs one pass and writes its own rows and `artifacts/<pass_id>/`.

    The size bound: an artifact over `worker.max_artifact_bytes` is kept whole, recorded `oversize`, and
    evaluated like any other. Truncating it would score a file the worker never wrote (and cut its how-to and
    closing tags); dropping it would lose a pass with a loss, which SPEC § 5 values over format. Both workers
    of a pair are under the same rule, so the delta stays paired.

    A transport failure in a worker turn or the eval records the pass `transport_failed`, with no eval: it
    gives no delta and is offered for one re-run. A transport failure in a judge call drops that call, like a
    call dropped after its re-ask: the eval stands and the fires stay `undisposed`.
    """
    out_dir = env.artifacts / task.pass_id
    out_dir.mkdir(parents=True, exist_ok=False)
    artifact = out_dir / "index.html"
    messages = render_composition(env.book, task.request_text, task.decisions,
                                  task.notes if task.composition.notes else None)
    results: list[ChatResult] = []
    base = Pass(task.pass_id, task.round_, task.request.id, task.arm, "", task.composition, task.regime,
                pair_slot=task.slot)
    try:
        html, repaired, extracted = _turns(env, task, messages, artifact, results)
        size = len(html.encode())
        oversize = size > env.settings.max_artifact_bytes
        pass_ = dataclasses.replace(base, worker=results[0].model, artifact_path=str(artifact), repaired=repaired)
        report = env.evaluate(pass_, task.request, task.regime, task.request_text)
    except env.transport_errors as e:
        failed = dataclasses.replace(
            base, worker=results[0].model if results else "",
            artifact_path=str(artifact) if artifact.exists() else None, transport_failed=True,
        )
        env.passes.add_pass(failed)
        (out_dir / "pass.json").write_text(json.dumps(_meta(task, results, transport_error=str(e)), indent=1))
        return PassOutcome(failed, None, transport_error=f"{type(e).__name__}: {e}")
    if report.pass_id != pass_.id:
        raise ValueError(f"eval of {report.pass_id} returned for {pass_.id}")
    env.passes.add_pass(pass_)
    env.passes.add_eval(report)
    (out_dir / "eval.json").write_text(json.dumps(dataclasses.asdict(report), indent=1, default=str))
    observations: tuple[PendingObservation, ...] = ()
    dropped: list[str] = []
    if task.arm is Arm.JOURNAL:
        observations, dropped = _judge(env, task, pass_, html, report)
    (out_dir / "pass.json").write_text(json.dumps(_meta(
        task, results, bytes=size, oversize=oversize, extracted=extracted, repaired=repaired,
        dropped_calls=dropped,
    ), indent=1))
    return PassOutcome(pass_, report, observations, size, oversize, tuple(dropped))


def _judge(env: PassEnv, task: PassTask, pass_: Pass, html: str, report: EvalReport,
           ) -> tuple[tuple[PendingObservation, ...], list[str]]:
    """The lens call (artifact only), then the report call (adds the eval report); writes answers and fires."""
    composed = {d.id: d.payload for d in sorted(task.decisions, key=lambda d: d.id)}
    lens_ids = [x.id for x in task.lenses]
    dropped: list[str] = []
    try:
        answers = env.judge.lens_call(task.pass_id, task.request_text, composed, html, task.lenses)
    except env.transport_errors:
        answers = None
    if answers is None:
        dropped.append("lens")
    else:
        env.judge_store.add_lens_answers(answers)
    try:
        result = env.judge.report_call(task.pass_id, task.request_text, composed, html, answers or [],
                                       eval_report_text(report), lens_ids)
    except env.transport_errors:
        result = None
    if result is None:
        dropped.append("report")
        fires = [Fire(pass_.id, i, task.round_) for i in composed]
        env.judge_store.add_fires(fires)
        return (), dropped
    drafts, dispositions = result
    missing = set(composed) - set(dispositions)
    if missing:
        raise ValueError(f"{pass_.id}: report call returned no disposition for {sorted(missing)}")
    fires = [Fire(pass_.id, i, task.round_, dispositions[i][0], dispositions[i][1]) for i in composed]
    if any(f.disposition is Disposition.UNDISPOSED for f in fires):
        raise ValueError(f"{pass_.id}: a returned disposition is undisposed")
    env.judge_store.add_fires(fires)
    return tuple(PendingObservation(pass_.id, task.round_, task.request.id, d) for d in drafts), dropped


def eval_report_text(report: EvalReport) -> str:
    """The eval report as the report call reads it (SPEC § 6). Never names the regime; never the loss."""
    lines = ["Acceptance checks:"]
    lines += [f"- {c.check_id}: {'pass' if c.passed else 'fail'}" + (f" (failing step: {c.failing_step})"
              if not c.passed and c.failing_step else "") for c in report.checks] or ["- none"]
    lines.append("Probe suite:")
    for p in report.probes:
        lines.append(f"- {p.viewport[0]}x{p.viewport[1]}: loaded {'yes' if p.loaded else 'no'}, "
                     f"{len(p.console_errors)} console errors, {p.responsive} of {p.controls} controls responsive")
    if not report.probes:
        lines.append("- no reading")
    lines.append("How-to:")
    for h in report.how_to:
        lines.append(f"- {h.viewport[0]}x{h.viewport[1]}: read at rung {int(h.parse.rung)}; uncovered contract ids: "
                     f"{', '.join(h.parse.undeclared_contract_ids) or 'none'}")
        lines += [f"  - control {r.check_id}: {'pass' if r.passed else 'fail'}" for r in h.results]
    if not report.how_to:
        lines.append("- no reading")
    lines.append(f"Silent controls: {report.silent_controls}")
    lines.append("Console errors:")
    lines += [f"- {e}" for e in report.console_errors] or ["- none"]
    lines.append(f"Quality: {report.eslint_errors} ESLint errors, {report.axe_violations} axe-core violations "
                 f"(Q = {report.Q})")
    lines.append("Rubric scores (1 to 5): " + ", ".join(str(s) for s in report.rubric))
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------------------
# The round


@dataclass(frozen=True)
class RoundResult:
    """A round's pass outcomes, its paired deltas and its observations, for the round close."""

    outcomes: tuple[PassOutcome, ...]
    deltas: tuple[LedgerDelta, ...]
    observations: tuple[PendingObservation, ...]
    crashed: Mapping[str, str] = field(default_factory=dict)       # pass id -> traceback; never re-run
    transport_failed: tuple[str, ...] = ()                         # slots still failed after their re-runs


EnvFactory = Callable[[], AbstractContextManager[PassEnv]]


def _child(env_factory: EnvFactory, task: PassTask, conn: Any) -> None:
    """A pass's process: opens its own collaborators, runs the pass, sends the outcome or the traceback."""
    try:
        with env_factory() as env:
            outcome = run_pass(task, env)
    except BaseException:  # noqa: BLE001 - the process boundary: the parent records the crash, loses only this pass
        conn.send(("crash", traceback.format_exc()))
    else:
        conn.send(("ok", outcome))
    finally:
        conn.close()


def _run_processes(tasks: Sequence[PassTask], env_factory: EnvFactory, parallel: int,
                   ) -> tuple[list[PassOutcome], dict[str, str]]:
    ctx = multiprocessing.get_context("spawn")       # no browser or connection is inherited by a child
    pending = list(tasks)
    running: dict[Any, tuple[Any, PassTask]] = {}
    outcomes: list[PassOutcome] = []
    crashed: dict[str, str] = {}
    while pending or running:
        while pending and len(running) < parallel:
            task = pending.pop(0)
            recv, send = ctx.Pipe(duplex=False)
            proc = ctx.Process(target=_child, args=(env_factory, task, send), name=task.pass_id)
            proc.start()
            send.close()
            running[recv] = (proc, task)
        for conn in wait(list(running)):
            proc, task = running.pop(conn)
            try:
                status, value = conn.recv()
            except EOFError:
                status, value = "crash", "process exited without a result"
            conn.close()
            proc.join()
            if status == "ok":
                outcomes.append(value)
            else:
                crashed[task.pass_id] = f"{value}\nexit code {proc.exitcode}"
    return outcomes, crashed


def _run_in_process(tasks: Sequence[PassTask], env_factory: EnvFactory) -> tuple[list[PassOutcome], dict[str, str]]:
    outcomes: list[PassOutcome] = []
    crashed: dict[str, str] = {}
    with env_factory() as env:
        for task in tasks:
            try:
                outcomes.append(run_pass(task, env))
            except Exception:  # noqa: BLE001 - same rule as a pass's process: a crash loses only that pass
                crashed[task.pass_id] = traceback.format_exc()
    return outcomes, crashed


def run_round(
    round_: int, plans: Sequence[PairPlan], tasks: Sequence[PassTask], env_factory: EnvFactory, *,
    parallel: int, reruns: int, in_process: bool = False,
) -> RoundResult:
    """Runs every pass, re-runs each transport failure up to `reruns` times, and computes the paired deltas.

    `parallel` is `run.max_parallel_passes`: at most that many passes run at once, each in its own process
    with its own browser and store connections (`env_factory` opens them in the child). `in_process` runs
    the passes one after another in this process with one environment (tests and dry runs). A crashed pass
    is reported in `crashed`, gives no delta and is not re-run. Writes no Ledger row: the round close does.
    """
    if parallel < 1:
        raise ValueError("run.max_parallel_passes must be at least 1")
    if any(t.round_ != round_ for t in tasks):
        raise ValueError("a task belongs to another round")
    if len({t.pass_id for t in tasks}) != len(tasks):
        raise ValueError("pass ids repeat within the round")

    def run(batch: Sequence[PassTask]) -> tuple[list[PassOutcome], dict[str, str]]:
        return _run_in_process(batch, env_factory) if in_process else _run_processes(batch, env_factory, parallel)

    outcomes, crashed = run(tasks)
    task_of = {t.pass_id: t for t in tasks}
    failed = [o.pass_.id for o in outcomes if o.pass_.transport_failed]
    for attempt in range(1, reruns + 1):
        if not failed:
            break
        retry = [rerun_task(task_of[i], attempt) for i in failed]
        task_of.update((t.pass_id, t) for t in retry)
        more, more_crashed = run(retry)
        outcomes += more
        crashed.update(more_crashed)
        failed = [o.pass_.id for o in more if o.pass_.transport_failed]
    passes = [o.pass_ for o in outcomes]
    evals = {o.pass_.id: o.report for o in outcomes if o.report is not None}
    deltas = pair_deltas(round_, plans, passes, evals)
    observations = tuple(ob for o in sorted(outcomes, key=lambda o: o.pass_.id) for ob in o.observations)
    return RoundResult(tuple(outcomes), tuple(deltas), observations, crashed, tuple(failed))
