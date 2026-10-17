"""The pre-registered readings R1, R2 over held-out categories (SPEC § 12)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from friday.stats.bounds import t_interval
from friday.stats.significance import OneSidedTest


@dataclass(frozen=True)
class PairedReading:
    n_categories: int
    per_category: Mapping[str, float]
    mean: float
    p: float
    interval: tuple[float, float] | None
    level: float


def per_category_mean_diff(
    baseline: Sequence[tuple[str, float]], treated: Sequence[tuple[str, float]],
) -> dict[str, float]:
    """Per category, mean(baseline losses) - mean(treated losses). Raises when an arm lacks a category."""
    def means(rows: Sequence[tuple[str, float]]) -> dict[str, float]:
        acc: dict[str, list[float]] = defaultdict(list)
        for cat, loss in rows:
            acc[cat].append(loss)
        return {c: sum(v) / len(v) for c, v in acc.items()}

    b, t = means(baseline), means(treated)
    if b.keys() != t.keys():
        raise ValueError(f"arms cover different categories: {sorted(b.keys() ^ t.keys())}")
    return {c: b[c] - t[c] for c in sorted(b.keys() & t.keys())}


def paired_reading(per_category: Mapping[str, float], test: OneSidedTest, level: float) -> PairedReading:
    """p for H1 'treated has lower loss' and the two-sided `level` t-interval over category means."""
    xs = list(per_category.values())
    if not xs:
        raise ValueError("no categories to read")
    return PairedReading(len(xs), dict(per_category), sum(xs) / len(xs), test.p_value(xs), t_interval(xs, level), level)
