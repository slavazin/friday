"""Writes the showcase fixtures: a 3-round run with a drift round, the demo page renders from before a real run exists.

Outputs, under the target folder (default: this folder):
- `run.yml`: the repository run spec with the fixture's overrides (3 rounds, drift at round 3, small windows);
- `export.json`: `friday.app.export.build` over rows planted in the in-memory stores, written by `dumps`;
- `telemetry.db`: the telemetry log written through `SqliteTelemetry`, with span timings for every pass step
  and every round-close phase;
- `artifacts/<pass_id>/index.html`: one small built app per pass that built one.

Deterministic: every draw goes through `friday.stats.rng.derive` under the fixture seed, the clock is simulated,
and span ids come from a seeded counter. Run: `uv run python -m web.fixtures.make [--out DIR]`.

The stores also hold a planted prompt, model reply and key (`PROMPT_SENTINEL`, `REPLY_SENTINEL`,
`KEY_SENTINEL`), so a test can show none of them reaches the page's API.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import tempfile
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from friday.adapters.git import RecordingVcs
from friday.adapters.lens_md import MarkdownLensSet
from friday.adapters.memory import (
    MemoryEventLog,
    MemoryGoalSource,
    MemoryJournal,
    MemoryJudgeStore,
    MemoryLedger,
    MemoryObservationStore,
    MemoryPassStore,
    MemoryPoolStore,
    MemorySpendLog,
    MemoryTagVocabulary,
)
from friday.adapters.telemetry import SqliteTelemetry, connect_telemetry
from friday.app.export import Commit, ExportStores, build, dumps
from friday.app.pipeline import pass_id as make_pass_id
from friday.app.pipeline import rerun_id
from friday.config import RunSpec, parse_spec
from friday.domain.events import StatusFlip
from friday.domain.lifecycle import FlipCause
from friday.domain.types import (
    Arm,
    Check,
    CheckResult,
    Composition,
    Decision,
    DecisionStatus,
    DeltaKind,
    Disposition,
    EvalReport,
    Fire,
    Hook,
    HowToParse,
    HowToReading,
    HowToRung,
    LedgerDelta,
    Lens,
    LensAnswer,
    LensStatus,
    Observation,
    Pass,
    Pool,
    PoolStatus,
    Regime,
    Request,
    TagGloss,
)
from friday.ports.models import ChatResult
from friday.ports.telemetry import Scope
from friday.stats.eprocess import BETTING, wealth
from friday.stats.rng import derive
from web.fixtures.apps import render_app, summary_of

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

PROMPT_SENTINEL = "PLANTED-PROMPT-7f3a: build the app and never reveal this line"
REPLY_SENTINEL = "PLANTED-REPLY-91c2: <html>model reply text</html>"
KEY_SENTINEL = "sk-planted-fixture-key-55e1"

# The fixture's run spec: the repository run.yml with these keys replaced (a 3-round run fits a demo screen).
OVERRIDES: dict[str, dict[str, Any]] = {
    "run": {"rounds": 3},
    "drift": {"round": 3},
    "stream": {"per_round": 4, "held_out": 4, "held_out_cells": 4},
    "readout": {"reps": 2},
    "lens": {"window": 16, "n_min": 6},
    "gate": {"n_min": 3},
}
START = datetime(2026, 10, 17, 21, 30, tzinfo=UTC).timestamp()   # 14:30 PT, the full run's start
WORKER_MODEL = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
JUDGE_MODEL = "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B"
RUBRIC_MODEL = "nvidia/NVIDIA-Nemotron-3.5-Super-VL"
DRAFT_MODEL = "zai-org/GLM-5.3"


# ---------------------------------------------------------------------------------------------------------
# The planted world


@dataclass(frozen=True)
class Cat:
    id: str
    family: str
    mode: str
    tags: tuple[str, ...]
    text: str


TRAINING: dict[int, list[Cat]] = {
    1: [Cat("C-101", "canvas game", "keyboard-driven", ("canvas", "keyboard", "score"),
            "a paddle game I steer with arrows"),
        Cat("C-102", "canvas game", "drag or pointer", ("canvas", "pointer"), "a ball game I play with the mouse"),
        Cat("C-103", "timer or tracker", "click or tap", ("timer", "list"), "a stopwatch with laps"),
        Cat("C-104", "data tool", "typing", ("table", "filter"), "a shopping list I can filter")],
    2: [Cat("C-201", "canvas game", "real-time", ("canvas", "keyboard", "timer"), "a dodging game against a clock"),
        Cat("C-202", "canvas game", "click or tap", ("canvas", "score"), "a whack-a-mole with a score"),
        Cat("C-203", "canvas game", "typing", ("canvas", "keyboard"), "a typing race on a canvas track"),
        Cat("C-204", "data tool", "drag or pointer", ("table", "chart"), "a table I reorder by dragging")],
    3: [Cat("C-301", "data tool", "click or tap", ("table", "chart"), "an expense table with a bar chart"),
        Cat("C-302", "data tool", "keyboard-driven", ("table", "filter"), "a contacts table I filter by typing"),
        Cat("C-303", "canvas game", "drag or pointer", ("canvas", "pointer"), "a slingshot game I aim by dragging"),
        Cat("C-304", "timer or tracker", "typing", ("timer", "list"), "a habit tracker with daily timers")],
}
HELD_OUT = [Cat("H-01", "form or calculator", "real-time", ("form", "table"), "a loan calculator with a payment table"),
            Cat("H-02", "text tool", "drag or pointer", ("text", "pointer"), "a text editor I rearrange by dragging"),
            Cat("H-03", "data tool", "real-time", ("table", "chart", "timer"), "a live chart of a ticking counter"),
            Cat("H-04", "canvas game", "typing", ("canvas", "keyboard", "text"), "a word game typed onto a canvas")]

TAG_GLOSSES = {"canvas": "draws on a canvas element", "keyboard": "operated from the keyboard",
               "score": "keeps a score", "pointer": "operated by mouse or touch", "timer": "measures time",
               "list": "keeps a list of entries", "table": "shows rows in a table", "filter": "narrows a list",
               "chart": "draws a chart", "form": "takes typed inputs", "text": "edits text"}
TAG_MINTED = {"canvas": 1, "keyboard": 1, "score": 1, "pointer": 1, "timer": 1, "list": 1, "table": 1,
              "filter": 1, "chart": 2, "form": 3, "text": 3}

DECISIONS = {
    "D-0001": dict(pool="P-0001", fires=("canvas",), not_this=(), drafted=1,
                   payload="Size the canvas from its container's width on load and on resize, and redraw at that "
                           "size. Place every control outside the canvas and label it with its key."),
    "D-0002": dict(pool="P-0002", fires=("table",), not_this=(), drafted=1,
                   payload="When the table holds no rows, show one row saying what to add first. Remove that row "
                           "on the first insert and keep the header visible."),
    "D-0003": dict(pool="P-0003", fires=("canvas", "pointer"), not_this=("keyboard",), drafted=3,
                   payload="Draw the canvas at the full viewport width up to 640 px. Scale pointer coordinates by "
                           "the canvas's displayed size before hit-testing."),
}

BASELINE_LENSES = [
    ("L-01", "Which stated requirement does the code not implement? Quote the requirement's nearest code.",
     "Which requirement is implemented in a form the request did not ask for?"),
    ("L-02", "Which contract id is missing, misspelled or bound to the wrong element?",
     "Which present id is wired to nothing a user can trigger?"),
    ("L-03", "Where does an input event fail to reach the handler meant for it?",
     "Which handler is reached but changes no visible state?"),
    ("L-04", "Where does state fail to reset, restart or persist as the request implies?",
     "Which persistence did the request never ask for?"),
    ("L-05", "Which code path throws on load or on first interaction? Name the line.",
     "Which suspicious line never runs?"),
    ("L-06", "Which composed decision does the artifact contradict?",
     "Which composed decision does not apply to this request at all?"),
    ("L-07", "Where is the output truncated, stubbed or left as a placeholder?",
     "Where did the worker build beyond the request at the cost of a requested feature?"),
    ("L-08", "Which requested control is laid out invisible, overlapped or off-screen at 1280×800?",
     "Which hidden element is meant to be hidden?"),
]
MINTED_LENSES = [
    ("L-09", 1, "Where is a drawing sized in fixed pixels while its container can be narrower? Quote the size.",
     "Which fixed size is the request's own?"),
    ("L-10", 2, "Which table renders nothing at all when it holds no rows?",
     "Which empty table is meant to stay blank?"),
]
# How tightly each lens's answer tracks the pass's loss (1 = exactly, 0 = not at all).
LENS_SIGNAL = {"L-01": 0.8, "L-02": 0.5, "L-03": 0.7, "L-04": 0.68, "L-05": 0.3, "L-06": 0.6, "L-07": 0.2,
               "L-08": 0.55, "L-09": 0.9, "L-10": 0.4}

# Planted per-pass quality targets: (round, category) -> (quality without the decision, with it).
EXPOSURES = {(2, "C-201"): "D-0001", (2, "C-202"): "D-0001", (2, "C-203"): "D-0001", (2, "C-204"): "D-0002",
             (3, "C-301"): "D-0002", (3, "C-302"): "D-0002"}
ABLATIONS = {(3, "C-303"): "D-0001"}
# Judge dispositions planted as contradicted (besides D-0001 on every composed pass from the drift round on).
CONTRADICTED = {("D-0001", 2, "C-202"), ("D-0001", 2, "C-203")}
TRANSPORT_FAILED = {"r01-C-103-journal-1", "r02-C-204-notes-0", "r04-H-02-notes-1"}


@dataclass
class PassPlan:
    p: Pass
    cat: Cat
    quality: float
    report: EvalReport | None
    reran: bool = False
    composed_tokens: int = 0
    calls: list[tuple[str, str, int, int, float]] = field(default_factory=list)  # role, model, tin, tout, usd


def _disposition(decision_id: str, p: Pass) -> Disposition:
    """The judge's planted disposition of `decision_id` on pass `p`."""
    hit = (decision_id, p.round, p.category) in CONTRADICTED or (decision_id == "D-0001" and p.round >= 3)
    return Disposition.CONTRADICTED if hit else Disposition.APPLIED


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _report(spec: RunSpec, p: Pass, cat: Cat, quality: float, seed: int) -> EvalReport:
    """An EvalReport whose loss follows SPEC § 5 from planted P, H, C and R."""
    rng = derive(seed, "eval", p.id)
    P = round(_clip(quality + rng.uniform(-0.08, 0.1)), 4)
    H = round(_clip(quality + rng.uniform(-0.15, 0.08)), 4)
    C = round(_clip(quality + rng.uniform(-0.12, 0.12)), 4)
    R = round(_clip(quality + rng.uniform(-0.1, 0.1)), 4)
    w = spec.eval.a_weights
    A = w["probe"] * P + w["how_to"] * H + w["checks"] * C
    loss = round(1 - (spec.eval.w_a * A + spec.eval.w_r * R), 4)
    roll = rng.random()
    rung = HowToRung.STRICT if roll < 0.72 else HowToRung.REPAIRED if roll < 0.88 else \
        HowToRung.SALVAGED if roll < 0.95 else HowToRung.NONE
    contract = ("#start", "#score") if cat.family == "canvas game" else ("#add", "#rows")
    declared = () if rung is HowToRung.NONE else tuple(
        Check(f"how-to-{i}", (), {"exists": cid}) for i, cid in enumerate(contract))
    undeclared = contract if rung is HowToRung.NONE else (contract[1],) if rung is HowToRung.SALVAGED else ()
    parse = HowToParse(rung, declared, 0, tuple(c.expect["exists"] for c in declared), undeclared,
                       "" if rung is HowToRung.NONE else summary_of(cat.family))
    viewports = spec.eval.viewports[p.regime.value]
    readings = tuple(
        HowToReading(p.id, (int(v[0]), int(v[1])), parse,
                     tuple(CheckResult(c.id, rng.random() < quality + 0.15) for c in declared), H)
        for v in viewports)
    rubric = tuple(1 + round(4 * _clip(quality + rng.uniform(-0.2, 0.2))) for _ in range(spec.eval.rubric_questions))
    eslint, axe = rng.randrange(0, 4), rng.randrange(0, 3)
    return EvalReport(p.id, p.regime, (CheckResult("c1", C > 0.5),), (), round(A, 4), R, rubric, eslint + axe, loss,
                      eslint_errors=eslint, axe_violations=axe, P=P, H=H, C=C, how_to=readings,
                      silent_controls=rng.randrange(0, 3))


