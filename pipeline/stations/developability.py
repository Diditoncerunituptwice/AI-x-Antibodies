"""pipeline/stations/developability.py — REAL station. Sequence-only, no network.

Calls algo.scan_liabilities (D14: the pipeline never reimplements the scan) and
writes one Evidence row per metric. Also computes two cheap, genuinely
sequence-derived properties: net charge at pH 7 and GRAVY (Kyte-Doolittle).

Nothing here predicts binding. Every metric is `developability` class.
"""
from __future__ import annotations

from algo.liabilities import scan_liabilities, liability_counts
from pipeline.db import connect, ensure_metric, get_or_create_model, log_state

STATION = "algo.liabilities"
FAMILY = "algo-liabilities"      # set by hand (D8); independent of any protein model
VERSION = "0.1.0"

KINDS = ("deamidation_NG", "isomerization_DG", "fragmentation_DP", "oxidation_M",
         "oxidation_W", "unpaired_cysteine", "nglyc_sequon", "hydrophobic_patch",
         "positive_patch")

# Kyte-Doolittle hydropathy
_KD = {"A": 1.8, "R": -4.5, "N": -3.5, "D": -3.5, "C": 2.5, "Q": -3.5, "E": -3.5,
       "G": -0.4, "H": -3.2, "I": 4.5, "L": 3.8, "K": -3.9, "M": 1.9, "F": 2.8,
       "P": -1.6, "S": -0.8, "T": -0.7, "W": -0.9, "Y": -1.3, "V": 4.2}


def gravy(seq):
    return sum(_KD[c] for c in seq) / len(seq)


def net_charge_ph7(seq):
    """Crude net charge at pH 7: K+R positive, D+E negative, H half."""
    return (seq.count("K") + seq.count("R") + 0.5 * seq.count("H")
            - seq.count("D") - seq.count("E"))


def run(conn=None, verbose=True):
    conn = conn or connect()
    model_id = get_or_create_model(conn, name=STATION, family=FAMILY,
                                   role="evaluator", version=VERSION)
    for kind in KINDS:
        ensure_metric(conn, f"liab_{kind}", "developability", "count",
                      f"Count of {kind} liabilities found by {STATION}.",
                      added_by=f"{STATION}@{VERSION}")

    rows = conn.execute(
        "SELECT c.candidate_id, c.sequence, c.cdr1_start, c.cdr1_end, c.cdr2_start,"
        " c.cdr2_end, c.cdr3_start, c.cdr3_end FROM candidate c"
        " WHERE NOT EXISTS (SELECT 1 FROM evidence e WHERE e.candidate_id=c.candidate_id"
        "                   AND e.model_id=?)", (model_id,)).fetchall()

    n = 0
    for r in rows:
        spans = None
        if r["cdr1_start"] is not None:
            spans = [(r["cdr1_start"], r["cdr1_end"]), (r["cdr2_start"], r["cdr2_end"]),
                     (r["cdr3_start"], r["cdr3_end"])]
        findings = scan_liabilities(r["sequence"], cdr_spans=spans)
        counts = liability_counts(findings)

        vals = {"liability_motif_count": float(counts["total"]),
                "net_charge_ph7": float(net_charge_ph7(r["sequence"])),
                "gravy": float(gravy(r["sequence"]))}
        for kind in KINDS:
            vals[f"liab_{kind}"] = float(counts.get(kind, 0))

        with conn:
            for metric, value in vals.items():
                conn.execute(
                    "INSERT INTO evidence (candidate_id, model_id, evidence_type, metric_name,"
                    " value) VALUES (?,?,'predicted',?,?)",
                    (r["candidate_id"], model_id, metric, value))
            log_state(conn, r["candidate_id"], "scored", note=f"{STATION} v{VERSION}")
        n += 1
        if verbose:
            print(f"  {r['candidate_id']}: {counts['total']} liabilities "
                  f"({', '.join(k for k in KINDS if counts.get(k)) or 'none'})")

    print(f"[developability] scored {n} candidate(s); {len(rows) - n} skipped")
    return n
