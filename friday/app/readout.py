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
from friday.ports.goals import GoalSource
from friday.ports.stores import EventLog, Journal, Ledger, LensSet, PassStore, SpendLog
from friday.stats.significance import PAIRED_TESTS

# SPEC § 2 claim language: the pitch word each reading earns.
IMPROVES = "improves"
UNRESOLVED = "unresolved"
UNEARNED = "unearned"
LEANER = "leaner than shared notes"
FORGETS = "forgets what went stale"
JUDGE_LEARNS = "the judge learns what to look at"

# Retirements that count as earned forgetting (SPEC § 1 goal 3: "retired by evidence"). A steered
# retirement is reported beside them, never inside the count (SPEC § 11, the steer).
_EARNED_RETIRE = frozenset({FlipCause.DETECT_RETIRE.value, FlipCause.SUPERSEDE.value})


@dataclass(frozen=True)
class ReadoutStores:
    """The stores the readout reads. `composed_tokens` maps a pass id to the tokens composed into its
    worker prompt (decisions and notes), as recorded by the pass pipeline; a pass absent from it is unread."""

    passes: PassStore
    goals: GoalSource
    journal: Journal
    lenses: LensSet
    events: EventLog
    ledger: Ledger
    spend: SpendLog
    composed_tokens: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class PairedClaim:
    """R1 or R2: per held-out category the mean over reps of loss(baseline) - loss(journal), then the
    registered one-sided test and the two-sided t-interval over the category means."""

    reading: str
    baseline: str
    population: str | None
    n_categories: int | None
    reps: Mapping[str, tuple[int, int]]           # arm -> (min, max) scored reps per category read
    unread_categories: tuple[str, ...]            # held-out categories missing from either arm
    per_category: Mapping[str, float]
    mean: float | None
    p: float | None
    interval: tuple[float, float] | None
    level: float
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
    spec: RunSpec, verdict_of,
) -> PairedClaim:
    level = spec.readout.interval_level
    base_rows, jour_rows = rows.get(baseline, []), rows.get(Arm.JOURNAL, [])
    common = {c for c, _ in base_rows} & {c for c, _ in jour_rows}
    unread = tuple(sorted(held_out - common))
    if not common:
        return PairedClaim(reading, baseline.value, None, None, {}, unread, {}, None, None, None, level, None)
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
                       r.mean, r.p, r.interval, level, verdict_of(r))


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
    """Held-out requests on which >= 1 S* decision fires, and the categories whose journal passes composed one."""
    if not held:
        return HookTransfer(None, (), (), ())
    s_star = sorted((d for d in stores.journal.decisions() if d.status in COMPOSABLE_DECISION), key=lambda d: d.id)
    fired = tuple(sorted(r.id for r in held if any(fires(d.hook, r.tags) for d in s_star)))
    composed = tuple(sorted({p.category for p in journal_rows if p.composition.decision_ids}))
    population = f"{len(held)} held-out requests, {len(s_star)} decisions in S*"
    return HookTransfer(population, tuple(d.id for d in s_star), fired, composed)


def _tokens(arm_passes: Sequence[Pass], composed: Mapping[str, int]) -> ArmMean:
    """Mean tokens composed per pass; unread when any of the arm's passes has no recorded count."""
    ps = [p for p in arm_passes if not p.transport_failed]
    if not ps or any(p.id not in composed for p in ps):
        return ArmMean(len(ps), None)
    return _mean([float(composed[p.id]) for p in ps])


def readings(stores: ReadoutStores, spec: RunSpec) -> Readout:
    """Every pre-registered and descriptive reading of SPEC § 12, computed from the stores."""
    held = sorted(stores.goals.held_out(), key=lambda r: r.id)
    held_ids = frozenset(r.id for r in held)
    regime = Regime(spec.readout.regime).value
    v = readout_passes(stores.passes, held_ids, regime)
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
        # Earned when the interval does not lie (wholly) below 0 and the journal arm composes fewer tokens.
        if r.interval is None:
            return UNRESOLVED
        t_j, t_n = tokens[Arm.JOURNAL.value].mean, tokens[Arm.NOTES.value].mean
        leaner = t_j is not None and t_n is not None and t_j < t_n
        return LEANER if r.interval[1] >= 0 and leaner else UNEARNED

    r1 = _paired("R1", Arm.EMPTY, loss_rows, held_ids, spec, r1_verdict)
    r2 = _paired("R2", Arm.NOTES, loss_rows, held_ids, spec, r2_verdict)
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


def _fmt(x: float | None, digits: int = 3) -> str:
    return "unread" if x is None else f"{x:.{digits}f}"


def lines(r: Readout) -> list[str]:
    """One line per reading, each with its population and its interval or p; failed readings included."""
    out = [f"Readout over {r.population}"]
    for c in (r.r1, r.r2):
        if c.verdict is None:
            out.append(f"{c.reading} loss({c.baseline}) - loss(journal): unread (no category scored in both arms)")
            continue
        iv = "n/a" if c.interval is None else f"[{c.interval[0]:.3f}, {c.interval[1]:.3f}]"
        out.append(f"{c.reading} loss({c.baseline}) - loss(journal) over {c.population}: mean {_fmt(c.mean)}, "
                   f"p {_fmt(c.p, 4)}, {int(c.level * 100)} % interval {iv}: {c.verdict}")
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
    out.append("Hook transfer: unread (no held-out set)" if h.population is None
               else f"Hook transfer over {h.population}: fires on {len(h.requests_fired)}")
    return out
