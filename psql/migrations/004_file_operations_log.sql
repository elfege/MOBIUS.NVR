-- Migration 004: Add file_operations_log table
-- Purpose: Audit trail for file operations (migration, deletion, etc.)
-- Date: January 19, 2026

-- =============================================================================
-- FILE OPERATIONS LOG TABLE
-- =============================================================================
-- Tracks all file operations for audit purposes:
-- - Migration from recent to archive tier
-- - Deletion from archive tier
-- - Manual file operations
-- - Error conditions

CREATE TABLE IF NOT EXISTS file_operations_log (
    -- Primary key
    id BIGSERIAL PRIMARY KEY,

    -- Operation type
    operation VARCHAR(20) NOT NULL
        CHECK (operation IN ('migrate', 'delete', 'restore', 'create', 'error', 'reconcile')),

    -- File paths
    source_path TEXT NOT NULL,
    destination_path TEXT,

    -- File metadata
    file_size_bytes BIGINT,

    -- Recording linkage (nullable - file may not have DB entry)
    recording_id BIGINT REFERENCES recordings(id) ON DELETE SET NULL,

    -- Camera identification
    camera_id VARCHAR(50),

    -- Operation context
    reason VARCHAR(100),
    trigger_type VARCHAR(20)
        CHECK (trigger_type IN ('age', 'capacity', 'manual', 'scheduled', 'reconcile')),

    -- Result
    success BOOLEAN DEFAULT true,
    error_message TEXT,

    -- Timestamp
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- INDEXES
-- =============================================================================

-- Query by operation type
CREATE INDEX idx_file_ops_operation ON file_operations_log(operation);

-- Query by camera
CREATE INDEX idx_file_ops_camera ON file_operations_log(camera_id);

-- Query by time (most recent first)
CREATE INDEX idx_file_ops_created ON file_operations_log(created_at DESC);

-- Query failed operations
CREATE INDEX idx_file_ops_failures ON file_operations_log(success)
    WHERE success = false;

-- Query by recording
CREATE INDEX idx_file_ops_recording ON file_operations_log(recording_id)
    WHERE recording_id IS NOT NULL;

-- =============================================================================
-- PERMISSIONS
-- =============================================================================

-- Grant read/write to anonymous role (used by RecordingService via PostgREST)
GRANT SELECT, INSERT ON file_operations_log TO nvr_anon;
GRANT USAGE, SELECT ON SEQUENCE file_operations_log_id_seq TO nvr_anon;

-- Enable Row Level Security
ALTER TABLE file_operations_log ENABLE ROW LEVEL SECURITY;

-- Allow all operations for nvr_anon (single-user system)
CREATE POLICY "Allow all for file_operations_log"
ON file_operations_log
FOR ALL
TO nvr_anon
USING (true)
WITH CHECK (true);

-- =============================================================================
-- COMMENTS
-- =============================================================================

COMMENT ON TABLE file_operations_log IS 'Audit log for all file operations (migration, deletion, etc.)';
COMMENT ON COLUMN file_operations_log.operation IS 'Type: migrate, delete, restore, create, error, reconcile';
COMMENT ON COLUMN file_operations_log.trigger_type IS 'What triggered the operation: age, capacity, manual, scheduled, reconcile';
COMMENT ON COLUMN file_operations_log.reason IS 'Human-readable reason (e.g., "file older than 3 days", "capacity below 20%")';
