# Spec findings at scaffolding (2026-10-17)

Facts read while building the scaffold that bear on SPEC.md. `run.yml` holds the resolved values. The
items in "Open questions" change the design and need a decision before round 1.

## Read from the service

`GET /v1/models` on `https://managed-inference-api-proxy.crusoecloud.com/v1`, 2026-10-17. The offline
fixture is `tests/data/models_2026-10-17.json`.

| SPEC § 5 pin | Served as | Effect |
| --- | --- | --- |
| `nvidia/Nemotron-3-Nano-30B-A3B` (worker) | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | id corrected in run.yml |
| `nvidia/Nemotron-3-Super-120B-A12B` (judge; Ultra fallback) | `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B` | id corrected in run.yml |
| `nvidia/NVIDIA-Nemotron-3-Ultra-550B` (nominator, minters, pooling) | not served | those roles take the Super fallback, the same model as the judge |
| `google/gemma-4-31b-it` (rubric, first choice) | served, multimodal | rubric pin holds |
| `openai/gpt-oss-120b` (coordinator) | served; tags omit "tool calling" | tool calling unconfirmed for the coordinator |

Other served models not in the spec: `nvidia/NVIDIA-Nemotron-3.5-Super-VL` (multimodal),
`nvidia/Nemotron-3.5-Lightning-30B-A3B`, `zai-org/GLM-5.3`, `deepseek-ai/Deepseek-V4-Flash`,
`deepseek-ai/DeepSeek-V4.1-Flash`.

The listing carries per-model prices, so cost is computed from it. A live structured-output call
(`response_format: json_schema`) to the judge pin returned valid JSON.

## Arithmetic read from the gate as built

- **`gate.n_min` never binds.** Under λ₁ = 0.25 and λ_max = 0.9, wealth after 3 pairs is at most
  1.25 · 1.9 · 1.9 ≈ 4.5, below 1/α = 5. No sequence of 3 pairs can admit, so `n_min = 4` only restates the
  betting schedule. `tests/unit/test_gate.py` covers `n_min` with a larger value.
- **The § 8 resolving-power table reproduces.** Run under the gate as built:
  - admission rate 51 % at 0.10, 89 % at 0.15, 99 % at 0.20, each within 3 binomial standard errors;
  - at no effect, about 1 % against the spec's 1.4 %, varying with the seed. The test bounds it at 2 %;
  - median pairs to admission 16 at 0.15, 12 at 0.20, 8 at 0.30, each within 2.
- **A constant effect of 0.3 needs about 8 pairs.** With 3–6 pairs a round, admission takes two rounds or
  more.

## Open questions

1. **Ultra is not served.** The nominator, minters and pooling call run on the judge's model. A pass's
   observations are drafted into payloads by the same model that filed them. Pick another strong text model
   (`zai-org/GLM-5.3`, `deepseek-ai/DeepSeek-V4-Pro`) to keep the roles independent, or accept and record it.
2. **Coordinator tool calling.** `/v1/models` does not tag `openai/gpt-oss-120b` with tool calling;
   `zai-org/GLM-5.3` and `deepseek-ai/DeepSeek-V4-Pro` carry the tag. Verify with one tool call before the
   dry run.
3. **Lineage is a DAG.** A decision can supersede two predecessors, and a fold has several. Appendix B's
   scalar `supersedes` / `successor` cannot hold that, so the code uses lists. The spec's front-matter
   example should follow.
4. **One home for status.** § 2 L2 names the decision's front matter as the home of its status, and § 14
   lists an `events` table. The code makes the event log canonical and writes the files and commits from it,
   in the round close. The front matter is then a projection, not a second home.
5. **Disposition vocabulary.** A pass whose report call is dropped after the quote re-ask leaves its fires
   without a verdict. The code records them `undisposed`, and the detector's contradicted share skips them.
   § 2's disposition set has no such value.
6. **Lens value per judge model.** A lens answer records the judge's served model string. If the judge pin
   falls back mid-run, the lens's value history mixes two answerers. Decide whether a fallback resets the
   window.
7. **Fold on correlation alone.** Two lenses whose `p` move together may still ask different things, and the
   fold supersedes one on ρ > 0.85 with no look at their quotes. A cheap guard: fold only when the two lenses'
   flagged quotes overlap on most passes.
8. **Contradicted path.** A judge label alone moves a decision to `suspect` (§ 9). Retirement still needs
   ablation pairs, which the code enforces. State in the readout that `suspect` can come from the judge alone.
