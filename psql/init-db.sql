-- NVR Recording System Database Schema
-- PostgreSQL 16+ initialization script
-- This file is automatically executed when the database is first created

-- Create roles for PostgREST
CREATE ROLE nvr_anon NOLOGIN;
CREATE ROLE nvr_api LOGIN PASSWORD 'PLACEHOLDER_PASSWORD_REPLACED_BY_ENV';

-- Grant basic permissions
GRANT USAGE ON SCHEMA public TO nvr_anon;
GRANT nvr_anon TO nvr_api;

-- =============================================================================
-- RECORDINGS TABLE
-- =============================================================================
CREATE TABLE recordings (
    -- Primary key
    id BIGSERIAL PRIMARY KEY,
    
    -- Camera identification
    camera_id VARCHAR(50) NOT NULL,
    camera_name VARCHAR(100),
    
    -- Temporal data
    timestamp TIMESTAMPTZ NOT NULL,
    end_timestamp TIMESTAMPTZ,
    duration_seconds INTEGER,
    
    -- File location
    file_path TEXT NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    storage_tier VARCHAR(10) NOT NULL 
        CHECK (storage_tier IN ('recent', 'archive')),
    file_size_bytes BIGINT,
    
    -- Motion metadata
    motion_triggered BOOLEAN DEFAULT true,
    motion_source VARCHAR(20) 
        CHECK (motion_source IN ('onvif', 'ffmpeg', 'eufy_bridge', 'manual', NULL)),
    motion_event_id BIGINT,
    
    -- Encoding information
    codec VARCHAR(20),
    resolution VARCHAR(20),
    fps INTEGER,
    bitrate_kbps INTEGER,
    
    -- Recording status
    status VARCHAR(20) DEFAULT 'recording' 
        CHECK (status IN ('recording', 'completed', 'archived', 'error')),
    error_message TEXT,
    
    -- Timestamps for lifecycle tracking
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    archived_at TIMESTAMPTZ,

    -- Unique constraint
    CONSTRAINT recordings_camera_timestamp_unique 
        UNIQUE (camera_id, timestamp)
);

-- Performance indexes for recordings
CREATE INDEX idx_recordings_camera_timestamp 
    ON recordings(camera_id, timestamp DESC);

CREATE INDEX idx_recordings_timestamp 
    ON recordings(timestamp DESC);

CREATE INDEX idx_recordings_storage_tier 
    ON recordings(storage_tier);

CREATE INDEX idx_recordings_status 
    ON recordings(status);

CREATE INDEX idx_recordings_motion_source 
    ON recordings(motion_source) 
    WHERE motion_source IS NOT NULL;

-- Composite index for common queries
CREATE INDEX idx_recordings_camera_tier_timestamp
    ON recordings(camera_id, storage_tier, timestamp DESC);

-- Index for playback queries (find recent recordings)
CREATE INDEX idx_recordings_updated_at
    ON recordings(updated_at DESC);

