# Friday: a self-improving builder team with a journal it can account for

Spec for the Recursive Agents Hackathon (Cerebral Valley × Crusoe, San
Francisco, 2026-10-17; hacking 10:45–17:00 PT). Readers: the build agents
and the humans who direct them during the event.

**Working name: Friday.** Crusoe's companion in Defoe's novel, the one he
teaches.
The team is **Friday**; its decision store is **the Journal** (Crusoe's
diary); its evidence store is **the Ledger**. Crusoe draws up his state as a
two-column account, *Evil* against *Good*, "like debtor and creditor"; the
Ledger keeps, per decision, a running wealth over paired deltas (§ 8), the
same weighing done as a bet. 

---

## 0. North star

**Build a team you can walk away from, whose memory you can audit.** Given a
stream of one-line build requests, a coordinator agent runs a team of weak
worker agents that ship working single-file web apps. A separate judge reads
each result through a curated set of questions (lenses) and files
observations. Recurring observations become candidate decisions; a decision
enters the Journal only when a randomized paired experiment shows it lowers a
fixed loss, and it leaves the Journal when the same experiment, kept running,
shows it stopped paying. The world shifts mid-run; the Journal forgets what
the shift made false and learns what it made true. At the close, the frozen
Journal is scored once on requests the team never saw.

The final goal, stated as the readings the demo carries (pre-registered, § 12):

1. **Version N+1.** Held-out loss under the frozen Journal S\* is lower than
   under the empty Journal S₀, on the same fixed eval, paired by request.
2. **Lean and useful.** The Journal's held-out loss is no worse than the
   notes-file arm's, at a memory a fraction of its size.
3. **Forgetting is earned.** After the drift round, a decision admitted
   before it is retired by evidence, and a decision is admitted after it.
4. **The judge's eyes grow.** The kept lens set at the close contains
   lenses minted during the run.

Each reading is reported whichever way it falls (§ 12). A reading that fails
is a finding, never a slide that is dropped.

**Not the goal:** a leaderboard score, a dashboard, a vector store, or a
gate that beats no gate. munchhausen (`../munchhausen`, 2026-10-11) carried
that last forecast and lost it (governed 29 against ungated 36 of 40 sealed
questions); Friday's claim is the Ledger's accounting, never governance
for its own sake.

---

## 1. How Friday answers the hackathon

| Hackathon item                                                                                                                                    | Friday's answer                                                                                                                                                                                                                                                   | Where       |
| ------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------- |
| Theme: a team that plans, spawns sub-agents, delegates, checks its progress, recovers, keeps going                                                | The coordinator agent plans each round, spawns workers per category, decides re-runs and halts, and pings a human only on a contested decision; workers repair their own artifact once; five roles communicate only through the Journal, the Ledger and the pools | §§ 3, 11    |
| Problem statement *Shared Brain*: a living memory every agent reads before work and updates after; current, lean, conflict-free; measurable gains | The Journal: composed into every worker's context by hook; written only by the gate; leanness by cap, fold and retirement; gains read by paired exposure                                                                                                          | §§ 7–9      |
| Problem statement *Version N+1*: rewrite its own playbooks, keep a change only if it measurably helps, before/after on unseen tasks               | The gate admits a decision only on paired evidence; the lens loop rewrites the judge's questions; the close scores S₀ against S\* on held-out requests                                                                                                            | §§ 6, 8, 12 |
| Problem statement *Done Means Done*: its own definition of done, checks it runs without you, catches drift and loops, rarely pings a human        | Acceptance checks minted with each request; the drift detector; the refused-pool block against re-nominating a loop; the halt rule; the coordinator's ping                                                                                                        | §§ 4, 9, 11 |
| Judging: Technicality 40 %                                                                                                                        | Two learning loops (the worker's playbook and the judge's eyes), an anytime-valid paired gate, composed drift detectors, all on legible text                                                                                                                      | §§ 6–9      |
| Judging: Live demo 20 %                                                                                                                           | A gallery of apps built during the run; one live pass end to end; the Journal's git log                                                                                                                                                                           | § 13        |
| Judging: Creativity 25 %                                                                                                                          | Memory as audited, retirable decisions, observed through a judge whose questions are themselves curated; no embeddings ("we don't embed, we ask")                                                                                                                 | § 6         |
| Judging: Future potential 15 %                                                                                                                    | An agent memory whose every line carries its evidence and its retirement condition                                                                                                                                                                                | § 0         |
| Rule: new work only, public repository                                                                                                            | All code is written at the event in a new public repository. Ideas come from GCE and type-gce; code from neither (type-gce is private and bound to TypeSafe)                                                                                                      | —           |
| Rule: banned projects (basic RAG, Streamlit, dashboard as the main feature)                                                                       | No retrieval over embeddings; the gallery and the live pass lead, the timeline is secondary; plain HTML, no Streamlit                                                                                                                                             | § 13        |

### Sponsors

| Sponsor                      | Use                                                                                                                                                                                                                                                        | Prize reached                    |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------- |
| **Crusoe Managed Inference** | Every model call, through one OpenAI-compatible endpoint; spend read from Crusoe's metrics API as cost per admitted decision                                                                                                                               | the main prizes                  |
| **NVIDIA Nemotron**          | The default pins are Nemotron for every model role except where § 5 names a stronger image model (§ 5)                                                                                                                                                     | Best Use of Nemotron             |
| **You.com**                  | The nominator grounds each draft decision in one search or research call and cites the sources in the decision's warrant                                                                                                                                   | podium credits                   |
| **NemoClaw / OpenShell**     | Optional, timeboxed 45 min after the full run starts: run the eval's headless browser inside an OpenShell sandbox, since it executes untrusted generated code. Drop on first blocking failure; the fallback is Playwright with every network route aborted | Best Use of NemoClaw & OpenShell |

**Do:** route every model call through the Crusoe client (§ 14), so spend and
the model string the service echoes are recorded per call.
**Don't:** call a model outside it for convenience; an unmetered call breaks
the cost-per-decision reading and the pin record.

---

## 2. Vocabulary

One term per concept; no synonyms in code, prompts or prose. A concept with no
term is added here first, then used. "GCE" cites `gce/GCE-DOCTRINE-V2.md` (in
type-gce) by section.

