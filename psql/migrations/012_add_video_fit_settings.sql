-- =============================================================================
-- VIDEO FIT SETTINGS MIGRATION
-- =============================================================================
-- Adds per-camera and per-user video fit mode settings.
--
-- video_fit_mode controls CSS object-fit on the video/img element:
--   'cover' — fills the tile, may crop edges (no deformation, small loss)
--   'fill'  — stretches to fill exactly (no loss, deformation allowed)
--
-- Per-camera setting on cameras table overrides the user default.
-- User default on user_preferences table applies to all cameras without
-- an explicit override.
--
-- Migration: 012_add_video_fit_settings.sql
-- Created: March 07, 2026
--
-- Run with:
--   docker exec -i nvr-db psql -U nvr_api -d nvr < psql/migrations/012_add_video_fit_settings.sql

DO $$
BEGIN
    -- =========================================================================
    -- ADD video_fit_mode TO cameras TABLE
    -- =========================================================================
    -- Per-camera override. NULL means "use user default".
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'cameras' AND column_name = 'video_fit_mode'
    ) THEN
        ALTER TABLE cameras
            ADD COLUMN video_fit_mode VARCHAR(10) DEFAULT NULL
            CHECK (video_fit_mode IN ('cover', 'fill'));
        RAISE NOTICE 'Added video_fit_mode column to cameras table';
    END IF;

    -- =========================================================================
    -- ADD default_video_fit TO user_preferences TABLE
    -- =========================================================================
    -- Global default for the user. Applies to any camera without an explicit
    -- video_fit_mode set. Default is 'cover' (no deformation, minor crop).
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'user_preferences' AND column_name = 'default_video_fit'
    ) THEN
        ALTER TABLE user_preferences
            ADD COLUMN default_video_fit VARCHAR(10) NOT NULL DEFAULT 'cover'
            CHECK (default_video_fit IN ('cover', 'fill'));
        RAISE NOTICE 'Added default_video_fit column to user_preferences table';
    END IF;

    RAISE NOTICE 'Video fit settings migration (012) completed successfully';
END $$;
