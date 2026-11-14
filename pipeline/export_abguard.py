"""pipeline/export_abguard.py — registry -> the CSV the hackathon organisers' abguard expects.

One row per design:
  sequence_id,kind,heavy_sequence,light_sequence,design_description
kind: vhh | scfv | paired   (registry 'fab' -> 'paired')

Pre-checks mirror abguard's stated rejection rules, so bad rows are dropped HERE with a reason
(only standard 20 aa, <=250 residues, even number of cysteines). Antibody-domain recognition and
the novelty checks are abguard's own and are NOT reproduced here.

Selection (human choice stays human): by default every candidate that is not rejected.
  --ids C-1,C-2          only these candidate_ids
  --min-iptm 0.5         keep only candidates with independent mean ipTM >= value
  --out reports/team-N.csv

Usage: python -m pipeline.export_abguard --out reports/team-N.csv [--ids ...] [--min-iptm X]
"""
from __future__ import annotations

import argparse
import csv
import pathlib

from pipeline.db import REPO, connect

AA = set("ACDEFGHIKLMNPQRSTVWY")
KIND = {"vhh": "vhh", "scfv": "scfv", "fab": "paired"}
HEADER = ["sequence_id", "kind", "heavy_sequence", "light_sequence", "design_description"]


def problems(seq: str) -> list[str]:
    p = []
    if not seq or set(seq) - AA:
        p.append("non-standard residues")
    if len(seq) > 250:
        p.append("longer than 250")
    if seq.count("C") % 2:
        p.append("odd cysteines")
    return p


def run(out: pathlib.Path, ids=None, min_iptm=None, conn=None):
    conn = conn or connect()
    rows = conn.execute("SELECT * FROM v_registry ORDER BY candidate_id").fetchall()
    keep, dropped = [], []
    for r in rows:
        cid = r["candidate_id"]
        if ids and cid not in ids:
            continue
        if r["decision"] == "rejected":
            dropped.append((cid, "rejected in registry: %s" % r["reason_code"]))
            continue
        if min_iptm is not None and (r["iptm_mean_indep"] is None or r["iptm_mean_indep"] < min_iptm):
            dropped.append((cid, "ipTM below %s" % min_iptm))
            continue
        kind = KIND[r["format"]]
        h, l = r["sequence_h"], (r["sequence_l"] or "") if kind != "vhh" else ""
        bad = problems(h) + (problems(l) if l else [])
        if kind != "vhh" and not l:
            bad.append("missing light chain")
        if bad:
            dropped.append((cid, "; ".join(bad)))
            continue
        iptm = r["iptm_mean_indep"]
        desc = "%s v%s seed %s; ipTM %s (predicted, not measured)" % (
            r["generator"], r["generator_version"], r["generator_seed"],
            "n/a" if iptm is None else "%.2f" % iptm)
        keep.append([cid, kind, h, l, desc])
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        w.writerows(keep)
    print(f"[abguard] {out}: {len(keep)} rows written, {len(dropped)} dropped")
    for cid, why in dropped:
        print(f"  dropped {cid}: {why}")
    return len(keep)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--ids")
    ap.add_argument("--min-iptm", type=float)
    a = ap.parse_args()
    ids = set(a.ids.split(",")) if a.ids else None
    run(pathlib.Path(a.out) if pathlib.Path(a.out).is_absolute() else REPO / a.out, ids, a.min_iptm)
