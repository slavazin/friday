"""Prompt templates loaded from `prompts/` (frozen before round 1). One loader, so a test can render every
role's template and scan it against the blinding list."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import yaml


@dataclass(frozen=True)
class PromptBook:
    root: Path

    def text(self, name: str) -> str:
        return (self.root / name).read_text()

    @cached_property
    def blinding(self) -> dict[str, tuple[str, ...]]:
        """Reader -> words no template it receives may name."""
        raw = yaml.safe_load(self.text("blinding.yml"))
        return {k: tuple(v) for k, v in raw["readers"].items()}

    @cached_property
    def hedges(self) -> tuple[str, ...]:
        return tuple(yaml.safe_load(self.text("blinding.yml"))["hedges"])
