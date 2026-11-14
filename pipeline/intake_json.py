"""pipeline/intake_json.py — ingest a generator handoff in JSON.

    python -m pipeline.intake_json data/incoming/B-001 [--force] [--target T-X]

Same contract as intake.py (CSV), different wrapper. The directory must hold
`candidates.json` (and optionally `manifest.json`).

candidates.json is a JSON array of objects, each with the same fields as a CSV
row. Minimum required per row: candidate_id, sequence_h (or 'sequence'),
model_name. Everything else optional. Extra keys ignored.

Example:
[
  {"candidate_id": "C-001", "format": "scfv",
   "sequence_h": "EVQLV...", "sequence_l": "DIQMT...",
   "model_name": "RFantibody", "seed": 42, "parent_id": "C-trastuzumab-ref"},
  {"candidate_id": "C-002", "format": "vhh",
   "sequence_h": "QVQLQ...", "model_name": "RFdiffusion"}
]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

from pipeline.db import connect, get_or_create_model
from pipeline.intake import (CDR_H, CDR_L, validate_row, sha256_of)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ingest a JSON handoff into the registry.")
    ap.add_argument("directory")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--target", default=None)
    ap.add_argument("--batch", default=None)
    ap.add_argument("--family", default=None)
    args = ap.parse_args(argv)

    d = pathlib.Path(args.directory)
    json_path = d / "candidates.json"
    man_path = d / "manifest.json"
    if not json_path.exists():
        print(f"ERROR: {json_path} not found"); return 1

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERROR: candidates.json is not valid JSON — {exc}"); return 1
    if isinstance(data, dict) and "candidates" in data:
        data = data["candidates"]      # accept {"candidates": [...]} too
    if not isinstance(data, list) or not data:
        print("ERROR: candidates.json must be a non-empty JSON array"); return 1

    manifest = json.loads(man_path.read_text(encoding="utf-8")) if man_path.exists() else None
    conn = connect()

    target_id = args.target or (manifest or {}).get("target_id")
    if not target_id:
        rows = conn.execute("SELECT target_id FROM target").fetchall()
        if len(rows) == 1:
            target_id = rows[0][0]
        else:
            print(f"ERROR: pass --target (registry holds {len(rows)} targets)."); return 1
    if not conn.execute("SELECT 1 FROM target WHERE target_id=?", (target_id,)).fetchone():
        print(f"ERROR: target {target_id!r} not in registry — run pipeline.seed_target first")
        return 1

    # validate every row with the same validator as the CSV path
    seen_ids, seen_pairs = set(), {}
    clean, skips = [], []
    for i, row in enumerate(data, start=1):
        if not isinstance(row, dict):
            skips.append((i, None, f"row {i} is not an object")); continue
        # coerce every value to a string the CSV validator understands
        srow = {k: ("" if v is None else str(v)) for k, v in row.items()}
        rec, err = validate_row(srow, i, seen_ids, seen_pairs)
        if err:
            skips.append((i, (srow.get("candidate_id") or "").strip() or None, err)); continue
        seen_ids.add(rec["candidate_id"])
        seen_pairs[(rec["sequence_h"], rec["sequence_l"] or "")] = rec["candidate_id"]
        clean.append(rec)

    for rec in list(clean):
        hit = conn.execute(
            "SELECT candidate_id FROM candidate WHERE candidate_id=? OR (sequence_h=? AND COALESCE(sequence_l,'')=COALESCE(?,''))",
            (rec["candidate_id"], rec["sequence_h"], rec["sequence_l"])).fetchone()
        if hit:
            clean.remove(rec)
            skips.append((None, rec["candidate_id"], f"already in registry as {hit[0]!r}"))

    if skips and not args.force:
        print(f"REJECTED {json_path} — {len(skips)} bad row(s), nothing ingested "
              f"(re-run with --force to ingest the {len(clean)} good rows):")
        for rn, c, w in skips:
            print(f"  {'row '+str(rn) if rn else 'pre-existing'}: {c or '-'} -> {w}")
        return 2
    if not clean:
        print("nothing to ingest"); return 2

    actual = sha256_of(json_path)
    file_sha, manifest_present = None, 0
    if manifest is not None:
        manifest_present = 1
        declared = manifest.get("json_sha256") or manifest.get("csv_sha256")  # either name
        if declared and declared != actual:
            print(f"ERROR: manifest sha256 mismatch. manifest={declared} actual={actual}")
            return 1
        file_sha = actual

    mman = (manifest or {}).get("model", {})
    model_name = mman.get("name") or clean[0]["model_name"]
    model_version = mman.get("version") or clean[0]["model_version"]
    family = args.family or mman.get("family") or model_name.lower()
    model_id = get_or_create_model(conn, name=model_name, family=family, role="generator",
                                   version=model_version, settings=mman.get("settings"))

    batch_id = args.batch or (manifest or {}).get("batch_id") or d.name
    if conn.execute("SELECT 1 FROM ingest_batch WHERE batch_id=?", (batch_id,)).fetchone():
        print(f"ERROR: batch_id {batch_id!r} already ingested — pass a new --batch"); return 1

    insert_cols = ("candidate_id", "format", "sequence_h", "sequence_l", "target_id",
                   "generator_model_id", "seed", "parent_id", "batch_id",
                   *CDR_H, *CDR_L, "cdr_source")
    try:
        with conn:
            conn.execute(
                "INSERT INTO ingest_batch (batch_id, generator_model_id, target_id, file_name,"
                " file_sha256, manifest_present, forced, n_rows_declared, n_rows_ingested, n_rows_skipped)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (batch_id, model_id, target_id, json_path.name, file_sha, manifest_present,
                 1 if args.force else 0, (manifest or {}).get("n_rows"), len(clean), len(skips)))
            for rec in clean:
                rec = dict(rec, target_id=target_id, generator_model_id=model_id, batch_id=batch_id)
                conn.execute(
                    f"INSERT INTO candidate ({','.join(insert_cols)}) "
                    f"VALUES ({','.join('?' * len(insert_cols))})",
                    tuple(rec.get(c) for c in insert_cols))
            for rn, c, w in skips:
                conn.execute(
                    "INSERT INTO ingest_skip (batch_id, row_number, candidate_id, reason)"
                    " VALUES (?,?,?,?)", (batch_id, rn or -1, c, w))
    except Exception as exc:
        print(f"ERROR: ingest failed, nothing written — {exc}"); return 1

    by_fmt = {}
    for rec in clean:
        by_fmt[rec["format"]] = by_fmt.get(rec["format"], 0) + 1
    print(f"ingested {len(clean)} candidate(s) from JSON as batch {batch_id} "
          f"({', '.join(f'{v} {k}' for k, v in by_fmt.items())}) "
          f"generator {model_name} v{model_version}, family {family!r}, target {target_id}")
    if manifest_present:
        print(f"  manifest verified, sha256 {actual[:16]}...")
    else:
        print("  NO MANIFEST — hash not verified (manifest_present=0)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
