"""The readout (SPEC § 12): the pre-registered readings R1-R4 and the descriptive readings, computed live
from the stores at the close. Nothing here is stored; every figure is recomputed on each call.

Population rules:
- a readout pass is a pass on a held-out category (the held-out set V is never built during training);
  a training pass is any other pass. A held-out pass that carries a pair slot, an exposure or an
  ablation, runs under a regime other than `readout.regime`, or whose composition contradicts its arm,
  is a leak and raises.
- a scored pass did not fail for transport and has an EvalReport.
- a reading whose population is missing reads unread: its figures and its verdict are None.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from friday.algorithms.readout import paired_reading, per_category_mean_diff
from friday.config import RunSpec
from friday.domain.events import StatusFlip
from friday.domain.hooks import fires
from friday.domain.lifecycle import COMPOSABLE_DECISION, FlipCause
from friday.domain.types import (
    Arm,
    DecisionStatus,
    DeltaKind,
    EvalReport,
    HowToRung,
    LensStatus,
    Pass,
    Regime,
    Request,
)
from friday.ports.goals import GoalSource, TagVocabulary
from friday.ports.stores import EventLog, Journal, Ledger, LensSet, PassStore, SpendLog
from friday.ports.telemetry import Telemetry
from friday.stats.significance import PAIRED_TESTS

# SPEC § 2 claim language: the pitch word each reading earns.
IMPROVES = "improves"
UNRESOLVED = "unresolved"
UNEARNED = "unearned"
LEANER = "leaner than shared notes"
NOT_WORSE = "not shown worse than shared notes"
FORGETS = "forgets what went stale"
JUDGE_LEARNS = "the judge learns what to look at"

# Retirements that count as earned forgetting (SPEC § 1 goal 3: "retired by evidence"). A steered
# retirement is reported beside them, never inside the count (SPEC § 11, the steer).
_EARNED_RETIRE = frozenset({FlipCause.DETECT_RETIRE.value, FlipCause.SUPERSEDE.value})


@dataclass(frozen=True)
class ReadoutStores:
    """The stores the readout reads. `composed_tokens` maps a pass id to the tokens composed into its
    worker prompt (decisions and notes), as recorded by the pass pipeline; a pass absent from it is unread.
    `tags` is the tag vocabulary that holds each request's tags; without it, or with any held-out request
    untagged, hook transfer reads unread."""

    passes: PassStore
    goals: GoalSource
    journal: Journal
    lenses: LensSet
    events: EventLog
    ledger: Ledger
    spend: SpendLog
    composed_tokens: Mapping[str, int] = field(default_factory=dict)
    tags: TagVocabulary | None = None


@dataclass(frozen=True)
class PairedClaim:
    """R1 or R2: per held-out category the mean over reps of loss(baseline) - loss(journal), then the
    registered one-sided test and the one-sided lower bound from inverting that same test (one estimator
    per figure, SPEC § 2: the bound is above 0 exactly when p <= readout.r1_p)."""

    reading: str
    baseline: str
    population: str | None
    n_categories: int | None
    reps: Mapping[str, tuple[int, int]]           # arm -> (min, max) scored reps per category read
    unread_categories: tuple[str, ...]            # held-out categories missing from either arm
    per_category: Mapping[str, float]
    mean: float | None
    p: float | None
    lower_bound: float | None                    # None: no bound attainable at this n (reads -inf)
    level: float
    claim_above: float                            # the claim is earned when lower_bound lies above this
    verdict: str | None


@dataclass(frozen=True)
class ForgettingClaim:
    """R3, from the event log, with the drift round as the boundary: admitted before it means first admitted
    at a round close < drift round; retired after it means retired at a round close >= drift round."""

    population: str | None
    drift_round: int
    admitted_before_retired_after: tuple[str, ...]
    admitted_after: tuple[str, ...]
    steered_retired_after: tuple[str, ...]        # admitted before, retired after by a steer (not counted)
    suspected_after: tuple[str, ...]              # admitted before, moved to suspect at or after the drift round
    c: int | None                                 # candidates tested: decisions with >= 1 exposure delta
    alpha: float
    alpha_c: float | None
    verdict: str | None


@dataclass(frozen=True)
class LensClaim:
    """R4: lenses the lens minter proposed that are active at the close."""

    population: str | None
    kept_minted: tuple[str, ...]
    hidden_minted: tuple[str, ...]
    verdict: str | None


@dataclass(frozen=True)
class CostReading:
    total_usd: float
    admitted: int
    usd_per_admitted: float | None


@dataclass(frozen=True)
class ArmMean:
    """A per-arm mean over the arm's scored readout passes; None when the arm has none."""

    n: int
    mean: float | None


