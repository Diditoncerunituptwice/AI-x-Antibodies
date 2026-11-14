"""pipeline/stations/paratope_identity.py — score each candidate against the
anti-HER2 reference library.

Per candidate, finds the closest reference binder by sequence identity and
writes two Evidence rows (class `novelty`):
  - `paratope_identity_max`: max identity (0-1), source_ref = closest binder
  - `paratope_subdomain_hits`: how many reference binders in the closest match's
    subdomain are within +/-0.05 identity (context for the novelty score)

Identity strategy, in priority order:
  1. If candidate has CDRs and reference has CDRs, compare CDR-by-CDR
     (positional diff, Hamming over equal-length loops; unequal-length loops
     scored as len(diff)/max(len)). Score = weighted mean, weights H3=0.5,
     others 0.1 each (6 loops) or H3=0.6 for VHH (3 loops).
  2. Fallback: whole-chain Needleman-Wunsch-ish identity over VH (+VL for scFv).
     Pure Python, no libraries.

The hackathon novelty rule: identity > 0.70 against ANY known anti-HER2
binder means the paratope is not novel. filter.py rejects those as
reason_code='duplicate'.
"""
from __future__ import annotations

from pipeline.db import connect, ensure_metric, get_or_create_model, log_state

STATION = "algo.paratope_identity"
FAMILY = "algo-paratope"
VERSION = "0.1.0"


def _identity(a, b):
    """Simple sequence identity: aligned by position from N-term, divided by max len.
    No gaps, no alignment library — pragmatic for CDR comparison."""
    if not a or not b:
        return 0.0
    n = max(len(a), len(b))
    m = sum(1 for i in range(min(len(a), len(b))) if a[i] == b[i])
    return m / n


def _cdr_identity(c_cdrs, r_cdrs, vhh=False):
    """Weighted CDR identity. c_cdrs and r_cdrs each have {h1,h2,h3,[l1,l2,l3]}."""
    if vhh:
        weights = {"h1": 0.2, "h2": 0.2, "h3": 0.6}
    else:
        weights = {"h1": 0.1, "h2": 0.1, "h3": 0.5, "l1": 0.1, "l2": 0.1, "l3": 0.1}
    total, wsum = 0.0, 0.0
    for k, w in weights.items():
        ca, ra = c_cdrs.get(k), r_cdrs.get(k)
        if ca and ra:
            total += w * _identity(ca, ra)
            wsum += w
    return (total / wsum) if wsum > 0 else None


def _whole_identity(c_vh, c_vl, r_vh, r_vl):
    """Fallback: whole VH+VL identity. Average of VH and VL when both present."""
    scores = []
    if c_vh and r_vh:
        scores.append(_identity(c_vh, r_vh))
    if c_vl and r_vl:
        scores.append(_identity(c_vl, r_vl))
    return (sum(scores) / len(scores)) if scores else 0.0


def _slice_cdrs(seq, cdr1s, cdr1e, cdr2s, cdr2e, cdr3s, cdr3e):
    if seq is None or cdr1s is None:
        return {}
    return {"h1": seq[cdr1s - 1:cdr1e], "h2": seq[cdr2s - 1:cdr2e], "h3": seq[cdr3s - 1:cdr3e]}


def run(conn=None, verbose=True):
    conn = conn or connect()
    n_refs = conn.execute("SELECT COUNT(*) FROM reference_binder").fetchone()[0]
    if n_refs == 0:
        print("[paratope_identity] reference library is empty — "
              "run `python -m pipeline.import_reference_binders <file>` first")
        return 0

    model_id = get_or_create_model(conn, name=STATION, family=FAMILY,
                                   role="evaluator", version=VERSION)
    ensure_metric(conn, "paratope_identity_max", "novelty", "0-1",
                  "Max sequence identity against the anti-HER2 reference library.",
                  added_by=f"{STATION}@{VERSION}")
    ensure_metric(conn, "paratope_subdomain_hits", "novelty", "count",
                  "Number of reference binders in the closest match's subdomain within 0.05 identity.",
                  added_by=f"{STATION}@{VERSION}")

    refs = list(conn.execute(
        "SELECT binder_id, name, format, subdomain, vh_sequence, vl_sequence,"
        " cdr_h1, cdr_h2, cdr_h3, cdr_l1, cdr_l2, cdr_l3 FROM reference_binder"))

    rows = conn.execute(
        "SELECT c.candidate_id, c.format, c.sequence_h, c.sequence_l,"
        " c.cdr1_start, c.cdr1_end, c.cdr2_start, c.cdr2_end, c.cdr3_start, c.cdr3_end"
        " FROM candidate c"
        " WHERE NOT EXISTS (SELECT 1 FROM evidence e WHERE e.candidate_id=c.candidate_id"
        "                   AND e.model_id=? AND e.metric_name='paratope_identity_max')",
        (model_id,)).fetchall()

    n = 0
    for c in rows:
        is_vhh = (c["format"] == "vhh")
        c_cdrs_h = _slice_cdrs(c["sequence_h"], c["cdr1_start"], c["cdr1_end"],
                               c["cdr2_start"], c["cdr2_end"], c["cdr3_start"], c["cdr3_end"])
        best, best_score = None, -1.0
        per_ref = []
        for r in refs:
            r_cdrs = {"h1": r["cdr_h1"], "h2": r["cdr_h2"], "h3": r["cdr_h3"]}
            if not is_vhh:
                r_cdrs.update({"l1": r["cdr_l1"], "l2": r["cdr_l2"], "l3": r["cdr_l3"]})
            score = None
            if any(r_cdrs.values()) and c_cdrs_h:
                score = _cdr_identity(c_cdrs_h, r_cdrs, vhh=is_vhh)
            if score is None:
                score = _whole_identity(c["sequence_h"], c["sequence_l"],
                                        r["vh_sequence"], r["vl_sequence"])
            per_ref.append((r, score))
            if score > best_score:
                best, best_score = r, score

        # subdomain hits within 0.05
        hits = 0
        if best is not None and best["subdomain"]:
            for r, s in per_ref:
                if r["subdomain"] == best["subdomain"] and abs(s - best_score) <= 0.05:
                    hits += 1

        src = f"{best['binder_id']}:{best['subdomain']}" if best else None
        with conn:
            conn.execute(
                "INSERT INTO evidence (candidate_id, model_id, evidence_type, metric_name,"
                " value, source_ref) VALUES (?,?,'predicted','paratope_identity_max',?,?)",
                (c["candidate_id"], model_id, float(best_score), src))
            conn.execute(
                "INSERT INTO evidence (candidate_id, model_id, evidence_type, metric_name,"
                " value, source_ref) VALUES (?,?,'predicted','paratope_subdomain_hits',?,?)",
                (c["candidate_id"], model_id, float(hits), src))
            log_state(conn, c["candidate_id"], "scored", note=f"{STATION} v{VERSION}")
        n += 1
        if verbose:
            tag = "NOVEL" if best_score < 0.70 else "duplicate-like"
            print(f"  {c['candidate_id']} [{c['format']}]: max identity "
                  f"{best_score:.2f} vs {best['name'] if best else '-'} "
                  f"(subdomain {best['subdomain'] if best else '-'}) → {tag}")

    print(f"[paratope_identity] scored {n} candidate(s) against {len(refs)} reference binder(s)")
    return n
