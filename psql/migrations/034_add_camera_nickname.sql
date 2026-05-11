-- Migration 034: per-camera nickname for API-friendly addressing.
--
-- Adds cameras.nickname so external callers (and URL query params) can
-- address a camera by a short, human-readable handle instead of its
-- factory serial number.
--
-- Use case:
--   GET /streams?fullscreen=lobby
--   GET /light?fullscreen=lobby
--   GET /api/cameras/nicknames
--
-- The page server-side resolves nickname -> serial, JS triggers the
-- normal openFullscreen() pathway so localStorage.lastFullscreenCamera
-- is updated as it would be on a native click (restore-on-reload works
-- identically).
--
-- Constraints:
--   - Regex ^[a-z]+[0-9]?$  — letters then OPTIONAL single digit.
--     Examples that pass:  lobby, lobby0, lobby9, kitchen, hallway2
--     Examples that fail:  Lobby (uppercase), lobby10 (multi-digit),
--                          lobby-cam (hyphen), lobby_a (underscore/alpha-after-digit).
--   - NOT a brand name. The auto-naming code derives the base from the
--     camera display name; without this filter a brand-suffixed default
--     name like "Reolink 1" would yield "reolink1" which would shadow
--     legitimate addressing later. The set covers vendors we currently
--     support plus a handful of well-known industry brands so future
--     vendor additions don't conflict.
--   - UNIQUE — nickname must address exactly one camera.
--
-- Nullable: existing cameras get NULL nickname until the operator (or
-- the auto-suggest UI hint) sets one. NULL is filtered out by the list
-- endpoint and never matches the ?fullscreen= query — addressing by
-- serial number still works for cameras without a nickname.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'cameras' AND column_name = 'nickname'
    ) THEN
        ALTER TABLE cameras ADD COLUMN nickname TEXT;

        -- Uniqueness (NULLs are allowed by default in unique indexes).
        CREATE UNIQUE INDEX IF NOT EXISTS idx_cameras_nickname_unique
            ON cameras (nickname)
            WHERE nickname IS NOT NULL;

        -- Regex: lowercase letters, optional single trailing digit.
        ALTER TABLE cameras
            ADD CONSTRAINT cameras_nickname_format_chk
            CHECK (nickname IS NULL OR nickname ~ '^[a-z]+[0-9]?$');

        -- Brand-name blacklist. Matches the CAMERA_TYPES set in
        -- streaming_hub.py plus a few well-known industry brands so
        -- a future vendor add doesn't collide.
        ALTER TABLE cameras
            ADD CONSTRAINT cameras_nickname_not_brand_chk
            CHECK (
                nickname IS NULL OR nickname NOT IN (
                    'reolink', 'eufy', 'amcrest', 'sv3c', 'unifi',
                    'hikvision', 'dahua', 'axis', 'foscam', 'wyze',
                    'neolink', 'mediamtx', 'go2rtc', 'baichuan'
                )
            );

        COMMENT ON COLUMN cameras.nickname IS
            'Optional short handle for URL-based addressing. '
            'Regex ^[a-z]+[0-9]?$, must not be a brand name, must be '
            'unique. Used by /streams?fullscreen=<nickname> and '
            'GET /api/cameras/nicknames.';

        RAISE NOTICE 'cameras.nickname column added with regex + brand-name + unique constraints';
    ELSE
        RAISE NOTICE 'cameras.nickname already exists, skipping';
    END IF;
END $$;
