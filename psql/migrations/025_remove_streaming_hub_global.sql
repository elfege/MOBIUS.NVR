-- Migration 025: Remove streaming_hub_global from nvr_settings
--
-- The global hub override was dangerous — one wrong value could take down
-- ALL cameras at once. Replaced with per-camera hub assignment UI.
-- The cameras.streaming_hub column (per-camera) remains the sole hub selector.

DELETE FROM nvr_settings WHERE key = 'streaming_hub_global';
