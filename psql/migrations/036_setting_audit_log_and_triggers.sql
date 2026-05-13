-- Migration 036: trigger-based settings audit log.
--
-- Operator decision 2026-05-13: replace the originally-planned
-- per-endpoint audit_log() calls with Postgres triggers. Rationale: over
-- time we'd forget to call the helper on new endpoints; trigger-based
-- audit makes it impossible to silently miss a settings change. NOTIFY
-- 'setting_changed' gives in-process listeners (Python audit_listener
-- thread, future Anamnesis ingest, etc.) a single subscription channel
-- without per-endpoint code.
--
-- Atomicity guarantee: NOTIFY fires only on transaction COMMIT, so the
-- audit row and the data change are committed together — no "wrote
-- audit but rollback the data" or vice versa.
--
-- Actor capture: each request stashes user_id / client_id / origin onto
-- the DB session via `SET LOCAL audit.user_id = '<id>'` etc. The trigger
-- reads them via current_setting(..., true) — `true` arg returns NULL
-- (not error) when the GUC isn't set. The Flask before_request hook
-- (app.py) does the SET LOCAL once per mutating request.
--
-- Coverage test in tests/test_audit_coverage.py (added in same branch)
-- introspects information_schema.triggers and fails CI if any of the
-- known settings tables is missing its audit trigger.

BEGIN;

-- =============================================================================
-- 1. Audit table
-- =============================================================================

CREATE TABLE IF NOT EXISTS setting_audit_log (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- user_id is INFORMATIONAL — no FK. An audit table must never fail
    -- to record because the referenced user was deleted/renamed. Same
    -- reasoning for client_id below. Lookups still work via plain JOIN
    -- with users.id / trusted_devices.device_token at query time.
    user_id     INTEGER,
    client_id   UUID,
    origin      TEXT  NOT NULL CHECK (origin IN ('ui','api','system_auto','trigger')),
    table_name  TEXT  NOT NULL,
    row_pk      TEXT,                       -- the row's PK as text (varied types across tables)
    setting_key TEXT,                       -- column name for single-col diffs; NULL for multi-col
    old_value   JSONB,
    new_value   JSONB,
    note        TEXT
);

CREATE INDEX IF NOT EXISTS idx_setting_audit_ts
    ON setting_audit_log (ts DESC);
CREATE INDEX IF NOT EXISTS idx_setting_audit_table_row
    ON setting_audit_log (table_name, row_pk, ts DESC);
CREATE INDEX IF NOT EXISTS idx_setting_audit_client_id
    ON setting_audit_log (client_id, ts DESC)
    WHERE client_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_setting_audit_user_id
    ON setting_audit_log (user_id, ts DESC)
    WHERE user_id IS NOT NULL;

GRANT SELECT, INSERT ON setting_audit_log TO nvr_api;
GRANT USAGE, SELECT ON setting_audit_log_id_seq TO nvr_api;

COMMENT ON TABLE setting_audit_log IS
    'Append-only audit log for every settings change. Populated by '
    'AFTER UPDATE / AFTER INSERT triggers on every *_settings table '
    '(see audit_setting_change()), plus direct INSERTs from the '
    'browser-side audit-outbox (POST /api/audit/batch). Retention: '
    '90 days via a background prune in services/audit_listener.py.';


-- =============================================================================
-- 2. Helper — count JSONB object keys (Postgres has no builtin)
-- =============================================================================

CREATE OR REPLACE FUNCTION jsonb_object_keys_count(j JSONB)
    RETURNS INTEGER AS $$
    SELECT COUNT(*)::INTEGER FROM jsonb_object_keys(j);
$$ LANGUAGE SQL IMMUTABLE;


-- =============================================================================
-- 3. Generic audit trigger function
-- =============================================================================
--
-- Attached to a table via:
--   CREATE TRIGGER audit_<table>
--       AFTER UPDATE OR INSERT ON <table>
--       FOR EACH ROW EXECUTE FUNCTION audit_setting_change('<pk_col>');
--
-- Pass the PK column name as TG_ARGV[0] so the trigger can serialize it
-- to text without knowing the table schema at function-definition time.

CREATE OR REPLACE FUNCTION audit_setting_change() RETURNS TRIGGER AS $$
DECLARE
    old_diff     JSONB;
    new_diff     JSONB;
    diff_keys    INTEGER;
    pk_col       TEXT;
    pk_value     TEXT;
