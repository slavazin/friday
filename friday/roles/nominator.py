"""The nominator (SPEC § 7): drafts one candidate from one pool, grounded by one search call.

The draft's payload is read by `check_payload` (SPEC § 2 Checks): the word bound, the worker's blinding list
with the goal family names, and run ids are refusal causes (one re-ask, then NoDecision); hedge words and a
code block are recorded as readings and never refused. The nominator's prompt never carries the regime, the
drift, the arm, a goal family name, an id or a Ledger figure.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from friday.algorithms.language import blinding_hits, check_payload
from friday.domain.types import Decision, DecisionStatus, Hook, Observation, ObservationStatus, Pool, PoolStatus
from friday.ports.models import Citation, Message, ModelClient, SearchClient
from friday.roles.ask import Rejected, ask_json
from friday.roles.prompts import PromptBook

ROLE = "nominator"
_HEADING = re.compile(r"^\s*(#{1,6}\s|---)", flags=re.MULTILINE)


@dataclass(frozen=True)
class Draft:
    """A drafted candidate's text; the runner mints its id and writes the Journal file."""

    hook: Hook
    payload: str
    sources: tuple[Citation, ...]
    readings: dict[str, Any] = field(default_factory=dict)   # hedges, code block, refusals before acceptance


@dataclass(frozen=True)
class NoDecision:
    reason: str


def _tags(value: Any, what: str, vocabulary: frozenset[str]) -> frozenset[str]:
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        raise Rejected(f"{what} must be a list of tags")
    unknown = sorted(set(value) - vocabulary)
    if unknown:
        raise Rejected(f"{what} uses tags outside the tag vocabulary: {unknown}")
    return frozenset(value)


@dataclass(frozen=True)
class _Reply:
    hook: Hook | None
    payload: str
    no_decision: str
    hedges: tuple[str, ...] = ()


def validate_draft(
    raw: Any, vocabulary: frozenset[str], max_words: int, blinded: Sequence[str], hedges: Sequence[str],
    nominator_blinded: Sequence[str],
) -> _Reply:
    """The hook and payload of a nominator reply, or its no-decision reason; raises Rejected on a refusal cause.

    Refused: a hook tag outside `vocabulary`, an empty `fires_when`, a tag in both lists, a payload over
    `max_words`, naming a `blinded` word, citing a run id, or holding a markdown heading line. A re-ask names
    the cause but never echoes a word from `nominator_blinded`.
    """
    if not isinstance(raw, dict):
        raise Rejected("expected a JSON object")
    if "no_decision" in raw:
        if set(raw) != {"no_decision"}:
            raise Rejected("a no_decision reply carries no hook or payload")
        reason = raw["no_decision"]
        if not isinstance(reason, str) or not reason.strip():
            raise Rejected("no_decision needs a one-sentence reason")
        return _Reply(None, "", reason.strip())
    if set(raw) != {"fires_when", "not_this", "payload"}:
        raise Rejected("a draft has exactly fires_when, not_this and payload")
    fires_when = _tags(raw["fires_when"], "fires_when", vocabulary)
    not_this = _tags(raw["not_this"], "not_this", vocabulary)
    if not fires_when:
        raise Rejected("fires_when needs at least one tag")
    if fires_when & not_this:
        raise Rejected(f"tags in both fires_when and not_this: {sorted(fires_when & not_this)}")
    payload = raw["payload"]
    if not isinstance(payload, str) or not payload.strip():
        raise Rejected("payload must be a non-empty string")
    payload = payload.strip()
    if _HEADING.search(payload):
        raise Rejected("the payload is one directive, with no heading or rule line")
    check = check_payload(payload, max_words, blinded, hedges)
    causes = []
    if check.too_long:
        causes.append(f"the payload is over {max_words} words")
    if check.blinded:
        sayable = [w for w in check.blinded if not blinding_hits(w, nominator_blinded)]
        causes.append("the payload names words a builder's prompt never carries"
                      + (f": {sayable}" if sayable else ""))
    if check.run_ids:
        causes.append(f"the payload cites ids the builder cannot look up: {list(check.run_ids)}")
    if causes:
        raise Rejected("; ".join(causes))
    return _Reply(Hook(fires_when, not_this), payload, "", check.hedges)


