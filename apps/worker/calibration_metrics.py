"""Rater-vs-judge statistical agreement metrics (Spec 04 §2 Phase 9). Pure
functions, no I/O — mirrors reconciliation.py's posture, directly
unit-testable without a live judge, a benchmark corpus loader, or a
database. Every function takes two equal-length sequences of IELTS bands
(0.0-9.0, 0.5 increments): `preds` (the judge's reconciled score) and
`golds` (the certified human rater's score).
"""
from __future__ import annotations

from collections.abc import Sequence


def _check_equal_length(preds: Sequence[float], golds: Sequence[float]) -> None:
    if len(preds) != len(golds):
        raise ValueError(f"preds/golds length mismatch: {len(preds)} != {len(golds)}")


def mean_absolute_error(preds: Sequence[float], golds: Sequence[float]) -> float:
    _check_equal_length(preds, golds)
    if not preds:
        return 0.0
    return sum(abs(p - g) for p, g in zip(preds, golds)) / len(preds)


def root_mean_squared_error(preds: Sequence[float], golds: Sequence[float]) -> float:
    _check_equal_length(preds, golds)
    if not preds:
        return 0.0
    return (sum((p - g) ** 2 for p, g in zip(preds, golds)) / len(preds)) ** 0.5


def agreement_rate(preds: Sequence[float], golds: Sequence[float], *, tolerance: float) -> float:
    """Fraction of pairs within `tolerance` bands of each other. Called at
    tolerance=0.0 (exact), 0.5, and 1.0 for the three agreement-rate
    metrics Spec 04 §2 Phase 9 asks for."""
    _check_equal_length(preds, golds)
    if not preds:
        return 0.0
    # A small epsilon guards against float round-off (e.g. 0.1+0.2-style
    # error) putting an exact-tolerance boundary case on the wrong side.
    return sum(1 for p, g in zip(preds, golds) if abs(p - g) <= tolerance + 1e-9) / len(preds)


def pearson_correlation(preds: Sequence[float], golds: Sequence[float]) -> float | None:
    """None on fewer than 2 points or zero variance in either series (the
    coefficient is undefined, not zero, in that case)."""
    _check_equal_length(preds, golds)
    n = len(preds)
    if n < 2:
        return None
    mean_p = sum(preds) / n
    mean_g = sum(golds) / n
    covariance = sum((p - mean_p) * (g - mean_g) for p, g in zip(preds, golds))
    var_p = sum((p - mean_p) ** 2 for p in preds)
    var_g = sum((g - mean_g) ** 2 for g in golds)
    if var_p == 0 or var_g == 0:
        return None
    return covariance / (var_p**0.5 * var_g**0.5)


def _average_ranks(values: Sequence[float]) -> list[float]:
    """1-indexed ranks with ties given the average of the ranks they span
    (the standard convention Spearman's rho requires)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return ranks


def spearman_correlation(preds: Sequence[float], golds: Sequence[float]) -> float | None:
    """Pearson correlation of the rank-transformed series — equivalent to
    Spearman's rho, and reuses pearson_correlation's degenerate-input
    handling (e.g. every value tied -> undefined, not zero)."""
    _check_equal_length(preds, golds)
    if len(preds) < 2:
        return None
    return pearson_correlation(_average_ranks(preds), _average_ranks(golds))


def quadratic_weighted_kappa(
    preds: Sequence[float],
    golds: Sequence[float],
    *,
    min_band: float = 0.0,
    max_band: float = 9.0,
    step: float = 0.5,
) -> float | None:
    """Cohen's quadratic weighted kappa over bands bucketed into their
    discrete 0.5-increment classes (19 classes for the default 0.0-9.0
    range). Returns None on degenerate inputs (empty, or every point in a
    single class for both preds and golds — the expected-agreement
    denominator is then zero) rather than raising or returning a
    misleadingly-precise number.
    """
    _check_equal_length(preds, golds)
    n = len(preds)
    if n == 0:
        return None

    num_classes = round((max_band - min_band) / step) + 1

    def to_class(band: float) -> int:
        cls = round((band - min_band) / step)
        return max(0, min(num_classes - 1, cls))

    pred_classes = [to_class(p) for p in preds]
    gold_classes = [to_class(g) for g in golds]

    if num_classes == 1:
        return 1.0  # only one possible class -- trivially perfect agreement

    observed = [[0] * num_classes for _ in range(num_classes)]
    pred_hist = [0] * num_classes
    gold_hist = [0] * num_classes
    for p, g in zip(pred_classes, gold_classes):
        observed[p][g] += 1
        pred_hist[p] += 1
        gold_hist[g] += 1

    weights = [
        [((i - j) ** 2) / ((num_classes - 1) ** 2) for j in range(num_classes)]
        for i in range(num_classes)
    ]

    numerator = sum(
        weights[i][j] * observed[i][j] for i in range(num_classes) for j in range(num_classes)
    )
    denominator = sum(
        weights[i][j] * pred_hist[i] * gold_hist[j] / n
        for i in range(num_classes)
        for j in range(num_classes)
    )
    if denominator == 0:
        return None
    return 1 - numerator / denominator
