"""pipeline/evidence_cards.py — one markdown card per candidate, straight from the registry.

    python -m pipeline.evidence_cards [--only-selected]

Writes `reports/evidence_cards.md` — the document the judges actually read.
Every number is a SELECT from the registry; nothing is invented. Rejected
candidates get a card too, with the reason.

Scientific reasoning is 40 % of the score, and it's judged on *why* each
candidate was chosen. This file is that answer.
"""
from __future__ import annotations

import argparse
import datetime
import pathlib

from pipeline.db import REPO, connect, spec_version


def _fmt(v, nd=3):
    if v is None:
        return "—"
    try:
        return f"{float(v):.{nd}f}"
    except Exception:
        return str(v)


def _card(conn, cid):
    r = conn.execute("SELECT * FROM v_registry WHERE candidate_id=?", (cid,)).fetchone()
    if not r:
        return None
    lines = []
    lines.append(f"## {r['candidate_id']}")
    lines.append(f"**Format:** {r['format']} · **State:** {r['current_state']}")
    if r['decision']:
        lines.append(f"**Decision:** `{r['decision']}` "
                     f"({r['decision_authority']}, by {r['decided_by']})")
    else:
        lines.append("**Decision:** pending")
    if r['reason_code']:
        lines.append(f"**Reason:** {r['reason_code']}")
    if r['rationale']:
        lines.append(f"**Rationale:** {r['rationale']}")
    lines.append("")
    lines.append(f"**Generator:** {r['generator']} `{r['generator_version']}` "
                 f"seed={r['generator_seed']}")
    if r['parent_id']:
        lines.append(f"**Parent:** {r['parent_id']}")
    lines.append("")
    lines.append("### Sequences")
    lines.append(f"- **H** ({len(r['sequence_h'])} aa): `{r['sequence_h']}`")
    if r['sequence_l']:
        lines.append(f"- **L** ({len(r['sequence_l'])} aa): `{r['sequence_l']}`")
    lines.append("")
    lines.append("### Evidence summary (independent evaluators only)")
    lines.append("")
    lines.append("| metric | class | mean (indep) | spread | #families | n(all) | source |")
    lines.append("|---|---|---|---|---|---|---|")
    for s in conn.execute("""
            SELECT ms.metric_name, ms.metric_class, ms.mean_independent,
                   ms.spread_independent, ms.n_independent_families, ms.n_rows_all,
                   (SELECT GROUP_CONCAT(DISTINCT m.name)
                      FROM evidence e JOIN model m ON m.model_id=e.model_id
                     WHERE e.candidate_id=? AND e.metric_name=ms.metric_name) AS srcs
              FROM v_metric_summary ms
             WHERE ms.candidate_id=?
          ORDER BY ms.metric_class, ms.metric_name
        """, (cid, cid)):
        lines.append(f"| `{s['metric_name']}` | {s['metric_class']} | "
                     f"{_fmt(s['mean_independent'])} | {_fmt(s['spread_independent'])} | "
                     f"{s['n_independent_families'] or 0} | {s['n_rows_all']} | "
                     f"{s['srcs'] or '—'} |")
    lines.append("")

    hist = list(conn.execute(
        "SELECT created_at, outcome, authority, actor, reason_code, rationale"
        " FROM decision WHERE candidate_id=? ORDER BY decision_id", (cid,)))
    if hist:
        lines.append("### Decision history")
        for d in hist:
            tag = f"**{d['outcome']}** ({d['authority']} / {d['actor']})"
            rc = f" — `{d['reason_code']}`" if d['reason_code'] else ""
            lines.append(f"- {d['created_at'][:19]} {tag}{rc} — {d['rationale']}")
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--only-selected", action="store_true",
                    help="only write cards for state='selected' (final 10)")
    ap.add_argument("--out", default="reports/evidence_cards.md")
    args = ap.parse_args(argv)

    conn = connect()
    out = REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)

    where = "WHERE current_state='selected'" if args.only_selected else ""
    rows = conn.execute(f"SELECT candidate_id FROM v_registry {where} ORDER BY "
                        "CASE current_state WHEN 'selected' THEN 0 WHEN 'shortlisted' THEN 1 "
                        "WHEN 'scored' THEN 2 ELSE 9 END, candidate_id").fetchall()

    counts = {s: 0 for s in ("selected", "shortlisted", "scored", "generated", "rejected")}
    for r in conn.execute("SELECT current_state, COUNT(*) n FROM v_registry GROUP BY current_state"):
        counts[r["current_state"]] = r["n"]
    total = sum(counts.values())

    with open(out, "w", encoding="utf-8") as fh:
        fh.write(f"# Evidence cards — AI × Antibodies, team 8\n\n")
        fh.write(f"Generated {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} "
                 f"from the registry (spec {spec_version(conn)}).\n\n")
        fh.write(f"**Funnel:** {total} total · "
                 f"{counts['selected']} selected · {counts['shortlisted']} shortlisted · "
                 f"{counts['scored']} scored · {counts['rejected']} rejected\n\n")
        fh.write("Every number in every table is a `SELECT` from the registry. "
                 "Confidence metrics (pLDDT/ipTM/PAE/ipSAE) are model opinions about geometry, "
                 "not measured binding. Rejected candidates stay with their reason.\n\n")
        fh.write("---\n\n")
        for r in rows:
            card = _card(conn, r["candidate_id"])
            if card:
                fh.write(card + "\n---\n\n")

    print(f"[evidence_cards] wrote {out} ({len(rows)} cards)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
