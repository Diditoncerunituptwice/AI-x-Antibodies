"""algo/tests/test_methods.py — tests for T5(2-6): Pareto, consensus /
disagreement, diversity selection, sensitivity.

Standalone: `python3 algo/tests/test_methods.py`  (or via pytest).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from pareto import pareto_front, pareto_layers, dominates          # noqa: E402
from aggregation import rank_matrix, disagreement, consensus       # noqa: E402
from diversity import select_diverse, kmer_jaccard_distance        # noqa: E402
from sensitivity import sensitivity, composite_scores, top_k       # noqa: E402


# ---------------------------------------------------------------------------
# T5.2 Pareto
# ---------------------------------------------------------------------------

def test_pareto_basic_maxmax():
    objs = [[2, 1], [1, 2], [1, 1], [3, 3]]
    front = set(pareto_front(objs, ["max", "max"], ids=["A", "B", "C", "D"]))
    assert front == {"D"}, front


def test_pareto_tradeoff_front():
    objs = [[2, 1], [1, 2], [1.5, 1.5]]
    front = set(pareto_front(objs, ["max", "max"], ids=["A", "B", "C"]))
    assert front == {"A", "B", "C"}, front


def test_pareto_mixed_directions():
    objs = [[0.9, 3], [0.8, 1], [0.7, 5]]
    front = set(pareto_front(objs, ["max", "min"], ids=["A", "B", "C"]))
    assert front == {"A", "B"}, front


def test_pareto_duplicates_both_kept():
    objs = [[1, 1], [1, 1]]
    front = pareto_front(objs, ["max", "max"], ids=["A", "B"])
    assert set(front) == {"A", "B"}


def test_pareto_layers_peel():
    objs = [[3, 3], [2, 2], [1, 1]]
    layers = pareto_layers(objs, ["max", "max"], ids=["A", "B", "C"])
    assert layers == [["A"], ["B"], ["C"]], layers


def test_pareto_raises_on_missing():
    try:
        pareto_front([[1, None]], ["max", "max"])
    except ValueError:
        return
    raise AssertionError("should raise on missing objective")


def test_dominates_predicate():
    assert dominates([2, 2], [1, 1])
    assert dominates([2, 1], [1, 1])
    assert not dominates([2, 0], [1, 1])
    assert not dominates([1, 1], [1, 1])


# ---------------------------------------------------------------------------
# T5.3 / T5.4 consensus + disagreement
# ---------------------------------------------------------------------------

def test_rank_matrix_ties_and_direction():
    scores = [[10, 1], [10, 2], [5, 3]]
    ranks = rank_matrix(scores, ["max", "min"])
    assert ranks[0] == [1.5, 1.0]
    assert ranks[2] == [3.0, 3.0]


def test_disagreement_agree_vs_split():
    scores = [
        [100, 100],
        [90, 10],
        [10, 90],
    ]
    d = disagreement(scores, ["max", "max"])
    assert d[0]["rank_std"] == 0.0
    assert d[1]["rank_std"] > d[0]["rank_std"]
    assert d[2]["rank_std"] > d[0]["rank_std"]


def test_consensus_penalizes_disagreement():
    scores = [
        [100, 100],
        [1, 1],
        [100, 1],
        [1, 100],
    ]
    res = consensus(scores, ["max", "max"], ids=["A", "B", "C", "D"], penalty=2.0)
    order = [r["id"] for r in res]
    assert order[0] == "A"
    byid = {r["id"]: r for r in res}
    assert byid["C"]["effective_rank"] > byid["C"]["base_rank"]
    assert byid["C"]["disagreement"] > 0


def test_consensus_missing_evaluator_coverage():
    scores = [[10, None], [8, 5], [6, 9]]
    res = consensus(scores, ["max", "max"], ids=["A", "B", "C"])
    byid = {r["id"]: r for r in res}
    assert byid["A"]["coverage"] == 1
    assert byid["B"]["coverage"] == 2


def test_consensus_unscored_sorts_last():
    scores = [[None, None], [5, 5]]
    res = consensus(scores, ["max", "max"], ids=["A", "B"])
    assert res[-1]["id"] == "A"
    assert res[-1]["effective_rank"] == float("inf")


# ---------------------------------------------------------------------------
# T5.5 diversity
# ---------------------------------------------------------------------------

def test_kmer_distance_bounds():
    assert kmer_jaccard_distance("ACDEFG", "ACDEFG") == 0.0
    assert kmer_jaccard_distance("AAAA", "KRKR", k=2) == 1.0


def test_select_diverse_avoids_near_duplicates():
    ids = ["A", "Aprime", "B"]
    seqs = [
        "ACDEFGHIKLMNPQRSTVWY",
        "ACDEFGHIKLMNPQRSTVWY",
        "WYTVSRQPNMLKIHGFEDCA",
    ]
    quality = [1.0, 0.95, 0.5]
    picks = [p["id"] for p in select_diverse(ids, quality, sequences=seqs,
                                             budget=2, lam=0.3)]
    assert picks[0] == "A"
    assert picks[1] == "B"


def test_select_diverse_pure_quality_when_lam_1():
    ids = ["A", "Aprime", "B"]
    seqs = ["ACDEFGHIKL", "ACDEFGHIKM", "WYTVSRQPNM"]
    quality = [1.0, 0.95, 0.1]
    picks = [p["id"] for p in select_diverse(ids, quality, sequences=seqs,
                                             budget=2, lam=1.0)]
    assert picks == ["A", "Aprime"]


def test_select_diverse_min_distance_floor():
    ids = ["A", "Aprime"]
    seqs = ["ACDEFGHIKL", "ACDEFGHIKM"]
    quality = [1.0, 0.9]
    picks = select_diverse(ids, quality, sequences=seqs, budget=2,
                           lam=0.5, min_distance=0.9)
    assert len(picks) == 1


def test_select_diverse_budget_and_distance_matrix():
    ids = [0, 1, 2, 3]
    quality = [0.9, 0.8, 0.7, 0.6]
    dm = [[0 if i == j else 1 for j in range(4)] for i in range(4)]
    picks = select_diverse(ids, quality, distance_matrix=dm, budget=3, lam=0.5)
    assert len(picks) == 3
    assert picks[0]["id"] == 0


# ---------------------------------------------------------------------------
# T5.6 sensitivity
# ---------------------------------------------------------------------------

def test_sensitivity_stable_set_scores_high():
    scores = [[1.0, 1.0, 1.0], [0.2, 0.2, 0.2], [0.1, 0.1, 0.1]]
    res = sensitivity(scores, ["max", "max", "max"], [1, 1, 1],
                      ids=["A", "B", "C"], k=1)
    assert res["base_top_k"] == ["A"]
    assert res["selection_frequency"]["A"] == 1.0
    assert res["mean_jaccard_stability"] == 1.0


def test_sensitivity_fragile_set_scores_lower():
    scores = [
        [1.0, 0.0],
        [0.0, 1.0],
    ]
    res = sensitivity(scores, ["max", "max"], [0.55, 0.45],
                      ids=["A", "B"], k=1, delta=0.5)
    assert res["mean_jaccard_stability"] < 1.0
    assert res["selection_frequency"]["A"] < 1.0


def test_sensitivity_mc_is_reproducible():
    scores = [[1.0, 0.2], [0.2, 1.0], [0.5, 0.5]]
    kw = dict(scores=scores, directions=["max", "max"], weights=[0.5, 0.5],
              ids=["A", "B", "C"], k=2, method="mc", n_samples=100, seed=7)
    a = sensitivity(**kw)
    b = sensitivity(**kw)
    assert a == b


def test_composite_and_topk_helpers():
    norm = [[1.0, 0.0], [0.0, 1.0], [0.5, 0.5]]
    comp = composite_scores(norm, [0.7, 0.3])
    assert comp[0] == 0.7 and comp[1] == 0.3
    assert top_k(["A", "B", "C"], comp, 1) == ["A"]


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
