-- Migration 040: ONVIF subscription health observability columns.
--
-- Motivated by AMCREST LOBBY (AMC043145A67EFBF79): the ONVIF event
-- listener has been silently logging "Subscribe Creation Failed"
-- forever, then retrying every 5s, with NO surfaceable signal to the
-- operator that anything is wrong. Motion-event subscriptions die
-- without recovering and there's no audit of the failure rate.
--
-- This migration adds OBSERVABILITY columns ONLY — no auto-disable
-- behaviour yet. The listener increments failure_count + stamps the
-- last error each time CreatePullPointSubscription throws, and zeros
-- both on a successful subscribe. The operator queries the columns
-- (directly or via GET /api/onvif/health/<serial>) to decide whether
-- to flip a camera to FFmpeg motion detection manually.
--
-- Auto-disable + operator revert UI are explicitly deferred to a
-- follow-up branch so the operator can shape the policy with real
-- failure-rate data in hand instead of guessing at thresholds.
--
-- Plumbing (the "4 places" CLAUDE.md flags) for each column:
--   1. This migration.
--   2. services/camera_config_sync.py DIRECT_FIELDS — cameras.json seed.
--   3. services/camera_repository.py direct_fields — DB -> cache.
--   4. routes/camera.py EDITABLE_KEYS (the revert/manual-override path
--      will go through this when added — for now the listener writes
--      directly via psycopg2, bypassing the PUT endpoint).

ALTER TABLE cameras
    ADD COLUMN IF NOT EXISTS onvif_subscription_state    TEXT NULL,
    ADD COLUMN IF NOT EXISTS onvif_failure_count         INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS onvif_last_failure_ts       TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS onvif_last_error_message    TEXT NULL,
    ADD COLUMN IF NOT EXISTS onvif_last_success_ts       TIMESTAMPTZ NULL;

-- onvif_subscription_state values are an open set so the follow-up
-- branch can introduce 'auto_disabled' / 'user_overridden' without
-- another migration. The CHECK constraint is intentionally permissive.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.constraint_column_usage
        WHERE constraint_name = 'cameras_onvif_state_check'
    ) THEN
        ALTER TABLE cameras
            ADD CONSTRAINT cameras_onvif_state_check
            CHECK (onvif_subscription_state IS NULL
                OR onvif_subscription_state IN
                   ('healthy','failing','auto_disabled','user_overridden'));
    END IF;
END $$;

COMMENT ON COLUMN cameras.onvif_subscription_state IS
    'healthy / failing / auto_disabled / user_overridden, or NULL if not yet observed.';
COMMENT ON COLUMN cameras.onvif_failure_count IS
    'Consecutive Subscribe Creation Failed (or equivalent) events since the last successful subscribe. Zeroed on each healthy subscribe.';
COMMENT ON COLUMN cameras.onvif_last_failure_ts IS
    'Timestamp of the most recent listener-level failure.';
COMMENT ON COLUMN cameras.onvif_last_error_message IS
    'Truncated last error string from the listener. For operator diagnosis.';
COMMENT ON COLUMN cameras.onvif_last_success_ts IS
    'Timestamp of the most recent successful CreatePullPointSubscription.';
