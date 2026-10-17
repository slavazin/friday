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

## Open questions

1. **Ultra is not served.** The nominator, the minters, the tagger and the pooling call run on the judge's
   model. Pick another strong text model (`zai-org/GLM-5.3`, `deepseek-ai/DeepSeek-V4-Pro`) to keep the
   roles independent, or accept it and record it beside D6.
2. **Coordinator tool calling.** `/v1/models` does not tag `openai/gpt-oss-120b` with tool calling. Verify
   it with one tool call before the dry run.
3. **Lens value per judge model.** If the judge pin falls back mid-run, a lens's value history mixes two
   answerers. Decide whether a fallback resets the window.
4. **Fold on correlation alone.** Two lenses whose `p` move together may ask different things. A cheap
   guard: fold only when their flagged quotes overlap on most passes.
5. **The contradicted path.** A judge label alone moves a decision to `suspect` (§ 9); retirement still
   needs ablation pairs. State it in the readout.
6. **Smaller effects.** One acceptance check is now worth 0.10 of loss, not 0.15. Fewer decisions may clear
   the gate within 12 pairs (§ 8). The falsifier "no candidate admitted by the drift round" (§ 16) reads it.
