-- Migration: Add presence table for household presence tracking
-- Run: psql -h localhost -U nvr -d nvr_db -f psql/migrations/003_add_presence_table.sql
-- Or via Docker: docker exec nvr-postgres psql -U nvr -d nvr_db -f /migrations/003_add_presence_table.sql

-- =============================================================================
-- PRESENCE SENSORS TABLE
-- =============================================================================
-- Tracks presence status for household members
-- Integrates with Hubitat presence sensors and supports manual toggle

-- Check if table already exists
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'presence') THEN
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

        RAISE NOTICE 'Created presence table';
    ELSE
        RAISE NOTICE 'Presence table already exists';
    END IF;
END
$$;

-- Insert default entries for Elfege and Jessica if not already present
INSERT INTO presence (person_name, is_present)
VALUES
    ('Elfege', false),
    ('Jessica', false)
ON CONFLICT (person_name) DO NOTHING;

SELECT 'Migration 003 complete - presence table ready' AS status;
