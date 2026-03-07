-- =============================================================================
-- NVR Database Schema - Complete Initialization
-- PostgreSQL 16+ initialization script
-- This file is automatically executed when the database is first created.
--
-- IMPORTANT: This file is the single source of truth for the DB schema.
-- When adding new tables or altering existing ones, update this file
-- AND create a migration file in psql/migrations/ for existing deployments.
--
-- Last updated: March 7, 2026 (consolidated migrations 001-013)
-- =============================================================================

-- =============================================================================
-- ROLES
-- =============================================================================

DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'nvr_anon') THEN
        CREATE ROLE nvr_anon NOLOGIN;
    END IF;
END $$;
DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'nvr_api') THEN
        CREATE ROLE nvr_api LOGIN PASSWORD 'PLACEHOLDER_PASSWORD_REPLACED_BY_ENV';
    END IF;
END $$;

GRANT USAGE ON SCHEMA public TO nvr_anon;
GRANT nvr_anon TO nvr_api;

-- =============================================================================
-- SHARED FUNCTIONS
-- =============================================================================

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE 'plpgsql';

-- =============================================================================
-- RECORDINGS TABLE
-- =============================================================================

CREATE TABLE recordings (
    id BIGSERIAL PRIMARY KEY,
    camera_id VARCHAR(50) NOT NULL,
    camera_name VARCHAR(100),
    timestamp TIMESTAMPTZ NOT NULL,
    end_timestamp TIMESTAMPTZ,
    duration_seconds INTEGER,
    file_path TEXT NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    storage_tier VARCHAR(10) NOT NULL
        CHECK (storage_tier IN ('recent', 'archive')),
    file_size_bytes BIGINT,
    motion_triggered BOOLEAN DEFAULT true,
    motion_source VARCHAR(20)
        CHECK (motion_source IN ('onvif', 'ffmpeg', 'eufy_bridge', 'manual', NULL)),
    motion_event_id BIGINT,
    codec VARCHAR(20),
    resolution VARCHAR(20),
    fps INTEGER,
    bitrate_kbps INTEGER,
    status VARCHAR(20) DEFAULT 'recording'
        CHECK (status IN ('recording', 'completed', 'archived', 'error')),
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    archived_at TIMESTAMPTZ,
    CONSTRAINT recordings_camera_timestamp_unique UNIQUE (camera_id, timestamp)
);

CREATE INDEX idx_recordings_camera_timestamp ON recordings(camera_id, timestamp DESC);
CREATE INDEX idx_recordings_timestamp ON recordings(timestamp DESC);
CREATE INDEX idx_recordings_storage_tier ON recordings(storage_tier);
CREATE INDEX idx_recordings_status ON recordings(status);
CREATE INDEX idx_recordings_motion_source ON recordings(motion_source) WHERE motion_source IS NOT NULL;
CREATE INDEX idx_recordings_camera_tier_timestamp ON recordings(camera_id, storage_tier, timestamp DESC);
CREATE INDEX idx_recordings_updated_at ON recordings(updated_at DESC);

CREATE TRIGGER update_recordings_updated_at
    BEFORE UPDATE ON recordings
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- MOTION EVENTS TABLE
-- =============================================================================

