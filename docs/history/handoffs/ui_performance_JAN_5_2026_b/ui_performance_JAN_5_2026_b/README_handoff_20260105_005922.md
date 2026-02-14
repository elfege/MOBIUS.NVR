---
title: "Session Handoff Buffer"
layout: default
---

<!-- markdownlint-disable MD025 -->
<!-- markdownlint-disable MD024 -->
<!-- markdownlint-disable MD036 -->
<!-- markdownlint-disable MD060 -->

# Session Handoff Buffer

This file is updated after each file modification during a Claude Code session.
It serves as a buffer before content is transferred to `README_project_history.md`.

---

*Last updated: January 5, 2026 01:00 EST*

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Previous Session Summary

**Branch merged to main:** `docs_update_JAN_4_2026_a`

---

## Current Session

**Branch:** `ui_performance_JAN_5_2026_b`
**Date:** January 4-5, 2026 (23:30 - 01:00 EST)

**Context compaction occurred at ~00:35 EST** - Continued from previous session

### What Was Accomplished This Session

1. **Reduced FFmpeg analyzeduration/probesize** for two cameras:
   - Office Desk (T8416P0023370398): 1000000 → 500000
   - REOLINK OFFICE (95270001CSO4BPDZ): 1000000 → 500000

2. **WebRTC Latency Testing Results:**
   - Observed: ~6s initially, ~2s after warmup
   - Expected: ~200ms (not achievable with FFmpeg transcoding)
   - Root cause: FFmpeg transcoding adds ~2s latency floor

3. **IMPLEMENTED: Protect Snapshot API for UniFi MJPEG**
   - Replaced `USE_PROTECT=true` bypass that returned None
   - New implementation uses Protect API: `https://{protect_host}/proxy/protect/api/cameras/{camera_id}/snapshot`
   - Added session management with auto re-authentication on 401
   - Camera 68d49398005cf203e400043f (OFFICE KITCHEN) now works with `stream_type: "MJPEG"`
   - Verified: 40+ frames captured, ~34KB per frame, no errors
   - **CONFIRMED WORKING** after container restart - user confirmed stream visible (with expected MJPEG latency ~10s)

4. **Fixed MJPEG restart capability:**
   - Added `_camera_services` dict to store camera_service references
   - `restart_capture()` can now find camera_service for watchdog restarts

5. **Switched OFFICE KITCHEN from MJPEG to WEBRTC** (Jan 5, 00:00 EST):
   - Changed `stream_type` in cameras.json from `MJPEG` to `WEBRTC`
   - User ran `./start.sh` to apply changes
   - WebRTC now streaming with ~2s latency (vs ~10s with MJPEG snapshots)

6. **Fixed database schema - added missing `updated_at` column** (Jan 5, 00:45 EST):
   - Error: `Column 'updated_at' of relation 'recordings' does not exist`
   - Root cause: Database was created before `updated_at` was added to `psql/init-db.sql`
   - Created migration file: `psql/migrations/002_add_updated_at.sql`
   - Ran migration against nvr-postgres container - column now exists

7. **Fixed Neolink config error** (Jan 5, 00:48 EST):
   - Error: `missing field 'cameras'` in `/etc/neolink.toml`
   - Cause: No cameras configured for NEOLINK stream_type
   - Added placeholder camera entry to satisfy config parser
   - Container now runs without crashing (placeholder fails to connect as expected)
   - Note: config/neolink.toml is gitignored - change is local only

### Files Modified

| File | Change |
|------|--------|
| [services/unifi_protect_service.py](services/unifi_protect_service.py) | Implemented Protect snapshot API with session auth |
| [services/unifi_mjpeg_capture_service.py](services/unifi_mjpeg_capture_service.py) | Store camera_service for restart capability |
| config/cameras.json | Changed OFFICE KITCHEN stream_type: MJPEG → WEBRTC (gitignored) |
| [psql/migrations/002_add_updated_at.sql](psql/migrations/002_add_updated_at.sql) | New migration for missing updated_at column |
| config/neolink.toml | Added placeholder camera entry (gitignored) |

### Commits This Session

1. `6a0d349` - Implement Protect snapshot API for UniFi MJPEG streaming
2. `b54a198` - Fix UniFi MJPEG restart - store camera_service reference
3. `b9b2da5` - Add migration for missing updated_at column in recordings table

---

## Key Technical Findings

### MediaMTX Path Cleanup

**Question:** Does `update_mediamtx_paths.sh` clean up unused paths?

**Answer:** YES. The script:
1. Reads cameras.json for LL_HLS, NEOLINK, and WEBRTC cameras only
2. Replaces EVERYTHING after `paths:` in mediamtx.yml with only those cameras
3. MJPEG cameras are automatically removed on next run

### UniFi WebRTC Configuration

**Key finding:** The `rtsp_alias` in cameras.json is **IGNORED** for UniFi cameras!

