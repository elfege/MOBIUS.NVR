-- Migration 032: per-host settings for the kiosk performance manager.
--
-- Why a new table (not nvr_settings):
--   nvr_settings is a single-row global key/value bag. The throttle
--   settings are per-machine (one host might have a beefy GPU and
--   tolerate 80% CPU, another a fanless laptop that needs 30%). A
--   dedicated table with host_label as PK is the cleanest fit and
--   leaves room for future per-host columns (preferred grid layout,
--   default stream type, etc.) without re-keying.
--
-- Why not user_camera_preferences:
--   That table is per-user, not per-machine. The same kiosk machine
--   may run as different logins over its lifetime, but its hardware
--   stays the same — throttle thresholds belong to the hardware.
--
-- last_seen is updated by the host-agent push handler on every poll;
-- the Settings UI uses it to display "rog (online, last seen 3s ago)"
-- vs "rog (offline, last seen 4 days ago)".

CREATE TABLE IF NOT EXISTS host_settings (
    host_label                       TEXT PRIMARY KEY,
    performance_throttle_enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    performance_max_cpu_pct          INTEGER NOT NULL DEFAULT 50
        CHECK (performance_max_cpu_pct BETWEEN 1 AND 95),
    -- Hysteresis band: don't restore demoted tiles until CPU drops
    -- this many percentage points below the threshold. Prevents
    -- demote/restore oscillation around the boundary.
    performance_restore_hysteresis_pct INTEGER NOT NULL DEFAULT 10
        CHECK (performance_restore_hysteresis_pct BETWEEN 0 AND 50),
    last_seen                        TIMESTAMPTZ,
    created_at                       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE host_settings IS
    'Per-machine settings for the kiosk performance manager. One row '
    'per host_label (matches HOST_LABEL in mobius-nvr-host-agent config).';

COMMENT ON COLUMN host_settings.performance_throttle_enabled IS
    'When false the host_state load metrics are recorded but never '
    'trigger tile demotion. UI toggle.';

COMMENT ON COLUMN host_settings.performance_max_cpu_pct IS
    'Sustained CPU load threshold (as % of total cores) above which '
    'the page demotes one tile at a time toward snapshot mode. '
    'UI slider, 1-95.';

COMMENT ON COLUMN host_settings.performance_restore_hysteresis_pct IS
    'Restore-band offset below the throttle threshold. CPU must drop '
    'to (max_cpu_pct - restore_hysteresis_pct) before tiles are '
    'restored, preventing oscillation.';

-- Auto-update updated_at on every UPDATE
CREATE OR REPLACE FUNCTION host_settings_touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS host_settings_touch_updated_at ON host_settings;
CREATE TRIGGER host_settings_touch_updated_at
    BEFORE UPDATE ON host_settings
    FOR EACH ROW EXECUTE FUNCTION host_settings_touch_updated_at();

-- Grants for the application role and PostgREST
GRANT SELECT, INSERT, UPDATE, DELETE ON host_settings TO nvr_api;
GRANT USAGE ON SCHEMA public TO nvr_api;
