-- Migration 021: Seed go2rtc_source values for all existing cameras
--
-- go2rtc_source was added in migration 020 with no default values.
-- This migration populates known values for all cameras.
-- STAIRS (UniFi) is left NULL — token-based auth, handled separately.
--
-- ${ENV_VAR} placeholders are resolved by go2rtc at runtime from container env.
-- This migration stores the template strings literally; go2rtc substitutes them.

UPDATE cameras SET go2rtc_source = 'rtsp://${NVR_REOLINK_API_USER}:${NVR_REOLINK_API_PASSWORD}@192.168.10.187/h264Preview_01_main'
  WHERE serial = '95270000D1B5FBEW'; -- Former CAM STAIRS

UPDATE cameras SET go2rtc_source = 'rtsp://neolink:8554/95270000YPTKLLD6/mainStream'
  WHERE serial = '95270000YPTKLLD6'; -- REOLINK Cat Feeders

UPDATE cameras SET go2rtc_source = 'rtsp://${NVR_REOLINK_API_USER}:${NVR_REOLINK_API_PASSWORD}@192.168.10.89/h264Preview_01_main'
  WHERE serial = '95270001CSHLPO74'; -- Terrace South

UPDATE cameras SET go2rtc_source = 'rtsp://${NVR_REOLINK_API_USER}:${NVR_REOLINK_API_PASSWORD}@192.168.10.88/h264Preview_01_main'
  WHERE serial = '95270001CSO4BPDZ'; -- REOLINK OFFICE

UPDATE cameras SET go2rtc_source = 'rtsp://${NVR_REOLINK_API_USER}:${NVR_REOLINK_API_PASSWORD}@192.168.10.118/h264Preview_01_main'
  WHERE serial = '95270001NT3KNA67'; -- LAUNDRY ROOM

UPDATE cameras SET go2rtc_source = 'rtsp://${NVR_REOLINK_API_USER}:${NVR_REOLINK_API_PASSWORD}@192.168.10.121/h264Preview_01_main'
  WHERE serial = '95270001Q3D82PF7'; -- MEBO

UPDATE cameras SET go2rtc_source = 'rtsp://${NVR_AMCREST_LOBBY_USERNAME}:${NVR_AMCREST_LOBBY_PASSWORD}@192.168.10.34/cam/realmonitor?channel=1&subtype=0'
  WHERE serial = 'AMC043145A67EFBF79'; -- AMCREST LOBBY

UPDATE cameras SET go2rtc_source = 'rtsp://admin:${NVR_SV3C_PASSWORD}@192.168.10.90:554/stream1'
  WHERE serial = 'C6F0SgZ0N0PoL2'; -- SV3C_Living_3

UPDATE cameras SET go2rtc_source = 'eufy://${NVR_EUFY_BRIDGE_USERNAME}:${NVR_EUFY_BRIDGE_PASSWORD}@T821451024233587'
  WHERE serial = 'T821451024233587'; -- Entrance door

UPDATE cameras SET go2rtc_source = 'eufy://${NVR_EUFY_BRIDGE_USERNAME}:${NVR_EUFY_BRIDGE_PASSWORD}@T8416P0023352DA9'
  WHERE serial = 'T8416P0023352DA9'; -- Living Room

UPDATE cameras SET go2rtc_source = 'eufy://${NVR_EUFY_BRIDGE_USERNAME}:${NVR_EUFY_BRIDGE_PASSWORD}@T8416P0023370398'
  WHERE serial = 'T8416P0023370398'; -- Office Desk

UPDATE cameras SET go2rtc_source = 'eufy://${NVR_EUFY_BRIDGE_USERNAME}:${NVR_EUFY_BRIDGE_PASSWORD}@T8416P00233717CB'
  WHERE serial = 'T8416P00233717CB'; -- Entryway

UPDATE cameras SET go2rtc_source = 'eufy://${NVR_EUFY_BRIDGE_USERNAME}:${NVR_EUFY_BRIDGE_PASSWORD}@T8416P0023390DE9'
  WHERE serial = 'T8416P0023390DE9'; -- Kitchen

UPDATE cameras SET go2rtc_source = 'eufy://${NVR_EUFY_BRIDGE_USERNAME}:${NVR_EUFY_BRIDGE_PASSWORD}@T8416P6024350412'
  WHERE serial = 'T8416P6024350412'; -- HALLWAY

UPDATE cameras SET go2rtc_source = 'eufy://${NVR_EUFY_BRIDGE_USERNAME}:${NVR_EUFY_BRIDGE_PASSWORD}@T8419P0024110C6A'
  WHERE serial = 'T8419P0024110C6A'; -- KITCHEN OFFICE

UPDATE cameras SET go2rtc_source = 'eufy://${NVR_EUFY_BRIDGE_USERNAME}:${NVR_EUFY_BRIDGE_PASSWORD}@T8441P12242302AC'
  WHERE serial = 'T8441P12242302AC'; -- Terrace Shed

UPDATE cameras SET go2rtc_source = 'eufy://${NVR_EUFY_BRIDGE_USERNAME}:${NVR_EUFY_BRIDGE_PASSWORD}@T8441P122428038A'
  WHERE serial = 'T8441P122428038A'; -- Hot Tub

UPDATE cameras SET go2rtc_source = 'rtsp://${NVR_REOLINK_API_USER}:${NVR_REOLINK_API_PASSWORD}@192.168.10.186/h264Preview_01_main'
  WHERE serial = 'XCPTP369388MNVTG'; -- Living_REOLINK
