"""algo/pareto.py — Pareto front + non-dominated layers (T5.2).

Pure, dependency-free. Given candidates scored on several objectives (each
max or min), returns the non-dominated set (the trade-off frontier) and,
optionally, the deeper layers for tie-breaking.

A candidate A dominates B when A is at least as good on every objective and
strictly better on at least one. The front is everyone nobody dominates.

This only compares the objective vectors the caller supplies (Utgang §0.7);
it invents no score. Ranking (T3) calls it to see which candidates are
'best without compromise' before weighting anything.

Objective vectors must be COMPLETE — a missing objective makes dominance
ill-defined, so we raise. Ranking imputes or drops incomplete rows first.
"""
from __future__ import annotations

try:  # package import
    from .common import is_missing, orient_value
except ImportError:  # standalone import (tests)
    from common import is_missing, orient_value

__all__ = ["dominates", "pareto_front", "pareto_layers"]


def _orient_complete(objectives, directions):
    k = len(directions)
    oriented = []
    for r, row in enumerate(objectives):
        if len(row) != k:
            raise ValueError(f"row {r} has {len(row)} objectives, expected {k}")
        new = []
        for v, d in zip(row, directions):
            if is_missing(v):
                raise ValueError(
                    f"row {r} has a missing objective; Pareto needs complete "
                    f"vectors (impute or drop upstream)"
                )
            new.append(orient_value(float(v), d))
        oriented.append(new)
    return oriented


def dominates(a, b):
    """True if oriented vector a dominates b (>= on all, > on at least one)."""
    gt_any = False
    for x, y in zip(a, b):
        if x < y:
            return False
        if x > y:
            gt_any = True
    return gt_any


def pareto_front(objectives, directions, ids=None):
    """Return the ids (or 0-based indices) of the non-dominated candidates.

    objectives: n x k (float). directions: list[str] length k ('max'/'min').
    ids: optional labels, length n. Duplicates are mutually non-dominating,
    so identical best vectors both stay on the front.
    """
    oriented = _orient_complete(objectives, directions)
    n = len(oriented)
    labels = list(range(n)) if ids is None else list(ids)
    if len(labels) != n:
        raise ValueError("ids length must match number of rows")
    front = []
    for i in range(n):
        if not any(i != j and dominates(oriented[j], oriented[i]) for j in range(n)):
            front.append(labels[i])
    return front


def pareto_layers(objectives, directions, ids=None):
    """Non-dominated sorting: list of layers, layer 0 = the Pareto front.

    Each subsequent layer is the front of what remains once higher layers
    are removed. Useful for tie-breaking / depth when the first front is
    larger than the budget.
    """
    oriented = _orient_complete(objectives, directions)
    n = len(oriented)
    labels = list(range(n)) if ids is None else list(ids)
    if len(labels) != n:
        raise ValueError("ids length must match number of rows")
    remaining = set(range(n))
    layers = []
    while remaining:
        layer = [
            i for i in remaining
            if not any(j != i and dominates(oriented[j], oriented[i]) for j in remaining)
        ]
        if not layer:  # safety: cycles are impossible, but never loop forever
            layer = list(remaining)
        layers.append([labels[i] for i in layer])
        remaining -= set(layer)
    return layers
