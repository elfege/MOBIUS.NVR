-- Migration 041: per-camera tracking owner.
--
-- Operator-requested 2026-05-23. Introduces a single nullable text column
-- describing WHO is responsible for object/person tracking on a camera:
--
--   tracking_owner TEXT NULL  (effective default 'native')
--     'native' — the camera (or, for Eufy, the Eufy bridge) does its own
--                on-device auto-tracking. For Eufy cameras the application
--                drives the native `motionTracking` device property
--                (eufy-security-client PropertyName.DeviceMotionTracking,
--                P2P command CMD_INDOOR_PAN_MOTION_TRACK) when this is set.
--     'nvr'    — the NVR's own detection/tracking pipeline owns tracking.
--                This pipeline is NOT built yet (see
--                docs/plans/object_detection_and_person_tracking_pipeline_*),
--                so 'nvr' is currently a stored no-op preference.
--     'off'    — no tracking from any owner.
--
-- NULL is treated by the application as 'native' (the prior implicit
-- behaviour: cameras that auto-track did so on their own). Existing rows are
-- therefore left NULL — no behavioural change until the operator picks a
-- value in the camera settings form.
--
-- The audit trigger on `cameras` (migration 036) already captures changes to
-- any column on this table, so no additional trigger work is needed.

ALTER TABLE cameras
    ADD COLUMN IF NOT EXISTS tracking_owner TEXT NULL;

COMMENT ON COLUMN cameras.tracking_owner IS
    'Who owns tracking: native (camera/Eufy on-device), nvr (future NVR pipeline, no-op for now), off. NULL = native.';
