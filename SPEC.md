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
worker agents that ship working single-file web apps, each with a how-to
that declares what it does and how to operate it. A separate judge reads
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

## 2. Language

Friday's text is part of its machinery. Prompts steer every model, payloads
are the memory, lenses are the judge's eyes, status-flip commits are the
Journal's public history, and the readout is the claim the hackathon judges
score. This section sets the rules for the words on each of those surfaces.
It adapts type-gce's `corpus/LANGUAGE.md` (laws L1–L3, claim language, lens
language) to one run and one demo. Rules that reach a prompt freeze with the
prompts before round 1 (§ 5). The rest are corrected in place, and git holds
the amendments.

### What it blocks

Four failures, none of which throws an error in a one-day build:

1. **Term drift.** Code, prompts and the demo give one concept three names,
   until the pooling call, the judge and the timeline disagree about what a
   pass is. Countermeasure: the vocabulary, and one home per fact.
2. **Leakage.** A word in a prompt hands the worker or the lens call the
   label (a check, the loss, the regime, the arm). This voids a pair or a
   lens value, and nothing reports it. Countermeasure: blinding.
3. **Unusable memory.** A payload a weak worker cannot act on with only the
   payload in front of it: hedged, generic, keyed on its own conclusion, or
   citing ids the worker cannot look up. It uses up gate pairs and expires.
   Countermeasure: payload language.
4. **Claim drift.** A pitch line, readout row or commit message says more
   than the reading behind it, and Technicality (40 %) is scored from those
   words. Countermeasure: claim language.

### Vocabulary

One term per concept; no synonyms in code, prompts or prose. A concept with no
term is added here first, then used. "GCE" cites `gce/GCE-DOCTRINE-V2.md` (in
type-gce) by section.

| Term                      | Means in Friday                                                                                                                                                                                                            | Anchor                                          |
| ------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| **request**               | A one-line build demand in a user's words, plus its interface contract, tags and acceptance checks. Minted into the goal stream before round 1 and frozen; tagged when its round starts | hackathon: the unit of work                     |
| **category**              | One request's identity. A training category is built in one round only, by its pair; a held-out category is built `readout.reps` times at the close | § 4                                             |
| **goal stream**           | The frozen, ordered list of training requests, `stream.per_round` per round, drawn under the run seed from the regime's goal mix; minted once before round 1 together with the held-out set | § 4                                             |
| **goal family**           | A stratum the goal minter draws from (e.g. `canvas game`, `data tool`). Seen only by the goal minter and the runner; never by an agent of the loop, never a tag | § 4                                             |
| **goal mix**              | The share of each goal family in one regime's draws                                                                                                                                                                    | §§ 4, 10                                        |
| **tagger**                | The strong-model step that tags each round's requests at round start, reusing the tag vocabulary and minting a new tag only under the minting bar of § 4                                                           | § 4                                             |
| **tag vocabulary**        | The tags minted so far. Open: it grows by the tagger's minting during the run and freezes at the close, before the held-out set is tagged                                                                          | § 4                                             |
| **tag**                   | A trait word attached to a request by the tagger (e.g. `canvas`, `timer`, `keyboard`, `persistence`). Hooks match on tags. No tag set is given: the vocabulary is discovered (tag vocabulary)                        | GCE § 9 (legible symbolic keys), § 2 position 1 |
| **interface contract**    | The element ids a request requires (e.g. `#start`, `#score`) and its test hooks (e.g. `?test=1` shortens durations), stated in the request so acceptance checks are deterministic                                          | § 4                                             |
| **acceptance check**      | One declarative browser step list with an expectation, run by the eval. Each request carries `stream.checks_per_regime` (2–3) per regime, minted with the request                                                  | § 4                                             |
| **contract stub**         | An HTML page holding every contract id and no script. A valid acceptance check fails on it                                                                                                                         | § 4                                             |
| **probe suite**           | The fixed, request-independent part of the eval: the page loads, logs no console error, and each control it exposes changes visible state when operated (§ 5)                                                     | § 5                                             |
| **how-to**                | The section every artifact ships: a visible `#how-to` text and a JSON copy (`#how-to-data`) declaring what was built and, per control, its action and its visible effect (Appendix A)                               | § 5                                             |
| **how-to check**          | The eval's reading of the how-to: each declared control's action run and its declared effect expected, as acceptance checks; plus whether every contract id is declared                                            | § 5                                             |
| **silent control**        | A control the probe suite found working that the how-to does not declare. A reading, never part of the loss                                                                                                       | § 5                                             |
| **regime**                | The world's current conditions: a goal mix and a set of constraints. `R1` before the drift round, `R2` from it on                                                                                                  | § 10                                            |
| **drift constraint**      | One sentence appended to every request from the drift round on, with its own acceptance checks and a second viewport for the probe suite and the rubric                                                            | § 10                                            |
| **pass**                  | One worker building one request once (with at most one self-repair turn), then eval, then judge. The row of the run                                                                                                        | GCE § 7.6 (fast loop)                           |
| **round**                 | Every request the goal stream holds for that round, built by its two paired workers, plus the notes arm's pass per request; then the round close. One accrual step                                                  | § 8                                             |
| **round close**           | The single-writer step after a round's passes: lens curation, pooling, nomination, gate, detector, status flips, git commits. Tagging the next round's requests follows it                                         | § 11                                            |
| **arm**                   | One policy of memory use. `journal` (Friday), `notes` (the shared-notes baseline), `empty` (no memory, read only at the close)                                                                                             | § 12                                            |
| **coordinator**           | The agent that runs the team: plans each round, spawns passes, decides re-runs, halts, pings a human. It never computes a statistic or writes the Journal                                                                  | § 11; GCE § 14.2                                |
| **worker**                | The weak model that turns a composed prompt into one HTML file, with one self-repair turn. Keeps no memory of its own                                                                                                      | GCE § 14.2 (the implementer keeps no ledger)    |
| **self-repair turn**      | One extra worker turn given its own artifact's console errors and missing contract ids, never the acceptance checks                                                                                                        | § 5                                             |
| **baseline instructions** | The fixed worker system prompt both arms of every pair receive (Appendix A)                                                                                                                                                | § 5                                             |
| **composition**           | The worker's prompt: the baseline instructions, the admitted decisions whose hooks match (up to the cap), the candidate under test if exposed, the request                                                                 | GCE § 4.2                                       |
| **cap**                   | The most admitted decisions composed into one prompt. Over the cap, the ones with the lowest e-value are left out. A candidate under test is composed in addition to the cap                                               | GCE § 6 I8                                      |
| **eval**                  | The fixed scorer. Never learns, never changes during a run. Runs the probe suite, the how-to check and the acceptance checks in a headless browser, and asks the rubric model one frozen question set over screenshots | GCE § 7.1 (the world), § 6 I12                  |
| **loss**                  | The scalar the gate reads: `1 − (w_a · A + w_r · R)`, A the weighted mean of the probe, how-to and acceptance-check fractions (§ 5), R the rubric score on [0, 1]                                                   | § 5                                             |
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
| **disposition**           | The judge's verdict on each composed decision for one pass: `applied`, `not-applicable`, `contradicted`; `undisposed` when the report call was dropped, never defaulted | GCE § 7.6 (use-time dispositions)               |
| **pool**                  | A stable group `P-NNNN` of observations under one lens id sharing one shape, with at most two direction labels                                                                                                             | § 7; GCE § 7.4 (piles grouped by shape)         |
| **direction label**       | A short phrase naming what would lower loss, assigned by the pooling call, one of at most two per pool                                                                                                                     | § 7                                             |
| **recurrence bar**        | When a pool may be nominated: at least 3 open observations, all under one direction label, from at least `pool.recurrence_min_requests` (2) distinct requests and at least 2 rounds | GCE § 7.4 (counts nominate)                     |
| **fast-track**            | A pool whose open observations come from at least `pool.fast_track_min_requests` (3) distinct requests and whose leading direction label holds at least `pool.fast_track_share` (2/3) of them. It is nominated without the two-round requirement, ahead of the queue. It changes when a candidate is tested, never the bar it must clear | § 7                                             |
| **nominator**             | The strong model that drafts a candidate decision from one pool, grounded by one You.com call                                                                                                                              | GCE § 6 I2 (the machine proposes)               |
| **decision**              | One Journal entry `D-NNNN`: hook, payload, warrant, enforcement, lifecycle. Its id is minted at drafting; its text is frozen from drafting; only its status flips                                                          | GCE § 6 (the entry contract)                    |
| **hook**                  | A decision's activation: it fires on a request iff the request carries at least one `fires_when` tag and no `not_this` tag                                                                                                 | GCE § 9                                         |
| **reach**                 | The requests a decision's hook fires on: in a round, the training requests; at the close, the held-out set                                                                                                                 | § 8                                             |
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
| **run spec**              | `run.yml`: every constant, pin and seed, committed before round 1. The critic run reads `run-critic.yml`, which overrides named keys only | § 14                                            |
| **critic**                | In the critic run only: a strong model that reads a finished artifact, its how-to and its screenshot, and returns criticism and at most `critic.max_requests` feature requests for one revision turn               | § 17                                            |
| **feature request**       | One critic demand, phrased as a user would. It joins the request text for the revision turn, and the how-to must declare it                                                                                        | § 17                                            |
| **critic run**            | A second, optional run in which every pass is build → critic → revision. Its own Journal and Ledger; same eval and loss                                                                                           | § 17                                            |

