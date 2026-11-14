"""pipeline/decide.py — log human decisions (shortlist / select / reject) without SQL.

For teammates who shouldn't touch the database:

    python -m pipeline.decide list                              # pending (shortlisted, unauthorised)
    python -m pipeline.decide show C-001                        # one candidate's full history
    python -m pipeline.decide select C-001 "epitope match + low disagreement" --actor alice
    python -m pipeline.decide reject C-002 adversarial_review   "biologist flagged H3 liability" --actor bob
    python -m pipeline.decide unselect C-001 "replaced by C-007" --actor alice

Rules enforced by the schema:
  - selected → requires human actor + authorized (D6)
  - rejected → requires reason_code (D7)
  - nothing is deleted; every change is a new Decision row (D7 append-only)

reason_codes: low_confidence · no_interface_contact · off_target_epitope ·
              developability · model_disagreement · duplicate ·
              adversarial_review · other
"""
from __future__ import annotations

import argparse
import sys

from pipeline.db import connect, log_state

REASONS = {"low_confidence", "no_interface_contact", "off_target_epitope",
           "developability", "model_disagreement", "duplicate",
           "adversarial_review", "other"}


def _show_one(conn, cid):
    row = conn.execute("SELECT * FROM v_registry WHERE candidate_id=?", (cid,)).fetchone()
    if not row:
        print(f"no candidate {cid!r}"); return 1
    print(f"{row['candidate_id']} [{row['format']}] generator={row['generator']} "
          f"state={row['current_state']}")
    print(f"  current decision: {row['decision']} ({row['decision_authority']}, by {row['decided_by']})")
    if row['rationale']:
        print(f"  rationale: {row['rationale']}")
    print(f"  evidence rows: ", end="")
    counts = conn.execute(
        "SELECT metric_class, COUNT(*) n FROM evidence e"
        " JOIN metric_definition m ON m.metric_name=e.metric_name"
        " WHERE e.candidate_id=? GROUP BY metric_class", (cid,)).fetchall()
    print(", ".join(f"{r['metric_class']}={r['n']}" for r in counts) or "none yet")
    print("  decision history:")
    for d in conn.execute(
            "SELECT created_at, outcome, authority, actor, reason_code, rationale"
            " FROM decision WHERE candidate_id=? ORDER BY decision_id", (cid,)):
        tag = f"{d['outcome']} ({d['authority']}/{d['actor']})"
        extra = f" {d['reason_code']}" if d['reason_code'] else ""
        print(f"    {d['created_at'][:19]} {tag}{extra} — {d['rationale']}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Log human decisions on candidates.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="shortlisted candidates awaiting human selection")

    p_show = sub.add_parser("show", help="full history of one candidate")
    p_show.add_argument("candidate_id")

    p_sel = sub.add_parser("select", help="AUTHORIZE a candidate for the final set (human-only)")
    p_sel.add_argument("candidate_id")
    p_sel.add_argument("rationale", help="at least 10 chars; the pitch's 'why'")
    p_sel.add_argument("--actor", required=True, help="your name (logged as the human authority)")

    p_rej = sub.add_parser("reject", help="RECOMMEND rejection (or authorize it as a human)")
    p_rej.add_argument("candidate_id")
    p_rej.add_argument("reason_code", choices=sorted(REASONS))
    p_rej.add_argument("rationale")
    p_rej.add_argument("--actor", required=True)
    p_rej.add_argument("--authorize", action="store_true",
                       help="human-authorize (default is recommended)")

    p_short = sub.add_parser("shortlist", help="put a candidate back on the shortlist")
    p_short.add_argument("candidate_id")
    p_short.add_argument("rationale")
    p_short.add_argument("--actor", required=True)

    p_un = sub.add_parser("unselect", help="revert a selection (adds a new shortlist decision)")
    p_un.add_argument("candidate_id")
    p_un.add_argument("rationale")
    p_un.add_argument("--actor", required=True)

    args = ap.parse_args(argv)
    conn = connect()

    if args.cmd == "list":
        rows = conn.execute("""
            SELECT candidate_id, format, decision, decided_by,
                   round(iptm_mean_indep,3) iptm, cast(liabilities as int) liab,
                   n_independent_families fams
              FROM v_registry
             WHERE decision='shortlisted' OR (decision IS NULL AND current_state='scored')
          ORDER BY candidate_id
        """).fetchall()
        if not rows:
            print("nothing shortlisted / awaiting human decision"); return 0
        print(f"{len(rows)} candidate(s) awaiting human decision:")
        for r in rows:
            print(f"  {r['candidate_id']:22s} [{r['format']:4s}] iptm={r['iptm']} "
                  f"liab={r['liab']} indep-families={r['fams']} "
                  f"decision={r['decision']} by={r['decided_by']}")
        return 0

    if args.cmd == "show":
        return _show_one(conn, args.candidate_id)

    cid = args.candidate_id
    if not conn.execute("SELECT 1 FROM candidate WHERE candidate_id=?", (cid,)).fetchone():
        print(f"no candidate {cid!r}"); return 1
    if len(args.rationale) < 10:
        print("rationale must be at least 10 characters"); return 1

    if args.cmd == "select":
        with conn:
            conn.execute(
                "INSERT INTO decision (candidate_id, outcome, authority, actor_kind, actor, rationale)"
                " VALUES (?,'selected','authorized','human',?,?)",
                (cid, args.actor, args.rationale))
            log_state(conn, cid, "selected", note=f"authorized by {args.actor}")
        print(f"AUTHORIZED {cid} as selected by {args.actor}")

    elif args.cmd == "reject":
        authority = "authorized" if args.authorize else "recommended"
        actor_kind = "human"
        with conn:
            conn.execute(
                "INSERT INTO decision (candidate_id, outcome, authority, actor_kind, actor,"
                " reason_code, rationale) VALUES (?,'rejected',?,?,?,?,?)",
                (cid, authority, actor_kind, args.actor, args.reason_code, args.rationale))
            log_state(conn, cid, "rejected", note=f"{args.reason_code} by {args.actor}")
        print(f"REJECTED {cid} ({authority}) by {args.actor}: {args.reason_code}")

    elif args.cmd == "shortlist":
        with conn:
            conn.execute(
                "INSERT INTO decision (candidate_id, outcome, authority, actor_kind, actor, rationale)"
                " VALUES (?,'shortlisted','authorized','human',?,?)",
                (cid, args.actor, args.rationale))
            log_state(conn, cid, "shortlisted", note=f"by {args.actor}")
        print(f"SHORTLISTED {cid} by {args.actor}")

    elif args.cmd == "unselect":
        with conn:
            conn.execute(
                "INSERT INTO decision (candidate_id, outcome, authority, actor_kind, actor, rationale)"
                " VALUES (?,'shortlisted','authorized','human',?,?)",
                (cid, args.actor, f"unselected: {args.rationale}"))
            log_state(conn, cid, "shortlisted", note=f"unselected by {args.actor}")
        print(f"UNSELECTED {cid} by {args.actor} — back on shortlist")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
