-- =============================================================================
-- FIX RLS POLICY ON user_camera_preferences
-- =============================================================================
-- The existing RLS policy requires current_setting('app.user_id') to match
-- user_id, but the Flask app never sets this GUC variable when calling
-- PostgREST. This blocks all INSERT/UPDATE/DELETE operations.
--
-- Fix: Replace the restrictive policy with a permissive "allow all" policy
-- for the nvr_anon role. User isolation is enforced at the Flask application
-- layer (current_user.id filtering), not the database layer.
--
-- Migration: 018_fix_user_camera_preferences_rls.sql
-- Created: March 27, 2026

DO $$
BEGIN
    -- Drop the existing restrictive policy
    DROP POLICY IF EXISTS "Users see own preferences" ON user_camera_preferences;

    -- Create permissive policy that allows all operations for nvr_anon
    -- User isolation is handled by Flask (current_user.id in WHERE clauses)
    CREATE POLICY "Allow all for user_camera_preferences"
    ON user_camera_preferences
    FOR ALL
    TO nvr_anon
    USING (true)
    WITH CHECK (true);

    RAISE NOTICE 'Fixed RLS policy on user_camera_preferences — allow all for nvr_anon';
END $$;
