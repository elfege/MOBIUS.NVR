-- Migration 024: Grant INSERT on nvr_settings to nvr_anon
--
-- The unified Settings class (_upsert) does POST (INSERT) with
-- merge-duplicates first, falling back to PATCH on conflict.
-- Migration 022 only granted SELECT + UPDATE, causing 401 errors
-- when the Settings class attempted the initial INSERT via PostgREST.

GRANT INSERT ON nvr_settings TO nvr_anon;