@dataclass(frozen=True)
class HowToHonesty:
    """Per arm on V. Each share is a mean over the passes where it is defined; its n says how many."""

    n_passes: int
    declared_passed_share: ArmMean     # passed declared checks / (declared + invalid entries), all viewports
    contract_declared_share: ArmMean   # contract ids declared / contract ids
    silent_per_pass: ArmMean
    rung_share: Mapping[str, float] | None   # rung name -> share of passes with a how-to reading
    no_how_to: int                     # scored passes with no how-to reading


@dataclass(frozen=True)
class HookTransfer:
    population: str | None
    s_star: tuple[str, ...]
    requests_fired: tuple[str, ...]          # held-out requests on which >= 1 S* decision's hook fires
    categories_composed: tuple[str, ...]     # held-out categories whose journal-arm passes composed >= 1 decision
    untagged: tuple[str, ...] = ()           # held-out requests with no tags in the vocabulary (reading unread)


@dataclass(frozen=True)
class Readout:
    population: str
    held_out: tuple[str, ...]
    regime: str
    unscored: Mapping[str, int]                 # arm -> readout passes that failed for transport or lack an eval
    r1: PairedClaim
    r2: PairedClaim
    r3: ForgettingClaim
    r4: LensClaim
    cost: CostReading
    quality: Mapping[str, ArmMean]
    how_to: Mapping[str, HowToHonesty]
    hook_transfer: HookTransfer
    tokens_composed: Mapping[str, ArmMean]


def held_out_ids(goals: GoalSource) -> frozenset[str]:
    """The held-out category ids."""
    return frozenset(r.id for r in goals.held_out())


def _check_readout_pass(p: Pass, regime: str) -> None:
    c = p.composition
    if p.pair_slot is not None or c.exposed is not None or c.ablated is not None:
        raise ValueError(f"pass {p.id} on held-out category {p.category} is a training pair; V is never trained on")
    if p.regime.value != regime:
        raise ValueError(f"readout pass {p.id} ran under {p.regime.value}, not readout.regime {regime}")
    if p.arm is Arm.EMPTY and (c.decision_ids or c.notes):
        raise ValueError(f"empty-arm pass {p.id} composes decisions or notes")
    if p.arm is Arm.NOTES and (c.decision_ids or not c.notes):
        raise ValueError(f"notes-arm pass {p.id} must compose the notes file and no decisions")
    if p.arm is Arm.JOURNAL and c.notes:
        raise ValueError(f"journal-arm pass {p.id} composes the notes file")


def check_journal_arm(passes: Sequence[Pass], journal: Journal) -> None:
    """Raises when a journal-arm readout pass composes a decision outside S* (status at the close not composable)."""
    status = {d.id: d.status for d in journal.decisions()}
    for p in passes:
        if p.arm is not Arm.JOURNAL:
            continue
        outside = sorted(i for i in p.composition.decision_ids if status.get(i) not in COMPOSABLE_DECISION)
        if outside:
            raise ValueError(f"journal-arm pass {p.id} composes {outside}, outside S* at the close")


def readout_passes(passes: PassStore, held_out: frozenset[str], regime: str) -> list[Pass]:
    """Passes on held-out categories, sorted by id. Raises on a leak (see the module docstring)."""
    out = sorted((p for p in passes.passes() if p.category in held_out), key=lambda p: p.id)
    for p in out:
        _check_readout_pass(p, regime)
    return out


def training_passes(passes: PassStore, held_out: frozenset[str]) -> list[Pass]:
    """Passes on training categories, sorted by (round, id)."""
    return sorted((p for p in passes.passes() if p.category not in held_out), key=lambda p: (p.round, p.id))


def scored(passes: PassStore, ps: Sequence[Pass]) -> list[tuple[Pass, EvalReport]]:
    """(pass, eval) for the passes that did not fail for transport and carry an eval."""
    out = []
    for p in ps:
        if p.transport_failed:
            continue
        report = passes.eval_of(p.id)
        if report is not None:
            out.append((p, report))
    return out


def _reps_text(reps: Mapping[str, tuple[int, int]]) -> str:
    lo = min(r[0] for r in reps.values())
    hi = max(r[1] for r in reps.values())
    return f"{lo} reps" if lo == hi else f"{lo}-{hi} reps"


