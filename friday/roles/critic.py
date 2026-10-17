"""The critic role (SPEC § 17): reads one built artifact as a manager would and returns a short criticism and at
most `critic.max_requests` feature requests in a user's words. It reads the request text, the artifact source,
its how-to and one screenshot taken after load; never an eval reading, a figure, or another pass's artifact.

Also here: the revision turn's message (the worker's one turn after the critic) and the pure match of a feature
request against the revised how-to's declared controls ("requests honored").
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from friday.ports.models import ChatResult, Message, ModelClient
from friday.roles.ask import Asked, Rejected, ask_json
from friday.roles.prompts import PromptBook

ROLE = "critic"
# SPEC § 17 "a short criticism". Pending a run.yml knob (seam request: critic.criticism_max_words).
CRITICISM_MAX_WORDS = 60
# A request is honored by a declared control whose description holds at least this share of the request's
# content words. Pending a run.yml knob (seam request: critic.match_share).
MATCH_SHARE = 0.5
_STEM = 5                       # words compare on their first five letters: "resets" ~ "reset", "colours" ~ "colou"
_STOP = frozenset((
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "could", "do", "does", "for", "from", "get",
    "go", "has", "have", "i", "i'd", "i'm", "id", "im", "if", "in", "into", "is", "it", "its", "it's", "let",
    "lets", "like", "make", "me", "more", "my", "need", "of", "on", "one", "or", "please", "some", "so", "than",
    "that", "the", "their", "them", "then", "there", "these", "they", "this", "to", "too", "want", "was", "way",
    "we", "what", "when", "which", "while", "will", "wish", "with", "would", "you", "your", "also", "just", "able",
    "each", "every", "should", "shows", "show", "see",
))
_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
_SENTENCE_END = re.compile(r"[.!?]\s+[A-Z]")      # a second sentence starts


class CriticUnread(RuntimeError):
    """The critic's reply stayed unusable after its re-ask: the pass has no critic reaction to revise to."""


@dataclass(frozen=True)
class Criticism:
    """The critic's reply: one criticism and the feature requests, each one sentence."""

    criticism: str
    requests: tuple[str, ...]


@dataclass(frozen=True)
class CriticReply:
    criticism: Criticism
    asked: Asked[Criticism]

    @property
    def results(self) -> tuple[ChatResult, ...]:
        return self.asked.results


