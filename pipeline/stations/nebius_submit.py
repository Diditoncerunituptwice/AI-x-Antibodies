"""pipeline/stations/nebius_submit.py — fire Nebius folding jobs for every candidate.

    python -m pipeline.stations.nebius_submit [--only vhh|scfv] [--limit N]

Per candidate:
  - VHH  -> nanobodybuilder2 ('>H\n<seq_h>')
  - scFv -> abodybuilder3    ('>H\n<seq_h>\n>L\n<seq_l>')
Submit with --no-wait, capture the op-id, record it in `nebius_submission`.
Does NOT wait — call `pipeline.stations.nebius_collect` later to parse results.

Fire this EARLY (16 teams share this; first call per model can queue 15 min).
Requires:
  - NEBIUS_CREDS env var pointing at credentials.private.json
  - forge.py installed at ~/.claude/skills/nebius-scientific-ai/forge.py
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys

from pipeline.db import REPO, connect, get_or_create_model

FORGE_DEFAULT = pathlib.Path.home() / ".claude" / "skills" / "nebius-scientific-ai" / "forge.py"

# Per-format: (nebius model name, family, entry spec)
FORMAT_MAP = {
    "vhh":  ("nanobodybuilder2", "immunebuilder", "sequence|nanobody-fasta/v1"),
    "scfv": ("abodybuilder3",    "immunebuilder", "sequences|antibody-fasta/v1"),
    "fab":  ("abodybuilder3",    "immunebuilder", "sequences|antibody-fasta/v1"),
}

OP_RE = re.compile(r"\b(op-[A-Za-z0-9]+)\b")


def _fasta_for(rec):
    if rec["format"] == "vhh":
        return f">H\n{rec['sequence_h']}\n"
    return f">H\n{rec['sequence_h']}\n>L\n{rec['sequence_l']}\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["vhh", "scfv"], default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--forge", default=str(FORGE_DEFAULT))
    ap.add_argument("--creds", default=os.environ.get("NEBIUS_CREDS"))
    ap.add_argument("--dry-run", action="store_true",
                    help="write FASTA files and print commands, do not submit")
    args = ap.parse_args(argv)

    if not args.creds and not args.dry_run:
        print("ERROR: NEBIUS_CREDS not set (point it at credentials.private.json)")
        return 1
    forge = pathlib.Path(args.forge)
    if not forge.exists() and not args.dry_run:
        print(f"ERROR: forge.py not found at {forge}")
        return 1

    conn = connect()
    in_dir = REPO / "data" / "nebius-inputs"
    in_dir.mkdir(parents=True, exist_ok=True)

    # pick candidates that have no pending/fetched submission for their format's model
    rows = conn.execute("""
        SELECT candidate_id, format, sequence_h, sequence_l FROM candidate
         WHERE format IS NOT NULL
         ORDER BY created_at
    """).fetchall()
    if args.only:
        rows = [r for r in rows if r["format"] == args.only]

    submitted, skipped = 0, 0
    for r in rows:
        if args.limit and submitted >= args.limit:
            break
        if r["format"] not in FORMAT_MAP:
            skipped += 1
            continue
        model_name, family, entry_type = FORMAT_MAP[r["format"]]

        # has an op already?
        exists = conn.execute(
            "SELECT op_id, status FROM nebius_submission WHERE candidate_id=? AND model_name=?",
            (r["candidate_id"], model_name)).fetchone()
        if exists:
            skipped += 1
            continue

        fasta = _fasta_for(dict(r))
        fasta_path = in_dir / f"{r['candidate_id']}.fasta"
        fasta_path.write_text(fasta, encoding="utf-8")
        label = f"{r['candidate_id']}-{model_name}"

        cmd = [sys.executable, str(forge), "submit", model_name,
               "--entry", f"{entry_type}|{fasta_path}",
               "--params", '{"numbering_scheme":"imgt"}',
               "--label", label, "--no-wait"]

        if args.dry_run:
            print(f"DRY {r['candidate_id']} [{r['format']}] -> {' '.join(cmd)}")
            continue

        env = dict(os.environ, NEBIUS_CREDS=args.creds)
        try:
            out = subprocess.run(cmd, check=True, capture_output=True, text=True,
                                 env=env, timeout=120)
        except subprocess.CalledProcessError as exc:
            print(f"  FAIL {r['candidate_id']}: {exc.stderr.strip()[:200]}")
            continue
        text = (out.stdout or "") + (out.stderr or "")
        m = OP_RE.search(text)
        if not m:
            print(f"  WARN {r['candidate_id']}: submit succeeded but no op-id found in output")
            print(f"    output: {text.strip()[:200]}")
            continue
        op_id = m.group(1)

        # register the evaluator model so Evidence can link to it when results arrive
        evaluator_id = get_or_create_model(conn, name=model_name, family=family,
                                           role="evaluator", version="nebius-oslo-2026")
        with conn:
            conn.execute(
                "INSERT INTO nebius_submission (candidate_id, model_name, evaluator_model_id,"
                " op_id, label, input_path) VALUES (?,?,?,?,?,?)",
                (r["candidate_id"], model_name, evaluator_id, op_id, label,
                 str(fasta_path.relative_to(REPO))))
        submitted += 1
        print(f"  SUBMIT {r['candidate_id']} [{r['format']}] -> {model_name} {op_id}")

    print(f"[nebius_submit] submitted {submitted}, skipped {skipped} "
          f"(use nebius_collect to fetch results)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