def _paired(
    reading: str, baseline: Arm, rows: Mapping[Arm, list[tuple[str, float]]], held_out: frozenset[str],
    spec: RunSpec, verdict_of, claim_above: float,
) -> PairedClaim:
    level = spec.readout.interval_level
    base_rows, jour_rows = rows.get(baseline, []), rows.get(Arm.JOURNAL, [])
    common = {c for c, _ in base_rows} & {c for c, _ in jour_rows}
    unread = tuple(sorted(held_out - common))
    if not common:
        return PairedClaim(reading, baseline.value, None, None, {}, unread, {}, None, None, None, level, claim_above,
                           None)
    b = [r for r in base_rows if r[0] in common]
    t = [r for r in jour_rows if r[0] in common]
    reps: dict[str, tuple[int, int]] = {}
    for arm, arm_rows in ((baseline, b), (Arm.JOURNAL, t)):
        counts = Counter(c for c, _ in arm_rows)
        reps[arm.value] = (min(counts.values()), max(counts.values()))
    diffs = per_category_mean_diff(b, t)
    r = paired_reading(diffs, PAIRED_TESTS.build(spec.readout.test), level)
    population = f"{r.n_categories} held-out categories × {_reps_text(reps)}"
    return PairedClaim(reading, baseline.value, population, r.n_categories, reps, unread, r.per_category,
                       r.mean, r.p, r.lower_bound, level, claim_above, verdict_of(r))


def _admit_round(flips: Sequence[StatusFlip]) -> int | None:
    rounds = [f.round for f in flips if f.new == DecisionStatus.ADMITTED.value and f.cause == FlipCause.GATE_ADMIT]
    return min(rounds) if rounds else None


def forgetting(stores: ReadoutStores, spec: RunSpec) -> ForgettingClaim:
    """R3 counts from the event log; c from the Ledger's exposure rows."""
    drift, alpha = spec.drift.round, spec.gate.alpha
    decisions = stores.journal.decisions()
    if not decisions:
        return ForgettingClaim(None, drift, (), (), (), (), None, alpha, None, None)
    earned, after, steered, suspected = [], [], [], []
    c = 0
    for d in sorted(decisions, key=lambda d: d.id):
        flips = stores.events.flips(d.id)
        if stores.ledger.deltas(d.id, DeltaKind.EXPOSURE):
            c += 1
        admitted = _admit_round(flips)
        if admitted is None:
            continue
        if admitted >= drift:
            after.append(d.id)
            continue
        retire = [f for f in flips if f.new == DecisionStatus.RETIRED.value and f.round >= drift]
        if any(f.cause in _EARNED_RETIRE for f in retire):
            earned.append(d.id)
        elif retire:
            steered.append(d.id)
        if any(f.new == DecisionStatus.SUSPECT.value and f.round >= drift for f in flips):
            suspected.append(d.id)
    verdict = FORGETS if earned and after else UNEARNED
    population = f"{len(decisions)} decisions drafted; drift at round {drift}"
    return ForgettingClaim(population, drift, tuple(earned), tuple(after), tuple(steered), tuple(suspected),
                           c, alpha, alpha * c, verdict)


def judge_eyes(stores: ReadoutStores) -> LensClaim:
    """R4: minted lenses (minted_round set) active at the close."""
    lenses = sorted(stores.lenses.lenses(), key=lambda x: x.id)
    if not lenses:
        return LensClaim(None, (), (), None)
    minted = [x for x in lenses if x.minted_round is not None]
    kept = tuple(x.id for x in minted if x.status is LensStatus.ACTIVE)
    hidden = tuple(x.id for x in minted if x.status is LensStatus.HIDDEN)
    population = f"{len(lenses)} lenses at the close, {len(minted)} minted during the run"
    return LensClaim(population, kept, hidden, JUDGE_LEARNS if kept else UNEARNED)


def cost(stores: ReadoutStores) -> CostReading:
    """Total spend and dollars per decision the gate admitted."""
    admitted = {f.subject_id for f in stores.events.flips()
                if f.new == DecisionStatus.ADMITTED.value and f.cause == FlipCause.GATE_ADMIT}
    total = stores.spend.total_usd()
    return CostReading(total, len(admitted), total / len(admitted) if admitted else None)


def _mean(xs: Sequence[float]) -> ArmMean:
    return ArmMean(len(xs), sum(xs) / len(xs) if xs else None)


