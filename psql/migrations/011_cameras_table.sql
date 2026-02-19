-- =============================================================================
-- CAMERAS TABLE MIGRATION
-- =============================================================================
-- Migrates camera configuration from cameras.json to PostgreSQL database.
-- cameras.json remains as canonical reset source; database is runtime source.
--
-- Migration: 011_cameras_table.sql
-- Created: February 19, 2026
--
-- This migration adds:
-- 1. cameras table (mirrors cameras.json devices structure)
-- 2. camera_state table (runtime state tracking)
-- 3. Extends user_camera_preferences with visibility and display order
-- 4. RLS policies and permissions for new tables
-- 5. Updated_at triggers for new tables
--
-- Run with:
--   docker exec -i nvr-db psql -U nvr_api -d nvr < psql/migrations/011_cameras_table.sql

DO $$
BEGIN
    -- =========================================================================
    -- CAMERAS TABLE
    -- =========================================================================
    -- Canonical runtime source for camera configuration.
    -- Populated from cameras.json on first run, then DB is source of truth.
    -- cameras.json used only for resets and detecting new cameras.
    IF NOT EXISTS (SELECT FROM pg_tables WHERE tablename = 'cameras') THEN
        CREATE TABLE cameras (
            -- Primary key: camera serial number (matches cameras.json device keys)
            serial VARCHAR(255) PRIMARY KEY,

            -- Identity
            name VARCHAR(255) NOT NULL,
            type VARCHAR(100) NOT NULL,  -- eufy, unifi, reolink, sv3c, amcrest
            camera_id VARCHAR(255),
            host VARCHAR(45),
            mac VARCHAR(17),
            packager_path VARCHAR(255),

            -- Streaming configuration
            stream_type VARCHAR(50) NOT NULL DEFAULT 'LL_HLS',
            rtsp_alias VARCHAR(255),
            max_connections INTEGER DEFAULT 1,

            -- Capabilities and features
            capabilities JSONB DEFAULT '[]'::jsonb,
            onvif_port INTEGER,
            true_mjpeg BOOLEAN DEFAULT FALSE,
            power_supply VARCHAR(50),

            -- Display and UI
            hidden BOOLEAN DEFAULT FALSE,
            ui_health_monitor BOOLEAN DEFAULT TRUE,
            reversed_pan BOOLEAN DEFAULT FALSE,
            reversed_tilt BOOLEAN DEFAULT FALSE,

            -- Per-stream-type configuration (complex nested objects)
            ll_hls JSONB DEFAULT '{}'::jsonb,
            mjpeg_snap JSONB DEFAULT '{}'::jsonb,
            neolink JSONB DEFAULT '{}'::jsonb,
            player_settings JSONB DEFAULT '{}'::jsonb,
            rtsp_input JSONB DEFAULT '{}'::jsonb,
            rtsp_output JSONB DEFAULT '{}'::jsonb,
            two_way_audio JSONB DEFAULT '{}'::jsonb,

            -- Power management
            power_cycle_on_failure JSONB DEFAULT '{}'::jsonb,
            power_supply_device_id INTEGER,

            -- Catch-all for any fields not explicitly mapped
            extra_config JSONB DEFAULT '{}'::jsonb,

            -- Metadata
            notes TEXT DEFAULT '',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );

        -- Indexes for common query patterns
        CREATE INDEX idx_cameras_type ON cameras(type);
        CREATE INDEX idx_cameras_hidden ON cameras(hidden) WHERE hidden = false;
        CREATE INDEX idx_cameras_stream_type ON cameras(stream_type);
        CREATE INDEX idx_cameras_capabilities ON cameras USING GIN(capabilities);

        -- Auto-update updated_at trigger (reuses function from migration 002)
        CREATE TRIGGER update_cameras_updated_at
            BEFORE UPDATE ON cameras
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column();

        RAISE NOTICE 'cameras table created';
    END IF;

    -- =========================================================================
    -- CAMERA STATE TABLE
    -- =========================================================================
    -- Runtime state tracking (not configuration).
    -- Ephemeral data: health status, active connections, last seen.
    IF NOT EXISTS (SELECT FROM pg_tables WHERE tablename = 'camera_state') THEN
        CREATE TABLE camera_state (
            camera_serial VARCHAR(255) PRIMARY KEY,
            current_stream_type VARCHAR(50),
            health_status VARCHAR(50) DEFAULT 'unknown',
            last_seen TIMESTAMPTZ,
            active_connections INTEGER DEFAULT 0,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );

        -- Auto-update updated_at trigger
        CREATE TRIGGER update_camera_state_updated_at
            BEFORE UPDATE ON camera_state
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column();

        RAISE NOTICE 'camera_state table created';
    END IF;

    -- =========================================================================
    -- EXTEND USER_CAMERA_PREFERENCES
    -- =========================================================================
    -- Add visibility and display order columns for per-user camera selection
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'user_camera_preferences' AND column_name = 'visible'
    ) THEN
        ALTER TABLE user_camera_preferences
            ADD COLUMN visible BOOLEAN DEFAULT TRUE;
        RAISE NOTICE 'Added visible column to user_camera_preferences';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'user_camera_preferences' AND column_name = 'display_order'
    ) THEN
        ALTER TABLE user_camera_preferences
            ADD COLUMN display_order INTEGER;
        RAISE NOTICE 'Added display_order column to user_camera_preferences';
    END IF;

    -- =========================================================================
    -- ROW LEVEL SECURITY (RLS) POLICIES
    -- =========================================================================
    -- Permissive policies (security enforced at Flask level, consistent with
    -- migrations 007/008 approach)

    -- Cameras table: all authenticated users can read, only Flask/admin can write
    ALTER TABLE cameras ENABLE ROW LEVEL SECURITY;

    DROP POLICY IF EXISTS "Allow all access on cameras" ON cameras;
    CREATE POLICY "Allow all access on cameras"
        ON cameras FOR ALL
        TO nvr_anon
        USING (true)
        WITH CHECK (true);

    -- Camera state table: same permissive policy
    ALTER TABLE camera_state ENABLE ROW LEVEL SECURITY;

    DROP POLICY IF EXISTS "Allow all access on camera_state" ON camera_state;
    CREATE POLICY "Allow all access on camera_state"
        ON camera_state FOR ALL
        TO nvr_anon
        USING (true)
        WITH CHECK (true);

    -- =========================================================================
    -- PERMISSIONS
    -- =========================================================================
    GRANT SELECT, INSERT, UPDATE, DELETE ON cameras TO nvr_anon;
    GRANT SELECT, INSERT, UPDATE, DELETE ON camera_state TO nvr_anon;

    RAISE NOTICE 'Camera database migration (011) completed successfully';

END $$;
