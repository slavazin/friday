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
    "telemetry.busy_timeout_ms": "composition root (connect_telemetry)",
    "telemetry.live_poll_ms": "web live view",
    "pilot.min_share_passing": "pilot round",
    "pilot.max_mean_a": "pilot round",
    "stream.path": "goal source",
    "stream.held_out_path": "goal source",
    "stream.tags_path": "tagger store",
    "stream.per_round": "goal minter",
    "stream.held_out": "goal minter",
    "stream.tags_per_request": "tagger",
    "stream.max_new_tags": "tagger",
    "stream.checks_per_regime": "goal minter",
    "stream.check_regenerations": "goal minter",
    "stream.families": "goal minter; blinding test",
    "stream.modes": "goal minter; blinding test",
    "stream.held_out_cells": "goal minter (draw_held_out_cells)",
    "stream.split_max_draws": "goal minter (draw_held_out_cells)",
    "stream.mix": "goal minter",
    "eval.a_weights": "evaluator (A from P, H, C)",
    "eval.load_timeout_ms": "composition root (Playwright)",
    "eval.action_timeout_ms": "composition root (Playwright)",
    "eval.max_wait_ms": "goal minter check validation",
    "eval.test_query": "goal minter",
    "eval.eslint_config": "quality probe",
    "probe.max_controls": "probe suite",
    "probe.settle_ms": "probe suite",
    "probe.sample_text": "probe suite",
    "how_to.max_controls": "how-to check",
    "how_to.repair_penalty": "how-to check",
    "pool.fast_track_share": "recurrence bar (fast-track)",
    "pool.recurrence_min_requests": "recurrence bar (merge of slice s9)",
    "pool.fast_track_min_requests": "recurrence bar (merge of slice s9)",
    "critic.max_parallel_passes": "critic run",
    "critic.max_requests": "critic run",
    "critic.spend_cap_usd": "critic run",
    "judge.max_observations": "judge report call",
    "judge.quote_reasks": "judge calls",
    "nominator.payload_max_words": "nominator",
    "nominator.reasks": "nominator",
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
