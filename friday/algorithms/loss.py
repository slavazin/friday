"""The loss the gate reads (SPEC § 5): loss = 1 - (w_a * A + w_r * R), A = a_w.probe P + a_w.how_to H + a_w.checks C."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from friday.domain.types import CheckResult


@dataclass(frozen=True)
class LossModel:
    w_a: float
    w_r: float
    rubric_scale: tuple[int, int] = (1, 5)
    rubric_questions: int = 3
    a_weights: Mapping[str, float] | None = None   # run.yml eval.a_weights: probe, how_to, checks

    def a_mix(self, p: float, h: float, c: float) -> float:
        """A: the eval.a_weights mix of the probe share P, the how-to fraction H and the checks share C."""
        if self.a_weights is None:
            raise ValueError("a_weights not configured (run.yml eval.a_weights)")
        w = self.a_weights
        if set(w) != {"probe", "how_to", "checks"}:
            raise ValueError(f"a_weights must hold probe, how_to and checks, got {sorted(w)}")
        for name, v in (("P", p), ("H", h), ("C", c)):
            if not 0.0 <= v <= 1.0:
                raise ValueError(f"{name} = {v} outside [0, 1]")
        return w["probe"] * p + w["how_to"] * h + w["checks"] * c

    def acceptance(self, results: Sequence[CheckResult]) -> float:
        """A: the passed fraction of acceptance checks. Raises on no checks: a request has 4-6 per regime."""
        if not results:
            raise ValueError("no acceptance checks to score")
        return sum(r.passed for r in results) / len(results)

    def rubric(self, scores: Sequence[int]) -> float:
        """R: rubric scores mapped to [0, 1] and averaged. Raises unless every question is scored."""
        lo, hi = self.rubric_scale
        if len(scores) != self.rubric_questions:
            raise ValueError(f"expected {self.rubric_questions} rubric scores, got {len(scores)}")
        for s in scores:
            if not lo <= s <= hi:
                raise ValueError(f"rubric score {s} outside {lo}..{hi}")
        return sum((s - lo) / (hi - lo) for s in scores) / len(scores)

    def loss(self, a: float, r: float) -> float:
        """The scalar on [0, 1]; lower is better."""
        if not (0.0 <= a <= 1.0 and 0.0 <= r <= 1.0):
            raise ValueError("A and R must lie in [0, 1]")
        return 1.0 - (self.w_a * a + self.w_r * r)
