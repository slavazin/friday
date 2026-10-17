"""The judge (SPEC § 6): a lens call that sees the artifact only, and a report call that adds the eval report.

Every quote must be a verbatim substring of the artifact source; one re-ask, then the call's output is dropped
and the drop is recorded in `Judge.calls`.

Quotes are verified against the artifact source, not the whole call input: the lens call's input is the
request, the composed payloads and the source, and SPEC § 6 asks for "a verbatim span from the source"; the
report call adds the eval report, and a quote lifted from it would carry check ids and failing steps into the
observations, the pools and the nominator's draft.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from friday.algorithms.language import quote_in
from friday.domain.types import FREE_LENS, Disposition, Fire, Lens, LensAnswer, LensStatus
from friday.ports.models import Message, ModelClient
from friday.roles.ask import Asked, Rejected, ask_json
from friday.roles.prompts import PromptBook

ROLE = "judge"

ANSWERABLE = frozenset({LensStatus.ACTIVE, LensStatus.HIDDEN})

LENS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"lenses": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "string"}, "p": {"type": "number"}, "quote": {"type": "string"}},
        "required": ["id", "p", "quote"]}}},
    "required": ["lenses"],
}

REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "observations": {"type": "array", "items": {
            "type": "object",
            "properties": {k: {"type": "string"} for k in ("lens_id", "shape", "direction", "quote")},
            "required": ["lens_id", "shape", "direction", "quote"]}},
        "dispositions": {"type": "object", "additionalProperties": {
            "type": "object",
            "properties": {"disposition": {"type": "string",
                                           "enum": ["applied", "not-applicable", "contradicted"]},
                           "quote": {"type": "string"}},
            "required": ["disposition"]}},
    },
    "required": ["observations", "dispositions"],
}

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def fill(template: str, **values: object) -> str:
    """`template` with each `{name}` replaced by its value in one pass (inserted text is never re-scanned).

    Raises KeyError for a placeholder with no value: a template and its caller disagree.
    """
    return _PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), template)


@dataclass(frozen=True)
class DraftObservation:
    lens_id: str
    shape: str
    direction: str
    quote: str


@dataclass(frozen=True)
class JudgeCall:
    """One judge call as it ran: dropped (`ok` False) after its re-asks, or validated."""

    pass_id: str
    call: str                       # "lens" | "report"
    ok: bool
    attempts: int
    failures: tuple[str, ...]
    models: tuple[str, ...]         # the model string the service echoed, per attempt
    cost_usd: float | None          # None when any attempt was unpriced


def validate_lens_answers(
    raw: Any, pass_id: str, lenses: Sequence[Lens], source: str, flag_p: float, judge_model: str,
) -> list[LensAnswer]:
    """LensAnswers for every lens in `lenses`; raises Rejected on a missing, unknown or repeated lens, a bad p
    or an unquoted span."""
    items = raw.get("lenses") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise Rejected("expected an object with a 'lenses' list")
    wanted = {lens.id for lens in lenses}
    by_id: dict[str, dict[str, Any]] = {}
    for i in items:
        if not isinstance(i, dict):
            raise Rejected("each lens answer is an object")
        lid = str(i.get("id"))
        if lid not in wanted:
            raise Rejected(f"unknown lens id {lid!r}")
        if lid in by_id:
            raise Rejected(f"{lid} answered twice")
        by_id[lid] = i
    out = []
    for lens in lenses:
        item = by_id.get(lens.id)
        if item is None:
            raise Rejected(f"no answer for {lens.id}")
        try:
            p = float(item.get("p"))
        except (TypeError, ValueError):
            raise Rejected(f"{lens.id}: p is not a number") from None
        if not 0.0 <= p <= 1.0:
            raise Rejected(f"{lens.id}: p outside [0, 1]")
        quote = str(item.get("quote") or "")
        if p >= flag_p and not quote:
            raise Rejected(f"{lens.id}: p >= {flag_p} needs a quote")
        if p >= flag_p and not quote_in(quote, source):
            raise Rejected(f"{lens.id}: quote is not verbatim from the input")
        out.append(LensAnswer(pass_id, lens.id, p, quote if p >= flag_p else "", judge_model))
    return out


def validate_report(
    raw: Any, lens_ids: Sequence[str], composed: Sequence[str], source: str, max_observations: int,
) -> tuple[list[DraftObservation], dict[str, tuple[Disposition, str]]]:
    """Observations and per-decision dispositions; raises Rejected on any field outside the form, or on a
    disposition for a decision that was not composed.

    A quote is kept only on a `contradicted` disposition, where it is required and verified.
    """
    if not isinstance(raw, dict):
        raise Rejected("expected an object")
    obs_raw = raw.get("observations", [])
    if not isinstance(obs_raw, list) or len(obs_raw) > max_observations:
        raise Rejected(f"observations must be a list of at most {max_observations}")
    allowed = set(lens_ids) | {FREE_LENS}
    observations = []
    for o in obs_raw:
        if not isinstance(o, dict):
            raise Rejected("each observation is an object")
        lens_id = str(o.get("lens_id", ""))
        if lens_id not in allowed:
            raise Rejected(f"unknown lens_id {lens_id!r}")
        shape, direction, quote = (str(o.get(k) or "").strip() for k in ("shape", "direction", "quote"))
        if not (shape and direction and quote):
            raise Rejected("each observation needs shape, direction and quote")
        if not quote_in(quote, source):
            raise Rejected("an observation quote is not verbatim from the input")
        observations.append(DraftObservation(lens_id, shape, direction, quote))
    disp_raw: Mapping[str, Any] = raw.get("dispositions", {}) or {}
    if not isinstance(disp_raw, dict):
        raise Rejected("dispositions must be an object keyed by decision id")
    unknown = sorted(set(map(str, disp_raw)) - set(composed))
    if unknown:
        raise Rejected(f"dispositions name decisions that were not composed: {', '.join(unknown)}")
    dispositions: dict[str, tuple[Disposition, str]] = {}
    for d_id in composed:
        item = disp_raw.get(d_id)
        if item is None:
            raise Rejected(f"no disposition for {d_id}")
        value = item.get("disposition") if isinstance(item, dict) else item
        quote = str(item.get("quote") or "") if isinstance(item, dict) else ""
        try:
            disp = Disposition(value)
        except ValueError:
            raise Rejected(f"{d_id}: disposition must be applied, not-applicable or contradicted") from None
        if disp is Disposition.UNDISPOSED:
            raise Rejected(f"{d_id}: disposition must be applied, not-applicable or contradicted")
        if disp is Disposition.CONTRADICTED and not (quote and quote_in(quote, source)):
            raise Rejected(f"{d_id}: contradicted needs a verbatim quote")
        dispositions[d_id] = (disp, quote if disp is Disposition.CONTRADICTED else "")
    return observations, dispositions


def fires_for(pass_id: str, round_: int, composed: Sequence[str],
              dispositions: Mapping[str, tuple[Disposition, str]] | None) -> list[Fire]:
    """One Fire per composed decision: the report call's verdict, or `undisposed` when the call was dropped.

    Raises KeyError when `dispositions` lacks a composed decision (validate_report never returns such a map).
    """
    if dispositions is None:
        return [Fire(pass_id, d, round_) for d in composed]
    return [Fire(pass_id, d, round_, dispositions[d][0], dispositions[d][1]) for d in composed]


def render_decisions(composed: Mapping[str, str]) -> str:
    """The composed decisions, one `- id: payload` line each in id order."""
    if not composed:
        return "(none)"
    return "\n".join(f"- {d}: {composed[d].strip()}" for d in sorted(composed))


def render_lenses(lenses: Sequence[Lens]) -> str:
    """Each lens as its id, angle and counterfactual."""
    return "\n".join(f"- {x.id}\n  angle: {x.angle.strip()}\n  counterfactual: {x.counterfactual.strip()}"
                     for x in lenses)


def render_answers(answers: Sequence[LensAnswer]) -> str:
    """The lens call's answers, one line each in lens id order; a quote only where p reached the flag."""
    if not answers:
        return "(none)"
    return "\n".join(f"- {a.lens_id}: p {a.p:.2f}" + (f", quote: {a.quote}" if a.quote else "")
                     for a in sorted(answers, key=lambda a: a.lens_id))


