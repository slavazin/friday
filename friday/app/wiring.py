"""Composition root: builds every algorithm and adapter from the RunSpec. The only module that chooses
implementations; everything else receives its collaborators."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from friday.algorithms.detector import Detector
from friday.algorithms.gate import GateRule
from friday.algorithms.lens_curation import CurationParams
from friday.algorithms.loss import LossModel
from friday.algorithms.recurrence import RecurrenceBar
from friday.config import RunSpec
from friday.stats.bounds import UPPER_BOUNDS
from friday.stats.eprocess import BETTING
from friday.stats.ranks import CORRELATIONS, LENS_VALUES
from friday.stats.significance import PAIRED_TESTS


@dataclass(frozen=True)
class Mechanics:
    """The run's pure algorithms, configured from run.yml."""

    loss: LossModel
    gate: GateRule
    detector: Detector
    bar: RecurrenceBar
    curation: CurationParams
    lens_value: object
    fold_correlation: object
    readout_test: object


def build_mechanics(spec: RunSpec) -> Mechanics:
    """Every algorithm the run uses, selected and parameterised by `spec`."""
    betting = BETTING.build(spec.gate.betting.name, **spec.gate.betting.params)
    bound = UPPER_BOUNDS.build(spec.gate.upper_bound)
    g, dt, ln = spec.gate, spec.detect, spec.lens
    return Mechanics(
        loss=LossModel(spec.eval.w_a, spec.eval.w_r, spec.eval.rubric_scale, spec.eval.rubric_questions,
                       dict(spec.eval.a_weights)),
        gate=GateRule(g.alpha, g.n_min, g.n_max, g.quality_margin, g.quality_level, betting, bound),
        detector=Detector(dt.window, dt.level, g.alpha, dt.confirm_pairs, dt.contradicted_share, betting, bound),
        bar=RecurrenceBar(
            spec.pool.recurrence_min_observations, spec.pool.recurrence_min_rounds,
            fast_track_share=spec.pool.fast_track_share, min_requests=spec.pool.recurrence_min_requests,
            fast_track_min_requests=spec.pool.fast_track_min_requests,
        ),
        curation=CurationParams(
            n_min=ln.n_min, flag_p=ln.flag_p, floor_quantile=ln.floor_quantile, floor_shuffles=ln.floor_shuffles,
            floor_strikes=ln.floor_strikes, fold_rho=ln.fold_rho, residual_loss=ln.residual_loss, cap=ln.cap,
        ),
        lens_value=LENS_VALUES.build(ln.value),
        fold_correlation=CORRELATIONS.build(ln.fold_correlation),
        readout_test=PAIRED_TESTS.build(spec.readout.test),
    )


def flip_constants(spec: RunSpec) -> dict[str, float | int]:
    """run.yml figures the flip templates cite beside the Ledger's."""
    return {
        "threshold": 1.0 / spec.gate.alpha, "margin": spec.gate.quality_margin, "n_max": spec.gate.n_max,
        "level_pct": spec.detect.level * 100, "fold_rho": spec.lens.fold_rho, "cap": spec.lens.cap,
    }


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def evaluator_settings(spec: RunSpec) -> dict[str, Any]:
    """The Evaluator's run.yml figures (everything but its browser, rubric and quality probe)."""
    return {
        "loss_model": build_mechanics(spec).loss,
        "viewports": {k: tuple(tuple(v) for v in vs) for k, vs in spec.eval.viewports.items()},
        "test_query": spec.eval.test_query, "probe_max_controls": spec.probe.max_controls,
        "settle_ms": spec.probe.settle_ms, "sample_text": spec.probe.sample_text,
        "how_to_max_controls": spec.how_to.max_controls, "repair_penalty": spec.how_to.repair_penalty,
    }


def with_optional(fn: Callable[..., Any], /, *args: Any, **optional: Any) -> Any:
    """Calls `fn(*args, **kw)` passing each optional keyword only when `fn`'s signature takes it.

    Telemetry parameters land in the steps' signatures one by one (S18); until a step takes one, it is
    left out instead of raising."""
    params = inspect.signature(fn).parameters
    takes_any = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    return fn(*args, **{k: v for k, v in optional.items() if takes_any or k in params})
