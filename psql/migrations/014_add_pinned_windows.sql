-- =============================================================================
-- PINNED WINDOWS MIGRATION
-- =============================================================================
-- Adds pinned_windows to user_preferences table.
--
-- pinned_windows stores the last-known position and size of each camera's
-- floating window (activated when a camera is both pinned and in HD mode).
--
-- Format (JSONB): { "<serial>": { "x": 120, "y": 80, "w": 720, "h": 450 }, ... }
--
-- The frontend merges DB values with localStorage on page load (DB is
-- authoritative for new devices that haven't stored a local position yet).
--
-- Migration: 014_add_pinned_windows.sql
-- Created: March 07, 2026
--
-- Run with:
--   docker exec -i nvr-db psql -U nvr_api -d nvr < psql/migrations/014_add_pinned_windows.sql

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'user_preferences' AND column_name = 'pinned_windows'
    ) THEN
        ALTER TABLE user_preferences
            ADD COLUMN pinned_windows JSONB DEFAULT '{}'::jsonb;
        RAISE NOTICE 'Added pinned_windows column to user_preferences table';
    END IF;

    RAISE NOTICE 'Pinned windows migration (014) completed successfully';
END $$;
