"""The worker role: composes the prompt, builds one HTML file, takes at most one self-repair turn."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from friday.domain.types import Composition, Decision
from friday.ports.models import ChatResult, Message, ModelClient
from friday.roles.prompts import PromptBook

ROLE = "worker"


def render_composition(
    book: PromptBook, request_text: str, decisions: Sequence[Decision], notes: str | None = None,
) -> list[Message]:
    """The worker's messages. A candidate renders exactly as an admitted decision: same heading, no status."""
    system = book.text("worker/baseline.md").strip()
    parts: list[str] = []
    if decisions:
        heading = book.text("worker/decisions_heading.md").strip()
        ordered = sorted(decisions, key=lambda d: d.id)
        parts.append(heading + "\n" + "\n".join(f"- {d.payload.strip()}" for d in ordered))
    if notes:
        parts.append(book.text("worker/notes_heading.md").strip() + "\n" + notes.strip())
    parts.append(book.text("worker/request_heading.md").strip() + "\n" + request_text.strip())
    return [Message("system", system), Message("user", "\n\n".join(parts))]


def extract_html(text: str) -> str | None:
    """The HTML document in a worker reply: a fenced block if present, else from <!doctype html> on."""
    fenced = re.search(r"```(?:html)?\s*(<!doctype html.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    start = text.lower().find("<!doctype html")
    return text[start:].strip() if start >= 0 else None


def repair_message(book: PromptBook, console_errors: Sequence[str], missing_ids: Sequence[str]) -> Message:
    """The self-repair turn: the artifact's own console errors and missing contract ids, never the checks."""
    body = book.text("worker/repair.md").strip()
    lines = [f"- console error: {e}" for e in console_errors] + [f"- missing element id: #{i}" for i in missing_ids]
    return Message("user", body + "\n" + "\n".join(lines))


def missing_contract_ids(html: str, contract: Mapping[str, str]) -> list[str]:
    """Contract ids (without '#') that no element in `html` carries."""
    present = set(re.findall(r"""\bid\s*=\s*["']([^"']+)["']""", html))
    return [i.lstrip("#") for i in contract if i.lstrip("#") not in present]


@dataclass
class WorkerOutput:
    html: str | None
    first: ChatResult
    repair: ChatResult | None


@dataclass
class Worker:
    client: ModelClient
    book: PromptBook

    def first_turn(self, messages: Sequence[Message], tag: str) -> ChatResult:
        return self.client.chat(ROLE, messages, tag=tag)

    def repair_turn(self, messages: Sequence[Message], first: ChatResult, repair: Message, tag: str) -> ChatResult:
        return self.client.chat(ROLE, [*messages, Message("assistant", first.text), repair], tag=tag + ":repair")


def composition_decisions(composition: Composition, journal: Mapping[str, Decision]) -> list[Decision]:
    """The Decision objects a composition names, the exposed candidate included."""
    return [journal[i] for i in composition.decision_ids]
