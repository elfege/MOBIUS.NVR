-- =============================================================================
-- ADD GO2RTC STREAM TYPE MIGRATION
-- =============================================================================
-- Adds 'GO2RTC' to the preferred_stream_type CHECK constraint in
-- user_camera_preferences table. GO2RTC enables low-latency WebRTC streaming
-- via go2rtc container, bypassing FFmpeg + MediaMTX for Neolink cameras.
--
-- Migration: 016_add_go2rtc_stream_type.sql
-- Created: March 10, 2026

DO $$
BEGIN
    -- Drop the existing CHECK constraint and recreate with GO2RTC included
    IF EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'user_camera_preferences'
        AND constraint_type = 'CHECK'
    ) THEN
        -- Find and drop the constraint by name
        -- PostgreSQL auto-names CHECK constraints as tablename_columnname_check
        ALTER TABLE user_camera_preferences
            DROP CONSTRAINT IF EXISTS user_camera_preferences_preferred_stream_type_check;

        ALTER TABLE user_camera_preferences
            ADD CONSTRAINT user_camera_preferences_preferred_stream_type_check
            CHECK (preferred_stream_type IN ('MJPEG', 'HLS', 'LL_HLS', 'WEBRTC', 'NEOLINK', 'NEOLINK_LL_HLS', 'GO2RTC'));

        RAISE NOTICE 'Updated CHECK constraint to include GO2RTC';
    ELSE
        RAISE NOTICE 'No CHECK constraint found on user_camera_preferences — skipping';
    END IF;

    RAISE NOTICE 'GO2RTC stream type migration (016) completed successfully';
END $$;
