# Carry-forward

The state of the Friday build and every work slice left, for the next builder or coding agent. The reader
holds this file and the repository. [SPEC.md](SPEC.md) is the design, [run.yml](run.yml) holds every
constant, and [ARCHITECTURE.md](ARCHITECTURE.md) maps the code. Terms follow SPEC § 2. A figure here is
either copied from its home or points to it.

State at 2026-10-17 12:45 PT, branch `scaffold`.

## Where the run stands

The pure mechanics of the decision loop are built and tested: the gate, the detector, exposure and
composition, lens curation, the recurrence bar, the readout statistics. So are the stores they write and
the Crusoe client that meters every call. Nothing that produces a pass exists yet:
- the goal stream;
- the probe suite and the how-to check;
- the rubric adapter;
- the judge prompts;
- the pass pipeline.

The next two blocks of SPEC § 13 build those. The 3-round dry run is due at 14:15.

## Work done

| Part | Reading |
| --- | --- |
| Layered package: domain, stats, algorithms, ports, eval, roles, adapters, app | `tests/architecture/test_layers.py` enforces the import rules |
| `run.yml` and its strict loader | an unknown, missing or mistyped key raises; `tests/architecture/test_knobs.py` lists every knob without a consumer yet |
| Interchangeable algorithms by registry key | betting strategy, upper bound, lens value, fold correlation, readout test; swapping one is a `run.yml` change (`tests/contract/test_review_regressions.py`) |
| E-process, t-bounds, ranks, permutation floor, sign-flip test, counter-based seeded draws | checked against tables and hand computation (`tests/unit/test_stats.py`) |
| Gate | reproduces SPEC § 8's resolving-power figures for both the current and the earlier rule (`tests/unit/test_gate.py`, slow) |
| Detector, exposure, composition, lens curation, recurrence bar and refused-pool block, expiry, halt, readout | unit and contract tests; the detector restarts at a clear |
| Ledger, event log, spend (SQLite, append-only by trigger); Journal and lens set (markdown, one commit per flip, frozen-text hash) | contract tests |
| Crusoe client: retries, spend row per call, prices from `/v1/models`, pin resolution with fallbacks | offline tests; `friday doctor` and one structured call verified live |
| Check DSL validator and interpreter; Playwright adapter | DSL tested on a fake page; the adapter has never run against a browser |
| Worker composition, HTML extraction, repair turn; judge answer validation; JSON repair and re-ask | contract tests, including blinding and candidate indistinguishability |
| Spec revision: goal stream, probe suite, how-to, fast-track, gate α 0.3, phone drift, critic run | SPEC §§ 2, 4, 5, 7, 8, 10, 12–14, 16, 17, Appendices A and B |
| S7 pass pipeline | merged; pairs differ only by the exposure, repair prompts included; crashed passes recorded with no eval |
| S10 round close | merged; phases run in SPEC § 9 order; a re-run of a close writes nothing twice |
| S14 readout and export | merged; `docs/export-schema.md` lists every field |
| Telemetry seam | `docs/telemetry.md` catalogues every row; wiring is S18 |

The test run is 320 passed, plus 1 live test that is skipped without the key, and ruff is clean.

Run them with:

```bash
uv run pytest
```

Run the live test and the pin check with:

```bash
uv run --env-file .env pytest -m live
```

```bash
uv run --env-file .env friday doctor
```

## Open questions (a human decides; record the answer in docs/spec-findings.md)

The six questions are in [docs/spec-findings.md](docs/spec-findings.md), under "Open questions". One blocks a
slice: **coordinator tool calling (question 1)** blocks S11. The drafting-role pins are settled
(`zai-org/GLM-5.3`).

## Work slices

Blocks follow SPEC § 13. A slice is done when its "done when" holds and the full test suite passes.
Every slice that adds a `run.yml` consumer removes that knob from `PENDING` in
`tests/architecture/test_knobs.py`.