def _quality(seed: int, round_: int, cat: Cat, arm: Arm, composed: tuple[str, ...]) -> float:
    """The planted build quality of one pass: lower under R2, higher with a decision that fits."""
    rng = derive(seed, "quality", round_, cat.id, arm.value, composed)
    q = 0.55 + rng.uniform(-0.12, 0.12)
    if cat.family == "canvas game" and round_ < 3:
        q += 0.4 if "D-0001" in composed else -0.4                # unsized canvases fail at R1
    if cat.family == "canvas game" and round_ >= 3:
        q += -0.12 if "D-0001" in composed else 0.06              # under R2 the fixed-size rule hurts
    if cat.family == "data tool" and arm is not Arm.NOTES:
        q += 0.44 if "D-0002" in composed else -0.42
    if arm is Arm.NOTES:
        q -= 0.04
    if arm is Arm.EMPTY:
        q -= 0.08
    return _clip(q, 0.05, 0.97)


def _admitted_at(round_: int) -> set[str]:
    """Decisions composable (admitted or suspect) during `round_`."""
    out = set()
    if round_ >= 3:
        out.add("D-0001")
    if round_ >= 4:
        out.add("D-0002")
    return out


def plan_passes(spec: RunSpec, seed: int) -> list[PassPlan]:
    """Every training and readout pass of the fixture run, with its eval and its model calls."""
    plans: list[PassPlan] = []
    for r, cats in TRAINING.items():
        regime = Regime.R2 if r >= spec.drift.round else Regime.R1
        for cat in cats:
            exposed, ablated = EXPOSURES.get((r, cat.id)), ABLATIONS.get((r, cat.id))
            admitted = sorted(d for d in _admitted_at(r) if set(DECISIONS[d]["fires"]) & set(cat.tags))
            treated = derive(seed, "coin", r, cat.id).randrange(2)
            for slot in (0, 1):
                ids = list(admitted)
                if exposed and slot == treated:
                    ids.append(exposed)
                if ablated and slot != treated:
                    ids.remove(ablated)
                comp = Composition(cat.id, tuple(sorted(ids)), exposed=exposed if slot == treated else None,
                                   ablated=ablated if slot != treated else None)
                plans.append(_plan(spec, seed, r, cat, Arm.JOURNAL, slot, comp, regime))
            plans.append(_plan(spec, seed, r, cat, Arm.NOTES, 0, Composition(cat.id, (), notes=True), regime))
    readout_round = spec.run.rounds + 1
    for cat in HELD_OUT:
        s_star = sorted(d for d in _admitted_at(readout_round) if set(DECISIONS[d]["fires"]) & set(cat.tags))
        for arm in (Arm.EMPTY, Arm.NOTES, Arm.JOURNAL):
            for rep in range(spec.readout.reps):
                comp = Composition(cat.id, tuple(s_star) if arm is Arm.JOURNAL else (), notes=arm is Arm.NOTES)
                plans.append(_plan(spec, seed, readout_round, cat, arm, rep, comp, Regime(spec.readout.regime),
                                   readout=True))
    return plans


