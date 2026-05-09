-- 029_grant_evidence_tables_to_anon.sql
--
-- Migration 027 granted the new evidence_* tables only to the
-- authenticated nvr_api role. PostgREST runs anonymous requests as
-- nvr_anon (PGRST_DB_ANON_ROLE), so the tables were unreachable from
-- the PostgREST endpoint at http://postgrest:3001 without a JWT.
--
-- This follow-up migration grants nvr_anon the same CRUD permissions
-- it already has on cameras and other operational tables.
--
-- SECURITY NOTE: nvr_anon access here is appropriate for the SETTINGS
-- tables (evidence_camera_settings, evidence_cases — UI-managed). For
-- audio_events (per-event capture records) and any future endpoint that
-- exposes promoted manifest entries, Phase 5 will introduce a Flask
-- /api/evidence/ surface with proper authentication and these PostgREST
-- grants on audio_events should be tightened or revoked at that time.

BEGIN;

GRANT SELECT, INSERT, UPDATE, DELETE ON evidence_camera_settings TO nvr_anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON evidence_cases           TO nvr_anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON audio_events             TO nvr_anon;

GRANT USAGE ON SEQUENCE evidence_cases_id_seq TO nvr_anon;
GRANT USAGE ON SEQUENCE audio_events_id_seq   TO nvr_anon;

COMMIT;

-- Refresh PostgREST schema cache so the new permissions are visible.
NOTIFY pgrst, 'reload schema';
