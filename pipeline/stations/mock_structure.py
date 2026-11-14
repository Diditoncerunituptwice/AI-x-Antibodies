"""pipeline/stations/mock_structure.py — *** MOCK. NOT REAL EVIDENCE. ***

Stand-in structural evaluators so the whole line runs end-to-end BEFORE the real
tools (OpenFold/Apheris, Boltz, JapanFold, Nebius) are wired in. Values are a
deterministic hash of the sequence — they mean NOTHING about binding.

Every model it registers is named MOCK-* so no fake number can ever be mistaken
for real evidence in the registry or a score table. DELETE THIS STATION, or stop
calling it, the moment real evaluators are connected.

Two separate families (mock-folda / mock-foldb) so the independence flag,
cross-model disagreement and the uncertainty columns all exercise properly.
"""
from __future__ import annotations

import hashlib

from pipeline.db import connect, get_or_create_model, log_state

MOCKS = (("MOCK-FoldA", "mock-folda"), ("MOCK-FoldB", "mock-foldb"))
VERSION = "0.0.0-mock"
METRICS = ("iptm", "ipsae", "plddt_mean")


def _pseudo(seq, model_name, metric, lo, hi):
    """Deterministic pseudo-value in [lo, hi] from sha256(seq|model|metric)."""
    h = hashlib.sha256(f"{seq}|{model_name}|{metric}".encode()).digest()
    frac = int.from_bytes(h[:4], "big") / 0xFFFFFFFF
    return lo + frac * (hi - lo)


def run(conn=None, verbose=False):
    conn = conn or connect()
    print("  *** MOCK structural evaluators — values are fake, for plumbing only ***")
    total = 0
    for name, family in MOCKS:
        model_id = get_or_create_model(conn, name=name, family=family,
                                       role="evaluator", version=VERSION)
        rows = conn.execute("""
            SELECT c.candidate_id,
                   IFNULL(c.sequence_h,'') || IFNULL(c.sequence_l,'') AS sequence
              FROM candidate c
             WHERE NOT EXISTS (SELECT 1 FROM evidence e
                                WHERE e.candidate_id = c.candidate_id
                                  AND e.model_id = ?)
        """, (model_id,)).fetchall()
        with conn:
            for r in rows:
                for metric, lo, hi in (("iptm", 0.2, 0.95), ("ipsae", 0.15, 0.9),
                                       ("plddt_mean", 55.0, 95.0)):
                    conn.execute(
                        "INSERT INTO evidence (candidate_id, model_id, evidence_type,"
                        " metric_name, value, run_seed) VALUES (?,?,'predicted',?,?,0)",
                        (r["candidate_id"], model_id, metric,
                         _pseudo(r["sequence"], name, metric, lo, hi)))
                log_state(conn, r["candidate_id"], "scored", note=f"{name} (MOCK)")
        total += len(rows)
        print(f"  [{name}] scored {len(rows)} candidate(s)")
    return total