def _calls(p: Pass, tokens: int, rng: Any, readout: bool) -> list[tuple[str, str, int, int, float]]:
    """The model calls one pass makes: the build turn, the self-repair turn, the rubric, the judge's two calls."""
    calls = [("worker", WORKER_MODEL, 900 + tokens, 2600 + rng.randrange(1800), 0.0018 + rng.random() * 0.0012)]
    if p.transport_failed:
        return calls
    if p.repaired:
        calls.append(("worker", WORKER_MODEL, 3500, 2400, 0.0021))
    calls.append(("rubric", RUBRIC_MODEL, 1800, 120, 0.0035 + rng.random() * 0.001))
    if p.arm is Arm.JOURNAL and not readout:
        calls.append(("judge", JUDGE_MODEL, 5200, 600, 0.008 + rng.random() * 0.003))
        calls.append(("judge", JUDGE_MODEL, 6100, 700, 0.009 + rng.random() * 0.003))
    return calls


def _plan(spec: RunSpec, seed: int, r: int, cat: Cat, arm: Arm, slot: int, comp: Composition, regime: Regime,
          readout: bool = False, rerun_of: str | None = None) -> PassPlan:
    pid = rerun_id(rerun_of) if rerun_of else make_pass_id(r, cat.id, arm, slot)
    rng = derive(seed, "pass", pid)
    failed = pid in TRANSPORT_FAILED
    q = _quality(seed, r, cat, arm, comp.decision_ids)
    p = Pass(pid, r, cat.id, arm, WORKER_MODEL, comp, regime,
             artifact_path=None if failed else f"artifacts/{pid}/index.html",
             repaired=not failed and rng.random() < 0.3, transport_failed=failed,
             pair_slot=slot if arm is Arm.JOURNAL and not readout else None)
    plan = PassPlan(p, cat, q, None if failed else _report(spec, p, cat, q, seed))
    plan.composed_tokens = 60 * len(comp.decision_ids) + (2200 + rng.randrange(400) if comp.notes else 0)
    plan.calls = _calls(p, plan.composed_tokens, rng, readout)
    return plan


def reruns(plans: list[PassPlan], spec: RunSpec, seed: int) -> list[PassPlan]:
    """One re-run per pass failed for transport, same request, arm, slot and composition (SPEC § 11)."""
    out = []
    for pl in plans:
        if not pl.p.transport_failed:
            continue
        pl.reran = True
        p0 = pl.p
        readout = p0.round > spec.run.rounds
        out.append(_plan(spec, seed, p0.round, pl.cat, p0.arm, p0.pair_slot or int(p0.id[-1]), p0.composition,
                         p0.regime, readout=readout, rerun_of=p0.id))
    return out


@dataclass
class World:
    spec: RunSpec
    plans: list[PassPlan]
    stores: ExportStores
    journal: MemoryJournal
    lenses: MarkdownLensSet
    commits: list[Commit]
    flips_by_round: dict[int, list[StatusFlip]]
    close_calls: dict[int, list[tuple[str, str, int, int, float]]]
    lens_answers_by_round: dict[int, int]


def _scored_loss(plans: list[PassPlan]) -> dict[tuple[int, str, int], float]:
    """(round, category, slot) -> loss of the scored journal-arm pass in that slot (re-runs replace failures)."""
    out = {}
    for pl in plans:
        if pl.p.arm is Arm.JOURNAL and pl.report is not None and pl.p.pair_slot is not None:
            out[(pl.p.round, pl.p.category, pl.p.pair_slot)] = (pl.report.loss, pl.report.Q)
    return out


def _sha(seed: int, *keys: object) -> str:
    return hashlib.sha256("\x1f".join(map(str, (seed, *keys))).encode()).hexdigest()[:12]


