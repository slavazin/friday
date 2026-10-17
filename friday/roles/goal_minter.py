"""The goal minter (SPEC § 4): mints the goal stream and the held-out set before round 1, with contracts,
acceptance checks and contract stubs, each check validated to fail on its stub.

The family of every position is drawn up front under the run seed, so the family sequence never depends on
how many model calls failed or were retried. The check DSL and the stub run are injected (`dsl_check`,
`stub_reading`): this module touches neither the eval nor a browser.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from friday.domain.types import Check, Regime, Request
from friday.ports.models import Message, ModelClient
from friday.roles.ask import Rejected, ask_json
from friday.roles.prompts import PromptBook
from friday.roles.tagger import names_family
from friday.stats.rng import derive

ROLE = "goal_minter"

CONTRACT_ID = re.compile(r"^#[A-Za-z][A-Za-z0-9_-]*$")
SELECTOR_ID = re.compile(r"#([A-Za-z][A-Za-z0-9_-]*)")
SELECTOR_STEPS = ("click", "type", "focus")
ELEMENT_KINDS = ("button", "input", "textarea", "select", "checkbox", "canvas", "display")


class MintFailed(RuntimeError):
    """A position for which no request survived drafting and check validation. Raised by `mint_requests`
    after every other position was tried; `partial` holds what was minted, `failed` the positions that
    were not."""

    def __init__(self, message: str, partial: MintedGoals | None = None, failed: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.partial = partial
        self.failed = list(failed)


@dataclass(frozen=True)
class MintParams:
    """The run.yml figures the goal minter reads, passed in by the composition root."""

    seed: int
    rounds: int
    per_round: int
    held_out: int
    drift_round: int
    families: tuple[str, ...]
    mix: Mapping[str, tuple[float, ...]]
    checks_per_regime: tuple[int, int]
    check_regenerations: int
    request_regenerations: int
    constraint: str
    test_query: str
    max_wait_ms: int
    reasks: int
    expect_verbs: frozenset[str]    # the check DSL's expectation verbs (the eval owns the table)


@dataclass(frozen=True)
class Slot:
    """One position to mint: request id, stream round (None when held out), position, regime, family."""

    id: str
    round: int | None
    pos: int
    regime: Regime
    family: str


@dataclass(frozen=True)
class StubReading:
    """One check run on its contract stub at every viewport of its regime."""

    passed: bool          # True when the check passed at any viewport: it does not read behaviour
    detail: str = ""      # the step that failed before the expectation, per viewport, when one did


@dataclass(frozen=True)
class Draft:
    text: str             # the drafted request text, without the appended constraint
    contract: dict[str, str]
    checks: tuple[Check, ...]
    timed: bool


@dataclass
class MintedGoals:
    """What one minting run writes: the stream, the held-out set, a stub per request, and a report row for
    every check regenerated, dropped or failing on its stub before its expectation (never silently kept)."""

    stream: list[Request] = field(default_factory=list)
    held_out: list[Request] = field(default_factory=list)
    stubs: dict[str, str] = field(default_factory=dict)
    report: list[dict[str, str]] = field(default_factory=list)

    def note(self, request_id: str, check_id: str, event: str, detail: str = "") -> None:
        self.report.append({"request": request_id, "check": check_id, "event": event, "detail": detail})


@dataclass(frozen=True)
class MintedSlot:
    """One position as minted: its request, family, drafted text, stub and the report rows it produced."""

    request: Request
    family: str
    draft_text: str
    stub: str
    report: tuple[dict[str, str], ...]


class MintJournal(Protocol):
    """Where each minted position is kept as soon as it completes, so a failed run resumes."""

    def minted(self) -> Mapping[str, MintedSlot]: ...

    def record(self, minted: MintedSlot) -> None: ...


StubCheck = Callable[[str, Mapping[str, str], Check, Regime], StubReading]
DslCheck = Callable[[Sequence[Check]], None]


def regime_of(round_: int | None, drift_round: int) -> Regime:
    """R2 for held-out positions and for rounds from the drift round on; R1 before it."""
    return Regime.R2 if round_ is None or round_ >= drift_round else Regime.R1


def quotas(n: int, shares: Sequence[float]) -> list[int]:
    """Counts summing to `n`, each within one of `n * share` (largest remainder; ties go to the earlier)."""
    if abs(sum(shares) - 1.0) > 1e-9 or any(s < 0 for s in shares):
        raise ValueError(f"a goal mix must be non-negative and sum to 1, got {list(shares)}")
    exact = [n * s for s in shares]
    counts = [int(x) for x in exact]
    order = sorted(range(len(shares)), key=lambda i: (-(exact[i] - counts[i]), i))
    for i in order[: n - sum(counts)]:
        counts[i] += 1
    return counts


def _assign(positions: Sequence[tuple[Any, ...]], families: Sequence[str], shares: Sequence[float],
            seed: int) -> dict[tuple[Any, ...], str]:
    """Families for `positions` in the exact quota of `shares`, placed by a seeded key per position."""
    if len(shares) != len(families):
        raise ValueError(f"mix has {len(shares)} shares for {len(families)} families")
    ranked = sorted(positions, key=lambda k: (derive(seed, "family", *k).random(), k))
    out: dict[tuple[Any, ...], str] = {}
    i = 0
    for fam, count in zip(families, quotas(len(positions), shares), strict=True):
        for k in ranked[i:i + count]:
            out[k] = fam
        i += count
    return out


def plan_slots(p: MintParams) -> list[Slot]:
    """Every stream position in stream order, then the held-out positions, each with its drawn family.

    Within a regime the family counts match the regime's goal mix to within one request; the held-out set
    is drawn from the R2 mix alone. The draw reads only the seed and the positions.
    """
    if not 1 < p.drift_round <= p.rounds:
        raise ValueError(f"drift round {p.drift_round} outside 2..{p.rounds}")
    stream = [(r, pos) for r in range(1, p.rounds + 1) for pos in range(p.per_round)]
    fam: dict[tuple[Any, ...], str] = {}
    for regime in (Regime.R1, Regime.R2):
        positions = [k for k in stream if regime_of(k[0], p.drift_round) is regime]
        fam.update(_assign(positions, p.families, p.mix[regime.value], p.seed))
    held = [("held_out", pos) for pos in range(p.held_out)]
    fam.update(_assign(held, p.families, p.mix[Regime.R2.value], p.seed))
    slots = [Slot(f"G-{i:03d}", r, pos, regime_of(r, p.drift_round), fam[(r, pos)])
             for i, (r, pos) in enumerate(stream, start=1)]
    slots += [Slot(f"H-{pos + 1:02d}", None, pos, Regime.R2, fam[k]) for pos, k in enumerate(held)]
    return slots


def request_text(draft_text: str, regime: Regime, constraint: str) -> str:
    """The text the worker reads: the drafted text, with the drift constraint appended once under R2 (after
    a full stop when the drafted text ends without one)."""
    if regime is not Regime.R2:
        return draft_text
    return f"{draft_text if draft_text.endswith(('.', '!', '?')) else draft_text + '.'} {constraint}"


def element_kind(role: str) -> str:
    """The stub element for a contract role: its leading kind word, else a kind word in it, else display."""
    words = re.findall(r"[a-z]+", role.lower())
    if words and words[0] in ELEMENT_KINDS:
        return words[0]
    return next((k for k in ELEMENT_KINDS if k in words), "display")


def stub_html(contract: Mapping[str, str]) -> str:
    """The contract stub: every contract id on an inert element of its kind, and no script."""
    lines = []
    for sel, role in contract.items():
        el = sel[1:]
        lines.append({
            "button": f'<button id="{el}">{el}</button>',
            "input": f'<input id="{el}" type="text">',
            "textarea": f'<textarea id="{el}"></textarea>',
            "select": f'<select id="{el}"><option>-</option></select>',
            "checkbox": f'<input id="{el}" type="checkbox">',
            "canvas": f'<canvas id="{el}" width="300" height="150"></canvas>',
        }.get(element_kind(role), f'<div id="{el}">-</div>'))
    body = "\n".join(lines)
    return ('<!doctype html>\n<html lang="en">\n<head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1"><title>stub</title></head>\n'
            f"<body>\n{body}\n</body>\n</html>\n")


def _selectors(check: Check, verbs: frozenset[str]) -> list[str]:
    """Every selector the check's steps and its expectation name. The verb is picked as the DSL picks it:
    the first expect key that is a verb, so a modifier listed first is never read as a selector."""
    sels = [str(s[v]) for s in check.steps for v in SELECTOR_STEPS if v in s]
    verb = next(k for k in check.expect if k in verbs)
    if verb != "no_console_errors":
        sels.append(str(check.expect[verb]))
    return sels


def build_check(raw: Any, check_id: str) -> Check:
    """A Check from a model's JSON object; raises Rejected when it is not an object of steps and expect."""
    if not isinstance(raw, dict):
        raise Rejected(f"{check_id}: a check is an object with 'steps' and 'expect'")
    steps, expect = raw.get("steps"), raw.get("expect")
    if not isinstance(steps, list) or not all(isinstance(s, dict) for s in steps):
        raise Rejected(f"{check_id}: 'steps' must be a list of objects")
    if not isinstance(expect, dict) or not expect:
        raise Rejected(f"{check_id}: 'expect' must be one object")
    return Check(check_id, tuple(steps), dict(expect))