### Surfaces: who writes, who reads

Each surface is written for its reader, and the reader holds only what is
listed here. The rule column names the subsection that governs it.

| Surface                                           | Writer                               | Reader                                     | Reader holds                                          | Rules                       |
| ------------------------------------------------- | ------------------------------------ | ------------------------------------------ | ----------------------------------------------------- | --------------------------- |
| baseline instructions, `prompts/`                 | a human, before round 1              | each model                                 | its own input, nothing of the run                     | blinding, L3                |
| payload                                           | nominator                            | weak worker                                | the request and the composition; never the warrant    | payload language            |
| lens (angle and counterfactual)                   | a human (baseline), the lens minter  | judge, lens call                           | the request, composed payloads, the artifact source   | lens language, blinding     |
| observation (shape, direction, quote)             | judge, report call                   | pooling call, nominator                    | the shapes already pooled under one lens              | observation language        |
| pool shape, direction label                       | pooling call                         | the next pooling calls, nominator, timeline | the labels already minted                             | observation language        |
| warrant figures, status-flip commit, timeline row | the runner (rendered)                | a hackathon judge reading `git log`        | the public repository, never the run                  | rendered text               |
| round plan, ping                                  | coordinator                          | the human, the demo                        | the plan and the round's results                      | claim language              |
| how-to                                            | worker                               | the how-to check, the judge, the gallery   | the artifact                                          | Appendix A                  |
| tags                                              | tagger                               | hooks, the nominator, the timeline         | the tag vocabulary and the round's request text       | observation language        |
| criticism, feature requests (critic run)          | critic                               | the worker's revision turn                 | the request, the artifact, its how-to, a screenshot   | payload language            |
| readout, demo page, pitch, README                 | humans                               | hackathon judges                           | three minutes, no spec                                | claim language, demo words  |
| code prose, test titles                           | builders and their coding agents     | the next teammate                          | the code                                              | code prose                  |

### Priority under the clock

**Do:** apply the rules strictly in tier order. **Tier 1** covers what freezes
before round 1: `prompts/`, the baseline lenses, the payload and lens rules
the nominator and minter prompts carry, and the blinding list. Read it in the
12:15–13:15 block, before the goal stream freezes (§ 13); a defect there costs the
whole run. **Tier 2** covers the public surfaces (readout, demo page, pitch,
README, commit templates), read in the 15:30–16:15 block. **Tier 3** is code
comments and scratch files, done when time allows.
**Don't:** spend build time polishing tier 3. And don't skip the read of a
tier-1 surface because the clock is short: reading a prompt takes minutes,
and a leak voids the run.

### The three laws, narrowed

**L1: write for the reader who holds only its input.**
**Do:** write each model-facing text to be true and actionable for a model
that reads one prompt and nothing else of the run. A payload never says "as
seen in earlier builds" or "per O-0112". A lens never says "as the checks
show". Journal files and commit messages are read by someone in a public
repository who never saw the run, so they state what stands and the evidence
for it.
**Don't:** strip evidence to make text timeless. The warrant keeps its
observation ids, its You.com citations and its Ledger figures. They are
there for the human reader; the worker never sees the warrant.

**L2: one home per fact; the figure comes from its home.**
**Do:** state each fact once, in the home whose lifecycle matches it:
`run.yml` for constants, pins and seeds; the Ledger (`ledger_deltas` in
`friday.db`) for every e-process wealth, pair count and mean delta;
`artifacts/<pass_id>/eval.json` for a pass's loss; a decision's front matter
for its status; `lenses/L-NN.md` for a lens's status. Every other surface
points to the home or is rendered from it (§ 14 already makes this spec's
defaults pointers to `run.yml`).
**Don't:** retype a figure into a slide, a commit or the README from memory.
Render it, or copy it digit for digit from the JSON export. And don't build a
check for a premise no code can run: the design premises of § 16 stay
premises with falsifiers.

