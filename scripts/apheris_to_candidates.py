"""scripts/apheris_to_candidates.py — turn an RFD4-Proteina run into candidates.json.

    python3 scripts/apheris_to_candidates.py <run-dir> <batch-id> [--target T-HER2]

Reads the per_example_metrics.csv in the run's generation/inference_metrics/
folder, extracts the designed H-chain sequence from the 'designed_sequences'
column (format 'H:SEQ' or 'H:SEQ;A:SEQ'), writes data/incoming/<batch-id>/
candidates.json ready for `python3 -m pipeline.intake_json`.

Target binder goes in `notes`, not as sequence_l — the target is the antigen,
not a light chain. For scFv that come out of future runs, extend this.
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("batch_id")
    ap.add_argument("--target", default="T-HER2")
    ap.add_argument("--model-version", default="v0.3.0-her2-novel-2026-11-09")
    args = ap.parse_args()

    run = pathlib.Path(args.run_dir)
    csv_path = run / "generation" / "inference_metrics" / "per_example_metrics.csv"
    if not csv_path.exists():
        print(f"ERROR: {csv_path} not found"); return 1

    repo = pathlib.Path(__file__).resolve().parent.parent
    out = repo / "data" / "incoming" / args.batch_id
    out.mkdir(parents=True, exist_ok=True)

    candidates = []
    with open(csv_path, newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            example_id = row.get("example_id", "").strip()
            des = row.get("designed_sequences", "").strip()
            if not example_id or not des:
                continue
            # parse "H:SEQ" or "H:SEQ;A:SEQ"
            parts = {}
            for p in des.split(";"):
                if ":" in p:
                    k, v = p.split(":", 1)
                    parts[k.strip()] = v.strip()
            vh = parts.get("H")
            if not vh:
                print(f"  SKIP {example_id}: no H chain in designed_sequences")
                continue
            cid = "C-" + example_id.replace("__", "-").replace("_", "-")[:38]
            seed_raw = row.get("seed", "").strip()
            try:
                seed = int(float(seed_raw)) if seed_raw else None
            except ValueError:
                seed = None
            note_bits = []
            if row.get("hotspot_obedience.hotspot_obedience"):
                note_bits.append(f"hotspot_obedience={row['hotspot_obedience.hotspot_obedience']}")
            if row.get("ckpt_name"):
                note_bits.append(f"ckpt={row['ckpt_name']}")
            candidates.append({
                "candidate_id": cid,
                "format": "vhh",
                "sequence_h": vh,
                "model_name": "apheris-rfd4-proteina",
                "model_version": args.model_version,
                "seed": seed,
                "notes": "; ".join(note_bits),
            })

    out_path = out / "candidates.json"
    out_path.write_text(json.dumps(candidates, indent=2), encoding="utf-8")
    print(f"wrote {len(candidates)} candidate(s) to {out_path}")
    print(f"next: python3 -m pipeline.intake_json data/incoming/{args.batch_id} --family rfd4-proteina")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
