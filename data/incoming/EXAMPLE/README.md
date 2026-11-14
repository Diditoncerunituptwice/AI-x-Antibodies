# Example handoff — copy and edit

Two rows show scFv (two chains) and one row shows VHH (single chain).
The `_comment` key is ignored by the ingest; add it anywhere for readability.

To ingest this example (will fail the second time because candidate_ids
are already in the registry — that's by design):
```bash
python -m pipeline.intake_json data/incoming/EXAMPLE
```

Delete this folder before a real run.
