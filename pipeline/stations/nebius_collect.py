"""pipeline/stations/nebius_collect.py — poll + fetch pending Nebius ops,
parse results into Evidence rows.

    python -m pipeline.stations.nebius_collect [--wait]

Walks every `pending` submission, calls forge.py status; when a run is done,
calls forge.py fetch into results/<op-id>/. Defensively parses whatever shape
the result.json has (pLDDT-like per-residue confidence -> plddt_mean; any
`ptm`/`iptm`/`ipsae` keys at top level -> matching metrics). Marks the
submission `fetched` on success, `failed` otherwise.

With --wait, blocks on `forge.py wait` for each run. Without, only parses runs
that are already done and skips the rest for next time.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

from pipeline.db import REPO, connect, log_state

FORGE_DEFAULT = pathlib.Path.home() / ".claude" / "skills" / "nebius-scientific-ai" / "forge.py"
RESULTS_DIR = REPO / "results"


def _run(cmd, env, timeout=180):
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)


def _mean(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return sum(xs) / len(xs) if xs else None


def _walk(obj, keys):
    """Yield numeric values whose ancestor key contains any of `keys` (case-insensitive)."""
    hits = []
    def _r(x, path):
        if isinstance(x, dict):
            for k, v in x.items():
                _r(v, path + [k.lower()])
        elif isinstance(x, list):
            for v in x:
                _r(v, path)
        elif isinstance(x, (int, float)):
            if any(any(k in p for k in keys) for p in path):
                hits.append(x)
    _r(obj, [])
    return hits


def parse_result(result_dir):
    """Return a dict of metric_name -> value. Defensive: ships whatever it finds."""
    out = {}
    rjson = result_dir / "result.json"
    if not rjson.exists():
        # try common alternatives
        candidates = list(result_dir.rglob("result.json")) + list(result_dir.rglob("*.json"))
        rjson = candidates[0] if candidates else None
    if rjson is None:
        return out

    try:
        data = json.loads(rjson.read_text(encoding="utf-8"))
    except Exception:
        return out

    # pLDDT-like per-residue confidence -> mean
    plddt = _walk(data, ("plddt", "per_residue_confidence", "confidence_per_residue"))
    if plddt:
        m = _mean(plddt)
        if m is not None:
            # ImmuneBuilder returns 0-1 error-ish values; pLDDT is 0-100. Rescale heuristically.
            if 0.0 <= m <= 1.0:
                m *= 100.0
            out["plddt_mean"] = float(m)

    # global ptm / iptm / ipsae
    for key, metric in (("iptm", "iptm"), ("ipsae", "ipsae"), ("ptm", "ptm")):
        vals = _walk(data, (key,))
        if vals:
            # take the first scalar-ish one
            out[metric] = float(vals[0])

    # PAE (interface mean) — if present
    pae = _walk(data, ("pae_interface_mean", "pae_interface", "pae_mean"))
    if pae:
        out["pae_interface_mean"] = float(_mean(pae))

    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", action="store_true",
                    help="block on forge.py wait for each pending run")
    ap.add_argument("--forge", default=str(FORGE_DEFAULT))
    ap.add_argument("--creds", default=os.environ.get("NEBIUS_CREDS"))
    args = ap.parse_args(argv)

    if not args.creds:
        print("ERROR: NEBIUS_CREDS not set"); return 1
    forge = pathlib.Path(args.forge)
    if not forge.exists():
        print(f"ERROR: forge.py not found at {forge}"); return 1

    conn = connect()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, NEBIUS_CREDS=args.creds)

    pending = conn.execute(
        "SELECT sub_id, candidate_id, model_name, evaluator_model_id, op_id, label"
        " FROM nebius_submission WHERE status='pending'").fetchall()
    if not pending:
        print("[nebius_collect] nothing pending"); return 0

    fetched, still_pending, failed = 0, 0, 0
    for sub in pending:
        op = sub["op_id"]
        if args.wait:
            r = _run([sys.executable, str(forge), "wait", op], env, timeout=3600)
            if r.returncode != 0:
                with conn:
                    conn.execute("UPDATE nebius_submission SET status='failed', error=?, fetched_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE sub_id=?",
                                 (r.stderr.strip()[:500], sub["sub_id"]))
                failed += 1
                print(f"  FAIL {sub['candidate_id']} {op}: {r.stderr.strip()[:120]}")
                continue
        else:
            r = _run([sys.executable, str(forge), "status", op], env, timeout=60)
            status_out = (r.stdout + r.stderr).lower()
            if "done" not in status_out and "succeed" not in status_out and "complete" not in status_out:
                still_pending += 1
                continue

        dest = RESULTS_DIR / op
        dest.mkdir(parents=True, exist_ok=True)
        r = _run([sys.executable, str(forge), "fetch", op, "--dest", str(dest)], env, timeout=600)
        if r.returncode != 0:
            with conn:
                conn.execute("UPDATE nebius_submission SET status='failed', error=?, fetched_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE sub_id=?",
                             (("fetch: " + r.stderr.strip())[:500], sub["sub_id"]))
            failed += 1
            print(f"  FETCH-FAIL {sub['candidate_id']} {op}: {r.stderr.strip()[:120]}")
            continue

        metrics = parse_result(dest)
        if not metrics:
            print(f"  WARN {sub['candidate_id']} {op}: no metrics parsed from result "
                  f"(inspect {dest} and extend parse_result)")
        with conn:
            for name, value in metrics.items():
                conn.execute(
                    "INSERT INTO evidence (candidate_id, model_id, evidence_type, metric_name,"
                    " value, source_ref, artifact_path) VALUES (?,?,'predicted',?,?,?,?)",
                    (sub["candidate_id"], sub["evaluator_model_id"], name, float(value), op,
                     str(dest.relative_to(REPO))))
            conn.execute("UPDATE nebius_submission SET status='fetched', fetched_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE sub_id=?",
                         (sub["sub_id"],))
            log_state(conn, sub["candidate_id"], "scored", note=f"{sub['model_name']} {op}")
        fetched += 1
        print(f"  FETCHED {sub['candidate_id']} {op}: {', '.join(f'{k}={v:.3f}' for k, v in metrics.items()) or 'no metrics'}")

    print(f"[nebius_collect] fetched {fetched}, still pending {still_pending}, failed {failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
