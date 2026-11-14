"""pipeline/seed_target.py — put the brief's Target into the registry.

    python -m pipeline.seed_target [config/target.json]

Everything about the target defaults to status 'assumption'. Mark a field
'established' only when the organisers actually stated it.
"""
from __future__ import annotations

import json
import pathlib
import sys

from pipeline.db import REPO, connect

FIELDS = ("target_id", "name", "antigen_source", "antigen_sequence", "target_region",
          "region_status", "epitope", "epitope_status", "desired_behavior", "behavior_status")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cfg_path = pathlib.Path(argv[0]) if argv else REPO / "config" / "target.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg.pop("_comment", None)

    missing = [f for f in ("target_id", "name", "desired_behavior") if not cfg.get(f)]
    if missing:
        print(f"ERROR: required field(s) empty in {cfg_path}: {missing}")
        return 1

    conn = connect()
    row = conn.execute("SELECT name FROM target WHERE target_id=?", (cfg["target_id"],)).fetchone()
    if row:
        print(f"target {cfg['target_id']!r} already present ({row[0]!r}) — not overwritten "
              f"(targets are provenance; add a new target_id if the brief changed)")
        return 0

    vals = {f: cfg.get(f) or ("assumption" if f.endswith("_status") else None) for f in FIELDS}
    conn.execute(
        f"INSERT INTO target ({','.join(FIELDS)}) VALUES ({','.join('?' * len(FIELDS))})",
        tuple(vals[f] for f in FIELDS),
    )
    conn.commit()
    print(f"target seeded: {cfg['target_id']} — {cfg['name']}")
    print(f"  region={vals['target_region']!r} ({vals['region_status']}) "
          f"epitope={vals['epitope']!r} ({vals['epitope_status']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
