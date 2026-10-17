"""The lens minter (SPEC § 6 step 4): proposes at most `limit` lenses from the residual view.

A proposal is kept only in lens form (SPEC § 2 Lens language): an angle and a counterfactual, each one
question that opens with a wh-word (a stipulated defect, never a possibility), a counterfactual that is not
vacuous, no blinded or hedge word, and no near-copy of a current lens or of another proposal. One re-ask
states what was refused; the minter then returns what validated across both replies.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from friday.algorithms.language import blinding_hits, word_hits
from friday.domain.types import Lens, Observation
from friday.ports.models import ChatResult, Message, ModelClient
from friday.roles.judge import fill, render_lenses
from friday.roles.prompts import PromptBook
from friday.support.json_repair import decode

ROLE = "lens_minter"

# Word-set Jaccard similarity of two lenses (angle and counterfactual together) at or above which a proposal
# is a near-copy. Not a run.yml knob yet (requested as `lens.mint_near_copy`).
NEAR_COPY_JACCARD = 0.6

# The lens call reads a minted lens as lens text; the Lens language forbids naming the eval besides the
# judge_lens row.
EVAL_WORDS = ("eval",)

_OPENERS = ("which", "where", "what", "when", "how")
# A vacuous counterfactual asks the plain negation; each pattern is anchored to the question's end (or, for
# "no defect" / "nothing wrong", to the whole answer), so "disabled or not rendered" is not refused.
_VACUOUS = re.compile(
    r"(\b(or is it fine|is it fine|or not|is (it|everything) (ok|okay|correct|fine))\s*\?$)|"
    r"(\b(is there|are there) (no|any) (defects?|problems?|issues?|bugs?)\s*\?$)|"
    r"(\bnothing (is )?wrong\b)", re.IGNORECASE)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"lenses": {"type": "array", "items": {
        "type": "object",
        "properties": {"angle": {"type": "string"}, "counterfactual": {"type": "string"}},
        "required": ["angle", "counterfactual"]}}},
    "required": ["lenses"],
}


@dataclass(frozen=True)
class ResidualItem:
    request_text: str
    source_excerpt: str
    observations: tuple[Observation, ...]


@dataclass(frozen=True)
class LensProposal:
    angle: str
    counterfactual: str


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9#'-]+", text.lower()))


def similarity(a: str, b: str) -> float:
    """Jaccard similarity of the word sets of `a` and `b` (0 when both are empty)."""
    wa, wb = _words(a), _words(b)
    union = wa | wb
    return len(wa & wb) / len(union) if union else 0.0


def _question_problem(name: str, text: str) -> str | None:
    if not text:
        return f"{name} is empty"
    if text.count("?") != 1:
        return f"{name} must be exactly one question (one '?')"
    if not text.lower().startswith(_OPENERS):
        return f"{name} must open with Which, Where, What, When or How and stipulate the defect"
    return None


def lens_form_problem(angle: str, counterfactual: str, blinded: Sequence[str], hedges: Sequence[str],
                      existing: Sequence[tuple[str, str]]) -> str | None:
    """Why (angle, counterfactual) is not in lens form, or None when it is.

    `existing` holds the (angle, counterfactual) pairs a proposal must not near-copy.
    """
    for name, text in (("angle", angle), ("counterfactual", counterfactual)):
        problem = _question_problem(name, text)
        if problem:
            return problem
    if _VACUOUS.search(counterfactual):
        return "counterfactual is vacuous; name a concrete opposite reading"
    both = f"{angle} {counterfactual}"
    hits = blinding_hits(both, blinded)
    if hits:
        return f"names blinded words: {', '.join(hits)}"
    hedge = word_hits(both, hedges)
    if hedge:
        return f"hedges: {', '.join(hedge)}"
    for a, c in existing:
        if similarity(both, f"{a} {c}") >= NEAR_COPY_JACCARD:
            return f"near-copy of the lens asking {a!r}"
    return None


def render_residual(residual: Sequence[ResidualItem]) -> str:
    """Each residual build: its request, source excerpt and the report call's observations (no round, no arm)."""
    blocks = []
    for n, item in enumerate(residual, 1):
        obs = "\n".join(f"- [{o.lens_id}] {o.shape.strip()}; direction: {o.direction.strip()}; "
                        f"quote: {o.quote.strip()}" for o in item.observations) or "(none)"
        blocks.append(f"Build {n}\nRequest:\n{item.request_text.strip()}\n\nSource excerpt:\n"
                      f"{item.source_excerpt.strip()}\n\nObservations:\n{obs}")
    return "\n\n".join(blocks)


