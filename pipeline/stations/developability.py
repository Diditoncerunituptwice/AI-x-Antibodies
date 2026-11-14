"""pipeline/stations/developability.py — REAL station. Sequence-only, no network.

Per-chain scan: VHH scans sequence_h with expected_cys_pairs=1; scFv/Fab scans
EACH chain separately, each with expected_cys_pairs=1, then merges counts. This
prevents a false positive where cross-chain cysteines look 'unpaired' in a
concatenated sequence (critical for a two-chain target like trastuzumab).

Writes one Evidence row per metric per candidate. Everything is `developability`
class — nothing here predicts binding.
"""
from __future__ import annotations

from algo.liabilities import scan_liabilities, liability_counts, LiabilityConfig
from pipeline.db import connect, ensure_metric, get_or_create_model, log_state

STATION = "algo.liabilities"
FAMILY = "algo-liabilities"
VERSION = "0.2.0"          # bumped — multi-chain

KINDS = ("deamidation_NG", "isomerization_DG", "fragmentation_DP", "oxidation_M",
         "oxidation_W", "unpaired_cysteine", "nglyc_sequon", "hydrophobic_patch",
         "positive_patch")

_KD = {"A": 1.8, "R": -4.5, "N": -3.5, "D": -3.5, "C": 2.5, "Q": -3.5, "E": -3.5,
       "G": -0.4, "H": -3.2, "I": 4.5, "L": 3.8, "K": -3.9, "M": 1.9, "F": 2.8,
       "P": -1.6, "S": -0.8, "T": -0.7, "W": -0.9, "Y": -1.3, "V": 4.2}


def gravy(seq):
    return sum(_KD[c] for c in seq) / len(seq)


def net_charge_ph7(seq):
    return (seq.count("K") + seq.count("R") + 0.5 * seq.count("H")
            - seq.count("D") - seq.count("E"))


def _scan_one_chain(seq, cdr_spans):
    """Per-chain scan with VHH/VH/VL assumption: ONE canonical disulfide."""
    cfg = LiabilityConfig(expected_cys_pairs=1)
    return scan_liabilities(seq, cdr_spans=cdr_spans, config=cfg)


def run(conn=None, verbose=True):
    conn = conn or connect()
    model_id = get_or_create_model(conn, name=STATION, family=FAMILY,
                                   role="evaluator", version=VERSION)
    for kind in KINDS:
        ensure_metric(conn, f"liab_{kind}", "developability", "count",
                      f"Count of {kind} liabilities found by {STATION}.",
                      added_by=f"{STATION}@{VERSION}")
    # per-chain breakdowns (nice-to-have context, same class)
    for ch in ("h", "l"):
        ensure_metric(conn, f"liability_motif_count_{ch}", "developability", "count",
                      f"Liability count on chain {ch.upper()} (per-chain, VHH leaves L=0).",
                      added_by=f"{STATION}@{VERSION}")

    rows = conn.execute(
        "SELECT c.candidate_id, c.format, c.sequence_h, c.sequence_l,"
        " c.cdr1_start, c.cdr1_end, c.cdr2_start, c.cdr2_end, c.cdr3_start, c.cdr3_end,"
        " c.cdr_l1_start, c.cdr_l1_end, c.cdr_l2_start, c.cdr_l2_end, c.cdr_l3_start, c.cdr_l3_end"
        " FROM candidate c"
        " WHERE NOT EXISTS (SELECT 1 FROM evidence e WHERE e.candidate_id=c.candidate_id"
        "                   AND e.model_id=?)", (model_id,)).fetchall()

    n = 0
    for r in rows:
        h_spans = None
        if r["cdr1_start"] is not None:
            h_spans = [(r["cdr1_start"], r["cdr1_end"]), (r["cdr2_start"], r["cdr2_end"]),
                       (r["cdr3_start"], r["cdr3_end"])]
        h_findings = _scan_one_chain(r["sequence_h"], h_spans)
        h_counts = liability_counts(h_findings)

        if r["format"] in ("scfv", "fab") and r["sequence_l"]:
            l_spans = None
            if r["cdr_l1_start"] is not None:
                l_spans = [(r["cdr_l1_start"], r["cdr_l1_end"]), (r["cdr_l2_start"], r["cdr_l2_end"]),
                           (r["cdr_l3_start"], r["cdr_l3_end"])]
            l_findings = _scan_one_chain(r["sequence_l"], l_spans)
            l_counts = liability_counts(l_findings)
        else:
            l_findings, l_counts = [], {"total": 0}

        merged = {"total": h_counts["total"] + l_counts["total"]}
        for kind in KINDS:
            merged[kind] = h_counts.get(kind, 0) + l_counts.get(kind, 0)

        # charge/gravy across H+L concatenated (reasonable physical proxy)
        full = (r["sequence_h"] or "") + (r["sequence_l"] or "")

        vals = {"liability_motif_count": float(merged["total"]),
                "liability_motif_count_h": float(h_counts["total"]),
                "liability_motif_count_l": float(l_counts["total"]),
                "net_charge_ph7": float(net_charge_ph7(full)),
                "gravy": float(gravy(full))}
        for kind in KINDS:
            vals[f"liab_{kind}"] = float(merged.get(kind, 0))

        with conn:
            for metric, value in vals.items():
                conn.execute(
                    "INSERT INTO evidence (candidate_id, model_id, evidence_type, metric_name, value)"
                    " VALUES (?,?,'predicted',?,?)",
                    (r["candidate_id"], model_id, metric, value))
            log_state(conn, r["candidate_id"], "scored", note=f"{STATION} v{VERSION}")
        n += 1
        if verbose:
            tag = f"H={h_counts['total']}" + (f" L={l_counts['total']}" if r["format"] != "vhh" else "")
            kinds = [k for k in KINDS if merged.get(k)]
            print(f"  {r['candidate_id']} [{r['format']}]: {merged['total']} liabilities ({tag}) "
                  f"— {', '.join(kinds) or 'none'}")

    print(f"[developability] scored {n} candidate(s); {len(rows) - n} skipped")
    return n