def build_world(spec: RunSpec) -> World:
    """Plants the fixture run in the in-memory stores."""
    seed = spec.run.seed
    plans = plan_passes(spec, seed)
    plans += reruns(plans, spec, seed)
    passes, judge, spend = MemoryPassStore(), MemoryJudgeStore(), MemorySpendLog()
    composed_tokens: dict[str, int] = {}
    for pl in plans:
        passes.add_pass(pl.p)
        if pl.report is not None:
            passes.add_eval(pl.report)
        composed_tokens[pl.p.id] = pl.composed_tokens
        for role, model, tin, tout, usd in pl.calls:
            spend.record(role, pl.p.id, ChatResult(REPLY_SENTINEL, model, tin, tout, round(usd, 6)), True)
        if pl.p.transport_failed:
            spend.record("worker", pl.p.id, None, False, "transport: 503 after 5 tries")

    # Goals and tags
    requests = [Request(c.id, f"{PROMPT_SENTINEL}. Build {c.text}.", frozenset(c.tags), {"#start": "starts"},
                        {Regime.R1: (), Regime.R2: ()}, round=r) for r, cs in TRAINING.items() for c in cs]
    requests += [Request(c.id, f"{PROMPT_SENTINEL}. Build {c.text}.", frozenset(c.tags), {"#start": "starts",
                         "#add": "adds a row"}, {Regime.R1: (), Regime.R2: ()}, held_out=True) for c in HELD_OUT]
    tags = MemoryTagVocabulary()
    for t, gloss in TAG_GLOSSES.items():
        tags.add(TagGloss(t, gloss, TAG_MINTED[t]))
    tags.set_tags({c.id: frozenset(c.tags) for cs in [*TRAINING.values(), HELD_OUT] for c in cs})

    # Journal, Ledger and the event log
    journal, ledger, events = MemoryJournal(), MemoryLedger(), MemoryEventLog()
    for did, d in DECISIONS.items():
        journal.draft(Decision(did, DecisionStatus.CANDIDATE, d["pool"], Hook(frozenset(d["fires"]),
                               frozenset(d["not_this"])), d["payload"], drafted_round=d["drafted"]))
    losses = _scored_loss(plans)
    for (r, cat), did in sorted({**EXPOSURES, **ABLATIONS}.items()):
        treated = derive(seed, "coin", r, cat).randrange(2)
        (l_t, q_t), (l_u, q_u) = losses[(r, cat, treated)], losses[(r, cat, 1 - treated)]
        if (r, cat) in EXPOSURES:   # treated slot got the candidate: d = loss(without) - loss(with)
            ledger.append(LedgerDelta(did, r, cat, DeltaKind.EXPOSURE, round(l_u - l_t, 4), q_t - q_u))
        else:                        # treated slot kept the admitted decision, the other omitted it
            ledger.append(LedgerDelta(did, r, cat, DeltaKind.ABLATION, round(l_u - l_t, 4), q_t - q_u))
    betting = BETTING.build(spec.gate.betting.name, **spec.gate.betting.params)

    def w_of(did: str, upto: int) -> tuple[float, int, float]:
        ds = [x.d for x in ledger.deltas(did, DeltaKind.EXPOSURE) if x.round <= upto]
        return wealth(ds, betting), len(ds), sum(ds) / len(ds)

    flips: dict[int, list[StatusFlip]] = defaultdict(list)
    w1, n1, m1 = w_of("D-0001", 2)
    w2, n2, m2 = w_of("D-0002", 3)
    flips[2].append(StatusFlip("D-0001", "candidate", "admitted", FlipCause.GATE_ADMIT, 2,
                               {"wealth": round(w1, 4), "n": n1, "mean_d": round(m1, 4)}))
    flips[3].append(StatusFlip("D-0002", "candidate", "admitted", FlipCause.GATE_ADMIT, 3,
                               {"wealth": round(w2, 4), "n": n2, "mean_d": round(m2, 4)}))
    composed = [pl.p for pl in sorted(plans, key=lambda x: (x.p.round, x.p.id))
                if pl.report is not None and pl.p.arm is Arm.JOURNAL and pl.p.round <= spec.run.rounds
                and "D-0001" in pl.p.composition.decision_ids]
    window = composed[-spec.detect.window:]
    contradicted = sum(_disposition("D-0001", x) is Disposition.CONTRADICTED for x in window)
    if not contradicted / len(window) > spec.detect.contradicted_share:
        raise ValueError("fixture suspects D-0001 without a contradicted share above detect.contradicted_share")
    flips[3].append(StatusFlip("D-0001", "admitted", "suspect", FlipCause.DETECT_SUSPECT, 3,
                               {"contradicted": contradicted, "window": len(window)}))
    commits: list[Commit] = []
    bar = 1 / spec.gate.alpha
    for w_, did in ((w1, "D-0001"), (w2, "D-0002")):
        if w_ < bar:
            raise ValueError(f"fixture admits {did} at W {w_:.3f} below 1/alpha {bar:.3f}; replant its deltas")
    for r in sorted(flips):
        for f in flips[r]:
            events.append_flip(f)
            if f.cause is FlipCause.GATE_ADMIT:
                subject = (f"{f.subject_id} candidate → admitted: W {f.evidence['wealth']:.2f} ≥ {bar:.2f}, "
                           f"n {f.evidence['n']} pairs, mean d {f.evidence['mean_d']:.2f}")
            else:
                subject = (f"{f.subject_id} admitted → suspect: contradicted on {f.evidence['contradicted']} of its "
                           f"last {f.evidence['window']} composed passes")
            journal.flip(f, subject)
            commits.append(Commit(_sha(seed, "commit", f.subject_id, f.round, f.new), subject))

    # Lenses
    lenses = MarkdownLensSet(tempfile.mkdtemp(prefix="friday-fixture-lenses-"), RecordingVcs())
    for lid, angle, cf in BASELINE_LENSES:
        lenses.add(Lens(lid, angle, cf, LensStatus.ACTIVE, has_cleared_floor=True))
    for lid, minted, angle, cf in MINTED_LENSES:
        lenses.add(Lens(lid, angle, cf, LensStatus.HIDDEN, minted_round=minted))
    lens_flips = {2: [StatusFlip("L-09", "hidden", "active", FlipCause.LENS_CLEAR_FLOOR, 2, {})],
                  3: [StatusFlip("L-04", "active", "superseded", FlipCause.LENS_FOLD, 3, {"rho": 0.91}, ("L-03",))]}
    # Answers from journal-arm training passes: each lens is answered from the round after it was minted.
    minted_at = {lid: m for lid, m, _, _ in MINTED_LENSES}
    answers_by_round: dict[int, int] = defaultdict(int)
    for pl in plans:
        if pl.p.arm is not Arm.JOURNAL or pl.report is None or pl.p.round > spec.run.rounds:
            continue
        rng = derive(seed, "lens-answer", pl.p.id)
        for lid in [x[0] for x in BASELINE_LENSES] + list(minted_at):
            if lid in minted_at and pl.p.round <= minted_at[lid]:
                continue
            s = LENS_SIGNAL[lid]
            p = round(_clip(s * pl.report.loss + (1 - s) * rng.random() + rng.uniform(-0.05, 0.05)), 3)
            judge.add_lens_answers([LensAnswer(pl.p.id, lid, p, "quoted line", JUDGE_MODEL)])
            answers_by_round[pl.p.round] += 1
        for did in pl.p.composition.decision_ids:
            judge.add_fires([Fire(pl.p.id, did, pl.p.round, _disposition(did, pl.p), "quoted line")])
    for r in sorted(lens_flips):
        for f in lens_flips[r]:
            events.append_flip(f)
            msg = (f"{f.subject_id} minted lens clears its floor at round {r}" if f.new == "active"
                   else f"{f.subject_id} folds into {f.successors[0]}: ρ {f.evidence['rho']:.2f}")
            lenses.flip(f, msg)
            flips[r].append(f)
            commits.append(Commit(_sha(seed, "commit", f.subject_id, r, f.new), msg))

    # Observations and pools
    pools, obs = MemoryPoolStore(), MemoryObservationStore()
    pools.put_pool(Pool("P-0001", "L-08", "canvas drawn at a fixed pixel size", ("size the canvas from its container",),
                        PoolStatus.NOMINATED))
    pools.put_pool(Pool("P-0002", "L-07", "empty table renders nothing", ("show an empty-state row",),
                        PoolStatus.NOMINATED))
    pools.put_pool(Pool("P-0003", "L-09", "pointer coordinates off on a narrow screen",
                        ("scale pointer coordinates", "widen the canvas"), PoolStatus.NOMINATED))
    pools.put_pool(Pool("P-0004", "free", "timer keeps running after reset", ("clear the interval on reset",)))
    pools.put_pool(Pool("P-0005", "L-08", "canvas overflows its panel", ("size the canvas from its container",),
                        PoolStatus.MERGED, merged_into="P-0001"))
    world_pool_lens = {x.id: x.lens_id for x in pools.pools()}
    n = 0
    for pl in plans:
        if pl.p.arm is not Arm.JOURNAL or pl.report is None or pl.p.round > spec.run.rounds:
            continue
        pool = {"canvas game": "P-0001" if pl.p.round < 3 else "P-0003", "data tool": "P-0002",
                "timer or tracker": "P-0004"}.get(pl.cat.family)
        if pool is None or derive(seed, "obs", pl.p.id).random() < 0.35:
            continue
        n += 1
        status = "closed" if pool in ("P-0001", "P-0002") else "open"
        lens_id = world_pool_lens[pool]
        obs.add_observations([Observation(f"O-{n:04d}", pl.p.id, pl.p.round, lens_id, pl.p.category, "shape",
                                          "direction", "quoted line", status=status, pool_id=pool)])

    close_calls: dict[int, list[tuple[str, str, int, int, float]]] = {}
    for r in TRAINING:
        calls = [("pooling", DRAFT_MODEL, 4200, 900, 0.006), ("tagger", DRAFT_MODEL, 2600, 300, 0.003)]
        if r in (1, 2, 3):
            calls.append(("nominator", DRAFT_MODEL, 5200, 700, 0.007))
        if r in (1, 2):
            calls.append(("lens_minter", DRAFT_MODEL, 6800, 500, 0.008))
        calls.append(("coordinator", "openai/gpt-oss-120b", 3100, 400, 0.002))
        close_calls[r] = calls
        for role, model, tin, tout, usd in calls:
            spend.record(role, f"close-{r}", ChatResult(REPLY_SENTINEL, model, tin, tout, usd), True)

    stores = ExportStores(passes, MemoryGoalSource(requests), judge, obs, pools, ledger, events, spend,
                          composed_tokens, tags)
    return World(spec, plans, stores, journal, lenses, commits, dict(flips), close_calls, dict(answers_by_round))