def how_to_honesty(rows: Sequence[tuple[Pass, EvalReport]], contracts: Mapping[str, Mapping[str, str]]) -> HowToHonesty:
    """SPEC § 12 how-to honesty over one arm's scored passes."""
    passed_share, contract_share, silent = [], [], []
    rungs: Counter[str] = Counter()
    no_how_to = 0
    for p, report in rows:
        silent.append(float(report.silent_controls))
        if not report.how_to:
            no_how_to += 1
            continue
        parse = report.how_to[0].parse
        rungs[HowToRung(parse.rung).name.lower()] += 1
        denom = sum(len(h.parse.declared) + h.parse.invalid_entries for h in report.how_to)
        if denom:
            passed_share.append(sum(r.passed for h in report.how_to for r in h.results) / denom)
        contract = contracts[p.category]
        if contract:
            contract_share.append((len(contract) - len(parse.undeclared_contract_ids)) / len(contract))
    read = sum(rungs.values())
    shares = {r.name.lower(): rungs[r.name.lower()] / read for r in HowToRung} if read else None
    return HowToHonesty(len(rows), _mean(passed_share), _mean(contract_share), _mean(silent), shares, no_how_to)


def hook_transfer(stores: ReadoutStores, held: Sequence[Request], journal_rows: Sequence[Pass]) -> HookTransfer:
    """Held-out requests on which >= 1 S* decision fires (tags read from the tag vocabulary), and the
    categories whose journal passes composed one. Unread (population None) when there is no held-out set,
    no vocabulary, or any held-out request is untagged."""
    s_star = sorted((d for d in stores.journal.decisions() if d.status in COMPOSABLE_DECISION), key=lambda d: d.id)
    s_ids = tuple(d.id for d in s_star)
    composed = tuple(sorted({p.category for p in journal_rows if p.composition.decision_ids}))
    if not held:
        return HookTransfer(None, s_ids, (), composed)
    tags = {r.id: None if stores.tags is None else stores.tags.tags_of(r.id) for r in held}
    untagged = tuple(sorted(i for i, t in tags.items() if t is None))
    if untagged:
        return HookTransfer(None, s_ids, (), composed, untagged)
    fired = tuple(sorted(r.id for r in held if any(fires(d.hook, tags[r.id]) for d in s_star)))
    population = f"{len(held)} held-out requests, {len(s_star)} decisions in S*"
    return HookTransfer(population, s_ids, fired, composed)


def _tokens(arm_passes: Sequence[Pass], composed: Mapping[str, int]) -> ArmMean:
    """Mean tokens composed per pass: `composed` for a pass it names, else the pass's own `composed_tokens`;
    unread when any of the arm's passes has neither."""
    ps = [p for p in arm_passes if not p.transport_failed]
    counts = [composed.get(p.id, p.composed_tokens) for p in ps]
    if not ps or any(c is None for c in counts):
        return ArmMean(len(ps), None)
    return _mean([float(c) for c in counts if c is not None])


def readings(stores: ReadoutStores, spec: RunSpec) -> Readout:
    """Every pre-registered and descriptive reading of SPEC § 12, computed from the stores."""
    held = sorted(stores.goals.held_out(), key=lambda r: r.id)
    held_ids = frozenset(r.id for r in held)
    regime = Regime(spec.readout.regime).value
    v = readout_passes(stores.passes, held_ids, regime)
    check_journal_arm(v, stores.journal)
    by_arm: dict[Arm, list[Pass]] = defaultdict(list)
    for p in v:
        by_arm[p.arm].append(p)
    scored_by_arm = {arm: scored(stores.passes, by_arm.get(arm, [])) for arm in Arm}
    unscored = {arm.value: len(by_arm.get(arm, [])) - len(scored_by_arm[arm]) for arm in Arm}
    loss_rows = {arm: [(p.category, e.loss) for p, e in rows] for arm, rows in scored_by_arm.items()}

    def r1_verdict(r) -> str:
        return IMPROVES if r.p <= spec.readout.r1_p else UNRESOLVED

    tokens = {arm.value: _tokens(by_arm.get(arm, []), stores.composed_tokens) for arm in Arm}

    def r2_verdict(r) -> str:
        # Non-inferiority (SPEC § 12): "not shown worse" is earned when the lower bound lies above
        # -readout.r2_margin. "Leaner" is a token claim on top of it: earned only when both arms' tokens composed
        # per pass are read and the journal arm's mean is the lower; otherwise the reading stays "not shown worse".
        if r.lower_bound is None:
            return UNRESOLVED
        if r.lower_bound <= -spec.readout.r2_margin:
            return UNEARNED
        t_j, t_n = tokens[Arm.JOURNAL.value].mean, tokens[Arm.NOTES.value].mean
        return LEANER if t_j is not None and t_n is not None and t_j < t_n else NOT_WORSE

    r1 = _paired("R1", Arm.EMPTY, loss_rows, held_ids, spec, r1_verdict, 0.0)
    r2 = _paired("R2", Arm.NOTES, loss_rows, held_ids, spec, r2_verdict, -spec.readout.r2_margin)
    contracts = {r.id: r.contract for r in held}
    population = f"{len(held)} held-out categories × {spec.readout.reps} reps planned, regime {regime}"
    return Readout(
        population=population,
        held_out=tuple(r.id for r in held),
        regime=regime,
        unscored=unscored,
        r1=r1,
        r2=r2,
        r3=forgetting(stores, spec),
        r4=judge_eyes(stores),
        cost=cost(stores),
        quality={arm.value: _mean([float(e.Q) for _, e in scored_by_arm[arm]]) for arm in Arm},
        how_to={arm.value: how_to_honesty(scored_by_arm[arm], contracts) for arm in Arm},
        hook_transfer=hook_transfer(stores, held, by_arm.get(Arm.JOURNAL, [])),
        tokens_composed=tokens,
    )


