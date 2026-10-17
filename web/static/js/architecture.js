// Architecture: the technical showcase. One diagram of the system as bands (goal side, round loop, round close,
// readout, cross-cutting), drawn as inline SVG by this module. Hover a component for a one-line gloss; click it
// (or Enter) for its detail drawer: what it does, process, data, algorithm with run.yml knobs, models, code.
// The content describes the design (SPEC.md, ARCHITECTURE.md, run.yml); it claims no result.
import { h, s } from "./util.js";

// ---- layout -------------------------------------------------------------------------------------------------

const VB_W = 1612;
const VB_H = 900;
const BW = 215;          // box width
const BH = 92;           // box height
const COLS = [40, 305, 570, 835, 1100, 1365];

const BANDS = [
  { id: "goal", label: "Goal side", note: "minted before round 1 · tags at each round start", y: 10, hgt: 135 },
  { id: "loop", label: "Round loop", note: "each pass in its own process, up to run.max_parallel_passes", y: 160, hgt: 135 },
  { id: "close", label: "Round close", note: "one process, the single writer, in this order", y: 310, hgt: 265 },
  { id: "readout", label: "Readout", note: "after the halt", y: 590, hgt: 135 },
  { id: "cross", label: "Cross-cutting", note: "every layer above codes against ports; adapters implement them", y: 740, hgt: 150 },
];

const ROW_Y = { goal: 40, loop: 190, close: 340, closeB: 470, readout: 620, cross: 775 };

// kind -> legend entry; the colour is a CSS class on the box (app.css .arch-k-*)
const KINDS = {
  role: { label: "model role", note: "friday/roles: a prompt and a pinned model, over ports only" },
  pure: { label: "pure algorithm", note: "friday/domain, stats, algorithms: stdlib only, no I/O" },
  eval: { label: "fixed eval", note: "friday/eval over the Page port: never learns" },
  app: { label: "orchestration", note: "friday/app: wiring, pipeline, round close, readout" },
  store: { label: "store / adapter", note: "friday/adapters: SQLite, markdown + git, Crusoe, Playwright" },
  ui: { label: "showcase", note: "web/: static pages over the JSON export" },
};

// ---- the components -----------------------------------------------------------------------------------------
// Each: id, title, sub (second line in the box), kind, at [row, col], tip (hover), and the drawer sections.