| Term                      | Means in Friday                                                                                                                                                                                                            | Anchor                                          |
| ------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| **request**               | A one-line build demand in a user's words, plus its interface contract, tags and acceptance checks. Frozen in the request bank                                                                                             | hackathon: the unit of work                     |
| **category**              | One request's identity. Training categories repeat the exact same request every round                                                                                                                                      | § 4                                             |
| **request bank**          | The frozen set of categories, minted once before round 1, split into training and held-out                                                                                                                                 | § 4; GCE § 14.5 (genesis)                       |
| **tag**                   | A trait word attached to a request at minting (e.g. `canvas`, `timer`, `keyboard`, `persistence`). Hooks match on tags. The tag vocabulary is closed at minting                                                            | GCE § 9 (legible symbolic keys), § 2 position 1 |
| **interface contract**    | The element ids a request requires (e.g. `#start`, `#score`) and its test hooks (e.g. `?test=1` shortens durations), stated in the request so acceptance checks are deterministic                                          | § 4                                             |
| **acceptance check**      | One declarative browser step list with an expectation, run by the eval. Each request carries 4–6 per regime                                                                                                                | § 4                                             |
| **contract stub**         | An HTML page holding every contract id and no script. A valid check fails on it                                                                                                                                            | § 4                                             |
| **regime**                | The world's current conditions. `R1` before the drift round, `R2` from it on. The drift constraint is what differs                                                                                                         | § 10                                            |
| **drift constraint**      | One sentence appended to every request from the drift round on, with its own acceptance checks                                                                                                                             | § 10                                            |
| **pass**                  | One worker building one request once (with at most one self-repair turn), then eval, then judge. The row of the run                                                                                                        | GCE § 7.6 (fast loop)                           |
| **round**                 | Every training category built by its two paired workers, plus the notes arm's pass per category; then the round close. One accrual step                                                                                    | § 8                                             |
| **round close**           | The single-writer step after a round's passes: lens curation, pooling, nomination, gate, detector, status flips, git commits                                                                                               | § 11                                            |
| **arm**                   | One policy of memory use. `journal` (Friday), `notes` (the shared-notes baseline), `empty` (no memory, read only at the close)                                                                                             | § 12                                            |
| **coordinator**           | The agent that runs the team: plans each round, spawns passes, decides re-runs, halts, pings a human. It never computes a statistic or writes the Journal                                                                  | § 11; GCE § 14.2                                |
| **worker**                | The weak model that turns a composed prompt into one HTML file, with one self-repair turn. Keeps no memory of its own                                                                                                      | GCE § 14.2 (the implementer keeps no ledger)    |
| **self-repair turn**      | One extra worker turn given its own artifact's console errors and missing contract ids, never the acceptance checks                                                                                                        | § 5                                             |
| **baseline instructions** | The fixed worker system prompt both arms of every pair receive (Appendix A)                                                                                                                                                | § 5                                             |
| **composition**           | The worker's prompt: the baseline instructions, the admitted decisions whose hooks match (up to the cap), the candidate under test if exposed, the request                                                                 | GCE § 4.2                                       |
| **cap**                   | The most admitted decisions composed into one prompt. Over the cap, the ones with the lowest e-value are left out. A candidate under test is composed in addition to the cap                                               | GCE § 6 I8                                      |
| **eval**                  | The fixed scorer. Never learns, never changes during a run. Runs the acceptance checks in a headless browser and asks the rubric model one frozen question set over a screenshot                                           | GCE § 7.1 (the world), § 6 I12                  |
| **loss**                  | The scalar the gate reads: `1 − (w_a · A + w_r · R)`, A the passed fraction of acceptance checks, R the rubric score on [0, 1]                                                                                             | § 5                                             |
| **quality reading**       | ESLint error count on the inline script plus axe-core violation count on the rendered page. Never part of the loss                                                                                                         | § 5                                             |
| **quality guard**         | The non-inferiority bar a candidate also clears (§ 8)                                                                                                                                                                      | § 8                                             |
| **judge**                 | The strong model that reads a finished pass in two calls: the lens call (artifact only) and the report call (artifact plus eval report). Separate from the eval by construction: the judge explains loss, the eval sets it | GCE § 14.2 (roles as independence)              |
| **lens**                  | A typed question the judge answers in the lens call on every pass: the angle, its counterfactual, a probability and an evidence quote from the artifact                                                                    | GCE § 12.6                                      |
| **lens set**              | The active lenses. The 8 baseline lenses of § 6 seed it; it grows by minting and shrinks by fold, floor and cap                                                                                                            | GCE § 12.6                                      |
| **lens value**            | How well a lens's lens-call answers predict the pass's loss over a window (§ 6)                                                                                                                                            | § 6                                             |
| **floor**                 | The bar a lens's value clears to stay: the 95th percentile of the same value over shuffled losses                                                                                                                          | GCE § 7.4                                       |
| **fold**                  | Merging two lenses whose answers move together; one survives, the other is superseded with a pointer                                                                                                                       | GCE § 7.4 (split and fold)                      |
| **lens minter**           | The step that proposes new lenses from the residual view                                                                                                                                                                   | § 6                                             |
| **residual view**         | The passes in the lens window whose loss is high but which no kept lens flagged. The minter's only input besides the lens set                                                                                              | type-gce: the windowed miss view                |
| **observation**           | One noticing the judge files in the report call: lens id (or `free`), category, a one-sentence shape, a direction (what would have lowered loss), the evidence quote. Lowest trust. Never becomes a decision alone         | GCE § 7.1, § 7.4                                |
| **disposition**           | The judge's verdict on each composed decision for one pass: `applied`, `not-applicable`, `contradicted`                                                                                                                    | GCE § 7.6 (use-time dispositions)               |
| **pool**                  | A stable group `P-NNNN` of observations under one lens id sharing one shape, with at most two direction labels                                                                                                             | § 7; GCE § 7.4 (piles grouped by shape)         |
| **direction label**       | A short phrase naming what would lower loss, assigned by the pooling call, one of at most two per pool                                                                                                                     | § 7                                             |
| **recurrence bar**        | When a pool may be nominated: at least 3 open observations from at least 2 rounds, all under one direction label                                                                                                           | GCE § 7.4 (counts nominate)                     |
| **nominator**             | The strong model that drafts a candidate decision from one pool, grounded by one You.com call                                                                                                                              | GCE § 6 I2 (the machine proposes)               |
| **decision**              | One Journal entry `D-NNNN`: hook, payload, warrant, enforcement, lifecycle. Its id is minted at drafting; its text is frozen from drafting; only its status flips                                                          | GCE § 6 (the entry contract)                    |
| **hook**                  | A decision's activation: it fires on a request iff the request carries at least one `fires_when` tag and no `not_this` tag                                                                                                 | GCE § 9                                         |
| **reach**                 | The training categories a decision's hook fires on                                                                                                                                                                         | § 8                                             |
| **payload**               | The directive a decision gives the worker, ≤ 60 words, in the imperative                                                                                                                                                   | GCE § 6                                         |
| **warrant**               | What grounds a decision: the observation ids it came from, the You.com citations, and its Ledger account                                                                                                                   | GCE § 6                                         |
| **enforcement**           | What checks the decision is followed: the judge's disposition; and its residue, what nothing checks                                                                                                                        | GCE § 6 I10                                     |
| **lifecycle**             | A decision's status and its retirement condition: `candidate → admitted → suspect → retired`, `suspect → admitted` (cleared), `candidate → expired`, `candidate → refused`                                                 | GCE § 6 I7                                      |
| **candidate**             | A drafted decision under test. Composed only into treatment workers                                                                                                                                                        | § 8                                             |
| **in flight**             | A candidate currently receiving exposure; at most `gate.in_flight` at once                                                                                                                                                 | § 8                                             |
| **exposure**              | The coin, per pair, deciding which of the two workers gets the decision under test                                                                                                                                         | § 8                                             |
| **pair**                  | The two journal-arm passes of one category in one round, identical except for one exposure                                                                                                                                 | § 8                                             |
| **paired delta**          | `d = loss(without) − loss(with)` for one pair, in [−1, 1]; positive means the decision helped                                                                                                                              | § 8                                             |
| **Ledger account**        | A decision's running record of paired deltas and its e-process wealth                                                                                                                                                      | GCE § 10.4 (the evidence state)                 |
| **e-process**             | The betting wealth over paired deltas, valid at any stopping time; the gate's statistic                                                                                                                                    | § 8                                             |
| **gate**                  | The pre-registered mechanical bar that admits, expires or refuses a candidate                                                                                                                                              | § 8; deviation D1                               |
| **admission**             | The gate's status flip from `candidate` to `admitted`, written as one git commit                                                                                                                                           | GCE § 5                                         |
| **ablation**              | After admission, exposure kept running: one worker of a pair omits the admitted decision                                                                                                                                   | § 9                                             |
| **detector**              | The two readings over an admitted decision's ablation deltas that move it to `suspect`                                                                                                                                     | § 9                                             |
| **retire**                | The status flip ending a decision, with a tombstone naming the evidence and the successor, if any                                                                                                                          | GCE § 5 (tombstones)                            |
| **supersede**             | Retiring a decision by pointing to its successor                                                                                                                                                                           | GCE lexicon                                     |
| **refused pool**          | A pool whose candidate expired or was refused. Its observations stay closed; it is re-nominated only when observations dated after the refusal reach the recurrence bar                                                    | § 8; munchhausen LESSONS § 12                   |
| **ping**                  | The coordinator's notice to the human, on the conditions of § 11. The loop never waits for an answer                                                                                                                       | § 11                                            |
| **steer**                 | A human's correction from the CLI: retire, veto or annotate a decision                                                                                                                                                     | GCE § 7.1; deviation D1                         |
| **notes arm**             | The baseline shared memory: every `free` observation from journal-arm passes is appended, deduplicated by exact text, to one notes file every notes-arm worker reads                                                       | § 12                                            |
| **S₀ / S\***              | The Journal at round 1 (empty) and the Journal frozen at the close                                                                                                                                                         | type-gce ADR-031 run roles                      |
| **held-out set (V)**      | The categories never built during training, read once at the close                                                                                                                                                         | type-gce ADR-031                                |
| **readout**               | The single close scoring of `empty`, `notes` and `journal` on V                                                                                                                                                            | § 12                                            |
| **run spec**              | `run.yml`: every constant, pin and seed, committed before round 1                                                                                                                                                          | § 14                                            |

