"""The pass pipeline (SPEC §§ 5, 6, 8, 11): plan a round's pairs, run each pass, return the paired deltas.

`plan_round` draws the round's exposures and persists them before any pass runs. `run_pass` builds one
artifact (with the self-repair rule), evaluates it and, on the journal arm, runs the two judge calls; it writes
only its own rows (pass, eval, lens answers, fires) and `artifacts/<pass_id>/`. `run_round` runs the passes,
one process each, re-runs transport failures, and returns the paired deltas and the report call's
observations. The Ledger, observations, pools, events and the Journal belong to the round close: nothing here
writes them. A pass proposes; nothing it proposes is yet true.

Telemetry (docs/telemetry.md, Pass): `run_pass` narrows the process's one log to the pass for the pass's
duration, so every collaborator holding that log (the model client, the evaluator, the judge, the browser)
writes under the pass's scope, and opens `pass` with `pass.compose`, `pass.worker` and `pass.repair` inside.
`pass.outcome` is written by the pass for a scored or transport-failed pass, and by `run_round` for a pass
that crashed, died or was terminated.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import multiprocessing
import os
import re
import time
import traceback
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass, field
from multiprocessing.connection import wait
from pathlib import Path
from typing import Any

from friday.algorithms.composition import compose_pair, select_composed
from friday.algorithms.exposure import assign_exposures
from friday.algorithms.gate import select_in_flight
from friday.app.ledger import pair_deltas
from friday.app.notes import approx_tokens, notes_text
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
from friday.eval import how_to, probe
from friday.ports.browser import Browser, Page
from friday.ports.models import ChatResult, Message
from friday.ports.stores import ExposureStore, Journal, JudgeStore, Ledger, PassStore
from friday.ports.telemetry import Telemetry
from friday.roles.critic import Criticism, match_request, revision_message
from friday.roles.judge import DraftObservation
from friday.roles.prompts import PromptBook
from friday.roles.worker import Worker, extract_html, render_composition, repair_message
from friday.stats.eprocess import BETTING, wealth

JUDGED_LENS = frozenset({LensStatus.ACTIVE, LensStatus.HIDDEN})  # hidden lenses are answered and valued (§ 6)
RERUN_SUFFIX = "-rr"
TIMED_OUT = "timed out"      # the head of a terminated pass's crash reason


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
    notes = notes_text(notes_path, spec.notes.max_tokens, spec.notes.chars_per_token)
    return round_tasks(plan, requests, regime, spec.drift.constraint, decisions, lenses, notes)


def round_limits(spec: RunSpec) -> tuple[int, int]:
    """(`run.max_parallel_passes`, `run.transport_reruns`): the `parallel` and `reruns` of `run_round`."""
    return spec.run.max_parallel_passes, spec.run.transport_reruns


def pass_timeout(spec: RunSpec) -> float:
    """`run.pass_timeout_s`: the `pass_timeout_s` of `run_round`, past which a pass process is terminated."""
    return spec.run.pass_timeout_s


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
    chars_per_token: int                   # notes.chars_per_token: the token proxy of `Pass.composed_tokens`
    critic_viewport: Viewport | None = None  # the critic's screenshot (SPEC § 17: 1280x800, R1's first viewport)

    @classmethod
    def from_spec(cls, spec: RunSpec) -> PassSettings:
        """The pass settings `spec` gives."""
        return cls(
            repair_turns=spec.worker.repair_turns,
            max_artifact_bytes=spec.worker.max_artifact_bytes,
            viewports={Regime(k): tuple(v[0]) for k, v in spec.eval.viewports.items()},
            chars_per_token=spec.notes.chars_per_token,
            critic_viewport=tuple(spec.eval.viewports["R1"][0]),
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
    telemetry: Telemetry          # the process's one log, shared with the client, evaluator, judge and browser
    missing_ids: Callable[[Page, Collection[str]], list[str]] = page_missing_ids
    critic: Any = None            # roles.critic.Critic on the critic run (SPEC § 17); None: no critic turn
    taste: Any = None             # app.taste.Taste on the critic run (SPEC § 17 Taste); None: no planted quirks


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


_ARTIFACT_FILE = re.compile(r"""[^\s"'()<>]*artifacts/[^/\s"'()<>]+/index\.html""")
_ARTIFACT_DIR = re.compile(r"""[^\s"'()<>]*artifacts/[^/\s"'()<>]+/?""")


def neutral_location(text: str, artifact: Path, ids: Sequence[str]) -> str:
    """`text` with every trace of where the artifact lives replaced by `index.html`.

    A console error's stack and a load failure name the file's URL or path, and the path carries the pass id
    (round, request, arm, pair slot, re-run marker) and the home directory. The repair turn must read the same
    for both workers of a pair (SPEC § 2 Blinding), so the file URL, the path, any `artifacts/<pass_id>/`
    fragment and the pass ids in `ids` all read as the page's file name.
    """
    forms = {str(artifact), str(artifact.absolute()), str(artifact.resolve()),
             artifact.absolute().as_uri(), artifact.resolve().as_uri()}
    for form in sorted(forms, key=len, reverse=True):
        text = text.replace(form, "index.html")
    text = _ARTIFACT_FILE.sub("index.html", text)
    text = _ARTIFACT_DIR.sub("", text)
    for i in sorted(ids, key=len, reverse=True):
        text = text.replace(i, "index.html")
    return text


def _turns(env: PassEnv, task: PassTask, messages: list[Message], artifact: Path, results: list[ChatResult],
           ) -> tuple[str, bool, bool]:
    """Builds the artifact: the first turn, then up to `repair_turns` self-repair turns.

    Appends each chat result to `results` as it arrives (so a transport failure keeps the turns before it).
    Returns the final HTML, whether a repair turn ran, and whether the first reply held an HTML document.
    A reply with no document is written as the worker sent it: it is what the worker built.
    A repair reply with no document leaves the previous file in place.
    """
    t = env.telemetry
    with t.span("pass.worker") as span:
        first = env.worker.first_turn(messages, tag=task.pass_id)
        results.append(first)
        span.set(model=first.model, tokens_in=first.tokens_in, tokens_out=first.tokens_out, cost_usd=first.cost_usd)
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
        ids = [i for i in (task.pass_id, task.rerun_of) if i]
        errors = [neutral_location(e, artifact, ids) for e in errors]
        msg = repair_message(env.book, errors, [i.lstrip("#") for i in missing])
        with t.span("pass.repair", turn=turn + 1, console_errors=len(errors), missing_ids=len(missing)) as span:
            reply = env.worker.repair_turn(convo, last, msg, tag=f"{task.pass_id}:{turn + 1}")
            results.append(reply)
            span.set(model=reply.model, tokens_in=reply.tokens_in, tokens_out=reply.tokens_out,
                     cost_usd=reply.cost_usd, extracted=extract_html(reply.text) is not None)
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
    """The loaded page's console errors and missing contract ids: what the self-repair turn shows.

    A page that does not load gives the load error alone: its ids were never read, so none is reported missing.
    """
    with ExitStack() as stack:
        try:
            page = stack.enter_context(env.browser.page(str(artifact), viewport))
        except Exception as e:  # noqa: BLE001 - a page that does not load is the worker's defect; it earns the turn
            return [f"the page did not load: {type(e).__name__}: {e}"], []
        return list(page.console_errors()), list(env.missing_ids(page, contract))


def _meta(task: PassTask, results: Sequence[ChatResult], **extra: Any) -> dict[str, Any]:
    return {
        "pass_id": task.pass_id, "round": task.round_, "category": task.request.id, "arm": task.arm.value,
        "slot": task.slot, "rerun_of": task.rerun_of,
        "turns": [{"model": r.model, "tokens_in": r.tokens_in, "tokens_out": r.tokens_out, "cost_usd": r.cost_usd,
                   "sent": r.raw.get("sent")} for r in results],
        **extra,
    }


@contextmanager
def narrowed(telemetry: Telemetry, *, round_: int, pass_id: str | None = None) -> Iterator[Telemetry]:
    """`telemetry` itself, its scope narrowed to `round_` (and `pass_id`) until the block exits.

    The process holds one log, and its collaborators (the model client, the browser) hold that same object, so
    narrowing the object, not a copy, puts every row they write in the block under the block's scope.
    """
    prior = telemetry.scope
    telemetry.scope = prior.at(round=round_, pass_id=pass_id)
    try:
        yield telemetry
    finally:
        telemetry.scope = prior


def _pass_attrs(task: PassTask) -> dict[str, Any]:
    c = task.composition
    return {
        "request_id": task.request.id, "cell": list(task.request.cell) if task.request.cell else None,
        "arm": task.arm.value, "regime": task.regime.value, "pair_slot": task.slot, "exposed": c.exposed,
        "ablated": c.ablated, "composed": list(c.decision_ids), "notes": c.notes, "rerun_of": task.rerun_of,
        "process_pid": os.getpid(),
    }


def composed_tokens(book: PromptBook, task: PassTask, messages: Sequence[Message], chars_per_token: int) -> int:
    """Tokens composed into the worker prompt: the user message's length beyond the bare request's, in
    `notes.chars_per_token` characters per token (the decisions block and the notes block with their headings)."""
    bare = render_composition(book, task.request_text, (), None)
    extra = len(messages[-1].content) - len(bare[-1].content)
    if extra < 0:
        raise ValueError(f"{task.pass_id}: the composed prompt is shorter than the bare request")
    return approx_tokens("x" * extra, chars_per_token)


def run_pass(task: PassTask, env: PassEnv) -> PassOutcome:
    """Runs one pass inside its `pass` span (the process's log narrowed to the pass); see `_run_pass`."""
    with narrowed(env.telemetry, round_=task.round_, pass_id=task.pass_id) as t, \
            t.span("pass", **_pass_attrs(task)) as span:
        outcome = _run_pass(task, env)
        span.set(worker_model=outcome.pass_.worker)
        return outcome


def _run_pass(task: PassTask, env: PassEnv) -> PassOutcome:
    """Runs one pass and writes its own rows and `artifacts/<pass_id>/`.

    The size bound: an artifact over `worker.max_artifact_bytes` is kept whole, recorded `oversize`, and
    evaluated like any other. Truncating it would score a file the worker never wrote (and cut its how-to and
    closing tags); dropping it would lose a pass with a loss, which SPEC § 5 values over format. Both workers
    of a pair are under the same rule, so the delta stays paired.

    A transport failure in a worker turn or the eval records the pass `transport_failed`, with no eval: it
    gives no delta and is offered for one re-run. A transport failure in a judge call drops that call, like a
    call dropped after its re-ask: the eval stands and the fires stay `undisposed`.

    The store rows (pass, eval, lens answers, fires) are written together once the pass's outcome is final.
    Any other failure (a rubric answer unread after its re-ask, a judge reply that breaks its contract, a
    programming error) writes no store row, records the reason in `pass.json` and raises: `run_round` records
    the pass as crashed and writes its pass row with no eval, so the store never holds an eval of a pass
    the round lost.
    """
    out_dir = env.artifacts / task.pass_id
    out_dir.mkdir(parents=True, exist_ok=False)
    artifact = out_dir / "index.html"
    t = env.telemetry
    with t.span("pass.compose") as span:
        messages = render_composition(env.book, task.request_text, task.decisions,
                                      task.notes if task.composition.notes else None)
        tokens = composed_tokens(env.book, task, messages, env.settings.chars_per_token)
        prompt = "\n".join(m.content for m in messages)
        span.set(prompt_sha=hashlib.sha256(prompt.encode()).hexdigest(),
                 prompt_tokens_est=approx_tokens(prompt, env.settings.chars_per_token), composed_tokens=tokens)
    results: list[ChatResult] = []
    base = Pass(task.pass_id, task.round_, task.request.id, task.arm, "", task.composition, task.regime,
                pair_slot=task.slot, composed_tokens=tokens)
    if env.taste is not None and env.critic is None:
        raise ValueError("the taste is read on the critic run only: a PassEnv with a taste needs a critic")
    quirks = env.taste.in_scope(task.request.id) if env.taste is not None else ()
    eval_request = env.taste.scoped_request(task.request, task.regime, quirks) if quirks else task.request
    try:
        try:
            html, repaired, extracted = _turns(env, task, messages, artifact, results)
            reaction: Criticism | None = None
            if env.critic is not None:
                html, reaction = _critic_turns(env, task, messages, artifact, html, results,
                                               tuple(q.preference for q in quirks))
            size = len(html.encode())
            oversize = size > env.settings.max_artifact_bytes
            t.metric("pass.artifact_bytes", size, "bytes", oversize=oversize)
            pass_ = dataclasses.replace(base, worker=results[0].model, artifact_path=str(artifact),
                                        repaired=repaired)
            report = env.evaluate(pass_, eval_request, task.regime, task.request_text)
        except env.transport_errors as e:
            failed = dataclasses.replace(
                base, worker=results[0].model if results else "",
                artifact_path=str(artifact) if artifact.exists() else None, transport_failed=True,
                failed=f"{type(e).__name__}: {e}"[:500],
            )
            env.passes.add_pass(failed)
            (out_dir / "pass.json").write_text(json.dumps(_meta(task, results, transport_error=str(e)), indent=1))
            t.event("pass.outcome", outcome="transport_failed", error=failed.failed)
            return PassOutcome(failed, None, transport_error=f"{type(e).__name__}: {e}")
        if report.pass_id != pass_.id:
            raise ValueError(f"eval of {report.pass_id} returned for {pass_.id}")
        verdict = _judge(env, task, pass_, html, report) if task.arm is Arm.JOURNAL else _Judged((), None, (), [])
    except Exception as e:  # noqa: BLE001 - records the reason in pass.json and re-raises; nothing is defaulted
        (out_dir / "pass.json").write_text(json.dumps(_meta(task, results, failed=f"{type(e).__name__}: {e}"),
                                                      indent=1))
        raise
    if env.taste is not None:
        taste = env.taste.reading(report, quirks)
        (out_dir / TASTE_FILE).write_text(json.dumps({
            "quirks": [dataclasses.asdict(r) for r in taste.quirks], "loss": taste.loss,
            "loss_base": taste.loss_base, "C_base": taste.C_base}, indent=1))
        t.metric("pass.loss_base", taste.loss_base, "score", quirks=len(quirks))
        for r in taste.quirks:
            t.metric("pass.quirk_check", r.rate, "share", quirk=r.quirk, passed=r.passed)
    if reaction is not None:
        honored = requests_honored(html, report, reaction.requests)
        (out_dir / HONORED_FILE).write_text(json.dumps(honored, indent=1))
        if honored["share"] is not None:
            t.metric("pass.requests_honored", honored["share"], "share", requests=honored["of"])
    env.passes.add_pass(pass_)
    env.passes.add_eval(report)
    if verdict.answers is not None:
        env.judge_store.add_lens_answers(verdict.answers)
    if verdict.fires:
        env.judge_store.add_fires(verdict.fires)
    (out_dir / "eval.json").write_text(json.dumps(dataclasses.asdict(report), indent=1, default=str))
    (out_dir / "pass.json").write_text(json.dumps(_meta(
        task, results, bytes=size, oversize=oversize, extracted=extracted, repaired=repaired,
        dropped_calls=verdict.dropped,
    ), indent=1))
    for name, value in (("pass.loss", report.loss), ("pass.A", report.A), ("pass.R", report.R), ("pass.P", report.P),
                        ("pass.H", report.H), ("pass.C", report.C), ("pass.Q", report.Q)):
        t.metric(name, value, "score")
    t.event("pass.outcome", outcome="scored", error="", dropped_calls=list(verdict.dropped))
    return PassOutcome(pass_, report, verdict.observations, size, oversize, tuple(verdict.dropped))


# ---------------------------------------------------------------------------------------------------------
# The critic run's turns (SPEC § 17): build, critic, revision


CRITIC_FILE = "critic.json"          # artifacts/<pass_id>/: the critic's reply and the revision's outcome
HONORED_FILE = "requests_honored.json"
TASTE_FILE = "taste.json"            # the in-scope quirks' check results and loss_base (app.taste.TASTE_FILE)
BUILT_FILE = "built.html"            # the artifact as built, before the revision replaced it
SCREENSHOT_FILE = "critic.png"


def _critic_screenshot(env: PassEnv, artifact: Path) -> bytes | None:
    """One screenshot of the loaded page at the critic's viewport; None when the page does not load."""
    viewport = env.settings.critic_viewport
    if viewport is None:
        raise ValueError("PassSettings.critic_viewport is unset on a critic run")
    with ExitStack() as stack:
        try:
            page = stack.enter_context(env.browser.page(str(artifact), viewport))
        except Exception:  # noqa: BLE001 - a page that does not load gives the critic no screenshot; it reads the source
            return None
        return page.screenshot() or None


def _critic_turns(env: PassEnv, task: PassTask, messages: Sequence[Message], artifact: Path, html: str,
                  results: list[ChatResult], habits: Sequence[str] = ()) -> tuple[str, Criticism]:
    """The critic call on this pass's own artifact, then the worker's one revision turn.

    The critic reads the request text, the built source (its how-to inside) and a screenshot taken after load;
    no eval has run yet, so no reading of it exists to leak. `habits` are the manager's quirks in scope for the
    request (SPEC § 17 Taste), in the user's words; the critic alone reads them. The revised file replaces
    `index.html` (the built one is kept as `built.html`); a revision reply with no document leaves the built file
    in place. Returns the HTML now in `index.html` and the critic's reply. Writes `critic.json`.
    """
    t = env.telemetry
    out_dir = artifact.parent
    (out_dir / BUILT_FILE).write_text(html)
    shot = _critic_screenshot(env, artifact)
    if shot is not None:
        (out_dir / SCREENSHOT_FILE).write_bytes(shot)
    with t.span("pass.critic", screenshot=shot is not None, habits=len(habits)) as span:
        reply = env.critic.criticise(task.request_text, html, shot, tag=f"{task.pass_id}:critic", habits=habits)
        results.extend(reply.results)
        last = reply.results[-1]
        span.set(model=last.model, requests=len(reply.criticism.requests),
                 tokens_in=sum(r.tokens_in for r in reply.results), tokens_out=sum(r.tokens_out for r in reply.results),
                 cost_usd=sum(r.cost_usd or 0.0 for r in reply.results))
    msg = revision_message(env.book, task.request_text, reply.criticism)
    convo = [*messages, Message("assistant", html), msg]
    with t.span("pass.revision") as span:
        revised = env.worker.client.chat("worker", convo, tag=f"{task.pass_id}:revision")
        results.append(revised)
        found = extract_html(revised.text)
        span.set(model=revised.model, tokens_in=revised.tokens_in, tokens_out=revised.tokens_out,
                 cost_usd=revised.cost_usd, extracted=found is not None)
    if found is not None:
        html = found
        artifact.write_text(html)
    (out_dir / CRITIC_FILE).write_text(json.dumps({
        "critic_model": last.model, "criticism": reply.criticism.criticism,
        "requests": list(reply.criticism.requests), "screenshot": shot is not None,
        "critic_attempts": reply.asked.attempts, "critic_failures": list(reply.asked.failures),
        "revision_model": revised.model, "revised": found is not None,
    }, indent=1))
    return html, reply.criticism


def declared_descriptions(html: str) -> dict[int, str]:
    """Each how-to entry's position -> its `does` and its `id`, read at the rung `how_to.parse` reads it.

    The position is the `n` of the declared control's check id `how-to.{n}:#id`."""
    text = how_to.find_json(html)
    if text is None:
        return {}
    entries: list[Any] | None = None
    for read in (how_to._strict, how_to._repaired):          # the same rungs, in the same order, as how_to.parse
        value = read(text)
        if how_to._shaped(value):
            entries = value["controls"]
            break
    if entries is None:
        entries = how_to._salvage(text)
    out: dict[int, str] = {}
    for n, e in enumerate(entries):
        if isinstance(e, dict):
            does = e.get("does") if isinstance(e.get("does"), str) else ""
            ident = e.get("id") if isinstance(e.get("id"), str) else ""
            out[n] = f"{does} {re.sub(r'[#_-]+', ' ', ident)}".strip()
    return out


_HOW_TO_CHECK = re.compile(r"how-to\.(\d+):")


def requests_honored(html: str, report: EvalReport, requests: Sequence[str]) -> dict[str, Any]:
    """Per feature request: the revised how-to's declared control that matches its words, and whether the how-to
    check passed for that control at every viewport read. `share` is honored / requests (None with no request)."""
    descriptions = declared_descriptions(html)
    declared: list[tuple[str, str]] = []
    for reading in report.how_to[:1]:                       # every viewport parses the same file the same way
        for check in reading.parse.declared:
            m = _HOW_TO_CHECK.match(check.id)
            if m is not None and int(m.group(1)) in descriptions:
                declared.append((check.id, descriptions[int(m.group(1))]))
    rows = []
    for r in requests:
        match = match_request(r, declared)
        verdicts = [res.passed for reading in report.how_to for res in reading.results if res.check_id == match.control]
        passed = bool(verdicts) and all(verdicts)
        rows.append({"request": r, "control": match.control, "share": match.share,
                     "how_to_passed": passed if match.control else None,
                     "honored": match.control is not None and passed})
    honored = sum(row["honored"] for row in rows)
    return {"requests": rows, "honored": honored, "of": len(rows), "share": honored / len(rows) if rows else None}


@dataclass(frozen=True)
class _Judged:
    """The two judge calls' results, held until the pass writes its rows."""

    observations: tuple[PendingObservation, ...]
    answers: Sequence[Any] | None          # LensAnswers; None when the lens call was dropped
    fires: Sequence[Fire]
    dropped: list[str]


def _judge(env: PassEnv, task: PassTask, pass_: Pass, html: str, report: EvalReport) -> _Judged:
    """The lens call (artifact only), then the report call (adds the eval report); returns answers and fires.

    Raises when the report call breaks its contract (a composed decision with no disposition, or one
    returned `undisposed`). Writes nothing: the pass writes every row once its outcome is final.
    """
    composed = {d.id: d.payload for d in sorted(task.decisions, key=lambda d: d.id)}
    lens_ids = [x.id for x in task.lenses]
    dropped: list[str] = []
    try:
        answers = env.judge.lens_call(task.pass_id, task.request_text, composed, html, task.lenses)
    except env.transport_errors:
        answers = None
    if answers is None:
        dropped.append("lens")
    try:
        result = env.judge.report_call(task.pass_id, task.request_text, composed, html, answers or [],
                                       eval_report_text(report), lens_ids)
    except env.transport_errors:
        result = None
    if result is None:
        dropped.append("report")
        return _Judged((), answers, [Fire(pass_.id, i, task.round_) for i in composed], dropped)
    drafts, dispositions = result
    missing = set(composed) - set(dispositions)
    if missing:
        raise ValueError(f"{pass_.id}: report call returned no disposition for {sorted(missing)}")
    fires = [Fire(pass_.id, i, task.round_, dispositions[i][0], dispositions[i][1]) for i in composed]
    if any(f.disposition is Disposition.UNDISPOSED for f in fires):
        raise ValueError(f"{pass_.id}: a returned disposition is undisposed")
    observations = tuple(PendingObservation(pass_.id, task.round_, task.request.id, d) for d in drafts)
    return _Judged(observations, answers, fires, dropped)


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
    transport_failed: tuple[str, ...] = ()   # last attempt of each transport-failed slot that never evaluated:
                                             # failed for transport again, or its re-run crashed


class RoundCrashed(RuntimeError):
    """Every pass of a round crashed: the round gives no population, which is a fault, not an outcome."""


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


def _run_processes(tasks: Sequence[PassTask], env_factory: EnvFactory, parallel: int, timeout_s: float | None,
                   ) -> tuple[list[PassOutcome], dict[str, str]]:
    """Runs `tasks` at most `parallel` at a time, one process each; a pass past `timeout_s` is terminated."""
    ctx = multiprocessing.get_context("spawn")       # no browser or connection is inherited by a child
    pending = list(tasks)
    running: dict[Any, tuple[Any, PassTask, float]] = {}
    outcomes: list[PassOutcome] = []
    crashed: dict[str, str] = {}
    while pending or running:
        while pending and len(running) < parallel:
            task = pending.pop(0)
            recv, send = ctx.Pipe(duplex=False)
            proc = ctx.Process(target=_child, args=(env_factory, task, send), name=task.pass_id)
            proc.start()
            send.close()
            running[recv] = (proc, task, time.monotonic())
        timeout = None
        if timeout_s is not None:
            timeout = max(0.0, min(t0 for _, _, t0 in running.values()) + timeout_s - time.monotonic())
        for conn in wait(list(running), timeout):
            proc, task, _ = running.pop(conn)
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
        if timeout_s is not None:
            now = time.monotonic()
            for conn in [c for c, (_, _, t0) in running.items() if now - t0 >= timeout_s]:
                proc, task, _ = running.pop(conn)
                proc.terminate()
                proc.join()
                conn.close()
                crashed[task.pass_id] = f"{TIMED_OUT} after {timeout_s} s (run.pass_timeout_s); terminated"
    return outcomes, crashed


def _run_in_process(tasks: Sequence[PassTask], env: PassEnv) -> tuple[list[PassOutcome], dict[str, str]]:
    outcomes: list[PassOutcome] = []
    crashed: dict[str, str] = {}
    for task in tasks:
        try:
            outcomes.append(run_pass(task, env))
        except Exception:  # noqa: BLE001 - same rule as a pass's process: a crash loses only that pass
            crashed[task.pass_id] = traceback.format_exc()
    return outcomes, crashed


def _reason(trace: str) -> str:
    """The crash reason a pass row and `pass.outcome` carry: the traceback's last non-empty line, bounded."""
    lines = [ln for ln in trace.strip().splitlines() if ln.strip()]
    return (lines[-1] if lines else "crashed")[:500]


def _record_outcomes(telemetry: Telemetry, round_: int, crashed: Mapping[str, str]) -> None:
    """Writes `pass.outcome` for each pass that crashed, died or was terminated: its process wrote none."""
    for pid in sorted(crashed):
        trace = crashed[pid]
        outcome = "timed_out" if trace.startswith(TIMED_OUT) else "crashed"
        telemetry.scoped(round=round_, pass_id=pid).event("pass.outcome", outcome=outcome, error=_reason(trace))


def _record_crashed(env: PassEnv, round_: int, tasks: Mapping[str, PassTask], crashed: Mapping[str, str]) -> None:
    """Writes the pass row, with no eval, of each crashed pass that has none: the store keeps the population.

    The row reads as a pass that never evaluated (its eval is unread); the reason is in `RoundResult.crashed`
    and, when the pass got that far, in its `pass.json`.
    """
    have = {p.id for p in env.passes.passes(round_)}
    for pid in sorted(crashed):
        if pid in have:
            continue
        t = tasks[pid]
        artifact = env.artifacts / pid / "index.html"
        messages = render_composition(env.book, t.request_text, t.decisions, t.notes if t.composition.notes else None)
        env.passes.add_pass(Pass(pid, t.round_, t.request.id, t.arm, "", t.composition, t.regime,
                                 artifact_path=str(artifact) if artifact.exists() else None, pair_slot=t.slot,
                                 failed=_reason(crashed[pid]),
                                 composed_tokens=composed_tokens(env.book, t, messages, env.settings.chars_per_token)))


def run_round(
    round_: int, plans: Sequence[PairPlan], tasks: Sequence[PassTask], env_factory: EnvFactory, *,
    parallel: int, reruns: int, telemetry: Telemetry, in_process: bool = False, pass_timeout_s: float | None = None,
) -> RoundResult:
    """Runs every pass, re-runs each transport failure up to `reruns` times, and computes the paired deltas.

    `parallel` is `run.max_parallel_passes`: at most that many passes run at once, each in its own process
    with its own browser and store connections (`env_factory` opens them in the child). `pass_timeout_s`
    bounds each pass process; a pass past it is terminated and recorded as crashed (`None`: no bound; not
    enforced with `in_process`). `in_process` runs the passes one after another in this process with one
    environment (tests and dry runs). `telemetry` is the parent's log: it receives the `pass.outcome` of every
    pass that crashed, died or was terminated (a pass that ran to its end writes its own).

    A crashed pass is reported in `crashed`, gives no delta, is not re-run, and gets a pass row with no
    eval. Raises `RoundCrashed` when every first-attempt pass crashed. Raises before any pass runs when an
    `artifacts/<pass_id>/` of the round already exists: a round is entered once, and a restarted runner
    resumes at the round close, never by re-running passes over their own rows. Writes no Ledger row.
    """
    if parallel < 1:
        raise ValueError("run.max_parallel_passes must be at least 1")
    if pass_timeout_s is not None and pass_timeout_s <= 0:
        raise ValueError("run.pass_timeout_s must be positive")
    if any(t.round_ != round_ for t in tasks):
        raise ValueError("a task belongs to another round")
    if len({t.pass_id for t in tasks}) != len(tasks):
        raise ValueError("pass ids repeat within the round")

    with env_factory() as home:
        entered = sorted(t.pass_id for t in tasks if (home.artifacts / t.pass_id).exists())
        if entered:
            raise ValueError(f"round {round_} was entered before: artifacts of {entered} exist")

        def run(batch: Sequence[PassTask]) -> tuple[list[PassOutcome], dict[str, str]]:
            if in_process:
                return _run_in_process(batch, home)
            return _run_processes(batch, env_factory, parallel, pass_timeout_s)

        outcomes, crashed = run(tasks)
        _record_outcomes(telemetry, round_, crashed)
        task_of = {t.pass_id: t for t in tasks}
        failed = [o.pass_.id for o in outcomes if o.pass_.transport_failed]
        lost: list[str] = []                 # re-runs that crashed: their slot is still without an eval
        for attempt in range(1, reruns + 1):
            if not failed:
                break
            retry = [rerun_task(task_of[i], attempt) for i in failed]
            task_of.update((t.pass_id, t) for t in retry)
            more, more_crashed = run(retry)
            _record_outcomes(telemetry, round_, more_crashed)
            outcomes += more
            crashed.update(more_crashed)
            lost += [t.pass_id for t in retry if t.pass_id in more_crashed]
            failed = [o.pass_.id for o in more if o.pass_.transport_failed]
        _record_crashed(home, round_, task_of, crashed)
    if tasks and all(t.pass_id in crashed for t in tasks):
        raise RoundCrashed(f"round {round_}: every pass crashed; first: {crashed[tasks[0].pass_id]}")
    passes = [o.pass_ for o in outcomes]
    evals = {o.pass_.id: o.report for o in outcomes if o.report is not None}
    deltas = pair_deltas(round_, plans, passes, evals)
    observations = tuple(ob for o in sorted(outcomes, key=lambda o: o.pass_.id) for ob in o.observations)
    return RoundResult(tuple(outcomes), tuple(deltas), observations, crashed, tuple(sorted(failed + lost)))
