"""pipeline/intake.py — read the generator handoff into the registry.

    python -m pipeline.intake data/incoming/B-001 [--force] [--target T-X]

HANDOFF CONTRACT (D4, spec v0.1.2):
  candidates.csv  REQUIRED columns: candidate_id, sequence_h (or 'sequence'), model_name
                  OPTIONAL:
                    sequence_l         -> presence makes it scFv (two-chain)
                    format             -> 'vhh' / 'scfv' / 'fab' (override auto)
                    model_version, seed, parent_id
                    cdr1_start..cdr3_end          (H chain / VHH, all six or none)
                    cdr_l1_start..cdr_l3_end      (L chain, all six or none)
                  Extra columns are accepted and ignored.
  manifest.json   OPTIONAL. If present its csv_sha256 is verified.

Format auto-detect:
  - `format` column honoured if present
  - else `sequence_l` present -> 'scfv'; absent -> 'vhh'

Length: sequence_h 80-160; sequence_l 80-140. Enforced at intake (D14), not in algo.
All-or-nothing by default; --force partial-ingests and logs every skip.
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
H_MIN, H_MAX = 80, 160
L_MIN, L_MAX = 80, 140
CDR_H = ("cdr1_start", "cdr1_end", "cdr2_start", "cdr2_end", "cdr3_start", "cdr3_end")
CDR_L = ("cdr_l1_start", "cdr_l1_end", "cdr_l2_start", "cdr_l2_end", "cdr_l3_start", "cdr_l3_end")


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _int_or_none(raw):
    raw = (raw or "").strip()
    return None if raw == "" else int(raw)


def _check_seq(seq, lo, hi, name):
    if not seq:
        return f"{name} missing"
    bad = sorted(set(seq) - STANDARD_AA)
    if bad:
        return f"{name} has non-standard characters {bad} (uppercase 20-AA)"
    if not (lo <= len(seq) <= hi):
        return f"{name} length {len(seq)} outside {lo}-{hi} aa"
    return None


def _cdr_block(row, cols, seq_len):
    """Returns (dict, ok). dict has all cols; ok says whether they're internally valid."""
    try:
        raw = {c: _int_or_none(row.get(c)) for c in cols}
    except ValueError:
        return {c: None for c in cols}, False
    present = [v is not None for v in raw.values()]
    if not any(present):
        return {c: None for c in cols}, True  # empty is fine
    if not all(present):
        return {c: None for c in cols}, False  # partial is dropped silently
    a = raw[cols[0]], raw[cols[1]], raw[cols[2]], raw[cols[3]], raw[cols[4]], raw[cols[5]]
    ok = (a[0] >= 1 and a[1] >= a[0] < a[2] and a[1] < a[2]
          and a[3] >= a[2] and a[3] < a[4] and a[5] >= a[4] and a[5] <= seq_len)
    return (raw, True) if ok else ({c: None for c in cols}, False)


