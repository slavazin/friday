# Architecture

How the code realises [SPEC.md](SPEC.md). The spec is the authority on *what*; this file covers *where*
each part lives and which seams keep it changeable. Terms follow the SPEC § 2 vocabulary.

## Layers

```
friday/
  domain/      entities, lifecycles, hooks, ids, events             pure; stdlib only
  stats/       e-process, t-bounds, ranks, permutation null,       pure numerics; no domain knowledge
               sign-flip test, counter-based rng
  algorithms/  loss, composition, exposure, gate, detector,        pure; domain + stats
               lens curation, recurrence bar, split, readout,
               halt, payload and quote checks
  ports/       Protocols: ModelClient, SearchClient, Browser/Page, the seams every layer above codes against
               QualityProbe, stores, Journal, LensSet, VersionControl
  support/     the repairing JSON decoder                           pure
  eval/        the check DSL interpreter and the Evaluator          over the Page port only
  roles/       worker, judge, nominator, pooling call, lens minter, model-facing; over ports only
               goal minter, tagger, coordinator tool surface, prompt loader
  adapters/    Crusoe client, Playwright, SQLite, markdown Journal  the only place a vendor SDK or the
               and lens set, git, in-memory stores                  network appears
  app/         composition root (wiring), round close, Ledger       orchestration; the single writer
               rows, pin resolution, flip rendering
  config.py    typed, strict view of run.yml
  registry.py  named registries for interchangeable algorithms
  types.py     the Appendix B seam (re-exports domain/types.py)
```

`tests/architecture/test_layers.py` enforces these import rules from the source with `ast`. A forbidden
import fails the suite.

## The seams that keep doors open

**Interchangeable algorithms.** Every statistic with a credible alternative is registered by name
and selected in `run.yml`:

| run.yml key             | Registry                         | Default            |
| ----------------------- | -------------------------------- | ------------------ |
| `gate.betting.name`     | `stats.eprocess.BETTING`         | `agrapa`           |
| `gate.upper_bound`      | `stats.bounds.UPPER_BOUNDS`      | `student_t`        |
| `lens.value`            | `stats.ranks.LENS_VALUES`        | `auc`              |
| `lens.fold_correlation` | `stats.ranks.CORRELATIONS`       | `spearman`         |
| `readout.test`          | `stats.significance.PAIRED_TESTS`| `sign_flip_exact`  |

To swap one, register a class under a new key and change the key. No call site changes. An unknown key
raises at start-up.

**Configuration.** `run.yml` is the one home of every constant. `config.py` loads it strictly: an unknown
key, a missing key or a wrong type raises, so a typo cannot fall back to a default.
`tests/unit/test_config_json.py::test_every_knob_reaches_its_algorithm` checks that knobs reach their
consumers. The spec hash is recorded with the run.

**Randomness.** Every draw is a pure function of `(seed, purpose, keys)` (`stats/rng.py`). Adding a draw
anywhere never moves the exposure coin of `(round 5, category c)`.

**Evidence and status.**
- The Ledger (`ledger_deltas`) and the event log are append-only, enforced by SQLite triggers. The one
  exception is a steer's `applied_round`, which may be set once.
- E-process wealth is always recomputed from the deltas and never stored.
- A status flip is validated against the lifecycle tables in `domain/lifecycle.py` before anything is
  written. The round close then appends it to the event log and writes the Journal or lens file, one git
  commit each.
- Status is read from the file's front matter, which is written only by that step. The event log is the
  history the timeline and the detector read: when a decision was admitted, suspected or cleared.
- A decision's hook and payload are frozen at drafting. The file carries their hash (`text_sha`), and a
  flip on a file whose text no longer matches it is refused.

**Models.**
- Roles name a role, never a model id. Pins, fallbacks and temperatures live in `run.yml`.
- `app/pins.py` resolves the pins against `GET /v1/models` at start-up.
- Prices are read from the same listing, unless `models.price_overrides` names a model.
- Every call writes a spend row, failed calls included. The row holds the parameters actually sent and the
  model string the service echoed.

**Blinding.** Worker templates live in `prompts/worker/`. `prompts/blinding.yml` holds each reader's list.
`tests/contract/test_prompts.py` scans the templates (never request text) and checks two more things:
- a candidate renders exactly as an admitted decision;
- the repair turn shows errors and missing ids, never checks.

## Choices the spec left open (fixed here; change in one place)

