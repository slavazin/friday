"""The Journal as markdown in git (`journal/D-NNNN.md`), a projection of the event log.

Front matter holds the machine fields; the body renders them for a reader of the public repository.
Hook and payload are frozen at drafting: `flip` rewrites only status, lineage and the rendered Ledger line.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from friday.domain.events import StatusFlip
from friday.domain.hooks import text_hash
from friday.domain.lifecycle import check_decision_flip
from friday.domain.types import Decision, DecisionStatus, Hook
from friday.ports.vcs import VersionControl


class FrozenTextViolation(RuntimeError):
    pass


def render_decision(d: Decision) -> str:
    """The markdown file for `d`."""
    front = {
        "id": d.id,
        "status": d.status.value,
        "pool": d.pool_id,
        "fires_when": sorted(d.hook.fires_when),
        "not_this": sorted(d.hook.not_this),
        "supersedes": list(d.supersedes),
        "successors": list(d.successors),
        "drafted_round": d.drafted_round,
        "text_sha": text_hash(d.hook, d.payload),
        "warrant": d.warrant,
        "readings": d.readings,
    }
    w = d.warrant
    lines = [
        "---", yaml.safe_dump(front, sort_keys=False).rstrip(), "---",
        "## Payload", d.payload.strip(), "",
        "## Warrant",
        f"- Observations: {', '.join(w.get('observations', [])) or 'none'}"
        + (f" (pool {d.pool_id}, lens {w['lens']}, rounds {w['rounds']})" if "lens" in w and "rounds" in w else ""),
        f"- Sources: {', '.join(w.get('sources', [])) or 'none'}",
        f"- Ledger: {w.get('ledger', 'no pairs yet')}", "",
        "## Enforcement",
        "Judge disposition on every composed pass. Residue: nothing checks that the payload's reason is the "
        "cause of the loss it lowers.", "",
        "## Lifecycle",
        "Retire when: the detector moves it to `suspect`, no successor is admitted, and the confirmation pairs "
        "hold; or a steer retires it.",
        "Moot when: no request in the bank carries a `fires_when` tag.", "",
    ]
    return "\n".join(lines)


def parse_decision(text: str) -> Decision:
    """The Decision a file rendered by `render_decision` holds."""
    _, front_raw, body = text.split("---\n", 2)
    front: dict[str, Any] = yaml.safe_load(front_raw)
    payload = body.split("## Payload\n", 1)[1].split("\n## ", 1)[0].strip()
    return Decision(
        id=front["id"],
        status=DecisionStatus(front["status"]),
        pool_id=front.get("pool"),
        hook=Hook(frozenset(front.get("fires_when", [])), frozenset(front.get("not_this", []))),
        payload=payload,
        warrant=front.get("warrant") or {},
        supersedes=tuple(front.get("supersedes") or ()),
        successors=tuple(front.get("successors") or ()),
        drafted_round=int(front.get("drafted_round", 0)),
        readings=front.get("readings") or {},
    )


class MarkdownJournal:
    def __init__(self, root: str | Path, vcs: VersionControl) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.vcs = vcs

    def _path(self, decision_id: str) -> Path:
        return self.root / f"{decision_id}.md"

    def decisions(self) -> Sequence[Decision]:
        return [parse_decision(p.read_text()) for p in sorted(self.root.glob("D-*.md"))]

    def get(self, decision_id: str) -> Decision:
        return parse_decision(self._path(decision_id).read_text())

    def draft(self, decision: Decision) -> None:
        path = self._path(decision.id)
        if path.exists():
            raise FrozenTextViolation(f"{decision.id} already drafted; ids are reserved once")
        if decision.status is not DecisionStatus.CANDIDATE:
            raise ValueError("a decision is drafted as a candidate")
        path.write_text(render_decision(decision))

    def flip(self, flip: StatusFlip, message: str) -> Decision:
        path = self._path(flip.subject_id)
        text = path.read_text()
        old = parse_decision(text)
        stored = yaml.safe_load(text.split("---\n", 2)[1]).get("text_sha")
        if stored != text_hash(old.hook, old.payload):
            raise FrozenTextViolation(f"{flip.subject_id}: hook or payload changed since drafting")
        new_status = DecisionStatus(flip.new)
        check_decision_flip(old.status, new_status, flip.cause)
        warrant = dict(old.warrant)
        if "ledger" in flip.evidence:
            warrant["ledger"] = flip.evidence["ledger"]
        new = dataclasses.replace(
            old, status=new_status, warrant=warrant,
            successors=tuple(sorted(set(old.successors) | set(flip.successors))),
        )
        path.write_text(render_decision(new))
        self.vcs.commit([str(path)], message)
        return new
