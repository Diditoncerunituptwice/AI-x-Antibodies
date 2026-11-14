"""pipeline/run_all.py — the whole line, one command.

    python -m pipeline.run_all            # real stations only
    python -m pipeline.run_all --mock     # + MOCK structural evaluators (plumbing test)

Order: scoring stations -> hard filters -> export. Intake is separate and runs
per handoff batch (python -m pipeline.intake <dir>).
"""
from __future__ import annotations

import argparse

from pipeline import export, filter as filter_mod
from pipeline.db import connect, spec_version
from pipeline.stations import developability


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run scoring stations, filters and export.")
    ap.add_argument("--mock", action="store_true",
                    help="also run the MOCK structural evaluators (fake numbers, plumbing only)")
    args = ap.parse_args(argv)

    conn = connect()
    n = conn.execute("SELECT COUNT(*) FROM candidate").fetchone()[0]
    print(f"registry: spec {spec_version(conn)}, {n} candidate(s)")
    if n == 0:
        print("nothing to do — ingest a batch first: python -m pipeline.intake <dir>")
        return 1

    print("\n-- stations --")
    developability.run(conn)
    if args.mock:
        from pipeline.stations import mock_structure
        mock_structure.run(conn)

    print("\n-- filters --")
    filter_mod.run(conn)

    print("\n-- export --")
    export.run(conn)
    print("\ndone. Final 10 are NOT selected here: ranking recommends, a human authorizes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
