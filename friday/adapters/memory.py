"""In-memory stores implementing the storage ports. Used by tests and by dry runs without a database."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from friday.domain.events import StatusFlip, Steer
from friday.domain.lifecycle import check_decision_flip
from friday.domain.types import (
    Decision,
    DecisionStatus,
    EvalReport,
    Fire,
    LedgerDelta,
    LensAnswer,
    Observation,
    PairPlan,
    Pass,
    Pool,
    Request,
    TagGloss,
)
from friday.ports.models import ChatResult


@dataclass
class MemoryPassStore:
    _passes: dict[str, Pass] = field(default_factory=dict)
    _evals: dict[str, EvalReport] = field(default_factory=dict)

    def add_pass(self, pass_: Pass) -> None:
        if pass_.id in self._passes:
            raise ValueError(f"pass {pass_.id} already recorded")
        self._passes[pass_.id] = pass_

    def add_eval(self, report: EvalReport) -> None:
        self._evals[report.pass_id] = report

    def passes(self, round_: int | None = None) -> Sequence[Pass]:
        return [p for p in self._passes.values() if round_ is None or p.round == round_]

    def eval_of(self, pass_id: str) -> EvalReport | None:
        return self._evals.get(pass_id)


@dataclass
class MemoryJudgeStore:
    _answers: list[LensAnswer] = field(default_factory=list)
    _fires: list[Fire] = field(default_factory=list)

    def add_lens_answers(self, answers: Sequence[LensAnswer]) -> None:
        self._answers.extend(answers)

    def add_fires(self, fires: Sequence[Fire]) -> None:
        self._fires.extend(fires)

    def lens_answers(self, pass_ids: Sequence[str]) -> Sequence[LensAnswer]:
        want = set(pass_ids)
        return [a for a in self._answers if a.pass_id in want]

    def fires_of(self, decision_id: str) -> Sequence[Fire]:
        return [f for f in self._fires if f.decision_id == decision_id]


@dataclass
class MemoryObservationStore:
    _obs: dict[str, Observation] = field(default_factory=dict)

    def add_observations(self, observations: Sequence[Observation]) -> None:
        for o in observations:
            if o.id in self._obs:
                raise ValueError(f"observation {o.id} already recorded")
            self._obs[o.id] = o

    def put_observation(self, observation: Observation) -> None:
        self._obs[observation.id] = observation

    def observations(self) -> Sequence[Observation]:
        return list(self._obs.values())


@dataclass
class MemoryPoolStore:
    _pools: dict[str, Pool] = field(default_factory=dict)

    def put_pool(self, pool: Pool) -> None:
        self._pools[pool.id] = pool

    def pools(self) -> Sequence[Pool]:
        return list(self._pools.values())


@dataclass
class MemoryExposureStore:
    _plans: dict[int, list[PairPlan]] = field(default_factory=dict)

    def add_plans(self, round_: int, plans: Sequence[PairPlan]) -> None:
        if round_ in self._plans:
            raise ValueError(f"round {round_} plans already recorded")
        self._plans[round_] = list(plans)

    def plans(self, round_: int) -> Sequence[PairPlan]:
        return list(self._plans.get(round_, []))


@dataclass
class MemoryGoalSource:
    """A GoalSource over requests held in memory, grouped by their `round` field."""

    requests: list[Request] = field(default_factory=list)

    def round(self, n: int) -> Sequence[Request]:
        return [r for r in self.requests if r.round == n and not r.held_out]

    def held_out(self) -> Sequence[Request]:
        return [r for r in self.requests if r.held_out]


@dataclass
class MemoryTagVocabulary:
    _glosses: list[TagGloss] = field(default_factory=list)
    _tags: dict[str, frozenset[str]] = field(default_factory=dict)

    def glosses(self) -> Sequence[TagGloss]:
        return list(self._glosses)

    def add(self, gloss: TagGloss) -> None:
        if any(g.tag == gloss.tag for g in self._glosses):
            raise ValueError(f"tag {gloss.tag!r} exists; tags are never renamed or merged")
        self._glosses.append(gloss)

    def tags_of(self, request_id: str) -> frozenset[str] | None:
        return self._tags.get(request_id)

    def set_tags(self, tags: Mapping[str, frozenset[str]]) -> None:
        clash = sorted(set(tags) & set(self._tags))
        if clash:
            raise ValueError(f"requests already tagged: {clash}")
        self._tags.update(tags)


@dataclass
class MemoryLedger:
    _rows: list[LedgerDelta] = field(default_factory=list)

    def append(self, delta: LedgerDelta) -> None:
        self._rows.append(delta)

    def deltas(self, decision_id: str, kind: str | None = None) -> Sequence[LedgerDelta]:
        return [r for r in self._rows if r.decision_id == decision_id and (kind is None or r.kind == kind)]


@dataclass
class MemoryEventLog:
    _flips: list[StatusFlip] = field(default_factory=list)
    _steers: list[tuple[Steer, int | None]] = field(default_factory=list)

    def append_flip(self, flip: StatusFlip) -> None:
        self._flips.append(flip)

    def append_steer(self, steer: Steer) -> None:
        self._steers.append((steer, None))

    def flips(self, subject_id: str | None = None) -> Sequence[StatusFlip]:
        return [f for f in self._flips if subject_id is None or f.subject_id == subject_id]

    def pending_steers(self) -> Sequence[Steer]:
        return [s for s, applied in self._steers if applied is None]

    def mark_steer_applied(self, steer: Steer, round_: int) -> None:
        self._steers = [(s, round_ if s == steer and a is None else a) for s, a in self._steers]


@dataclass
class MemorySpendLog:
    rows: list[tuple[str, str, ChatResult | None, bool, str]] = field(default_factory=list)

    def record(self, role: str, tag: str, result: ChatResult | None, ok: bool, error: str = "") -> None:
        self.rows.append((role, tag, result, ok, error))

    def total_usd(self) -> float:
        return sum((r[2].cost_usd or 0.0) for r in self.rows if r[2] is not None)


@dataclass
class MemoryJournal:
    _decisions: dict[str, Decision] = field(default_factory=dict)
    commits: list[str] = field(default_factory=list)

    def decisions(self) -> Sequence[Decision]:
        return [self._decisions[k] for k in sorted(self._decisions)]

    def get(self, decision_id: str) -> Decision:
        return self._decisions[decision_id]

    def draft(self, decision: Decision) -> None:
        if decision.id in self._decisions:
            raise ValueError(f"{decision.id} already drafted")
        if decision.status is not DecisionStatus.CANDIDATE:
            raise ValueError("a decision is drafted as a candidate")
        self._decisions[decision.id] = decision

    def flip(self, flip: StatusFlip, message: str) -> Decision:
        old = self._decisions[flip.subject_id]
        new_status = DecisionStatus(flip.new)
        check_decision_flip(old.status, new_status, flip.cause)
        new = dataclasses.replace(
            old, status=new_status, successors=tuple(sorted(set(old.successors) | set(flip.successors)))
        )
        self._decisions[new.id] = new
        self.commits.append(message)
        return new
