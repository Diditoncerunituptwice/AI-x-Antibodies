"""pipeline/db.py — registry connection + model registration helpers.

Stdlib only. Every connection runs PRAGMA foreign_keys = ON (spec requires it).
"""
from __future__ import annotations

import json
import os
import pathlib
import sqlite3

REPO = pathlib.Path(__file__).resolve().parent.parent
# Default lives in the repo. Override with ANTIBODY_DB when the repo sits on a
# filesystem SQLite can't lock (network share, some FUSE mounts) -> "disk I/O error".
DEFAULT_DB = pathlib.Path(os.environ.get("ANTIBODY_DB") or (REPO / "registry.db"))
SCHEMA = REPO / "spec" / "schema.sql"


def connect(db_path=None):
    """Open the registry. Creates + initialises it from spec/schema.sql if absent."""
    db_path = pathlib.Path(db_path or DEFAULT_DB)
    fresh = not db_path.exists()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if fresh:
        conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        conn.commit()
    return conn


def spec_version(conn):
    row = conn.execute("SELECT value FROM spec_meta WHERE key='spec_version'").fetchone()
    return row[0] if row else None


def get_or_create_model(conn, name, family, role, version="unspecified", settings=None):
    """Register a Model row (idempotent on name+version+role+settings). Returns model_id.

    `family` groups tools that are NOT independent of each other, and is set BY HAND
    at registration (D8). The DB cannot know two tools share weights.
    """
    settings_json = json.dumps(settings or {}, sort_keys=True)
    row = conn.execute(
        "SELECT model_id FROM model WHERE name=? AND version=? AND role=? AND settings_json=?",
        (name, version, role, settings_json),
    ).fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        "INSERT INTO model (name, family, version, role, settings_json) VALUES (?,?,?,?,?)",
        (name, family, version, role, settings_json),
    )
    conn.commit()
    return cur.lastrowid


def ensure_metric(conn, metric_name, metric_class, unit, description,
                  allowed_evidence_type="predicted", added_by="pipeline"):
    """Add a metric_definition row at runtime (LITE allows this without a version bump).

    metric_class must be an EXISTING class value; a brand-new class is a structural
    change and needs a spec version bump.
    """
    conn.execute(
        "INSERT OR IGNORE INTO metric_definition "
        "(metric_name, metric_class, unit, allowed_evidence_type, description, added_by) "
        "VALUES (?,?,?,?,?,?)",
        (metric_name, metric_class, unit, allowed_evidence_type, description, added_by),
    )
    conn.commit()


def log_state(conn, candidate_id, state, note=None):
    """Append a lifecycle transition. LITE: logged, never refused."""
    conn.execute(
        "INSERT INTO candidate_state_log (candidate_id, state, note) VALUES (?,?,?)",
        (candidate_id, state, note),
    )


def current_state(conn, candidate_id):
    row = conn.execute(
        "SELECT current_state FROM v_candidate_state WHERE candidate_id=?", (candidate_id,)
    ).fetchone()
    return row[0] if row else None