const C = [
  {
    id: "goal-minter", title: "Goal minter", sub: "family × mode grid", kind: "role", at: ["goal", 0],
    tip: "Mints every request once before round 1: the frozen goal stream and the held-out set.",
    what: "Writes each training request and each held-out request as a plain one-line demand a non-technical "
      + "person would type, with its interface contract and acceptance checks. Both sets are frozen in git before "
      + "the first pass; a request touched after round 1 voids every pair that built it.",
    process: [
      "Lay out the grid: goal family (what the app is) × interaction mode (how it is operated); mark incoherent cells and drop them.",
      "Withhold stream.held_out_cells coherent cells under the run seed; redraw until every family and every mode still appears in a training cell.",
      "Draw each training request's family by the regime's goal mix (stream.mix.R1, then R2 from drift.round), its mode evenly.",
      "Mint the held-out set: one request per withheld cell, written against R2 (the drift constraint).",
      "Write 2–3 acceptance checks per regime in the check DSL; every check must fail on the request's contract stub (regenerated once, then dropped).",
    ],
    data: [
      "goals/stream.json: id, text, round, cell (generator-side only), contract (ids + role), checks.R1 / checks.R2",
      "goals/held_out.json: one request per withheld cell, round null, checks.R2",
      "goals/stubs/: contract stubs every check must fail on",
    ],
    algo: [
      "stream.families (5) × stream.modes (5) = 25 cells, incoherent ones dropped",
      "stream.held_out_cells = 6, stream.split_max_draws = 1000",
      "stream.per_round = 10 requests; stream.held_out = 6",
      "stream.mix.R1 = [0.5, 0.15, 0.15, 0.1, 0.1] (canvas game leads); R2 = [0.15, 0.5, 0.15, 0.1, 0.1] (data tool leads)",
      "stream.checks_per_regime = [2, 3], stream.check_regenerations = 1",
      "Every draw is a pure function of (seed, purpose, keys); run.seed = 20261017",
    ],
    models: ["goal_minter: zai-org/GLM-5.3 (a drafting role), fallback nvidia/NVIDIA-Nemotron-3-Super-120B-A12B, temperature 0.3"],
    code: ["friday/roles/goal_minter.py", "friday/algorithms/split.py (draw_held_out_cells, draw_split)", "friday/app/goals.py", "friday/adapters/goals_json.py", "prompts/goal_minter/"],
  },
  {
    id: "tagger", title: "Tagger", sub: "tag vocabulary", kind: "role", at: ["goal", 1],
    tip: "At each round start gives every request 2–4 tags; hooks fire on tags.",
    what: "Reads the round's request texts and the tag vocabulary so far and gives each request 2–4 tags. It reuses a "
      + "tag whenever one fits and mints a new one (with a one-line gloss) only when no tag names the trait. The "
      + "vocabulary and the hooks keyed on it become the team's discovered taxonomy of goal types. No family or mode "
      + "name ever reaches it as a label.",
    process: [
      "Read this round's request texts and goals/tags.json.",
      "Assign 2–4 tags per request, reusing existing tags first.",
      "Mint at most stream.max_new_tags new tags, each with its gloss and the round it was minted.",
      "Freeze the tags on each request. Never rename or merge a tag mid-run.",
      "At the close the vocabulary freezes; the held-out set is tagged with it, minting nothing.",
    ],
    data: ["goals/tags.json: tag, gloss, minted round", "request.tags (frozen once given)"],
    algo: ["stream.tags_per_request = [2, 4]", "stream.max_new_tags = 4 per round", "A hook fires iff the request holds at least one fires_when tag and no not_this tag"],
    models: ["tagger: zai-org/GLM-5.3, fallback nvidia/NVIDIA-Nemotron-3-Super-120B-A12B, temperature 0.3"],
    code: ["friday/roles/tagger.py", "friday/domain/hooks.py (fires)", "prompts/tagger/"],
  },
  {
    id: "coordinator", title: "Coordinator", sub: "advises · round plans", kind: "role", at: ["loop", 0],
    tip: "The planner agent: decides what work runs; owns no statistic and sets no status.",
    what: "Plans each round and runs it through a fixed tool surface: reads the in-flight candidates, due ablations and "
      + "last round's failures, writes a short round plan, spawns passes, re-runs transport failures once, pings the "
      + "human and halts. The exposure coins, the gate, the detector and the curation are code; it has no tool that "
      + "sets an exposure, a status or a constant.",
    process: [
      "plan_round(): write plans/round-NN.md (what runs, which failed passes re-run, what it expects to change).",
      "spawn_pass(category, arm, worker) for every pass of the round.",
      "rerun(pass_id) once for a pass failed for transport.",
      "Call the round close and read its results (read_ledger, read_journal).",
      "ping(reason) on a suspect decision the judge marks contradicted, on transport failures over a third, and at halt.",
      "halt(reason) at the first halt condition.",
    ],
    data: ["plans/round-NN.md", "events (pings)"],
    algo: [
      "Halt at the first of: run.rounds = 4 complete; spend reaches run.spend_cap_usd = 30.0; convergence for run.convergence_rounds = 3 rounds after the drift",
      "run.transport_reruns = 1; run.ping_transport_share = 0.3333",
      "drift.round = 4: regime R1 becomes R2",
    ],
    models: ["coordinator: openai/gpt-oss-120b, fallback nvidia/NVIDIA-Nemotron-3-Super-120B-A12B, temperature 0.2 (tool calls)"],
    code: ["friday/roles/coordinator.py", "friday/app/runner.py", "friday/algorithms/halt.py", "prompts/coordinator/"],
  },
  {
    id: "pipeline", title: "Pass pipeline", sub: "pair plans · compose · build", kind: "app", at: ["loop", 1],
    tip: "Plans each pair (exposure, ablation or none), composes by hook, and has the worker build with one self-repair turn.",
    what: "Turns a request into scored passes. Each request is built by a pair of journal-arm workers that differ only "
      + "by the pair plan, and once by the notes arm. Composition gives both workers the admitted decisions whose "
      + "hooks fire; the plan then exposes one worker to an in-flight candidate, or has one omit an admitted decision "
      + "due for ablation. The worker writes one HTML file with its how-to, then gets one self-repair turn on console "
      + "errors or missing contract ids.",
    process: [
      "Draw the round's pair plans in category-id order: in-flight candidate whose hook fires (fewest pairs first, fair coin under the run seed); else an admitted decision due for ablation (the coin picks the worker that omits it); else unexposed.",
      "Compose: baseline instructions + admitted decisions whose hooks fire, ordered by decision id, up to the cap.",
      "Worker build: one HTML file, inline CSS and JS, no external URL, every contract id, the how-to.",
      "Self-repair turn when the loaded page logs a console error or lacks a contract id: shows those errors and missing ids, never the checks.",
      "Eval, judge, store the pass's own rows; a settled pair writes its paired delta to the Ledger.",
    ],
    data: [
      "exposures(round, seq, category, kind exposure|ablation|none, decision_id, treated_slot)",
      "passes(id, round, category, arm, worker, regime, composition JSON, pair_slot, artifact_path, repaired, transport_failed, composed_tokens)",
      "artifacts/<pass_id>/index.html, shot-<viewport>.png, eval.json",
    ],
    algo: [
      "One pair carries at most one exposure; d = loss(without) − loss(with)",
      "composition.cap = 6, rank_by e_value (over the cap the lowest e-value is left out)",
      "detect.ablate_p = 0.75 (a free pair in an admitted decision's reach becomes an ablation pair)",
      "worker.repair_turns = 1, worker.max_artifact_bytes = 30720 (kept whole, never truncated)",
      "run.max_parallel_passes = 32, run.pass_timeout_s = 600",
    ],
    models: ["worker: nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B (weak on purpose), fallback openai/gpt-oss-120b, temperature 0.7, no seed"],
    code: ["friday/app/pipeline.py", "friday/algorithms/exposure.py (assign_exposures)", "friday/algorithms/composition.py (compose_pair)", "friday/roles/worker.py", "friday/app/notes.py", "prompts/worker/"],
  },
  {
    id: "eval", title: "Eval", sub: "P · H · C · R · Q", kind: "eval", at: ["loop", 2],
    tip: "The fixed scorer: probe suite, how-to check, acceptance checks, rubric and quality, in a headless browser.",
    what: "Loads the artifact in Playwright at each of the regime's viewports (network routes aborted) and reads it five "
      + "ways. P, H and C are deterministic; R is a pinned vision model on a screenshot; Q is ESLint and axe-core. "
      + "It is frozen from round 1, which is what makes every before/after readable.",
    process: [
      "P, probe suite: page loads in time; no console error; share of up to 12 controls whose default action changes text, a canvas hash or a visibility.",
      "H, how-to check: parse #how-to-data by the repair chain (strict → repairing parser → salvage → nothing), run each declared control as a check, times the declared share of contract ids, times the repair factor.",
      "C, acceptance checks: passed ÷ checks for the regime, each on a fresh page load, in the check DSL.",
      "R, rubric: three frozen questions on each viewport's screenshot, 1–5 mapped to 0–1, cached by artifact hash and viewport.",
      "Q, quality: ESLint errors over the inline script plus axe-core violations; a reading, never part of the loss.",
    ],
    data: [
      "evals(pass_id, regime, checks, console_errors, a, r, rubric, q, eslint_errors, axe_violations, loss)",
      "eval_details(pass_id, p, h, c, silent_controls, probes, how_to)",
      "Check DSL steps: goto, click, type, key, focus, wait ≤ 3000; expect: exists, visible, focused, text_matches, text_changed, canvas_changed, no_console_errors",
    ],
    algo: [
      "A = 0.3 · P + 0.3 · H + 0.4 · C (eval.a_weights)",
      "eval.viewports: R1 [1280×800]; R2 [1280×800, 375×812], averaged",
      "eval.load_timeout_ms = 15000, action_timeout_ms = 2000, max_wait_ms = 3000",
      "probe.max_controls = 12, settle_ms = 500; how_to.max_controls = 12, repair_penalty = 0.1 (rungs 2 and 3)",
      "eval.rubric_scale = [1, 5], rubric_questions = 3 per viewport",
    ],
    models: ["rubric: first image-input model served among nvidia/NVIDIA-Nemotron-3.5-Super-VL, google/gemma-4-31b-it, zai-org/GLM-5.3-Flash, Qwen/Qwen3.8-27B; fallback nvidia/Nemotron-3-Nano-Omni-Reasoning-30B-A3B; temperature 0"],
    code: ["friday/eval/evaluate.py", "friday/eval/probe.py", "friday/eval/how_to.py", "friday/eval/dsl.py", "friday/adapters/playwright_browser.py", "friday/adapters/rubric.py", "friday/adapters/quality.py", "config/eslint.json"],
  },
  {
    id: "loss", title: "Loss", sub: "1 − (w_a·A + w_r·R)", kind: "pure", at: ["loop", 3],
    tip: "One scalar per pass: 0 is perfect, 1 is broken. The gate, the detector and the lens loop all read it.",
    what: "Folds the eval into the one number both learning loops use as their yardstick. The deterministic share "
      + "(A) carries three quarters; the rubric carries a quarter so a model-graded label never drives structure alone. "
      + "Q stays out of it and moves only the gate's quality guard.",
    process: ["Take A from P, H and C.", "Take R from the rubric.", "loss = 1 − (w_a · A + w_r · R).", "An artifact that cannot load fails every check and scores the rubric floor; it still gives a delta."],
    data: ["evals.loss, evals.a, evals.r", "pass.loss telemetry metric with pass.A, pass.R, pass.P, pass.H, pass.C, pass.Q"],
    algo: ["eval.w_a = 0.75, eval.w_r = 0.25", "One acceptance check of three is worth 0.75 · 0.4 / 3 = 0.10 of loss"],
    models: [],
    code: ["friday/algorithms/loss.py"],
  },
  {
    id: "judge", title: "Judge", sub: "lens call · report call", kind: "role", at: ["loop", 4],
    tip: "Two calls per journal-arm pass: the lens call never sees the eval; the report call does.",
    what: "Reads every journal-arm pass twice. The lens call sees the request, the composed decisions and the artifact "
      + "source, never the eval, and answers each active lens with a probability and a quote: that is foresight from the "
      + "artifact. The report call adds the eval report and the rubric scores and files observations and a disposition "
      + "per composed decision. Notes-arm passes get no judge calls.",
    process: [
      "Lens call: for each active lens {id, p, quote}; quote empty when p < 0.5.",
      "Report call: up to 3 observations {lens_id | free, shape, direction, quote}.",
      "Dispositions per composed decision: applied | not-applicable | contradicted (with a quote).",
      "Every quote must be a substring of the call's input; re-ask once, then drop the call's output and record the drop.",
    ],
    data: ["lens_answers(pass_id, lens_id, p, quote, judge_model)", "fires(pass_id, decision_id, round, disposition, quote); a fire with no verdict is undisposed"],
    algo: ["judge.max_observations = 3", "judge.quote_reasks = 1", "lens.flag_p = 0.5"],
    models: ["judge: nvidia/NVIDIA-Nemotron-3-Super-120B-A12B, fallback deepseek-ai/DeepSeek-V4-Pro, temperature 0, max_concurrent 8 (passes queue for a slot)"],
    code: ["friday/roles/judge.py", "friday/roles/eval_text.py", "friday/algorithms/language.py (quote_in)", "prompts/judge/"],
  },
  {
    id: "observations", title: "Observations", sub: "O-NNNN, open", kind: "store", at: ["loop", 5],
    tip: "What the judge noticed in a pass, under a lens or free; the raw material of pools.",
    what: "The report call's observations, written open at the round close with minted O- ids. They pool by lens id, "
      + "count toward a pool's recurrence bar, and close when their pool is nominated: they become the candidate's warrant.",
    process: ["Mint O- ids in pass id order, then report order.", "Write each open, under its lens or free.", "Close unused after pool.expiry_rounds.", "Close as warrant on nomination; reopen on a no-decision."],
    data: ["observations(id, pass_id, round, lens_id, category, shape, direction, quote, status, pool_id, direction_label)"],
    algo: ["pool.expiry_rounds = 6", "A hidden lens's observations are written but never reach a pool"],
    models: [],
    code: ["friday/app/round_close.py (step 3)", "friday/algorithms/recurrence.py (expired)", "friday/adapters/sqlite/schema.sql"],
  },
  {
    id: "lens-curation", title: "Lens curation", sub: "value · floor · fold · mint", kind: "pure", at: ["close", 0],
    tip: "Keeps the lenses whose flags track loss: AUC value vs a shuffled floor, fold near-duplicates, mint new ones.",
    what: "The lens loop: learns what the judge should look at, self-supervised with the eval as the label. A kept lens "
      + "is one whose flags predict high loss, so its pools are about defects that cost something. Baseline lenses L-01 "
      + "to L-08 get no exemption.",
    process: [
      "Value: AUC of p against loss > median(window losses), average ranks for ties (Mann–Whitney).",
      "Floor: 95th percentile of the same AUC over 200 shuffles; below it at two consecutive closes retires the lens with a tombstone.",
      "Fold: Spearman correlation of two lenses' p; above fold_rho keep the higher-value lens, supersede the other.",
      "Mint (every 2 closes): the residual view (loss ≥ 0.5, no kept lens with p ≥ 0.5); with ≥ 6 such passes the lens minter proposes at most 2 lenses, entering hidden.",
      "Cap: at most 10 active lenses, hidden included; over it the lowest-value lens past n_min is retired.",
    ],
    data: ["lenses/L-NN.md (angle, counterfactual, status in front matter)", "lens_answers over the window", "events (lens flips)"],
    algo: [
      "lens.window = 64 journal-arm passes, lens.n_min = 32",
      "lens.value = auc, lens.floor_quantile = 0.95, lens.floor_shuffles = 200, lens.floor_strikes = 2",
      "lens.fold_correlation = spearman, lens.fold_rho = 0.85",
      "lens.mint_every = 2, residual_loss = 0.5, residual_min_passes = 6, mint_max = 2, cap = 10",
    ],
    models: ["lens_minter: zai-org/GLM-5.3, fallback nvidia/NVIDIA-Nemotron-3-Super-120B-A12B, temperature 0.3"],
    code: ["friday/algorithms/lens_curation.py (curate, residual_view, cap_evictions)", "friday/app/lens_close.py", "friday/stats/ranks.py (auc, spearman)", "friday/stats/resampling.py", "friday/roles/lens_minter.py", "friday/adapters/lens_md.py"],
  },
  {
    id: "pooling", title: "Pooling", sub: "shape + direction labels", kind: "role", at: ["close", 1],
    tip: "One call per kept lens (and free) routes new observations into stable pools P-NNNN.",
    what: "Groups the round's new observations by shape, without embeddings. Each pool has a one-line shape and at most "
      + "two direction labels; each observation gets one of its pool's labels. Refused pools keep receiving matching "
      + "observations, so evidence that failed once is not re-nominated as new.",
    process: [
      "For each kept lens and free: send its pools (id, shape, direction labels), the refused pools and the round's new observations.",
      "Assign each observation to an existing pool, a new pool (id minted by the runner) or a refused pool.",
      "Label each observation with one of its pool's direction labels.",
      "Merge two pools when asked (the lower id survives, the other records merged_into); never split.",
      "Validate the whole result before any write.",
    ],
    data: ["pools(id, lens_id, shape, direction_labels, status, merged_into, refused_at_round)", "observations.pool_id, observations.direction_label"],
    algo: ["pool.max_direction_labels = 2"],
    models: ["pooling: zai-org/GLM-5.3, fallback nvidia/NVIDIA-Nemotron-3-Super-120B-A12B, temperature 0.3"],
    code: ["friday/roles/pooling.py", "friday/app/round_close.py (step 4)", "prompts/pooling/"],
  },
  {
    id: "nomination", title: "Nomination", sub: "recurrence bar · fast-track", kind: "pure", at: ["close", 2],
    tip: "Sends a pool to the nominator when it recurs over distinct requests, while gate.in_flight has room.",
    what: "Decides when a pool is worth testing. Every count is over distinct requests, not passes: the two passes of a "
      + "pair build one request, so per-pass counting would read one request's quirk as recurrence. Agreement says when "
      + "to test; only paired exposure says whether it helps.",
    process: [
      "Count a pool's open observations by distinct request (after the refusal, for a refused pool).",
      "Recurrence bar: enough observations, requests and rounds.",
      "Fast-track: enough distinct requests and a leading direction label holding at least the share; needs no second round.",
      "Queue fast-tracked pools first, then the rest, oldest pool first within each, while gate.in_flight has room.",
      "On nomination the pool's open observations close as the warrant.",
    ],
    data: ["pools.status", "observations.status (open → warrant)"],
    algo: [
      "pool.recurrence_min_observations = 3, recurrence_min_requests = 2, recurrence_min_rounds = 2",
      "pool.fast_track_min_requests = 3, fast_track_share = 0.666 (2/3 of the distinct requests)",
      "gate.in_flight = 2",
    ],
    models: [],
    code: ["friday/algorithms/recurrence.py (RecurrenceBar, nominations, nomination_queue)", "friday/app/round_close.py (step 5)"],
  },
  {
    id: "nominator", title: "Nominator", sub: "You.com grounded draft", kind: "role", at: ["close", 3],
    tip: "One You.com search on the pool shape, then one candidate decision: hook + payload, or no-decision.",
    what: "Drafts a candidate decision from a nominated pool: a hook (fires_when / not_this tags) and a payload a weak "
      + "worker can apply without the warrant, keyed on how a request presents, never on the defect's conclusion. The "
      + "runner mints the D- id. Hook and payload are frozen at drafting under a text hash.",
    process: [
      "One You.com search or research call on the pool shape.",
      "Draft one candidate in the decision form, or return no-decision with a reason (the observations reopen).",
      "Check the payload: word limit, blinding words, hedges; re-ask once.",
      "Write journal/D-NNNN.md with status candidate, the warrant (observations, sources), text_sha; commit.",
      "Also invited to draft a successor for a suspect decision from pools opened since the suspicion.",
    ],
    data: ["journal/D-NNNN.md front matter: id, status, pool, fires_when, not_this, supersedes, successors, text_sha", "Body: Payload, Warrant, Enforcement, Lifecycle"],
    algo: ["nominator.payload_max_words = 60, nominator.reasks = 1", "search.provider = youcom, search.results = 5"],
    models: ["nominator: zai-org/GLM-5.3 (differs from the judge's model, so the roles stay independent), fallback nvidia/NVIDIA-Nemotron-3-Super-120B-A12B, temperature 0.3"],
    code: ["friday/roles/nominator.py", "friday/adapters/youcom.py", "friday/algorithms/language.py (check_payload)", "friday/domain/hooks.py (text_hash)", "prompts/nominator/"],
  },
  {
    id: "gate", title: "Gate", sub: "e-process · betting", kind: "pure", at: ["close", 4],
    tip: "Admits a candidate when its paired e-process wealth reaches 1/α with n ≥ n_min and the quality guard holds.",
    what: "The only way into the Journal as admitted. Each in-flight candidate's exposure deltas from the Ledger drive a "
      + "betting e-process; wealth is recomputed from the deltas at every close and never stored, so it is valid however "
      + "often it is read. Precedence on one reading: admit, then refuse, then expire.",
    process: [
      "Read the candidate's exposure deltas d (and dq) from the Ledger.",
      "Recompute wealth W from d; a mirror e-process on −d reads harm.",
      "Admit: W ≥ 1/α, n ≥ n_min, and the one-sided upper bound of mean dQ ≤ quality_margin.",
      "Refuse: the mirror reaches 1/α. Expire: n ≥ n_max with neither.",
      "Refused or expired: mark the pool refused (the refused-pool block).",
    ],
    data: ["ledger_deltas (kind = exposure)", "journal/D-NNNN.md status flip with the Ledger account rendered into the warrant"],
    algo: [
      "W₀ = 1; W_t = W_{t−1} · (1 + λ_t · d_t)",
      "λ₁ = 0.25; λ_t = clip(m / (v + m² + ε), 0, 0.9), m, v from d₁..d_{t−1} (gate.betting = agrapa: lambda_first 0.25, lambda_max 0.9, eps 1e-9)",
      "gate.alpha = 0.3, so admit at W ≥ 3.33",
      "gate.n_min = 4, gate.n_max = 12, gate.in_flight = 2",
      "gate.quality_margin = 2.0, quality_level = 0.90, upper_bound = student_t",
    ],
    models: [],
    code: ["friday/algorithms/gate.py (GateRule, select_in_flight)", "friday/stats/eprocess.py (wealth, mirror_wealth)", "friday/stats/bounds.py (t_interval)", "friday/app/round_close.py (DecisionClose)"],
  },
  {
    id: "detector", title: "Detector", sub: "ablation · suspect · retire", kind: "pure", at: ["close", 5],
    tip: "Reads each admitted decision's ablation deltas: harm moves it to suspect; confirmation retires it.",
    what: "After admission the experiment continues. The detector reads ablation deltas two ways side by side, each "
      + "covering the other's blind spot, and the judge's dispositions as the enforcement reading. Neither reading "
      + "retires on its own: both move to suspect, and retirement waits for confirmation. A suspect decision stays "
      + "composed and keeps being ablated; an admitted successor supersedes it.",
    process: [
      "Short window: over the last detect.window ablation deltas, the one-sided upper bound of the mean is below 0.",
      "Long run: a harm e-process (the gate formula on −d), started at admission, reaches 1/α.",
      "Contradicted: the judge marks it contradicted on more than half its last detect.window composed passes.",
      "Any of these: admitted → suspect; the nominator is invited to draft successors.",
      "Retire after detect.confirm_pairs further ablation pairs whose mean's upper bound is still below 0; otherwise clear back to admitted (evidence starts fresh).",
    ],
    data: ["ledger_deltas (kind = ablation)", "fires.disposition", "journal front matter supersedes / successors (lists: lineage may be a DAG)"],
    algo: ["detect.ablate_p = 0.75", "detect.window = 4, detect.level = 0.80", "detect.confirm_pairs = 3", "detect.contradicted_share = 0.5", "harm e-process at gate.alpha = 0.3"],
    models: [],
    code: ["friday/algorithms/detector.py (Detector, ConfirmAction)", "friday/app/round_close.py"],
  },
  {
    id: "ledger", title: "Ledger", sub: "append-only paired deltas", kind: "store", at: ["closeB", 4],
    tip: "Every pair's delta d (and dQ), tagged exposure or ablation; SQLite triggers forbid update and delete.",
    what: "The evidence account of every decision. A settled pair writes one row; a pair where either pass failed for "
      + "transport gives no delta. Wealth, bounds and the warrant's Ledger line are always recomputed from these rows.",
    process: ["A pair settles: d = loss(without) − loss(with), dq = Q(with) − Q(without).", "Append one row tagged exposure or ablation.", "The gate reads exposure rows; the detector reads ablation rows dated after the last flip to admitted."],
    data: ["ledger_deltas(seq, decision_id, round, category, kind CHECK exposure|ablation, d CHECK −1..1, dq)", "Triggers ledger_no_update, ledger_no_delete"],
    algo: ["d ∈ [−1, 1]; positive d means the decision lowered loss"],
    models: [],
    code: ["friday/app/ledger.py", "friday/adapters/sqlite/schema.sql"],
  },
  {
    id: "journal", title: "Journal", sub: "markdown + git", kind: "store", at: ["closeB", 5],
    tip: "journal/D-NNNN.md, one git commit per status flip; git log journal/ is its lifecycle.",
    what: "The team's memory of what the worker should do, in markdown under git, because its history is the product. "
      + "Status lives in the front matter and is written only by the round close; the event log keeps the history. "
      + "A flip is validated against the lifecycle table before anything is written, and refused if the file's text no "
      + "longer matches its text_sha.",
    process: ["Validate the flip against domain/lifecycle.py.", "Append it to the event log.", "Render the Ledger account into the warrant.", "Write the file and make one git commit."],
    data: ["journal/D-NNNN.md: status candidate | admitted | suspect | retired | expired | refused", "events(seq, type, subject_id, round, body, applied_round), append-only"],
    algo: ["One commit per status flip", "A steer (retire, veto, note) is an event applied at the next close as one commit"],
    models: [],
    code: ["friday/adapters/journal_md.py", "friday/adapters/git.py", "friday/app/render.py (FlipRenderer)", "friday/domain/lifecycle.py", "friday/app/steer.py"],
  },
  {
    id: "readout", title: "Readout", sub: "R1–R4 · sign-flip bounds", kind: "app", at: ["readout", 5],
    tip: "Builds the held-out set in three arms (empty, notes, journal) and reads R1–R4 as pre-registered.",
    what: "Version N+1. After the halt the Journal (S*) and the notes file freeze; each held-out category is built "
      + "readout.reps times under R2 in three arms. Each reading's bound comes from inverting its own test, so its p and "
      + "its bound cannot disagree. Every reading is reported whichever way it falls.",
    process: [
      "Arms: empty (baseline only), notes (baseline + frozen notes file), journal (baseline + S* composed by hook).",
      "R1: per category mean of loss(empty) − loss(journal); exact one-sided sign-flip test over the category means.",
      "R2: per category mean of loss(notes) − loss(journal), non-inferiority by the same test's lower bound.",
      "R3: decisions admitted before the drift and retired after it; admitted after it; c and α · c beside them.",
      "R4: kept lenses at the close that the lens minter proposed.",
    ],
    data: ["Held-out passes per arm", "Reported beside: cost per admitted decision, Q per arm, how-to honesty, hook transfer"],
    algo: [
      "readout.reps = 4, readout.regime = R2, readout.test = sign_flip_exact",
      "readout.r1_p = 0.1 (must equal 1 − interval_level), interval_level = 0.90",
      "Lower bound: the (k+1)-th smallest subset mean, k = ⌊α · 2ⁿ⌋ − 1; 6 categories give 64 flips",
      "readout.r2_margin = 0.05: R2 earned when its bound lies above −0.05",
      "notes.max_tokens = 8000 (chars_per_token 4)",
    ],
    models: ["worker as in training (same pin and temperature, same eval, same self-repair rule)"],
    code: ["friday/app/readout.py", "friday/algorithms/readout.py (per_category_mean_diff, paired_reading)", "friday/stats/significance.py (PAIRED_TESTS)"],
  },
  {
    id: "export", title: "Export", sub: "JSON for the UI", kind: "app", at: ["readout", 4],
    tip: "Writes the run's stores, Journal and readout as JSON the static pages read.",
    what: "Turns the run into a static showcase: passes, decisions with their flips, Ledger accounts, lenses, pools and "
      + "readout readings, in one documented schema. The pages compute nothing that decides anything.",
    process: ["Read the stores, the Journal, the lens set and the readout.", "Write the export JSON beside the artifacts.", "The web server serves it with the frozen artifacts."],
    data: ["Schema in docs/export-schema.md"],
    algo: [],
    models: [],
    code: ["friday/app/export.py", "docs/export-schema.md"],
  },
  {
    id: "ui", title: "This UI", sub: "stage · gallery · replay …", kind: "ui", at: ["readout", 3],
    tip: "Plain ES modules and CSS over the export and the live telemetry log; no build step.",
    what: "The showcase: the stage, the gallery of paired builds, the live and replay views over telemetry, the timeline "
      + "of status flips, the readout, the what-if page and this diagram. Inline SVG drawn by its own code; a strict CSP.",
    process: ["Fetch the export JSON.", "Live views page the telemetry log by after_seq, every telemetry.live_poll_ms.", "Render with the h / s DOM helpers."],
    data: ["/api/telemetry rows", "export JSON"],
    algo: ["telemetry.live_poll_ms = 1000"],
    models: [],
    code: ["web/static/index.html", "web/static/js/*.js", "web/static/app.css"],
  },
  {
    id: "telemetry", title: "Telemetry", sub: "spans · metrics · events", kind: "store", at: ["cross", 0],
    tip: "The operational record: an append-only span/metric/event log in SQLite WAL. No decision reads it.",
    what: "Records every timed step, reading and run event. The gate, the detector, lens curation and the readout read only "
      + "the evidence stores; telemetry feeds the live views and the timing fit. A span covers a whole step, failures "
      + "included; an absent reading is a null metric, never 0; no row holds a prompt, a reply, source or a key.",
    process: ["Open a span per step (run, round, pass, eval, judge, close …) with a parent id.", "Emit metrics with units (s, usd, tokens, count, share, bytes, score).", "Emit events (run.manifest, run.fallback, round.ping, pass.outcome, decision.turnaround …)."],
    data: ["telemetry(seq, kind span|metric|event, name, run, round, pass_id, at, span_id, parent_id, ended_at, ok, error, value, unit, attrs, pid)", "Triggers telemetry_no_update, telemetry_no_delete"],
    algo: ["telemetry.busy_timeout_ms = 10000", "telemetry.live_poll_ms = 1000", "Catalogue in docs/telemetry.md: a row not listed is not emitted"],
    models: [],
    code: ["friday/ports/telemetry.py", "friday/adapters/telemetry.py", "friday/app/telemetry_catalogue.py", "friday/app/telemetry_report.py", "docs/telemetry.md"],
  },
  {
    id: "stores", title: "Stores", sub: "SQLite · append-only", kind: "store", at: ["cross", 1],
    tip: "friday.db in WAL mode: passes, evals, lens answers, fires, observations, pools, exposures, Ledger, events, spend.",
    what: "All high-volume run state in one SQLite file. Pass processes write only their own rows; the round close in the "
      + "parent is the single writer of statuses, pools and lenses. Evidence tables are append-only or insert-only, "
      + "enforced by triggers; the one exception is a steer's applied_round, set once.",
    process: ["Each pass process opens its own connection.", "Insert the pass's rows (pass, eval, lens answers, fires).", "The close writes observations, pools, events and flips, in order."],
    data: ["passes, evals, eval_details, lens_answers, fires, observations, pools, exposures, exposure_rounds, ledger_deltas, events, spend", "Unique index: one finished holder per pair slot"],
    algo: ["PRAGMA journal_mode = WAL", "Triggers: passes / evals insert-only; events and ledger_deltas append-only"],
    models: [],
    code: ["friday/ports/stores.py", "friday/adapters/sqlite/", "friday/adapters/sqlite/schema.sql", "friday/adapters/memory.py"],
  },
  {
    id: "model-client", title: "Model client", sub: "Crusoe endpoint", kind: "store", at: ["cross", 2],
    tip: "One client for every role: pins resolved at start-up, retries under a total deadline, per-role caps, spend rows.",
    what: "Roles name a role, never a model id. At start-up the pins resolve against GET /v1/models (an absent pin takes "
      + "its fallback, recorded); prices come from the same listing. Every call, failed ones included, writes a spend "
      + "row with the parameters sent and the model string the service echoed. Model JSON goes through one repairing "
      + "parser, re-asked once.",
    process: [
      "Resolve each role's pins against /v1/models; bind role → model.",
      "Take one of the role's max_concurrent slots (shared across pass processes).",
      "Send; each attempt returns within request_timeout_s in total or is abandoned.",
      "Retry retry_status with exponential backoff and jitter, at most max_tries; then the pass is failed for transport.",
      "Write the spend row; a slow first drafting call moves the drafting roles to their fallback.",
    ],
    data: ["spend(seq, at, role, tag, model, tokens_in, tokens_out, cost_usd, ok, error, sent)"],
    algo: [
      "transport.max_tries = 5, backoff_base_s = 1.0, backoff_cap_s = 60.0",
      "transport.retry_status = [429, 500, 502, 503, 504, 520, 522, 524]",
      "transport.request_timeout_s = 150 per attempt (deadline thread)",
      "models.roles.judge.max_concurrent = 8; models.slow_first_call_s = 60; json.reasks = 1",
    ],
    models: [
      "endpoint: managed-inference-api-proxy.crusoecloud.com/v1",
      "worker Nemotron-3-Nano-30B-A3B · rubric Nemotron-3.5-Super-VL · judge Nemotron-3-Super-120B-A12B",
      "drafting roles GLM-5.3 · coordinator gpt-oss-120b · critic Nemotron-3-Nano-Omni-Reasoning-30B-A3B",
    ],
    code: ["friday/ports/models.py", "friday/adapters/crusoe.py (CrusoeClient)", "friday/app/pins.py", "friday/adapters/cache.py", "friday/support/json_repair.py"],
  },
  {
    id: "critic", title: "Critic run", sub: "open world · taste · tempo", kind: "role", at: ["cross", 3],
    tip: "A second run: build, critic, revision. The manager's taste quirks reach the loop only as failing checks.",
    what: "Each pass mimics a developer under a manager: the worker builds, a vision critic reads the request, artifact, "
      + "how-to and a 1280×800 screenshot and returns a criticism and at most 2 feature requests, then the worker gets one "
      + "revision turn. Six taste quirks in critic/taste.yml join C as checks; the loop must learn them through "
      + "observation, pool, nominator and paired exposure. Own Journal, Ledger and lens set; readings are descriptive.",
    process: [
      "Build with self-repair, as in the main run.",
      "Critic call per worker of the pair, on its own artifact.",
      "One revision turn; the revised artifact is evaluated and judged.",
      "A = 0.65 · A_main + 0.35 · T (T: share of in-scope quirk checks passed); loss_base recorded too.",
      "decision.turnaround events per status step; friday critic-readout reads them.",
    ],
    data: ["run-critic.yml overlay (paths.*, run.rounds, critic.*, worker pin, three tempo knobs)", "critic/taste.yml (scope, preference, check)", "critic.db, critic_export.json"],
    algo: [
      "critic.taste_weight = 0.35, critic.max_requests = 2, critic.spend_cap_usd = 10.0, critic.max_parallel_passes = 6",
      "Tempo: pool.recurrence_min_rounds = 1, pool.fast_track_min_requests = 2, gate.in_flight = 4",
      "Gate bar unchanged: gate.alpha 0.3, n_min 4, n_max 12, agrapa",
    ],
    models: ["critic: nvidia/Nemotron-3-Nano-Omni-Reasoning-30B-A3B, fallback google/gemma-4-31b-it, temperature 0.3; never the rubric's model", "worker (critic run): deepseek-ai/DeepSeek-V4.1-Flash, fallback openai/gpt-oss-120b"],
    code: ["run-critic.yml", "friday/roles/critic.py", "friday/app/taste.py", "friday/app/tempo.py", "critic/taste.yml", "prompts/critic/"],
  },
];