# ---------------------------------------------------------------------------------------------------------
# The telemetry log


class _Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> float:
        return self.now


class _Ids:
    """Seeded span ids (the adapter's own are random): a counter hashed under the fixture seed."""

    def __init__(self, seed: int) -> None:
        self.seed, self.n = seed, 0

    def __call__(self) -> str:
        self.n += 1
        return hashlib.sha256(f"{self.seed}:span:{self.n}".encode()).hexdigest()[:16]


class _Script:
    """Telemetry actions on a simulated clock, replayed in time order (passes run in parallel processes)."""

    def __init__(self, clock: _Clock) -> None:
        self.clock, self.actions = clock, []

    def at(self, t: float, fn: Callable[[], None]) -> None:
        self.actions.append((t, len(self.actions), fn))

    def span(self, tel: Any, name: str, t0: float, t1: float, attrs: dict[str, Any] | None = None,
             close_attrs: dict[str, Any] | None = None, error: str | None = None) -> None:
        if not t1 > t0:
            raise ValueError(f"span {name} closes at {t1} before it opens at {t0}")
        box: dict[str, Any] = {}

        def open_() -> None:
            box["cm"] = tel.span(name, **(attrs or {}))
            box["h"] = box["cm"].__enter__()

        def close_() -> None:
            if close_attrs:
                box["h"].set(**close_attrs)
            if error is None:
                box["cm"].__exit__(None, None, None)
                return
            exc = TransportError(error)
            box["cm"].__exit__(type(exc), exc, None)

        self.at(t0, open_)
        self.at(t1, close_)

    def metric(self, tel: Any, t: float, name: str, value: float | None, unit: str, **attrs: Any) -> None:
        self.at(t, lambda: tel.metric(name, value, unit, **attrs))

    def event(self, tel: Any, t: float, name: str, **attrs: Any) -> None:
        self.at(t, lambda: tel.event(name, **attrs))

    def run(self) -> None:
        for t, _, fn in sorted(self.actions, key=lambda a: (a[0], a[1])):
            self.clock.now = t
            fn()


class TransportError(Exception):
    """The fixture's stand-in for the client's transport failure."""



def _tel(conn: Any, scope: Scope, clock: _Clock, ids: _Ids, pid: int) -> SqliteTelemetry:
    tel = SqliteTelemetry(conn, scope, clock=clock, pid=pid)
    tel._new_id = ids   # seeded ids keep the fixture deterministic; SqliteTelemetry takes no id factory
    return tel