**L3: every directive ships its overshoot.**
**Do:** pair each directive in this spec and in `prompts/` with the opposite
error it must not be pushed into (the spec's **Do / Don't** form). For a lens
the counterfactual is the pair.
**Don't:** pad a payload with its own Don't. The payload's overshoot belongs
in its hook's `not_this`, which costs the worker no words. And don't write a
vacuous pair ("don't overdo it"): a counter-directive names a failure that a
reviewer can point to in a draft.

### Blinding

The run is valid only while certain readers never see certain things
(§§ 6, 8, 10), and words are how they leak. The blinding list covers the
**template text** of each prompt: the instructions, the headings and the
scaffolding the runner wraps around inputs.

| Reader                 | No prompt template it receives names                                                                                                          | Why                                                                                    |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| worker                 | checks or check ids, loss, score (as a grade), eval, judge, regime, `R1`/`R2`, drift, candidate, exposure, ablation, pair, arm, test (of itself), probe, verify (of the how-to), goal family | the two workers of a pair may differ only by the exposure; a worker told it is graded, or that its how-to is verified, is a different worker |
| judge, lens call       | the eval report, the rubric, checks, loss, regime, drift, pass or fail                                                                        | lens value measures foresight from the artifact (§ 6)                                  |
| judge, report call     | regime, drift                                                                                                                                 | the detector and the lens loop find the shift from loss alone (§ 10)                   |
| nominator, lens minter | regime, drift, the arm, goal family                                                                                                           | a payload or lens keyed on the regime is aimed at the run, not at the work             |
| tagger                 | regime, drift, goal family, the arm, loss                                                                                                     | a tag that names the regime or a family hands every hook the label                     |
| critic (critic run)    | checks, loss, eval, judge, regime, drift, candidate, exposure, pair, arm                                                                      | the critic shapes the artifact; it must not aim at the scorer                          |

**Do:** render a candidate under test in the composition exactly as an
admitted decision is rendered: the same heading ("Team decisions",
Appendix A), the same form, no status. The drift constraint reaches every
reader only as request text and is never labelled as a change.
**Don't:** apply the list to request text or artifact source. `#score` is a
contract id and "score" is a word in game requests. The list reads only the
templates the build writes.

### Payload language

The reader is the weak worker, which holds the request and the composition
and nothing of the pool, the warrant or the Ledger.