-- Auto-update updated_at on row changes
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_recordings_updated_at
    BEFORE UPDATE ON recordings
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- MOTION EVENTS TABLE
-- =============================================================================
CREATE TABLE motion_events (
    -- Primary key
    id BIGSERIAL PRIMARY KEY,
    
    -- Camera identification
    camera_id VARCHAR(50) NOT NULL,
    
    -- Event timing
    timestamp TIMESTAMPTZ NOT NULL,
    
    -- Motion source
    source VARCHAR(20) NOT NULL 
        CHECK (source IN ('onvif', 'ffmpeg', 'eufy_bridge', 'manual')),
    
    -- Source-specific confidence metrics
    confidence FLOAT,
    scene_score FLOAT,
    
    -- Recording linkage
    triggered_recording BOOLEAN DEFAULT false,
    recording_id BIGINT REFERENCES recordings(id),
    
    -- ONVIF-specific metadata
    onvif_rule_name VARCHAR(100),
    onvif_event_type VARCHAR(100),
    
    -- Creation timestamp
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Performance indexes for motion_events
CREATE INDEX idx_motion_events_camera_timestamp 
    ON motion_events(camera_id, timestamp DESC);

CREATE INDEX idx_motion_events_timestamp 
    ON motion_events(timestamp DESC);

CREATE INDEX idx_motion_events_source 
    ON motion_events(source);

CREATE INDEX idx_motion_events_recording 
    ON motion_events(recording_id) 
    WHERE recording_id IS NOT NULL;

-- =============================================================================
-- PERMISSIONS
-- =============================================================================

-- Grant SELECT to anonymous role (read-only public access)
GRANT SELECT ON recordings, motion_events TO nvr_anon;

-- Grant full CRUD to API role
GRANT INSERT, UPDATE, DELETE ON recordings, motion_events TO nvr_api;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO nvr_api;

-- =============================================================================
-- ROW LEVEL SECURITY (RLS)
-- =============================================================================
-- Enable RLS for future multi-user support
ALTER TABLE recordings ENABLE ROW LEVEL SECURITY;
ALTER TABLE motion_events ENABLE ROW LEVEL SECURITY;

-- RLS policies (currently allow all access)
CREATE POLICY "Allow all for nvr_api" ON recordings
    FOR ALL
    TO nvr_api
    USING (true);

CREATE POLICY "Allow all for nvr_api" ON motion_events
    FOR ALL
    TO nvr_api
    USING (true);

-- Allow anonymous read access
CREATE POLICY "Allow read for nvr_anon" ON recordings
    FOR SELECT
    TO nvr_anon
    USING (true);

CREATE POLICY "Allow read for nvr_anon" ON motion_events
    FOR SELECT
    TO nvr_anon
    USING (true);

-- ============================================
-- Recording Service Permissions
-- ============================================
-- RecordingService uses PostgREST unauthenticated (nvr_anon role)
-- Grant write permissions for recordings table

-- Grant table permissions (DELETE needed for storage migration reconciliation)
GRANT INSERT, UPDATE, DELETE ON recordings TO nvr_anon;

-- Grant sequence permissions (for auto-increment ID)
GRANT USAGE, SELECT ON SEQUENCE recordings_id_seq TO nvr_anon;

-- Create RLS policy allowing nvr_anon to insert/update
CREATE POLICY "Allow insert/update for nvr_anon" 
ON recordings 
FOR ALL 
TO nvr_anon 
USING (true) 
WITH CHECK (true);

-- Note: nvr_anon already has SELECT via existing read policy

-- =============================================================================
-- PTZ CLIENT LATENCY TABLE
-- =============================================================================
-- Stores learned ONVIF PTZ latency per client/camera pair
-- Used to optimize stop timing based on observed response times

CREATE TABLE ptz_client_latency (
    -- Primary key
    id BIGSERIAL PRIMARY KEY,

    -- Client identification (UUID generated by browser, stored in localStorage)
    client_uuid VARCHAR(36) NOT NULL,

    -- Camera identification
    camera_serial VARCHAR(50) NOT NULL,

    -- Latency statistics
    avg_latency_ms INTEGER NOT NULL DEFAULT 1000,
    samples JSONB DEFAULT '[]'::jsonb,
    sample_count INTEGER DEFAULT 0,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    -- Unique constraint: one record per client/camera pair
    CONSTRAINT ptz_client_latency_unique
        UNIQUE (client_uuid, camera_serial)
);

-- Index for fast lookups
CREATE INDEX idx_ptz_client_latency_lookup
    ON ptz_client_latency(client_uuid, camera_serial);

-- Auto-update updated_at on row changes
CREATE TRIGGER update_ptz_client_latency_updated_at
    BEFORE UPDATE ON ptz_client_latency
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Grant permissions
GRANT SELECT, INSERT, UPDATE ON ptz_client_latency TO nvr_anon;
GRANT USAGE, SELECT ON SEQUENCE ptz_client_latency_id_seq TO nvr_anon;

-- RLS policy
ALTER TABLE ptz_client_latency ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow all for ptz_client_latency"
ON ptz_client_latency
FOR ALL
TO nvr_anon
USING (true)
WITH CHECK (true);

-- =============================================================================
-- PTZ PRESETS CACHE TABLE
-- =============================================================================
-- Caches PTZ presets from ONVIF to reduce camera queries
-- Presets are cached with 6-day TTL

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

-- =============================================================================
-- PRESENCE SENSORS TABLE
-- =============================================================================
-- Tracks presence status for household members
-- Integrates with Hubitat presence sensors and supports manual toggle

CREATE TABLE presence (
    -- Primary key
    id BIGSERIAL PRIMARY KEY,

    -- Person identification
    person_name VARCHAR(100) NOT NULL UNIQUE,

    -- Presence status
    is_present BOOLEAN DEFAULT false,

    -- Hubitat device integration (optional)
    hubitat_device_id VARCHAR(50),

    -- Timestamps
    last_changed_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),

    -- Source of last change
    last_changed_by VARCHAR(50) DEFAULT 'manual'
        CHECK (last_changed_by IN ('manual', 'hubitat', 'api'))
);

-- Index for fast lookups
CREATE INDEX idx_presence_person_name
    ON presence(person_name);

-- Grant permissions
GRANT SELECT, INSERT, UPDATE, DELETE ON presence TO nvr_anon;
GRANT USAGE, SELECT ON SEQUENCE presence_id_seq TO nvr_anon;

-- RLS policy
ALTER TABLE presence ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow all for presence"
ON presence
FOR ALL
TO nvr_anon
USING (true)
WITH CHECK (true);

-- =============================================================================
-- INITIALIZATION COMPLETE
-- =============================================================================