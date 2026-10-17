"""Shared domain types: the seams every module codes against (SPEC Appendix B).

Pure data. No I/O, no model calls, no statistics. Names follow the SPEC § 2 vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Regime(StrEnum):
    R1 = "R1"
    R2 = "R2"


class Arm(StrEnum):
    JOURNAL = "journal"
    NOTES = "notes"
    EMPTY = "empty"


class DecisionStatus(StrEnum):
    CANDIDATE = "candidate"
    ADMITTED = "admitted"
    SUSPECT = "suspect"
    RETIRED = "retired"
    EXPIRED = "expired"
    REFUSED = "refused"


class LensStatus(StrEnum):
    HIDDEN = "hidden"          # minted; answered and valued, its observations do not pool yet
    ACTIVE = "active"
    RETIRED = "retired"
    SUPERSEDED = "superseded"  # folded into another lens


class ObservationStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"          # consumed as a warrant, or expired. Observations routed to a refused pool after
                               # its refusal stay open: they are what can re-nominate it.


class PoolStatus(StrEnum):
    OPEN = "open"
    NOMINATED = "nominated"    # a candidate drafted from it is in flight
    REFUSED = "refused"        # its candidate expired or was refused (the refused-pool block)
    MERGED = "merged"


class Disposition(StrEnum):
    APPLIED = "applied"
    NOT_APPLICABLE = "not-applicable"
    CONTRADICTED = "contradicted"
    UNDISPOSED = "undisposed"  # the report call was dropped; the fire has no verdict


class DeltaKind(StrEnum):
    EXPOSURE = "exposure"
    ABLATION = "ablation"


FREE_LENS = "free"


@dataclass(frozen=True)
class Check:
    """One acceptance check in the check DSL (SPEC § 4)."""

    id: str
    steps: tuple[dict[str, Any], ...]
    expect: dict[str, Any]


@dataclass(frozen=True)
class Request:
    id: str
    text: str
    tags: frozenset[str]
    contract: dict[str, str]                       # element id -> its role in one phrase
    checks: dict[Regime, tuple[Check, ...]]
    held_out: bool = False

    def checks_for(self, regime: Regime) -> tuple[Check, ...]:
        return self.checks[regime]


@dataclass(frozen=True)
class Hook:
    """A decision's activation: fires iff the request carries a `fires_when` tag and no `not_this` tag."""

    fires_when: frozenset[str]
    not_this: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Decision:
    """One Journal entry. Text (hook, payload) is frozen from drafting; only `status` flips.

    Lineage is a DAG: a decision may supersede several predecessors and have several successors.
    """

    id: str
    status: DecisionStatus
    pool_id: str | None
    hook: Hook
    payload: str
    warrant: dict[str, Any] = field(default_factory=dict)
    supersedes: tuple[str, ...] = ()
    successors: tuple[str, ...] = ()
    drafted_round: int = 0
    readings: dict[str, Any] = field(default_factory=dict)  # e.g. hedge words found at drafting


@dataclass(frozen=True)
class Composition:
    """What one worker's prompt is composed from (SPEC § 2 composition)."""

    request_id: str
    decision_ids: tuple[str, ...]           # composed decisions, the exposed candidate included
    exposed: str | None = None              # candidate under test given to this worker
    ablated: str | None = None              # admitted decision omitted from this worker
    notes: bool = False                     # notes-arm: the notes file is composed


@dataclass(frozen=True)
class Pass:
    id: str
    round: int
    category: str
    arm: Arm
    worker: str                             # model string the service echoed
    composition: Composition
    regime: Regime
    artifact_path: str | None = None
    repaired: bool = False
    transport_failed: bool = False
    pair_slot: int | None = None            # 0 or 1 within a journal-arm pair; None otherwise


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    passed: bool
    failing_step: str = ""


@dataclass(frozen=True)
class EvalReport:
    pass_id: str
    regime: Regime
    checks: tuple[CheckResult, ...]
    console_errors: tuple[str, ...]
    A: float
    R: float
    rubric: tuple[int, ...]
    Q: int
    loss: float
    eslint_errors: int = 0
    axe_violations: int = 0


@dataclass(frozen=True)
class Lens:
    id: str
    angle: str
    counterfactual: str
    status: LensStatus
    minted_round: int | None = None         # None for a baseline lens
    superseded_by: str | None = None
    has_cleared_floor: bool = False


@dataclass(frozen=True)
class LensAnswer:
    pass_id: str
    lens_id: str
    p: float
    quote: str
    judge_model: str = ""                   # lens value is read per judge model string


@dataclass(frozen=True)
class Observation:
    id: str
    pass_id: str
    round: int
    lens_id: str                            # a lens id or FREE_LENS
    category: str
    shape: str
    direction: str
    quote: str
    status: ObservationStatus = ObservationStatus.OPEN
    pool_id: str | None = None
    direction_label: str | None = None


@dataclass(frozen=True)
class Pool:
    id: str
    lens_id: str
    shape: str
    direction_labels: tuple[str, ...]
    status: PoolStatus = PoolStatus.OPEN
    merged_into: str | None = None
    refused_at_round: int | None = None


@dataclass(frozen=True)
class LedgerDelta:
    """One paired delta. `d = loss(without) - loss(with)`; `dQ = Q(with) - Q(without)`."""

    decision_id: str
    round: int
    category: str
    kind: DeltaKind
    d: float
    dQ: float


@dataclass(frozen=True)
class Fire:
    """One composed decision on one pass, disposed by the judge's report call."""

    pass_id: str
    decision_id: str
    round: int
    disposition: Disposition = Disposition.UNDISPOSED
    quote: str = ""
