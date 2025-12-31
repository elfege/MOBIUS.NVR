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

-- Grant table permissions
GRANT INSERT, UPDATE ON recordings TO nvr_anon;

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
-- INITIALIZATION COMPLETE
-- =============================================================================