def validate_row(row, rownum, seen_ids, seen_pairs):
    cid = (row.get("candidate_id") or "").strip()
    seq_h = (row.get("sequence_h") or row.get("sequence") or "").strip().upper()
    seq_l_raw = (row.get("sequence_l") or "").strip().upper() or None
    mname = (row.get("model_name") or "").strip()

    if not cid:
        return None, "candidate_id missing"
    if not ID_RE.match(cid):
        return None, f"candidate_id {cid!r} must be 3-40 chars of [A-Za-z0-9_-]"
    if cid in seen_ids:
        return None, f"duplicate candidate_id {cid!r} within this file"
    err = _check_seq(seq_h, H_MIN, H_MAX, "sequence_h")
    if err:
        return None, err
    if seq_l_raw is not None:
        err = _check_seq(seq_l_raw, L_MIN, L_MAX, "sequence_l")
        if err:
            return None, err
    pair_key = (seq_h, seq_l_raw or "")
    if pair_key in seen_pairs:
        return None, f"duplicate (sequence_h, sequence_l) within this file (same as {seen_pairs[pair_key]})"
    if not mname:
        return None, "model_name missing"

    fmt_raw = (row.get("format") or "").strip().lower() or None
    if fmt_raw and fmt_raw not in ("vhh", "scfv", "fab"):
        return None, f"format {fmt_raw!r} not in vhh/scfv/fab"
    fmt = fmt_raw or ("scfv" if seq_l_raw else "vhh")
    if fmt == "vhh" and seq_l_raw:
        return None, "format=vhh but sequence_l present"
    if fmt in ("scfv", "fab") and not seq_l_raw:
        return None, f"format={fmt} but sequence_l missing"

    try:
        seed = _int_or_none(row.get("seed"))
    except ValueError:
        return None, f"seed {row.get('seed')!r} is not an integer"

    parent = (row.get("parent_id") or "").strip() or None
    if parent == cid:
        return None, "parent_id equals candidate_id"

    cdr_h, _ = _cdr_block(row, CDR_H, len(seq_h))
    cdr_l = {c: None for c in CDR_L}
    if seq_l_raw:
        cdr_l, _ = _cdr_block(row, CDR_L, len(seq_l_raw))

    rec = {
        "candidate_id": cid, "format": fmt,
        "sequence_h": seq_h, "sequence_l": seq_l_raw,
        "model_name": mname,
        "model_version": (row.get("model_version") or "").strip() or "unspecified",
        "seed": seed, "parent_id": parent,
        "cdr_source": "handoff" if any(cdr_h[c] is not None for c in CDR_H) or any(cdr_l[c] is not None for c in CDR_L) else None,
    }
    rec.update(cdr_h); rec.update(cdr_l)
    return rec, None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ingest a generator handoff into the registry.")
    ap.add_argument("directory")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--target", default=None)
    ap.add_argument("--batch", default=None)
    ap.add_argument("--family", default=None, help="model family set BY HAND (D8)")
    args = ap.parse_args(argv)

    d = pathlib.Path(args.directory)
    csv_path = d / "candidates.csv"
    man_path = d / "manifest.json"
    if not csv_path.exists():
        print(f"ERROR: {csv_path} not found"); return 1

    manifest = json.loads(man_path.read_text(encoding="utf-8")) if man_path.exists() else None
    conn = connect()

    target_id = args.target or (manifest or {}).get("target_id")
    if not target_id:
        rows = conn.execute("SELECT target_id FROM target").fetchall()
        if len(rows) == 1:
            target_id = rows[0][0]
        else:
            print(f"ERROR: pass --target (registry holds {len(rows)} targets).")
            return 1
    if not conn.execute("SELECT 1 FROM target WHERE target_id=?", (target_id,)).fetchone():
        print(f"ERROR: target {target_id!r} not in registry — run pipeline.seed_target first")
        return 1

    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        print("ERROR: candidates.csv has no data rows"); return 1

    seen_ids, seen_pairs = set(), {}
    clean, skips = [], []
    for i, row in enumerate(rows, start=2):
        rec, err = validate_row(row, i, seen_ids, seen_pairs)
        if err:
            skips.append((i, (row.get("candidate_id") or "").strip() or None, err))
            continue
        seen_ids.add(rec["candidate_id"])
        seen_pairs[(rec["sequence_h"], rec["sequence_l"] or "")] = rec["candidate_id"]
        clean.append(rec)

    for rec in list(clean):
        hit = conn.execute(
            "SELECT candidate_id FROM candidate WHERE candidate_id=? OR (sequence_h=? AND COALESCE(sequence_l,'')=COALESCE(?,''))",
            (rec["candidate_id"], rec["sequence_h"], rec["sequence_l"])
        ).fetchone()
        if hit:
            clean.remove(rec)
            skips.append((None, rec["candidate_id"], f"already in registry as {hit[0]!r}"))

    if skips and not args.force:
        print(f"REJECTED {csv_path} — {len(skips)} bad row(s), nothing ingested "
              f"(re-run with --force to ingest the {len(clean)} good rows):")
        for rn, c, w in skips:
            print(f"  {'row '+str(rn) if rn else 'pre-existing'}: {c or '-'} -> {w}")
        return 2
    if not clean:
        print("nothing to ingest"); return 2

    actual = sha256_of(csv_path)
    file_sha, manifest_present = None, 0
    if manifest is not None:
        manifest_present = 1
        declared = manifest.get("csv_sha256")
        if declared and declared != actual:
            print(f"ERROR: manifest csv_sha256 mismatch. manifest={declared} actual={actual}")
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
                (batch_id, model_id, target_id, csv_path.name, file_sha, manifest_present,
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

    # summary
    by_fmt = {}
    for rec in clean:
        by_fmt[rec["format"]] = by_fmt.get(rec["format"], 0) + 1
    print(f"ingested {len(clean)} candidate(s) as batch {batch_id} "
          f"({', '.join(f'{v} {k}' for k, v in by_fmt.items())}) "
          f"generator {model_name} v{model_version}, family {family!r}, target {target_id}")
    if manifest_present:
        print(f"  manifest verified, sha256 {actual[:16]}...")
    else:
        print("  NO MANIFEST — hash not verified (manifest_present=0)")
    if skips:
        print(f"  skipped {len(skips)}:")
        for rn, c, w in skips:
            print(f"    {'row '+str(rn) if rn and rn > 0 else 'pre-existing'}: {c or '-'} -> {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
