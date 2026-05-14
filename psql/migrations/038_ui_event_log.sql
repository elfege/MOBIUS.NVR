-- Migration 038: UI interaction audit log.
--
-- Operator decision 2026-05-13: complete traceability of every click and
-- keystroke for litigation-grade accountability and hacker forensics.
-- PII risk is explicitly accepted by the operator; passwords are masked
-- client-side before they ever reach this table.
--
-- This is a SECOND audit stream, separate from setting_audit_log
-- (migration 036). Rationale:
--   - setting_audit_log captures *outcomes* (what value changed in
--     which table) and is triggered by Postgres DML triggers + browser
--     audit-outbox POSTs.
--   - ui_event_log captures *interactions* (what the user clicked,
--     focused, typed, submitted) at the DOM level. It is fed
--     exclusively from the browser by a single delegated event
--     listener (services/ui-event-tracker.js → services/ui-event-outbox.js
--     → POST /api/ui-event/batch).
-- Splitting tables avoids polluting the settings audit with UI noise
-- and makes the "wipe keystrokes only" operation a simple WHERE clause
-- on this table instead of risky cross-table surgery.
--
-- FK policy: same as 036 — user_id and client_id are INFORMATIONAL with
-- NO foreign-key constraints. An audit row must never fail to land
-- because the referenced user/device was deleted.

BEGIN;

-- =============================================================================
-- 1. Table
-- =============================================================================

CREATE TABLE IF NOT EXISTS ui_event_log (
    id           BIGSERIAL PRIMARY KEY,
    ts           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_id      INTEGER,                    -- intentionally NOT a FK
    client_id    UUID,                       -- intentionally NOT a FK
    host_label   TEXT,                       -- kiosk machine, when bound (localStorage.mobius_host_label)
    kind         TEXT NOT NULL CHECK (kind IN (
                     'click','keystroke','focus','blur','submit',
                     'navigation','modal_open','modal_close','scroll'
                 )),
    target_id    TEXT,                       -- DOM id when set
    target_tag   TEXT,                       -- e.g. 'BUTTON', 'INPUT'
    target_text  TEXT,                       -- innerText/aria-label, truncated to 200 chars client-side
    target_attrs JSONB,                      -- {class, data-action, name, type, role, ...}
    page_url     TEXT,                       -- window.location.pathname + ?query
    extra        JSONB                       -- per-kind details (key, modifiers, selector path, etc.)
);

-- =============================================================================
-- 2. Indexes
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_ui_event_log_ts
    ON ui_event_log (ts DESC);
CREATE INDEX IF NOT EXISTS idx_ui_event_log_kind_ts
    ON ui_event_log (kind, ts DESC);
CREATE INDEX IF NOT EXISTS idx_ui_event_log_user_ts
    ON ui_event_log (user_id, ts DESC) WHERE user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ui_event_log_client_ts
    ON ui_event_log (client_id, ts DESC) WHERE client_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ui_event_log_target_id
    ON ui_event_log (target_id) WHERE target_id IS NOT NULL;

-- =============================================================================
-- 3. Grants
-- =============================================================================

GRANT SELECT, INSERT, DELETE ON ui_event_log TO nvr_api;
GRANT USAGE, SELECT ON ui_event_log_id_seq TO nvr_api;

COMMENT ON TABLE ui_event_log IS
    'Append-only log of UI interactions (clicks, keystrokes, focus, '
    'navigations) captured by the browser ui-event-tracker. Password '
    'fields are masked to "*" client-side before insertion. Retention: '
    '90 days via background prune. The /api/ui-event/keystrokes DELETE '
    'endpoint wipes kind IN (keystroke,focus,blur) while preserving '
    'the click trail.';

COMMIT;
