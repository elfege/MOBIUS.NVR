-- Migration: Add PTZ Presets Cache table
-- Date: January 5, 2026
-- Description: Caches PTZ presets from ONVIF to reduce camera queries
--              Presets are cached with 6-day TTL

-- Check if table already exists
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_tables WHERE tablename = 'ptz_presets') THEN
        -- Create the table
        CREATE TABLE ptz_presets (
            -- Primary key
            id BIGSERIAL PRIMARY KEY,

            -- Camera identification
            camera_serial VARCHAR(50) NOT NULL,

            -- Preset data
            preset_token VARCHAR(100) NOT NULL,
            preset_name VARCHAR(255),

            -- Cache metadata
            cached_at TIMESTAMPTZ DEFAULT NOW(),

            -- Unique constraint: one record per camera/preset pair
            CONSTRAINT ptz_presets_unique
                UNIQUE (camera_serial, preset_token)
        );

        -- Index for fast lookups by camera
        CREATE INDEX idx_ptz_presets_camera
            ON ptz_presets(camera_serial);

        -- Index for cache expiry checks
        CREATE INDEX idx_ptz_presets_cached_at
            ON ptz_presets(cached_at);

        -- Grant permissions
        GRANT SELECT, INSERT, UPDATE, DELETE ON ptz_presets TO nvr_anon;
        GRANT USAGE, SELECT ON SEQUENCE ptz_presets_id_seq TO nvr_anon;

        -- RLS policy
        ALTER TABLE ptz_presets ENABLE ROW LEVEL SECURITY;

        CREATE POLICY "Allow all for ptz_presets"
        ON ptz_presets
        FOR ALL
        TO nvr_anon
        USING (true)
        WITH CHECK (true);

        RAISE NOTICE 'Created ptz_presets table';
    ELSE
        RAISE NOTICE 'ptz_presets table already exists, skipping';
    END IF;
END $$;
