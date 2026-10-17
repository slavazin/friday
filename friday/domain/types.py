"""Shared domain types: the seams every module codes against (SPEC Appendix B).

Pure data. No I/O, no model calls, no statistics. Names follow the SPEC § 2 vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
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
    tags: frozenset[str]                           # given by the tagger at round start; empty before
    contract: dict[str, str]                       # element id (with '#') -> its role in one phrase
    checks: dict[Regime, tuple[Check, ...]]
    held_out: bool = False
    round: int | None = None                       # the goal stream position; None when held out
    cell: tuple[str, str] | None = None            # (goal family, interaction mode); generator-side, never shown

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


Viewport = tuple[int, int]


class ControlKind(StrEnum):
    BUTTON = "button"
    TEXT = "text"          # input that takes typed text, textarea, contenteditable
    SELECT = "select"
    CHECKBOX = "checkbox"  # checkbox, radio, range
    CANVAS = "canvas"
    OTHER = "other"        # any other element with a tab index or a click handler


@dataclass(frozen=True)
class Control:
    """One operable element the probe suite found on a loaded page."""

    selector: str                 # a selector the Page port resolves to exactly this element
    kind: ControlKind
    element_id: str | None = None # its id with '#', when it has one


@dataclass(frozen=True)
class ProbeReading:
    """The probe suite at one viewport (SPEC § 5). P = mean(loaded, no console error, responsive share)."""

    pass_id: str
    viewport: Viewport
    loaded: bool
    console_errors: tuple[str, ...]
    controls: int                       # controls operated (at most probe.max_controls)
    responsive: int
    responsive_ids: tuple[str, ...]     # '#id' of responsive controls that have one
    silent: int                         # responsive controls the how-to does not declare
    P: float


class HowToRung(IntEnum):
    """The repair-chain rung the how-to JSON was read at (SPEC § 5)."""

    STRICT = 1
    REPAIRED = 2
    SALVAGED = 3
    NONE = 4


@dataclass(frozen=True)
class HowToParse:
    """The how-to read from an artifact's source, before any browser runs it. Pure."""

    rung: HowToRung
    declared: tuple[Check, ...]         # one acceptance check per valid declared control
    invalid_entries: int                # entries that decoded but are not valid DSL (count as failed)
    declared_ids: tuple[str, ...]       # '#id' named by the declared controls
    undeclared_contract_ids: tuple[str, ...]
    summary: str = ""


@dataclass(frozen=True)
class HowToReading:
    """The how-to check at one viewport. H = passed share x contract coverage x repair factor."""

    pass_id: str
    viewport: Viewport
    parse: HowToParse
    results: tuple[CheckResult, ...]
    H: float


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
    P: float = 0.0
    H: float = 0.0
    C: float = 0.0
    probes: tuple[ProbeReading, ...] = ()
    how_to: tuple[HowToReading, ...] = ()
    silent_controls: int = 0


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


class PairKind(StrEnum):
    EXPOSURE = "exposure"
    ABLATION = "ablation"
    NONE = "none"


@dataclass(frozen=True)
class PairPlan:
    """What one round's pair of a category carries (SPEC § 8)."""

    category: str
    kind: PairKind
    decision_id: str | None = None
    treated_slot: int = 0       # the slot that receives (exposure) or keeps (ablation) the decision


@dataclass(frozen=True)
class TagGloss:
    """One tag of the tag vocabulary, with its one-line gloss and the round it was minted."""

    tag: str
    gloss: str
    minted_round: int


@dataclass(frozen=True)
class Fire:
    """One composed decision on one pass, disposed by the judge's report call."""

    pass_id: str
    decision_id: str
    round: int
    disposition: Disposition = Disposition.UNDISPOSED
    quote: str = ""
