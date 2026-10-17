# Export schema

`friday.app.export.build(stores, journal, lens_set, git_log, spec)` returns one JSON document. The demo page
(SPEC § 13) renders from it, and every figure on the page comes from it. Nothing in it is copied from a stored
summary: e-process wealth is recomputed from the Ledger rows, lens values from the lens answers and losses,
and the readout from the passes and evals (SPEC § 12).

**Ordering.** Lists are sorted by id; `passes` by round, then id; `rounds` by round; `git_log` keeps the
order given. `dumps` writes keys sorted, so two builds over the same stores give the same text.

**Absent data** is `null`, never `0`. A reading whose population is missing has a `null` verdict (unread).

**Secrets.** The document holds no prompt, no key and no environment value. Spend appears only as the total.

## Top level

| Field | Type | Holds |
| --- | --- | --- |
| `schema_version` | int | 1 |
| `run` | object | run constants the page cites |
| `passes` | list | every pass, training and readout |
| `decisions` | list | every Journal entry, with status history and Ledger accounts |
| `lenses` | list | every lens, with status history and value history |
| `pools` | list | every pool, with its observation counts |
| `rounds` | list | per round: pass counts, the training-run reading, the round close's flips |
| `git_log` | list | the commits given to `build`, as `{sha, subject}` |
| `readout` | object | the SPEC § 12 readings |

## `run`

| Field | Holds |
| --- | --- |
| `run.spec_hash` | hash of `run.yml` |
| `run.seed` | `run.seed` |
| `run.rounds` | `run.rounds` |
| `run.drift_round` | `drift.round` |
| `run.readout_regime` | `readout.regime` |
| `run.readout_reps` | `readout.reps` |
| `run.spend_usd` | total spend from the spend log |

## `passes[]`

| Field | Holds |
| --- | --- |
| `passes[].id` | pass id |
| `passes[].round` | round |
| `passes[].category` | request id |
| `passes[].arm` | `empty`, `notes` or `journal` |
| `passes[].regime` | `R1` or `R2` |
| `passes[].readout` | true when the category is held out (a readout pass) |
| `passes[].worker` | model string the service echoed |
| `passes[].artifact_path` | path of the built HTML, or null |
| `passes[].decisions_composed` | decision ids composed into the prompt |
| `passes[].exposed` | the candidate under test given to this worker, or null |
| `passes[].ablated` | the admitted decision omitted from this worker, or null |
| `passes[].notes` | true when the notes file was composed |
| `passes[].pair_slot` | 0 or 1 within a journal-arm pair, or null |
| `passes[].repaired` | true when the self-repair turn ran |
| `passes[].transport_failed` | true when the pass failed for transport |
| `passes[].loss` | loss, or null without an eval |
| `passes[].Q` | quality reading `Q`, or null without an eval |
| `passes[].how_to_summary` | the how-to's summary line, or null |
| `passes[].how_to_rung` | repair rung 1-4 the how-to was read at, or null |

## `decisions[]`

| Field | Holds |
| --- | --- |
| `decisions[].id` | `D-NNNN` |
| `decisions[].status` | status at export |
| `decisions[].pool_id` | the pool it was drafted from |
| `decisions[].fires_when` | hook tags, sorted |
| `decisions[].not_this` | hook exclusions, sorted |
| `decisions[].payload` | the payload text |
| `decisions[].supersedes` | predecessor ids |
| `decisions[].successors` | successor ids |
| `decisions[].drafted_round` | round drafted |
| `decisions[].status_history` | flips from the event log: `{round, old, new, cause, evidence, successors}` |
| `decisions[].ledger` | `{exposure, ablation}` accounts, below |
| `decisions[].fires` | count of fires per judge disposition |

`ledger.exposure`: `n`, `mean_d`, `deltas` (`{round, category, d, dQ}`), `wealth`, `mirror_wealth` and
`wealth_path` (W_1..W_n), all recomputed from the rows with `gate.betting`.
`ledger.ablation`: `n`, `mean_d`, `deltas`, `harm_wealth_since_admitted` and `n_since_admitted` (over the
ablation rows dated after the last flip to `admitted`, as the detector reads them; null if never admitted).

## `lenses[]`

| Field | Holds |
| --- | --- |
| `lenses[].id` | `L-NN` |
| `lenses[].angle` | the lens question |
| `lenses[].counterfactual` | its counterfactual |
| `lenses[].status` | status at export |
| `lenses[].minted_round` | round minted, null for a baseline lens |
| `lenses[].superseded_by` | the lens it folded into, or null |
| `lenses[].has_cleared_floor` | true once it cleared its floor |
| `lenses[].status_history` | flips from the event log |
| `lenses[].value_history` | per training round `{round, value, n}`: the lens value (`lens.value`) over the last `lens.window` scored journal-arm training passes up to that round; `value` null below `lens.n_min` answered passes. Readout passes never enter a window |

