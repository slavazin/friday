"""The eval service: acceptance checks, rubric, quality reading, loss -> EvalReport."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from friday.algorithms.loss import LossModel
from friday.domain.types import EvalReport, Pass, Regime, Request
from friday.eval.dsl import run_check
from friday.ports.browser import Browser, QualityProbe


class Rubric(Protocol):
    def score(self, screenshot: bytes, request_text: str, artifact_hash: str) -> tuple[int, ...]:
        """The frozen questions' scores on the rubric scale (cached by artifact hash)."""
        ...


@dataclass
class Evaluator:
    browser: Browser
    rubric: Rubric
    quality: QualityProbe
    loss_model: LossModel
    viewport: tuple[int, int]

    def run(self, pass_: Pass, request: Request, regime: Regime, request_text: str) -> EvalReport:
        """Scores one pass's artifact under `regime`. `request_text` is the text the worker read."""
        if pass_.artifact_path is None:
            raise ValueError(f"{pass_.id} has no artifact")
        path = pass_.artifact_path
        html = Path(path).read_text()
        checks = request.checks_for(regime)

        def open_page():
            return self.browser.page(path, self.viewport)

        results = tuple(run_check(open_page, c) for c in checks)
        shot: bytes | None
        try:
            with open_page() as page:
                errors = tuple(page.console_errors())
                shot = page.screenshot()
                axe = self.quality.axe_violations(page)
        except Exception as e:  # noqa: BLE001 - an artifact that cannot load is scored, not dropped
            # Every check has already failed on load; the rubric's floor is the true reading of a page
            # nothing renders on. Recorded in console_errors so the report call sees why.
            errors, shot, axe = (f"load failed: {type(e).__name__}: {e}",), None, 0
        eslint = self.quality.eslint_errors(html)
        lo, _ = self.loss_model.rubric_scale
        rubric: Sequence[int] = (
            (lo,) * self.loss_model.rubric_questions if shot is None
            else self.rubric.score(shot, request_text, hashlib.sha256(html.encode()).hexdigest())
        )
        a = self.loss_model.acceptance(results)
        r = self.loss_model.rubric(rubric)
        return EvalReport(
            pass_id=pass_.id, regime=regime, checks=results, console_errors=errors, A=a, R=r,
            rubric=tuple(rubric), Q=eslint + axe, loss=self.loss_model.loss(a, r),
            eslint_errors=eslint, axe_violations=axe,
        )
