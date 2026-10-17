"""The lens set as markdown (`lenses/L-NN.md`), a projection of the event log like the Journal."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from pathlib import Path

import yaml

from friday.domain.events import StatusFlip
from friday.domain.lifecycle import check_lens_flip
from friday.domain.types import Lens, LensStatus
from friday.ports.vcs import VersionControl


def render_lens(lens: Lens) -> str:
    front = {"id": lens.id, "status": lens.status.value, "minted_round": lens.minted_round,
             "superseded_by": lens.superseded_by, "has_cleared_floor": lens.has_cleared_floor}
    return (f"---\n{yaml.safe_dump(front, sort_keys=False).rstrip()}\n---\n"
            f"## Angle\n{lens.angle.strip()}\n\n## Counterfactual\n{lens.counterfactual.strip()}\n")


def parse_lens(text: str) -> Lens:
    _, front_raw, body = text.split("---\n", 2)
    front = yaml.safe_load(front_raw)
    angle = body.split("## Angle\n", 1)[1].split("\n## ", 1)[0].strip()
    counter = body.split("## Counterfactual\n", 1)[1].strip()
    return Lens(front["id"], angle, counter, LensStatus(front["status"]), front.get("minted_round"),
                front.get("superseded_by"), bool(front.get("has_cleared_floor")))


class MarkdownLensSet:
    def __init__(self, root: str | Path, vcs: VersionControl) -> None:
        self.root, self.vcs = Path(root), vcs

    def lenses(self) -> Sequence[Lens]:
        return [parse_lens(p.read_text()) for p in sorted(self.root.glob("L-*.md"))]

    def add(self, lens: Lens) -> None:
        path = self.root / f"{lens.id}.md"
        if path.exists():
            raise FileExistsError(f"{lens.id} exists; ids are reserved once")
        path.write_text(render_lens(lens))

    def flip(self, flip: StatusFlip, message: str) -> Lens:
        path = self.root / f"{flip.subject_id}.md"
        old = parse_lens(path.read_text())
        new_status = LensStatus(flip.new)
        check_lens_flip(old.status, new_status, flip.cause)
        new = dataclasses.replace(
            old, status=new_status,
            superseded_by=flip.successors[0] if flip.successors else old.superseded_by,
            has_cleared_floor=old.has_cleared_floor or new_status is LensStatus.ACTIVE,
        )
        path.write_text(render_lens(new))
        self.vcs.commit([str(path)], message)
        return new