def check_against_contract(check: Check, contract: Mapping[str, str], text: str, test_query: str,
                           dsl_check: DslCheck, verbs: frozenset[str]) -> None:
    """Raises Rejected when `check` is outside the DSL, is one no build could pass (an element outside the
    contract, a canvas_changed with no input step), names no contract element, or reloads with a query the
    request text does not state."""
    try:
        dsl_check([check])
    except ValueError as e:                     # InvalidCheck: a DSL shape error, stated to the model
        raise Rejected(f"{check.id}: {e}") from None
    for sel in _selectors(check, verbs):
        ids = SELECTOR_ID.findall(sel)
        if not ids:
            raise Rejected(f"{check.id}: selector {sel!r} names no contract id")
        missing = [f"#{i}" for i in ids if f"#{i}" not in contract]
        if missing:
            raise Rejected(f"{check.id}: selector {sel!r} names {missing}, not in the contract")
    if "canvas_changed" in check.expect and all("goto" in s or "wait" in s for s in check.steps):
        raise Rejected(f"{check.id}: canvas_changed needs an input step; its control repeats the waits with no "
                       "input, so a canvas that changes by itself fails it on every build")
    for s in check.steps:
        if "goto" in s:
            if s["goto"] not in ("", test_query):
                raise Rejected(f"{check.id}: goto takes '' or {test_query!r} only")
            if s["goto"] == test_query and test_query not in text:
                raise Rejected(f"{check.id}: uses {test_query!r}, which the request text does not state")


