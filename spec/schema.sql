-- AI x Antibodies registry schema. SPEC VERSION 0.1.1 "LITE"
-- SQLite >= 3.38. Every connection MUST run: PRAGMA foreign_keys = ON;
--
-- WHAT "LITE" MEANS (vs 0.1.0 strict, archived as schema.v0.1.0-strict.sql):
--   Enforcement that could block the ML teammate or the pipeline mid-event is removed.
--   Two blocking triggers are GONE: the generator-vs-evaluator family block, and the
--   lifecycle gate. Both are pure INSERT triggers, so strict mode is re-enabled by running
--   strict_mode.sql (no table rebuild). See that file.
--
-- THE FIVE HARD CONSTRAINTS KEPT (they ARE the judging story, and they're cheap):
--   1. evidence_type predicted/experimental; experimental needs an assay model + source_ref
--   2. confidence metrics (pLDDT/ipTM/PAE/ipSAE) can never be filed as affinity
--   3. an 'authorized' decision requires a human actor
--   4. every rejection carries a reason_code
--   5. append-only: evidence, decisions, decision_evidence, state log never change
--
-- KEPT BEYOND THE FIVE (don't block the hot path, protect provenance / principle 0.7):
--   candidate no-delete, candidate identity immutable, role-integrity of the generator link,
--   candidate structural CHECKs (sequence is a real AA sequence, CDR ordering). The pipeline
--   validates + skips bad rows BEFORE insert (--force), so these are a backstop, not a gate.

PRAGMA foreign_keys = ON;

CREATE TABLE spec_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
INSERT INTO spec_meta VALUES ('spec_version', '0.1.2'), ('mode', 'lite');

-- ───────────────────────── 1. TARGET ─────────────────────────
CREATE TABLE target (
  target_id        TEXT PRIMARY KEY CHECK (target_id NOT GLOB '*[^A-Za-z0-9_-]*' AND length(target_id) BETWEEN 2 AND 40),
  name             TEXT NOT NULL,
  antigen_source   TEXT,
  antigen_sequence TEXT,
  target_region    TEXT,
  region_status    TEXT NOT NULL DEFAULT 'assumption' CHECK (region_status  IN ('assumption','established')),
  epitope          TEXT,
  epitope_status   TEXT NOT NULL DEFAULT 'assumption' CHECK (epitope_status IN ('assumption','established')),
  desired_behavior TEXT NOT NULL,
  behavior_status  TEXT NOT NULL DEFAULT 'assumption' CHECK (behavior_status IN ('assumption','established')),
  created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- ───────────────────────── 3. MODEL ─────────────────────────
-- role: generator | evaluator | assay.  family groups tools that are NOT independent of each other.
-- family is set BY HAND at registration (D8). The DB can't know two tools share weights.
CREATE TABLE model (
  model_id      INTEGER PRIMARY KEY,
  name          TEXT NOT NULL,
  family        TEXT NOT NULL,
  version       TEXT NOT NULL DEFAULT 'unspecified',   -- LITE: pipeline may default when manifest omits it (D4)
  role          TEXT NOT NULL CHECK (role IN ('generator','evaluator','assay')),
  settings_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(settings_json)),
  UNIQUE (name, version, role, settings_json)
);

-- ───────────────────────── ingest batch (provenance of the handoff) ─────────────────────────
-- LITE: manifest is optional (D4). If absent, the pipeline builds a batch row from CLI/defaults
-- and sets manifest_present=0 + file_sha256=NULL so the registry shows the hash was not verified.
CREATE TABLE ingest_batch (
  batch_id           TEXT PRIMARY KEY,
  generator_model_id INTEGER NOT NULL REFERENCES model(model_id),
  target_id          TEXT NOT NULL REFERENCES target(target_id),
  file_name          TEXT NOT NULL,
  file_sha256        TEXT CHECK (file_sha256 IS NULL OR length(file_sha256) = 64),  -- LITE: nullable
  manifest_present   INTEGER NOT NULL DEFAULT 1 CHECK (manifest_present IN (0,1)),  -- LITE
  forced             INTEGER NOT NULL DEFAULT 0 CHECK (forced IN (0,1)),            -- LITE: --force used?
  n_rows_declared    INTEGER,          -- from manifest if present
  n_rows_ingested    INTEGER NOT NULL DEFAULT 0,
  n_rows_skipped     INTEGER NOT NULL DEFAULT 0,
  ingested_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- rows skipped by --force, with the reason (D4: "reports every skipped row")
CREATE TABLE ingest_skip (
  skip_id      INTEGER PRIMARY KEY,
  batch_id     TEXT NOT NULL REFERENCES ingest_batch(batch_id),
  row_number   INTEGER NOT NULL,
  candidate_id TEXT,
  reason       TEXT NOT NULL
);

-- ───────────────────────── 2. VHH CANDIDATE ─────────────────────────
-- CDR positions: 1-based, inclusive, indexes into THIS sequence (not IMGT/Kabat). Positional for now (D9).
CREATE TABLE candidate (
  candidate_id       TEXT PRIMARY KEY CHECK (candidate_id NOT GLOB '*[^A-Za-z0-9_-]*' AND length(candidate_id) BETWEEN 3 AND 40),
  -- format: 'vhh' (single-domain, only sequence_h used) | 'scfv' or 'fab' (both chains)
  format             TEXT NOT NULL DEFAULT 'vhh' CHECK (format IN ('vhh','scfv','fab')),
  -- sequence_h: VHH domain OR the heavy chain. 80-160 aa. Alphabet-checked.
  sequence_h         TEXT NOT NULL CHECK (sequence_h NOT GLOB '*[^ACDEFGHIKLMNPQRSTVWY]*' AND length(sequence_h) BETWEEN 80 AND 160),
  -- sequence_l: light chain for scFv/fab, NULL for VHH. 90-120 aa typical VL.
  sequence_l         TEXT CHECK (sequence_l IS NULL OR (sequence_l NOT GLOB '*[^ACDEFGHIKLMNPQRSTVWY]*' AND length(sequence_l) BETWEEN 80 AND 140)),
  target_id          TEXT NOT NULL REFERENCES target(target_id),
  generator_model_id INTEGER NOT NULL REFERENCES model(model_id),
  seed               INTEGER,
  parent_id          TEXT REFERENCES candidate(candidate_id),
  batch_id           TEXT NOT NULL REFERENCES ingest_batch(batch_id),
  -- H-chain CDRs (or VHH CDRs)
  cdr1_start INTEGER, cdr1_end INTEGER,
  cdr2_start INTEGER, cdr2_end INTEGER,
  cdr3_start INTEGER, cdr3_end INTEGER,
  -- L-chain CDRs (scfv/fab only)
  cdr_l1_start INTEGER, cdr_l1_end INTEGER,
  cdr_l2_start INTEGER, cdr_l2_end INTEGER,
  cdr_l3_start INTEGER, cdr_l3_end INTEGER,
  cdr_source         TEXT,
  created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  CHECK (parent_id IS NULL OR parent_id <> candidate_id),
  -- format consistency: scfv/fab must have L chain; vhh must not
  CHECK ((format = 'vhh' AND sequence_l IS NULL AND cdr_l1_start IS NULL)
      OR (format IN ('scfv','fab') AND sequence_l IS NOT NULL)),
  -- H-chain CDR structural checks (unchanged from v0.1.1)
  CHECK (cdr1_start IS NULL OR (cdr1_start >= 1 AND cdr1_end >= cdr1_start AND cdr1_end < cdr2_start)),
  CHECK (cdr2_start IS NULL OR (cdr2_end >= cdr2_start AND cdr2_end < cdr3_start)),
  CHECK (cdr3_start IS NULL OR (cdr3_end >= cdr3_start AND cdr3_end <= length(sequence_h))),
  CHECK ((cdr1_start IS NULL) = (cdr2_start IS NULL) AND (cdr2_start IS NULL) = (cdr3_start IS NULL)),
  -- L-chain CDR structural checks (when present)
  CHECK (cdr_l1_start IS NULL OR (cdr_l1_start >= 1 AND cdr_l1_end >= cdr_l1_start AND cdr_l1_end < cdr_l2_start)),
  CHECK (cdr_l2_start IS NULL OR (cdr_l2_end >= cdr_l2_start AND cdr_l2_end < cdr_l3_start)),
  CHECK (cdr_l3_start IS NULL OR (cdr_l3_end >= cdr_l3_start AND cdr_l3_end <= length(sequence_l))),
  CHECK ((cdr_l1_start IS NULL) = (cdr_l2_start IS NULL) AND (cdr_l2_start IS NULL) = (cdr_l3_start IS NULL))
);
CREATE UNIQUE INDEX ix_candidate_pair ON candidate (sequence_h, COALESCE(sequence_l,''));

-- role integrity of the generator link (kept: backbone of the generator/evaluator distinction)
CREATE TRIGGER trg_candidate_checks BEFORE INSERT ON candidate
BEGIN
  SELECT RAISE(ABORT, 'generator_model_id must reference a model with role=generator')
   WHERE (SELECT role FROM model WHERE model_id = NEW.generator_model_id) IS NOT 'generator';
END;

-- identity immutable (CDR columns may still be backfilled); candidates are never deleted (principle 0.7)
CREATE TRIGGER trg_candidate_immutable BEFORE UPDATE OF
  candidate_id, format, sequence_h, sequence_l, target_id, generator_model_id, seed, parent_id, batch_id ON candidate
BEGIN SELECT RAISE(ABORT, 'candidate identity fields are immutable'); END;
CREATE TRIGGER trg_candidate_nodelete BEFORE DELETE ON candidate
BEGIN SELECT RAISE(ABORT, 'candidates are never deleted; reject them with a reason'); END;

-- ───────────────────────── 4. EVIDENCE ─────────────────────────
-- Metric meaning is fixed here, not by whoever writes the row.
-- LITE: new rows may be INSERTed at RUNTIME without a version bump, as long as metric_class is an
-- EXISTING class value (D/ item 5). added_at/added_by log who added them. A brand-new metric_CLASS
-- is a structural change -> version bump.
CREATE TABLE metric_definition (
  metric_name           TEXT PRIMARY KEY,
  metric_class          TEXT NOT NULL CHECK (metric_class IN
                          ('confidence','geometry','developability','predicted_affinity_proxy',
                           'experimental_binding','experimental_affinity','experimental_developability')),
  unit                  TEXT,
  allowed_evidence_type TEXT NOT NULL CHECK (allowed_evidence_type IN ('predicted','experimental')),
  description           TEXT NOT NULL,
  added_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  added_by              TEXT NOT NULL DEFAULT 'runtime',
  -- (2) confidence vs affinity, locked:
  CHECK ((allowed_evidence_type = 'experimental') = (metric_class LIKE 'experimental_%')),
  CHECK (metric_name NOT IN ('plddt_mean','plddt_cdr3','ptm','iptm','ipsae','pae_interface_mean')
         OR metric_class = 'confidence')
);

INSERT INTO metric_definition (metric_name,metric_class,unit,allowed_evidence_type,description,added_by) VALUES
 ('plddt_mean',          'confidence',  '0-100', 'predicted', 'Mean pLDDT of the predicted structure. Model confidence, NOT affinity.','spec-0.1.0'),
 ('plddt_cdr3',          'confidence',  '0-100', 'predicted', 'Mean pLDDT over CDR3 residues.','spec-0.1.0'),
 ('ptm',                 'confidence',  '0-1',   'predicted', 'Predicted TM-score (global fold confidence).','spec-0.1.0'),
 ('iptm',                'confidence',  '0-1',   'predicted', 'Interface predicted TM-score. Model confidence, NOT affinity.','spec-0.1.0'),
 ('ipsae',               'confidence',  '0-1',   'predicted', 'ipSAE interface score from PAE. Model confidence, NOT affinity.','spec-0.1.0'),
 ('pae_interface_mean',  'confidence',  'A',     'predicted', 'Mean predicted aligned error across the interface.','spec-0.1.0'),
 ('interface_area',      'geometry',    'A^2',   'predicted', 'Buried surface area at the predicted interface.','spec-0.1.0'),
 ('epitope_overlap_frac','geometry',    '0-1',   'predicted', 'Fraction of predicted interface residues inside the target epitope.','spec-0.1.0'),
 ('interface_contacts',  'geometry',    'count', 'predicted', 'Number of interfacial residue contacts.','spec-0.1.0'),
 ('net_charge_ph7',      'developability','e',   'predicted', 'Net charge at pH 7 from sequence.','spec-0.1.0'),
 ('pi',                  'developability', NULL, 'predicted', 'Isoelectric point from sequence.','spec-0.1.0'),
 ('gravy',               'developability', NULL, 'predicted', 'Grand average of hydropathy.','spec-0.1.0'),
 ('liability_motif_count','developability','count','predicted','Count of sequence liabilities (N-glyc, deamidation, isomerization).','spec-0.1.0'),
 ('nativeness_score',    'developability', NULL, 'predicted', 'VHH nativeness/humanness-style score (tool defined by Model row).','spec-0.1.0'),
 -- PROVISIONAL (D10): confirm against the real Adaptyv result format; may change when announced.
 ('binding_detected',    'experimental_binding',  '0/1',  'experimental', 'Adaptyv: binding signal detected (1) or not (0). PROVISIONAL.','spec-0.1.0'),
 ('kd_nm',               'experimental_affinity', 'nM',   'experimental', 'Adaptyv: measured KD. PROVISIONAL.','spec-0.1.0'),
 ('expressed',           'experimental_developability','0/1','experimental','Adaptyv: candidate expressed (1) or not (0). PROVISIONAL.','spec-0.1.0');

CREATE TABLE evidence (
  evidence_id   INTEGER PRIMARY KEY,
  candidate_id  TEXT    NOT NULL REFERENCES candidate(candidate_id),
  model_id      INTEGER NOT NULL REFERENCES model(model_id),
  evidence_type TEXT    NOT NULL CHECK (evidence_type IN ('predicted','experimental')),
  metric_name   TEXT    NOT NULL REFERENCES metric_definition(metric_name),
  value         REAL    NOT NULL,
  run_seed      INTEGER,
  source_ref    TEXT,               -- required for experimental (Adaptyv result id)
  artifact_path TEXT,
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX ix_evidence_candidate ON evidence(candidate_id, metric_name);

-- (1) predicted/experimental + (2) confidence-not-affinity kept. The generator-vs-evaluator
-- FAMILY block is REMOVED in lite (D3): same-family evaluators are allowed. Independence is a
-- DERIVED flag in v_evidence, never typed in, never blocking.
CREATE TRIGGER trg_evidence_checks BEFORE INSERT ON evidence
BEGIN
  SELECT RAISE(ABORT, 'predicted evidence must come from a model with role=evaluator')
   WHERE NEW.evidence_type = 'predicted'
     AND (SELECT role FROM model WHERE model_id = NEW.model_id) IS NOT 'evaluator';

  SELECT RAISE(ABORT, 'experimental evidence must come from a model with role=assay')
   WHERE NEW.evidence_type = 'experimental'
     AND (SELECT role FROM model WHERE model_id = NEW.model_id) IS NOT 'assay';

  SELECT RAISE(ABORT, 'experimental evidence requires source_ref (Adaptyv result id)')
   WHERE NEW.evidence_type = 'experimental' AND (NEW.source_ref IS NULL OR length(NEW.source_ref) = 0);

  SELECT RAISE(ABORT, 'metric not allowed for this evidence_type (see metric_definition)')
   WHERE (SELECT allowed_evidence_type FROM metric_definition WHERE metric_name = NEW.metric_name)
         IS NOT NEW.evidence_type;
END;

-- ───────────────────────── 5. DECISION ─────────────────────────
CREATE TABLE decision (
  decision_id  INTEGER PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES candidate(candidate_id),
  outcome      TEXT NOT NULL CHECK (outcome IN ('rejected','shortlisted','selected')),
  authority    TEXT NOT NULL CHECK (authority IN ('recommended','authorized')),
  actor_kind   TEXT NOT NULL CHECK (actor_kind IN ('human','script','model')),
  actor        TEXT NOT NULL CHECK (length(actor) > 0),
  reason_code  TEXT CHECK (reason_code IN ('low_confidence','no_interface_contact','off_target_epitope',
                           'developability','model_disagreement','duplicate','adversarial_review','other')),
  rationale    TEXT NOT NULL CHECK (length(rationale) >= 10),
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  CHECK (authority = 'recommended' OR actor_kind = 'human'),   -- (3) only a human can authorize (D6)
  CHECK (outcome <> 'rejected' OR reason_code IS NOT NULL)     -- (4) rejections need a reason (D7)
);
CREATE INDEX ix_decision_candidate ON decision(candidate_id, decision_id);

CREATE TABLE decision_evidence (
  decision_id INTEGER NOT NULL REFERENCES decision(decision_id),
  evidence_id INTEGER NOT NULL REFERENCES evidence(evidence_id),
  direction   TEXT NOT NULL CHECK (direction IN ('supports','against','context')),
  PRIMARY KEY (decision_id, evidence_id)
);

CREATE TRIGGER trg_decision_evidence_match BEFORE INSERT ON decision_evidence
BEGIN
  SELECT RAISE(ABORT, 'evidence belongs to a different candidate than the decision')
   WHERE (SELECT candidate_id FROM evidence WHERE evidence_id = NEW.evidence_id)
      IS NOT (SELECT candidate_id FROM decision WHERE decision_id = NEW.decision_id);
END;

-- ───────────────────────── claims (assumption vs established fact) ─────────────────────────
CREATE TABLE claim (
  claim_id     INTEGER PRIMARY KEY,
  subject_type TEXT NOT NULL CHECK (subject_type IN ('target','candidate','evidence','decision')),
  subject_id   TEXT NOT NULL,
  statement    TEXT NOT NULL,
  status       TEXT NOT NULL CHECK (status IN ('assumption','established','disputed','retracted')),
  basis        TEXT,
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  CHECK (status <> 'established' OR (basis IS NOT NULL AND length(basis) > 0))
);

-- ───────────────────────── LIFECYCLE (LITE: log-only, no gates) ─────────────────────────
-- The blocking gate trigger from 0.1.0 is REMOVED. Transitions are recorded, never refused.
-- Re-enable the gate with strict_mode.sql. Current state = latest row (v_candidate_state).
CREATE TABLE candidate_state_log (
  log_id       INTEGER PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES candidate(candidate_id),
  state        TEXT NOT NULL CHECK (state IN
                 ('generated','scored','filtered','shortlisted','selected','submitted','validated','rejected')),
  at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  note         TEXT
);
CREATE INDEX ix_state_candidate ON candidate_state_log(candidate_id, log_id);

CREATE TRIGGER trg_candidate_born AFTER INSERT ON candidate
BEGIN
  INSERT INTO candidate_state_log (candidate_id, state, at) VALUES (NEW.candidate_id, 'generated', NEW.created_at);
END;

-- ───────────────────────── (5) append-only audit trail ─────────────────────────
CREATE TRIGGER trg_evidence_noupdate BEFORE UPDATE ON evidence BEGIN SELECT RAISE(ABORT, 'evidence is append-only'); END;
CREATE TRIGGER trg_evidence_nodelete BEFORE DELETE ON evidence BEGIN SELECT RAISE(ABORT, 'evidence is append-only'); END;
CREATE TRIGGER trg_decision_noupdate BEFORE UPDATE ON decision BEGIN SELECT RAISE(ABORT, 'decisions are append-only; add a new one'); END;
CREATE TRIGGER trg_decision_nodelete BEFORE DELETE ON decision BEGIN SELECT RAISE(ABORT, 'decisions are append-only'); END;
CREATE TRIGGER trg_de_noupdate BEFORE UPDATE ON decision_evidence BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER trg_de_nodelete BEFORE DELETE ON decision_evidence BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER trg_log_noupdate BEFORE UPDATE ON candidate_state_log BEGIN SELECT RAISE(ABORT, 'state log is append-only'); END;
CREATE TRIGGER trg_log_nodelete BEFORE DELETE ON candidate_state_log BEGIN SELECT RAISE(ABORT, 'state log is append-only'); END;

-- ───────────────────────── VIEWS (read-only; the "registry") ─────────────────────────
CREATE VIEW v_candidate_state AS
SELECT candidate_id, state AS current_state, at AS state_since
  FROM candidate_state_log
 WHERE log_id IN (SELECT MAX(log_id) FROM candidate_state_log GROUP BY candidate_id);

CREATE VIEW v_lifecycle AS
SELECT candidate_id,
  MIN(CASE WHEN state='generated'   THEN at END) AS generated_at,
  MIN(CASE WHEN state='scored'      THEN at END) AS scored_at,
  MIN(CASE WHEN state='filtered'    THEN at END) AS filtered_at,
  MIN(CASE WHEN state='shortlisted' THEN at END) AS shortlisted_at,
  MIN(CASE WHEN state='selected'    THEN at END) AS selected_at,
  MIN(CASE WHEN state='submitted'   THEN at END) AS submitted_at,
  MIN(CASE WHEN state='validated'   THEN at END) AS validated_at,
  MIN(CASE WHEN state='rejected'    THEN at END) AS rejected_at
FROM candidate_state_log GROUP BY candidate_id;

-- (D3) INDEPENDENCE is derived here, per evidence row. An evaluator in the SAME family as the
-- candidate's generator is NOT independent (independent=0). Experimental (assay) evidence is
-- always independent of an in-silico generator. NULL-safe family compare via IS NOT.
CREATE VIEW v_evidence AS
SELECT e.*,
       m.family AS evaluator_family, m.role AS evaluator_role,
       g.family AS generator_family,
       CASE WHEN e.evidence_type = 'experimental' THEN 1
            WHEN m.family IS NOT g.family THEN 1
            ELSE 0 END AS independent
  FROM evidence e
  JOIN model m     ON m.model_id = e.model_id
  JOIN candidate c ON c.candidate_id = e.candidate_id
  JOIN model g     ON g.model_id = c.generator_model_id;

-- Uncertainty counts INDEPENDENT evidence only (pitch rule: only independent evidence is
-- independent support). n_rows_all is kept for context (e.g. a same-family refold still logged).
CREATE VIEW v_metric_summary AS
SELECT ev.candidate_id, ev.metric_name, md.metric_class, ev.evidence_type,
       COUNT(*)                                                           AS n_rows_all,
       SUM(ev.independent)                                               AS n_independent_rows,
       COUNT(DISTINCT CASE WHEN ev.independent=1 THEN ev.evaluator_family END) AS n_independent_families,
       AVG(CASE WHEN ev.independent=1 THEN ev.value END)                 AS mean_independent,
       MIN(CASE WHEN ev.independent=1 THEN ev.value END)                 AS min_independent,
       MAX(CASE WHEN ev.independent=1 THEN ev.value END)                 AS max_independent,
       MAX(CASE WHEN ev.independent=1 THEN ev.value END)
         - MIN(CASE WHEN ev.independent=1 THEN ev.value END)             AS spread_independent,
       AVG(ev.value)                                                     AS mean_all
  FROM v_evidence ev
  JOIN metric_definition md ON md.metric_name = ev.metric_name
 GROUP BY ev.candidate_id, ev.metric_name, ev.evidence_type;

CREATE VIEW v_current_decision AS
SELECT d.* FROM decision d
 WHERE d.decision_id IN (SELECT MAX(decision_id) FROM decision GROUP BY candidate_id);

-- The registry the judges see: one row per candidate (rejected included, with reason).
-- Structural metrics are INDEPENDENT means/spread; n_independent_families is the uncertainty headline.
CREATE VIEW v_registry AS
SELECT c.candidate_id, c.format, c.sequence_h, c.sequence_l, c.parent_id,
       g.name AS generator, g.version AS generator_version, c.seed AS generator_seed,
       s.current_state,
       cd.outcome AS decision, cd.authority AS decision_authority, cd.actor AS decided_by,
       cd.reason_code, cd.rationale,
       (SELECT COUNT(*) FROM decision_evidence de WHERE de.decision_id = cd.decision_id) AS n_evidence_links,
       (SELECT mean_independent        FROM v_metric_summary x WHERE x.candidate_id=c.candidate_id AND x.metric_name='iptm')  AS iptm_mean_indep,
       (SELECT spread_independent      FROM v_metric_summary x WHERE x.candidate_id=c.candidate_id AND x.metric_name='iptm')  AS iptm_spread_indep,
       (SELECT mean_independent        FROM v_metric_summary x WHERE x.candidate_id=c.candidate_id AND x.metric_name='ipsae') AS ipsae_mean_indep,
       (SELECT spread_independent      FROM v_metric_summary x WHERE x.candidate_id=c.candidate_id AND x.metric_name='ipsae') AS ipsae_spread_indep,
       (SELECT mean_independent        FROM v_metric_summary x WHERE x.candidate_id=c.candidate_id AND x.metric_name='plddt_mean') AS plddt_mean_indep,
       (SELECT n_independent_families  FROM v_metric_summary x WHERE x.candidate_id=c.candidate_id AND x.metric_name='iptm') AS n_independent_families,
       (SELECT mean_independent        FROM v_metric_summary x WHERE x.candidate_id=c.candidate_id AND x.metric_name='liability_motif_count') AS liabilities,
       (SELECT mean_independent        FROM v_metric_summary x WHERE x.candidate_id=c.candidate_id AND x.metric_name='binding_detected') AS measured_binding,
       (SELECT mean_independent        FROM v_metric_summary x WHERE x.candidate_id=c.candidate_id AND x.metric_name='kd_nm') AS measured_kd_nm
  FROM candidate c
  JOIN model g ON g.model_id = c.generator_model_id
  JOIN v_candidate_state s ON s.candidate_id = c.candidate_id
  LEFT JOIN v_current_decision cd ON cd.candidate_id = c.candidate_id;


-- ───────────────────────── Nebius submissions (pending ops) ─────────────────────────
-- Tracks file-based batch jobs that are in flight. Written by nebius_submit,
-- consumed by nebius_collect, which parses results into Evidence rows and flips
-- `status` to 'fetched' or 'failed'. Append-only in spirit; status is the only
-- mutable column (via INSERT-then-DELETE-latest would be cleaner, but this is
-- pragmatic). No PII, no secrets.
CREATE TABLE IF NOT EXISTS nebius_submission (
  sub_id       INTEGER PRIMARY KEY,
  candidate_id TEXT    NOT NULL REFERENCES candidate(candidate_id),
  model_name   TEXT    NOT NULL,            -- 'nanobodybuilder2' etc, Nebius id
  evaluator_model_id INTEGER NOT NULL REFERENCES model(model_id),
  op_id        TEXT    NOT NULL UNIQUE,     -- Nebius operation id (op-xxxxxxxx)
  label        TEXT    NOT NULL,
  input_path   TEXT    NOT NULL,
  status       TEXT    NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','fetched','failed')),
  submitted_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  fetched_at   TEXT,
  error        TEXT
);
CREATE INDEX IF NOT EXISTS ix_nebius_pending ON nebius_submission(status);
