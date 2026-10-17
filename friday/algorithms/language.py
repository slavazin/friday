"""Presence and shape checks on model-facing and model-written text (SPEC § 2, Checks)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from friday.domain.ids import ANY_RUN_ID


def quote_in(quote: str, source: str) -> bool:
    """True iff `quote` is a verbatim substring of `source` (an empty quote is trivially present)."""
    return quote in source


INFLECTIONS = r"(?:s|es|ed|d|ing|er|ers)?"


def word_hits(text: str, words: Iterable[str], inflected: bool = False) -> list[str]:
    """The listed words or phrases present in `text`, case-insensitively at word boundaries.

    With `inflected`, a listed word also matches with a plural or verb suffix ('judge' hits 'judges').
    """
    suffix = INFLECTIONS if inflected else ""
    hits = []
    for w in words:
        if re.search(rf"(?<![\w-]){re.escape(w)}{suffix}(?![\w-])", text, flags=re.IGNORECASE):
            hits.append(w)
    return hits


def blinding_hits(template: str, words: Iterable[str]) -> list[str]:
    """Blinded words a prompt template names, inflections included."""
    return word_hits(template, words, inflected=True)


_CODE_AND_IDS = re.compile(r"`[^`]*`|#[\w-]+")


@dataclass(frozen=True)
class PayloadCheck:
    """Refusal causes (bound, leak, reference) and the recorded hedge reading."""

    too_long: bool
    blinded: tuple[str, ...]
    run_ids: tuple[str, ...]
    hedges: tuple[str, ...]

    @property
    def refusable(self) -> bool:
        return self.too_long or bool(self.blinded) or bool(self.run_ids)


def check_payload(payload: str, max_words: int, blinded: Iterable[str], hedges: Iterable[str]) -> PayloadCheck:
    """Reads a drafted payload against the 60-word bound, the worker's blinding list and run-id references.

    Backticked code and `#id` selectors are skipped by the blinding scan: `#score` is a contract id, not a grade.
    """
    return PayloadCheck(
        too_long=len(payload.split()) > max_words,
        blinded=tuple(blinding_hits(_CODE_AND_IDS.sub(" ", payload), blinded)),
        run_ids=tuple(ANY_RUN_ID.findall(payload)),
        hedges=tuple(word_hits(payload, hedges)),
    )
