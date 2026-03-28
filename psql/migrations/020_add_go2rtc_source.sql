-- Migration 020: Add go2rtc_source column to cameras table
--
-- go2rtc_source stores the URL template that go2rtc uses to connect to the camera.
-- Values use ${ENV_VAR} syntax for credential substitution at go2rtc startup.
--
-- Examples by camera type:
--   Neolink (Baichuan):  rtsp://neolink:8554/{serial}/mainStream
--   Reolink (RTSP):      rtsp://${NVR_REOLINK_API_USER}:${NVR_REOLINK_API_PASSWORD}@{host}/h264Preview_01_main
--   Eufy:                eufy://${NVR_EUFY_BRIDGE_USERNAME}:${NVR_EUFY_BRIDGE_PASSWORD}@{serial}
--   Amcrest:             rtsp://${NVR_AMCREST_LOBBY_USERNAME}:${NVR_AMCREST_LOBBY_PASSWORD}@{host}/cam/realmonitor?channel=1&subtype=0
--   SV3C:                rtsp://admin:${NVR_SV3C_PASSWORD}@{host}:554/stream1
--   UniFi:               null (token-based, handled separately)
--
-- This field is read by scripts/update_go2rtc_config.sh on startup to generate
-- go2rtc.yaml entries for ALL cameras (not just Neolink).
-- It is synced from cameras.json via camera_config_sync.py (DIRECT_FIELDS).

ALTER TABLE cameras ADD COLUMN IF NOT EXISTS go2rtc_source TEXT DEFAULT NULL;

COMMENT ON COLUMN cameras.go2rtc_source IS
  'go2rtc source URL template (with ${ENV_VAR} credentials). Used by update_go2rtc_config.sh to generate go2rtc.yaml entries. Null = not yet configured for go2rtc.';
