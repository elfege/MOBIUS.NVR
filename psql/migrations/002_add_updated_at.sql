-- Migration: Add updated_at column to recordings table
-- This column exists in init-db.sql but was added after database creation
-- Run with: docker exec -i nvr-db psql -U nvr_api -d nvr < psql/migrations/002_add_updated_at.sql

-- Add the missing column
ALTER TABLE recordings
ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

-- Add index for updated_at lookups
CREATE INDEX IF NOT EXISTS idx_recordings_updated_at
    ON recordings(updated_at DESC);

-- Backfill: Set updated_at = created_at for existing records
UPDATE recordings SET updated_at = created_at WHERE updated_at IS NULL;

-- Ensure trigger function exists (idempotent)
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Create trigger if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'update_recordings_updated_at'
    ) THEN
        CREATE TRIGGER update_recordings_updated_at
            BEFORE UPDATE ON recordings
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column();
    END IF;
END $$;
