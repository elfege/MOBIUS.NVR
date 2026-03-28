-- Migration 022: Add streaming_hub_global setting to nvr_settings
--
-- streaming_hub_global controls which streaming hub ALL cameras use,
-- overriding per-camera streaming_hub when set.
--
-- Values:
--   NULL / missing row  → use per-camera streaming_hub (default)
--   'go2rtc'            → force all cameras to go2rtc
--   'mediamtx'          → force all cameras to mediamtx
--
-- The nvr_settings.value column does not allow NULL, so we use an empty
-- string '' to mean "no global override — use per-camera setting".
-- The application maps '' → null → per-camera fallback.

-- Allow NULL in value column so global settings with no value can be stored cleanly.
-- Existing rows are NOT NULL so this is a safe change.
ALTER TABLE nvr_settings ALTER COLUMN value DROP NOT NULL;

-- Seed the global streaming hub setting (NULL = per-camera, no override).
INSERT INTO nvr_settings (key, value)
VALUES ('streaming_hub_global', NULL)
ON CONFLICT (key) DO NOTHING;
