-- 028_add_audio_input_supported.sql
--
-- Track whether each camera's RTSP stream contains an audio track.
--
-- Determined empirically by running ffprobe against the camera's
-- streaming-hub-resolved RTSP URL (see scripts/survey_camera_audio.py).
-- Unlike the existing two_way_audio JSONB field — which describes the
-- camera's *talkback* capability for the user to send audio TO the
-- camera — this flag records whether the camera has a microphone the
-- pipeline can READ audio from.
--
-- Many budget cameras (e.g. some SV3C models) have a microphone but
-- do not actually publish the audio track over RTSP. Empirical probing
-- is the only reliable test.
--
-- Tri-state semantics:
--   NULL   = not yet probed
--   TRUE   = ffprobe found an audio stream
--   FALSE  = ffprobe completed cleanly with no audio stream
-- (Probe failures do not write to this column — they leave the prior
--  value intact, so transient network errors do not downgrade a known
--  TRUE to FALSE.)

BEGIN;

ALTER TABLE cameras
    ADD COLUMN IF NOT EXISTS audio_input_supported BOOLEAN,
    ADD COLUMN IF NOT EXISTS audio_input_probed_at TIMESTAMPTZ;

COMMENT ON COLUMN cameras.audio_input_supported IS
    'Whether the camera publishes an audio stream over RTSP. '
    'NULL = not yet probed; populated by scripts/survey_camera_audio.py.';
COMMENT ON COLUMN cameras.audio_input_probed_at IS
    'Timestamp of the most recent successful ffprobe run, regardless of result. '
    'NULL means the camera has never been probed cleanly.';

COMMIT;
