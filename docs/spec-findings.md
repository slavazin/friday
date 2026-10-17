# Spec findings at scaffolding (2026-10-17)

Facts read while building the scaffold that bear on SPEC.md. `run.yml` holds the resolved values. The
items in "Open questions" change the design and need a decision before round 1.

## Read from the service

`GET /v1/models` on `https://managed-inference-api-proxy.crusoecloud.com/v1`, 2026-10-17. The offline
fixture is `tests/data/models_2026-10-17.json`.

| SPEC § 5 pin | Served as | Effect |
| --- | --- | --- |
| `nvidia/Nemotron-3-Nano-30B-A3B` (worker) | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | id corrected in run.yml and SPEC § 5 |
| `nvidia/Nemotron-3-Super-120B-A12B` (judge; Ultra fallback) | `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B` | id corrected in run.yml and SPEC § 5 |
| `nvidia/NVIDIA-Nemotron-3-Ultra-550B` (nominator, minters, pooling) | not served | those roles take the Super fallback, the same model as the judge |
| `google/gemma-4-31b-it` (rubric, first choice at the time) | served, multimodal | now second; `nvidia/NVIDIA-Nemotron-3.5-Super-VL` leads the rubric pins |
| `openai/gpt-oss-120b` (coordinator) | served; tags omit "tool calling" | tool calling unconfirmed for the coordinator |

Other served models not in the spec at the time: `nvidia/NVIDIA-Nemotron-3.5-Super-VL` (multimodal),
`nvidia/Nemotron-3.5-Lightning-30B-A3B`, `zai-org/GLM-5.3`, `deepseek-ai/Deepseek-V4-Flash`,
`deepseek-ai/DeepSeek-V4.1-Flash`.

The listing carries per-model prices, so cost is computed from it. A live structured-output call
(`response_format: json_schema`) to the judge pin returned valid JSON.

## Arithmetic read from the gate as built

- **Under the spec's first rule (α 0.2, 24 pairs), `gate.n_min` never bound.** Wealth after 3 pairs is
  at most 1.25 · 1.9 · 1.9 ≈ 4.5, below 1/α = 5. Under the rule now in `run.yml` (α 0.3, so 3.33), it
  binds.
- **SPEC § 8's resolving-power figures are simulated with the gate as built.** `tests/unit/test_gate.py`
  re-reads them on every run, along with the earlier rule's figures as a cross-check.

## Settled on 2026-10-17

- The experiment: a goal stream with goal families and a mix shift at the drift, plus a phone-width
  drift constraint (SPEC §§ 4, 10).
- The eval's deterministic share: the probe suite, the how-to check with its repair chain, and
  stub-validated acceptance checks (SPEC § 5).
- The gate: α 0.3, at most 12 pairs, at most 2 in flight, and fast-track nomination at a 2/3 share
  (SPEC §§ 7, 8).
- The rubric pin: `nvidia/NVIDIA-Nemotron-3.5-Super-VL` first.
- Lineage is a list (Appendix B), and `undisposed` joins the disposition vocabulary.
- Status is read from the front matter. The event log is the append-only history, and a flip is validated
  before either is written.
- The critic run: optional, timeboxed (SPEC § 17).
- The drafting roles (nominator, lens minter, goal minter, tagger, pooling call) are pinned to
  `zai-org/GLM-5.3`, since Ultra is not served. The drafting model then differs from the judge's, which
  keeps the roles independent. A live structured call answered in 0.8 s. The critic moves to
  `deepseek-ai/DeepSeek-V4-Pro`, so it differs from both.
- **Readout bounds come from the test that decides the verdict.** R1 had shown a two-sided t-interval beside
  a sign-flip p. At 6 categories the two can split, which puts "improves" next to an interval that crosses
  0 and leaves the choice of reading to the writer. The bound is now the inversion of the sign-flip test
  (the (k+1)-th smallest subset mean, k = ⌊α·2ⁿ⌋ − 1), so p ≤ 0.1 is exactly the bound lying above 0.
  R2 became non-inferiority with a margin (`readout.r2_margin`, 0.05 loss): "the interval does not lie
  below 0" was earned by any reading too noisy to show harm. R2's token condition left the verdict, since
  § 12 pre-registers tokens as reported. Ruled before round 1, with no readout data in existence.
- **The critic reads the screenshot, and is never the rubric's model.** The critic is pinned to a vision model
  (`nvidia/Nemotron-3-Nano-Omni-Reasoning-30B-A3B`, fallback `google/gemma-4-31b-it`) so it reads the 1280×800
  screenshot. Binding refuses a critic whose model equals the bound rubric model: the critic would steer
  revisions toward the scorer's taste and bias the critic run's loss comparison. It refuses the judge's and the
  nominator's model too (SPEC § 17 Independence). `app/pins.py` `check_critic_independence`.

- **A fold needs both lens values read.** In the first live round two baseline lenses folded at ρ 0.99 and 1.0
  over about 12 answered passes, and the keeper was arbitrary because neither value was read below
  `lens.n_min`. A fold now runs only between lenses whose values are read, which "keep the higher-value lens"
  already assumed.
- **Model calls have a total deadline.** A judge lens call held its connection open with no reply for up to
  580 s: httpx bounds each read, not the call. Each attempt now ends at `transport.request_timeout_s` (150 s)
  in total and is retried like a 5xx.

- **Amendment after round 1 of the main run (15:20).** Under 32 parallel passes the judge's endpoint answered
  HTTP 429 to bursts of about 40 calls: 13 of 43 round-1 judge calls failed after five tries, and their passes
  carry no lens answers or observations (recorded as dropped calls). The run resumed at round 2 with two
  changes that alter when calls are made, never what any reading computes: `models.roles.judge.max_concurrent:
  8` (passes queue for the judge across processes) and `transport.backoff_cap_s` 30 -> 60. Round 1's records
  stand as written; its spec hash differs from rounds 2-7's.

## Open questions

1. **Coordinator tool calling.** `/v1/models` does not tag `openai/gpt-oss-120b` with tool calling. Verify
   it with one tool call before the dry run.
2. **Lens value per judge model.** If the judge pin falls back mid-run, a lens's value history mixes two
   answerers. Decide whether a fallback resets the window.
3. **Fold on correlation alone.** Two lenses whose `p` move together may ask different things. A cheap
   guard: fold only when their flagged quotes overlap on most passes.
4. **The contradicted path.** A judge label alone moves a decision to `suspect` (§ 9); retirement still
   needs ablation pairs. State it in the readout.
5. **Smaller effects.** One acceptance check is now worth 0.10 of loss, not 0.15. Fewer decisions may clear
   the gate within 12 pairs (§ 8). The falsifier "no candidate admitted by the drift round" (§ 16) reads it.
