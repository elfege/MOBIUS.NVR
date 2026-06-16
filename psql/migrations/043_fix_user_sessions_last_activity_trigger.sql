-- =============================================================================
-- 043_fix_user_sessions_last_activity_trigger.sql
-- =============================================================================
-- Fix a long-standing bug in the `user_sessions` UPDATE trigger.
--
-- The trigger was named `update_sessions_last_activity` (suggesting it
-- should bump the `last_activity` column on every UPDATE) but it was
-- wired to EXECUTE `update_updated_at_column()` — a function that sets
-- `NEW.updated_at = NOW()`. The `user_sessions` table has no
-- `updated_at` column (it uses `last_activity`), so every UPDATE failed
-- with:
--     ERROR 42703: record "new" has no field "updated_at"
--
-- Visible symptom: `_deactivate_user_session()` in routes/helpers.py
-- calls PostgREST to PATCH user_sessions setting is_active=false on
-- logout. Because of this trigger the PATCH failed silently in the
-- background — Flask-Login still cleared the cookie, but the DB row
-- stayed is_active=true forever. Cumulative effect: every user_sessions
-- row across the system's lifetime is stuck active. The next anomaly
-- audit that depends on is_active flips will see fictional users.
--
-- This was caught while writing the Phase D AUTH.LOGOUT e2e test
-- (2026-06-16). The test now fails-then-passes on this migration alone.
--
-- Fix:
--   1. Create a dedicated `update_last_activity_column()` function that
--      sets NEW.last_activity (the actual column name).
--   2. Drop the broken trigger and recreate it pointing at the new
--      function.
--
-- The existing `update_updated_at_column()` function stays — it's still
-- used by other tables that DO have `updated_at` columns.
-- =============================================================================

CREATE OR REPLACE FUNCTION update_last_activity_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.last_activity = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_sessions_last_activity ON user_sessions;

CREATE TRIGGER update_sessions_last_activity
    BEFORE UPDATE ON user_sessions
    FOR EACH ROW
    EXECUTE FUNCTION update_last_activity_column();
