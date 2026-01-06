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

*Last updated: January 5, 2026 16:20 EST*

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Current Session

**Branch:** `reolink_aio_stability_JAN_5_2026_b`
**Date:** January 5, 2026 (14:39-16:20 EST)

**Context compaction occurred at 15:37 EST** - Continuing E1 mainStream work.
**Second context compaction occurred at ~16:00 EST** - Continuing fullscreen investigation.

### Work Completed This Session

#### 1. Configurable Neolink Stream Selection

**Problem:** E1 camera showed wrong resolution (704x480 subStream instead of 2304x1296 mainStream)

**Root Cause:** `update_neolink_config.sh` was hardcoding `stream = "subStream"` in neolink.toml

**Fix:** Modified script to read `neolink.stream` from cameras.json:

- `update_neolink_config.sh` lines 48, 98, 114 - Now uses `neolink.stream` field
- Commit: `f946a63`

#### 2. Neolink URL Builder Fix

**Problem:** After setting `neolink.stream = "mainStream"`, FFmpeg failed with `Error opening input file rtsp://neolink:8554/.../sub`

**Root Cause:** When Neolink is configured for mainStream, it ONLY exposes `/main` RTSP paths - NOT `/sub`. But the URL builder was still requesting `/sub`.

**Fix:** Modified `streaming/handlers/reolink_stream_handler.py` `_build_NEOlink_url()`:

- Checks `neolink.stream` config
- When "mainStream", forces `/main` path regardless of requested stream_type
- Commit: `6c193db`

**Result:** E1 now streaming successfully via mainStream:

```text
Built Neolink bridge URL for REOLINK Cat Feeders: rtsp://neolink:8554/95270000YPTKLLD6/main
✅ Started: REOLINK Cat Feeders
```

#### 3. User Configured All Cameras for Passthrough

User set `video_main.c:v = "copy"` for all cameras in cameras.json (not just Reolink)

#### 4. Stream Slot Zombie Bug Fix

**Problem:** Restart API and stream starts failed because dead slots weren't cleared

**Root Cause:** `is_stream_alive()` returns False for slots with status 'starting', but those zombie slots still blocked new starts

**Fix:** Added three improvements in `app.py` and `stream_manager.py`:

1. Restart API checks for ANY slot and force-removes zombies
2. Dual-output check verifies FFmpeg process is actually running (`process.poll() is None`)
3. Stale 'starting' slots (>30 seconds old) auto-removed

Commit: `cfdd166`

#### 5. Fullscreen Main Stream URL Investigation

**User Report:** "looks like neolink is still playing sub when in full screen: all neolink cameras"

**Investigation Results:**

1. **Backend - WORKING CORRECTLY:**
   - API returns `/hls/{serial}_main/index.m3u8` for `type: 'main'` requests
   - MediaMTX serves both sub (320x240) and main (2304x1296) paths correctly
   - Dual-output FFmpeg correctly publishes both streams

2. **Frontend - APPEARS CORRECT:**
   - `stream.js` `openFullscreen()` checks `streamType === 'NEOLINK'`
   - Calls `hlsManager.startStream(cameraId, videoEl, 'main')`
   - Uses backend-returned `stream_url` with `_main` suffix

3. **Real Issue - Neolink/E1 Connection Instability:**
   - Neolink logs show "Broken pipe" errors reconnecting to camera
   - Creates zombie slots in stream manager
   - Stream keeps dying and restarting
   - Fallback to sub stream on error

**Debug Logging Added:**

- `app.py` lines 807, 816 - Print statements for resolution and stream_url
- `hls-stream.js` lines 117, 129, 134-140 - Console logs for stream type and URLs
- Commit: `ae2b2b5`

**Verified via API tests:**

```bash
# Sub stream returns correct URL:
curl -X POST -d '{"type":"sub"}' /api/stream/start/95270000YPTKLLD6
# → stream_url: /hls/95270000YPTKLLD6/index.m3u8

# Main stream returns correct URL:
curl -X POST -d '{"type":"main"}' /api/stream/start/95270000YPTKLLD6
# → stream_url: /hls/95270000YPTKLLD6_main/index.m3u8
```

**MediaMTX verification:**

- Sub: `RESOLUTION=320x240` at `/95270000YPTKLLD6/`
- Main: `RESOLUTION=2304x1296` at `/95270000YPTKLLD6_main/`

---

### Previous Issues (from `_a` branch)

#### Issue 1: reolink_aio AttributeError on _transport.close()

**Error logs:**

```text
ERROR:services.motion.reolink_motion_service:Error monitoring Living_REOLINK: 'NoneType' object has no attribute 'close'
AttributeError: 'NoneType' object has no attribute 'close'
```

**Root Cause:** `reolink_aio` library calls `self._transport.close()` during logout, but `_transport` can be `None` if:

1. Connection was never fully established
2. Connection was already closed

**Fix Applied:** Added defensive checks in `services/motion/reolink_motion_service.py`:

- Check if `host.baichuan` exists before calling `unsubscribe_events()`
- Wrap `logout()` calls in try/except for partially initialized hosts
- Same pattern applied to `_cleanup_all()` method
- Downgraded cleanup errors from ERROR to DEBUG level

### Issue 2: UnexpectedDataError response mismatch

**Error logs:**

```text
reolink_aio.exceptions.UnexpectedDataError: Host 192.168.10.88:443 error mapping responses to requests, received 1 responses while requesting 20 responses
```

**Affected Cameras:**

- REOLINK OFFICE (95270001CSO4BPDZ) - 192.168.10.88
- Terrace South (95270001CSHLPO74) - 192.168.10.89 (same model/firmware)

**Root Cause:** Camera responds with fewer items than `reolink_aio` requested during `get_host_data()`. Common transient error when camera is under load or network has latency.

**Fix Applied:** Added exponential backoff specifically for `UnexpectedDataError`:

- Import `UnexpectedDataError` from `reolink_aio.exceptions`
- Track consecutive data errors separately
- Double retry delay on each occurrence (10s → 20s → 40s... up to 5min max)
- Reduce log verbosity: WARNING for first 3 errors, then DEBUG
- Reset backoff counters on successful connection or different error type

### Commits

- `c1fef1e` - Add defensive cleanup for reolink_aio Baichuan connections (merged to main)
- `5491e25` - Add exponential backoff for reolink_aio UnexpectedDataError

---

## Previous Session Reference

**Branch merged:** `fix_e1_stream_restart_btn_JAN_5_2026_a`
**Date:** January 5, 2026 (10:15-13:58 EST)

See `docs/README_project_history.md` for full session details including:

- E1 stream restart button implementation
- PTZ capability check fix (prevents PTZ attempts on non-PTZ cameras)
- E1 PTZ investigation results (reolink_aio doesn't support E1 PTZ)

Archived handoff: `docs/archive/handoffs/fix_e1_stream_restart_btn_JAN_5_2026_a/README_handoff_20260105_1358.md`

---

## TODO List

**Future Enhancements:**

- [ ] Research Neolink MQTT PTZ for E1 camera (direct reolink_aio doesn't work)
- [ ] Add `max_rtsp_connections` field to cameras.json for direct passthrough support
- [ ] Add blank-frame detection to health monitor
- [ ] Add ICE state monitoring to health.js
- [ ] Consider STUN server for remote access
- [ ] Test fullscreen main/sub stream switching with WEBRTC
- [ ] Investigate watchdog cooldown clearing bug
- [ ] Investigate UI freezes (health monitor canvas ops at 2s per camera)

---