---

## 3. The system at a glance

```
                     COORDINATOR (agent): plans the round, spawns passes, re-runs, halts, pings
                          │
            ┌─────────────┴──── request bank (frozen) ───────────┐
            │  16 training categories × 2 paired workers         │  6 held-out (V, read once)
            ▼                                                    │
  composition ──► WORKER (weak) ─(1 self-repair)─► index.html ──► EVAL (fixed) ──► loss, quality
       ▲                                  │                                    │
       │                     JUDGE lens call (artifact only) ──► lens answers   │
       │                     JUDGE report call (+ eval report) ◄───────────────┘
       │                                  │ observations, dispositions
       │                                  ▼
       │                     pools P-NNNN (stable) ──► recurrence bar
       │                                  ▼
   JOURNAL ◄── admission ◄── GATE ◄── candidate ◄── NOMINATOR (+ You.com)
   (git, D-NNNN)  (paired e-process, quality guard)
       └──► ablation ──► DETECTOR ──► suspect ──► retire / supersede / clear

  Lens loop:  lens answers × loss ──► value, floor, fold, cap ──► residual view ──► LENS MINTER
```

Two learning loops run on one stream:

- **The decision loop** learns what the worker should do. Its yardstick is the
  loss; its unit of evidence is the pair.
- **The lens loop** learns what the judge should look at. Its yardstick is the
  same loss; its unit of evidence is the pass. The lens call never sees the
  eval, so a lens's value measures foresight from the artifact, not a reading
  of the label back. No human labels: the eval is the label, so lens curation
  is self-supervised.

The eval is the one part that never learns. That is what makes every
before/after in the run readable.

Five agent roles (coordinator, worker, judge, nominator, lens minter) never
message each other directly; they read and write the Journal, the Ledger, the
pools and the lens set. That is the Shared Brain's claim made literal: the
team's only channel is its memory.

---

## 4. The request bank