def validate_draft(raw: Any, regime: Regime, p: MintParams, dsl_check: DslCheck) -> Draft:
    """The drafted request; raises Rejected on any field outside the form the prompt asks for."""
    if not isinstance(raw, dict):
        raise Rejected("expected an object with text, timed, contract and checks")
    text = raw.get("text")
    if not isinstance(text, str) or not text.strip():
        raise Rejected("'text' must be a non-empty string")
    text = text.strip()
    if "\n" in text:
        raise Rejected("'text' must be one line")
    if names_family(text, p.families):
        raise Rejected("'text' must describe the app in the user's words, not name a kind label")
    lead = re.split(r"[:.]", p.constraint.strip(), maxsplit=1)[0].strip().lower()
    if p.constraint.strip() in text or (lead and lead in text.lower()):
        raise Rejected("'text' must not include the phone-screen sentence; it is added after your text")
    timed = raw.get("timed")
    if not isinstance(timed, bool):
        raise Rejected("'timed' must be true or false")
    if timed and p.test_query not in text:
        raise Rejected(f"a timed request states {p.test_query!r} in its text")
    contract = raw.get("contract")
    if not isinstance(contract, dict) or not contract:
        raise Rejected("'contract' must be a non-empty object of '#id': role")
    for sel, role in contract.items():
        if not CONTRACT_ID.match(str(sel)):
            raise Rejected(f"contract key {sel!r} must be an id with '#', such as '#start'")
        if not isinstance(role, str) or not role.strip():
            raise Rejected(f"contract role of {sel} must be a non-empty phrase")
        if names_family(role, p.families) or names_family(str(sel).lstrip("#"), p.families):
            raise Rejected(f"contract entry {sel}: name the element's job, not a kind label")
        words = re.findall(r"[a-z]+", role.lower())
        if not words or words[0] not in ELEMENT_KINDS:
            raise Rejected(f"contract role of {sel} must begin with its element kind, one of {list(ELEMENT_KINDS)}")
    contract = {str(k): str(v).strip() for k, v in contract.items()}
    raw_checks = raw.get("checks")
    lo, hi = p.checks_per_regime
    if not isinstance(raw_checks, list) or not lo <= len(raw_checks) <= hi:
        raise Rejected(f"'checks' must be a list of {lo} to {hi} checks")
    full = request_text(text, regime, p.constraint)
    checks = tuple(build_check(c, f"c{i}") for i, c in enumerate(raw_checks, start=1))
    for c in checks:
        check_against_contract(c, contract, full, p.test_query, dsl_check, p.expect_verbs)
    try:
        dsl_check(checks)
    except ValueError as e:
        raise Rejected(str(e)) from None
    return Draft(text, contract, checks, timed)