The code in [unifi_protect_service.py:38](services/unifi_protect_service.py#L38) reads from environment variable:
```python
self.rtsp_alias = os.getenv('CAMERA_68d49398005cf203e400043f_TOKEN_ALIAS', "None")
```

Container already has the correct env var via `.bash_utils`:
```
CAMERA_68d49398005cf203e400043f_TOKEN_ALIAS=zmUKsRyrMpDGSThn
```

RTSP URL format: `rtsp://192.168.10.3:7447/{rtsp_alias}` (plain RTSP, port 7447)

### WebRTC Session Stability Issue (Resolved)

Initial symptom: WebRTC showed still image refreshing every ~20s

**Root cause:** Watchdog restart cycle:
1. FFmpeg publishes → MediaMTX reports `ready: true`
2. Brief gap in MediaMTX polling → `ready: false` detected
3. Watchdog triggers FFmpeg restart → kills existing WebRTC session
4. Repeat

**Resolution:** Stream stabilized after warmup period (~30s). Watchdog cooldown mechanism eventually stopped the cycling.

### UniFi WebRTC 90s Latency Investigation (RESOLVED)

**Symptom:** User reports ~90s latency despite WebRTC badge showing ~250ms

**Root cause:** Stale browser state from before FFmpeg restart. Browser was showing buffered video.

**Resolution:** After browser reconnected, WebRTC session established with ~1s latency. Confirmed stable with light toggle test - zero packet loss.

### Neolink for Baichuan/Port 9000 Cameras

**Question:** Does Neolink support non-RTSP Reolink cameras (port 9000)?

**Answer:** Yes! Neolink is specifically designed for Reolink cameras that use the **Baichuan protocol** (port 9000). Supported cameras include:
- Reolink E1 series
- Reolink Argus series (battery cameras)
- Some doorbell models
- Older Reolink cameras without RTSP support

To use: Configure camera in `config/neolink.toml`, set `stream_type: "NEOLINK"` in cameras.json.

---

## Next Session Plan: Add Reolink E1 Camera via Neolink

**Camera Info (from screenshot):**
- IP: 192.168.10.123
- MAC: 44:ef:bf:27:0d:30
- Type: Reolink E1 (Baichuan protocol, port 9000, no RTSP/ONVIF)

**Implementation Steps:**

1. **Update `config/neolink.toml`** - Replace placeholder with real E1 camera:
```toml
[[cameras]]
name = "E1_CAMERA"  # Use a descriptive name or serial
address = "192.168.10.123:9000"
username = "admin"  # Or whatever the camera credentials are
password = "${REOLINK_E1_PASSWORD}"  # Get from user or AWS Secrets
channel_id = 0
stream = "subStream"  # Use subStream for grid view
```

2. **Add entry to `config/cameras.json`:**
```json
"E1_CAMERA": {
  "type": "reolink",
  "name": "E1 Camera Name",
  "serial": "E1_CAMERA",
  "stream_type": "NEOLINK",
  "ip": "192.168.10.123",
  ...
}
```

3. **Run `./update_mediamtx_paths.sh`** to add MediaMTX path

4. **Restart containers:** `./start.sh`

**Questions for user:**
- What name/serial to use for this camera?
- Camera credentials (username/password)?
- Are there other E1 cameras to add?

---

## TODO List

**Completed This Session:**

- [x] Implement Protect snapshot API in UniFiProtectService.get_snapshot()
- [x] Test UniFi MJPEG with camera 68d49398005cf203e400043f
- [x] Switch OFFICE KITCHEN from MJPEG to WEBRTC
- [x] Verify WebRTC streaming working

**Future Enhancements:**

- [ ] Add `max_rtsp_connections` field to cameras.json for direct passthrough support
  - UniFi supports multiple RTSP connections (unlike Eufy/SV3C)
  - Direct passthrough could achieve ~200ms latency (vs ~2s with transcoding)
- [ ] Add blank-frame detection to health monitor (browser-side black frames not detected)
- [ ] Add ICE state monitoring to health.js for better WebRTC health detection
- [ ] Consider STUN server for remote access (currently LAN-only)
- [ ] Test fullscreen main/sub stream switching with WEBRTC
- [ ] Investigate watchdog cooldown clearing bug (clearing on publisher_active=True may cause rapid cycling)

---

## Key Technical Details

### Protect Snapshot API Implementation

```python
# Login to Protect API
login_url = f"https://{protect_host}/api/auth/login"
session.post(login_url, json={"username": username, "password": password}, verify=False)

# Get snapshot
snapshot_url = f"https://{protect_host}/proxy/protect/api/cameras/{camera_id}/snapshot"
response = session.get(snapshot_url, verify=False)
```

- Uses `requests.Session()` for cookie-based auth persistence
- Auto re-authenticates on 401 response
- Self-signed cert handling with `verify=False`

---