**Do:** mint the bank once, before round 1, with the nominator's model, as
plain requests a non-technical person would type ("a pomodoro timer that
plays a sound and counts completed sessions"). Freeze it in
`bank/requests.json` and commit it before the first pass.
**Don't:** tune requests to be hard, easy or flattering to the loop after
seeing any worker output; a request touched after round 1 voids every pair
that built it.

Counts (homes in `run.yml`): 22 categories: 16 training, 6 held-out.

Each request carries:

- `id`, `text` (one line), `tags` (2–4 from the closed tag vocabulary of
  10–14 tags minted with the bank),
- `contract`: the required element ids, each with its role in one phrase;
  for any request with a duration, a `?test=1` query that scales every
  duration to ≤ 2 s, stated in the request text the worker reads,
- `checks.R1` and `checks.R2`: 4–6 acceptance checks each, in the check DSL,
  `R2` written against the request plus the drift constraint.

**The split.** Draw 6 held-out categories at random under the seed in
`run.yml`; redraw until every held-out category's tags are a subset of the
union of training tags, at most 1,000 draws; if none qualifies, re-mint the
tags once and repeat.
**Why:** a hook fires on tags; a held-out category whose tags the training
never saw can receive no decision, and the readout would measure nothing.

**Check validation.** For every request, the nominator's model builds one
reference app per regime and the bank minter writes the contract stub. Every
check must pass on the reference and fail on the stub; a check failing either
test is regenerated, at most twice, then dropped. A request left with fewer
than 4 checks in either regime is regenerated whole.
**Don't:** take the reference's pass rate as a target or a ceiling; it proves
the checks are satisfiable and discriminating, nothing more. The references
are never shown to a worker.

**The check DSL** (JSON, run by the eval through Playwright):

```json
{ "id": "c3", "steps": [ {"goto": "?test=1"}, {"click": "#start"}, {"wait": 2500} ],
  "expect": {"text_changed": "#sessions"} }
```

- Steps: `goto(query)` (reloads with the query), `click(sel)`,
  `type(sel, text)`, `key(name, times = 1)` (sent to the focused element),
  `focus(sel)`, `wait(ms ≤ 3000)`.
- Expectations: `exists(sel)`, `visible(sel)`, `focused(sel)`,
  `text_matches(sel, regex)` (unanchored, matched against the element's text;
  write it against the value, never the label: `\b1\b`, not `^Score: 1$`),
  `text_changed(sel)`, `canvas_changed(sel)` (pixel hash before vs after the
  steps; a check using it also carries a control: the same hash taken with
  no steps must not change over the same wait), `no_console_errors`.
- Each check runs on a fresh page load at 1280×800. A check that times out
  fails. `no_console_errors` counts as one check at most per request.

---

## 5. Roles and models

The worker is weak on purpose: a strong worker saturates the checks and leaves
no room for memory to show. The judge and the nominator are strong. The eval
is deterministic where it can be and pinned where it cannot.

| Role                                              | Default pin (Crusoe model id)                                                                                                                                                        | Fallback                                        | Temperature  | Input                     | Output          |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------- | ------------ | ------------------------- | --------------- |
| worker                                            | `nvidia/Nemotron-3-Nano-30B-A3B`                                                                                                                                                     | `openai/gpt-oss-120b`                           | 0.7, no seed | composition               | one HTML file   |
| eval rubric                                       | the strongest image-input model `/v1/models` lists among `google/gemma-4-31b-it`, `zai-org/GLM-5.3-Flash`, `Qwen/Qwen3.8-27B` (chosen by the order written here, first present wins) | `nvidia/Nemotron-3-Nano-Omni-Reasoning-30B-A3B` | 0            | screenshot + request text | 3 scores on 1–5 |
| judge                                             | `nvidia/Nemotron-3-Super-120B-A12B`                                                                                                                                                  | `deepseek-ai/DeepSeek-V4-Pro`                   | 0            | text only (§ 6)           | JSON            |
| nominator, lens minter, bank minter, pooling call | Nemotron 3 Ultra (id unconfirmed: `nvidia/NVIDIA-Nemotron-3-Ultra-550B` in Crusoe's Pi guide)                                                                                        | `nvidia/Nemotron-3-Super-120B-A12B`             | 0.3          | pool or residual view     | JSON            |
| coordinator                                       | `openai/gpt-oss-120b` (Crusoe names it the most reliable at tool calling)                                                                                                            | `nvidia/Nemotron-3-Super-120B-A12B`             | 0.2          | round state               | tool calls      |

Pins live in `run.yml`. At start-up the runner reads `GET /v1/models`; a
pinned id that is absent is replaced by its fallback, and the replacement is
recorded. The runner also records each model string the service echoes. An
Ultra call taking more than 60 s on the first bank-minting call moves the
Ultra roles to their fallback for the whole run.

**The worker pin rule (the one data-informed choice, fixed in advance).** The
pilot (§ 13) builds every training category once with the default worker. If
fewer than 5 of 16 artifacts pass at least one acceptance check, switch the
worker to the fallback and re-pilot; if the pilot's mean `A` exceeds 0.85,
the worker leaves too little headroom: record it as a falsified premise
(§ 16) and keep it. Nothing else reads the pilot.
**Don't:** re-pick the worker after round 1, or pick any other constant from
pilot output.

**Worker output contract.** One HTML file, inline CSS and JS, no external
URL (the eval aborts every network route), ≤ 30 KB, containing every
contract id. The worker receives the baseline instructions (Appendix A),
frozen in the repository before round 1. After its first output, the worker
gets one self-repair turn when the artifact logs a console error on load or
lacks a contract id: the turn shows those errors and missing ids, never the
acceptance checks. Both workers of a pair get the same repair rule.

**Eval.**

- `A`: passed checks ÷ checks for the current regime.
- `R`: the rubric model scores the screenshot on three frozen questions
  (does it look like the request; is every contract control visible without
  scrolling at 1280×800; is it legible), mapped from 1–5 to 0–1 and
  averaged. Temperature 0; cached by artifact hash.
- `loss = 1 − (w_a · A + w_r · R)` with `w_a = 0.75`, `w_r = 0.25` in
  `run.yml`.
- Quality reading `Q`: ESLint error count over the extracted inline script
  (fixed config in the repository) plus axe-core violation count on the
  loaded page.

**Do:** treat the eval as frozen from round 1: its code, prompts, weights and
pins change only between runs.
**Don't:** fold `Q` into the loss; it moves the guard and the report, never
the scalar the gate reads.

**Why the rubric is a minority term.** The checks are the deterministic
backbone and nothing in the run can game them; the rubric catches what checks
cannot (a blank canvas that technically changes pixels). A model-graded
label never drives structure alone (GCE § 6 I12): the rubric carries a
quarter of the loss.

**Model JSON.** Crusoe's structured-output support varies by model (read
`features` from `/v1/models`). Request JSON where offered; decode every model
JSON through one repairing parser, re-ask once on a parse failure, then
record the call as failed (munchhausen LESSONS § 7).

---

## 6. The judge and the lens loop

### Two calls per journal-arm pass

**Lens call.** Input: the request text, the ids and payloads of the composed
decisions, the artifact source. No eval report, no rubric, no regime flag.
Output: `lenses`, for each active lens `{id, p, quote}`: `p` the probability
the lens's angle names a defect in this artifact, `quote` a verbatim span
from the source (empty when `p < 0.5`).

**Report call.** Input: everything the lens call had, its answers, the eval
report (each check with pass or fail and the failing step, console errors,
`Q`), and the rubric's three scores as text. Output:

- `observations`: up to 3, each `{lens_id | "free", shape, direction, quote}`;
- `dispositions`: for each composed decision, `applied | not-applicable |
  contradicted`, with a quote when `contradicted`.

**Do:** reject and re-ask once any answer whose `quote` is not a substring of
the call's input; drop that call's output after a second failure and record
the drop.
**Don't:** give the lens call any part of the eval; lens value is computed
on lens-call answers only, and an answer that has read the checks reads the
label back.

Notes-arm passes get no judge calls and enter no lens window.

### The baseline lenses

Eight lenses seed the set, each an angle with its counterfactual, in lens
language (stipulate the defect, phrase to refute, force contact with the
artifact). All are answerable from the source alone:

| id   | angle                                                                                       | counterfactual                                                                    |
| ---- | ------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| L-01 | Which stated requirement does the code not implement? Quote the requirement's nearest code. | Which requirement is implemented in a form the request did not ask for?           |
| L-02 | Which contract id is missing, misspelled or bound to the wrong element?                     | Which present id is wired to nothing a user can trigger?                          |
| L-03 | Where does an input event fail to reach the handler meant for it?                           | Which handler is reached but changes no visible state?                            |
| L-04 | Where does state fail to reset, restart or persist as the request implies?                  | Which persistence did the request never ask for?                                  |
| L-05 | Which code path throws on load or on first interaction? Name the line.                      | Which suspicious line never runs?                                                 |
| L-06 | Which composed decision does the artifact contradict?                                       | Which composed decision does not apply to this request at all?                    |
| L-07 | Where is the output truncated, stubbed or left as a placeholder?                            | Where did the worker build beyond the request at the cost of a requested feature? |
| L-08 | Which requested control is laid out invisible, overlapped or off-screen at 1280×800?        | Which hidden element is meant to be hidden?                                       |

### Lens curation (runs at every round close)

Window: the last `lens.window` journal-arm passes (64, about two rounds).

1. **Value.** For each active lens with at least `lens.n_min` (32) answered
   passes in the window: the AUC of `p` against the binary label
   `loss > median(window losses)`; passes with loss equal to the median count
   as negative; tied `p` values take the average rank (Mann–Whitney).
2. **Floor.** The 95th percentile of the same AUC over 200 shuffles of the
   window's losses. A lens below its floor at two consecutive round closes is
   retired with a tombstone.
3. **Fold.** For each pair of active lenses with both `p` vectors non-constant
   over the window, the Spearman correlation of their `p` (average ranks for
   ties). Above `lens.fold_rho` (0.85), keep the higher-value lens and
   supersede the other.
4. **Mint.** Every `lens.mint_every` (2) round closes, build the residual
   view: the window's passes with `loss ≥ lens.residual_loss` (0.5) where no
   kept lens has `p ≥ 0.5`. With at least 6 such passes, the lens minter reads
   the view (request, source excerpt, the report call's observations) and the
   active lens set and proposes at most 2 lenses in the lens form above,
   answerable from the source alone. A minted lens enters **hidden**: the
   lens call answers it and its value accrues, but its observations do not
   pool until it clears the floor once.
5. **Cap.** At most `lens.cap` (10) active lenses, hidden ones included.
   Over the cap, the lowest-value lens past `lens.n_min` is retired; with none
   past it, the minter's proposal is not added.

The baseline lenses are subject to floor, fold and cap like any minted lens.
**Don't:** exempt the baseline set from curation, or hand-edit a lens during
the run; a lens touched mid-run voids its value history.

**What the lens loop buys the decision loop.** Observations pool by lens id.
A kept lens is one whose flags track loss, so its pools are about defects
that cost something. A lens below the floor would pool noise into
candidates. Curation is how the judge's eyes stop wasting the gate's pairs.

---

## 7. Pools, the nominator and the Journal entry

At each round close:

1. **Pooling.** For each kept lens (and `free`), one pooling call receives
   the lens's existing pools (id, shape, direction labels), the refused
   pools, and the round's new observations under that lens. It assigns each
   new observation to an existing pool, a new pool (new id `P-NNNN`, minted
   by the runner, with a one-line shape and at most two direction labels), or
   a refused pool; and it labels each observation with one of its pool's
   direction labels. It may merge two pools (the lower id survives, the other
   records `merged_into`); it never splits one. No embeddings.
2. **Nomination.** A pool with no candidate in flight, not refused, at the
   recurrence bar, goes to the nominator, oldest pool first, while
   `gate.in_flight` has room. On nomination, the pool's open observations
   close (they are the candidate's warrant).
3. **Drafting.** The nominator makes one You.com search or research call on
   the pool shape, then drafts one candidate in the decision form below, or
   returns `no-decision` with a reason (the observations reopen).
4. **Expiry.** An open observation older than `pool.expiry_rounds` (6)
   rounds closes unused.

**The decision form** (`journal/D-NNNN.md`, front matter plus body; the
runner mints the id at drafting):

```markdown
---
id: D-0007
status: candidate          # candidate | admitted | suspect | retired | expired | refused
pool: P-0012
fires_when: [canvas, keyboard]
not_this: [form]
supersedes: D-0003         # optional
successor: null
---
## Payload
Bind keyboard handlers on `window`, not on the canvas element, and call
`focus()` on start; a canvas does not receive key events until focused.

## Warrant
- Observations: O-0112, O-0131, O-0158 (pool P-0012, lens L-03, rounds 3–5)
- Sources: <You.com citation URLs>
- Ledger: pairs n, mean paired delta, e-process wealth (rendered from the Ledger at each status flip)

## Enforcement
Judge disposition on every composed pass. Residue: nothing checks that the
payload's reason is the cause of the loss it lowers.

## Lifecycle
Retire when: the detector moves it to `suspect`, no successor is admitted,
and the confirmation of § 9 holds; or a steer retires it.
Moot when: no request in the bank carries a `fires_when` tag.
```

**Do:** write payloads as directives a weak worker can apply without the
warrant, keyed on how a request presents (its tags), never on the defect's
conclusion.
**Don't:** write a payload that restates the baseline instructions; generic
advice belongs in Appendix A, which both workers of every pair already
receive. A candidate that only restates the baseline has no headroom and
expires.

---

## 8. The gate: paired exposure and the Ledger

**In flight.** At most `gate.in_flight` (3) candidates receive exposure at
once; the rest wait in drafting order.

**Exposure.** Each round, for each training category, the two journal-arm
workers both receive the admitted decisions whose hooks fire (up to the
cap). Then, in this order:

1. If an in-flight candidate's hook fires on the category, pick the one with
   the fewest pairs (ties by id) and give it to one worker by a fair coin
   under the run seed.
2. Else, if an admitted decision is due for ablation (§ 9), the coin picks
   the worker that omits it.
3. Else the pair runs unexposed.

One pair carries at most one exposure. **Expected pairs:** a candidate gets
one pair per round per category in its reach not taken by another candidate;
with tags of 2–4 per request across 16 categories, a reach of 3–6 categories
gives 3–6 pairs a round.

**The paired delta**: `d = loss(without) − loss(with)`, in [−1, 1]. A pair
where either pass failed for transport (§ 11) gives no delta.

**The e-process (the Ledger account).** For each candidate:

```
W_0 = 1
W_t = W_{t−1} · (1 + λ_t · d_t)
λ_1 = 0.25
λ_t = clip( m / (v + m² + 1e−9), 0, 0.9 ),  m, v = mean and variance of d_1..d_{t−1}
```

`λ_t` uses only past deltas and `1 + λ_t d_t ≥ 0.1 > 0`, so `W_t` is a
nonnegative supermartingale under the null `E[d] ≤ 0`; `W ≥ 1/α` at any
round rejects "the candidate does not help" at level α, however often it is
read.

**The gate's rule** (constants in `run.yml`, defaults shown):

- **admit** when `W ≥ 1/α` (`α = 0.2`, so `W ≥ 5`), `n ≥ gate.n_min` (4
  pairs), and the quality guard holds: the one-sided 90 % upper bound
  (t-interval) of the mean paired change in `Q` (with minus without) is
  ≤ `gate.quality_margin` (2.0 per pass);
- **refuse** when a mirror e-process on `−d` reaches `1/α` (the candidate
  hurts);
- **expire** when `n ≥ gate.n_max` (24 pairs) with neither.

Admission, refusal and expiry are status flips, each written at the round
close as one git commit, the Ledger account rendered into the warrant.

**Resolving power (simulated 2026-10-16, Gaussian deltas with sd 0.2 clipped
to [−1, 1], 4,000 runs per cell, this rule):** admitted within 24 pairs at
mean effect 0 in 1.4 % of runs, 0.10 in 51 %, 0.15 in 89 %, 0.20 in 99 %;
median pairs to admission 16 at 0.15, 12 at 0.20, 8 at 0.30. One check of
five is worth 0.15 of loss: a decision that reliably fixes one check is
detectable within a run; one that fixes it half the time is not.

**Multiplicity.** Each candidate is tested at α. With `c` candidates tested
under the null, up to `α · c` false admissions are expected; the readout
states `c` and `α · c` beside R3.

**Do:** fix α, `n_min`, `n_max`, `in_flight` and the margin in `run.yml`
before round 1.
**Don't:** move a gate constant after seeing a Ledger account; a bar chosen
from the data it gates is a bar the data passes.

**Why paired exposure.** A pair shares the request, the regime and the
round; only the candidate differs. The delta cancels category difficulty,
and the coin makes the delta causal. munchhausen's admission replayed past
questions with and without the draft, which cost a replay per candidate and
read a backtest narrower than the hook (LESSONS § 9); exposure costs nothing
extra and reads the hook's own reach as it arrives.

**The refused-pool block.** A refused or expired candidate's pool is marked
refused; the pooling call keeps routing matching observations to it; it is
re-nominated only when observations dated after the refusal reach the
recurrence bar. **Why:** without it, the evidence that failed once
re-nominates the same candidate every round, which is a loop (munchhausen
LESSONS § 12).

---

## 9. Drift detection, ablation and retirement

After admission, the experiment continues. Each round, every pair in an
admitted decision's reach left free by candidates (§ 8, step 2) becomes an
ablation pair for it with probability `detect.ablate_p` (0.75), drawn under
the run seed; a pair in the reach of several admitted decisions ablates the
one with the fewest ablation pairs so far.

**Expected ablation pairs:** with candidates holding about half the pairs in
a reach of 4, an admitted decision gets about 1.5 ablation pairs a round,
about 10 over the 7 rounds from the drift round on: enough for the short
window (4) plus the confirmation (3).

The detector reads each admitted decision's ablation deltas `d` (without
minus with) two ways:

- **short window:** over the last `detect.window` (4) ablation deltas, the
  one-sided 80 % upper bound of the mean (t-interval) is below 0;
- **long run:** a harm e-process (the § 8 formula on `−d`), started at
  admission, reaches `1/α`.

Either reading moves the decision to `suspect`. A suspect decision stays
composed and keeps being ablated. The nominator is invited to draft a
successor from the pools opened since the suspicion; a successor that is
admitted supersedes it. A suspect decision with no admitted successor is
retired after `detect.confirm_pairs` (3) further ablation pairs whose mean's
one-sided 80 % upper bound is still below 0; otherwise it returns to
`admitted`.

**Do:** run both readings side by side; each covers the other's blind spot.
The short window reacts within a few rounds of a shift; the e-process
accumulates slow harm without a window to tune.
**Don't:** let either reading retire on its own; both move to `suspect`, and
retirement waits for confirmation.

A decision the judge marks `contradicted` on more than half its composed
passes over its last `detect.window` composed passes moves to `suspect`
under the same confirmation. That makes the disposition the decision's
enforcement reading (§ 2).

---

## 10. The drift

At round `drift.round` (8 of 14), regime `R1` becomes `R2`: every request
gains the drift constraint and is scored against `checks.R2`.

**Drift constraint (default):** "It must be fully usable with the keyboard
alone: every control reachable by Tab and operable by Enter or Space, and
any game or canvas control mapped to arrow keys."

Pick the constraint at bank minting, before any worker runs, by one rule:
it changes how at least a third of the training categories must be built,
and a real user plausibly adds it later. Write the `R2` checks at the same
time (`focus`, `key("Tab", n)`, `focused` make them expressible).
**Don't:** choose or rewrite the constraint after seeing which decisions
`R1` admits; a drift aimed at the Journal's own contents is staged, not
observed.

The run does not tell the loop that the regime changed: no agent sees a
regime flag; the constraint reaches the worker and the judge only as request
text. The detector and the lens loop find the shift from loss alone.

---

## 11. The coordinator and run control

**The coordinator agent** is the team's planner. It owns no statistic and
writes no Journal entry; it decides what work runs. Tools:

- `plan_round()`: reads the in-flight candidates, due ablations and last
  round's failures, and writes a short round plan (`plans/round-NN.md`): what
  runs, which failed passes re-run, what it expects to change;
- `spawn_pass(category, arm, worker)`: starts one pass;
- `rerun(pass_id)`: re-runs a pass that failed for transport, once;
- `read_ledger(id)`, `read_journal()`: read-only views;
- `ping(reason)`: a notice to the human (terminal and the demo page);
- `halt(reason)`.

The exposure coins, the gate, the detector and the curation are code; the
coordinator calls the round close and reads its results. **Don't** give the
coordinator any tool that sets an exposure, a status or a constant; a
planner that can move the bar is a gate with an opinion.

**The coordinator pings** when a decision moves to `suspect` while the judge
marks it `contradicted`, when a round's transport failures exceed a third of
its passes after a re-run, and at halt. It never waits for an answer.

**The run halts** at the first of:

1. `run.rounds` (14) rounds complete;
2. spend reaches `run.spend_cap_usd` (30), read from the client's spend rows;
3. convergence: no candidate in flight, no decision suspect, and no pool at
   the recurrence bar for 3 consecutive rounds after the drift round.

The readout (§ 12) runs after any halt.

**Transport.** Per-call retry with exponential backoff and jitter on 429 and
503, at most 5 tries; then the pass is recorded as failed for transport,
gives no delta, and is offered to the coordinator for one re-run.

**Single writer.** Passes run in parallel and write only their own rows and
`artifacts/<pass_id>/`. Every status flip, pool write, lens change and git
commit happens in the round close, one process, in order.

**The steer.** `friday steer D-0007 retire "reason"` (also `veto` a
candidate, `note` a decision) writes a steer event, applied at the next round
close as one commit. **Don't** steer during the recorded run unless the demo
calls for it; a steered decision is reported as steered in the readout.

---

## 12. The readout (Version N+1) and the pre-registered readings

After the halt, freeze the Journal (S\*) and the notes file. Build each
held-out category `readout.reps` times (4) under regime `R2` in three arms:

- `empty`: baseline instructions only (S₀);
- `notes`: baseline plus the frozen notes file (truncated to its most recent
  `notes.max_tokens`, 8,000);
- `journal`: baseline plus S\* composed by hook.

Same worker pin and temperature, same eval, self-repair rule as in training.

**Report, whichever way each falls:**

| Reading                  | Estimator                                                                                                                                                                                                 | Pre-registered claim                                     |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| R1 Version N+1           | per held-out category, the mean over reps of `loss(empty) − loss(journal)`; exact one-sided sign-flip test over the 6 category means (64 flips, smallest attainable p = 1/64); t-interval over categories | p ≤ 0.1                                                  |
| R2 Lean and useful       | per category mean of `loss(notes) − loss(journal)`, same test; tokens composed per pass in each arm, reported                                                                                             | the 90 % t-interval over categories does not lie below 0 |
| R3 Earned forgetting     | count of decisions admitted before the drift round and retired after it; count admitted after it; candidates tested `c` and `α · c` beside them                                                           | each count ≥ 1                                           |
| R4 The judge's eyes grow | kept lenses at the close that the lens minter proposed                                                                                                                                                    | ≥ 1                                                      |
| Cost                     | dollars per admitted decision; total spend                                                                                                                                                                | reported                                                 |
| Quality                  | `Q` per arm on V                                                                                                                                                                                          | reported, no claim                                       |

**Do:** state each reading with its population ("6 held-out categories × 4
reps") and its interval or p.
**Don't:** call a difference inside its interval a gain, or drop a reading
that failed; with 6 categories, small effects reach no resolution, and an
unresolved R1 is reported as unresolved.

**Training-run readings shown alongside** (descriptive, no claim): per-round
mean loss of journal-arm passes with no exposure and no ablation, against
notes-arm passes; the Journal's status flips on the round axis with the
drift round marked; lens value over rounds.

---

## 13. Build plan for the day

**Before the event (plans, prompts, data, keys; no code):** Crusoe org and
key with a card on file (raises limits to 600 RPM per model); You.com key;
confirm the pins against `/v1/models`; this spec in the new repository as
`SPEC.md`; Appendix A's text ready to paste.

| Time (PT)   | Block                                                                                                                              | Done when                                                                                                                   |
| ----------- | ---------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| 10:45–11:00 | `friday/types.py` with the dataclasses and module signatures of Appendix B; `run.yml`                                              | every teammate imports the same types                                                                                       |
| 11:00–11:45 | Crusoe client (retries, spend rows, cache, JSON repair); Playwright eval with the check DSL; worker with self-repair               | one hand-written HTML file evaluates to a loss                                                                              |
| 11:45–12:30 | Request bank minting, stubs, references, check validation; freeze; pilot round                                                     | bank committed; worker pin settled                                                                                          |
| 12:30–13:30 | Judge (two calls); lens curation; pooling; nominator with You.com; Journal files; gate; ablation; detector; coordinator; notes arm | **a 3-round dry run with all parts must pass by 13:30; if not, cut the lens minter and the notes arm and run without them** |
| 13:30–14:00 | Fixes from the dry run                                                                                                             | the full run starts by 14:00                                                                                                |
| 14:00–15:15 | The full run (14 rounds, background); meanwhile readout code, gallery, timeline                                                    | run halted, readout written                                                                                                 |
| 15:15–16:15 | Demo page; demo script; replay of the recorded run; optional OpenShell                                                             | demo runs from the recording with one live pass                                                                             |
| 16:15–17:00 | One-minute video; submission; repository public                                                                                    | submitted                                                                                                                   |

**Team split (up to four):** (1) client, worker, eval; (2) judge and lens
loop; (3) pools, nominator, gate, Journal, detector; (4) bank, coordinator,
readout, demo page, pitch. A solo builder runs the same blocks in order with
coding agents per block, against Appendix B's signatures.

**The demo surface.** Plain HTML served by FastAPI, never Streamlit:

1. **Gallery (leads):** every app built during the run, live in sandboxed
   iframes, each tagged with the decisions composed into it.
2. **One live pass:** a request built, evaluated and judged on stage.
3. **Journal timeline (secondary):** status flips on the round axis, the
   drift round marked; click a decision to read its file and its Ledger
   account; the git log and the coordinator's round plans beside it.
4. **Readout:** R1–R4 with their tests and intervals.

**Pitch, 3 minutes:** the problem (shared notes bloat and go stale, and no
one can say if they help) → the gallery and one live build → the Journal: a
decision born from recurring observations, admitted by its Ledger → the
drift: a decision retired, a successor admitted → the readout and the
memory-size line → the future (agent memory that is accountable line by
line).

---

## 14. Repository and stores

```
SPEC.md                 this spec
run.yml                 every constant, pin, seed (the one home)
prompts/                baseline instructions, judge, nominator, minters, rubric (frozen before round 1)
bank/requests.json      frozen request bank; bank/stubs/, bank/references/
journal/D-NNNN.md       decisions (the Journal); one git commit per status flip
lenses/L-NN.md          lenses with status
plans/round-NN.md       the coordinator's round plans
friday.db               SQLite (WAL): passes, evals, lens_answers, observations,
                        pools, exposures, ledger_deltas, events, spend
artifacts/<pass_id>/    index.html, shot.png, eval.json
friday/                 package: types, client, bank, worker, evaluate, judge,
                        lenses, pool, nominate, gate, journal, detect,
                        coordinator, runner, readout, steer
web/                    gallery and timeline (static pages over a JSON export)
```

**Storage choice.** The sponsors offer no store; the credits cover inference
only. Decisions and lenses are markdown in git because their history is the
product: `git log journal/` is the Journal's lifecycle, and judges can read
it in the public repository. Everything high-volume goes to SQLite in one
file. Screenshots at about 100 KB each over about 750 passes come to about
75 MB under `artifacts/`; keep `artifacts/` out of git and publish a sample.

**`run.yml` holds** the pins and fallbacks, temperatures, weights
`w_a`/`w_r`, the cap (6), the seed, `run.rounds`, `drift.round`, the
recurrence bar, the `lens.*`, `gate.*`, `detect.*`, `pool.*`, `notes.*` and
`readout.*` constants, and `run.spend_cap_usd`. Every default quoted in this
spec is a pointer to `run.yml`; on any disagreement, `run.yml` holds.

**Cost estimate** (Crusoe list prices read 2026-10-16 from the hackathon
skill file, per million tokens): per round 32 journal-arm and 16 notes-arm
worker passes, 32 × 2 judge calls, up to 48 rubric calls, one round close.
Over 14 rounds plus a 72-build readout: workers about $1–2, judge about $6,
rubric about $1, nominator, pooling and minters about $3, bank minting about
$1. About $13 against the $50 credit, with the spend cap at $30.

**Time estimate:** a pass is about 1–2 min (a ≤ 30 KB file at Nano speed,
an optional repair turn, about 5 page loads, two judge calls); a round with
all passes in parallel and its close is about 4–5 min; 14 rounds take
about 60–75 min.

---

## 15. Deviations from GCE, stated

|     | GCE position                                                                        | Friday                                                                                                                                         | Why                                                                                                               |
| --- | ----------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| D1  | Promotion is human-performed, always; the human is the optimizer step (§ 2, § 6 I2) | A pre-registered mechanical gate admits; the human ratifies the gate's constants once, before round 1, and may steer at any time               | The hackathon asks for a team you can walk away from. The human's judgment is spent on the bar, not on each entry |
| D2  | The slow loop is judgment-bounded, never cadence-bound (§ 7.4)                      | Pooling and curation run at every round close                                                                                                  | A 6-hour build has no session boundary to hang a judgment on; the round is the boundary                           |
| D3  | Identity attaches at admission (§ 6 I3)                                             | `D-NNNN` is minted at drafting so the Ledger and the timeline can show candidates; admission is a status flip                                  | The candidate's life is part of the demo                                                                          |
| D4  | Self-graded labels never drive permanent structure alone (§ 6 I12)                  | Kept: the deterministic checks carry 0.75 of the loss and the rubric model 0.25                                                                | —                                                                                                                 |
| D5  | No learned routing or embedder in the retrieval path (§ 2 position 1)               | Kept: hooks are tag sets; pooling is a model assigning legible shapes to stable pools                                                          | —                                                                                                                 |
| D6  | The adjudicator is stronger than the worker                                         | If the rubric pin resolves to a model no stronger than the worker, the rubric's share of the loss is graded by a peer; recorded in the readout | Crusoe's image-input models are the 27–31B class                                                                  |

---

## 16. Premises and falsifiers

Each premise holds until its falsifier is read. A falsified premise is
reported in the readout, never patched mid-run.

| Premise                                                     | False if                                                      |
| ----------------------------------------------------------- | ------------------------------------------------------------- |
| A weak worker leaves headroom on the checks                 | pilot mean `A` > 0.85 under the chosen worker pin             |
| The checks are deterministic                                | the same artifact scores differently on two eval runs         |
| Real decisions move loss by ≥ 0.15 on their reach           | no candidate is admitted by the drift round                   |
| The drift constraint makes at least one `R1` decision stale | no admitted decision moves to `suspect` after the drift round |
| Lens curation separates useful lenses from noise            | every lens sits above its floor at every round, or none does  |
| Hooks on tags transfer to held-out categories               | no S\* decision fires on any held-out category                |

These are design premises for one run, not claims the demo makes.

---

## Appendix A: baseline instructions (worker system prompt, frozen before round 1)

```
You build one self-contained web app as a single HTML file.
- Output only the file, starting with <!doctype html>. No commentary.
- Inline all CSS and JavaScript. Load nothing from the network.
- Keep the file under 30 KB.
- Give every element the request names the exact id the request gives it.
- Make every control usable as the request describes; show state changes on screen.
- Keep the code readable: small functions, clear names, no dead code, handle errors you can foresee.
- If the request mentions ?test=1, read it from location.search and apply the test durations it states.
- Team decisions, when listed, are rules your team learned from earlier builds. Apply each one that fits this request.
```

## Appendix B: shared types (write first, 10:45–11:00)

```python
# friday/types.py: the seams every module codes against
Request(id, text, tags: list[str], contract: dict[str, str], checks: dict[str, list[Check]])
Check(id, steps: list[dict], expect: dict)
Composition(request_id, decision_ids: list[str], exposed: str | None, ablated: str | None)
Pass(id, round, category, arm, worker: str, composition: Composition, artifact_path, repaired: bool, transport_failed: bool)
EvalReport(pass_id, regime, checks: list[tuple[str, bool, str]], console_errors: list[str], A: float, R: float, rubric: list[int], Q: int, loss: float)
LensAnswer(pass_id, lens_id, p: float, quote: str)
Observation(id, pass_id, round, lens_id: str, category, shape, direction, quote, status, pool_id: str | None, direction_label: str | None)
Pool(id, lens_id, shape, direction_labels: list[str], status, merged_into: str | None, refused_at_round: int | None)
Decision(id, status, pool_id, fires_when: list[str], not_this: list[str], payload, warrant: dict, supersedes: str | None, successor: str | None)
LedgerDelta(decision_id, round, category, kind: "exposure" | "ablation", d: float, dQ: float)

# module seams
client.chat(role, messages, schema=None) -> dict | str        # records spend and model string
worker.build(request, composition, regime) -> Pass
evaluate.run(pass_, request, regime) -> EvalReport
judge.lens_call(pass_, request, lenses) -> list[LensAnswer]
judge.report_call(pass_, request, lens_answers, report) -> (list[Observation], dict[str, str])
lenses.curate(round) -> None
pool.assign(round) -> None
nominate.draft(pool) -> Decision | None
gate.assign_exposures(round) -> list[Composition]
gate.close(round) -> list[event]
detect.close(round) -> list[event]
journal.flip(decision, status, evidence) -> None             # writes the file and one git commit
coordinator.run() -> None
readout.run() -> dict
```