def _model_call(s: _Script, tel: Any, t0: float, t1: float, role: str, tag: str, call: tuple, *, fail: bool = False,
                structured: bool = False) -> None:
    _, model, tin, tout, usd = call
    attrs = {"role": role, "tag": tag, "structured": structured}
    if fail:
        s.span(tel, "model.call", t0, t1, attrs, {"model": None, "attempts": 5, "retry_statuses": [503] * 5,
                                                  "backoff_s": 31.0, "tokens_in": None, "tokens_out": None,
                                                  "cost_usd": None, "cached": False},
               error="TransportError: 503 after 5 tries")
        return
    s.span(tel, "model.call", t0, t1, attrs, {"model": model, "attempts": 1, "retry_statuses": [], "backoff_s": 0.0,
                                              "tokens_in": tin, "tokens_out": tout, "cost_usd": round(usd, 6),
                                              "cached": False})
    if structured:
        s.event(tel, t1 + 0.001, "model.json", role=role, tag=tag, repair_rung=1, reasks=0, ok=True)


def _pass_script(s: _Script, tel: Any, pl: PassPlan, t: float, spec: RunSpec, readout: bool) -> float:
    """Writes one pass's spans from `t`; returns its end time."""
    p, rng = pl.p, derive(spec.run.seed, "timing", pl.p.id)
    c = p.composition
    calls = list(pl.calls)
    cur = t + 0.02

    def adv(d: float) -> tuple[float, float]:
        nonlocal cur
        a, b = cur, cur + d
        cur = b + 0.01
        return a, b

    a, b = adv(0.04 + rng.random() * 0.1)
    s.span(tel, "pass.compose", a, b, {}, {"prompt_sha": _sha(spec.run.seed, "prompt", p.id),
                                           "prompt_tokens_est": 900 + pl.composed_tokens})
    worker = calls.pop(0)
    a, b = adv(22 + rng.random() * 30 + (31 if p.transport_failed else 0))
    s.span(tel, "pass.worker", a, b, {"model": WORKER_MODEL},
           None if p.transport_failed else {"tokens_in": worker[2], "tokens_out": worker[3],
                                            "cost_usd": round(worker[4], 6)},
           error="TransportError: 503 after 5 tries" if p.transport_failed else None)
    _model_call(s, tel, a + 0.01, b - 0.01, "worker", "build", worker, fail=p.transport_failed)
    if p.transport_failed:
        s.event(tel, cur, "pass.outcome", outcome="transport_failed", error="TransportError: 503 after 5 tries")
        return cur + 0.02
    s.metric(tel, cur, "pass.artifact_bytes", float(5200 + rng.randrange(9000)), "bytes", truncated=False)
    if p.repaired:
        call = calls.pop(0)
        a, b = adv(10 + rng.random() * 14)
        s.span(tel, "pass.repair", a, b, {}, {"tokens_in": call[2], "tokens_out": call[3], "cost_usd": call[4]})
        _model_call(s, tel, a + 0.01, b - 0.01, "worker", "repair", call)
    report = pl.report
    assert report is not None
    viewports = spec.eval.viewports[p.regime.value]
    e0 = cur
    cur += 0.02
    ctx0 = cur
    cur += 0.3
    for v in viewports:
        v0 = cur
        cur += 0.01
        a, b = adv(0.8 + rng.random() * 1.6)
        s.span(tel, "eval.load", a, b, {}, {"console_errors": len(report.console_errors)})
        a, b = adv(3 + rng.random() * 3)
        found = 3 + rng.randrange(4)
        s.span(tel, "eval.probe", a, b, {}, {"controls_found": found, "controls_driven": found,
                                             "silent_controls": report.silent_controls})
        h = report.how_to[0] if report.how_to else None
        a, b = adv(2 + rng.random() * 3)
        s.span(tel, "eval.how_to", a, b, {}, {"rung": int(h.parse.rung) if h else 4,
                                              "steps": len(h.parse.declared) if h else 0,
                                              "steps_passed": sum(x.passed for x in h.results) if h else 0})
        a, b = adv(2 + rng.random() * 3)
        s.span(tel, "eval.checks", a, b, {}, {"checks": len(report.checks),
                                              "checks_passed": sum(x.passed for x in report.checks)})
        a, b = adv(0.3 + rng.random() * 0.3)
        s.span(tel, "eval.screenshot", a, b, {}, {"bytes": 48000 + rng.randrange(40000)})
        s.span(tel, "eval.viewport", v0, cur, {"width": int(v[0]), "height": int(v[1])})
        cur += 0.02
    call = calls.pop(0)
    a, b = adv(4 + rng.random() * 5)
    s.span(tel, "eval.rubric", a, b, {"model": RUBRIC_MODEL, "questions": spec.eval.rubric_questions})
    _model_call(s, tel, a + 0.01, b - 0.01, "rubric", "rubric", call, structured=True)
    a, b = adv(1 + rng.random())
    s.span(tel, "eval.quality", a, b, {}, {"eslint_errors": report.eslint_errors,
                                           "axe_violations": report.axe_violations})
    s.span(tel, "eval.browser_context", ctx0, cur, {}, {"shared_server": True})
    s.span(tel, "eval", e0, cur + 0.01, {"viewports": len(viewports)})
    cur += 0.03
    if p.arm is Arm.JOURNAL and not readout:
        call = calls.pop(0)
        a, b = adv(5 + rng.random() * 5)
        lens_n = 10
        s.span(tel, "judge.lens_call", a, b, {"lenses": lens_n}, {"answered": lens_n, "reasks": 0})
        _model_call(s, tel, a + 0.01, b - 0.01, "judge", "lens", call, structured=True)
        call = calls.pop(0)
        a, b = adv(6 + rng.random() * 6)
        s.span(tel, "judge.report_call", a, b, {}, {"observations": rng.randrange(0, 3),
                                                    "fires": len(c.decision_ids), "quote_reasks": 0})
        _model_call(s, tel, a + 0.01, b - 0.01, "judge", "report", call, structured=True)
    for name in ("loss", "A", "R", "P", "H", "C", "Q"):
        s.metric(tel, cur, f"pass.{name}", float(getattr(report, name)), "score")
    s.event(tel, cur + 0.001, "pass.outcome", outcome="scored", error="")
    return cur + 0.02


