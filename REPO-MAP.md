# Repo map — where everything lives, and why

One-page guide to this repository. Open this before anything else.

**Five groups, five purposes:**

1. **Ontology** (`spec/`) — what is true. Rules that bind everything.
2. **Methods** (`algo/`) — pure functions. No DB, no network. Unit-tested.
3. **Plumbing** (`pipeline/`) — the code that moves data in, through, out.
4. **Data** (`config/`, `data/`, `reports/`, `registry.db`) — the actual content.
5. **Docs** (`README.md`, `TEAM-WORKFLOW.md`, `docs/`) — explanations.

---

## Full layout

```
ai-antibodies/
│
├── README.md                     Quick start (first thing a judge opens)
├── TEAM-WORKFLOW.md              One page: how the team uses this
├── REPO-MAP.md                   This file
├── requirements.txt              No dependencies (stdlib only, Python 3.9+)
├── .gitignore                    Blocks credentials and registry.db
│
├── spec/                         ──────── ONTOLOGY (what is true) ────────
│   ├── SPEC.md                   What the ontology is, in plain language
│   ├── schema.sql                Tables, constraints, triggers (v0.1.3 "lite")
│   ├── models.py                 Python mirror of the schema (pydantic)
│   ├── strict_mode.sql           Optional stricter version (re-enables blocks)
│   ├── schema.v0.1.0-strict.sql  Archived previous version
│   └── models_v0_1_0_strict.py   Same, Python side
│
├── algo/                         ──────── METHODS (pure logic) ────────
│   ├── common.py                 Shared numeric helpers
│   ├── liabilities.py            Liability scanner (AC + regex + window)
│   ├── pareto.py                 Pareto front and non-dominated layers
│   ├── aggregation.py            Consensus rank + disagreement penalty
│   ├── diversity.py              MMR diversity-aware selection
│   ├── sensitivity.py            Top-k stability under weight perturbation
│   └── tests/                    35 tests — run via scripts/run_tests.py
│
├── pipeline/                     ──────── PLUMBING (I/O + coordination) ────
│   ├── db.py                     Connect to registry.db + helpers
│   ├── seed_target.py            Put Target row into registry from config/
│   ├── intake.py                 CSV handoff → registry
│   ├── intake_json.py            JSON handoff → registry (team uses this)
│   ├── import_reference_binders.py  Bio-expert's library → registry
│   ├── stations/                 Scoring stations (each writes Evidence rows)
│   │   ├── developability.py     Liability scan + GRAVY + net charge
│   │   ├── paratope_identity.py  70% novelty rule vs reference library
│   │   ├── nebius_submit.py      Fire async Nebius folding jobs
│   │   ├── nebius_collect.py     Fetch results, parse into Evidence
│   │   ├── apheris_openfold3_collect.py  Apheris OpenFold3 scores → Evidence (ipTM, pTM, pLDDT)
│   │   └── mock_structure.py     FAKE (remove when real evaluators run)
│   ├── filter.py                 Hard filters → Decision(recommended)
│   ├── decide.py                 Human-in-the-loop CLI (select/reject)
│   ├── export.py                 CSV reports → reports/
│   ├── export_abguard.py         Submission CSV in the organisers' abguard format → reports/
│   ├── evidence_cards.py         Markdown pitch doc → reports/
│   └── run_all.py                Stations + filter + export in one command
│
├── config/                       ──────── SETTINGS ────────
│   └── target.json               Target definition (fill at the brief)
│
├── data/                         ──────── INPUT ────────
│   └── incoming/
│       ├── EXAMPLE/              Copyable JSON template + README
│       └── <your-batch>/         Drop new candidates.json here
│
├── scripts/                      ──────── HELPERS ────────
│   ├── apheris_to_candidates.py  RFD4-Proteina metrics → data/incoming/<batch>/candidates.json
│   ├── fake_generator.py         Produce fake VHH sequences (dry run only)
│   └── run_tests.py              Run all 35 tests without pytest
│
├── sequences/                    ──────── REFERENCE SEQUENCES ────────
│   ├── trastuzumab_WT_reference.fasta  Wild-type heavy + light chain
│   └── README.md                 The two tracks (de novo <70% / optimisation ≥70%)
│
├── docs/                         ──────── REFERENCE MATERIAL ────────
│   ├── OPTIMISATION-TRACK.md     Matteo's brief, mutants, 70% rule, open questions
│   └── apheris/
│       ├── openfold3-schema.json Apheris OpenFold3 input/output schema
│       └── workflows.txt         (empty — no Nextflow workflows for us)
│
├── reports/                      ──────── OUTPUT ────────
│   └── (registry.csv, score_table.csv, rejected.csv,
│       provenance.csv, evidence_cards.md — all after run_all)
│
└── registry.db                   ──────── THE DATABASE ────────
                                  (gitignored; auto-created on first run)
```

---

## What runs what

The flow, in one sentence:

> **generate → put in `data/incoming/` → ingest → run_all (score + filter + export) → decide → evidence_cards → pitch.**

Commands, in order:

```bash
# once, at the start of the day
python3 -m pipeline.seed_target
python3 -m pipeline.import_reference_binders <bio library file>

# for every batch of candidates
python3 -m pipeline.intake_json data/incoming/<batch>
python3 -m pipeline.run_all

# human-in-the-loop, as decisions are made
python3 -m pipeline.decide list
python3 -m pipeline.decide select C-xxx "reason..." --actor <name>
python3 -m pipeline.decide reject C-yyy <reason_code> "reason..." --actor <name>

# before submit at 18:00
python3 -m pipeline.evidence_cards
```

---

## Non-negotiables (written into the schema)

- **Predicted ≠ measured binding.** Confidence (pLDDT/ipTM/PAE) is never filed as affinity.
- **The generator never evaluates or selects its own candidates.** Enforced by family flag.
- **Only humans authorize selections.** Scripts write `recommended`, not `authorized`.
- **Rejected candidates stay** with their reason. Nothing is deleted.
- **Append-only audit trail.** Every evidence row and decision is permanent.
- **Credentials never go in git.** `.gitignore` blocks `credentials.private.json`.

---

## Where to look when something is wrong

| Problem | Open |
|---|---|
| Not sure how to use the pipeline | `TEAM-WORKFLOW.md` |
| Not sure what the ontology means | `spec/SPEC.md` |
| Want to understand a method | `algo/<name>.py` — each has a docstring at the top |
| Ingest failed, want to know why | the error message names the row and the reason |
| Score looks wrong | `reports/score_table.csv` has source attribution per metric |
| Candidate is missing from the final list | `reports/rejected.csv` shows every rejection with reason |
| Want to see the full history of one candidate | `python3 -m pipeline.decide show C-xxx` |

---

## Spec version

Current: **0.1.3 "lite"** — see `spec/SPEC.md` §9 for the changelog.
