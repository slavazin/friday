"""Every run.yml knob has a consumer in the code, or is listed as awaiting one.

A knob nothing reads is a constant the run claims to hold but does not. Remove a knob from PENDING when
its consumer lands; the heuristic below cannot detect a stale entry reliably.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]

# section.key -> the unbuilt consumer that will read it
PENDING = {
    "run.rounds": "runner (halt_reason)",
    "run.spend_cap_usd": "runner (halt_reason)",
    "run.convergence_rounds": "runner (halt_reason)",
    "run.max_parallel_passes": "runner",
    "composition.rank_by": "pass pipeline (select_composed ranks by e-value)",
    "run.transport_reruns": "coordinator rerun tool",
    "run.ping_transport_share": "coordinator ping",
    "pilot.min_artifacts_passing": "pilot round",
    "pilot.max_mean_a": "pilot round",
    "bank.path": "bank loader",
    "bank.training": "bank minter",
    "bank.held_out": "bank minter / split",
    "bank.tags_per_request": "bank minter",
    "bank.tag_vocabulary_size": "bank minter",
    "bank.checks_per_regime": "bank minter",
    "bank.split_max_draws": "bank minter / split",
    "bank.check_regenerations": "bank minter",
    "drift.constraint": "runner request text",
    "worker.repair_turns": "pass pipeline",
    "worker.max_artifact_bytes": "pass pipeline",
    "eval.check_timeout_ms": "composition root (Playwright)",
    "eval.action_timeout_ms": "composition root (Playwright)",
    "eval.max_wait_ms": "bank minter check validation",
    "eval.test_query": "bank minter",
    "eval.eslint_config": "quality probe",
    "eval.viewport": "composition root (Evaluator)",
    "composition.cap": "pass pipeline",
    "gate.in_flight": "composition root (DecisionClose)",
    "detect.ablate_p": "pass pipeline (assign_exposures)",
    "pool.expiry_rounds": "round close",
    "pool.max_direction_labels": "pooling call",
    "lens.window": "round close",
    "lens.mint_every": "round close",
    "lens.residual_min_passes": "round close",
    "lens.mint_max": "lens minter",
    "judge.max_observations": "judge report call",
    "judge.quote_reasks": "judge calls",
    "nominator.payload_max_words": "nominator",
    "nominator.reasks": "nominator",
    "notes.max_tokens": "notes arm",
    "readout.reps": "readout runner",
    "readout.regime": "readout runner",
    "readout.r1_p": "readout runner",
    "readout.interval_level": "readout runner",
    "json.reasks": "roles (ask_json callers)",
    "models.slow_first_call_s": "composition root",
    "search.provider": "composition root",
    "search.api_key_env": "search adapter",
    "search.results": "search adapter",
    "paths.db": "composition root",
    "paths.artifacts": "pass pipeline",
    "paths.journal": "composition root",
    "paths.lenses": "composition root",
    "paths.plans": "coordinator",
    "paths.prompts": "composition root",
    "paths.templates": "composition root",
}


def leaves():
    spec = yaml.safe_load((ROOT / "run.yml").read_text())
    for section, body in spec.items():
        if section == "models":
            yield from (f"models.{k}" for k in body if k != "roles")
            continue
        for key in body:
            yield f"{section}.{key}"


SRC = "\n".join(p.read_text() for p in (ROOT / "friday").rglob("*.py") if p.name != "config.py")


def code_reads(dotted: str) -> bool:
    """True when the code reads `.<key>` as an attribute (consumers alias sections, e.g. `g = spec.gate`).

    A heuristic: a common key name can read as consumed by an unrelated attribute. It catches the costly
    case, a knob nothing reads under any name.
    """
    section, key = dotted.split(".")
    if section == "models" and key == "endpoint":
        key = "base_url"
    return re.search(rf"\.{key}\b", SRC) is not None


def test_every_knob_has_a_consumer_or_is_pending():
    unread = sorted(k for k in leaves() if k not in PENDING and not code_reads(k))
    assert unread == [], f"knobs nothing reads: {unread}"


def test_pending_names_real_knobs():
    assert set(PENDING) <= set(leaves())
