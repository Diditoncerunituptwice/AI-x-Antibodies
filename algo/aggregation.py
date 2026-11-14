"""algo/aggregation.py — consensus / rank-aggregation + disagreement (T5.3, T5.4).

Pure, dependency-free.

Evaluators (independent ones only — the caller filters; Spec derives
independence DB-side) each score the same candidates on different, non-
comparable scales (ipTM vs ipSAE vs developability). So we aggregate in
RANK space, not raw-score space: each evaluator's scores become ranks
(1 = best), and we combine the ranks. Scale-free and robust.

T5.4 disagreement/uncertainty: how much the evaluators disagree about a
candidate = the spread of that candidate's ranks across evaluators. A
candidate everyone puts around 5th is more trustworthy than one that is
1st for two evaluators and 20th for a third.

T5.3 consensus applies a disagreement PENALTY (D12): a candidate's effective
rank is worsened in proportion to how much its evaluators disagree, so a
hidden-split candidate cannot ride one evaluator into a scarce Adaptyv slot.

Nothing here is an affinity (Utgang §0.7) — it is rank evidence about
agreement, not a measured number.
"""
from __future__ import annotations

try:
    from .common import is_missing, average_ranks
except ImportError:
    from common import is_missing, average_ranks

__all__ = ["rank_matrix", "disagreement", "consensus"]


def _check(scores, directions):
    m = len(directions)
    for r, row in enumerate(scores):
        if len(row) != m:
            raise ValueError(f"row {r} has {len(row)} evaluators, expected {m}")
    for d in directions:
        if d not in ("max", "min"):
            raise ValueError(f"direction must be 'max'/'min', got {d!r}")


def _mean(xs):
    return sum(xs) / len(xs)


def _pstdev(xs):
    if len(xs) < 2:
        return 0.0
    mu = _mean(xs)
    return (sum((x - mu) ** 2 for x in xs) / len(xs)) ** 0.5


def rank_matrix(scores, directions):
    """Column-wise ranks (1 = best) per evaluator. Missing scores -> None.

    scores: n candidates x m evaluators. directions: list[str] length m.
    Returns an n x m matrix of ranks (float | None).
    """
    _check(scores, directions)
    n = len(scores)
    m = len(directions)
    ranks = [[None] * m for _ in range(n)]
    for e in range(m):
        col = [scores[i][e] for i in range(n)]
        col_ranks = average_ranks(col, higher_better=(directions[e] == "max"))
        for i in range(n):
            ranks[i][e] = col_ranks[i]
    return ranks


def disagreement(scores, directions, ids=None):
    """Per-candidate evaluator disagreement (T5.4).

    Returns a list of dicts, one per candidate, in input order:
        {id, coverage, rank_std, rank_range, disagreement_norm}
    - coverage: how many evaluators scored this candidate.
    - rank_std: population std of its ranks across evaluators (rank units).
    - rank_range: max-minus-min rank.
    - disagreement_norm: rank_std scaled by (n-1) into [0, ~0.5], comparable
      across runs of different size. 0 means the evaluators agree exactly.
    With <2 evaluators scoring a candidate, disagreement is 0 (nothing to
    disagree on) and coverage flags the thin evidence.
    """
    ranks = rank_matrix(scores, directions)
    n = len(scores)
    labels = list(range(n)) if ids is None else list(ids)
    denom = (n - 1) if n > 1 else 1
    out = []
    for i in range(n):
        rs = [r for r in ranks[i] if not is_missing(r)]
        if len(rs) >= 2:
            std = _pstdev(rs)
            rng = max(rs) - min(rs)
        else:
            std = 0.0
            rng = 0.0
        out.append({
            "id": labels[i],
            "coverage": len(rs),
            "rank_std": std,
            "rank_range": rng,
            "disagreement_norm": std / denom,
        })
    return out


def consensus(scores, directions, ids=None, weights=None, penalty=1.0,
              aggregate="mean_rank"):
    """Rank-aggregation across evaluators with a disagreement penalty (T5.3).

    scores: n x m (candidates x evaluators), float | None (None = not scored).
    directions: list[str] length m. weights: optional per-evaluator weights
    (length m, used only by 'mean_rank'). penalty: how hard disagreement
    worsens the effective rank (rank units per rank-std; 0 disables).
    aggregate: 'mean_rank' (weighted mean of ranks) or 'median_rank' (robust).

    Returns a list of dicts sorted best-first (lowest effective_rank):
        {id, effective_rank, base_rank, disagreement, coverage, n_evaluators}
    base_rank is the aggregate rank before penalty; effective_rank =
    base_rank + penalty * disagreement (rank_std). Candidates no evaluator
    scored get effective_rank = inf and sort last.
    """
    _check(scores, directions)
    n = len(scores)
    m = len(directions)
    labels = list(range(n)) if ids is None else list(ids)
    if aggregate not in ("mean_rank", "median_rank"):
        raise ValueError("aggregate must be 'mean_rank' or 'median_rank'")
    if weights is not None and len(weights) != m:
        raise ValueError("weights length must match number of evaluators")

    ranks = rank_matrix(scores, directions)
    dis = disagreement(scores, directions, ids=labels)

    rows = []
    for i in range(n):
        present = [(e, ranks[i][e]) for e in range(m) if not is_missing(ranks[i][e])]
        coverage = len(present)
        if coverage == 0:
            rows.append({
                "id": labels[i], "effective_rank": float("inf"),
                "base_rank": None, "disagreement": 0.0,
                "coverage": 0, "n_evaluators": m,
            })
            continue
        if aggregate == "median_rank":
            vals = sorted(r for _, r in present)
            mid = len(vals) // 2
            base = vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0
        else:  # weighted mean rank
            if weights is None:
                base = sum(r for _, r in present) / coverage
            else:
                wsum = sum(weights[e] for e, _ in present)
                if wsum == 0:
                    base = sum(r for _, r in present) / coverage
                else:
                    base = sum(weights[e] * r for e, r in present) / wsum
        d = dis[i]["rank_std"]
        rows.append({
            "id": labels[i],
            "effective_rank": base + penalty * d,
            "base_rank": base,
            "disagreement": d,
            "coverage": coverage,
            "n_evaluators": m,
        })

    rows.sort(key=lambda row: row["effective_rank"])
    return rows