@dataclass
class LensMinter:
    """The lens minter. Constructible as `LensMinter(client, book)` (the declared seam); `propose` raises
    until `reasks` (run.yml json.reasks) and `families` (run.yml stream.families, blinded for this reader per
    SPEC § 2) are given, so neither the re-ask nor the family blinding can be skipped by omission."""

    client: ModelClient
    book: PromptBook
    reasks: int | None = field(default=None, kw_only=True)
    families: tuple[str, ...] | None = field(default=None, kw_only=True)
    results: list[ChatResult] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def _require_config(self) -> tuple[int, tuple[str, ...]]:
        if self.reasks is None or self.families is None:
            raise ValueError("LensMinter needs reasks (run.yml json.reasks) and families (run.yml "
                             "stream.families) before it mints")
        if not self.families:
            raise ValueError("LensMinter given no goal families to blind")
        return self.reasks, self.families

    def blinded(self) -> tuple[str, ...]:
        """Words a minted lens may not name: the minter's row, the lens call's row, the eval, the families.

        Raises ValueError when reasks or families were not given."""
        _, families = self._require_config()
        rows = self.book.blinding
        return (*rows["lens_minter"], *rows["judge_lens"], *EVAL_WORDS, *families)

    def messages(self, residual: Sequence[ResidualItem], lenses: Sequence[Lens], limit: int) -> list[Message]:
        system = fill(self.book.text("lens_minter/system.md").strip(), limit=limit)
        user = fill(self.book.text("lens_minter/user.md").strip(), lenses=render_lenses(lenses),
                    residual=render_residual(residual))
        return [Message("system", system), Message("user", user)]

    def propose(self, residual: Sequence[ResidualItem], lenses: Sequence[Lens], limit: int) -> list[LensProposal]:
        """At most `limit` proposals in lens form; fewer (or none) when the replies hold fewer that validate.

        Each refusal reason is appended to `failures`, each reply to `results`.
        """
        reasks, _ = self._require_config()
        if limit <= 0:
            return []
        blinded, hedges = self.blinded(), self.book.hedges
        kept: list[LensProposal] = []
        convo = self.messages(residual, lenses, limit)
        for attempt in range(reasks + 1):
            res = self.client.chat(ROLE, convo, json_schema=SCHEMA,
                                   tag="lens_minter" if attempt == 0 else f"lens_minter:reask{attempt}")
            self.results.append(res)
            refused = self._take(res.text, lenses, kept, blinded, hedges, limit)
            self.failures += refused
            if not refused or len(kept) >= limit:
                break
            why = "; ".join(refused)
            convo = [*convo, Message("assistant", res.text), Message(
                "user", f"These proposals were refused: {why}. Reply again with JSON only, in the same form, "
                        f"with at most {limit - len(kept)} lenses that keep to the lens form.")]
        return kept

    def _take(self, text: str, lenses: Sequence[Lens], kept: list[LensProposal], blinded: Sequence[str],
              hedges: Sequence[str], limit: int) -> list[str]:
        """Appends this reply's valid proposals to `kept` (up to `limit`); returns the refusal reasons."""
        decoded = decode(text)
        if not decoded.ok:
            return [f"invalid JSON ({decoded.error})"]
        items = decoded.value.get("lenses") if isinstance(decoded.value, dict) else None
        if not isinstance(items, list):
            return ["expected an object with a 'lenses' list"]
        refused = []
        for n, item in enumerate(items, 1):
            if len(kept) >= limit:
                break
            if not isinstance(item, dict):
                refused.append(f"lens {n}: not an object")
                continue
            angle = str(item.get("angle") or "").strip()
            counter = str(item.get("counterfactual") or "").strip()
            existing = [(x.angle, x.counterfactual) for x in lenses] + [(k.angle, k.counterfactual) for k in kept]
            problem = lens_form_problem(angle, counter, blinded, hedges, existing)
            if problem:
                refused.append(f"lens {n}: {problem}")
            else:
                kept.append(LensProposal(angle, counter))
        return refused
