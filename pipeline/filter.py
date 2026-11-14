"""pipeline/filter.py — hard filters. Writes RECOMMENDED decisions only.

A script can recommend; only a human can authorize (D6). Rejections always carry
a reason_code (D7) and are LINKED to the evidence rows that caused them, so every
rejection in the final report traces to a number.

Nothing is ever deleted — rejected candidates stay in the registry with the reason.

Thresholds live in FILTERS and are meant to be tuned on the day once you see the
real score distributions. Keep them conservative: at this stage you are removing
candidates that are clearly broken, not picking winners.
"""
from __future__ import annotations

from pipeline.db import connect, log_state

ACTOR = "pipeline.filter@v0.1"

# metric, comparison, threshold, reason_code, human-readable rationale
FILTERS = [
    ("liab_unpaired_cysteine", ">=", 1.0, "developability",
     "free cysteine beyond the canonical pair: aggregation / disulfide scrambling risk"),
    ("liability_motif_count", ">", 8.0, "developability",
     "liability motif load above the cut-off"),
    ("iptm", "<", 0.30, "low_confidence",
     "interface confidence below the cut-off across independent evaluators"),
]

_OPS = {">=": lambda a, b: a >= b, ">": lambda a, b: a > b,
        "<=": lambda a, b: a <= b, "<": lambda a, b: a < b}


def run(conn=None, verbose=True):
    conn = conn or connect()

    # Only judge candidates that have been scored and have no decision yet.
    cands = conn.execute(
        "SELECT c.candidate_id FROM candidate c"
        " LEFT JOIN v_current_decision d ON d.candidate_id = c.candidate_id"
        " WHERE d.decision_id IS NULL"
        "   AND EXISTS (SELECT 1 FROM evidence e WHERE e.candidate_id = c.candidate_id)"
    ).fetchall()

    rejected = shortlisted = 0
    for (cid,) in cands:
        summary = {r["metric_name"]: r for r in conn.execute(
            "SELECT metric_name, mean_independent, mean_all FROM v_metric_summary"
            " WHERE candidate_id=?", (cid,))}

        hits = []
        for metric, op, thr, code, why in FILTERS:
            row = summary.get(metric)
            if row is None:
                continue                       # metric absent -> cannot judge on it
            val = row["mean_independent"]
            if val is None:
                val = row["mean_all"]          # fall back when nothing is independent
            if val is None:
                continue
            if _OPS[op](val, thr):
                hits.append((metric, val, op, thr, code, why))

        with conn:
            if hits:
                metric, val, op, thr, code, why = hits[0]
                detail = "; ".join(f"{m}={v:.3g} {o} {t:g}" for m, v, o, t, _, _ in hits)
                cur = conn.execute(
                    "INSERT INTO decision (candidate_id, outcome, authority, actor_kind, actor,"
                    " reason_code, rationale) VALUES (?,'rejected','recommended','script',?,?,?)",
                    (cid, ACTOR, code, f"{why} ({detail})"))
                did = cur.lastrowid
                # link the evidence rows that triggered it
                for m, *_ in hits:
                    for (eid,) in conn.execute(
                            "SELECT evidence_id FROM evidence WHERE candidate_id=? AND metric_name=?",
                            (cid, m)):
                        conn.execute(
                            "INSERT OR IGNORE INTO decision_evidence (decision_id, evidence_id,"
                            " direction) VALUES (?,?,'against')", (did, eid))
                log_state(conn, cid, "rejected", note=code)
                rejected += 1
                if verbose:
                    print(f"  REJECT {cid}: {detail}")
            else:
                conn.execute(
                    "INSERT INTO decision (candidate_id, outcome, authority, actor_kind, actor,"
                    " rationale) VALUES (?,'shortlisted','recommended','script',?,?)",
                    (cid, ACTOR, "passed all hard filters; carried to multi-objective ranking"))
                log_state(conn, cid, "shortlisted", note="passed hard filters")
                shortlisted += 1

    print(f"[filter] {shortlisted} shortlisted, {rejected} rejected "
          f"(rejections keep their reason and evidence links)")
    return shortlisted, rejected