def run_readout(stores: ReadoutStores, spec: RunSpec, *, telemetry: Telemetry) -> Readout:
    """`readings` inside one `readout` span, with one `readout.reading` event per pre-registered reading.

    Telemetry records what the readout computed; nothing here reads it back."""
    with telemetry.span("readout", reps=spec.readout.reps) as span:
        r = readings(stores, spec)
        span.set(passes=len(readout_passes(stores.passes, frozenset(r.held_out), r.regime)))
        for claim in (r.r1, r.r2):
            telemetry.event("readout.reading", reading=claim.reading, population=claim.population,
                            estimate=claim.mean, lower_bound=claim.lower_bound, claim_above=claim.claim_above,
                            p=claim.p, verdict=claim.verdict)
        telemetry.event("readout.reading", reading="R3", population=r.r3.population,
                        estimate=None if r.r3.population is None else len(r.r3.admitted_before_retired_after),
                        lower_bound=None, claim_above=None, p=None, verdict=r.r3.verdict)
        telemetry.event("readout.reading", reading="R4", population=r.r4.population,
                        estimate=None if r.r4.population is None else len(r.r4.kept_minted),
                        lower_bound=None, claim_above=None, p=None, verdict=r.r4.verdict)
        return r


def _fmt(x: float | None, digits: int = 3) -> str:
    return "unread" if x is None else f"{x:.{digits}f}"


def _bound(c: PairedClaim) -> str:
    return f"{int(c.level * 100)} % lower bound " + ("-inf" if c.lower_bound is None else f"{c.lower_bound:.3f}")


def lines(r: Readout) -> list[str]:
    """One line per reading, each with its population and its interval or p; failed readings included."""
    out = [f"Readout over {r.population}"]
    t_j, t_n = r.tokens_composed[Arm.JOURNAL.value].mean, r.tokens_composed[Arm.NOTES.value].mean
    ratio = "unread" if t_j is None or t_n is None or t_n == 0 else f"{t_j / t_n:.3f}"
    for c in (r.r1, r.r2):
        if c.population is None:
            out.append(f"{c.reading} loss({c.baseline}) - loss(journal): unread (no category scored in both arms)")
            continue
        line = (f"{c.reading} loss({c.baseline}) - loss(journal) over {c.population}: mean {_fmt(c.mean)}, "
                f"p {_fmt(c.p, 4)}, {_bound(c)}; claim needs the bound above {c.claim_above:g}")
        if c.reading == "R2":
            line += f"; tokens composed per pass journal {_fmt(t_j, 1)}, notes {_fmt(t_n, 1)}, ratio {ratio}"
        out.append(f"{line}: {c.verdict}")
    f = r.r3
    if f.verdict is None:
        out.append("R3 earned forgetting: unread (no decision drafted)")
    else:
        out.append(f"R3 earned forgetting over {f.population}: {len(f.admitted_before_retired_after)} admitted "
                   f"before and retired after, {len(f.admitted_after)} admitted after; c {f.c}, "
                   f"α · c {_fmt(f.alpha_c, 2)}; {len(f.suspected_after)} moved to suspect after; "
                   f"{len(f.steered_retired_after)} retired by steer: {f.verdict}")
    lc = r.r4
    out.append("R4 kept minted lenses: unread (no lens set)" if lc.verdict is None
               else f"R4 kept minted lenses over {lc.population}: {len(lc.kept_minted)}: {lc.verdict}")
    out.append(f"Cost: total ${r.cost.total_usd:.2f}, {r.cost.admitted} admitted, "
               f"$/admitted {_fmt(r.cost.usd_per_admitted, 2)}")
    for arm in Arm:
        q, t = r.quality[arm.value], r.tokens_composed[arm.value]
        out.append(f"Q {arm.value}: {_fmt(q.mean, 2)} over {q.n} passes; tokens composed per pass "
                   f"{_fmt(t.mean, 1)} over {t.n} passes")
    h = r.hook_transfer
    if h.population is not None:
        out.append(f"Hook transfer over {h.population}: fires on {len(h.requests_fired)}")
    elif h.untagged:
        out.append(f"Hook transfer: unread ({len(h.untagged)} held-out requests untagged)")
    else:
        out.append("Hook transfer: unread (no held-out set)")
    return out


