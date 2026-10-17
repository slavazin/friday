"""The judge (SPEC § 6): a lens call that sees the artifact only, and a report call that adds the eval report.

Every quote must be a verbatim substring of the call's input; one re-ask, then the call's output is dropped.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from friday.algorithms.language import quote_in
from friday.domain.types import FREE_LENS, Disposition, Lens, LensAnswer
from friday.roles.ask import Rejected

ROLE = "judge"


@dataclass(frozen=True)
class DraftObservation:
    lens_id: str
    shape: str
    direction: str
    quote: str


def validate_lens_answers(
    raw: Any, pass_id: str, lenses: Sequence[Lens], source: str, flag_p: float, judge_model: str,
) -> list[LensAnswer]:
    """LensAnswers for every lens in `lenses`; raises Rejected on a missing lens, bad p or unquoted span."""
    items = raw.get("lenses") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise Rejected("expected an object with a 'lenses' list")
    by_id = {str(i.get("id")): i for i in items if isinstance(i, dict)}
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
    """Observations and per-decision dispositions; raises Rejected on any field outside the form."""
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
        dispositions[d_id] = (disp, quote)
    return observations, dispositions
