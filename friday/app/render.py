"""Renders status-flip commit messages from templates; every figure comes from its home."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from friday.domain.events import StatusFlip


class _Missing(dict):
    def __missing__(self, key: str) -> str:
        raise KeyError(f"flip template needs {key!r}, which the flip's evidence lacks")


@dataclass(frozen=True)
class FlipRenderer:
    templates: Mapping[str, Mapping[str, str]]

    @classmethod
    def load(cls, path: str | Path) -> FlipRenderer:
        return cls(yaml.safe_load(Path(path).read_text()))

    def render(self, family: str, flip: StatusFlip, constants: Mapping[str, Any] | None = None) -> str:
        """The commit message for `flip`; raises KeyError when a figure the template names is missing."""
        fields = _Missing({"id": flip.subject_id, "old": flip.old, "new": flip.new,
                           **(constants or {}), **flip.evidence})
        return self.templates[family][flip.cause.value].format_map(fields)
