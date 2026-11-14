"""algo/sensitivity.py — stability of the selection under weight changes (T5.6).

Pure, dependency-free. The final-10 weights are provisional (D5), so Ranking
needs to know: if we nudge the weights, does the same set survive? A set
that only exists at one exact weighting is fragile; one that persists across
perturbations is a defensible recommendation (and a strong pitch line).

Composite score per candidate = weighted sum of its per-axis scores, where
each axis is oriented (higher = better) and min-max normalized to [0, 1] so
the weights are comparable. Normalization is independent of the weights, so
perturbing weights changes only the weighting, not the scale.

Two modes:
  - 'axis_sweep' (default, deterministic, explainable): scale each axis
    weight by (1 +/- delta) one at a time, renormalize, recompute top-k.
    Reproducible, and reads cleanly on a slide ("halve developability's
    weight and 9/10 stay").
  - 'mc' (Monte Carlo): multiplicative log-normal noise on all weights,
    n_samples draws with a fixed seed.

Reports, per candidate, how often it stays in the top-k, plus the mean
Jaccard overlap between each perturbed top-k and the base top-k.
"""
from __future__ import annotations

import math
import random

try:
    from .common import is_missing, oriented_columns, minmax_normalize_column, average_ranks
except ImportError:
    from common import is_missing, oriented_columns, minmax_normalize_column, average_ranks

__all__ = ["composite_scores", "top_k", "sensitivity"]


def _normalized_axes(scores, directions):
    """Orient + min-max each axis to [0,1]. Returns n x k (None preserved)."""
    oriented = oriented_columns(scores, directions)
    n = len(oriented)
    k = len(directions)
    cols = [[oriented[i][a] for i in range(n)] for a in range(k)]
    norm_cols = [minmax_normalize_column(c) for c in cols]
    return [[norm_cols[a][i] for a in range(k)] for i in range(n)]


def composite_scores(norm_axes, weights):
    """Weighted sum per candidate over normalized axes. Missing axis -> 0."""
    out = []
    for row in norm_axes:
        s = 0.0
        for a, w in enumerate(weights):
            v = row[a]
            if not is_missing(v):
                s += w * v
        out.append(s)
    return out


def top_k(labels, composite, k):
    """The k best ids by composite (ties -> original order)."""
    ranks = average_ranks(composite, higher_better=True)
    order = sorted(range(len(labels)), key=lambda i: (ranks[i], i))
    return [labels[i] for i in order[:k]]


def _renorm(weights):
    total = sum(weights)
    if total <= 0:
        raise ValueError("weights must sum to a positive number")
    return [w / total for w in weights]


def sensitivity(scores, directions, weights, ids=None, k=10,
                method="axis_sweep", delta=0.25, n_samples=200, seed=0):
    """How stable is the top-k under weight perturbations (T5.6).

    scores: n x K (candidates x axes). directions/weights: length K.
    k: size of the set to watch. delta: perturbation size. method:
    'axis_sweep' (deterministic) or 'mc' (Monte Carlo, seeded).

    Returns:
        {
          "base_top_k": [ids...],
          "selection_frequency": {id: fraction in [0,1]},
          "mean_jaccard_stability": float in [0,1],
          "n_perturbations": int,
          "method": str,
        }
    selection_frequency is the share of perturbations keeping each candidate
    in the top-k; mean_jaccard_stability averages |perturbed ∩ base| /
    |perturbed ∪ base| over the perturbations.
    """
    K = len(directions)
    if len(weights) != K:
        raise ValueError("weights length must match number of axes")
    n = len(scores)
    labels = list(range(n)) if ids is None else list(ids)
    base_w = _renorm([float(w) for w in weights])

    norm_axes = _normalized_axes(scores, directions)
    base_top = top_k(labels, composite_scores(norm_axes, base_w), k)
    base_set = set(base_top)

    # build the perturbed weight vectors
    perturbed = []
    if method == "axis_sweep":
        for a in range(K):
            for factor in (1.0 + delta, 1.0 - delta):
                w = list(base_w)
                w[a] = max(0.0, w[a] * factor)
                perturbed.append(_renorm(w))
    elif method == "mc":
        rng = random.Random(seed)
        for _ in range(n_samples):
            w = [max(0.0, bw * math.exp(rng.gauss(0.0, delta))) for bw in base_w]
            if sum(w) <= 0:
                continue
            perturbed.append(_renorm(w))
    else:
        raise ValueError("method must be 'axis_sweep' or 'mc'")

    freq = {lab: 0 for lab in labels}
    jaccard_sum = 0.0
    for w in perturbed:
        tk = set(top_k(labels, composite_scores(norm_axes, w), k))
        for lab in tk:
            freq[lab] += 1
        union = base_set | tk
        jaccard_sum += (len(base_set & tk) / len(union)) if union else 1.0

    npert = len(perturbed)
    return {
        "base_top_k": base_top,
        "selection_frequency": {lab: (freq[lab] / npert if npert else 0.0)
                                for lab in labels},
        "mean_jaccard_stability": (jaccard_sum / npert) if npert else 1.0,
        "n_perturbations": npert,
        "method": method,
    }