def validate_regenerated(raw: Any, rejected: Check, others: Sequence[Check], contract: Mapping[str, str],
                         text: str, p: MintParams, dsl_check: DslCheck) -> Check:
    """The replacement check under the rejected one's id; raises Rejected when it is outside the form or
    repeats the rejected check."""
    if not isinstance(raw, dict) or "check" not in raw:
        raise Rejected("expected an object with one 'check'")
    check = build_check(raw["check"], rejected.id)
    if (check.steps, check.expect) == (rejected.steps, rejected.expect):
        raise Rejected("the replacement repeats the rejected check")
    check_against_contract(check, contract, text, p.test_query, dsl_check, p.expect_verbs)
    try:
        dsl_check([*others, check])
    except ValueError as e:
        raise Rejected(str(e)) from None
    return check


def _check_resumed(minted: MintedSlot, slot: Slot) -> None:
    """Raises ValueError when a journaled position does not match the plan (another seed, mix or regime)."""
    r = minted.request
    if (r.id, r.round, r.held_out, minted.family, set(r.checks)) != (
            slot.id, slot.round, slot.round is None, slot.family, {slot.regime}):
        raise ValueError(f"the mint journal's {r.id} does not match the plan's {slot}; mint into a fresh goals dir")


def _check_json(c: Check) -> str:
    return json.dumps({"steps": list(c.steps), "expect": c.expect})