def _pass_attrs(pl: PassPlan) -> dict[str, Any]:
    c = pl.p.composition
    return {"request_id": pl.p.category, "cell": [pl.cat.family, pl.cat.mode], "arm": pl.p.arm.value,
            "regime": pl.p.regime.value, "pair_slot": pl.p.pair_slot, "exposed": c.exposed, "ablated": c.ablated,
            "composed": list(c.decision_ids), "notes": c.notes, "worker_model": WORKER_MODEL}


def write_telemetry(world: World, doc: dict[str, Any], db: Path) -> None:
    """Writes the fixture run's telemetry log to `db` (replaced if present)."""
    spec, seed = world.spec, world.spec.run.seed
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db}{suffix}").unlink(missing_ok=True)
    conn = connect_telemetry(db, busy_timeout_ms=spec.telemetry.busy_timeout_ms)
    clock, ids = _Clock(), _Ids(seed)
    s = _Script(clock)
    runner = _tel(conn, Scope("main"), clock, ids, pid=4100)
    t = START
    halt_at = {}
    run_start = t
    s.event(runner, t + 0.01, "run.manifest", spec_hash=spec.spec_hash, git_sha=_sha(seed, "git"), seed=seed,
            role_models={k: v.pins[0] for k, v in spec.models.roles.items()}, fallbacks_taken=[], python="3.12.7",
            playwright="1.55.0", chromium="140.0.7339.16", host_cpus=12,
            max_parallel_passes=spec.run.max_parallel_passes)
    t += 0.5
    spend_so_far = 0.0
    lens_values = {x["id"]: {h["round"]: h for h in x["value_history"]} for x in doc["lenses"]}
    decisions = {d["id"]: d for d in doc["decisions"]}
    by_round: dict[int, list[PassPlan]] = defaultdict(list)
    for pl in world.plans:
        by_round[pl.p.round].append(pl)
    pid_n = 5000
    for r in sorted(TRAINING):
        rt = runner.scoped(round=r)
        firsts = [pl for pl in by_round[r] if not pl.p.id.endswith("-rr")]
        rrs = [pl for pl in by_round[r] if pl.p.id.endswith("-rr")]
        r0 = t
        s.span(rt, "goals.tag", r0 + 0.05, r0 + 2.8, {"requests": len(TRAINING[r])},
               {"new_tags": sum(TAG_MINTED[x] == r for x in TAG_GLOSSES)})
        p0 = r0 + 3.0
        ends, spans_iv, first_iv = [], [], {}
        for k, pl in enumerate(firsts):
            pid_n += 1
            tel = _tel(conn, Scope("main", r, pl.p.id), clock, ids, pid=pid_n)
            st = p0 + 0.05 + k * 0.35
            end = _pass_script(s, tel, pl, st + 0.01, spec, readout=False)
            s.span(tel, "pass", st, end, {**_pass_attrs(pl), "process_pid": pid_n})
            ends.append(end)
            spans_iv.append((st, end))
            first_iv[pl.p.id] = end
        for k, pl in enumerate(rrs):
            pid_n += 1
            tel = _tel(conn, Scope("main", r, pl.p.id), clock, ids, pid=pid_n)
            st = first_iv[pl.p.id[:-3]] + 1.0 + k * 0.3
            end = _pass_script(s, tel, pl, st + 0.01, spec, readout=False)
            s.span(tel, "pass", st, end, {**_pass_attrs(pl), "process_pid": pid_n, "rerun_of": pl.p.id[:-3]})
            ends.append(end)
            spans_iv.append((st, end))
        p1 = max(ends) + 0.05
        s.span(rt, "round.passes", p0, p1)
        peak = max(sum(1 for a, b in spans_iv if a <= x < b) for x, _ in spans_iv)
        # Round close
        c0 = p1 + 0.3
        cur = c0 + 0.02
        flips = world.flips_by_round.get(r, [])
        admitted = [f.subject_id for f in flips if f.new == "admitted" and f.subject_id.startswith("D-")]
        suspect = [f.subject_id for f in flips if f.new == "suspect"]
        minted = [lid for lid, m, _, _ in MINTED_LENSES if m == r]
        folded = [f.subject_id for f in flips if f.new == "superseded"]
        drafted = [d for d, v in DECISIONS.items() if v["drafted"] == r]
        n_obs = sum(1 for o in world.stores.observations.observations() if o.round == r)
        calls = {role: (role, m, a, b, u) for role, m, a, b, u in world.close_calls[r]}
        phases: list[tuple[str, float, dict[str, Any], list[str]]] = [
            ("steers", 0.05, {}, []),
            ("lens_curation", 2.2 + r * 0.4, {"lenses_minted": len(minted), "lenses_folded": len(folded)},
             ["lens_minter"]),
            ("observations", 0.4, {"observations": n_obs}, []),
            ("pooling", 7.5 + r, {"pools_new": {1: 2, 2: 1, 3: 1}[r], "pools_merged": int(r == 2)}, ["pooling"]),
            ("nomination", 9.0, {"nominated": len(drafted), "fast_tracked": int(r == 1)}, ["nominator"]),
            ("gate", 0.3, {"admitted": len(admitted), "rejected": 0}, []),
            ("detector", 0.2, {"retired": 0, "suspect": len(suspect)}, []),
            ("pings", 0.05, {}, ["coordinator"]),
        ]
        for name, dur, counts, roles in phases:
            a, b = cur, cur + dur
            s.span(rt, f"close.{name}", a, b, {}, counts)
            for role in roles:
                if role in calls:
                    _model_call(s, rt, a + 0.1 * dur, b - 0.1 * dur, role, name, calls[role], structured=True)
            if name == "lens_curation":
                for lid, hist in lens_values.items():
                    if r in hist:
                        s.metric(rt, b - 0.01, "close.lens_value", hist[r]["value"], "score", lens_id=lid,
                                 n=hist[r]["n"])
            if name == "gate":
                for did, d in decisions.items():
                    rows = [x for x in d["ledger"]["exposure"]["deltas"] if x["round"] <= r]
                    if not any(x["round"] == r for x in rows):
                        continue   # under test at this close: it took exposure this round
                    path = d["ledger"]["exposure"]["wealth_path"]
                    s.metric(rt, b - 0.01, "close.wealth", path[len(rows) - 1], "score", decision_id=did,
                             n=len(rows), mean_d=sum(x["d"] for x in rows) / len(rows))
            cur = b + 0.02
        for f in flips:
            sha = next(c.sha for c in world.commits if c.sha == _sha(seed, "commit", f.subject_id, f.round, f.new))
            s.event(rt, cur, "close.commit", subject_id=f.subject_id, sha=sha, cause=f.cause.value)
        if suspect:
            s.event(rt, cur + 0.01, "round.ping", reason=f"{suspect[0]} moved to suspect while the judge marks it "
                                                          "contradicted")
        c1 = cur + 0.05
        s.span(rt, "close", c0, c1)
        round_calls = [c for pl in by_round[r] for c in pl.calls] + world.close_calls[r]
        spend_so_far += sum(c[4] for c in round_calls)
        by_role: dict[str, float] = defaultdict(float)
        for role, _, _, _, usd in round_calls:
            by_role[role] += usd
        e = c1 + 0.02
        n_failed = sum(pl.p.transport_failed for pl in by_round[r])
        s.metric(rt, e, "round.pass_count", float(sum(pl.report is not None for pl in by_round[r])), "count",
                 outcome="scored")
        s.metric(rt, e, "round.pass_count", float(n_failed), "count", outcome="transport_failed")
        s.metric(rt, e, "round.pass_count", float(len(rrs)), "count", outcome="rerun")
        s.metric(rt, e, "round.concurrency", float(peak), "count")
        for role in sorted(by_role):
            s.metric(rt, e, "round.spend", round(by_role[role], 6), "usd", role=role)
        s.metric(rt, e + 0.01, "run.spend", round(spend_so_far, 6), "usd")
        regime = "R2" if r >= spec.drift.round else "R1"
        s.span(rt, "round", r0, e + 0.05, {"regime": regime, "requests": len(TRAINING[r]),
                                               "pairs_planned": len(TRAINING[r])})
        t = e + 2.0
        halt_at = {"round": r}
    # Readout
    rr = spec.run.rounds + 1
    ro = runner.scoped(round=rr)
    r0 = t
    plans = [pl for pl in by_round[rr] if not pl.p.id.endswith("-rr")]
    ends = []
    for k, pl in enumerate(plans):
        pid_n += 1
        tel = _tel(conn, Scope("main", rr, pl.p.id), clock, ids, pid=pid_n)
        st = r0 + 0.1 + k * 0.3
        end = _pass_script(s, tel, pl, st + 0.01, spec, readout=True)
        s.span(tel, "pass", st, end, {**_pass_attrs(pl), "process_pid": pid_n})
        ends.append((pl, end))
    for k, pl in enumerate(p for p in by_round[rr] if p.p.id.endswith("-rr")):
        pid_n += 1
        tel = _tel(conn, Scope("main", rr, pl.p.id), clock, ids, pid=pid_n)
        st = max(e for _, e in ends) + 1.0 + k * 0.3
        end = _pass_script(s, tel, pl, st + 0.01, spec, readout=True)
        s.span(tel, "pass", st, end, {**_pass_attrs(pl), "process_pid": pid_n, "rerun_of": pl.p.id[:-3]})
        ends.append((pl, end))
    r1 = max(e for _, e in ends) + 0.2
    readout = doc["readout"]
    for key, label in (("r1", "R1"), ("r2", "R2")):
        c = readout[key]
        s.event(ro, r1, "readout.reading", reading=label, population=c["population"], estimate=c["mean"],
                interval=None, lower_bound=c["lower_bound"], claim_above=c["claim_above"], p=c["p"],
                verdict=c["verdict"])   # the catalogue's `interval` is null: R1 and R2 read a one-sided bound
    r3, r4 = readout["r3"], readout["r4"]
    s.event(ro, r1, "readout.reading", reading="R3", population=r3["population"],
            estimate=len(r3["admitted_before_retired_after"]), interval=None, p=None, verdict=r3["verdict"])
    s.event(ro, r1, "readout.reading", reading="R4", population=r4["population"], estimate=len(r4["kept_minted"]),
            interval=None, p=None, verdict=r4["verdict"])
    s.span(ro, "readout", r0, r1 + 0.1, {"passes": len(by_round[rr]), "reps": spec.readout.reps})
    s.event(runner, r1 + 0.2, "run.halt", reason="run.rounds", round=halt_at["round"])
    s.span(runner, "run", run_start, r1 + 0.3, {}, {"halt_reason": "run.rounds"})
    s.run()
    conn.close()