def how_to_text(html: str) -> str:
    """The artifact's own how-to as it wrote it: the `#how-to` section and the how-to JSON, raw ("" when absent)."""
    parts: list[str] = []
    sec = re.search(r"<section\b[^>]*\bid\s*=\s*[\"']how-to[\"'][^>]*>(.*?)</section\s*>", html,
                    flags=re.IGNORECASE | re.DOTALL)
    if sec:
        parts.append(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", sec.group(1))).strip())
    data = re.search(r"<script\b[^>]*\bid\s*=\s*[\"']how-to-data[\"'][^>]*>(.*?)</script\s*>", html,
                     flags=re.IGNORECASE | re.DOTALL)
    if data:
        parts.append(data.group(1).strip())
    return "\n".join(p for p in parts if p)


def validate_reply(value: Any, max_requests: int) -> Criticism:
    """The reply as a Criticism; raises Rejected with the reason the re-ask states."""
    if not isinstance(value, dict):
        raise Rejected('the reply must be one JSON object {"criticism": "...", "requests": ["..."]}')
    criticism, requests = value.get("criticism"), value.get("requests")
    if not isinstance(criticism, str) or not criticism.strip():
        raise Rejected('"criticism" must be a non-empty string')
    if len(criticism.split()) > CRITICISM_MAX_WORDS:
        raise Rejected(f'"criticism" has {len(criticism.split())} words; keep it to {CRITICISM_MAX_WORDS}')
    if not isinstance(requests, list) or not all(isinstance(r, str) and r.strip() for r in requests):
        raise Rejected('"requests" must be a list of non-empty strings')
    if len(requests) > max_requests:
        raise Rejected(f'"requests" lists {len(requests)}; give at most {max_requests}')
    for r in requests:
        if "\n" in r.strip() or _SENTENCE_END.search(r.strip()):
            raise Rejected(f"each request is one sentence; this one is not: {r.strip()!r}")
    return Criticism(criticism.strip(), tuple(r.strip() for r in requests))


@dataclass
class Critic:
    client: ModelClient
    book: PromptBook
    max_requests: int            # critic.max_requests
    reasks: int                  # json.reasks

    def messages(self, request_text: str, html: str, screenshot: bytes | None,
                 habits: Sequence[str] = ()) -> list[Message]:
        """The critic call's messages: the instructions (with the manager's `habits` in scope for this request,
        when any), then the request, the source and its how-to, with the screenshot attached."""
        system = self.book.text("critic/system.md").strip().replace("{max_requests}", str(self.max_requests))
        system = system.replace("{max_words}", str(CRITICISM_MAX_WORDS))
        if habits:
            listed = "\n".join(f"- {h}" for h in habits)
            system += "\n\n" + self.book.text("critic/habits.md").strip().replace("{habits}", listed)
        user = self.book.text("critic/user.md").strip()
        user = (user.replace("{request}", request_text.strip()).replace("{how_to}", how_to_text(html) or "(none)")
                .replace("{source}", html))
        if not screenshot:
            return [Message("system", system),
                    Message("user", user + "\n\n" + self.book.text("critic/no_screenshot.md").strip())]
        return [Message("system", system), Message("user", user, images=(screenshot,))]

    def criticise(self, request_text: str, html: str, screenshot: bytes | None, tag: str,
                  habits: Sequence[str] = ()) -> CriticReply:
        """The critic's reaction to one artifact (`screenshot` None: the page did not load; `habits`: the
        manager's habits in scope for this request); raises CriticUnread when no usable reply came after the
        re-ask."""
        asked = ask_json(self.client, ROLE, self.messages(request_text, html, screenshot, habits),
                         lambda v: validate_reply(v, self.max_requests), reasks=self.reasks, tag=tag)
        if asked.value is None:
            raise CriticUnread(f"critic reply unusable after {asked.attempts} tries: {'; '.join(asked.failures)}")
        return CriticReply(asked.value, asked)


def revision_message(book: PromptBook, request_text: str, criticism: Criticism) -> Message:
    """The worker's revision turn: the request text with the criticism and the feature requests appended."""
    body = book.text("critic/revision.md").strip()
    lines = [body, book.text("worker/request_heading.md").strip(), request_text.strip(), "",
             book.text("critic/criticism_heading.md").strip(), criticism.criticism]
    if criticism.requests:
        lines += ["", book.text("critic/requests_heading.md").strip()]
        lines += [f"- {r}" for r in criticism.requests]
    return Message("user", "\n".join(lines))


# ---------------------------------------------------------------------------------------------------------
# Requests honored: a request's words against the declared controls' descriptions


def content_stems(text: str) -> frozenset[str]:
    """The text's content words, lowercased, on their first five letters, stop words left out."""
    words = [w.replace("'", "") for w in _WORD.findall(text.lower())]
    return frozenset(w[:_STEM] for w in words if len(w) >= 3 and w not in _STOP)


@dataclass(frozen=True)
class RequestMatch:
    request: str
    control: str | None          # the declared control's check id that best covers the request; None if none does
    share: float                 # the share of the request's content words that control's description holds


def match_request(request: str, controls: Sequence[tuple[str, str]]) -> RequestMatch:
    """The declared control (check id, description) covering `request` best, when it holds at least
    `MATCH_SHARE` of the request's content words; ties go to the first declared."""
    want = content_stems(request)
    if not want:
        return RequestMatch(request, None, 0.0)
    best: tuple[str | None, float] = (None, 0.0)
    for check_id, does in controls:
        share = len(want & content_stems(does)) / len(want)
        if share > best[1]:
            best = (check_id, share)
    return RequestMatch(request, best[0] if best[1] >= MATCH_SHARE else None, best[1])
