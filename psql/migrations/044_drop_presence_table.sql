-- Migration 044: Drop presence table (feature removed 2026-06-19).
--
-- The presence feature (household member tracking with Hubitat integration)
-- was removed in commit 8b20682a — its routes, service, controller, and
-- template wiring were stripped. This migration removes the last remaining
-- footprint: the `presence` table itself, its index, its sequence, its RLS
-- policy, and the GRANTs to nvr_anon (all implied by `DROP TABLE … CASCADE`).
--
-- Companion changes in the same commit that brings this migration:
--   * psql/init-db.sql: presence CREATE TABLE / INDEX / INSERT / GRANT /
--     ALTER … ENABLE RLS / CREATE POLICY blocks removed
--   * psql/migrations/003_add_presence_table.sql: deleted (no longer needed
--     and would otherwise re-create the table on every start.sh boot,
--     defeating this drop)
--
-- IDEMPOTENT — start.sh runs every *.sql migration in numeric order on every
-- boot (services/migration runner has no "applied" tracking). `IF EXISTS`
-- + the DO block make this safe to re-run on a fresh DB (where presence was
-- never created) and on an existing DB (where presence may still hold rows).
--
-- DESTRUCTIVE — `DROP TABLE presence CASCADE` removes whatever rows still
-- live in the table (the seed `Elfege` + `Jessica` entries plus any
-- subsequent toggles). This is intended: the feature is gone, so its data
-- has no consumer. Operator was warned + authorized 2026-06-19.

DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'presence') THEN
        DROP TABLE presence CASCADE;
        RAISE NOTICE 'Dropped presence table and dependents';
    ELSE
        RAISE NOTICE 'presence table already absent — nothing to drop';
    END IF;
END
$$;

SELECT 'Migration 044 complete - presence table removed' AS status;