CREATE TABLE motion_events (
    id BIGSERIAL PRIMARY KEY,
    camera_id VARCHAR(50) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    source VARCHAR(20) NOT NULL
        CHECK (source IN ('onvif', 'ffmpeg', 'eufy_bridge', 'manual')),
    confidence FLOAT,
    scene_score FLOAT,
    triggered_recording BOOLEAN DEFAULT false,
    recording_id BIGINT REFERENCES recordings(id),
    onvif_rule_name VARCHAR(100),
    onvif_event_type VARCHAR(100),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_motion_events_camera_timestamp ON motion_events(camera_id, timestamp DESC);
CREATE INDEX idx_motion_events_timestamp ON motion_events(timestamp DESC);
CREATE INDEX idx_motion_events_source ON motion_events(source);
CREATE INDEX idx_motion_events_recording ON motion_events(recording_id) WHERE recording_id IS NOT NULL;

-- =============================================================================
-- PTZ CLIENT LATENCY TABLE
-- =============================================================================

CREATE TABLE ptz_client_latency (
    id BIGSERIAL PRIMARY KEY,
    client_uuid VARCHAR(36) NOT NULL,
    camera_serial VARCHAR(50) NOT NULL,
    avg_latency_ms INTEGER NOT NULL DEFAULT 1000,
    samples JSONB DEFAULT '[]'::jsonb,
    sample_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT ptz_client_latency_unique UNIQUE (client_uuid, camera_serial)
);

CREATE INDEX idx_ptz_client_latency_lookup ON ptz_client_latency(client_uuid, camera_serial);

CREATE TRIGGER update_ptz_client_latency_updated_at
    BEFORE UPDATE ON ptz_client_latency
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- PTZ PRESETS CACHE TABLE
-- =============================================================================

CREATE TABLE ptz_presets (
    id BIGSERIAL PRIMARY KEY,
    camera_serial VARCHAR(50) NOT NULL,
    preset_token VARCHAR(100) NOT NULL,
    preset_name VARCHAR(255),
    cached_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT ptz_presets_unique UNIQUE (camera_serial, preset_token)
);

CREATE INDEX idx_ptz_presets_camera ON ptz_presets(camera_serial);
CREATE INDEX idx_ptz_presets_cached_at ON ptz_presets(cached_at);

-- =============================================================================
-- PRESENCE TABLE
-- =============================================================================

CREATE TABLE presence (
    id BIGSERIAL PRIMARY KEY,
    person_name VARCHAR(100) NOT NULL UNIQUE,
    is_present BOOLEAN DEFAULT false,
    hubitat_device_id VARCHAR(50),
    last_changed_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_changed_by VARCHAR(50) DEFAULT 'manual'
        CHECK (last_changed_by IN ('manual', 'hubitat', 'api'))
);

CREATE INDEX idx_presence_person_name ON presence(person_name);

INSERT INTO presence (person_name, is_present) VALUES
    ('Elfege', false),
    ('Jessica', false)
ON CONFLICT (person_name) DO NOTHING;

-- =============================================================================
-- FILE OPERATIONS LOG TABLE
-- =============================================================================

CREATE TABLE file_operations_log (
    id BIGSERIAL PRIMARY KEY,
    operation VARCHAR(20) NOT NULL
        CHECK (operation IN ('migrate', 'delete', 'restore', 'create', 'error', 'reconcile')),
    source_path TEXT NOT NULL,
    destination_path TEXT,
    file_size_bytes BIGINT,
    recording_id BIGINT REFERENCES recordings(id) ON DELETE SET NULL,
    camera_id VARCHAR(50),
    reason VARCHAR(100),
    trigger_type VARCHAR(20)
        CHECK (trigger_type IN ('age', 'capacity', 'manual', 'scheduled', 'reconcile')),
    success BOOLEAN DEFAULT true,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_file_ops_operation ON file_operations_log(operation);
CREATE INDEX idx_file_ops_camera ON file_operations_log(camera_id);
CREATE INDEX idx_file_ops_created ON file_operations_log(created_at DESC);
CREATE INDEX idx_file_ops_failures ON file_operations_log(success) WHERE success = false;
CREATE INDEX idx_file_ops_recording ON file_operations_log(recording_id) WHERE recording_id IS NOT NULL;

-- =============================================================================
-- USERS TABLE
-- =============================================================================

CREATE TABLE users (
    id BIGSERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(10) NOT NULL CHECK (role IN ('admin', 'user')),
    must_change_password BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TRIGGER update_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Default accounts:
--   admin / admin  (must_change_password = true → forced change on first login)
--   view  / view   (read-only user account)
INSERT INTO users (username, password_hash, role, must_change_password) VALUES
    ('admin', '$2b$12$Ton4Soqs/mkZbpyOUaQI0.Zs19b0CvFvYQzymcExvd60zKce1ULrG', 'admin', true),
    ('view',  '$2b$12$VRm8r/UCO2pMZv6orf9KUO9JDHC6N1POA/UMDJk7UH0ddT9kZAV3O', 'user',  false)
ON CONFLICT (username) DO NOTHING;

-- =============================================================================
-- USER SESSIONS TABLE
-- =============================================================================

CREATE TABLE user_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_activity TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ DEFAULT NULL,
    ip_address INET,
    user_agent TEXT,
    is_active BOOLEAN DEFAULT true
);

CREATE INDEX idx_user_sessions_lookup
    ON user_sessions(id, user_id, is_active)
    WHERE is_active = true;

CREATE TRIGGER update_sessions_last_activity
    BEFORE UPDATE ON user_sessions
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- USER CAMERA PREFERENCES TABLE
-- =============================================================================

CREATE TABLE user_camera_preferences (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    camera_serial VARCHAR(50) NOT NULL,
    preferred_stream_type VARCHAR(20) NOT NULL
        CHECK (preferred_stream_type IN ('MJPEG', 'HLS', 'LL_HLS', 'WEBRTC', 'NEOLINK', 'NEOLINK_LL_HLS')),
    visible BOOLEAN DEFAULT TRUE,
    display_order INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT user_camera_unique UNIQUE(user_id, camera_serial)
);

CREATE INDEX idx_user_camera_prefs_user ON user_camera_preferences(user_id);

CREATE TRIGGER update_user_camera_prefs_updated_at
    BEFORE UPDATE ON user_camera_preferences
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- USER CAMERA ACCESS TABLE
-- =============================================================================

CREATE TABLE user_camera_access (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    camera_serial VARCHAR(50) NOT NULL,
    allowed BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT user_camera_access_unique UNIQUE(user_id, camera_serial)
);

CREATE INDEX idx_user_camera_access_user ON user_camera_access(user_id);

CREATE TRIGGER update_user_camera_access_updated_at
    BEFORE UPDATE ON user_camera_access
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- USER PREFERENCES TABLE
-- =============================================================================

CREATE TABLE user_preferences (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    hidden_cameras JSONB DEFAULT '[]'::jsonb,
    hd_cameras JSONB DEFAULT '[]'::jsonb,
    -- Video fit mode default: 'cover' (crop edges, no deform) or 'fill' (stretch, no crop)
    default_video_fit VARCHAR(10) NOT NULL DEFAULT 'cover' CHECK (default_video_fit IN ('cover', 'fill')),
    -- Pinned camera: auto-expands on load, backdrop click blocked while set
    pinned_camera VARCHAR(255) DEFAULT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_user_preferences_user ON user_preferences(user_id);

CREATE TRIGGER update_user_preferences_updated_at
    BEFORE UPDATE ON user_preferences
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- CAMERAS TABLE
-- =============================================================================

CREATE TABLE cameras (
    serial VARCHAR(255) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    type VARCHAR(100) NOT NULL,
    camera_id VARCHAR(255),
    host VARCHAR(45),
    mac VARCHAR(17),
    packager_path VARCHAR(255),
    stream_type VARCHAR(50) NOT NULL DEFAULT 'LL_HLS',
    rtsp_alias VARCHAR(255),
    max_connections INTEGER DEFAULT 1,
    capabilities JSONB DEFAULT '[]'::jsonb,
    onvif_port INTEGER,
    true_mjpeg BOOLEAN DEFAULT FALSE,
    power_supply VARCHAR(50),
    hidden BOOLEAN DEFAULT FALSE,
    ui_health_monitor BOOLEAN DEFAULT TRUE,
    reversed_pan BOOLEAN DEFAULT FALSE,
    reversed_tilt BOOLEAN DEFAULT FALSE,
    ll_hls JSONB DEFAULT '{}'::jsonb,
    mjpeg_snap JSONB DEFAULT '{}'::jsonb,
    neolink JSONB DEFAULT '{}'::jsonb,
    player_settings JSONB DEFAULT '{}'::jsonb,
    rtsp_input JSONB DEFAULT '{}'::jsonb,
    rtsp_output JSONB DEFAULT '{}'::jsonb,
    two_way_audio JSONB DEFAULT '{}'::jsonb,
    power_cycle_on_failure JSONB DEFAULT '{}'::jsonb,
    power_supply_device_id INTEGER,
    extra_config JSONB DEFAULT '{}'::jsonb,
    notes TEXT DEFAULT '',
    -- Per-camera video fit override: 'cover'|'fill'|NULL (NULL = use user default)
    video_fit_mode VARCHAR(10) DEFAULT NULL CHECK (video_fit_mode IN ('cover', 'fill')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_cameras_type ON cameras(type);
CREATE INDEX idx_cameras_hidden ON cameras(hidden) WHERE hidden = false;
CREATE INDEX idx_cameras_stream_type ON cameras(stream_type);
CREATE INDEX idx_cameras_capabilities ON cameras USING GIN(capabilities);

CREATE TRIGGER update_cameras_updated_at
    BEFORE UPDATE ON cameras
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- CAMERA STATE TABLE
-- =============================================================================

CREATE TABLE camera_state (
    camera_serial VARCHAR(255) PRIMARY KEY,
    current_stream_type VARCHAR(50),
    health_status VARCHAR(50) DEFAULT 'unknown',
    last_seen TIMESTAMPTZ,
    active_connections INTEGER DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TRIGGER update_camera_state_updated_at
    BEFORE UPDATE ON camera_state
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- PERMISSIONS
-- =============================================================================

GRANT SELECT ON recordings, motion_events TO nvr_anon;
GRANT INSERT, UPDATE, DELETE ON recordings, motion_events TO nvr_anon;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO nvr_api;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO nvr_anon;

GRANT SELECT, INSERT, UPDATE ON ptz_client_latency TO nvr_anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON ptz_presets TO nvr_anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON presence TO nvr_anon;
GRANT SELECT, INSERT ON file_operations_log TO nvr_anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON users TO nvr_anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON user_sessions TO nvr_anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON user_camera_preferences TO nvr_anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON user_camera_access TO nvr_anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON user_preferences TO nvr_anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON cameras TO nvr_anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON camera_state TO nvr_anon;

-- =============================================================================
-- ROW LEVEL SECURITY
-- =============================================================================

ALTER TABLE recordings ENABLE ROW LEVEL SECURITY;
ALTER TABLE motion_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE ptz_client_latency ENABLE ROW LEVEL SECURITY;
ALTER TABLE ptz_presets ENABLE ROW LEVEL SECURITY;
ALTER TABLE presence ENABLE ROW LEVEL SECURITY;
ALTER TABLE file_operations_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_camera_preferences ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_camera_access ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_preferences ENABLE ROW LEVEL SECURITY;
ALTER TABLE cameras ENABLE ROW LEVEL SECURITY;
ALTER TABLE camera_state ENABLE ROW LEVEL SECURITY;

-- Permissive policies for all tables (security enforced at Flask/PostgREST level)
CREATE POLICY "Allow all" ON recordings FOR ALL TO nvr_anon USING (true) WITH CHECK (true);
CREATE POLICY "Allow all" ON motion_events FOR ALL TO nvr_anon USING (true) WITH CHECK (true);
CREATE POLICY "Allow all" ON ptz_client_latency FOR ALL TO nvr_anon USING (true) WITH CHECK (true);
CREATE POLICY "Allow all" ON ptz_presets FOR ALL TO nvr_anon USING (true) WITH CHECK (true);
CREATE POLICY "Allow all" ON presence FOR ALL TO nvr_anon USING (true) WITH CHECK (true);
CREATE POLICY "Allow all" ON file_operations_log FOR ALL TO nvr_anon USING (true) WITH CHECK (true);
CREATE POLICY "Allow all" ON users FOR ALL TO nvr_anon USING (true) WITH CHECK (true);
CREATE POLICY "Allow all" ON user_sessions FOR ALL TO nvr_anon USING (true) WITH CHECK (true);
CREATE POLICY "Allow all" ON user_camera_preferences FOR ALL TO nvr_anon USING (true) WITH CHECK (true);
CREATE POLICY "Allow all" ON user_camera_access FOR ALL TO nvr_anon USING (true) WITH CHECK (true);
CREATE POLICY "Allow all" ON user_preferences FOR ALL TO nvr_anon USING (true) WITH CHECK (true);
CREATE POLICY "Allow all" ON cameras FOR ALL TO nvr_anon USING (true) WITH CHECK (true);
CREATE POLICY "Allow all" ON camera_state FOR ALL TO nvr_anon USING (true) WITH CHECK (true);

-- =============================================================================
-- INITIALIZATION COMPLETE
-- =============================================================================
-- Tables created: recordings, motion_events, ptz_client_latency, ptz_presets,
--   presence, file_operations_log, users, user_sessions, user_camera_preferences,
--   user_camera_access, user_preferences, cameras, camera_state
-- Columns added (012-013): cameras.video_fit_mode, user_preferences.default_video_fit,
--   user_preferences.pinned_camera
-- Default accounts: admin/admin (must change), view/view
-- =============================================================================