| Id | Slice | Block | Depends on |
| --- | --- | --- | --- |
| S1 | Probe suite, how-to check, repair chain, A from P, H and C | 12:15–13:15 | — |
| S2 | Goal minter, contract stubs, check validation, goal source; freeze `goals/` | 12:15–13:15 | — |
| S3 | Tagger and tag vocabulary store | 12:15–13:15 | S2 |
| S4 | Rubric adapter, quality probe, response cache | 12:15–13:15 | — |
| S5 | Pilot round and the worker pin rule | 13:00–13:15 | S1–S4 |
| S6 | SQLite run stores and the process model | 13:15–14:15 | — |
| S7 | Pass pipeline | 13:15–14:15 | S1, S3, S4, S6 |
| S8 | Judge prompts and calls | 13:15–14:15 | S7 |
| S9 | Pooling call, fast-track, nominator with You.com | 13:15–14:15 | S8 |
| S10 | Round-close orchestrator | 13:15–14:15 | S9 |
| S11 | Coordinator, runner, notes arm, steer CLI | 13:15–14:15 | S10, question 1 |
| S12 | Dry run, 3 rounds | 14:15–14:30 | S11 |
| S13 | Full run | 14:30–15:30 | S12 |
| S14 | Readout runner | 14:30–15:30 | S6 |
| S15 | Showcase UI: live round view, gallery, timeline, readout | 13:30–16:15 | S14 export schema; S18 for live data |
| S16 | Critic run (optional) | 15:30–16:15 | S13 |
| S17 | Tier-2 language read, video, submission | 16:15–17:00 | S14, S15 |
| S18 | Telemetry wiring through every step | 13:30–14:15 | S1, S4, S6, S7, S10 |

### S1. Probe suite, how-to check, repair chain

Build the deterministic share of the loss (SPEC § 5).

- `friday/eval/probe.py`: `run(page, viewport) -> ProbeReading` over the `Page` port. It needs one new port
  method that lists the page's controls, which the Playwright adapter implements.
- `friday/eval/how_to.py`: `read(html, contract) -> HowToReading`. It walks the four repair rungs, compiles
  each declared control into an acceptance check, and computes H.
- `Evaluator`: compute A from `eval.a_weights`, run every reading at each of the regime's
  `eval.viewports`, and record silent controls.
- Run the Playwright adapter against a real browser for the first time. `uv sync --extra browser`, then
  `uv run playwright install chromium`.
- Move the self-repair turn's missing-id test (`roles/worker.py`, a source regex) to `Page.exists` on the
  loaded page.

**Done when:**
- a hand-written HTML file with a correct how-to scores H = 1;
- the same file with its JSON truncated mid-array scores at rung 3;
- an unloadable file still yields an `EvalReport`;
- the probe suite scores 0 for a control that changes nothing.

**Do:**
- make `how_to.read` total: every input returns a reading, and none raises;
- test each rung with a fixture file.

**Don't:**
- re-ask the worker for a readable how-to (SPEC § 5);
- count a silent control in the loss.

### S2. Goal minter and the goal stream

Mint `goals/stream.json` (`stream.per_round` × `run.rounds` requests) and `goals/held_out.json`
(`stream.held_out`, all drawn from the R2 mix) with the goal minter (SPEC § 4).
- Each request carries a contract, `stream.checks_per_regime` acceptance checks for its regime, and a
  contract stub under `goals/stubs/`.
- Validate each check: it must fail on its stub; regenerate it at most `stream.check_regenerations` times.
- Add a `GoalSource` port with two implementations, the frozen stream and a fixed set (`algorithms/split.py`
  serves the latter).
- Write the minter prompt in `prompts/goal_minter/`.

Draw requests from the family × interaction-mode grid (SPEC § 4 The grid). Drop incoherent cells at
minting, and withhold `stream.held_out_cells` cells with `algorithms/split.draw_held_out_cells`.

**Done when:**
- `goals/` is committed;
- every check fails on its stub;
- the families' shares per regime match `stream.mix` to within one request.

**Do:**
- draw families under the run seed (`stats/rng.py`, keyed by round and position);
- write requests in a user's words.

**Don't:**
- put a family name in a request's text or its tags (blinding, SPEC § 2);
- edit a request after the commit.

### S3. Tagger

Tag each round's requests at round start (SPEC § 4).
- `roles/tagger.py` is the seam.
- Store the vocabulary with its glosses and minted rounds in `goals/tags.json`.
- Enforce `stream.max_new_tags` per round, and freeze tags on a request once given.
- At the close, tag the held-out set with the frozen vocabulary, minting nothing.

**Done when:**
- a scripted-client test shows reuse of an existing tag, a mint under the bar, and refusal of a mint over
  the bar;
- the tagger's template passes the `tagger` row of `prompts/blinding.yml`.

**Do:** reuse a tag whenever one names the trait.

**Don't:** rename or merge tags mid-run. A rename silently changes the reach of every hook keyed on it.

### S4. Rubric adapter, quality probe, response cache

- Rubric: the frozen three questions per viewport, in `prompts/rubric.md`, on the `rubric` role. Cache by
  artifact hash and viewport.
- Quality probe: ESLint over the extracted inline script with `config/eslint.json`, and axe-core injected
  into the page. Both run from a local Node install; ESLint and axe-core become dev dependencies in a small
  `package.json`.
- Response cache for model calls, keyed by a content hash of the request body. Cache hits record a
  zero-cost spend row.

**Done when:**
- a fixture screenshot scores through a recorded live call;
- a second call is a cache hit;
- Q on a fixture file matches a hand count.