| Choice | Where |
| --- | --- |
| AGrapa variance is the population variance of past deltas | `stats/eprocess.py` |
| Gate precedence on one reading: admit, then refuse, then expire | `algorithms/gate.py` |
| Exposure counts update as pairs are assigned within a round, in category-id order | `algorithms/exposure.py` |
| Composition order is by decision id; over the cap, the lowest e-value is left out (ties: higher id) | `algorithms/composition.py` |
| `text_changed` / `canvas_changed` snapshot after the check's leading `goto` steps | `eval/dsl.py` |
| Floor = permutation of the high-loss labels; percentile by linear interpolation | `algorithms/lens_curation.py` |
| Residual view reads lenses surviving this close, hidden ones included | `algorithms/lens_curation.py` |
| Confirmation reads the first `detect.confirm_pairs` ablation deltas after the suspect round | `algorithms/detector.py` |
| `supersedes` / `successors` are lists (lineage may be a DAG) | `domain/types.py` |
| A fire with no judge verdict is recorded `undisposed`, never defaulted | `domain/types.py` |
| A lens value equal to its floor takes a strike (only "above the floor" keeps a lens clear) | `algorithms/lens_curation.py` |
| After a clear, the detector reads only ablation deltas and fires dated after the clear (likewise after admission) | `app/round_close.py` |
| An artifact that cannot load fails every check and scores the rubric floor; it still gives a delta | `eval/evaluate.py`, `eval/dsl.py` |
| The blinding scan matches listed words with plural and verb suffixes; a payload's backticked code and `#ids` are skipped | `algorithms/language.py` |
| The self-repair turn's missing-id test is a source regex until S1 moves it to `Page.exists` on the loaded page | `roles/worker.py` |

**Concurrency.** The Playwright sync API is not thread-safe, and SQLite allows one writer at a time.
Parallel passes therefore run as separate processes, each with its own browser and connection. Passes
write only their own rows. The round close runs in the parent process, alone.

## Build status

The work slices to finish it, in order, are in [CARRY_FORWARD.md](CARRY_FORWARD.md).

| Part | State |
| --- | --- |
| Domain, stats, algorithms | built, tested |
| Config, registries, wiring | built, tested |
| Crusoe client, pin resolution | built, tested offline; `friday doctor` verified live |
| SQLite Ledger, event log, spend; markdown Journal and lens set; git | built, tested |
| Check DSL interpreter, Evaluator | built; DSL tested on a fake page |
| Playwright adapter | written, not yet run against a real browser |
| Worker composition, HTML extraction, repair message | built, tested |
| Judge answer validation, JSON re-ask loop | built, tested |
| Round close, decision steps: gate, detector, supersede, refused-pool block | built, tested |
| Round close orchestrator: steers, lens curation flips, pooling, nomination, expiry, in order | not written |
| SQLite stores for passes, evals, lens answers, fires, observations, pools (the schema exists) | not written; in-memory only |
| Pass pipeline: compose, build with repair, eval, judge, store, `pair_deltas` | not written |
| Response and rubric cache by content hash | not written |
| Nominator, pooling call, lens minter, goal minter, tagger | interfaces only (`NotImplementedError`) |
| Probe suite, how-to check and its repair chain, A from P, H and C | not written (SPEC § 5) |
| Goal source (`goals/` loader), regime viewports in the Evaluator | not written |
| Fast-track in the recurrence bar | not written (SPEC § 7) |
| Critic run (`run-critic.yml`, critic role, revision turn) | not written; optional (SPEC § 17) |
| Prompts for judge, nominator, pooling, minters, tagger, rubric, critic | not written (tier 1, before the goal stream freezes) |
| Quality probe (ESLint, axe-core), rubric adapter, You.com adapter | not written |
| Coordinator loop, runner, readout runner, steer CLI, notes arm, demo page | not written |

## Running

```bash
uv sync
```

```bash
uv run pytest
```

```bash
uv run --env-file .env friday doctor
```

The default run includes the SPEC § 8 resolving-power simulation (4,000 runs per cell, about 5 s), which
checks the gate as built against the spec's published figures. `-m "not slow"` skips it.
`doctor` reports key presence and how each role's pin resolves.

`tests/architecture/test_knobs.py` keeps `run.yml` honest. Every knob must have a consumer in the code or
appear in its `PENDING` list, which names the unbuilt part that will read it.
