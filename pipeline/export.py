"""pipeline/export.py — the tables the judges see.

Every number is a SELECT over evidence joined to model (Utgang §5 rule): if it
cannot be traced to an Evidence row, it does not go in the table.

Writes into reports/:
  registry.csv       one row per candidate, rejected included, with reason
  score_table.csv    candidate x metric, with how many independent families
                     produced it and the spread across them
  rejected.csv       every rejection with its reason and the triggering numbers
  provenance.csv     every model used, its role, family and version
"""
from __future__ import annotations

import csv
import pathlib

from pipeline.db import REPO, connect

OUT = REPO / "reports"


def _dump(path, rows, header=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = list(rows)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        if not rows:
            if header:
                csv.writer(fh).writerow(header)
            return 0
        w = csv.writer(fh)
        w.writerow(rows[0].keys())
        for r in rows:
            w.writerow([r[k] for k in r.keys()])
    return len(rows)


def run(conn=None):
    conn = conn or connect()
    OUT.mkdir(parents=True, exist_ok=True)

    n1 = _dump(OUT / "registry.csv", conn.execute("SELECT * FROM v_registry ORDER BY candidate_id"))
    n2 = _dump(OUT / "score_table.csv", conn.execute(
        "SELECT candidate_id, metric_name, metric_class, evidence_type,"
        " n_rows_all, n_independent_rows, n_independent_families,"
        " mean_independent, spread_independent, mean_all"
        " FROM v_metric_summary ORDER BY candidate_id, metric_class, metric_name"))
    n3 = _dump(OUT / "rejected.csv", conn.execute(
        "SELECT candidate_id, reason_code, rationale, decided_by, decision_authority"
        " FROM v_registry WHERE decision='rejected' ORDER BY reason_code, candidate_id"),
        header=["candidate_id", "reason_code", "rationale", "decided_by", "decision_authority"])
    n4 = _dump(OUT / "provenance.csv", conn.execute(
        "SELECT m.model_id, m.name, m.family, m.version, m.role, m.settings_json,"
        " (SELECT COUNT(*) FROM evidence e WHERE e.model_id=m.model_id) AS n_evidence"
        " FROM model m ORDER BY m.role, m.name"))

    print(f"[export] reports/registry.csv ({n1}) score_table.csv ({n2}) "
          f"rejected.csv ({n3}) provenance.csv ({n4})")

    counts = conn.execute(
        "SELECT current_state, COUNT(*) n FROM v_registry GROUP BY current_state"
    ).fetchall()
    print("  funnel: " + ", ".join(f"{r['current_state']}={r['n']}" for r in counts))
    mocks = conn.execute(
        "SELECT COUNT(*) FROM evidence e JOIN model m ON m.model_id=e.model_id"
        " WHERE m.name LIKE 'MOCK-%'").fetchone()[0]
    if mocks:
        print(f"  !! {mocks} evidence rows come from MOCK models — not real evidence")
    return n1
