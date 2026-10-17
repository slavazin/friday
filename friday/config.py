"""Typed view of `run.yml`, the one home of every constant, pin and seed.

Loading is strict: an unknown key or a missing key raises, so a typo cannot silently fall back to a
default. The spec hash is recorded with the run so a changed constant is visible as a new run.
"""

from __future__ import annotations

import dataclasses
import hashlib
import types
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class RunSection:
    seed: int
    rounds: int
    spend_cap_usd: float
    max_parallel_passes: int
    convergence_rounds: int
    transport_reruns: int
    ping_transport_share: float


@dataclass(frozen=True)
class PilotSection:
    min_artifacts_passing: int
    max_mean_a: float


@dataclass(frozen=True)
class BankSection:
    path: str
    training: int
    held_out: int
    tags_per_request: tuple[int, int]
    tag_vocabulary_size: tuple[int, int]
    checks_per_regime: tuple[int, int]
    split_max_draws: int
    check_regenerations: int


@dataclass(frozen=True)
class DriftSection:
    round: int
    constraint: str


@dataclass(frozen=True)
class WorkerSection:
    repair_turns: int
    max_artifact_bytes: int


@dataclass(frozen=True)
class EvalSection:
    w_a: float
    w_r: float
    viewport: tuple[int, int]
    check_timeout_ms: int
    action_timeout_ms: int
    max_wait_ms: int
    rubric_scale: tuple[int, int]
    rubric_questions: int
    test_query: str
    eslint_config: str


@dataclass(frozen=True)
class CompositionSection:
    cap: int
    rank_by: str


@dataclass(frozen=True)
class BettingSection:
    name: str
    params: dict[str, float]


@dataclass(frozen=True)
class GateSection:
    alpha: float
    n_min: int
    n_max: int
    in_flight: int
    quality_margin: float
    quality_level: float
    betting: BettingSection
    upper_bound: str


@dataclass(frozen=True)
class DetectSection:
    ablate_p: float
    window: int
    level: float
    confirm_pairs: int
    contradicted_share: float


@dataclass(frozen=True)
class PoolSection:
    expiry_rounds: int
    recurrence_min_observations: int
    recurrence_min_rounds: int
    max_direction_labels: int


@dataclass(frozen=True)
class LensSection:
    window: int
    n_min: int
    flag_p: float
    value: str
    floor_quantile: float
    floor_shuffles: int
    floor_strikes: int
    fold_correlation: str
    fold_rho: float
    mint_every: int
    residual_loss: float
    residual_min_passes: int
    mint_max: int
    cap: int


@dataclass(frozen=True)
class JudgeSection:
    max_observations: int
    quote_reasks: int


@dataclass(frozen=True)
class NominatorSection:
    payload_max_words: int
    reasks: int


@dataclass(frozen=True)
class NotesSection:
    max_tokens: int


@dataclass(frozen=True)
class ReadoutSection:
    reps: int
    regime: str
    r1_p: float
    interval_level: float
    test: str


@dataclass(frozen=True)
class TransportSection:
    max_tries: int
    backoff_base_s: float
    backoff_cap_s: float
    retry_status: tuple[int, ...]
    request_timeout_s: float


@dataclass(frozen=True)
class JsonSection:
    reasks: int


@dataclass(frozen=True)
class EndpointSection:
    base_url: str
    api_key_env: str


@dataclass(frozen=True)
class RolePin:
    pins: tuple[str, ...]
    fallback: str
    temperature: float
    seed: int | None = None
    requires_image: bool = False
    slow_first_call_group: str | None = None


@dataclass(frozen=True)
class ModelsSection:
    endpoint: EndpointSection
    price_overrides: dict[str, dict[str, float]]
    slow_first_call_s: float
    roles: dict[str, RolePin]


@dataclass(frozen=True)
class SearchSection:
    provider: str
    api_key_env: str
    results: int


@dataclass(frozen=True)
class PathsSection:
    db: str
    artifacts: str
    journal: str
    lenses: str
    plans: str
    prompts: str
    templates: str


