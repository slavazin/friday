"""The notes arm's shared notes file (SPEC § 12, § 2 notes arm).

Every `free` observation from a journal-arm pass is appended as one line, deduplicated by exact text. Every
notes-arm worker reads the file truncated to its most recent `notes.max_tokens`. The round close appends
(it is the single writer); a pass only reads the text it was handed at round start.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from pathlib import Path

from friday.domain.types import FREE_LENS, Arm, Observation


def approx_tokens(text: str, chars_per_token: int) -> int:
    """Tokens `text` is counted as against `notes.max_tokens`: ceil(characters / `notes.chars_per_token`).

    The character proxy stands in for a tokenizer; none is pinned."""
    if chars_per_token < 1:
        raise ValueError("notes.chars_per_token must be positive")
    return math.ceil(len(text) / chars_per_token)


def note_line(observation: Observation) -> str:
    """The one line an observation contributes: its shape, then its direction, on one line."""
    shape = " ".join(observation.shape.split())
    direction = " ".join(observation.direction.split())
    if not shape or not direction:
        raise ValueError(f"{observation.id}: an observation needs a shape and a direction")
    return f"- {shape} {direction}"


def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line for line in path.read_text().splitlines() if line.strip()]


def append_free(path: str | Path, observations: Sequence[Observation], arm_of: Mapping[str, Arm]) -> list[str]:
    """Appends the `free` observations' lines not already in the file; returns the lines written.

    `arm_of` maps each observation's pass id to its arm. Raises when a `free` observation comes from a pass
    that is not a journal-arm pass, or from a pass `arm_of` does not know: notes are fed by the journal arm
    only, and an unknown origin is a wiring error, not a skip.
    """
    path = Path(path)
    existing = set(_read_lines(path))
    written: list[str] = []
    for o in sorted(observations, key=lambda o: o.id):
        if o.lens_id != FREE_LENS:
            continue
        arm = arm_of.get(o.pass_id)
        if arm is not Arm.JOURNAL:
            raise ValueError(f"{o.id}: free observation from pass {o.pass_id} of arm {arm}; notes take journal only")
        line = note_line(o)
        if line in existing:
            continue
        existing.add(line)
        written.append(line)
    if written:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.writelines(line + "\n" for line in written)
    return written


def notes_text(path: str | Path, max_tokens: int, chars_per_token: int) -> str | None:
    """The most recent whole lines of the notes file within `max_tokens`, oldest first; None when none fit.

    Lines are kept whole: a line that would cross the budget is left out together with every older line.
    """
    if max_tokens < 1:
        raise ValueError("notes.max_tokens must be positive")
    kept: list[str] = []
    used = 0
    for line in reversed(_read_lines(Path(path))):
        cost = approx_tokens(line + "\n", chars_per_token)
        if used + cost > max_tokens:
            break
        kept.append(line)
        used += cost
    return "\n".join(reversed(kept)) if kept else None
