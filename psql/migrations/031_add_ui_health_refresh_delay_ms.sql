-- Migration 031: per-camera tunable delay before the UI health monitor
-- triggers a stream refresh on backend recovery notification.
--
-- Background: when the StreamWatchdog notifies the frontend that a camera
-- has "recovered" (degraded → online transition), `handleBackendRecovery`
-- in stream.js runs a stop+restart cycle on the user-facing player. For
-- streams that are working continuously (e.g. MJPEG via the streaming
-- hub), this is a false positive — the user sees a "Signal Lost" overlay
-- on a live feed.
--
-- This column lets the user defer the refresh by N milliseconds. If the
-- backend reports degraded again before the delay elapses, the recovery
-- is cancelled and no UI churn happens. Default 0 preserves existing
-- behavior (immediate refresh) for backward compatibility.
--
-- Range: 0 (immediate) to 60000 (60s). Suggested values:
--   0       — current behavior (default)
--   2000    — light debounce, hides one-blip transients
--   5000    — moderate, hides short backend hiccups
--   10000   — aggressive, only refresh on sustained outages
--   60000   — never refresh from health monitor (use with ui_health_monitor=false)
--
-- See also: ui_health_monitor (existing column). When ui_health_monitor
-- is false, the refresh path is skipped entirely regardless of this value.

ALTER TABLE cameras
    ADD COLUMN IF NOT EXISTS ui_health_refresh_delay_ms INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN cameras.ui_health_refresh_delay_ms IS
    'Milliseconds to wait after a backend recovery notification before the '
    'frontend triggers a stream refresh. 0 = immediate. Higher values mask '
    'transient backend hiccups for streams that are actually fine.';