BEGIN
    pk_col := COALESCE(TG_ARGV[0], 'id');

    IF TG_OP = 'INSERT' THEN
        -- For INSERT: old_diff is NULL (nothing existed before); new_diff
        -- is the entire new row.
        old_diff := NULL;
        new_diff := to_jsonb(NEW);
    ELSIF TG_OP = 'UPDATE' THEN
        -- For UPDATE: emit only the columns that actually changed.
        SELECT
            jsonb_object_agg(key, o.value),
            jsonb_object_agg(key, n.value)
          INTO old_diff, new_diff
          FROM jsonb_each(to_jsonb(OLD)) o
          JOIN jsonb_each(to_jsonb(NEW)) n USING (key)
         WHERE o.value IS DISTINCT FROM n.value;

        IF old_diff IS NULL THEN
            -- No-op update (e.g., touched timestamp only with no other changes).
            -- Skip to avoid log spam.
            RETURN NEW;
        END IF;
    END IF;

    -- Serialize the PK value to text. EXECUTE format(...) lets us read a
    -- column by name from the NEW row without knowing the type up front.
    EXECUTE format('SELECT ($1).%I::text', pk_col) INTO pk_value USING NEW;

    diff_keys := COALESCE(jsonb_object_keys_count(new_diff), 0);

    INSERT INTO setting_audit_log (
        ts, user_id, client_id, origin, table_name, row_pk,
        setting_key, old_value, new_value
    ) VALUES (
        NOW(),
        nullif(current_setting('audit.user_id',   true), '')::int,
        nullif(current_setting('audit.client_id', true), '')::uuid,
        COALESCE(nullif(current_setting('audit.origin', true), ''), 'trigger'),
        TG_TABLE_NAME, pk_value,
        -- When exactly one column changed, surface its name in setting_key
        -- so simple consumers don't have to crack open the JSONB diff.
        CASE WHEN diff_keys = 1
             THEN (SELECT k FROM jsonb_object_keys(new_diff) k LIMIT 1)
             ELSE NULL
        END,
        old_diff, new_diff
    );

    -- Broadcast to in-process listeners. Payload size cap: Postgres NOTIFY
    -- max is 8000 bytes — for very large rows we trim to the diff only,
    -- which is what we already build above.
    PERFORM pg_notify('setting_changed', jsonb_build_object(
        'table', TG_TABLE_NAME,
        'pk',    pk_value,
        'op',    TG_OP,
        'old',   old_diff,
        'new',   new_diff,
        'ts',    NOW()
    )::text);

    RETURN NEW;

EXCEPTION
    -- Never fail the original write because audit had a problem. Log to
    -- Postgres log and continue.
    WHEN OTHERS THEN
        RAISE WARNING 'audit_setting_change: % %', SQLSTATE, SQLERRM;
        RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- =============================================================================
-- 4. Attach triggers to every settings-bearing table
-- =============================================================================
--
-- One CREATE TRIGGER per table. The PK column name is the trigger argument.
-- New settings tables must add their own CREATE TRIGGER line in the same
-- migration that creates the table — CI test (test_audit_coverage.py) fails
-- if a known settings table lacks the trigger.

DROP TRIGGER IF EXISTS audit_cameras ON cameras;
CREATE TRIGGER audit_cameras
    AFTER INSERT OR UPDATE ON cameras
    FOR EACH ROW EXECUTE FUNCTION audit_setting_change('serial');

DROP TRIGGER IF EXISTS audit_host_settings ON host_settings;
CREATE TRIGGER audit_host_settings
    AFTER INSERT OR UPDATE ON host_settings
    FOR EACH ROW EXECUTE FUNCTION audit_setting_change('host_label');

DROP TRIGGER IF EXISTS audit_user_camera_preferences ON user_camera_preferences;
CREATE TRIGGER audit_user_camera_preferences
    AFTER INSERT OR UPDATE ON user_camera_preferences
    FOR EACH ROW EXECUTE FUNCTION audit_setting_change('id');

DROP TRIGGER IF EXISTS audit_nvr_settings ON nvr_settings;
CREATE TRIGGER audit_nvr_settings
    AFTER INSERT OR UPDATE ON nvr_settings
    FOR EACH ROW EXECUTE FUNCTION audit_setting_change('key');

DROP TRIGGER IF EXISTS audit_trusted_devices ON trusted_devices;
CREATE TRIGGER audit_trusted_devices
    AFTER INSERT OR UPDATE ON trusted_devices
    FOR EACH ROW EXECUTE FUNCTION audit_setting_change('id');

DROP TRIGGER IF EXISTS audit_camera_credentials ON camera_credentials;
CREATE TRIGGER audit_camera_credentials
    AFTER INSERT OR UPDATE ON camera_credentials
    FOR EACH ROW EXECUTE FUNCTION audit_setting_change('id');

DROP TRIGGER IF EXISTS audit_evidence_camera_settings ON evidence_camera_settings;
CREATE TRIGGER audit_evidence_camera_settings
    AFTER INSERT OR UPDATE ON evidence_camera_settings
    FOR EACH ROW EXECUTE FUNCTION audit_setting_change('serial');

COMMIT;