def _cost(asked: Asked[Any]) -> float | None:
    costs = [r.cost_usd for r in asked.results]
    return None if any(c is None for c in costs) else sum(c for c in costs if c is not None)


@dataclass
class Judge:
    """The two judge calls (SPEC § 6). Prompts live in prompts/judge/. Every call run is appended to `calls`."""

    client: ModelClient
    book: PromptBook
    flag_p: float
    max_observations: int
    quote_reasks: int
    calls: list[JudgeCall] = field(default_factory=list)
    # The lenses each pass's lens call was given, recorded before the model call so that the report call
    # renders them even when the lens call was dropped (SPEC § 6: the report call has everything the lens
    # call had).
    lenses_by_pass: dict[str, tuple[Lens, ...]] = field(default_factory=dict)

    def lens_messages(self, request_text: str, composed: Mapping[str, str], source: str,
                      lenses: Sequence[Lens]) -> list[Message]:
        """The lens call's messages: request, composed decisions, artifact source and lenses; nothing of the eval."""
        system = fill(self.book.text("judge/lens_system.md").strip(), flag_p=self.flag_p)
        user = fill(self.book.text("judge/lens_user.md").strip(), request=request_text.strip(),
                    decisions=render_decisions(composed), source=source, lenses=render_lenses(lenses))
        return [Message("system", system), Message("user", user)]

    def report_messages(self, request_text: str, composed: Mapping[str, str], source: str,
                        answers: Sequence[LensAnswer], eval_report_text: str,
                        lenses: Sequence[Lens] = ()) -> list[Message]:
        """The report call's messages: the lens call's inputs (lenses included), its answers and the eval
        report text."""
        system = fill(self.book.text("judge/report_system.md").strip(), max_observations=self.max_observations)
        user = fill(self.book.text("judge/report_user.md").strip(), request=request_text.strip(),
                    decisions=render_decisions(composed), source=source,
                    lenses=render_lenses(lenses) if lenses else "(none)", answers=render_answers(answers),
                    eval_report=eval_report_text.strip())
        return [Message("system", system), Message("user", user)]

    def _record(self, pass_id: str, call: str, asked: Asked[Any]) -> None:
        self.calls.append(JudgeCall(pass_id, call, asked.ok, asked.attempts, asked.failures,
                                    tuple(r.model for r in asked.results), _cost(asked)))

    def lens_call(self, pass_id: str, request_text: str, composed: Mapping[str, str], source: str,
                  lenses: Sequence[Lens]) -> list[LensAnswer] | None:
        """Lens answers from the artifact only; None when the call is dropped after its re-ask.

        Each answer records the model string the service echoed on the reply that validated. With no lens to
        answer, returns [] and makes no call. Raises ValueError when given a retired or superseded lens.
        """
        stale = [x.id for x in lenses if x.status not in ANSWERABLE]
        if stale:
            raise ValueError(f"lens call given lenses that are not active or hidden: {stale}")
        self.lenses_by_pass[pass_id] = tuple(lenses)
        if not lenses:
            return []
        asked = ask_json(
            self.client, ROLE, self.lens_messages(request_text, composed, source, lenses),
            lambda raw: validate_lens_answers(raw, pass_id, lenses, source, self.flag_p, ""),
            reasks=self.quote_reasks, tag=f"judge:lens:{pass_id}", schema=LENS_SCHEMA,
        )
        self._record(pass_id, "lens", asked)
        if asked.value is None:
            return None
        served = asked.results[-1].model
        return [replace(a, judge_model=served) for a in asked.value]

    def report_call(self, pass_id: str, request_text: str, composed: Mapping[str, str], source: str,
                    answers: Sequence[LensAnswer], eval_report_text: str, lens_ids: Sequence[str],
                    ) -> tuple[list[DraftObservation], dict[str, tuple[Disposition, str]]] | None:
        """Observations and dispositions; None when the call is dropped (its fires stay undisposed).

        Renders the lenses this pass's lens call was given (those named in `lens_ids`). Raises ValueError when
        `lens_ids` names a lens the pass's lens call was not given: the report call would pool blind.
        """
        given = {x.id: x for x in self.lenses_by_pass.get(pass_id, ())}
        missing = [i for i in lens_ids if i not in given]
        if missing:
            raise ValueError(f"report call names lenses the lens call of {pass_id} was not given: {missing}")
        lenses = [given[i] for i in lens_ids]
        asked = ask_json(
            self.client, ROLE,
            self.report_messages(request_text, composed, source, answers, eval_report_text, lenses),
            lambda raw: validate_report(raw, lens_ids, sorted(composed), source, self.max_observations),
            reasks=self.quote_reasks, tag=f"judge:report:{pass_id}", schema=REPORT_SCHEMA,
        )
        self._record(pass_id, "report", asked)
        return asked.value
