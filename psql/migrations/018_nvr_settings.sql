-- NVR Settings table
-- Stores application-level settings like NVR_SECRET_KEY and NVR_LICENSE_KEY.
-- These are generated/set at runtime and persist across container restarts.
-- Replaces the need for secrets.env for these values.

CREATE TABLE IF NOT EXISTS nvr_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Grant access to the API user
GRANT SELECT, INSERT, UPDATE ON nvr_settings TO nvr_api;

-- Upsert function for convenience
CREATE OR REPLACE FUNCTION upsert_setting(p_key TEXT, p_value TEXT)
RETURNS VOID AS $$
BEGIN
    INSERT INTO nvr_settings (key, value, updated_at)
    VALUES (p_key, p_value, NOW())
    ON CONFLICT (key) DO UPDATE SET value = p_value, updated_at = NOW();
END;
$$ LANGUAGE plpgsql;