// Arrows: [points…], label?, dashed?. Box geometry helpers keep them attached to the boxes above.
function box(id) {
  const c = C.find((x) => x.id === id);
  const [row, col] = c.at;
  return { x: COLS[col], y: ROW_Y[row], w: BW, h: BH };
}
const R = (id) => { const b = box(id); return [b.x + b.w, b.y + b.h / 2]; };
const L = (id) => { const b = box(id); return [b.x, b.y + b.h / 2]; };

function arrows() {
  const loop = ["coordinator", "pipeline", "eval", "loss", "judge", "observations"];
  const close = ["lens-curation", "pooling", "nomination", "nominator", "gate", "detector"];
  const out = [];
  for (let i = 0; i + 1 < loop.length; i++) out.push({ pts: [R(loop[i]), L(loop[i + 1])] });
  for (let i = 0; i + 1 < close.length; i++) out.push({ pts: [R(close[i]), L(close[i + 1])] });
  const g = box("goal-minter"), t = box("tagger"), p = box("pipeline"), o = box("observations"), lc = box("lens-curation");
  const ga = box("gate"), de = box("detector"), le = box("ledger"), jo = box("journal"), ro = box("readout");
  out.push({ pts: [R("goal-minter"), L("tagger")] });
  out.push({ pts: [[t.x + 60, t.y + t.h], [t.x + 60, p.y]], label: "tagged", lx: t.x + 68, ly: 172 });
  const yGap = 302;
  out.push({ pts: [[o.x + o.w / 2, o.y + o.h], [o.x + o.w / 2, yGap], [lc.x + 170, yGap], [lc.x + 170, lc.y]],
    label: "round's observations → round close", lx: 700, ly: yGap - 5 });
  out.push({ pts: [[le.x + 70, le.y], [ga.x + 70, ga.y + ga.h]], label: "exposure d", lx: le.x + 62, ly: 456, anchor: "end" });
  out.push({ pts: [[le.x + le.w, le.y + 60], [1340, le.y + 60], [1340, de.y + 70], [de.x, de.y + 70]], label: "ablation d", lx: 1334, ly: 520, rot: true });
  out.push({ pts: [[ga.x + 190, ga.y + ga.h], [ga.x + 190, 452], [jo.x + 50, 452], [jo.x + 50, jo.y]], label: "flips", lx: 1350, ly: 446 });
  out.push({ pts: [[de.x + 170, de.y + de.h], [de.x + 170, jo.y]], label: "flips", lx: de.x + 176, ly: 458 });
  out.push({ pts: [[jo.x + jo.w / 2, jo.y + jo.h], [jo.x + jo.w / 2, ro.y]], label: "S* frozen", lx: jo.x + jo.w / 2 - 8, ly: 606, anchor: "end" });
  out.push({ pts: [L("readout"), R("export")] });
  out.push({ pts: [L("export"), R("ui")] });
  // the feedback: admitted decisions compose by hook into the next round's passes
  out.push({ pts: [[jo.x + jo.w, jo.y + 30], [1598, jo.y + 30], [1598, 152], [p.x + 160, 152], [p.x + 160, p.y]],
    label: "admitted + candidates compose by hook (next round)", lx: 900, ly: 148, dashed: true, accent: true });
  return out;
}

