"""The tagger (SPEC § 4): tags each round's requests at round start, reusing the tag vocabulary and minting
at most `stream.max_new_tags` new tags a round. Tags are frozen on a request once given.

A reply that renames an existing tag, mints a near-duplicate of one (a merge in disguise: `timer` beside
`timers`), mints over the bar or names a goal family is re-asked once, then refused (TaggingRefused).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from friday.algorithms.language import INFLECTIONS
from friday.domain.types import Request, TagGloss
from friday.ports.models import Message, ModelClient
from friday.roles.ask import Rejected, ask_json
from friday.roles.prompts import PromptBook

ROLE = "tagger"

TAG = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


class TaggingRefused(RuntimeError):
    """The tagger's reply stayed outside the minting bar or the form after its re-asks."""


def _forms(tag: str) -> set[str]:
    """The spellings a tag collapses to: hyphens dropped, a plural ending stripped."""
    t = tag.replace("-", "")
    forms = {t}
    if t.endswith("ies") and len(t) > 4:
        forms.add(t[:-3] + "y")
    if t.endswith("es") and len(t) > 3:
        forms.add(t[:-2])
    if t.endswith("s") and len(t) > 2:
        forms.add(t[:-1])
    return forms


def near_duplicate(a: str, b: str) -> bool:
    """True when two tags name one trait by spelling alone (plural, hyphenation)."""
    return bool(_forms(a) & _forms(b))


def _family_pattern(family: str) -> re.Pattern[str]:
    """A family in any spelling: each word may carry an inflection, and words are joined by spaces,
    hyphens, underscores or nothing ('forms or calculators', 'canvas-games', 'canvasgames')."""
    words = family.split()
    body = r"[\s_-]*".join(re.escape(w) + INFLECTIONS for w in words)
    return re.compile(rf"(?<![^\W_]){body}(?![^\W_])", re.IGNORECASE)


def names_family(text: str, families: Sequence[str]) -> bool:
    """True when `text` (a tag, a request text, a contract role or id) names a goal family in any spelling:
    inflected after any word ('data tools', 'forms or calculators'), hyphenated ('canvas-games'),
    underscored ('canvas_game') or joined ('canvasgames')."""
    return any(_family_pattern(f).search(text) for f in families)


def validate_tagging(
    raw: Any, requests: Sequence[Request], vocabulary: Sequence[TagGloss], round_: int, max_new: int,
    tags_per_request: tuple[int, int], families: Sequence[str],
) -> tuple[dict[str, frozenset[str]], list[TagGloss]]:
    """Tags per request id and the new glosses; raises Rejected on a rename, merge, mint over `max_new`,
    a tag outside the form or a request tagged outside `tags_per_request`."""
    if not isinstance(raw, dict):
        raise Rejected("expected an object with 'tags' and 'new'")
    existing = [g.tag for g in vocabulary]
    new_raw = raw.get("new", [])
    if not isinstance(new_raw, list):
        raise Rejected("'new' must be a list of {tag, gloss}")
    new: list[TagGloss] = []
    for item in new_raw:
        if not isinstance(item, dict):
            raise Rejected("each new tag is an object with 'tag' and 'gloss'")
        tag, gloss = str(item.get("tag", "")).strip(), str(item.get("gloss", "")).strip()
        if not TAG.match(tag):
            raise Rejected(f"new tag {tag!r} must be one lowercase word or hyphenated words")
        if not gloss or "\n" in gloss:
            raise Rejected(f"new tag {tag!r} needs a one-line gloss")
        if tag in existing:
            raise Rejected(f"'{tag}' exists; reuse it as it is, never re-gloss it")
        twin = next((e for e in existing if near_duplicate(tag, e)), None)
        if twin is not None:
            raise Rejected(f"'{tag}' renames or merges the existing tag '{twin}'; reuse '{twin}'")
        twin = next((n.tag for n in new if near_duplicate(tag, n.tag)), None)
        if twin is not None:
            raise Rejected(f"'{tag}' and '{twin}' name one trait; mint one of them")
        if names_family(tag, families):
            raise Rejected(f"'{tag}' names a kind label, not a trait of the request")
        new.append(TagGloss(tag, gloss, round_))
    if len(new) > max_new:
        raise Rejected(f"{len(new)} new tags minted; at most {max_new} may be minted now")
    known = set(existing) | {n.tag for n in new}
    given = raw.get("tags")
    if not isinstance(given, dict):
        raise Rejected("'tags' must be an object from request id to a list of tags")
    want = {r.id for r in requests}
    if set(given) != want:
        raise Rejected(f"'tags' must cover exactly the requests {sorted(want)}")
    lo, hi = tags_per_request
    out: dict[str, frozenset[str]] = {}
    for rid in sorted(want):
        tags = given[rid]
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise Rejected(f"{rid}: tags must be a list of strings")
        if len(set(tags)) != len(tags) or not lo <= len(tags) <= hi:
            raise Rejected(f"{rid}: give {lo} to {hi} distinct tags")
        unknown = [t for t in tags if t not in known]
        if unknown:
            raise Rejected(f"{rid}: {unknown} are neither existing tags nor listed in 'new'")
        out[rid] = frozenset(tags)
    unused = [n.tag for n in new if not any(n.tag in ts for ts in out.values())]
    if unused:
        raise Rejected(f"new tags {unused} are given to no request")
    return out, new


@dataclass
class Tagger:
    client: ModelClient
    book: PromptBook
    tags_per_request: tuple[int, int]
    reasks: int
    families: tuple[str, ...]       # for refusing a tag that spells one; never sent to the model

    def tag(self, requests: Sequence[Request], vocabulary: Sequence[TagGloss], round_: int,
            max_new: int) -> tuple[Mapping[str, frozenset[str]], list[TagGloss]]:
        """Tags per request id, and the tags minted this round (`minted_round` = `round_`).

        Raises ValueError on an already-tagged request or a held-out request with `max_new` above 0, and
        TaggingRefused when the reply stays outside the bar after the re-asks.
        """
        if not requests:
            raise ValueError("no requests to tag")
        tagged = sorted(r.id for r in requests if r.tags)
        if tagged:
            raise ValueError(f"requests already carry tags (frozen once given): {tagged}")
        if max_new < 0 or (max_new > 0 and any(r.held_out for r in requests)):
            raise ValueError("held-out tagging mints nothing: max_new must be 0")
        lo, hi = self.tags_per_request
        vocab = "\n".join(f"- {g.tag}: {g.gloss}" for g in vocabulary) or "- (none yet)"
        user = self.book.text("tagger/request.md").format(
            vocabulary=vocab, requests="\n".join(f"- {r.id}: {r.text}" for r in requests),
            lo=lo, hi=hi, max_new=max_new,
        )
        messages = [Message("system", self.book.text("tagger/system.md")), Message("user", user)]
        asked = ask_json(
            self.client, ROLE, messages,
            lambda raw: validate_tagging(raw, requests, vocabulary, round_, max_new, self.tags_per_request,
                                         self.families),
            reasks=self.reasks, tag=f"{ROLE}:{round_}",
        )
        if asked.value is None:
            raise TaggingRefused("; ".join(asked.failures))
        return asked.value
