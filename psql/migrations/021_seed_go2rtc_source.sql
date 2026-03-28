-- Migration 021: Seed go2rtc_source values for cameras (ONE-TIME ONLY)
--
-- This migration was originally an unconditional UPDATE that overwrote
-- user-configured values on every restart. Now it only seeds cameras
-- that have NULL go2rtc_source (first boot / new cameras).
--
-- go2rtc_source is the runtime source of truth in the DB.
-- Changes are made via the UI (Advanced tab) and persist across restarts.

-- Reolink cameras (direct RTSP)
UPDATE cameras SET go2rtc_source = 'rtsp://${go2rtc_username}:${go2rtc_password}@192.168.10.187/h264Preview_01_main'
  WHERE serial = '95270000D1B5FBEW' AND go2rtc_source IS NULL;
UPDATE cameras SET go2rtc_source = 'rtsp://neolink:8554/95270000YPTKLLD6/mainStream'
  WHERE serial = '95270000YPTKLLD6' AND go2rtc_source IS NULL;
UPDATE cameras SET go2rtc_source = 'rtsp://${go2rtc_username}:${go2rtc_password}@192.168.10.89/h264Preview_01_main'
  WHERE serial = '95270001CSHLPO74' AND go2rtc_source IS NULL;
UPDATE cameras SET go2rtc_source = 'rtsp://${go2rtc_username}:${go2rtc_password}@192.168.10.88/h264Preview_01_main'
  WHERE serial = '95270001CSO4BPDZ' AND go2rtc_source IS NULL;
UPDATE cameras SET go2rtc_source = 'rtsp://${go2rtc_username}:${go2rtc_password}@192.168.10.118/h264Preview_01_main'
  WHERE serial = '95270001NT3KNA67' AND go2rtc_source IS NULL;
UPDATE cameras SET go2rtc_source = 'rtsp://${go2rtc_username}:${go2rtc_password}@192.168.10.121/h264Preview_01_main'
  WHERE serial = '95270001Q3D82PF7' AND go2rtc_source IS NULL;
UPDATE cameras SET go2rtc_source = 'rtsp://${go2rtc_username}:${go2rtc_password}@192.168.10.186/h264Preview_01_main'
  WHERE serial = 'XCPTP369388MNVTG' AND go2rtc_source IS NULL;

-- Amcrest
UPDATE cameras SET go2rtc_source = 'rtsp://${go2rtc_username}:${go2rtc_password}@192.168.10.34/cam/realmonitor?channel=1&subtype=0'
  WHERE serial = 'AMC043145A67EFBF79' AND go2rtc_source IS NULL;

-- SV3C
UPDATE cameras SET go2rtc_source = 'rtsp://${go2rtc_username}:${go2rtc_password}@192.168.10.90:554/stream1'
  WHERE serial = 'C6F0SgZ0N0PoL2' AND go2rtc_source IS NULL;

-- Eufy cameras (direct local RTSP, NOT eufy:// cloud P2P)
UPDATE cameras SET go2rtc_source = 'rtsp://${go2rtc_username}:${go2rtc_password}@192.168.10.185/live0'
  WHERE serial = 'T821451024233587' AND go2rtc_source IS NULL;
UPDATE cameras SET go2rtc_source = 'rtsp://${go2rtc_username}:${go2rtc_password}@192.168.10.84/live0'
  WHERE serial = 'T8416P0023352DA9' AND go2rtc_source IS NULL;
UPDATE cameras SET go2rtc_source = 'rtsp://${go2rtc_username}:${go2rtc_password}@192.168.10.87/live0'
  WHERE serial = 'T8416P0023370398' AND go2rtc_source IS NULL;
UPDATE cameras SET go2rtc_source = 'rtsp://${go2rtc_username}:${go2rtc_password}@192.168.10.182/live0'
  WHERE serial = 'T8416P00233717CB' AND go2rtc_source IS NULL;
UPDATE cameras SET go2rtc_source = 'rtsp://${go2rtc_username}:${go2rtc_password}@192.168.10.117/live0'
  WHERE serial = 'T8416P0023390DE9' AND go2rtc_source IS NULL;
UPDATE cameras SET go2rtc_source = 'rtsp://${go2rtc_username}:${go2rtc_password}@192.168.10.181/live0'
  WHERE serial = 'T8416P6024350412' AND go2rtc_source IS NULL;
UPDATE cameras SET go2rtc_source = 'rtsp://${go2rtc_username}:${go2rtc_password}@192.168.10.119/live0'
  WHERE serial = 'T8419P0024110C6A' AND go2rtc_source IS NULL;
UPDATE cameras SET go2rtc_source = 'rtsp://${go2rtc_username}:${go2rtc_password}@192.168.10.184/live0'
  WHERE serial = 'T8441P12242302AC' AND go2rtc_source IS NULL;
UPDATE cameras SET go2rtc_source = 'rtsp://${go2rtc_username}:${go2rtc_password}@192.168.10.183/live0'
  WHERE serial = 'T8441P122428038A' AND go2rtc_source IS NULL;