// ---- rendering ----------------------------------------------------------------------------------------------

export class ArchitecturePage {
  constructor(root) {
    this.root = root;
    this.byId = new Map(C.map((c) => [c.id, c]));
    this.openId = null;
    this.render();
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && this.openId) { e.preventDefault(); this.close(); }
    });
    window.addEventListener("hashchange", () => this.close());
  }

  render() {
    const head = h("div", { class: "page-head" }, h("div", {},
      h("div", { class: "eyebrow" }, "Architecture"),
      h("h1", {}, "How Friday is built"),
      h("p", { class: "lede" }, "Two learning loops on one goal stream. The decision loop learns what the worker should do; ",
        "its evidence is the pair. The lens loop learns what the judge should look at; its evidence is the pass. ",
        "The eval is the one part that never learns. Hover a component for a gloss; click it for its process, data, ",
        "algorithm with run.yml knobs, models and code.")));

    this.tip = h("div", { class: "arch-tip", role: "tooltip" });
    const svg = this.diagram();
    const stage = h("div", { class: "arch-stage" }, svg, this.tip);

    this.drawer = h("aside", { class: "arch-drawer", "aria-hidden": "true", tabindex: "-1" });
    this.backdrop = h("div", { class: "arch-backdrop", onclick: () => this.close() });

    this.root.replaceChildren(head, stage, this.legend(), this.backdrop, this.drawer);
  }

  diagram() {
    const svg = s("svg", { class: "arch-svg", viewBox: `0 0 ${VB_W} ${VB_H}`, role: "img",
      "aria-label": "Friday architecture: goal side, round loop, round close, readout and cross-cutting parts" });
    svg.append(s("defs", {},
      s("marker", { id: "arch-head", viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse" },
        s("path", { d: "M0,0 L10,5 L0,10 z", class: "arch-head" })),
      s("marker", { id: "arch-head-accent", viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse" },
        s("path", { d: "M0,0 L10,5 L0,10 z", class: "arch-head-accent" }))));
    for (const b of BANDS) {
      svg.append(s("rect", { class: `arch-band arch-band-${b.id}`, x: 10, y: b.y, width: VB_W - 30, height: b.hgt, rx: 14 }),
        s("text", { class: "arch-band-label", x: 26, y: b.y + 20 }, b.label),
        s("text", { class: "arch-band-note", x: VB_W - 36, y: b.y + 20, "text-anchor": "end" }, b.note));
    }
    for (const a of arrows()) {
      const d = a.pts.map((p, i) => `${i ? "L" : "M"}${p[0]},${p[1]}`).join(" ");
      svg.append(s("path", { d, class: `arch-arrow${a.dashed ? " arch-dashed" : ""}${a.accent ? " arch-accent" : ""}`,
        "marker-end": a.accent ? "url(#arch-head-accent)" : "url(#arch-head)" }));
      if (a.label && a.lx !== undefined) {
        svg.append(s("text", { class: `arch-arrow-label${a.accent ? " arch-accent-text" : ""}`, x: a.lx, y: a.ly,
          "text-anchor": a.anchor || null, transform: a.rot ? `rotate(-90 ${a.lx} ${a.ly})` : null }, a.label));
      } else if (a.label) {
        const [p0, p1] = a.pts;
        svg.append(s("text", { class: "arch-arrow-label", x: (p0[0] + p1[0]) / 2, y: (p0[1] + p1[1]) / 2 - 8, "text-anchor": "middle" }, a.label));
      }
    }
    // notes inside bands
    svg.append(s("text", { class: "arch-note", x: 570, y: 74 }, "goal stream: 10 requests a round, frozen in git before round 1"));
    svg.append(s("text", { class: "arch-note", x: 570, y: 98 }, "held-out set: 6 requests, one per withheld cell, built only at the readout"));
    svg.append(s("text", { class: "arch-note", x: 570, y: 122 }, "drift at round 4: the goal mix shifts and a phone-width constraint joins every request"));
    svg.append(s("text", { class: "arch-note", x: 40, y: 500 }, "Steers, then lens curation, observations, pooling,"));
    svg.append(s("text", { class: "arch-note", x: 40, y: 522 }, "nomination, gate, detector, pings. Every status flip:"));
    svg.append(s("text", { class: "arch-note", x: 40, y: 544 }, "validate → event log → file → one git commit."));
    svg.append(s("text", { class: "arch-note", x: 40, y: 650 }, "Held-out × 4 reps under R2 in three arms:"));
    svg.append(s("text", { class: "arch-note", x: 40, y: 672 }, "empty · notes · journal (S*). Pre-registered R1–R4."));
    svg.append(s("text", { class: "arch-note", x: 1100, y: 810 }, "Pass processes write only their own rows;"));
    svg.append(s("text", { class: "arch-note", x: 1100, y: 832 }, "the round close is the single writer."));
    svg.append(s("text", { class: "arch-note", x: 1100, y: 854 }, "No decision reads telemetry."));

    for (const c of C) svg.append(this.node(c));
    return svg;
  }

  node(c) {
    const b = box(c.id);
    const g = s("g", { class: `arch-node arch-k-${c.kind}`, tabindex: "0", role: "button", "data-id": c.id,
      "aria-label": `${c.title}: ${c.tip}` });
    g.append(s("rect", { class: "arch-box", x: b.x, y: b.y, width: b.w, height: b.h, rx: 12 }),
      s("rect", { class: "arch-stripe", x: b.x, y: b.y + 12, width: 5, height: b.h - 24, rx: 2 }),
      s("text", { class: "arch-title", x: b.x + 18, y: b.y + 38 }, c.title),
      s("text", { class: "arch-sub", x: b.x + 18, y: b.y + 66 }, c.sub));
    if (c.models.length && c.kind === "role") g.append(s("circle", { class: "arch-model-dot", cx: b.x + b.w - 18, cy: b.y + 18, r: 5 }));
    g.addEventListener("mouseenter", (e) => this.showTip(c, e));
    g.addEventListener("mousemove", (e) => this.moveTip(e));
    g.addEventListener("mouseleave", () => this.hideTip());
    g.addEventListener("focus", () => { if (g.matches(":focus-visible")) this.showTipAt(c, g); });
    g.addEventListener("blur", () => this.hideTip());
    g.addEventListener("click", (e) => { e.stopPropagation(); this.open(c.id); });
    g.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); this.open(c.id); }
    });
    return g;
  }

  // ---- tooltip --------------------------------------------------------------------------------------------

  showTip(c, e) {
    this.tip.replaceChildren(h("b", {}, c.title), h("span", {}, c.tip));
    this.tip.classList.add("arch-on");
    this.moveTip(e);
  }

  moveTip(e) {
    const stage = this.tip.parentElement.getBoundingClientRect();
    const tw = this.tip.offsetWidth, th = this.tip.offsetHeight;
    let x = e.clientX - stage.left + 16;
    let y = e.clientY - stage.top + 18;
    if (x + tw > stage.width - 4) x = e.clientX - stage.left - tw - 16;
    if (y + th > stage.height - 4) y = e.clientY - stage.top - th - 14;
    this.tip.style.left = `${Math.max(4, x)}px`;
    this.tip.style.top = `${Math.max(4, y)}px`;
  }

  showTipAt(c, g) {
    const r = g.getBoundingClientRect();
    this.showTip(c, { clientX: r.left + r.width / 2, clientY: r.bottom });
  }

  hideTip() { this.tip.classList.remove("arch-on"); }

  // ---- drawer ---------------------------------------------------------------------------------------------

  open(id) {
    const c = this.byId.get(id);
    if (!c) return;
    this.hideTip();
    this.openId = id;
    for (const n of this.root.querySelectorAll(".arch-node")) n.classList.toggle("arch-sel", n.dataset.id === id);
    const sec = (title, body) => (body ? h("section", { class: "arch-sec" }, h("h3", {}, title), body) : null);
    const list = (items, ordered = false) => (items && items.length
      ? h(ordered ? "ol" : "ul", { class: "arch-list" }, ...items.map((x) => h("li", {}, x))) : null);
    const codeList = (items) => (items && items.length
      ? h("ul", { class: "arch-list arch-code" }, ...items.map((x) => h("li", {}, h("code", {}, x)))) : null);
    const close = h("button", { class: "arch-close", type: "button", onclick: () => this.close() }, "Close");
    this.drawer.replaceChildren(
      h("div", { class: "arch-drawer-head" },
        h("div", {}, h("div", { class: `arch-kind arch-k-${c.kind}` }, KINDS[c.kind].label), h("h2", {}, c.title)),
        close),
      h("div", { class: "arch-drawer-body" },
        h("p", { class: "arch-tipline" }, c.tip),
        sec("What it does", h("p", {}, c.what)),
        sec("Process", list(c.process, true)),
        sec("Data / schema", list(c.data)),
        sec("Algorithm", list(c.algo)),
        sec("Models", list(c.models)),
        sec("Code", codeList(c.code))));
    this.drawer.classList.add("arch-on");
    this.backdrop.classList.add("arch-on");
    this.drawer.setAttribute("aria-hidden", "false");
    this.lastFocus = document.activeElement;
    close.focus();
  }

  close() {
    if (!this.openId) return;
    this.openId = null;
    this.drawer.classList.remove("arch-on");
    this.backdrop.classList.remove("arch-on");
    this.drawer.setAttribute("aria-hidden", "true");
    for (const n of this.root.querySelectorAll(".arch-node.arch-sel")) n.classList.remove("arch-sel");
    if (this.lastFocus && this.root.contains(this.lastFocus)) this.lastFocus.focus();
  }

  // ---- legend ---------------------------------------------------------------------------------------------

  legend() {
    const kinds = Object.entries(KINDS).map(([k, v]) => h("div", { class: "arch-leg-item" },
      h("span", { class: `arch-swatch arch-k-${k}` }), h("b", {}, v.label), h("span", { class: "muted" }, v.note)));
    const layers = [
      ["pure", "domain · stats · algorithms · support", "entities, lifecycles, hooks; e-process, bounds, ranks, sign-flip test, counter-based rng; loss, gate, detector, curation. Stdlib only."],
      ["ports", "ports", "Protocols every layer above codes against: ModelClient, SearchClient, Browser/Page, QualityProbe, stores, Journal, LensSet, VersionControl, Telemetry."],
      ["adapters", "adapters", "the only place a vendor SDK or the network appears: Crusoe, You.com, Playwright, SQLite, markdown Journal and lens set, git."],
      ["app", "eval · roles · app", "eval and roles over ports only; app is the composition root, the pass pipeline, the round close (the single writer) and the readout."],
    ];
    return h("div", { class: "arch-legend" },
      h("div", { class: "arch-leg-col" }, h("h3", {}, "Colour"), ...kinds,
        h("div", { class: "arch-leg-item" }, h("span", { class: "arch-dot-sample" }), h("b", {}, "model call"),
          h("span", { class: "muted" }, "the role calls a pinned model through the model client")),
        h("div", { class: "arch-leg-item" }, h("span", { class: "arch-line-sample" }), h("b", {}, "feedback"),
          h("span", { class: "muted" }, "the Journal composes into the next round"))),
      h("div", { class: "arch-leg-col" }, h("h3", {}, "Layers (imports enforced by tests/architecture/test_layers.py)"),
        ...layers.map(([k, name, note]) => h("div", { class: "arch-leg-item" },
          h("span", { class: `arch-layer arch-layer-${k}` }, name), h("span", { class: "muted" }, note))),
        h("p", { class: "muted small" }, "Interchangeable statistics are registered by name and chosen in run.yml (gate.betting.name, ",
          "gate.upper_bound, lens.value, lens.fold_correlation, readout.test); an unknown key raises at start-up.")));
  }
}
