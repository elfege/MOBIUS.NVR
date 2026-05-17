-- Migration 039: per-camera throttle priority + never-throttle flag.
--
-- Operator-flagged 2026-05-14: AMCREST LOBBY is safety-critical and must
-- never be UI-throttled. More generally, the client-side ThrottleController
-- picks demotion candidates by stream-type only, which means a critical
-- camera and a redundant one are treated identically under load.
--
-- This migration introduces:
--
--   throttle_priority INT NULL DEFAULT NULL
--     1 = first to demote when load climbs, 2 = next, ... NULL = "tie-break
--     by stream-type only" (current behaviour). The ThrottleController
--     sorts candidates by priority ascending (lower number = earlier to
--     demote); ties are broken by the existing DEMOTION_PRIORITY stream-
--     type order.
--
--   throttle_never BOOLEAN NOT NULL DEFAULT FALSE
--     When TRUE, the ThrottleController removes this camera from the
--     candidate set entirely. The tile stays at its current quality even
--     when load is over threshold. Use for safety-critical cameras whose
--     output you can't lose visibility on (lobby, entry, etc.).
--
-- Together they replace the previous "all cameras are equally demotable"
-- semantics with operator-controllable priority. Defaults preserve the
-- current behaviour for every existing camera (no-op until the operator
-- flips a value in the camera settings form).

ALTER TABLE cameras
    ADD COLUMN IF NOT EXISTS throttle_priority INTEGER NULL,
    ADD COLUMN IF NOT EXISTS throttle_never    BOOLEAN NOT NULL DEFAULT FALSE;

-- The audit trigger on `cameras` (migration 036) already captures
-- changes to any column on this table, including these two. No
-- additional trigger work needed.

COMMENT ON COLUMN cameras.throttle_priority IS
    'Lower number = earlier to demote under CPU load. NULL = stream-type tiebreak only.';
COMMENT ON COLUMN cameras.throttle_never IS
    'TRUE = exempt from UI throttling entirely. Use for safety-critical cameras.';
