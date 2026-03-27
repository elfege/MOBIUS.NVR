-- =============================================================================
-- ADD streaming_hub COLUMN TO cameras TABLE
-- =============================================================================
-- Supports per-camera routing between MediaMTX and go2rtc as the streaming
-- relay hub. go2rtc cameras bypass FFmpeg+MediaMTX for viewing; MediaMTX
-- cameras use the existing FFmpeg→MediaMTX pipeline.
--
-- Values: 'mediamtx' (default), 'go2rtc'
--
-- Migration: 019_add_streaming_hub_column.sql
-- Created: March 27, 2026

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'cameras' AND column_name = 'streaming_hub'
    ) THEN
        ALTER TABLE cameras ADD COLUMN streaming_hub VARCHAR(20) DEFAULT 'mediamtx';
        RAISE NOTICE 'Added streaming_hub column to cameras table';
    ELSE
        RAISE NOTICE 'streaming_hub column already exists';
    END IF;
END $$;