# ---------------------------------------------------------------------------------------------------------
# The critic run's readings (SPEC § 17): descriptive; the pre-registered R1-R4 belong to the main run


@dataclass(frozen=True)
class HonoredRound:
    round: int
    honored: int
    requests: int
    passes: int                   # critic-run passes whose requests were read
    share: float | None           # honored / requests; None when the round's critic asked for nothing


@dataclass(frozen=True)
class LossBeside:
    category: str
    main: float                   # the main run's mean loss over its scored training passes on the request
    critic: float                 # the critic run's mean loss over its scored passes on the same request
    main_passes: int
    critic_passes: int


@dataclass(frozen=True)
class CriticReadings:
    honored: tuple[HonoredRound, ...]
    honored_unread: tuple[str, ...]          # scored critic-run passes with no requests-honored record
    loss: tuple[LossBeside, ...]
    mean_main: float | None                  # the mean of the per-request means, over the shared requests
    mean_critic: float | None
    mean_diff: float | None                  # critic - main; None with no shared request
    admitted_main: tuple[tuple[str, str], ...]     # (decision id, payload)
    admitted_critic: tuple[tuple[str, str], ...]


def _training_scored(passes: PassStore, held_out: frozenset[str]) -> list[tuple[Pass, EvalReport]]:
    return scored(passes, [p for p in passes.passes() if p.category not in held_out])


def critic_readings(
    main: PassStore, critic: PassStore, honored: Mapping[str, Mapping[str, object]], main_journal: Journal,
    critic_journal: Journal, held_out: frozenset[str], loss_base: Mapping[str, float] | None = None,
) -> CriticReadings:
    """Requests honored per round, the mean loss of each run on the requests both built, and the decisions each
    run admitted. `honored` maps a critic-run pass id to its `requests_honored.json` record.

    With the taste on (SPEC § 17 Taste), `loss_base` maps each scored critic-run pass id to its loss without the
    taste checks, and the critic side of the comparison reads it, so both runs' losses hold the same checks; a
    scored pass absent from it raises.

    Only training passes count (held-out requests are the main run's readout). Absent data reads unread."""
    crit_rows = _training_scored(critic, held_out)
    by_round: dict[int, list[tuple[int, int]]] = defaultdict(list)
    unread: list[str] = []
    for p, _ in crit_rows:
        rec = honored.get(p.id)
        if rec is None:
            unread.append(p.id)
            continue
        by_round[p.round].append((int(rec["honored"]), int(rec["of"])))  # type: ignore[call-overload]
    rounds = tuple(
        HonoredRound(n, sum(h for h, _ in rows), sum(k for _, k in rows), len(rows),
                     sum(h for h, _ in rows) / sum(k for _, k in rows) if sum(k for _, k in rows) else None)
        for n, rows in sorted(by_round.items()))
    main_loss: dict[str, list[float]] = defaultdict(list)
    for p, e in _training_scored(main, held_out):
        main_loss[p.category].append(e.loss)
    crit_loss: dict[str, list[float]] = defaultdict(list)
    for p, e in crit_rows:
        if loss_base is None:
            crit_loss[p.category].append(e.loss)
        elif p.id in loss_base:
            crit_loss[p.category].append(loss_base[p.id])
        else:
            raise ValueError(f"{p.id}: scored on the critic run with the taste on, but no loss_base recorded")
    shared = sorted(set(main_loss) & set(crit_loss))
    beside = tuple(LossBeside(c, sum(main_loss[c]) / len(main_loss[c]), sum(crit_loss[c]) / len(crit_loss[c]),
                              len(main_loss[c]), len(crit_loss[c])) for c in shared)
    mean_main = sum(b.main for b in beside) / len(beside) if beside else None
    mean_critic = sum(b.critic for b in beside) / len(beside) if beside else None

    def admitted(j: Journal) -> tuple[tuple[str, str], ...]:
        return tuple((d.id, d.payload) for d in sorted(j.decisions(), key=lambda d: d.id)
                     if d.status is DecisionStatus.ADMITTED)

    return CriticReadings(
        rounds, tuple(sorted(unread)), beside, mean_main, mean_critic,
        None if mean_main is None or mean_critic is None else mean_critic - mean_main,
        admitted(main_journal), admitted(critic_journal))


