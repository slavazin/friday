"""Id minting. Ids are reserved once, never reused, and gaps are allowed (a burned id stays burned)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

ID_PATTERNS: dict[str, tuple[str, int]] = {
    "decision": ("D", 4),
    "pool": ("P", 4),
    "observation": ("O", 4),
    "lens": ("L", 2),
}

ANY_RUN_ID = re.compile(r"\b[ODPL]-\d{2,}\b")


def format_id(kind: str, n: int) -> str:
    prefix, width = ID_PATTERNS[kind]
    return f"{prefix}-{n:0{width}d}"


def parse_id(value: str) -> tuple[str, int]:
    """Returns (prefix, number) of an id such as 'D-0007'."""
    prefix, _, digits = value.partition("-")
    if not digits.isdigit():
        raise ValueError(f"not a run id: {value!r}")
    return prefix, int(digits)


@dataclass
class IdMinter:
    """Mints the next id of one kind past the highest already seen."""

    kind: str
    last: int = 0

    @classmethod
    def resume(cls, kind: str, existing: Iterable[str]) -> IdMinter:
        prefix, _ = ID_PATTERNS[kind]
        nums = [parse_id(i)[1] for i in existing if i.startswith(prefix + "-")]
        return cls(kind, max(nums, default=0))

    def next(self) -> str:
        self.last += 1
        return format_id(self.kind, self.last)
