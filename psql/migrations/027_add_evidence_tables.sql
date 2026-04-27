-- 027_add_evidence_tables.sql
--
-- Evidence Collection Pipeline — DB schema.
--
-- Three tables:
--   evidence_camera_settings  — per-camera enable/disable + tunables
--   audio_events              — queryable index of capture events
--                               (the canonical source of truth is
--                                /litigation/MANIFEST.jsonl, this table
--                                is a derived index for fast queries)
--   evidence_cases            — generic case registry; consumers like
--                               0_LEGAL register predicates and pull
--                               matching events
--
-- See docs/PROPOSAL_evidence_collection_pipeline.md §4 for the design.

BEGIN;

-- =================================================================
-- 1. Per-camera evidence settings.
-- =================================================================
CREATE TABLE IF NOT EXISTS evidence_camera_settings (
    serial                  TEXT        PRIMARY KEY
                            REFERENCES cameras(serial) ON DELETE CASCADE,
    enabled                 BOOLEAN     NOT NULL DEFAULT FALSE,
    capture_video           BOOLEAN     NOT NULL DEFAULT TRUE,
    capture_audio           BOOLEAN     NOT NULL DEFAULT TRUE,
    silence_db_threshold    REAL        NOT NULL DEFAULT -40.0,
    classifier_categories   JSONB       NOT NULL DEFAULT
        '["screams","crying","impacts","raised-voices"]'::jsonb,
    retention_days          INTEGER     NOT NULL DEFAULT 365,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (silence_db_threshold BETWEEN -90.0 AND 0.0),
    CHECK (retention_days > 0)
);

COMMENT ON TABLE evidence_camera_settings IS
    'Per-camera configuration for the evidence-collection pipeline. '
    'Cameras without a row here are NOT capturing evidence.';
COMMENT ON COLUMN evidence_camera_settings.silence_db_threshold IS
    'RMS dBFS threshold below which an audio window is treated as silent and pruned. '
    'Tuned per-camera since ambient noise varies (e.g. fridge humming, fan).';
COMMENT ON COLUMN evidence_camera_settings.classifier_categories IS
    'JSON array of YAMNet category names this camera should flag. '
    'Subset of: screams, crying, impacts, raised-voices.';

-- =================================================================
-- 2. Generic case registry (Phase 5 — universal consumer API).
-- =================================================================
CREATE TABLE IF NOT EXISTS evidence_cases (
    id              BIGSERIAL   PRIMARY KEY,
    name            TEXT        NOT NULL,
    consumer_id     TEXT        NOT NULL,
    predicates      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    archived_at     TIMESTAMPTZ
);

COMMENT ON TABLE evidence_cases IS
    'Generic case registry. Consumers (e.g. server:~/0_LEGAL daemon, '
    'attorney workstation, future Child Monitor app) register a case '
    'with predicates over events and pull matches via /api/evidence/cases.';
COMMENT ON COLUMN evidence_cases.consumer_id IS
    'Free-form identifier for the consuming system, e.g. '
    '"0_LEGAL/0_MARITAL", "0_LEGAL/0_WORK/mindhop", or an external app id.';
COMMENT ON COLUMN evidence_cases.predicates IS
    'JSON predicates over events. Example: '
    '{"cameras":["serial1","serial2"],"categories":["screams","impacts"],'
    '"after":"2026-01-01T00:00:00Z"}';

CREATE INDEX IF NOT EXISTS evidence_cases_consumer_idx
    ON evidence_cases(consumer_id) WHERE archived_at IS NULL;

-- =================================================================
-- 3. Audio events index.
-- =================================================================
CREATE TABLE IF NOT EXISTS audio_events (
    id                  BIGSERIAL   PRIMARY KEY,
    manifest_id         BIGINT      NOT NULL UNIQUE,
    camera_serial       TEXT        NOT NULL REFERENCES cameras(serial),
    timestamp_utc       TIMESTAMPTZ NOT NULL,
    duration_s          REAL        NOT NULL,
    primary_label       TEXT,
    primary_score       REAL,
    transcript_excerpt  TEXT,
    intake_path         TEXT        NOT NULL,
    flagged_paths       TEXT[]      NOT NULL DEFAULT '{}',
    anamnesis_id        TEXT,
    case_id             BIGINT      REFERENCES evidence_cases(id) ON DELETE SET NULL,
    promoted_at         TIMESTAMPTZ,
    CHECK (duration_s >= 0),
    CHECK (primary_score IS NULL OR (primary_score BETWEEN 0 AND 1))
);

COMMENT ON TABLE audio_events IS
    'Queryable index of evidence capture events. The canonical record '
    'lives in /litigation/MANIFEST.jsonl; this table is a derived index '
    'for fast queries by camera, time, label, and case binding.';
COMMENT ON COLUMN audio_events.manifest_id IS
    'Foreign reference to the corresponding line in MANIFEST.jsonl.';
COMMENT ON COLUMN audio_events.transcript_excerpt IS
    'First 300 chars of the Whisper transcript, for fast text search. '
    'Full transcript lives in the .txt file under /litigation/intake/.';
COMMENT ON COLUMN audio_events.flagged_paths IS
    'Symlink paths under /litigation/flagged/<category>/, one per matched category.';
COMMENT ON COLUMN audio_events.case_id IS
    'NULL until a consumer promotes this event to a case. Once promoted, '
    'auto-pruning is disabled — the event is case-managed at the consumer end.';

CREATE INDEX IF NOT EXISTS audio_events_camera_time_idx
    ON audio_events(camera_serial, timestamp_utc DESC);

CREATE INDEX IF NOT EXISTS audio_events_label_idx
    ON audio_events(primary_label) WHERE primary_label IS NOT NULL;

CREATE INDEX IF NOT EXISTS audio_events_case_idx
    ON audio_events(case_id) WHERE case_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS audio_events_unpromoted_idx
    ON audio_events(timestamp_utc DESC) WHERE case_id IS NULL;

-- =================================================================
-- 4. Grant access to the API role.
-- =================================================================
-- Match the pattern used by other migrations (e.g. 024_grant_insert_nvr_settings.sql)
GRANT SELECT, INSERT, UPDATE, DELETE ON evidence_camera_settings TO nvr_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON evidence_cases TO nvr_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON audio_events TO nvr_api;
GRANT USAGE ON SEQUENCE evidence_cases_id_seq TO nvr_api;
GRANT USAGE ON SEQUENCE audio_events_id_seq TO nvr_api;

COMMIT;
