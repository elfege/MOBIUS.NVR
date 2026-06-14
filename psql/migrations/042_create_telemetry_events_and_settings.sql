-- Migration 042: per-layer telemetry event log + admin-controlled settings.
--
-- Operator-requested 2026-06-14. Adds the infrastructure for an event-driven
-- diagnostic log that can localize long-uptime streaming failures (the
-- "Terrace Shed 404 from container, VLC works from LAN, restart-fixes-briefly"
-- class of bug — observed across hubs, so the failure is upstream of hub
-- choice). Design doc:
-- docs/plans/per_layer_telemetry_event_log_for_localizing_long_uptime_streaming_entropy_with_bounded_postgres_retention.md
--
-- Constraints baked in:
--   - DISABLED BY DEFAULT. No probe runs and no row is written until an admin
--     flips telemetry_enabled in the Data tab of the global settings modal.
--   - BOUNDED RETENTION. Cleanup tick respects the admin-set max-size cap and
--     retention window. Defaults: 100 MB cap, 7-day window.
--   - ADMIN-ONLY. The /api/telemetry/* endpoints enforce role='admin',
--     matching the existing pattern in audit_routes.py / storage.py.
--
-- The nvr_settings audit trigger (migration 036) automatically captures
-- changes to the three keys we insert below — operator gets a free trail of
-- who toggled telemetry and when.

-- ---------------------------------------------------------------------------
-- 1. telemetry_events — event log
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS telemetry_events (
    id           BIGSERIAL    PRIMARY KEY,
    ts           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    category     VARCHAR(40)  NOT NULL,
    subcategory  VARCHAR(60),
    camera_id    VARCHAR(255),
    severity     VARCHAR(16)  NOT NULL DEFAULT 'info',
    payload      JSONB        NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT telemetry_events_severity_chk
        CHECK (severity IN ('info', 'warning', 'error'))
);

COMMENT ON TABLE telemetry_events IS
    'Per-layer diagnostic event log for streaming-hub entropy localization. '
    'Event-driven for transitions, periodic-snapshot for resource counters. '
    'Bounded by admin-set max-size cap + retention window. Disabled by default.';

COMMENT ON COLUMN telemetry_events.category IS
    'Coarse layer/source: camera_state | publisher | ffmpeg | mediamtx_path '
    '| go2rtc_path | rtsp_probe | resource_snapshot | docker_conntrack';

COMMENT ON COLUMN telemetry_events.subcategory IS
    'Event kind within category: transition | process_spawn | process_exit '
    '| snapshot | probe_pass | probe_fail';

COMMENT ON COLUMN telemetry_events.camera_id IS
    'Camera serial when scoped to one camera; NULL for system-wide events '
    '(resource_snapshot, docker_conntrack).';

COMMENT ON COLUMN telemetry_events.payload IS
    'Free-form JSONB carrying event-specific fields. Schemas documented in '
    'services/telemetry_event_log.py per-category constants. Not enforced.';

-- Two dominant query patterns: "recent events for camera X" and "recent
-- events for category Y." Partial index for camera_id skips the wide
-- system-wide-event rows that don't have a camera.
CREATE INDEX IF NOT EXISTS idx_telemetry_camera_ts
    ON telemetry_events (camera_id, ts DESC)
    WHERE camera_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_telemetry_category_ts
    ON telemetry_events (category, ts DESC);

CREATE INDEX IF NOT EXISTS idx_telemetry_ts
    ON telemetry_events (ts DESC);

-- ---------------------------------------------------------------------------
-- 2. nvr_settings — admin-controlled config keys
-- ---------------------------------------------------------------------------
-- The three keys are inserted with their defaults. If the row already exists
-- from a prior partial run, we leave the existing value alone (ON CONFLICT
-- DO NOTHING) — never clobber operator-set values on re-run.
--
-- Values are stored as text (nvr_settings is a key-value table) — the Python
-- layer parses them with the appropriate type.

INSERT INTO nvr_settings (key, value) VALUES
    ('telemetry_enabled',        'false'),
    ('telemetry_max_size_mb',    '100'),
    ('telemetry_retention_days', '7')
ON CONFLICT (key) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 3. Diagnostic SQL views (read-only convenience for psql)
-- ---------------------------------------------------------------------------
-- These are tiny and don't hurt if telemetry is off (they just return empty
-- result sets). They're the operator's fast-path during the next entropy
-- incident: "what was happening on Terrace Shed around 23:47 yesterday?"

CREATE OR REPLACE VIEW recent_camera_transitions AS
    SELECT id, ts, camera_id, subcategory, severity, payload
    FROM telemetry_events
    WHERE category IN ('camera_state', 'publisher')
      AND ts > now() - INTERVAL '24 hours'
    ORDER BY ts DESC;

COMMENT ON VIEW recent_camera_transitions IS
    'Last 24h of camera-state + publisher-state transitions. Empty if '
    'telemetry is disabled or no transitions occurred.';

CREATE OR REPLACE VIEW recent_rtsp_failures AS
    SELECT id, ts, camera_id, severity, payload
    FROM telemetry_events
    WHERE category = 'rtsp_probe'
      AND subcategory = 'probe_fail'
      AND ts > now() - INTERVAL '24 hours'
    ORDER BY ts DESC;

COMMENT ON VIEW recent_rtsp_failures IS
    'Last 24h of in-container RTSP probe failures. Each row is a probe that '
    'failed to connect to the hub URL for a camera — the entropy reproducer.';

CREATE OR REPLACE VIEW recent_resource_snapshots AS
    SELECT id, ts, payload
    FROM telemetry_events
    WHERE category = 'resource_snapshot'
      AND ts > now() - INTERVAL '24 hours'
    ORDER BY ts DESC;

COMMENT ON VIEW recent_resource_snapshots IS
    'Last 24h of periodic resource snapshots: ffmpeg subprocess count, '
    'mediamtx path count, gunicorn worker RSS, conntrack table size, etc.';
