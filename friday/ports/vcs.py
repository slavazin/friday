"""Version control seam: one commit per status flip."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class VersionControl(Protocol):
    def commit(self, paths: Sequence[str], message: str) -> str:
        """Stages `paths`, commits with `message`, returns the commit id."""
        ...