**Don't:** default a missing rubric score to anything. `LossModel.rubric` raises on the wrong count; keep
it that way.

### S5. Pilot round

Build round 1's requests once, outside the run, and apply the worker pin rule with `pilot.*` (SPEC § 5).
Read the premises "headroom" and "how-to readable" (SPEC § 16).

Measure one pass end to end (worker turns, eval, both judge calls) and one round close, and fit the run
to the 17:00 deadline. Rounds × (round time) + readout time must end the run, readout included, by 15:30.
If it doesn't fit, change `stream.per_round` or `run.rounds` once, before round 1, and record the timing.

**Done when:**
- the pin is recorded in `docs/spec-findings.md`, with the pilot's mean A, P, H and the share of passes at
  each rung;
- the measured pass time, round time and the resulting `run.rounds` / `stream.per_round` are recorded
  there too.

**Don't:** pick any other constant from pilot output.

### S6. SQLite run stores and the process model

Implement `PassStore`, `JudgeStore`, `ObservationStore` and `PoolStore` over `adapters/sqlite/schema.sql`,
and add the `exposures` table. Passes run as one process each (`run.max_parallel_passes`), each with its
own browser and its own connection. The round close runs alone in the parent.

**Done when:**
- `tests/contract/test_conformance.py` covers each new store;
- a crash between passes loses no row of a finished pass.

**Don't:** share one connection or one browser across threads.

### S7. Pass pipeline

For each pair plan (`algorithms/exposure.py`), run the pass:
1. Compose (`algorithms/composition.py`, `roles/worker.py`).
2. Build, with the self-repair turn per `worker.repair_turns`.
3. Enforce `worker.max_artifact_bytes`.
4. Write `artifacts/<pass_id>/`.
5. Evaluate, then record the pass and its eval.

After the round, turn the pairs into Ledger rows with `app/ledger.py`. The notes-arm pass is the same
pipeline with the notes file composed.

**Done when:** a round of two requests runs end to end on scripted model clients and a real browser,
leaving Ledger rows.

**Do:** record transport failures on the pass, and offer them to the coordinator's rerun.

### S8. Judge prompts and calls

Write the lens call and the report call (SPEC § 6) in `prompts/judge/`. Wire them through `roles/ask.py`
with the existing validators and `judge.quote_reasks`. Record one `Fire` per composed decision, with
`undisposed` when the report call is dropped.

**Done when:**
- both templates pass the `judge_lens` and `judge_report` rows of the blinding list;
- a recorded live call on a fixture artifact validates.

**Don't:** give the lens call any part of the eval report, the how-to check's results included. The how-to
itself is artifact source and may be read.

### S9. Pooling, fast-track, nominator

- Pooling call (`roles/pooling.py`), one per kept lens plus `free`. The runner mints the `P-` ids.
- Fast-track in `algorithms/recurrence.py` with `pool.fast_track_share`, ordered ahead of the queue in
  `nomination_queue`.
- Nominator (`roles/nominator.py`): one You.com search, then a draft that passes `check_payload` with one
  re-ask, else `no-decision`. Needs `YOU_API_KEY` in `.env` and a You.com adapter.

**Done when:**
- unit tests show a fast-tracked pool nominated in its first round and ahead of an older bar-reached pool;
- a scripted nominator run drafts `journal/D-0001.md` as a candidate.

**Don't:** let fast-track touch the gate. It changes when a candidate is tested, never the bar it clears.
**Don't:** count passes toward the bar: count distinct requests (`pool.recurrence_min_requests`,
`pool.fast_track_min_requests`; SPEC § 7 Counting). A pair's two passes share one request.

### S10. Round-close orchestrator

Run the close in order (SPEC § 11):
1. steers;
2. lens curation (`algorithms/lens_curation.py`) and its flips;
3. the lens minter every `lens.mint_every` closes, with `lens.residual_min_passes` and `lens.mint_max`;
4. pooling;
5. observation expiry;
6. nomination;
7. gate and detector (`app/round_close.py`);
8. pings.

Each flip is one commit. Wire the composition root in `app/wiring.py` from `paths.*`.

**Done when:** a scripted 4-round simulation over in-memory stores admits a candidate, suspects it after a
planted drift, and retires it. The commit messages are rendered from `templates/flips.yml`.

### S11. Coordinator, runner, notes arm, steer

- The coordinator agent over `roles/coordinator.py`'s tool surface, with round plans in `plans/round-NN.md`.
- The runner loop and the halt rule (`algorithms/halt.py` with `run.rounds`, `run.spend_cap_usd`,
  `run.convergence_rounds`).
- Pings per `run.ping_transport_share`.
- The notes arm, truncated at `notes.max_tokens`.
- `friday steer`.

