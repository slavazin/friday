"""The demo's JSON export (SPEC § 13): one document the gallery, the timeline and the readout render from.

Every figure is recomputed from the stores on each build (e-process wealth from the Ledger rows, lens
values from the lens answers and losses); nothing is copied from a stored summary. The field list is
`docs/export-schema.md`. Ordering is deterministic: lists are sorted by id (passes by round, then id),
and `dumps` sorts keys.
"""

from __future__ import annotations

import dataclasses
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from friday.algorithms.lens_curation import high_loss_labels
from friday.app.readout import ReadoutStores, held_out_ids, readings, scored, training_passes
from friday.config import RunSpec
from friday.domain.events import StatusFlip
from friday.domain.types import Arm, DecisionStatus, DeltaKind, EvalReport, ObservationStatus, Pass
from friday.ports.goals import GoalSource
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
from friday.stats.eprocess import BETTING, mirror_wealth, wealth, wealth_path
from friday.stats.ranks import LENS_VALUES

SCHEMA_VERSION = 1
TOP_LEVEL = ("schema_version", "run", "passes", "decisions", "lenses", "pools", "rounds", "git_log", "readout")


@dataclass(frozen=True)
class ExportStores:
    """The run stores the export reads besides the Journal and the lens set."""

    passes: PassStore
    goals: GoalSource
    judge: JudgeStore
    observations: ObservationStore
    pools: PoolStore
    ledger: Ledger
    events: EventLog
    spend: SpendLog
    composed_tokens: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str


def jsonable(x: Any) -> Any:
    """`x` as JSON-ready data: dataclasses to dicts, enums to values, tuples and sets to lists.
    Raises TypeError on anything else that `json` cannot write."""
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        return {f.name: jsonable(getattr(x, f.name)) for f in dataclasses.fields(x)}
    if isinstance(x, Enum):
        return x.value
    if isinstance(x, Mapping):
        return {str(jsonable(k)): jsonable(v) for k, v in x.items()}
    if isinstance(x, (frozenset, set)):
        return sorted(jsonable(v) for v in x)
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    if x is None or isinstance(x, (bool, int, float, str)):
        return x
    raise TypeError(f"not exportable: {type(x).__name__}")


def _flip(f: StatusFlip) -> dict[str, Any]:
    return {"round": f.round, "old": f.old, "new": f.new, "cause": jsonable(f.cause),
            "evidence": jsonable(f.evidence), "successors": list(f.successors)}


def _pass_row(p: Pass, report: EvalReport | None, readout: bool) -> dict[str, Any]:
    parse = report.how_to[0].parse if report is not None and report.how_to else None
    c = p.composition
    return {
        "id": p.id, "round": p.round, "category": p.category, "arm": p.arm.value, "regime": p.regime.value,
        "readout": readout, "worker": p.worker, "artifact_path": p.artifact_path,
        "decisions_composed": list(c.decision_ids), "exposed": c.exposed, "ablated": c.ablated,
        "notes": c.notes, "pair_slot": p.pair_slot, "repaired": p.repaired, "transport_failed": p.transport_failed,
        "loss": None if report is None else report.loss, "Q": None if report is None else report.Q,
        "how_to_summary": None if parse is None else parse.summary,
        "how_to_rung": None if parse is None else int(parse.rung),
    }


def _account(rows: Sequence[Any]) -> dict[str, Any]:
    ds = [r.d for r in rows]
    return {"n": len(ds), "mean_d": sum(ds) / len(ds) if ds else None,
            "deltas": [{"round": r.round, "category": r.category, "d": r.d, "dQ": r.dQ} for r in rows]}


def _decision_rows(journal: Journal, stores: ExportStores, spec: RunSpec) -> list[dict[str, Any]]:
    betting = BETTING.build(spec.gate.betting.name, **spec.gate.betting.params)
    out = []
    for d in sorted(journal.decisions(), key=lambda d: d.id):
        flips = stores.events.flips(d.id)
        exposure = list(stores.ledger.deltas(d.id, DeltaKind.EXPOSURE))
        ablation = list(stores.ledger.deltas(d.id, DeltaKind.ABLATION))
        exp = _account(exposure)
        exp |= {"wealth": wealth([r.d for r in exposure], betting),
                "mirror_wealth": mirror_wealth([r.d for r in exposure], betting),
                "wealth_path": wealth_path([r.d for r in exposure], betting)}
        admitted_at = [f.round for f in flips if f.new == DecisionStatus.ADMITTED.value]
        since = admitted_at[-1] if admitted_at else None
        fresh = [r.d for r in ablation if since is not None and r.round > since]
        abl = _account(ablation)
        abl |= {"harm_wealth_since_admitted": None if since is None else mirror_wealth(fresh, betting),
                "n_since_admitted": None if since is None else len(fresh)}
        dispositions = Counter(f.disposition.value for f in stores.judge.fires_of(d.id))
        out.append({
            "id": d.id, "status": d.status.value, "pool_id": d.pool_id,
            "fires_when": sorted(d.hook.fires_when), "not_this": sorted(d.hook.not_this), "payload": d.payload,
            "supersedes": list(d.supersedes), "successors": list(d.successors), "drafted_round": d.drafted_round,
            "status_history": [_flip(f) for f in flips],
            "ledger": {"exposure": exp, "ablation": abl},
            "fires": dict(sorted(dispositions.items())),
        })
    return out


