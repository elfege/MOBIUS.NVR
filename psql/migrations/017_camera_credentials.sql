-- =============================================================================
-- Migration 017: Camera Credentials Table
-- Stores camera authentication credentials in the database instead of
-- environment variables. Supports per-camera credentials (Eufy) and
-- brand-level credentials (Reolink, UniFi, Amcrest, SV3C).
--
-- Credential types:
--   'camera'  — per-camera RTSP/streaming credentials (keyed by serial)
--   'service' — brand-level or service credentials (keyed by service name)
--
-- The username/password columns store encrypted values at the application
-- layer (Fernet symmetric encryption). The encryption key is derived from
-- NVR_SECRET_KEY, which is the only secret that must be provided via
-- environment variable or .env file.
-- =============================================================================

CREATE TABLE IF NOT EXISTS camera_credentials (
    id BIGSERIAL PRIMARY KEY,
    -- For 'camera' type: the camera serial number
    -- For 'service' type: a stable key like 'reolink_api', 'unifi_protect', 'eufy_bridge'
    credential_key VARCHAR(255) NOT NULL,
    -- Discriminator: 'camera' for per-camera, 'service' for brand/service-level
    credential_type VARCHAR(20) NOT NULL DEFAULT 'camera'
        CHECK (credential_type IN ('camera', 'service')),
    -- Camera vendor/brand (for filtering and UI grouping)
    vendor VARCHAR(50) NOT NULL
        CHECK (vendor IN ('eufy', 'reolink', 'unifi', 'amcrest', 'sv3c', 'system')),
    -- Encrypted credential values (Fernet-encrypted at application layer)
    username_enc TEXT NOT NULL,
    password_enc TEXT NOT NULL,
    -- Human-readable label for UI display
    label VARCHAR(255),
    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    -- Each credential_key + credential_type pair must be unique
    CONSTRAINT camera_credentials_key_type_unique UNIQUE (credential_key, credential_type)
);

CREATE INDEX IF NOT EXISTS idx_camera_credentials_key
    ON camera_credentials(credential_key);
CREATE INDEX IF NOT EXISTS idx_camera_credentials_vendor
    ON camera_credentials(vendor);
CREATE INDEX IF NOT EXISTS idx_camera_credentials_type
    ON camera_credentials(credential_type);

-- Trigger for updated_at
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'update_camera_credentials_updated_at'
    ) THEN
        CREATE TRIGGER update_camera_credentials_updated_at
            BEFORE UPDATE ON camera_credentials
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column();
    END IF;
END $$;

-- Permissions (matching existing pattern)
GRANT SELECT, INSERT, UPDATE, DELETE ON camera_credentials TO nvr_anon;
GRANT USAGE, SELECT ON SEQUENCE camera_credentials_id_seq TO nvr_anon;

-- RLS (matching existing pattern — security enforced at Flask level)
ALTER TABLE camera_credentials ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow all" ON camera_credentials FOR ALL TO nvr_anon USING (true) WITH CHECK (true);
