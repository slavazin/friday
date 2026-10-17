"""The how-to check (SPEC § 5): read the declared controls through the repair chain, then score them.

The JSON in `<script type="application/json" id="how-to-data">` is read at the first rung that succeeds:
1 strict parse, 2 the repairing parser, 3 salvage of each `controls` entry on its own, 4 nothing. A rung
succeeds when it yields an object with a `controls` list (rungs 1-2) or at least one valid salvaged entry
(rung 3). A JSON value that parses but has another shape falls through to the next rung.

Each entry `{id, does, action, effect}` becomes one acceptance check: `goto` the test query, the action, a
settle wait, the effect. Actions and effects are limited to Appendix A's forms. A declared control passes only
when its effect also fails to hold with the action left out: an effect that is true anyway is an overclaim.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Collection, Iterator, Sequence
from contextlib import AbstractContextManager
from typing import Any

from friday.domain.types import Check, CheckResult, HowToParse, HowToReading, HowToRung, Viewport
from friday.eval.dsl import InvalidCheck, run_check, validate
from friday.ports.browser import Page

ACTIONS = frozenset({"click", "type", "key", "focus"})       # Appendix A
EFFECTS = frozenset({"text_changed", "canvas_changed", "visible"})
SELECTOR_ACTIONS = frozenset({"click", "type", "focus"})
_SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)(?:</script\s*>|\Z)", re.IGNORECASE | re.DOTALL)
_ATTR = re.compile(r"""([^\s=/>]+)\s*(?:=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?""")
_ID_SELECTOR = re.compile(r"#[A-Za-z_][\w-]*")
_CONTROLS_KEY = re.compile(r'"controls"\s*:\s*\[')


def _hash_id(i: str) -> str:
    return i if i.startswith("#") else "#" + i


def _attrs(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _ATTR.finditer(raw):
        name = m.group(1).lower()
        if name not in out:
            out[name] = next((g for g in m.groups()[1:] if g is not None), "")
    return out


def find_json(html: str) -> str | None:
    """The text of the first `<script id="how-to-data">` element (any attribute order or quote style)."""
    for m in _SCRIPT.finditer(html):
        if _attrs(m.group(1)).get("id", "").strip() == "how-to-data":
            return m.group(2)
    return None


def _strict(text: str) -> Any:
    try:
        return json.loads(text)  # artifact text, not model output: rung 1 is the strict parse (SPEC § 5)
    except (ValueError, RecursionError):
        return None


def _repaired(text: str) -> Any:
    """Rung 2. Stands in for `friday.support.json_repair.decode`, which the eval layer may not import yet
    (seam request: allow eval -> support in tests/architecture/test_layers.py). It applies only decode's
    syntax repairs that apply to artifact text, in decode's order: outermost span, trailing commas, smart
    quotes. No key is invented and no value guessed."""
    s = text.strip()
    start = next((i for i, c in enumerate(s) if c in "{["), None)
    if start is None:
        return None
    end = max(s.rfind("}"), s.rfind("]"))
    candidates = [s[start : end + 1]] if end > start else []
    repairs = (lambda t: re.sub(r",\s*([}\]])", r"\1", t),
               lambda t: t.replace("\u201c", '"').replace("\u201d", '"'))
    for repair in repairs:
        if candidates:
            candidates.append(repair(candidates[-1]))
    for c in candidates:
        value = _strict(c)
        if value is not None:
            return value
    return None


def _shaped(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get("controls"), list)


def _objects_in_array(text: str, start: int) -> Iterator[str]:
    """Each balanced top-level `{...}` span inside the array opening at `text[start] == '['`; an unclosed
    trailing object (a truncated array) is not yielded."""
    depth = 0
    in_str = esc = False
    obj_start = -1
    for i in range(start + 1, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c in "{[":
            if depth == 0 and c == "{":
                obj_start = i
            depth += 1
        elif c in "}]":
            if depth == 0:
                return  # the controls array closed
            depth -= 1
            if depth == 0 and c == "}" and obj_start >= 0:
                yield text[obj_start : i + 1]
                obj_start = -1


def _salvage(text: str) -> list[Any]:
    m = _CONTROLS_KEY.search(text)
    if m is None:
        return []
    out: list[Any] = []
    for span in _objects_in_array(text, m.end() - 1):
        value = _repaired(span)
        if value is not None:
            out.append(value)
    return out


def _compile(n: int, entry: Any, test_query: str, settle_ms: int) -> tuple[Check, list[str]] | None:
    """Entry `n` as an acceptance check plus the '#id's it names; None when it is not valid DSL."""
    if not isinstance(entry, dict):
        return None
    eid, action, effect = entry.get("id"), entry.get("action"), entry.get("effect")
    if not (isinstance(eid, str) and eid.strip() and isinstance(action, dict) and isinstance(effect, dict)):
        return None
    a_verbs, e_verbs = [k for k in action if k in ACTIONS], [k for k in effect if k in EFFECTS]
    if len(a_verbs) != 1 or len(e_verbs) != 1 or set(action) - ACTIONS - {"text", "times"} or len(effect) != 1:
        return None
    a_verb, e_verb = a_verbs[0], e_verbs[0]
    if a_verb == "type" and e_verb == "text_changed" and action.get("type") == effect.get("text_changed"):
        return None  # typing into a field changes that field by definition: no effect is claimed
    eid = _hash_id(eid.strip())
    check = Check(f"how-to.{n}:{eid}", ({"goto": test_query}, dict(action), {"wait": settle_ms}), dict(effect))
    try:
        validate(check, settle_ms)
    except InvalidCheck:
        return None
    named = [eid]
    if a_verb in SELECTOR_ACTIONS:
        named.append(action[a_verb])
    named.append(effect[e_verb])
    return check, [s for s in named if _ID_SELECTOR.fullmatch(s)]


def _build(rung: HowToRung, entries: Sequence[Any], summary: Any, contract: Collection[str], max_controls: int,
           test_query: str, settle_ms: int) -> HowToParse:
    checks: list[Check] = []
    ids: list[str] = []
    invalid = 0
    for n, entry in enumerate(entries[:max_controls]):
        compiled = _compile(n, entry, test_query, settle_ms)
        if compiled is None:
            invalid += 1
            continue
        checks.append(compiled[0])
        for i in compiled[1]:
            if i not in ids:
                ids.append(i)
    undeclared = tuple(_hash_id(c) for c in contract if _hash_id(c) not in ids)
    return HowToParse(rung=rung, declared=tuple(checks), invalid_entries=invalid, declared_ids=tuple(ids),
                      undeclared_contract_ids=undeclared, summary=summary if isinstance(summary, str) else "")


def _nothing(contract: Collection[str]) -> HowToParse:
    return HowToParse(rung=HowToRung.NONE, declared=(), invalid_entries=0, declared_ids=(),
                      undeclared_contract_ids=tuple(_hash_id(c) for c in contract))


def parse(html: str, contract: Collection[str], max_controls: int, test_query: str, settle_ms: int) -> HowToParse:
    """The how-to in `html`, read at the first rung that succeeds. Total: never raises."""
    text = find_json(html) if isinstance(html, str) else None
    if text is None:
        return _nothing(contract)
    for rung, read in ((HowToRung.STRICT, _strict), (HowToRung.REPAIRED, _repaired)):
        value = read(text)
        if _shaped(value):
            return _build(rung, value["controls"], value.get("summary"), contract, max_controls, test_query,
                          settle_ms)
    salvaged = _salvage(text)
    built = _build(HowToRung.SALVAGED, salvaged, "", contract, max_controls, test_query, settle_ms)
    return built if built.declared else _nothing(contract)


def _without_action(check: Check) -> Check:
    return Check(check.id, tuple(s for s in check.steps if "goto" in s or "wait" in s), check.expect)


def run(open_page: Callable[[], AbstractContextManager[Page]], parse_: HowToParse) -> tuple[CheckResult, ...]:
    """One result per declared control, in order. A control passes when its check passes and the same check
    with the action left out fails (canvas_changed carries the DSL's own no-input control instead)."""
    out: list[CheckResult] = []
    for check in parse_.declared:
        result = run_check(open_page, check)
        controlled = result.passed and "canvas_changed" not in check.expect
        if controlled and run_check(open_page, _without_action(check)).passed:
            result = CheckResult(check.id, False, "control: the effect holds without the action")
        out.append(result)
    return tuple(out)


def score(parse_: HowToParse, results: Sequence[CheckResult], contract: Collection[str], repair_penalty: float,
          pass_id: str, viewport: Viewport) -> HowToReading:
    """H = passed share of declared controls x declared share of contract ids x repair factor.

    Invalid entries count as failed controls. Rung 4 gives H = 0. Raises on an empty contract (coverage has no
    denominator) or when `results` does not match the declared controls one for one.
    """
    if not contract:
        raise ValueError("a request's contract names at least one id; coverage is undefined without one")
    if [r.check_id for r in results] != [c.id for c in parse_.declared]:
        raise ValueError("how-to results must match the declared controls one for one")
    if parse_.rung is HowToRung.NONE:
        return HowToReading(pass_id, viewport, parse_, tuple(results), 0.0)
    ids = {_hash_id(c) for c in contract}
    coverage = len(ids & set(parse_.declared_ids)) / len(ids)
    attempted = len(parse_.declared) + parse_.invalid_entries
    # attempted == 0 means no valid entry, so no declared id and coverage 0: H is 0 either way
    passed_share = sum(r.passed for r in results) / attempted if attempted else 0.0
    factor = 1.0 - repair_penalty if parse_.rung in (HowToRung.REPAIRED, HowToRung.SALVAGED) else 1.0
    return HowToReading(pass_id, viewport, parse_, tuple(results), passed_share * coverage * factor)