def lens_value_history(
    lens_ids: Sequence[str], training: Sequence[tuple[Pass, EvalReport]], judge: JudgeStore, spec: RunSpec,
) -> dict[str, list[dict[str, Any]]]:
    """Per lens, per training round: its value over the last `lens.window` scored journal-arm training passes
    up to that round (SPEC § 6 value), or None below `lens.n_min` answered passes."""
    scorer = LENS_VALUES.build(spec.lens.value)
    journal_rows = sorted(((p, e) for p, e in training if p.arm is Arm.JOURNAL), key=lambda t: (t[0].round, t[0].id))
    answers: dict[tuple[str, str], float] = {}
    for a in judge.lens_answers([p.id for p, _ in journal_rows]):
        key = (a.pass_id, a.lens_id)
        if key in answers:
            raise ValueError(f"two lens answers for pass {a.pass_id}, lens {a.lens_id}")
        answers[key] = a.p
    history: dict[str, list[dict[str, Any]]] = {lid: [] for lid in lens_ids}
    for r in sorted({p.round for p, _ in journal_rows}):
        window = [(p, e) for p, e in journal_rows if p.round <= r][-spec.lens.window:]
        label_of = dict(zip((p.id for p, _ in window), high_loss_labels([e.loss for _, e in window]), strict=True))
        for lid in lens_ids:
            ans = [(answers[(p.id, lid)], label_of[p.id]) for p, _ in window if (p.id, lid) in answers]
            value = scorer.score([a for a, _ in ans], [y for _, y in ans]) if len(ans) >= spec.lens.n_min else None
            history[lid].append({"round": r, "value": value, "n": len(ans)})
    return history


def _round_rows(training: Sequence[Pass], scored_rows: Sequence[tuple[Pass, EvalReport]],
                events: EventLog) -> list[dict[str, Any]]:
    """Per training round: pass counts, the § 12 alongside reading (plain journal-arm vs notes-arm mean loss)
    and the round close's status flips."""
    flips_by_round: dict[int, list[StatusFlip]] = {}
    for f in events.flips():
        flips_by_round.setdefault(f.round, []).append(f)
    out = []
    for r in sorted({p.round for p in training} | set(flips_by_round)):
        ps = [p for p in training if p.round == r]
        rows = [(p, e) for p, e in scored_rows if p.round == r]
        plain = [e.loss for p, e in rows if p.arm is Arm.JOURNAL
                 and p.composition.exposed is None and p.composition.ablated is None]
        notes = [e.loss for p, e in rows if p.arm is Arm.NOTES]
        out.append({
            "round": r, "passes": len(ps), "transport_failed": sum(p.transport_failed for p in ps),
            "journal_plain": {"n": len(plain), "mean_loss": sum(plain) / len(plain) if plain else None},
            "notes": {"n": len(notes), "mean_loss": sum(notes) / len(notes) if notes else None},
            "flips": [{"subject_id": f.subject_id, **_flip(f)} for f in flips_by_round.get(r, [])],
        })
    return out


def build(
    stores: ExportStores, journal: Journal, lens_set: LensSet, git_log: Sequence[Commit], spec: RunSpec,
) -> dict[str, Any]:
    """The export document (JSON-ready). Carries no prompt, key or environment value."""
    held = held_out_ids(stores.goals)
    all_passes = sorted(stores.passes.passes(), key=lambda p: (p.round, p.id))
    training = training_passes(stores.passes, held)
    training_scored = scored(stores.passes, training)
    lenses = sorted(lens_set.lenses(), key=lambda x: x.id)
    values = lens_value_history([x.id for x in lenses], training_scored, stores.judge, spec)
    observations = stores.observations.observations()
    readout = readings(ReadoutStores(stores.passes, stores.goals, journal, lens_set, stores.events,
                                     stores.ledger, stores.spend, stores.composed_tokens), spec)
    doc = {
        "schema_version": SCHEMA_VERSION,
        "run": {"spec_hash": spec.spec_hash, "seed": spec.run.seed, "rounds": spec.run.rounds,
                "drift_round": spec.drift.round, "readout_regime": spec.readout.regime,
                "readout_reps": spec.readout.reps, "spend_usd": stores.spend.total_usd()},
        "passes": [_pass_row(p, stores.passes.eval_of(p.id), p.category in held) for p in all_passes],
        "decisions": _decision_rows(journal, stores, spec),
        "lenses": [{
            "id": x.id, "angle": x.angle, "counterfactual": x.counterfactual, "status": x.status.value,
            "minted_round": x.minted_round, "superseded_by": x.superseded_by,
            "has_cleared_floor": x.has_cleared_floor,
            "status_history": [_flip(f) for f in stores.events.flips(x.id)],
            "value_history": values[x.id],
        } for x in lenses],
        "pools": [{
            **jsonable(pool),
            "observations": sum(o.pool_id == pool.id for o in observations),
            "open_observations": sum(o.pool_id == pool.id and o.status is ObservationStatus.OPEN for o in observations),
        } for pool in sorted(stores.pools.pools(), key=lambda p: p.id)],
        "rounds": _round_rows(training, training_scored, stores.events),
        "git_log": [{"sha": c.sha, "subject": c.subject} for c in git_log],
        "readout": jsonable(readout),
    }
    if tuple(doc) != TOP_LEVEL:
        raise RuntimeError(f"export fields {tuple(doc)} differ from TOP_LEVEL")
    return doc


def dumps(doc: Mapping[str, Any]) -> str:
    """The export as JSON text with sorted keys; raises on a non-finite float."""
    return json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