**Done when:**
- the coordinator completes one tool call live on its pin;
- `friday run --rounds 1` runs a round on scripted clients.

**Don't:** add a coordinator tool that sets an exposure, a status or a constant. The conformance test
fails on one.

### S12. Dry run

Run 3 rounds with all parts on live models. If it fails by 14:15, cut the lens minter and the notes arm
(SPEC § 13).

**Done when:** three round closes commit, and every pass has a loss.

### S13. Full run

Start by 14:30 in the background. Commit `run.yml` and `goals/` first. Change no constant after round 1.

**Done when:** the run halts and the Journal, the Ledger and `artifacts/` are complete.

### S14. Readout runner

Compute R1–R4 and the descriptive readings of SPEC § 12 from the stores (`algorithms/readout.py`,
`readout.*`), and export JSON for the demo.

**Done when:** every pre-registered reading prints with its population and its interval or p, the failed
ones included.

### S15. Showcase UI

`web/`: a FastAPI app (`uv sync --extra web`) and static pages that show the run as it learns. It reads
the S14 export (`docs/export-schema.md`) for settled figures and the telemetry log (`docs/telemetry.md`)
for live state. Pages, in demo order:
1. **Live round:** the passes in flight (open `pass` spans and their eval phases), each finished pass's
   loss, the round close's phases as they run, and spend against the cap. It polls the log every
   `telemetry.live_poll_ms`.
2. **Gallery:** each built app in an iframe, captioned by its how-to, filterable by cell and arm.
3. **Timeline:** per decision, its wealth path against the 1/α line and its status flips; per lens, its
   value history; the drift round marked.
4. **Readout:** the SPEC § 12 readings with their populations and intervals, the failed ones included.

The prebuild starts once the export schema and the telemetry catalogue are fixed, against fixture
documents made from both; the dry run (S12) then replaces the fixtures with a real database.

**Done when:** every page renders from fixtures and from the dry-run database, with no figure typed by
hand.

**Don't:** coin a UI synonym for a vocabulary term (SPEC § 2, Demo words), or show a prompt, a reply or a
key.

### S18. Telemetry wiring

Emit every row in `docs/telemetry.md` from the step it times. The step owns the call:
- `CrusoeClient.chat` opens `model.call` and records attempts, retry statuses and backoff;
- the pass process opens `pass` and its children;
- the evaluator opens `eval.*`;
- the round close opens `close.*`;
- the runner opens `run` and `round` and writes `run.manifest`.

The composition root builds one `SqliteTelemetry` per process from `paths.db` and
`telemetry.busy_timeout_ms`, scoped to the run. Add `friday telemetry` (CLI) to print a round's spans as a
tree and a per-phase time table; S5 reads its timings from that table.

**Done when:** a 1-round dry run with fakes emits every catalogued row at least once (a test asserts the
catalogue against the log), and no decision path imports the telemetry port.

### S16. Critic run (optional)

The critic role on its pin, the revision turn, the "requests honored" reading, and `run-critic.yml`
(SPEC § 17). Start only after S14 has written the main readout.

### S17. Submission

Do the tier-2 read of the readout, the demo page, the pitch, the README and the commit templates against
SPEC § 2 claim language. Then record the video, make the repository public, and submit.

**Don't:** retype a figure. Render it from the export.

## Debt carried

| Item | Where | Slice |
| --- | --- | --- |
| A lens answer records the judge model, but a mid-run fallback is unhandled | open question 3 | S8 |
| Lens fold reads correlation only | open question 4, `algorithms/lens_curation.py` | S10 |
| The knob test can miss a knob whose name matches an unrelated attribute | `tests/architecture/test_knobs.py` | any |
| Pass processes each launch their own Chromium; a shared browser server (`npx playwright run-server`, `chromium.connect`) gives one browser many isolated contexts | `adapters/playwright_browser.py` | after the s1-eval merge |
| `SignFlipExact` enumerates at most 20 values | `stats/significance.py` | S14 if V grows |
| A hung pass is bounded only by an unset `pass_timeout_s`; notes use a module constant for chars per token | `app/pipeline.py`, `app/notes.py`; knobs `run.pass_timeout_s`, `notes.chars_per_token` | S18 |
| A crashed pass's reason lives only in `artifacts/<id>/pass.json`; tokens composed per pass are not stored, so R2 reads unread | `Pass.failed`, `Pass.composed_tokens` (additive, after S6 merges) | S18 |
| Report-call observations are held in memory until the close; a runner restart before the close loses them | `app/pipeline.py` | S11 |
| The export's wiring must pass the tag vocabulary, or hook transfer reads unread | `app/export.py` callers | S11 |
| A lens mint is not in the event log; a partly applied mint reads as complete on a close re-run | `app/lens_close.py` | S11 |
