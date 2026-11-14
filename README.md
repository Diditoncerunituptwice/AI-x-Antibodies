# AI × Antibodies — VHH design & selection pipeline

Nucleate Norway hackathon, 14 Nov 2026. Up to 10 VHH candidates submitted; Adaptyv tests ~5.

**The idea:** let the protein models do the biology, use algorithms to improve the
decision, and use an ontology to keep control of the information. Every number in
every report traces back to an Evidence row that names the model, version and seed
that produced it.

## Principles (non-negotiable)

- predicted binding ≠ measured binding
- model confidence (pLDDT / ipTM / PAE / ipSAE) ≠ affinity
- assumptions ≠ established facts
- recommendations ≠ authorized decisions — a script recommends, only a human authorizes
- the generator never selects its own candidates
- rejected candidates are kept, with the reason. Nothing is deleted.

## Quick start

```bash
# 1. the brief's target  (edit config/target.json first)
python -m pipeline.seed_target

# 2. candidates in  (ML teammate's handoff)
python -m pipeline.intake data/incoming/B-001

# 3. score, filter, export
python -m pipeline.run_all

# dry run with no real tools at all:
python scripts/fake_generator.py --n 25 --batch B-000-dryrun
python -m pipeline.intake data/incoming/B-000-dryrun
python -m pipeline.run_all --mock
```

Outputs land in `reports/`: `registry.csv`, `score_table.csv`, `rejected.csv`,
`provenance.csv`.

## The handoff contract (what the ML teammate must give you)

`data/incoming/<batch>/candidates.csv`

| column | required | note |
|---|---|---|
| `candidate_id` | **yes** | 3–40 chars of `[A-Za-z0-9_-]`, unique |
| `sequence` | **yes** | uppercase 20-AA, 80–160 aa, no gaps/X/* |
| `model_name` | **yes** | the generator |
| `model_version`, `seed`, `parent_id` | no | |
| `cdr1_start … cdr3_end` | no | all six or none, 1-based inclusive |

Extra columns are ignored. `manifest.json` is **optional** — when present its
`csv_sha256` is verified; when absent the batch is recorded with
`manifest_present=0` so the registry shows the hash was never checked.

One bad row rejects the whole file and every error is reported with its row
number. `--force` ingests the good rows and logs every skip with a reason.

## Layout

```
config/target.json     the brief. Edit, then seed_target.
data/incoming/<batch>/ generator handoffs land here
spec/                  schema.sql (v0.1.1 lite) + models.py; strict_mode.sql re-tightens
algo/                  pure, unit-tested methods — liabilities (AC + regex + window),
                       pareto, aggregation, diversity, sensitivity. No DB writes.
pipeline/              intake, stations/, filter, export, run_all. Does the I/O.
reports/               the tables the judges see
registry.db            SQLite. The hub — every candidate, score, rejection, decision.
```

## Stations

- `developability` — **real**, sequence only, no network. Calls `algo.scan_liabilities`
  (9 liability kinds), plus net charge and GRAVY.
- `mock_structure` — **FAKE**. Deterministic hashes standing in for ipTM/ipSAE/pLDDT so
  the line runs before the real tools are wired. Every model it registers is named
  `MOCK-*` and `export` warns when mock rows exist. **Delete once real evaluators are in.**

Real evaluators to add on the day: Apheris OpenFold (**required for the prize**),
Boltz / JapanFold / Nebius co-folding, TNP.

Register each with an honest `family` — the family is how "independent evidence" is
derived. Two tools in the same family are not independent of each other, and
same-family evidence is flagged `independent=0` and excluded from the uncertainty
columns (it is still logged).

## Filters

`pipeline/filter.py` → `FILTERS`. Currently: unpaired cysteine, liability load > 8,
ipTM < 0.30. **Tune these on the day once you see the real distributions.** At this
stage you are removing candidates that are clearly broken, not picking winners.

## Notes

- **Stdlib only.** No pip install needed. Python 3.9+.
- If the repo lives on a network share or a mount SQLite can't lock, you'll get
  `disk I/O error` — set `ANTIBODY_DB=/some/local/path.db`.
- `registry.db` is gitignored: it's an artifact, rebuilt from the handoffs.
