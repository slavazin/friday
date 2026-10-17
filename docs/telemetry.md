# Telemetry catalogue

The run records every timed step, reading and run event in the `telemetry` table of the run database
(`friday/ports/telemetry.py`, `friday/adapters/telemetry.py`). This file lists the rows the run emits. A
row not listed here is not emitted, and a listed row that never appears in a dry run is a wiring defect.

Telemetry is the operational record. No decision reads it: the gate, the detector, lens curation and the
readout read only the evidence stores. The demo page and the timing fit (CARRY_FORWARD S5) read it.

**Rules.**
- Every row carries a scope: `run` (`main` or `critic`), and `round` and `pass_id` when known.
- A span covers the whole step, failures included: an exception closes it with `ok = false` and the
  exception's type and message.
- An absent reading is a metric with `value = null`, never 0.
- No row holds a prompt, a model reply, an artifact's source, a key or an environment value. Rows point at
  where the text lives (a pass id, an artifact path, a cache key) instead.
- Units: `s`, `ms`, `usd`, `tokens`, `count`, `share`, `bytes`, `score`.

## Run

| Row | Kind | Attributes / value |
| --- | --- | --- |
| `run` | span | the whole run, from `friday run` start to halt; `halt_reason` |
| `run.manifest` | event | `spec_hash`, `git_sha`, `seed`, `role_models` (role -> bound model), `fallbacks_taken`, `python`, `playwright`, `chromium`, `host_cpus`, `max_parallel_passes` |
| `run.fallback` | event | `role`, `from_model`, `to_model`, `reason` (pin rule, slow first call, pilot rule) |
| `run.halt` | event | `reason`, `round` |
| `run.spend` | metric (usd) | cumulative spend after each round close |

## Round

| Row | Kind | Attributes / value |
| --- | --- | --- |
| `round` | span | one round: passes plus round close; `regime`, `requests`, `pairs_planned` |
| `round.passes` | span | from the first pass start to the last pass end |
| `round.pass_count` | metric (count) | `outcome`: `scored`, `transport_failed`, `crashed`, `rerun` |
| `round.concurrency` | metric (count) | peak passes in flight |
| `round.spend` | metric (usd) | spend in this round, by `role` |
| `round.ping` | event | `reason` |

## Pass

| Row | Kind | Attributes / value |
| --- | --- | --- |
| `pass` | span | `request_id`, `cell`, `arm`, `regime`, `pair_slot`, `exposed`, `ablated`, `composed` (decision ids), `notes`, `rerun_of`, `worker_model`, `process_pid` |
| `pass.compose` | span | `prompt_sha`, `prompt_tokens_est`, `composed_tokens` (the tokens `Pass.composed_tokens` records) |
| `pass.worker` | span | the first build turn; `model`, `tokens_in`, `tokens_out`, `cost_usd` |
| `pass.repair` | span | the self-repair turn, when it runs; `turn`, `console_errors`, `missing_ids` (counts), `model`, `tokens_in`, `tokens_out`, `cost_usd`, `extracted` |
| `pass.artifact_bytes` | metric (bytes) | `oversize` (over `worker.max_artifact_bytes`; the artifact is kept whole, never truncated) |
| `pass.loss` | metric (score) | also `pass.A`, `pass.R`, `pass.P`, `pass.H`, `pass.C`, `pass.Q`: one metric each |
| `pass.outcome` | event | `outcome`: `scored`, `transport_failed`, `crashed`, `timed_out`; `error`; `dropped_calls` on a scored pass. The pass writes `scored` and `transport_failed`; the parent writes `crashed` and `timed_out` |

## Eval (inside `pass`)

| Row | Kind | Attributes / value |
| --- | --- | --- |
| `eval` | span | `viewports` |
| `eval.viewport` | span | `width`, `height` |
| `eval.load` | span | the plain load that screenshots the page and reads axe; `console_errors` (count), `loaded` |
| `eval.probe` | span | `controls_driven` (controls operated, at most `probe.max_controls`), `responsive`, `silent_controls`, `loaded` |
| `eval.how_to` | span | `rung` (1–4), `steps`, `steps_passed` |
| `eval.checks` | span | `checks`, `checks_passed` |
| `eval.screenshot` | span | `bytes` |
| `eval.rubric` | span | `model` (bound), `questions`, `cached` |
| `eval.quality` | span | `eslint_errors`, `axe_violations` (null when no viewport loaded) |
| `eval.browser_context` | span | context create to close; `shared_server` (true when the shared browser server served it) |

## Judge (inside `pass`)

| Row | Kind | Attributes / value |
| --- | --- | --- |
| `judge.lens_call` | span | `lenses`, `answered`, `reasks` |
| `judge.report_call` | span | `observations`, `fires`, `quote_reasks` |

## Model call (inside whatever step made it)

| Row | Kind | Attributes / value |
| --- | --- | --- |
| `model.call` | span | `role`, `tag`, `requested` (bound model sent), `model` (served), `attempts`, `retry_statuses`, `backoff_s`, `tokens_in`, `tokens_out`, `cost_usd`, `cached`, `structured` (response_format sent) |
| `model.json` | event | `role`, `tag`, `repair_rung`, `reasks`, `ok` (from `ask_json` / `json_repair.decode`) |

## Round close

| Row | Kind | Attributes / value |
| --- | --- | --- |
| `close` | span | the whole close |
| `close.<phase>` | span | one per phase in SPEC § 9 order: `steers`, `lens_curation`, `observations`, `pooling`, `nomination`, `gate`, `detector`, `pings`; each with its counts: steers `flipped`, `notes`; lens_curation `lens_flips`, `lenses_minted`, `lenses_folded`, `lenses_retired`; observations `observations`, `observations_rerun`, `expired`; pooling `pools_new`, `pools_merged`; nomination `nominated`, `no_decision`; gate `admitted`, `rejected`, `expired`, `retired`; detector `suspect`, `retired`, `cleared`, `successors_nominated`; pings `pings`, `halt` |
| `close.wealth` | metric (score) | per candidate under test: `decision_id`, `n`, `mean_d`, `action` |
| `close.lens_value` | metric (score) | per lens: `lens_id`, `n`, `floor` |
| `close.commit` | event | `subject_id`, `sha` (null: the Journal and LensSet ports return no sha), `cause` |

## Goals, tagging, readout, critic

| Row | Kind | Attributes / value |
| --- | --- | --- |
| `goals.mint` | span | one minted request; `cell`, `attempts`, `check_regenerations`, `refused` |
| `goals.tag` | span | one round's tagging (or the held-out set's, `held_out`); `round`, `requests`, `new_tags` |
| `readout` | span | the readout runner; `passes`, `reps` |
| `readout.reading` | event | `reading` (R1–R4 …), `population`, `estimate`, `lower_bound`, `claim_above`, `p`, `verdict` |
| `critic.pass` | span | under `run = critic`; `requests_made`, `requests_honored` |