@dataclass
class GoalMinter:
    client: ModelClient
    book: PromptBook
    params: MintParams
    stub_reading: StubCheck
    dsl_check: DslCheck
    sink: Callable[[MintedGoals], None]
    journal: MintJournal | None = None      # keeps each position as it completes; a rerun skips those

    def mint(self) -> None:
        """Mints every planned position and hands the result to `sink` (which writes `goals/`). The sink is
        called only when every position minted; MintFailed propagates otherwise, after the journal has kept
        every position that did."""
        self.sink(self.mint_requests())

    def mint_requests(self, slots: Sequence[Slot] | None = None) -> MintedGoals:
        """The minted requests for `slots` (default: the whole plan). A position the journal already holds
        is taken from it, not re-minted. A position that yields no request with a check failing on its stub
        is reported and skipped; MintFailed is raised after the last position, carrying the partial result."""
        out = MintedGoals()
        prior: dict[str, list[str]] = {}
        failed: list[str] = []
        done = self.journal.minted() if self.journal is not None else {}
        for slot in plan_slots(self.params) if slots is None else slots:
            seen = prior.setdefault(slot.family, [])
            minted = done.get(slot.id)
            if minted is not None:
                _check_resumed(minted, slot)
                out.report.extend(minted.report)
            else:
                start = len(out.report)
                try:
                    req, draft_text, stub = self._mint_slot(slot, seen, out)
                except MintFailed as e:
                    out.note(slot.id, "", "position_failed", str(e))
                    failed.append(slot.id)
                    continue
                minted = MintedSlot(req, slot.family, draft_text, stub, tuple(out.report[start:]))
                if self.journal is not None:
                    self.journal.record(minted)
            seen.append(minted.draft_text)
            out.stubs[slot.id] = minted.stub
            (out.held_out if slot.round is None else out.stream).append(minted.request)
        if failed:
            raise MintFailed(f"no request survived for {failed}; the other positions are kept", out, failed)
        return out

    def _mint_slot(self, slot: Slot, prior: Sequence[str], out: MintedGoals) -> tuple[Request, str, str]:
        p = self.params
        retry: str | None = None                # why the previous draft was retried
        for attempt in range(p.request_regenerations + 1):
            asked = ask_json(
                self.client, ROLE, self._draft_messages(slot, prior, retry),
                lambda raw: validate_draft(raw, slot.regime, p, self.dsl_check),
                reasks=p.reasks, tag=f"{ROLE}:{slot.id}:draft{attempt}",
            )
            if asked.value is None:
                out.note(slot.id, "", "draft_refused", "; ".join(asked.failures))
                retry = "refused"
                continue
            draft = asked.value
            text = request_text(draft.text, slot.regime, p.constraint)
            kept = self._validated_checks(slot, text, draft, out)
            if kept:
                req = Request(slot.id, text, frozenset(), dict(draft.contract), {slot.regime: tuple(kept)},
                              held_out=slot.round is None, round=slot.round)
                return req, draft.text, stub_html(draft.contract)
            out.note(slot.id, "", "request_regenerated", "no check failed on the contract stub")
            retry = "no_behaviour"
        raise MintFailed(f"{slot.id}: no request survived {p.request_regenerations + 1} drafts")

    def _validated_checks(self, slot: Slot, text: str, draft: Draft, out: MintedGoals) -> list[Check]:
        p = self.params
        kept: list[Check] = []
        for idx, check in enumerate(draft.checks):
            current: Check | None = check
            for regen in range(p.check_regenerations + 1):
                assert current is not None
                reading = self.stub_reading(slot.id, draft.contract, current, slot.regime)
                if not reading.passed:
                    if reading.detail:
                        out.note(slot.id, current.id, "check_errored_on_stub", reading.detail)
                    kept.append(current)
                    break
                out.note(slot.id, current.id, "check_passed_on_stub", _check_json(current))
                if regen == p.check_regenerations:
                    out.note(slot.id, current.id, "check_dropped", "passed on its stub after regeneration")
                    break
                others = [*kept, *draft.checks[idx + 1:]]
                current = self._regenerate(slot, text, draft, current, others)
                if current is None:
                    out.note(slot.id, check.id, "check_dropped", "regeneration refused")
                    break
                out.note(slot.id, current.id, "check_regenerated", _check_json(current))
        return kept

    def _regenerate(self, slot: Slot, text: str, draft: Draft, rejected: Check,
                    others: Sequence[Check]) -> Check | None:
        p = self.params
        user = self.book.text("goal_minter/regenerate_check.md").format(
            text=text, contract=json.dumps(draft.contract, indent=1),
            others="\n".join(_check_json(c) for c in others) or "none",
            rejected=_check_json(rejected), test_query=p.test_query, max_wait_ms=p.max_wait_ms,
        )
        messages = [Message("system", self.book.text("goal_minter/system.md")), Message("user", user)]
        asked = ask_json(
            self.client, ROLE, messages,
            lambda raw: validate_regenerated(raw, rejected, others, draft.contract, text, p, self.dsl_check),
            reasks=p.reasks, tag=f"{ROLE}:{slot.id}:{rejected.id}:regen",
        )
        return asked.value

    def _draft_messages(self, slot: Slot, prior: Sequence[str], retry: str | None) -> list[Message]:
        p = self.params
        lo, hi = p.checks_per_regime
        phone = (self.book.text("goal_minter/phone_block.md").format(constraint=p.constraint)
                 if slot.regime is Regime.R2 else "")
        retry_block = {None: "", "refused": self.book.text("goal_minter/retry_refused.md"),
                       "no_behaviour": self.book.text("goal_minter/retry_block.md")}[retry]
        user = self.book.text("goal_minter/request.md").format(
            family=slot.family, lo=lo, hi=hi, test_query=p.test_query, max_wait_ms=p.max_wait_ms,
            phone_block=phone, retry_block=retry_block,
            prior="\n".join(f"- {t}" for t in prior) or "- none yet",
        )
        return [Message("system", self.book.text("goal_minter/system.md")), Message("user", user)]
