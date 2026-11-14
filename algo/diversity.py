"""algo/diversity.py — diversity-aware selection of up to N (T5.5).

Pure, dependency-free. Picks a high-quality AND diverse subset so the final
set does not waste scarce Adaptyv slots on near-duplicate sequences.

Greedy Maximal-Marginal-Relevance (MMR): seed with the best candidate, then
repeatedly add the candidate that best trades off its own quality against
how different it is from everything already picked:

    mmr(c) = lam * quality_norm(c) + (1 - lam) * min_distance(c, selected)

lam = 1 ignores diversity (pure quality top-k); lam = 0 ignores quality
(maximally spread). An optional min_distance floor hard-blocks picking two
candidates closer than a threshold (dedupe).

Distance is alignment-free k-mer Jaccard by default (handles unequal
lengths), or a caller-supplied precomputed matrix. Quality is whatever the
caller passes (e.g. -effective_rank from consensus); it is min-max
normalized internally so lam is meaningful. No affinity is invented.
"""
from __future__ import annotations

try:
    from .common import is_missing, minmax_normalize_column
except ImportError:
    from common import is_missing, minmax_normalize_column

__all__ = ["kmer_jaccard_distance", "select_diverse"]


def _kmers(seq, k):
    if len(seq) < k:
        return {seq}
    return {seq[i:i + k] for i in range(len(seq) - k + 1)}


def kmer_jaccard_distance(a, b, k=3):
    """1 - Jaccard overlap of k-mer sets. 0 = identical, 1 = no shared k-mer."""
    ka, kb = _kmers(a, k), _kmers(b, k)
    if not ka and not kb:
        return 0.0
    inter = len(ka & kb)
    union = len(ka | kb)
    return 1.0 - (inter / union if union else 0.0)


def _distance_getter(ids, sequences, distance_matrix, k):
    n = len(ids)
    if distance_matrix is not None:
        if len(distance_matrix) != n or any(len(r) != n for r in distance_matrix):
            raise ValueError("distance_matrix must be n x n")
        return lambda i, j: distance_matrix[i][j]
    if sequences is not None:
        if len(sequences) != n:
            raise ValueError("sequences length must match ids")
        cache = {}

        def dist(i, j):
            key = (i, j) if i < j else (j, i)
            if key not in cache:
                cache[key] = kmer_jaccard_distance(sequences[i], sequences[j], k)
            return cache[key]

        return dist
    raise ValueError("provide either sequences or distance_matrix")


def select_diverse(ids, quality, sequences=None, distance_matrix=None,
                   budget=10, lam=0.5, min_distance=0.0, k=3):
    """Pick up to `budget` ids balancing quality and diversity (MMR).

    ids:      candidate labels, length n.
    quality:  per-candidate score, higher = better (float; None allowed and
              treated as worst). Min-max normalized internally.
    sequences / distance_matrix: one of them supplies pairwise distance.
    budget:   max picks (<= 10 for this use).
    lam:      0..1 quality-vs-diversity knob (0.5 = balanced).
    min_distance: hard floor; never pick a candidate within this distance of
              an already-picked one (0 = off).

    Returns a list of dicts in selection order:
        {id, quality_norm, min_distance_at_pick}
    """
    n = len(ids)
    if len(quality) != n:
        raise ValueError("quality length must match ids")
    if not 0.0 <= lam <= 1.0:
        raise ValueError("lam must be in [0, 1]")
    if n == 0 or budget <= 0:
        return []

    qn_raw = minmax_normalize_column([None if is_missing(q) else float(q) for q in quality])
    qnorm = [0.0 if is_missing(q) else q for q in qn_raw]
    dist = _distance_getter(list(range(n)), sequences, distance_matrix, k)

    remaining = set(range(n))
    selected = []
    # seed: highest quality (ties -> lowest index, deterministic)
    first = max(remaining, key=lambda i: (qnorm[i], -i))
    selected.append({"id": ids[first], "quality_norm": qnorm[first],
                     "min_distance_at_pick": None})
    remaining.discard(first)
    chosen_idx = [first]

    while remaining and len(selected) < budget:
        best, best_mmr, best_md = None, None, None
        for c in remaining:
            md = min(dist(c, s) for s in chosen_idx)
            if min_distance > 0.0 and md < min_distance:
                continue  # too similar to something already picked
            score = lam * qnorm[c] + (1.0 - lam) * md
            if best is None or score > best_mmr or (score == best_mmr and c < best):
                best, best_mmr, best_md = c, score, md
        if best is None:
            break  # everything left is within the min_distance floor
        selected.append({"id": ids[best], "quality_norm": qnorm[best],
                         "min_distance_at_pick": best_md})
        chosen_idx.append(best)
        remaining.discard(best)

    return selected
