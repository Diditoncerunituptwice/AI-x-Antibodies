"""pipeline/intake.py — read the generator handoff into the registry.

    python -m pipeline.intake data/incoming/B-001 [--force] [--target T-X]

THE HANDOFF CONTRACT (D4, Option A — deliberately permissive):
  candidates.csv  REQUIRED columns: candidate_id, sequence, model_name
                  OPTIONAL: model_version, seed, parent_id,
                            cdr1_start..cdr3_end (all six or none)
                  Extra columns are accepted and ignored.
  manifest.json   OPTIONAL. If present its sha256 is verified against the CSV
                  and model metadata is taken from it. If absent we build a
                  batch row from CLI/defaults and record manifest_present=0 so
                  the registry shows the hash was never verified.

ALL-OR-NOTHING by default: one bad row rejects the whole file and every error
is reported with its row number. --force ingests the good rows and logs every
skipped row to ingest_skip with a reason.

Length (80-160 aa) is enforced HERE, not in the scanner (D14).
Parameterised queries only: candidate ids and sequences come from outside the
pipeline's trust boundary, and string-built SQL is how injection happens.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pathlib
import re
import sys

from pipeline.db import connect, get_or_create_model, log_state

STANDARD_AA = set("ACDEFGHIKLMNPQRSTVWY")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{3,40}$")
MIN_LEN, MAX_LEN = 80, 160
CDR_COLS = ("cdr1_start", "cdr1_end", "cdr2_start", "cdr2_end", "cdr3_start", "cdr3_end")


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _int_or_none(raw):
    raw = (raw or "").strip()
    if raw == "":
        return None
    return int(raw)


def validate_row(row, rownum, seen_ids, seen_seqs):
    """Return (clean_dict, None) or (None, 'reason'). Pure-ish: only reads `seen_*`."""
    cid = (row.get("candidate_id") or "").strip()
    seq = (row.get("sequence") or "").strip().upper()
    mname = (row.get("model_name") or "").strip()

    if not cid:
        return None, "candidate_id missing"
    if not ID_RE.match(cid):
        return None, f"candidate_id {cid!r} must be 3-40 chars of [A-Za-z0-9_-]"
    if cid in seen_ids:
        return None, f"duplicate candidate_id {cid!r} within this file"
    if not seq:
        return None, "sequence missing"
    bad = sorted(set(seq) - STANDARD_AA)
    if bad:
        return None, f"sequence has non-standard characters {bad} (expect uppercase 20-AA)"
    if not (MIN_LEN <= len(seq) <= MAX_LEN):
        return None, f"sequence length {len(seq)} outside {MIN_LEN}-{MAX_LEN} aa"
    if seq in seen_seqs:
        return None, f"duplicate sequence (same as {seen_seqs[seq]}) within this file"
    if not mname:
        return None, "model_name missing"

    try:
        seed = _int_or_none(row.get("seed"))
    except ValueError:
        return None, f"seed {row.get('seed')!r} is not an integer"

    parent = (row.get("parent_id") or "").strip() or None
    if parent == cid:
        return None, "parent_id equals candidate_id"

    # CDRs: all six or none, and only kept when internally consistent.
    cdr = {}
    try:
        raw = {c: _int_or_none(row.get(c)) for c in CDR_COLS}
    except ValueError:
        raw = {c: None for c in CDR_COLS}
    present = [v is not None for v in raw.values()]
    if all(present):
        ok = (raw["cdr1_start"] >= 1
              and raw["cdr1_end"] >= raw["cdr1_start"] < raw["cdr2_start"]
              and raw["cdr1_end"] < raw["cdr2_start"]
              and raw["cdr2_end"] >= raw["cdr2_start"]
              and raw["cdr2_end"] < raw["cdr3_start"]
              and raw["cdr3_end"] >= raw["cdr3_start"]
              and raw["cdr3_end"] <= len(seq))
        cdr = raw if ok else {c: None for c in CDR_COLS}
    else:
        cdr = {c: None for c in CDR_COLS}

    return {
        "candidate_id": cid, "sequence": seq, "model_name": mname,
        "model_version": (row.get("model_version") or "").strip() or "unspecified",
        "seed": seed, "parent_id": parent,
        "cdr_source": "handoff" if any(v is not None for v in cdr.values()) else None,
        **cdr,
    }, None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ingest a generator handoff into the registry.")
    ap.add_argument("directory", help="folder holding candidates.csv (+ optional manifest.json)")
    ap.add_argument("--force", action="store_true",
                    help="ingest good rows and skip bad ones (every skip is logged)")
    ap.add_argument("--target", default=None, help="target_id (default: the only one present)")
    ap.add_argument("--batch", default=None, help="batch_id (default: folder name)")
    ap.add_argument("--family", default=None,
                    help="model family, set BY HAND (D8). Default: lowercased model name.")
    args = ap.parse_args(argv)

    d = pathlib.Path(args.directory)
    csv_path = d / "candidates.csv"
    man_path = d / "manifest.json"
    if not csv_path.exists():
        print(f"ERROR: {csv_path} not found")
        return 1

    manifest = json.loads(man_path.read_text(encoding="utf-8")) if man_path.exists() else None
    conn = connect()

    # --- target -------------------------------------------------------------
    target_id = args.target or (manifest or {}).get("target_id")
    if not target_id:
        rows = conn.execute("SELECT target_id FROM target").fetchall()
        if len(rows) == 1:
            target_id = rows[0][0]
        else:
            print(f"ERROR: pass --target (registry holds {len(rows)} targets). "
                  f"Run `python -m pipeline.seed_target` first.")
            return 1
    if not conn.execute("SELECT 1 FROM target WHERE target_id=?", (target_id,)).fetchone():
        print(f"ERROR: target {target_id!r} not in registry — run pipeline.seed_target first")
        return 1

    # --- read + validate ----------------------------------------------------
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    if not rows:
        print("ERROR: candidates.csv has no data rows")
        return 1

    seen_ids, seen_seqs = set(), {}
    clean, skips = [], []
    for i, row in enumerate(rows, start=2):          # row 1 is the header
        rec, err = validate_row(row, i, seen_ids, seen_seqs)
        if err:
            skips.append((i, (row.get("candidate_id") or "").strip() or None, err))
            continue
        seen_ids.add(rec["candidate_id"])
        seen_seqs[rec["sequence"]] = rec["candidate_id"]
        clean.append(rec)

    # collisions with what is already in the registry
    for rec in list(clean):
        hit = conn.execute(
            "SELECT candidate_id FROM candidate WHERE candidate_id=? OR sequence=?",
            (rec["candidate_id"], rec["sequence"]),
        ).fetchone()
        if hit:
            clean.remove(rec)
            skips.append((None, rec["candidate_id"], f"already in registry as {hit[0]!r}"))

    if skips and not args.force:
        print(f"REJECTED {csv_path} — {len(skips)} bad row(s), nothing ingested "
              f"(re-run with --force to ingest the {len(clean)} good rows):")
        for rownum, cid, why in skips:
            where = f"row {rownum}" if rownum else "pre-existing"
            print(f"  {where}: {cid or '-'} -> {why}")
        return 2
    if not clean:
        print("nothing to ingest (all rows skipped)")
        return 2

    # --- manifest integrity -------------------------------------------------
    actual = sha256_of(csv_path)
    file_sha, manifest_present = None, 0
    if manifest is not None:
        manifest_present = 1
        declared = manifest.get("csv_sha256")
        if declared and declared != actual:
            print(f"ERROR: manifest csv_sha256 mismatch — file may be truncated or edited.\n"
                  f"  manifest: {declared}\n  actual:   {actual}")
            return 1
        file_sha = actual

    # --- model + batch ------------------------------------------------------
    mman = (manifest or {}).get("model", {})
    model_name = mman.get("name") or clean[0]["model_name"]
    model_version = mman.get("version") or clean[0]["model_version"]
    family = args.family or mman.get("family") or model_name.lower()
    model_id = get_or_create_model(conn, name=model_name, family=family, role="generator",
                                   version=model_version, settings=mman.get("settings"))

    batch_id = args.batch or (manifest or {}).get("batch_id") or d.name
    if conn.execute("SELECT 1 FROM ingest_batch WHERE batch_id=?", (batch_id,)).fetchone():
        print(f"ERROR: batch_id {batch_id!r} already ingested — pass a new --batch")
        return 1

    # --- insert, one transaction -------------------------------------------
    try:
        with conn:
            conn.execute(
                "INSERT INTO ingest_batch (batch_id, generator_model_id, target_id, file_name,"
                " file_sha256, manifest_present, forced, n_rows_declared, n_rows_ingested,"
                " n_rows_skipped) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (batch_id, model_id, target_id, csv_path.name, file_sha, manifest_present,
                 1 if args.force else 0, (manifest or {}).get("n_rows"), len(clean), len(skips)),
            )
            for rec in clean:
                conn.execute(
                    "INSERT INTO candidate (candidate_id, sequence, target_id, generator_model_id,"
                    " seed, parent_id, batch_id, cdr1_start, cdr1_end, cdr2_start, cdr2_end,"
                    " cdr3_start, cdr3_end, cdr_source)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rec["candidate_id"], rec["sequence"], target_id, model_id, rec["seed"],
                     rec["parent_id"], batch_id, rec["cdr1_start"], rec["cdr1_end"],
                     rec["cdr2_start"], rec["cdr2_end"], rec["cdr3_start"], rec["cdr3_end"],
                     rec["cdr_source"]),
                )
            for rownum, cid, why in skips:
                conn.execute(
                    "INSERT INTO ingest_skip (batch_id, row_number, candidate_id, reason)"
                    " VALUES (?,?,?,?)", (batch_id, rownum or -1, cid, why),
                )
    except Exception as exc:                                   # noqa: BLE001
        print(f"ERROR: ingest failed, nothing written — {exc}")
        return 1

    print(f"ingested {len(clean)} candidate(s) as batch {batch_id} "
          f"(generator {model_name} v{model_version}, family {family!r}, target {target_id})")
    if manifest_present:
        print(f"  manifest verified, sha256 {actual[:16]}...")
    else:
        print("  NO MANIFEST — hash not verified (recorded as manifest_present=0)")
    if skips:
        print(f"  skipped {len(skips)} row(s):")
        for rownum, cid, why in skips:
            where = f"row {rownum}" if rownum and rownum > 0 else "pre-existing"
            print(f"    {where}: {cid or '-'} -> {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
