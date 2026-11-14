"""scripts/fake_generator.py — stand-in for the ML teammate's generator.

    python scripts/fake_generator.py --n 25 --batch B-000-dryrun

Writes data/incoming/<batch>/candidates.csv + manifest.json in the handoff
format (D4) so the whole line can be exercised before anyone generates anything
real. Sequences are a real VHH framework with randomised CDR loops — plausible
shapes, meaningless biology. Delete the batch once real candidates arrive.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pathlib
import random
import datetime

REPO = pathlib.Path(__file__).resolve().parent.parent

# A real VHH framework split around its three CDR loops (the loops get replaced).
FR1 = "QVQLQESGGGLVQAGGSLRLSCAASG"
FR2 = "MGWFRQAPGKEREFVAAI"
FR3 = "YYADSVKGRFTISRDNAKNTVYLQMNSLKPEDTAVYYCAA"
FR4 = "WGQGTQVTVSS"
AA = "ACDEFGHIKLMNPQRSTVWY"


def make_sequence(rng):
    cdr1 = "".join(rng.choice(AA) for _ in range(rng.randint(6, 9)))
    cdr2 = "".join(rng.choice(AA) for _ in range(rng.randint(6, 9)))
    cdr3 = "".join(rng.choice(AA) for _ in range(rng.randint(8, 18)))
    seq = FR1 + cdr1 + FR2 + cdr2 + FR3 + cdr3 + FR4
    spans = []
    p = len(FR1)
    spans.append((p + 1, p + len(cdr1)));            p += len(cdr1) + len(FR2)
    spans.append((p + 1, p + len(cdr2)));            p += len(cdr2) + len(FR3)
    spans.append((p + 1, p + len(cdr3)))
    return seq, spans


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=25)
    ap.add_argument("--batch", default="B-000-dryrun")
    ap.add_argument("--target", default="T-TBD")
    ap.add_argument("--seed", type=int, default=20261114)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    out = REPO / "data" / "incoming" / args.batch
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / "candidates.csv"

    cols = ["candidate_id", "sequence", "model_name", "model_version", "seed", "parent_id",
            "cdr1_start", "cdr1_end", "cdr2_start", "cdr2_end", "cdr3_start", "cdr3_end"]
    seen = set()
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        i = 0
        while i < args.n:
            seq, spans = make_sequence(rng)
            if seq in seen or not (80 <= len(seq) <= 160):
                continue
            seen.add(seq)
            i += 1
            w.writerow([f"{args.batch}-{i:03d}", seq, "FAKE-Generator", "0.0.0-dryrun",
                        args.seed + i, "", *[v for s in spans for v in s]])

    sha = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    (out / "manifest.json").write_text(json.dumps({
        "spec_version": "0.1.1",
        "batch_id": args.batch,
        "target_id": args.target,
        "model": {"name": "FAKE-Generator", "family": "fake-generator",
                  "version": "0.0.0-dryrun", "role": "generator",
                  "settings": {"n": args.n, "seed": args.seed}},
        "n_rows": args.n,
        "csv_sha256": sha,
        "created_at": datetime.datetime.now(datetime.timezone.utc)
                      .strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, indent=2), encoding="utf-8")
    print(f"wrote {args.n} fake candidates -> {out}")
    print(f"  next: python -m pipeline.intake data/incoming/{args.batch}")


if __name__ == "__main__":
    main()