# ---------------------------------------------------------------------------------------------------------
# Entry point


def fixture_spec_text() -> str:
    """The repository run.yml with OVERRIDES applied, as YAML text with a header naming them."""
    raw = yaml.safe_load((REPO / "run.yml").read_text())
    for section, keys in OVERRIDES.items():
        raw[section].update(keys)
    header = ("# Showcase fixture run spec, written by web/fixtures/make.py from the repository run.yml.\n"
              "# Replaced keys: " + ", ".join(f"{s}.{k}" for s, ks in OVERRIDES.items() for k in ks) + "\n")
    return header + yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)


def make(out: Path = HERE) -> dict[str, Any]:
    """Writes run.yml, export.json, telemetry.db and artifacts/ under `out`; returns the export document."""
    out.mkdir(parents=True, exist_ok=True)
    text = fixture_spec_text()
    (out / "run.yml").write_text(text)
    spec = parse_spec(text)
    world = build_world(spec)
    key_env = spec.models.endpoint.api_key_env
    before = os.environ.get(key_env)
    os.environ[key_env] = KEY_SENTINEL          # a leak of the environment into the export would carry it
    try:
        doc = build(world.stores, world.journal, world.lenses, world.commits, spec)
    finally:
        if before is None:
            os.environ.pop(key_env, None)
        else:
            os.environ[key_env] = before
    (out / "export.json").write_text(dumps(doc) + "\n")
    shutil.rmtree(out / "artifacts", ignore_errors=True)
    for k, pl in enumerate(world.plans):
        if pl.p.artifact_path is None:
            continue
        d = out / "artifacts" / pl.p.id
        d.mkdir(parents=True)
        rung_none = pl.report is not None and pl.report.how_to and pl.report.how_to[0].parse.rung is HowToRung.NONE
        title = pl.cat.text[0].upper() + pl.cat.text[1:]
        (d / "index.html").write_text(render_app(pl.cat.family, title, k, with_how_to=not rung_none))
        # What a pass folder may also hold; the page never serves it.
        (d / "prompt.txt").write_text(PROMPT_SENTINEL + "\n")
        (d / "reply.txt").write_text(REPLY_SENTINEL + "\n")
    write_telemetry(world, doc, out / "telemetry.db")
    return doc


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=HERE)
    args = ap.parse_args(argv)
    doc = make(args.out)
    print(f"wrote {len(doc['passes'])} passes, {len(doc['decisions'])} decisions to {args.out}")


if __name__ == "__main__":
    main()