def critic_lines(r: CriticReadings) -> list[str]:
    """The critic readings as printed lines."""
    out = ["Critic run (SPEC § 17), descriptive", "Requests honored per round:"]
    out += [f"  round {h.round}: {h.honored} of {h.requests} ({_fmt(h.share)}) over {h.passes} passes"
            for h in r.honored] or ["  unread"]
    if r.honored_unread:
        out.append(f"  passes with no record: {', '.join(r.honored_unread)}")
    out.append(f"Mean loss on {len(r.loss)} shared requests: main {_fmt(r.mean_main)}, critic {_fmt(r.mean_critic)}, "
               f"critic - main {_fmt(r.mean_diff)}")
    out += [f"  {b.category}: main {_fmt(b.main)} ({b.main_passes}), critic {_fmt(b.critic)} ({b.critic_passes})"
            for b in r.loss]
    out.append(f"Admitted, main run: {', '.join(i for i, _ in r.admitted_main) or 'none'}")
    out.append(f"Admitted, critic run: {', '.join(i for i, _ in r.admitted_critic) or 'none'}")
    return out


# ---------------------------------------------------------------------------------------------------------
# The taste readings (SPEC § 17 Taste): per planted quirk, whether the loop learned it and on what evidence


@dataclass(frozen=True)
class QuirkLearned:
    """One admitted decision whose exposure made the quirk's check pass more often on its exposed slots."""

    decision_id: str
    admitted_round: int
    pairs: int                    # in-scope exposure pairs of this decision with both slots read
    exposed_rate: float           # the quirk check's pass rate on the exposed slots
    unexposed_rate: float         # and on their partner slots
    n_at_admission: int           # the decision's exposure deltas in the Ledger up to its admission round
    n_in_scope: int               # of those, deltas on requests in the quirk's scope
    rounds_to_admission: int | None   # admission round - the quirk's first in-scope stream round + 1


@dataclass(frozen=True)
class QuirkReading:
    quirk: str
    scope: str
    stream_share: float | None    # in-scope requests / stream requests over the run's rounds
    in_scope_requests: int
    in_scope_passes: int          # scored passes on in-scope requests with a taste record
    first_round: int | None       # the first stream round with an in-scope request
    learned: tuple[QuirkLearned, ...]   # empty: not learned
    rate_before: float | None     # the check's pass rate over in-scope passes up to the first admission round
    rate_after: float | None      # and after it (None when not learned, or no in-scope pass followed)

    @property
    def is_learned(self) -> bool:
        return bool(self.learned)


@dataclass(frozen=True)
class TasteReadings:
    quirks: tuple[QuirkReading, ...]
    learned: int
    of: int
    unread: tuple[str, ...]       # scored in-scope passes with no taste record


