# Friday

A self-improving builder team with a journal it can account for.

Built for the [Crusoe Recursive Agents Hackathon](https://cerebralvalley.ai/e/crusoe-recursive-agents-hackathon/).

A coordinator runs a team of weak worker agents that build single-file web apps. A judge reads each one
through a curated set of questions (lenses) and files observations. A recurring observation becomes a
candidate decision. A candidate enters the Journal only when a randomized paired experiment shows it
lowers a fixed loss. It leaves the Journal when the same experiment, kept running, shows it stopped
paying.

- [SPEC.md](SPEC.md) — the design.
- [ARCHITECTURE.md](ARCHITECTURE.md) — the code layout, its seams, and what is built.
- [docs/spec-findings.md](docs/spec-findings.md) — facts read while scaffolding, and open questions.

## Quick start

```bash
uv sync
```

```bash
uv run pytest
```

```bash
uv run --env-file .env friday doctor
```

## Status

Hackathon build in progress.
