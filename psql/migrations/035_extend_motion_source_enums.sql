-- Migration 035: extend motion_source / source enums for honest attribution.
--
-- Background (operator-confirmed 2026-05-13):
--   Probe of the production DB found 0 rows in motion_events over 24h and
--   100% of recordings.motion_source = 'manual' across 12 cameras / 3,419
--   rows. The schema already had the right columns
--   (recordings.motion_source, recordings.motion_event_id,
--   motion_events.source + onvif_rule_name/event_type/recording_id) but
--   the writers never used them — every recording call defaulted to
--   start_motion_recording(camera_id) without an event_id, so the writer
--   wrote 'manual' for everything. Phase 0 of this branch fixes the
--   writers; this migration extends the CHECK constraints so the new
--   correct values are allowed.
--
-- New values:
--   'reolink_baichuan' — Reolink native Baichuan motion push (distinct
--                        from generic ONVIF; different code path, different
--                        reliability profile)
--   'evidence'         — recordings triggered by the audio-analytics /
--                        evidence pipeline (YAMNet etc.). Currently no
--                        enum slot existed for this path; future
--                        evidence-pipeline writes will use it.
--
-- 'eufy_bridge' was already in the CHECK list but no code path writes it
-- (no Eufy bridge motion handler exists today per Phase 0 exploration).
-- Leave the slot reserved.
--
-- We DROP + ADD the constraint rather than ALTER ... ADD VALUE (which
-- only works on ENUM types — these are TEXT/VARCHAR CHECK constraints).

BEGIN;

ALTER TABLE recordings DROP CONSTRAINT IF EXISTS recordings_motion_source_check;
ALTER TABLE recordings
    ADD CONSTRAINT recordings_motion_source_check
    CHECK (
        motion_source IS NULL
        OR motion_source IN (
            'onvif',
            'ffmpeg',
            'eufy_bridge',
            'manual',
            'reolink_baichuan',
            'evidence'
        )
    );

ALTER TABLE motion_events DROP CONSTRAINT IF EXISTS motion_events_source_check;
ALTER TABLE motion_events
    ADD CONSTRAINT motion_events_source_check
    CHECK (
        source IN (
            'onvif',
            'ffmpeg',
            'eufy_bridge',
            'manual',
            'reolink_baichuan',
            'evidence'
        )
    );

COMMENT ON COLUMN recordings.motion_source IS
    'Which subsystem triggered this recording. NULL only for legacy rows '
    'predating Phase 0 attribution (2026-05-13). Possible values: '
    'onvif | ffmpeg | reolink_baichuan | evidence | eufy_bridge | manual.';

COMMENT ON COLUMN motion_events.source IS
    'Which detector produced this motion event. Same enum as '
    'recordings.motion_source minus the NULL case (this column is NOT NULL).';

COMMIT;
