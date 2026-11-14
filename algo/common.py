"""algo/common.py — small shared numeric helpers for the method library.

Pure, dependency-free. Orientation (max/min -> higher-is-better), min-max
normalization, and average-rank ranking with tie + missing handling.
Imported by pareto, aggregation, diversity, sensitivity.
"""
from __future__ import annotations

import math

__all__ = [
    "is_missing",
    "orient_value",
    "oriented_columns",
    "minmax_normalize_column",
    "average_ranks",
]


def is_missing(v):
    """True for None or NaN."""
    return v is None or (isinstance(v, float) and math.isnan(v))


def orient_value(v, direction):
    """Flip so that higher is always better. direction in {'max','min'}."""
    if direction == "max":
        return v
    if direction == "min":
        return -v
    raise ValueError(f"direction must be 'max' or 'min', got {direction!r}")


def oriented_columns(matrix, directions):
    """Orient every column to higher-is-better. Missing entries stay None.

    matrix: n rows x k cols (float | None). directions: list[str] length k.
    """
    k = len(directions)
    out = []
    for row in matrix:
        if len(row) != k:
            raise ValueError(f"row has {len(row)} cols, expected {k}")
        out.append([
            None if is_missing(v) else orient_value(float(v), d)
            for v, d in zip(row, directions)
        ])
    return out


def minmax_normalize_column(col):
    """Min-max a column to [0, 1]. None preserved; constant column -> 0.5."""
    vals = [v for v in col if not is_missing(v)]
    if not vals:
        return list(col)
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return [None if is_missing(v) else 0.5 for v in col]
    return [None if is_missing(v) else (v - lo) / (hi - lo) for v in col]


def average_ranks(values, higher_better=True):
    """Rank values, 1 = best. Ties share the average rank; None stays None.

    e.g. [10, 10, 5] higher_better -> [1.5, 1.5, 3].
    """
    present = [(v, i) for i, v in enumerate(values) if not is_missing(v)]
    present.sort(key=lambda t: t[0], reverse=higher_better)
    ranks = [None] * len(values)
    vals = [p[0] for p in present]
    idxs = [p[1] for p in present]
    n = len(present)
    j = 0
    pos = 1
    while j < n:
        k = j
        while k + 1 < n and vals[k + 1] == vals[j]:
            k += 1
        avg = (pos + (pos + (k - j))) / 2.0  # average of positions pos..pos+(k-j)
        for t in range(j, k + 1):
            ranks[idxs[t]] = avg
        pos += (k - j + 1)
        j = k + 1
    return ranks