def _rate(xs: Sequence[bool]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def taste_readings(
    quirks: Sequence[Any], cells: Mapping[str, tuple[str, str]], stream_rounds: Mapping[str, int],
    passes: PassStore, records: Mapping[str, Mapping[str, Any]], events: EventLog, ledger: Ledger,
    held_out: frozenset[str],
) -> TasteReadings:
    """The taste table: per quirk (an `app.taste.Quirk`), its scope's share of the stream, the passes in scope,
    the admitted decisions that taught it, their exposure evidence at admission, the rounds it took, and the
    quirk check's pass rate before and after.

    A decision taught a quirk when it was admitted by the gate and, over the in-scope exposure pairs of it, the
    quirk check passed on more exposed slots than unexposed ones. `records` maps a pass id to its `taste.json`.
    `stream_rounds` maps each stream request id of the run's rounds to its round. A quirk no decision taught reads
    not learned (no figure); a rate over no pass reads unread."""
    def check_of(rec: Mapping[str, Any], pass_id: str, quirk: str) -> bool:
        for r in rec["quirks"]:
            if r["quirk"] == quirk:
                return bool(r["passed"])
        raise ValueError(f"{pass_id}: in scope of {quirk} but its taste record does not read it")

    rows = _training_scored(passes, held_out)
    admitted: dict[str, int] = {}
    for f in events.flips():
        if f.new == DecisionStatus.ADMITTED.value and f.cause == FlipCause.GATE_ADMIT:
            admitted[f.subject_id] = min(f.round, admitted.get(f.subject_id, f.round))
    total = len(stream_rounds)
    out: list[QuirkReading] = []
    unread: set[str] = set()
    for q in quirks:
        scope_ids = {rid for rid in stream_rounds if q.covers(cells[rid])}
        mine: list[tuple[Pass, bool]] = []
        for p, _ in rows:
            if p.category not in scope_ids:
                continue
            rec = records.get(p.id)
            if rec is None:
                unread.add(p.id)
                continue
            mine.append((p, check_of(rec, p.id, q.id)))
        result = {p.id: ok for p, ok in mine}
        slots: dict[tuple[int, str], dict[int, list[Pass]]] = defaultdict(lambda: defaultdict(list))
        for p, _ in mine:
            if p.arm is Arm.JOURNAL and p.pair_slot is not None:
                slots[(p.round, p.category)][p.pair_slot].append(p)
        first = min((stream_rounds[rid] for rid in scope_ids), default=None)
        learned: list[QuirkLearned] = []
        for d, a in sorted(admitted.items(), key=lambda x: (x[1], x[0])):
            exp, une = [], []
            for by_slot in slots.values():
                if sorted(by_slot) != [0, 1] or any(len(v) != 1 for v in by_slot.values()):
                    continue
                x, y = by_slot[0][0], by_slot[1][0]
                if (x.composition.exposed == d) == (y.composition.exposed == d):
                    continue
                e_, u_ = (x, y) if x.composition.exposed == d else (y, x)
                exp.append(result[e_.id])
                une.append(result[u_.id])
            if not exp or sum(exp) <= sum(une):
                continue
            deltas = [x for x in ledger.deltas(d, DeltaKind.EXPOSURE) if x.round <= a]
            learned.append(QuirkLearned(
                d, a, len(exp), sum(exp) / len(exp), sum(une) / len(une), len(deltas),
                sum(x.category in scope_ids for x in deltas), None if first is None else a - first + 1))
        if learned:
            at = learned[0].admitted_round
            before = _rate([ok for p, ok in mine if p.round <= at])
            after = _rate([ok for p, ok in mine if p.round > at])
        else:
            before, after = _rate([ok for _, ok in mine]), None
        out.append(QuirkReading(q.id, q.scope_text, len(scope_ids) / total if total else None, len(scope_ids),
                                len(mine), first, tuple(learned), before, after))
    return TasteReadings(tuple(out), sum(r.is_learned for r in out), len(out), tuple(sorted(unread)))


def taste_lines(t: TasteReadings) -> list[str]:
    """The taste table as printed lines, then the summary line ordered from the densest scope to the sparsest."""
    out = ["Taste (SPEC § 17): planted quirks the loop learns from the eval alone"]
    for r in t.quirks:
        head = (f"  {r.quirk} [{r.scope}]: stream share {_fmt(r.stream_share, 2)} ({r.in_scope_requests} requests), "
                f"{r.in_scope_passes} passes, check pass rate before {_fmt(r.rate_before, 2)}, "
                f"after {_fmt(r.rate_after, 2)}")
        out.append(head)
        if not r.learned:
            out.append("    not learned")
        for x in r.learned:
            out.append(f"    learned by {x.decision_id} at round {x.admitted_round} "
                       f"({'unread' if x.rounds_to_admission is None else x.rounds_to_admission} rounds in scope): "
                       f"n {x.n_at_admission} at admission, {x.n_in_scope} in scope; check passes on "
                       f"{_fmt(x.exposed_rate, 2)} exposed vs {_fmt(x.unexposed_rate, 2)} unexposed slots "
                       f"over {x.pairs} pairs")
    if t.unread:
        out.append(f"  in-scope passes with no taste record: {', '.join(t.unread)}")
    dense = sorted(t.quirks, key=lambda r: (-(r.stream_share or 0.0), r.quirk))
    by_density = ", ".join(
        f"{r.quirk} ({_fmt(r.stream_share, 2)}) "
        + (f"n {r.learned[0].n_at_admission} in {r.learned[0].rounds_to_admission} rounds" if r.learned
           else "not learned")
        for r in dense)
    out.append(f"learned {t.learned} of {t.of} quirks; n at admission by density: {by_density}")
    return out