## `pools[]`

| Field | Holds |
| --- | --- |
| `pools[].id` | `P-NNNN` |
| `pools[].lens_id` | the lens it pools under |
| `pools[].shape` | pool shape |
| `pools[].direction_labels` | direction labels |
| `pools[].status` | pool status |
| `pools[].merged_into` | the pool it merged into, or null |
| `pools[].refused_at_round` | round of the refusal, or null |
| `pools[].observations` | observations routed to it |
| `pools[].open_observations` | of those, the open ones |

## `rounds[]`

| Field | Holds |
| --- | --- |
| `rounds[].round` | round |
| `rounds[].passes` | training passes in the round |
| `rounds[].transport_failed` | of those, failed for transport |
| `rounds[].journal_plain` | `{n, mean_loss}` of scored journal-arm passes with no exposure and no ablation |
| `rounds[].notes` | `{n, mean_loss}` of scored notes-arm passes |
| `rounds[].flips` | the flips the round close wrote: `{subject_id, round, old, new, cause, evidence, successors}` |

## `readout`

The `Readout` of `friday.app.readout.readings`. Population rules: a readout pass is a pass on a held-out
category; a scored pass did not fail for transport and has an eval.

| Field | Holds |
| --- | --- |
| `readout.population` | the planned population, e.g. "6 held-out categories × 4 reps planned, regime R2" |
| `readout.held_out` | held-out category ids |
| `readout.regime` | `readout.regime` |
| `readout.unscored` | per arm, readout passes failed for transport or without an eval |
| `readout.r1` | R1: `loss(empty) − loss(journal)` |
| `readout.r2` | R2: `loss(notes) − loss(journal)` |
| `readout.r3` | R3: earned forgetting |
| `readout.r4` | R4: kept minted lenses |
| `readout.cost` | `{total_usd, admitted, usd_per_admitted}` |
| `readout.quality` | per arm `{n, mean}` of `Q` |
| `readout.how_to` | per arm how-to honesty |
| `readout.hook_transfer` | held-out requests an S\* decision fires on |
| `readout.tokens_composed` | per arm `{n, mean}` tokens composed per pass |

`r1`, `r2`: `reading`, `baseline`, `population` ("N held-out categories × K reps"), `n_categories`, `reps`
(arm → [min, max] scored reps per category), `unread_categories`, `per_category` (category → mean
difference over reps), `mean`, `p` (the `readout.test` one-sided p over the category means), `lower_bound`
(the one-sided `readout.interval_level` lower bound from inverting that same test; `null` when no bound is
attainable at this many categories, read as −∞), `level`, `claim_above` (the claim is earned when
`lower_bound` lies above it: 0 for R1, −`readout.r2_margin` for R2), `verdict`. R1's verdict is `improves`
at `p ≤ readout.r1_p`, else `unresolved`; by construction this is the same event as `lower_bound > 0`.
R2's verdict is `leaner than shared notes` when `lower_bound > −readout.r2_margin`, `unresolved` with no
bound, else `unearned`. Tokens composed per pass are shown beside R2 (`readout.tokens_composed`) and never
enter its verdict.

`r3`: `population`, `drift_round`, `admitted_before_retired_after` (first admitted at a round close before
`drift.round`, retired by the detector or by supersession at a close on or after it),
`admitted_after` (first admitted at a close on or after `drift.round`), `steered_retired_after`,
`suspected_after`, `c` (decisions with at least one exposure delta), `alpha`, `alpha_c`, `verdict`
(`forgets what went stale` when both counts are at least 1, else `unearned`).

`r4`: `population`, `kept_minted` (minted lenses active at the close), `hidden_minted`, `verdict`
(`the judge learns what to look at` when at least one, else `unearned`).

`how_to.<arm>`: `n_passes`, `declared_passed_share`, `contract_declared_share`, `silent_per_pass` (each
`{n, mean}`), `rung_share` (rung → share of passes with a how-to reading), `no_how_to`.

`hook_transfer`: `population`, `s_star` (decisions admitted or suspect at the close), `requests_fired`,
`categories_composed` (held-out categories whose journal-arm passes composed at least one decision). `untagged` (held-out requests with no tags in the tag vocabulary). Tags are read from the
tag vocabulary; with no vocabulary or any held-out request untagged, `population` is `null` and
`requests_fired` is empty: the reading is unread, not "fires on 0".