**Do:** write one imperative directive, with at most one reason clause,
within the 60-word bound (§ 2, payload). Name the mechanism: the API, event,
element or order of operations. A worker that never saw the defect must be
able to apply it. Make it apply to every request the hook fires on, and
state any condition the tags cannot express as the first clause ("When the
app draws on a canvas, …").
**Don't:** hedge ("consider", "try to", "where possible"). A weak worker
skips a hedge, and the gate then measures a payload nobody applied. Don't
restate Appendix A: a payload with no headroom expires (§ 7). Don't cite
anything the worker cannot look up: `O-`, `P-`, `D-` or `L-` ids, "earlier
builds", the judge, a check. And don't overshoot the mechanism rule into an
implementation. One call or one line of code fits in 60 words; a code block
does not.

| Draft                                                          | Fails on                                    | Payload                                                                                              |
| -------------------------------------------------------------- | ------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| "Consider making keyboard controls work better."               | hedge; names no mechanism                   | "Bind `keydown` on `window`, not on the canvas, and call `canvas.focus()` when the game starts."     |
| "As observed in O-0112, timers drift."                         | an id the worker cannot look up; states the conclusion | "Drive countdowns from `Date.now()` differences, never from counting `setInterval` ticks."          |
| "Make sure every id from the request is present."              | restates Appendix A                         | none: the pool earns no decision                                                                     |
| "In the pomodoro app, put `#start` above the fold."            | names one category and one contract id      | "Place every requested control within the first 800 px of height; put explanatory text below them." |

### Lens language

The reader is the judge's lens call, which holds the request, the composed
payloads and the artifact source, and no part of the eval.

**Do:** ship each lens as one angle and its counterfactual. Stipulate the
defect ("Which requirement does the code not implement?", never "Does the
code implement every requirement?"). A question asked as a possibility is
answered "no" by default. Phrase it so the answer has to quote the source.
Keep to one angle per lens: observations pool by lens id, so two angles in
one lens mix two pools.
**Don't:** write a vacuous counterfactual ("…or is it fine?"). Don't hand
the lens its answer ("Confirm the timer uses `Date.now()`"): that is a
payload dressed as a lens. Don't name the eval, a check or the loss
(blinding). The lens minter's prompt carries this subsection verbatim,
copied in before round 1.

### Observation and pool language

**Do:** write an observation's **shape** as the defect presents in source,
in terms general across requests that share the trait: "keyboard handler
bound to an element that never takes focus", not "the snake game ignores
arrows". Write its **direction** as one act that would have lowered loss, in
payload words ("bind handlers on `window`"). Its **quote** is verbatim
(enforced, § 6). Pool shapes are short noun phrases and direction labels
short imperative phrases. Once a label is minted, the pooling call reuses it.
**Don't:** let labels paraphrase each other. Two labels for one act split a
pool's count and starve the recurrence bar. A shape naming one category
drafts a payload whose reach is one category.

### Rendered text: warrant, commit, timeline

**Do:** have the runner render every figure in a warrant, a status-flip
commit message and a timeline row from the Ledger and `run.yml`, one
template per flip. For example (figures illustrative):

```
D-0007 candidate → admitted: W 4.1 ≥ 3.33, n 9 pairs, mean d 0.17, Q change upper bound 0.8 ≤ 2.0
D-0004 suspect → retired: 3 confirmation pairs, mean d −0.12 (80 % upper bound −0.03); successor D-0011
L-09 minted lens clears its floor: AUC 0.71 over 48 passes, floor 0.64
```

**Don't:** hand-write a status-flip commit, or add adjectives to one
("strong", "clearly"). The figure carries the strength.

### Claim language: readout, demo, pitch

The reader is a hackathon judge with three minutes and no spec.

**Do:** tie each claim to its reading (R1–R4, § 12) and its population
("over 6 held-out categories × 4 reps"). State each effect with its interval
or p; a difference inside its interval is **unresolved**. Show every
pre-registered reading, the failed ones included, because a judge reads a
missing reading as a success. Count with one estimator per figure across
slides, the readout and the README.
**Don't:** hedge out of range ("seems to learn"). Hedged words cannot be
false, so they mislead as surely as an overclaim does. Narrow the
population and keep the verb.

| Pitch word                                   | Earned by                                                  | When unearned, say                                                     |
| -------------------------------------------- | ---------------------------------------------------------- | ---------------------------------------------------------------------- |
| "improves", "Version N+1"                    | R1 at p ≤ 0.1                                              | the mean difference and its p, called unresolved at 6 categories       |
| "leaner than shared notes"                   | R2's interval not below 0, beside tokens composed per pass | the interval and the token ratio as read                               |
| "forgets what went stale"                    | R3's counts ≥ 1, beside `c` and `α · c`                    | how many decisions the detector moved to `suspect`, and that none retired |
| "the judge learns what to look at"           | R4 ≥ 1                                                     | that no minted lens cleared its floor                                  |
| "you can walk away from it"                  | the pings and steers counted in the recorded run           | the counts                                                             |
| "no human labels"                            | true by construction: the eval is the label (§ 3)          | —                                                                      |

"Self-improving" in the title names the project. The claim slide uses the
readings.

### Demo words

**Do:** on the demo page and in the pitch, use the vocabulary's terms and
gloss each in plain words the first time it appears ("a pass: one worker
building one app once").
**Don't:** coin a friendlier synonym for the UI ("lesson", "memory item",
"rule"). The audience then cannot match the timeline to the git log.
"Team decisions" (Appendix A) is the one worker-facing phrase, and it keeps
the term.

### Code prose

**Do:** name identifiers with the vocabulary's terms (`Pass`, `Pool`,
`Decision`, `LedgerDelta`), never `Job`, `Memory` or `Rule` for these. A
docstring says what its function returns or writes. A test title names the
property its assertions check.
**Don't:** trace the body in a comment, or hold a commit for comment polish
(tier 3).

### Checks

Cheap checks fit for a hackathon, each reading presence or shape, never
quality:

- **quote substring:** in place (§ 6).
- **blinding:** a test renders each role's prompt template for a sample pass
  and greps it for that role's list. It reads templates only, never request
  text or artifacts.
- **candidate indistinguishable:** a test renders one composition with a
  decision as a candidate and one with it admitted, and asserts the worker's
  prompt text is equal.
- **payload at drafting:** over 60 words, a blinding word, or an `O-`, `P-`,
  `D-` or `L-` id gets one re-ask, then `no-decision`. These are bound, leak
  and reference failures. A hedge word is recorded on the decision as a
  reading and never refused, because form is not what admission judges.
- **rendered commits:** by construction, since `journal.flip` writes the
  commit from the Ledger.
- **how-to in Appendix A:** the blinding test reads Appendix A's how-to lines
  like any template: they ask for the how-to and never say it is verified.

None of these checks whether a payload is right, a lens is useful or a pitch
line overclaims. That stays review judgment: tier 1 at the 14:15 dry run,
tier 2 in the 15:30 demo block.

---

## 3. The system at a glance

```
                     COORDINATOR (agent): plans the round, spawns passes, re-runs, halts, pings
                          │
            ┌─────────────┴──── goal stream (frozen) ─────────────┐
            │  10 requests per round × 2 paired workers, TAGGER  │  6 held-out (V, read once)
            ▼                                                    │
  composition ──► WORKER (weak) ─(1 self-repair)─► index.html + how-to ──► EVAL (fixed) ──► loss, quality
                                                     probe suite · how-to check · acceptance checks · rubric
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

## 4. The goal stream and the held-out set

**Do:** mint every request of the run once, before round 1, with the goal
minter (the nominator's model): the goal stream (`stream.per_round` × `run.rounds`
training requests, 140 at the defaults) and the held-out set (6). Write each as a
plain request a non-technical person would type ("a pomodoro timer that plays a
sound and counts completed sessions"). Freeze both in `goals/stream.json` and
`goals/held_out.json` and commit them before the first pass.
**Don't:** tune a request to be hard, easy or flattering to the loop after
seeing any worker output; a request touched after round 1 voids every pair that
built it.

**Goal families and mixes.** The goal minter draws each request from a goal
family (`stream.families`: canvas games, data tools, and the rest listed in
`run.yml`) under the run seed, in the proportions of the regime's goal mix:
`stream.mix.R1` for rounds before the drift round, `stream.mix.R2` from it on
and for the held-out set. Families stratify the draw and nothing else.
**Don't:** let a family name reach any agent of the loop. It is not a tag, and
a hook keyed on it would be keyed on the regime.

Each request carries:

- `id`, `text` (one line), `round` (its stream position; `null` when held out),
- `contract`: the required element ids, each with its role in one phrase; for
  any request with a duration, a `?test=1` query that scales every duration to
  ≤ 2 s, stated in the request text the worker reads,
- `checks.R1` and `checks.R2`: `stream.checks_per_regime` (2–3) acceptance
  checks each, in the check DSL, `R2` written against the request plus the drift
  constraint. A training request carries only its own round's regime; a
  held-out request carries `R2`.

**Check validation.** Every acceptance check must fail on the request's
contract stub. A check that passes on the stub is regenerated once, then
dropped. A request left with no check in a regime it needs is regenerated
whole. No reference app is built: the probe suite and the how-to check carry
the deterministic share of the loss, and a check no pass ever satisfies is
reported, never silently kept as signal (§ 16).

**Tags.** No tag set is given. At each round's start, before composition, the
tagger reads that round's request texts and the tag vocabulary so far, and
gives each request 2–4 tags. It reuses an existing tag whenever one fits; it
mints a new tag only when no existing tag names the trait, with a one-line gloss
stored beside it, at most `stream.max_new_tags` per round. Tags are frozen on a
request once given. At the close the vocabulary freezes, and the held-out set is
tagged with it, minting nothing.
**Why:** hooks fire on tags, so the tag vocabulary and the hooks that use it
become the team's discovered taxonomy of goal types. A held-out request whose
tags training never minted can receive no decision; the readout reports how
many held-out requests receive at least one (§ 16).
**Don't:** let the tagger rename or merge tags mid-run. Two tags for one trait
split a hook's reach, and a renamed tag silently changes the reach of every
decision keyed on it.

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
- Each check runs on a fresh page load at the regime's viewports. A check that
  fails to load or times out fails. `no_console_errors` counts as one check at
  most per request.
- The how-to's declared actions and effects are written in the same DSL
  (Appendix A), so the how-to check runs them unchanged.

---

## 5. Roles and models

The worker is weak on purpose: a strong worker saturates the checks and leaves
no room for memory to show. The judge and the nominator are strong. The eval
is deterministic where it can be and pinned where it cannot.

| Role                                              | Default pin (Crusoe model id)                                                                                                                                                        | Fallback                                        | Temperature  | Input                     | Output          |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------- | ------------ | ------------------------- | --------------- |
| worker                                                         | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`                                                                                                                                                                       | `openai/gpt-oss-120b`                           | 0.7, no seed | composition               | one HTML file with its how-to |
| eval rubric                                                    | the first image-input model `/v1/models` lists among `nvidia/NVIDIA-Nemotron-3.5-Super-VL`, `google/gemma-4-31b-it`, `zai-org/GLM-5.3-Flash`, `Qwen/Qwen3.8-27B` (in the order written here) | `nvidia/Nemotron-3-Nano-Omni-Reasoning-30B-A3B` | 0            | screenshots + request text | 3 scores on 1–5 per viewport |
| judge                                                          | `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B`                                                                                                                                                                    | `deepseek-ai/DeepSeek-V4-Pro`                   | 0            | text only (§ 6)           | JSON            |
| nominator, lens minter, goal minter, tagger, pooling call     | `zai-org/GLM-5.3` (Nemotron 3 Ultra is not served; a model other than the judge's keeps the drafting roles independent of it)                                                       | `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B`      | 0.3          | pool, residual view, round's requests | JSON |
| coordinator                                                    | `openai/gpt-oss-120b`                                                                                                                                                                                          | `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B`      | 0.2          | round state               | tool calls      |
| critic (critic run only)                                       | `deepseek-ai/DeepSeek-V4-Pro`                                                                                                                                                                                  | `deepseek-ai/Deepseek-V4-Flash`                 | 0.3          | request, artifact, how-to, screenshot | JSON |

Pins live in `run.yml`. At start-up the runner reads `GET /v1/models`; a
pinned id that is absent is replaced by its fallback, and the replacement is
recorded. The runner also records each model string the service echoes. A
drafting-role call taking more than `models.slow_first_call_s` (60 s) on the
first goal-minting call moves the drafting roles to their fallback for the
whole run. That fallback is the judge's model; if it is taken, the readout
records it beside D6 (§ 15).

**The worker pin rule (the one data-informed choice, fixed in advance).** The
pilot (§ 13) builds round 1's requests once with the default worker, outside
the run. If fewer than `pilot.min_share_passing` (0.3) of its artifacts pass at
least one acceptance check, switch the worker to the fallback and re-pilot; if the pilot's mean `A` exceeds 0.85,
the worker leaves too little headroom: record it as a falsified premise
(§ 16) and keep it. Nothing else reads the pilot.
**Don't:** re-pick the worker after round 1, or pick any other constant from
pilot output.

**Worker output contract.** One HTML file, inline CSS and JS, no external
URL (the eval aborts every network route), ≤ 30 KB, containing every
contract id and the how-to (Appendix A). The worker receives the baseline
instructions, frozen in the repository before round 1. After its first output,
the worker gets one self-repair turn when the loaded page logs a console error
or lacks a contract id: the turn shows those errors and missing ids, never the
acceptance checks, the probe results or the how-to check. Both workers of a
pair get the same repair rule.
**Don't:** spend a repair turn on how-to format. A malformed how-to is read by
the repair chain below and costs score, never a turn.

**The how-to's form.** A visible `<section id="how-to">` saying what was built
and how to operate it, and the same content as JSON in
`<script type="application/json" id="how-to-data">`: a `summary` and a list of
`controls`, each `{id, does, action, effect}`, the action and the effect in the
check DSL (Appendix A). At most `how_to.max_controls` (12) are read.

**The how-to repair chain.** Every artifact is evaluated, whatever its how-to
looks like. The how-to check reads the JSON by the first rung that succeeds:

1. strict parse;
2. the repairing parser of every model JSON (§ 5, Model JSON);
3. salvage: each `controls` entry that decodes on its own and is valid DSL;
4. nothing: the how-to fraction `H` is 0 and the rest of the eval runs.

Rungs 2 and 3 multiply `H` by `1 − how_to.repair_penalty` (0.1). The rung
reached is recorded per pass; the readout reports the share at each rung.
**Don't:** re-ask the worker for a readable how-to, or drop a pass whose how-to
failed: a pass with a loss is worth more than a turn spent on format.

**Eval.** Each regime fixes its viewports (`eval.viewports`: 1280×800 in R1;
1280×800 and 375×812 in R2). Every reading below runs at each viewport and is
averaged over them.

- `P`, the probe suite: the share of three probes passed: the page loads within
  `eval.load_timeout_ms`; it logs no console error; and the share of its
  controls (buttons, inputs, selects, canvases, elements with a tab index, at
  most `probe.max_controls`) whose default action (click, type a sample string,
  pick an option, an arrow key on a focused canvas) changes the visible text,
  a canvas hash or a visibility within `probe.settle_ms`. A control that changes
  nothing scores 0, never "not applicable".
- `H`, the how-to check: the passed share of the declared controls (each one an
  acceptance check built from its action and effect), times the declared share
  of the contract ids, times the repair factor. An overclaim fails its control;
  a contract id the how-to omits lowers the coverage.
- `C`: passed acceptance checks ÷ acceptance checks for the current regime.
- `A = a_w.probe · P + a_w.how_to · H + a_w.checks · C` with weights
  `eval.a_weights` (0.3, 0.3, 0.4) summing to 1.
- Silent controls: the controls `P` found responsive that the how-to does not
  declare. Counted per pass, a reading only.
- `R`: the rubric model scores each viewport's screenshot on three frozen
  questions (does it look like the request; is every contract control visible
  without scrolling at this viewport; is it legible), mapped from 1–5 to 0–1
  and averaged. Temperature 0; cached by artifact hash and viewport. An
  artifact that cannot load scores the rubric floor without a call.
- `loss = 1 − (w_a · A + w_r · R)` with `w_a = 0.75`, `w_r = 0.25` in
  `run.yml`.
- Quality reading `Q`: ESLint error count over the extracted inline script
  (fixed config in `config/eslint.json`) plus axe-core violation count on the
  loaded page.

**Do:** treat the eval as frozen from round 1: its code, prompts, weights and
pins change only between runs.
**Don't:** fold `Q` into the loss; it moves the guard and the report, never
the scalar the gate reads.

**Why the rubric is a minority term.** The probe suite, the how-to check and
the acceptance checks are the deterministic backbone and nothing in the run can
game them; the how-to is the worker's own claim, so its check turns the
worker's declaration into a falsifier of the worker; the rubric catches what checks
cannot (a blank canvas that technically changes pixels). A model-graded
label never drives structure alone (GCE § 6 I12): the rubric carries a
quarter of the loss.

**Model JSON.** Crusoe's structured-output support varies by model (read
`features` from `/v1/models`). Request JSON where offered; decode every model
JSON through one repairing parser, re-ask once on a parse failure, then
record the call as failed.

---

## 6. The judge and the lens loop

### Two calls per journal-arm pass

**Lens call.** Input: the request text, the ids and payloads of the composed
decisions, the artifact source (its how-to included: it is part of the
artifact, not of the eval). No eval report, no rubric, no regime flag.
Output: `lenses`, for each active lens `{id, p, quote}`: `p` the probability
the lens's angle names a defect in this artifact, `quote` a verbatim span
from the source (empty when `p < 0.5`).

**Report call.** Input: everything the lens call had, its answers, the eval
report (each acceptance check with pass or fail and the failing step; the probe
results; each declared how-to control with pass or fail, the uncovered
contract ids, the repair rung and the silent controls; console errors; `Q`), and
the rubric's scores as text. Output:

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
   recurrence bar or fast-tracked, goes to the nominator while
   `gate.in_flight` has room: fast-tracked pools first, then the rest, oldest
   pool first within each. A pool is fast-tracked when it holds at least
   `pool.recurrence_min_observations` (3) open observations from at least
   `pool.fast_track_min_requests` (3) distinct requests and its leading
   direction label holds at least `pool.fast_track_share` (2/3) of them; it
   needs no second round.
   **Counting:** every count toward the bar or the fast-track is over
   distinct requests, not passes. The two passes of a pair build the same
   request, so their observations are correlated; counted per pass, one
   request's quirk would read as recurrence. On nomination, the pool's open observations close
   (they are the candidate's warrant).
   **Why:** a run that ends at 17:00 cannot wait two rounds for a shape every
   observation already agrees on. **Don't:** let agreement lower the gate.
   Observations are the judge's reading after it saw the eval report; their
   agreement says when to test a candidate, and only paired exposure says
   whether it helps (§ 8).
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
supersedes: [D-0003]       # optional; a list, since lineage may be a DAG
successors: []
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
Moot when: no request left in the goal stream or the held-out set carries a `fires_when` tag.
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

**In flight.** At most `gate.in_flight` (2) candidates receive exposure at
once; the rest wait in drafting order (fast-tracked first, § 7).

**Exposure.** Each round, for each of the round's requests, the two journal-arm
workers both receive the admitted decisions whose hooks fire (up to the
cap). Then, in this order:

1. If an in-flight candidate's hook fires on the category, pick the one with
   the fewest pairs (ties by id; counts update as the round's pairs are
   assigned in category-id order) and give it to one worker by a fair coin
   under the run seed.
2. Else, if an admitted decision is due for ablation (§ 9), the coin picks
   the worker that omits it.
3. Else the pair runs unexposed.

One pair carries at most one exposure. **Expected pairs:** a candidate gets
one pair per round per request in its reach not taken by another candidate;
with tags of 2–4 per request and `stream.per_round` (10) requests a round, a
reach of a third of the stream gives about 3 pairs a round.

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

- **admit** when `W ≥ 1/α` (`α = 0.3`, so `W ≥ 3.33`), `n ≥ gate.n_min` (4
  pairs), and the quality guard holds: the one-sided 90 % upper bound
  (t-interval) of the mean paired change in `Q` (with minus without) is
  ≤ `gate.quality_margin` (2.0 per pass);
- **refuse** when a mirror e-process on `−d` reaches `1/α` (the candidate
  hurts);
- **expire** when `n ≥ gate.n_max` (12 pairs) with neither.

On one reading, admit takes precedence over refuse, and refuse over expire.

Admission, refusal and expiry are status flips, each written at the round
close as one git commit, the Ledger account rendered into the warrant.

**Resolving power (simulated 2026-10-17 with the gate as built, Gaussian deltas
with sd 0.2 clipped to [−1, 1], 4,000 runs per cell, this rule):** admitted
within 12 pairs at mean effect 0 in 0.8 % of runs, 0.10 in 22 %, 0.15 in 52 %,
0.20 in 84 %, 0.30 in 99.6 %; median pairs to admission 9.5 at 0.15, 9 at 0.20,
6 at 0.30. The same simulation under α 0.2 and 24 pairs gives 1.2 %, 54 %, 90 %
and 99 %, at a median of 15 pairs for 0.15; the run's clock does not hold 24
pairs per candidate. One acceptance check of three is worth 0.10 of loss
(`0.75 · 0.4 / 3`); a decision that fixes one check and one declared control
reliably is detectable within a run; one that fixes a single check is not.
`n_min` binds under this rule: three pairs can reach `W ≈ 4.5 ≥ 3.33`.

**Multiplicity.** Each candidate is tested at α. With `c` candidates tested
under the null, up to `α · c` false admissions are expected; the readout
states `c` and `α · c` beside R3.

**Do:** fix α, `n_min`, `n_max`, `in_flight` and the margin in `run.yml`
before round 1.
**Don't:** move a gate constant after seeing a Ledger account; a bar chosen
from the data it gates is a bar the data passes.

**Why paired exposure.** A pair shares the request, the regime and the
round; only the candidate differs. The delta cancels request difficulty,
and the coin makes the delta causal. A replay of past requests with and
without the draft would cost a replay per candidate and read a population
narrower than the hook; exposure costs nothing extra and reads the hook's own
reach as it arrives. It needs no request to repeat: a fresh request per round
is paired within its round.

**The refused-pool block.** A refused or expired candidate's pool is marked
refused; the pooling call keeps routing matching observations to it; it is
re-nominated only when observations dated after the refusal reach the
recurrence bar or fast-track. **Why:** without it, the evidence that failed
once re-nominates the same candidate every round, which is a loop.

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

Both readings, and the contradicted share below, read only the evidence dated
after the decision's last flip to `admitted` (its admission, or a clear). A
cleared decision starts fresh; otherwise the harm that suspected it would
suspect it again at the next close.

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

At round `drift.round` (8 of 14), regime `R1` becomes `R2`. Two things change
at once, each earning one half of R3:

1. **The goal mix shifts** (`stream.mix.R1` to `stream.mix.R2`; at the
   defaults, mostly canvas games before, mostly data tools after). Decisions
   keyed on game traits fire less, and new shapes recur: this earns the
   admissions after the drift.
2. **The drift constraint** is appended to every request: "It must work on a
   phone screen: at 375 px wide every control is visible and usable without
   horizontal scrolling, and touch or click operates it." The eval adds the
   375×812 viewport to the probe suite, the acceptance checks (`checks.R2`)
   and the rubric. A decision that was right at 1280 px (a fixed-width canvas,
   a side panel) now costs loss where it fires: this earns the retirements.

Pick the mixes and the constraint at goal minting, before any worker runs, by
one rule: together they change how at least a third of the post-drift requests
must be built, and a real user plausibly asks for them later. Write the `R2`
checks at the same time.
**Don't:** choose or rewrite either after seeing which decisions `R1` admits;
a drift aimed at the Journal's own contents is staged, not observed. And don't
pick "no external libraries": the eval already aborts every network route, so
it changes nothing.

The run does not tell the loop that the regime changed: no agent sees a regime
flag or a family name; the constraint reaches the worker and the judge only as
request text, and the mix only as which requests arrive. The detector, the lens
loop and the tagger find the shift from loss and request text alone.

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
   the recurrence bar or fast-tracked for `run.convergence_rounds` (3)
   consecutive rounds after the drift round.

The readout (§ 12) runs after any halt.

**Transport.** Per-call retry with exponential backoff and jitter on 429 and
503, at most 5 tries; then the pass is recorded as failed for transport,
gives no delta, and is offered to the coordinator for one re-run.

**Single writer.** Passes run `run.max_parallel_passes` (16) at a time, one
process each with its own browser (the browser driver is not thread-safe),
and write only their own rows and `artifacts/<pass_id>/`. Every status flip, pool write, lens change and git
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
| How-to honesty           | per arm on V: the passed share of declared controls, the share of contract ids declared, silent controls per pass, the share of passes at each repair rung                                               | reported, no claim                                       |
| Hook transfer            | held-out requests on which at least one S\* decision fires                                                                                                                                               | reported, no claim                                       |

**Do:** state each reading with its population ("6 held-out categories × 4
reps") and its interval or p.
**Don't:** call a difference inside its interval a gain, or drop a reading
that failed; with 6 categories, small effects reach no resolution, and an
unresolved R1 is reported as unresolved.

**Training-run readings shown alongside** (descriptive, no claim): per-round
mean loss of journal-arm passes with no exposure and no ablation, against
notes-arm passes; the Journal's status flips on the round axis with the
drift round marked; lens value over rounds; the tag vocabulary's size over
rounds, with the round each tag was minted; fast-tracked against
bar-nominated candidates and their gate outcomes.

---

## 13. Build plan for the day

**Before the event (plans, prompts, data, keys; no code):** Crusoe org and
key with a card on file (raises limits to 600 RPM per model); You.com key;
confirm the pins against `/v1/models`; this spec in the new repository as
`SPEC.md`; Appendix A's text ready to paste.

| Time (PT)   | Block                                                                                                                                       | Done when                                                                                                                   |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| 10:45–12:15 | Types, `run.yml`, Crusoe client, gate, detector, lens curation, check DSL, Journal and Ledger stores (done; `CARRY_FORWARD.md`)              | tests pass; `friday doctor` resolves every pin                                                                               |
| 12:15–13:15 | Probe suite, how-to check and repair chain, rubric adapter; goal minter, stubs, check validation, tagger; freeze `goals/`; pilot round      | one hand-written HTML file evaluates to a loss; goals committed; worker pin settled                                          |
| 13:15–14:15 | Pass pipeline; judge prompts; pooling; nominator with You.com; round-close orchestrator; coordinator; notes arm; SQLite run stores           | **a 3-round dry run with all parts must pass by 14:15; if not, cut the lens minter and the notes arm and run without them** |
| 14:15–14:30 | Fixes from the dry run                                                                                                                      | the full run starts by 14:30                                                                                                 |
| 14:30–15:30 | The full run (14 rounds, background); meanwhile readout code, gallery, timeline                                                             | run halted, readout written                                                                                                  |
| 15:30–16:15 | Demo page; demo script; replay of the recorded run; optional critic run (§ 17) or OpenShell, not both                                       | demo runs from the recording with one live pass                                                                             |
| 16:15–17:00 | One-minute video; submission; repository public                                                                                             | submitted                                                                                                                   |

**Team split (up to four):** (1) client, worker, eval; (2) judge and lens
loop; (3) pools, nominator, gate, Journal, detector; (4) goals and tagger, coordinator,
readout, demo page, pitch. A solo builder runs the same blocks in order with
coding agents per block, against Appendix B's signatures.

**The demo surface.** Plain HTML served by FastAPI, never Streamlit:

1. **Gallery (leads):** every app built during the run, live in sandboxed
   iframes, each captioned by its own how-to and tagged with the decisions
   composed into it; click a decision to see the passes it changed, paired
   with their partners.
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
prompts/                baseline instructions, judge, nominator, minters, tagger, rubric, critic (frozen before round 1)
config/eslint.json      the fixed ESLint config of the quality reading
run-critic.yml          the critic run's overrides of run.yml (§ 17)
goals/stream.json       frozen goal stream; goals/held_out.json; goals/stubs/
goals/tags.json         tag vocabulary with glosses and the round each was minted
journal/D-NNNN.md       decisions (the Journal); one git commit per status flip
lenses/L-NN.md          lenses with status
plans/round-NN.md       the coordinator's round plans
friday.db               SQLite (WAL): passes, evals, lens_answers, fires, observations,
                        pools, exposures, ledger_deltas, events, spend
artifacts/<pass_id>/    index.html, shot-<viewport>.png, eval.json
friday/                 package in layers (ARCHITECTURE.md): domain, stats,
                        algorithms, ports, eval, roles, adapters, app
web/                    gallery and timeline (static pages over a JSON export)
```

**Storage choice.** The sponsors offer no store; the credits cover inference
only. Decisions and lenses are markdown in git because their history is the
product: `git log journal/` is the Journal's lifecycle, and judges can read
it in the public repository. Everything high-volume goes to SQLite in one
file. Screenshots at about 100 KB each over about 750 passes come to about
75 MB under `artifacts/`; keep `artifacts/` out of git and publish a sample.

**`run.yml` holds** the pins and fallbacks, temperatures, weights
`w_a`/`w_r` and `eval.a_weights`, the cap (6), the seed, `run.rounds`,
`drift.round`, the recurrence bar and fast-track share, the `stream.*`,
`probe.*`, `how_to.*`, `lens.*`, `gate.*`, `detect.*`, `pool.*`, `notes.*`,
`readout.*` and `critic.*` constants, and `run.spend_cap_usd`. Every default quoted in this
spec is a pointer to `run.yml`; on any disagreement, `run.yml` holds.

**Cost estimate** (Crusoe list prices read 2026-10-16 from the hackathon
skill file, per million tokens): per round 20 journal-arm and 10 notes-arm
worker passes, 20 × 2 judge calls, up to 30 rubric calls per viewport, one
tagger call, one round close. Over 14 rounds plus a 72-build readout: workers
about $1, judge about $5, rubric about $1, nominator, pooling, tagger and
minters about $3, goal minting about $1. About $11 against the $55 credit (2026-10-17),
with the spend cap at $30. Prices are read per call from `/v1/models`.

**Time estimate:** a pass is about 1–2 min (a ≤ 30 KB file at Nano speed,
an optional repair turn, about 10 page loads for probes, the how-to and the
checks, two judge calls); 30 passes at 16 in parallel plus the close is about
4–5 min a round; 14 rounds take about 60–70 min.

---

## 15. Deviations from GCE, stated

|     | GCE position                                                                        | Friday                                                                                                                                         | Why                                                                                                               |
| --- | ----------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| D1  | Promotion is human-performed, always; the human is the optimizer step (§ 2, § 6 I2) | A pre-registered mechanical gate admits; the human ratifies the gate's constants once, before round 1, and may steer at any time               | The hackathon asks for a team you can walk away from. The human's judgment is spent on the bar, not on each entry |
| D2  | The slow loop is judgment-bounded, never cadence-bound (§ 7.4)                      | Pooling and curation run at every round close                                                                                                  | A 6-hour build has no session boundary to hang a judgment on; the round is the boundary                           |
| D3  | Identity attaches at admission (§ 6 I3)                                             | `D-NNNN` is minted at drafting so the Ledger and the timeline can show candidates; admission is a status flip                                  | The candidate's life is part of the demo                                                                          |
| D4  | Self-graded labels never drive permanent structure alone (§ 6 I12)                  | Kept: the deterministic checks carry 0.75 of the loss and the rubric model 0.25                                                                | —                                                                                                                 |
| D5  | No learned routing or embedder in the retrieval path (§ 2 position 1)               | Kept: hooks are tag sets; pooling is a model assigning legible shapes to stable pools                                                          | —                                                                                                                 |
| D6  | The adjudicator is stronger than the worker                                         | If the rubric pin resolves to a model no stronger than the worker, the rubric's share of the loss is graded by a peer; recorded in the readout. Likewise if the drafting roles fall back to the judge's model | Crusoe's image-input models are mostly the 27–31B class |

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
| The probe suite and the how-to check leave headroom         | pilot mean `P` and mean `H` both above 0.9                    |
| A weak worker's how-to is readable after repair             | fewer than half of the pilot's passes reach rung 1, 2 or 3    |
| Acceptance checks without a reference are satisfiable       | more than a third of the run's checks are never passed        |
| The tagger reuses tags                                      | the tag vocabulary grows by more than 4 a round after round 3 |

These are design premises for one run, not claims the demo makes.

---

## 17. The critic run (optional, timeboxed)

A second run, after the main one and only if the 15:30 block has room, in which
each pass mimics a developer working under a manager: build, critic, revision.

- **The pass.** The worker builds as in § 5 (with its self-repair turn). The
  critic then reads the request, the artifact, its how-to and a 1280×800
  screenshot, and returns a short criticism and at most `critic.max_requests`
  (2) feature requests, each one sentence in a user's words. The worker gets
  one revision turn with the criticism and the requests appended to the request
  text. The revised artifact is the one evaluated and judged.
- **Obligations.** Each feature request must appear among the revised how-to's
  declared controls. The reading "requests honored" is the share of feature
  requests the how-to declares and the how-to check passes.
- **The loss is unchanged.** It is read against the original request's checks,
  the probe suite, the how-to and the rubric, so the critic run's losses are
  comparable to the main run's.
- **Independence.** The critic is a different model from the judge and the
  nominator, and reads no part of the eval (blinding, § 2). Each worker of a
  pair gets its own critic call on its own artifact: the critic's reaction is
  part of the exposure's effect, so the pair still differs only by the
  exposure.
- **Store.** Its own Journal, Ledger and lens set, started empty, under
  `run-critic.yml` (which overrides `paths.*`, `run.rounds` and `critic.*`
  only). Same seed, same goal stream.

**Readings** (descriptive; the pre-registered R1–R4 belong to the main run):
requests honored per round; mean loss against the main run's on the same
requests; the decisions admitted in each run, side by side.
**Concurrency.** The critic run may overlap the main run once the main run's
round 1 has closed cleanly. While both run, the critic run takes
`critic.max_parallel_passes` (6) of the machine's pass slots, and the main
run takes `run.max_parallel_passes` minus that.
**Don't:** let the critic run share the main run's stores or spend cap: it has
its own `paths.*` and `critic.spend_cap_usd`. And don't start it before the
main run's round 1 closes: a defect found there would void both.

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
- End the page with a visible section <section id="how-to"> that says what you built and how to operate each control.
- Put the same content as JSON in <script type="application/json" id="how-to-data">, in this form:
  {"summary": "one sentence", "controls": [{"id": "#start", "does": "starts the timer",
   "action": {"click": "#start"}, "effect": {"text_changed": "#time"}}]}
  An action is one of {"click": id}, {"type": id, "text": "..."}, {"key": "ArrowUp", "times": 1}, {"focus": id}.
  An effect is one of {"text_changed": id}, {"canvas_changed": id}, {"visible": id}.
- Team decisions, when listed, are rules your team learned from earlier builds. Apply each one that fits this request.
```

## Appendix B: shared types (write first, 10:45–11:00)

```python
# friday/types.py: the seams every module codes against
Request(id, text, round: int | None, tags: list[str], contract: dict[str, str], checks: dict[str, list[Check]])
HowToReading(pass_id, rung: 1 | 2 | 3 | 4, declared: list[Check], undeclared_ids: list[str], H: float)
ProbeReading(pass_id, viewport, loaded: bool, console_errors: list[str], controls: int, responsive: int, silent: int, P: float)
Check(id, steps: list[dict], expect: dict)
Composition(request_id, decision_ids: list[str], exposed: str | None, ablated: str | None)
Pass(id, round, category, arm, worker: str, composition: Composition, artifact_path, repaired: bool, transport_failed: bool)
EvalReport(pass_id, regime, checks: list[tuple[str, bool, str]], probes: list[ProbeReading], how_to: HowToReading, console_errors: list[str], P: float, H: float, C: float, A: float, R: float, rubric: list[int], Q: int, loss: float)
LensAnswer(pass_id, lens_id, p: float, quote: str)
Observation(id, pass_id, round, lens_id: str, category, shape, direction, quote, status, pool_id: str | None, direction_label: str | None)
Pool(id, lens_id, shape, direction_labels: list[str], status, merged_into: str | None, refused_at_round: int | None)
Decision(id, status, pool_id, fires_when: list[str], not_this: list[str], payload, warrant: dict, supersedes: list[str], successors: list[str])   # lineage is a DAG
Fire(pass_id, decision_id, round, disposition, quote)
LedgerDelta(decision_id, round, category, kind: "exposure" | "ablation", d: float, dQ: float)

# module seams
client.chat(role, messages, schema=None) -> dict | str        # records spend and model string
goals.round(n) -> list[Request]                               # the goal stream's requests for round n
tagger.tag(requests, vocabulary) -> dict[str, list[str]]      # may mint up to stream.max_new_tags
probe.run(page, viewport) -> ProbeReading
how_to.read(html, contract) -> HowToReading                   # the repair chain; never raises
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
critic.review(request, artifact, how_to, shot) -> (str, list[str])   # critic run only
```
