"""Rank statistics: average ranks, Mann-Whitney AUC, Spearman correlation (SPEC § 6)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Protocol

from friday.registry import Registry


def average_ranks(xs: Sequence[float]) -> list[float]:
    """1-based ranks of `xs`, ties taking the mean of the ranks they span."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        r = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = r
        i = j + 1
    return ranks


class BinaryScore(Protocol):
    def score(self, scores: Sequence[float], labels: Sequence[bool]) -> float | None:
        """How well `scores` rank positive `labels` above negative ones; None when undefined."""
        ...


class Correlation(Protocol):
    def corr(self, xs: Sequence[float], ys: Sequence[float]) -> float | None:
        """Correlation of paired `xs`, `ys`; None when either is constant."""
        ...


LENS_VALUES: Registry[BinaryScore] = Registry("lens_value")
CORRELATIONS: Registry[Correlation] = Registry("fold_correlation")


def auc(scores: Sequence[float], labels: Sequence[bool]) -> float | None:
    """Mann-Whitney AUC with average ranks for ties; None when either class is empty."""
    if len(scores) != len(labels):
        raise ValueError("scores and labels differ in length")
    n_pos = sum(1 for y in labels if y)
    n_neg = len(labels) - n_pos
    if n_pos == 0 or n_neg == 0:
        return None
    ranks = average_ranks(scores)
    rank_sum = sum(r for r, y in zip(ranks, labels, strict=True) if y)
    return (rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n != len(ys):
        raise ValueError("xs and ys differ in length")
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / math.sqrt(sxx * syy)


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Pearson correlation of average ranks; None when either vector is constant."""
    return pearson(average_ranks(xs), average_ranks(ys))


@LENS_VALUES.register("auc")
class AucScore:
    def score(self, scores: Sequence[float], labels: Sequence[bool]) -> float | None:
        return auc(scores, labels)


@CORRELATIONS.register("spearman")
class SpearmanCorrelation:
    def corr(self, xs: Sequence[float], ys: Sequence[float]) -> float | None:
        return spearman(xs, ys)


@CORRELATIONS.register("pearson")
class PearsonCorrelation:
    def corr(self, xs: Sequence[float], ys: Sequence[float]) -> float | None:
        return pearson(xs, ys)
