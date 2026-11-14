"""pipeline/import_reference_binders.py — ingest the anti-HER2 paratope library.

Formats accepted (auto-detected):

  TSV/CSV with columns:
    binder_id, name, format, subdomain, source, source_kind,
    vh_sequence, vl_sequence,
    cdr_h1, cdr_h2, cdr_h3, cdr_l1, cdr_l2, cdr_l3, notes

  JSON array of objects with the same fields.

  FASTA (best-effort):
    header '>name|format|subdomain|source[|source_kind]'
    headers ending in ':VH' / ':VL' are paired by the prefix.

Usage:
    python -m pipeline.import_reference_binders ref_library.tsv
    python -m pipeline.import_reference_binders paratope_library.fasta

Nothing is deleted. Re-importing with a known binder_id SKIPS it (not an error).
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import re
import sys

from pipeline.db import connect

STANDARD_AA = set("ACDEFGHIKLMNPQRSTVWY")
FORMATS = {"vhh", "scfv", "fab", "igg", "scaffold", "other"}
SUBDOMAINS = {"I", "II", "III", "IV", "II-IV", "unknown"}
SOURCE_KINDS = {"pdb", "patent", "paper", "other"}

COLS = ("binder_id", "name", "format", "subdomain", "source", "source_kind",
        "vh_sequence", "vl_sequence", "cdr_h1", "cdr_h2", "cdr_h3",
        "cdr_l1", "cdr_l2", "cdr_l3", "notes")


def _slug(s):
    s = re.sub(r"[^A-Za-z0-9_.-]", "_", s.strip())
    return s[:60] or "unnamed"


def _check(rec, n):
    missing = [k for k in ("binder_id", "name", "format", "source") if not rec.get(k)]
    if missing:
        return f"row {n}: missing {missing}"
    if rec["format"] not in FORMATS:
        return f"row {n}: format {rec['format']!r} not in {FORMATS}"
    if rec.get("subdomain") and rec["subdomain"] not in SUBDOMAINS:
        return f"row {n}: subdomain {rec['subdomain']!r} not in {SUBDOMAINS}"
    if rec.get("source_kind") and rec["source_kind"] not in SOURCE_KINDS:
        return f"row {n}: source_kind {rec['source_kind']!r} not in {SOURCE_KINDS}"
    for k in ("vh_sequence", "vl_sequence"):
        v = rec.get(k)
        if v:
            bad = sorted(set(v.upper()) - STANDARD_AA)
            if bad:
                return f"row {n}: {k} has non-standard chars {bad}"
    return None


def _from_tsv_or_csv(path):
    text = path.read_text(encoding="utf-8-sig")
    sep = "\t" if "\t" in text.splitlines()[0] else ","
    reader = csv.DictReader(text.splitlines(), delimiter=sep)
    return [dict(r) for r in reader]


def _from_json(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "binders" in data:
        data = data["binders"]
    if not isinstance(data, list):
        raise ValueError("JSON must be an array (or {'binders': [...]})")
    return [{k: ("" if v is None else str(v)) for k, v in r.items()} for r in data]


def _from_fasta(path):
    """Best-effort FASTA. Header '>name|format|subdomain|source[|source_kind]'."""
    recs = {}  # keyed by name
    text = path.read_text(encoding="utf-8-sig")
    cur_name = cur_chain = cur_seq = None
    for line in text.splitlines() + [">END"]:
        line = line.strip()
        if line.startswith(">"):
            if cur_name is not None:
                r = recs.setdefault(cur_name["name"], cur_name)
                if cur_chain == "VH":
                    r["vh_sequence"] = cur_seq
                elif cur_chain == "VL":
                    r["vl_sequence"] = cur_seq
                else:
                    r.setdefault("vh_sequence", cur_seq)
            if line == ">END":
                break
            # parse header
            head = line[1:].strip()
            # support '...:VH' / '...:VL' suffix
            chain = None
            if head.endswith(":VH"):
                chain, head = "VH", head[:-3]
            elif head.endswith(":VL"):
                chain, head = "VL", head[:-3]
            parts = [p.strip() for p in head.split("|")]
            # guarantee at least 4 pipe-fields with sensible defaults
            while len(parts) < 4:
                parts.append("")
            name = parts[0]
            cur_name = {
                "name": name,
                "binder_id": _slug(name),
                "format": (parts[1] or "fab").lower(),
                "subdomain": parts[2] or "unknown",
                "source": parts[3] or "unknown",
                "source_kind": (parts[4] if len(parts) > 4 and parts[4] else
                                ("pdb" if parts[3] and len(parts[3]) == 4 else "other")),
                "notes": "imported via FASTA",
            }
            cur_chain = chain
            cur_seq = ""
        elif line:
            cur_seq = (cur_seq or "") + line
    return list(recs.values())


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--format", choices=("auto", "tsv", "csv", "json", "fasta"), default="auto")
    args = ap.parse_args(argv)

    p = pathlib.Path(args.path)
    if not p.exists():
        print(f"ERROR: {p} not found"); return 1

    fmt = args.format
    if fmt == "auto":
        ext = p.suffix.lower()
        fmt = {"tsv": "tsv", "csv": "csv", "json": "json",
               "fasta": "fasta", "fa": "fasta", "faa": "fasta"}.get(ext.lstrip("."), "json")

    try:
        if fmt in ("tsv", "csv"):
            rows = _from_tsv_or_csv(p)
        elif fmt == "json":
            rows = _from_json(p)
        elif fmt == "fasta":
            rows = _from_fasta(p)
    except Exception as exc:
        print(f"ERROR: parse failed — {exc}"); return 1

    if not rows:
        print("no rows parsed"); return 1

    conn = connect()
    added = skipped = failed = 0
    for i, raw in enumerate(rows, start=1):
        # normalise
        rec = {k: (raw.get(k) or "").strip() for k in COLS}
        rec["format"] = rec["format"].lower() or "fab"
        rec["subdomain"] = rec["subdomain"] or "unknown"
        rec["source_kind"] = (rec["source_kind"] or
                              ("pdb" if len(rec["source"]) == 4 else "other")).lower()
        if not rec["binder_id"]:
            rec["binder_id"] = _slug(rec["name"] or f"ref-{i:03d}")
        for k in ("vh_sequence", "vl_sequence", "cdr_h1", "cdr_h2", "cdr_h3",
                  "cdr_l1", "cdr_l2", "cdr_l3"):
            rec[k] = rec[k].upper() if rec[k] else None
        for k in ("notes", "name", "source"):
            rec[k] = rec[k] or None

        err = _check(rec, i)
        if err:
            print(f"  SKIP {err}"); failed += 1; continue

        if conn.execute("SELECT 1 FROM reference_binder WHERE binder_id=?",
                        (rec["binder_id"],)).fetchone():
            skipped += 1; continue
        try:
            with conn:
                conn.execute(
                    f"INSERT INTO reference_binder ({','.join(COLS)}) "
                    f"VALUES ({','.join('?' * len(COLS))})",
                    tuple(rec[c] for c in COLS))
            added += 1
        except Exception as exc:
            print(f"  FAIL {rec['binder_id']}: {exc}"); failed += 1

    print(f"[import_reference_binders] added {added}, skipped {skipped} (already present), failed {failed}")
    total = conn.execute("SELECT COUNT(*) FROM reference_binder").fetchone()[0]
    print(f"  library now holds {total} reference binder(s)")
    by_sub = conn.execute(
        "SELECT subdomain, COUNT(*) n FROM reference_binder GROUP BY subdomain ORDER BY subdomain"
    ).fetchall()
    print("  by subdomain: " + ", ".join(f"{r[0]}={r[1]}" for r in by_sub))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