@dataclass(frozen=True)
class RunSpec:
    run: RunSection
    pilot: PilotSection
    bank: BankSection
    drift: DriftSection
    worker: WorkerSection
    eval: EvalSection
    composition: CompositionSection
    gate: GateSection
    detect: DetectSection
    pool: PoolSection
    lens: LensSection
    judge: JudgeSection
    nominator: NominatorSection
    notes: NotesSection
    readout: ReadoutSection
    transport: TransportSection
    json: JsonSection
    models: ModelsSection
    search: SearchSection
    paths: PathsSection
    spec_hash: str = field(default="", compare=False)

    def validate(self) -> None:
        """Raises ConfigError on values that would void the run's arithmetic."""
        problems: list[str] = []
        if abs(self.eval.w_a + self.eval.w_r - 1.0) > 1e-9:
            problems.append("eval.w_a + eval.w_r must equal 1")
        if not 0.0 < self.gate.alpha < 1.0:
            problems.append("gate.alpha must lie in (0, 1)")
        if self.gate.n_min > self.gate.n_max:
            problems.append("gate.n_min must not exceed gate.n_max")
        if not 1 <= self.drift.round <= self.run.rounds:
            problems.append("drift.round must lie within run.rounds")
        if not 0.0 <= self.detect.ablate_p <= 1.0:
            problems.append("detect.ablate_p must lie in [0, 1]")
        if self.lens.n_min > self.lens.window:
            problems.append("lens.n_min must not exceed lens.window")
        if self.readout.regime not in ("R1", "R2"):
            problems.append("readout.regime must be R1 or R2")
        levels = {"gate.quality_level": self.gate.quality_level, "detect.level": self.detect.level,
                  "readout.interval_level": self.readout.interval_level,
                  "lens.floor_quantile": self.lens.floor_quantile}
        problems += [f"{k} must lie in (0, 1)" for k, v in levels.items() if not 0.0 < v < 1.0]
        if self.detect.window < 2 or self.detect.confirm_pairs < 2:
            problems.append("detect.window and detect.confirm_pairs need >= 2 deltas for a t-bound")
        if self.composition.rank_by != "e_value":
            problems.append("composition.rank_by: only 'e_value' is implemented")
        if self.eval.rubric_questions < 1:
            problems.append("eval.rubric_questions must be >= 1")
        if problems:
            raise ConfigError("; ".join(problems))


def _coerce(tp: Any, value: Any, where: str) -> Any:
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if dataclasses.is_dataclass(tp):
        return _build(tp, value, where)
    if origin in (types.UnionType, typing.Union):
        if value is None and type(None) in args:
            return None
        inner = [a for a in args if a is not type(None)]
        return _coerce(inner[0], value, where)
    if origin is tuple:
        if not isinstance(value, list | tuple):
            raise ConfigError(f"{where}: expected a list")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_coerce(args[0], v, f"{where}[]") for v in value)
        if len(value) != len(args):
            raise ConfigError(f"{where}: expected {len(args)} items")
        return tuple(_coerce(a, v, f"{where}[{i}]") for i, (a, v) in enumerate(zip(args, value, strict=True)))
    if origin is dict:
        if not isinstance(value, dict):
            raise ConfigError(f"{where}: expected a mapping")
        return {str(k): _coerce(args[1], v, f"{where}.{k}") for k, v in value.items()}
    if tp is float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ConfigError(f"{where}: expected a number")
        return float(value)
    if tp in (int, str, bool):
        if not isinstance(value, tp) or (tp is int and isinstance(value, bool)):
            raise ConfigError(f"{where}: expected {tp.__name__}, got {type(value).__name__}")
        return value
    return value


def _build(cls: Any, raw: Any, where: str) -> Any:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: expected a mapping")
    hints = typing.get_type_hints(cls)
    fields = {f.name: f for f in dataclasses.fields(cls) if f.init and f.name != "spec_hash"}
    unknown = set(raw) - set(fields)
    if unknown:
        raise ConfigError(f"{where}: unknown keys {sorted(unknown)}")
    kwargs: dict[str, Any] = {}
    for name, f in fields.items():
        if name not in raw:
            if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
                raise ConfigError(f"{where}.{name}: missing")
            continue
        kwargs[name] = _coerce(hints[name], raw[name], f"{where}.{name}")
    return cls(**kwargs)


def parse_spec(text: str) -> RunSpec:
    """Returns the validated RunSpec for the YAML `text`."""
    spec: RunSpec = _build(RunSpec, yaml.safe_load(text), "run.yml")
    spec = dataclasses.replace(spec, spec_hash=hashlib.sha256(text.encode()).hexdigest()[:16])
    spec.validate()
    return spec


def load_spec(path: str | Path = "run.yml") -> RunSpec:
    """Returns the validated RunSpec read from `path`."""
    return parse_spec(Path(path).read_text())
