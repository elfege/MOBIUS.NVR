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

*Last updated: January 4, 2026 23:38 EST*

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Previous Session Summary

**Branch merged to main:** `docs_update_JAN_4_2026_a`

---

## Current Session

**Branch:** `mjpeg_status_fix_JAN_4_2026_b`
**Date:** January 4, 2026 (23:30 - 23:38 EST)

**Context compaction occurred at 23:30 EST** - Continued from previous session

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

### Files Modified

| File | Change |
|------|--------|
| [services/unifi_protect_service.py](services/unifi_protect_service.py) | Implemented Protect snapshot API with session auth |
| [services/unifi_mjpeg_capture_service.py](services/unifi_mjpeg_capture_service.py) | Store camera_service for restart capability |

### Commits This Session

1. `6a0d349` - Implement Protect snapshot API for UniFi MJPEG streaming
2. `b54a198` - Fix UniFi MJPEG restart - store camera_service reference

---

## TODO List

**Completed This Session:**

- [x] Implement Protect snapshot API in UniFiProtectService.get_snapshot()
- [x] Test UniFi MJPEG with camera 68d49398005cf203e400043f

**Future Enhancements:**

- [ ] Add blank-frame detection to health monitor (browser-side black frames not detected)
- [ ] Add ICE state monitoring to health.js for better WebRTC health detection
- [ ] Consider STUN server for remote access (currently LAN-only)
- [ ] Test fullscreen main/sub stream switching with WEBRTC
- [ ] Consider switching OFFICE KITCHEN from MJPEG to LL_HLS for lower latency (~2-3s vs ~10s)

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