def _quote_block(observations: Sequence[Observation]) -> str:
    rows = [{"shape": o.shape, "direction": o.direction, "quote": o.quote} for o in observations]
    return json.dumps(rows, indent=1)


def render_nominator(
    book: PromptBook, pool: Pool, label: str, observations: Sequence[Observation], sources: Sequence[Citation],
    vocabulary: frozenset[str], max_words: int,
) -> list[Message]:
    """The nominator's messages. Observations render by shape, direction and quote only: no id, round,
    category or lens."""
    system = (book.text("nominator/system.md")
              .replace("{{max_words}}", str(max_words))
              .replace("{{baseline}}", book.text("worker/baseline.md").strip()))
    src = json.dumps([{"title": c.title, "url": c.url, "snippet": c.snippet} for c in sources], indent=1)
    user = (book.text("nominator/user.md")
            .replace("{{shape}}", pool.shape)
            .replace("{{label}}", label)
            .replace("{{observations}}", _quote_block(observations))
            .replace("{{sources}}", src)
            .replace("{{tags}}", json.dumps(sorted(vocabulary))))
    return [Message("system", system), Message("user", user)]


@dataclass
class Nominator:
    client: ModelClient
    search: SearchClient
    book: PromptBook
    tag_vocabulary: frozenset[str]
    families: tuple[str, ...]       # run.yml stream.families, blinded in every payload
    payload_max_words: int          # run.yml nominator.payload_max_words
    reasks: int                     # run.yml nominator.reasks

    def draft(self, pool: Pool, label: str, observations: Sequence[Observation]) -> Draft | NoDecision:
        """One search on the pool shape, then a Draft (payload checked by `check_payload`) or NoDecision."""
        if pool.status in (PoolStatus.MERGED, PoolStatus.NOMINATED):
            raise ValueError(f"{pool.id} is {pool.status.value}; it is not nominated")
        if label not in pool.direction_labels:
            raise ValueError(f"{label!r} is not a label of {pool.id}")
        if not observations:
            raise ValueError(f"{pool.id}: a draft needs the pool's open observations")
        bad = [o.id for o in observations if o.pool_id != pool.id or o.status is not ObservationStatus.OPEN]
        if bad:
            raise ValueError(f"not open observations of {pool.id}: {bad}")
        if not self.tag_vocabulary:
            raise ValueError("the tag vocabulary is empty; a hook has nothing to fire on")
        sources = tuple(self.search.search(pool.shape))
        blinded = (*self.book.blinding["worker"], *self.families)
        asked = ask_json(
            self.client, ROLE,
            render_nominator(self.book, pool, label, observations, sources, self.tag_vocabulary,
                             self.payload_max_words),
            lambda raw: validate_draft(raw, self.tag_vocabulary, self.payload_max_words, blinded,
                                       self.book.hedges, self.book.blinding[ROLE]),
            reasks=self.reasks, tag=f"nominator:{pool.id}",
        )
        if asked.value is None:
            return NoDecision("refused at drafting after the re-ask: " + " | ".join(asked.failures))
        reply = asked.value
        if reply.hook is None:
            return NoDecision(reply.no_decision)
        readings: dict[str, Any] = {"hedges": list(reply.hedges), "code_block": "```" in reply.payload}
        if asked.failures:
            readings["refused_first"] = list(asked.failures)
        return Draft(reply.hook, reply.payload, sources, readings)


def to_decision(
    draft: Draft, decision_id: str, pool: Pool, observations: Sequence[Observation], round_: int,
) -> Decision:
    """The candidate Decision for `draft`: warrant holds the observation ids, lens, rounds and citations."""
    rounds = sorted({o.round for o in observations})
    span = f"{rounds[0]}" if rounds[0] == rounds[-1] else f"{rounds[0]}–{rounds[-1]}"
    warrant = {
        "observations": [o.id for o in observations],
        "lens": pool.lens_id,
        "rounds": span,
        "sources": [c.url for c in draft.sources],
        "citations": [{"url": c.url, "title": c.title} for c in draft.sources],
    }
    return Decision(
        id=decision_id, status=DecisionStatus.CANDIDATE, pool_id=pool.id, hook=draft.hook,
        payload=draft.payload, warrant=warrant, drafted_round=round_, readings=dict(draft.readings),
    )
