-- =============================================================================
-- PINNED CAMERA MIGRATION
-- =============================================================================
-- Adds pinned_camera to user_preferences table.
--
-- pinned_camera stores the serial of the camera the user has "pinned" in
-- expanded modal view. When set:
--   - The camera auto-expands on every page load (after streams initialize)
--   - Backdrop click is disabled for that camera (must unpin to close)
--   - Cleared by unpinning via the pin button in the expanded modal
--
-- Migration: 013_add_pinned_camera.sql
-- Created: March 07, 2026
--
-- Run with:
--   docker exec -i nvr-db psql -U nvr_api -d nvr < psql/migrations/013_add_pinned_camera.sql

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'user_preferences' AND column_name = 'pinned_camera'
    ) THEN
        ALTER TABLE user_preferences
            ADD COLUMN pinned_camera VARCHAR(255) DEFAULT NULL;
        RAISE NOTICE 'Added pinned_camera column to user_preferences table';
    END IF;

    RAISE NOTICE 'Pinned camera migration (013) completed successfully';
END $$;
