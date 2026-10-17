"""The rubric seam: the model-graded share of the loss (SPEC § 5)."""

from __future__ import annotations

from typing import Protocol

from friday.domain.types import Viewport


class Rubric(Protocol):
    def score(self, screenshot: bytes, request_text: str, artifact_hash: str, viewport: Viewport) -> tuple[int, ...]:
        """The frozen questions' scores on the rubric scale for one viewport's screenshot.

        Cached by (artifact hash, viewport). Raises when the model's answer cannot be read after the re-ask:
        a missing rubric score is never defaulted.
        """
        ...
