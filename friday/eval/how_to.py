"""The how-to check (SPEC § 5): read the declared controls through the repair chain, then score them."""

from __future__ import annotations

from collections.abc import Collection, Sequence

from friday.domain.types import CheckResult, HowToParse, HowToReading, Viewport


def parse(html: str, contract: Collection[str], max_controls: int, test_query: str, settle_ms: int) -> HowToParse:
    """The how-to in `html`, read at the first rung that succeeds. Total: never raises."""
    raise NotImplementedError("slice S1: how-to repair chain")


def score(parse_: HowToParse, results: Sequence[CheckResult], contract: Collection[str], repair_penalty: float,
          pass_id: str, viewport: Viewport) -> HowToReading:
    """H = passed share of declared controls x declared share of contract ids x repair factor."""
    raise NotImplementedError("slice S1: how-to scoring")
