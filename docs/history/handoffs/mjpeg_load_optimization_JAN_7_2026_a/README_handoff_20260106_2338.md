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

*Last updated: January 6, 2026 23:18 EST*

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Current Session

**Branch:** `mjpeg_load_optimization_JAN_7_2026_a`
**Date:** January 6, 2026 (22:47-23:18 EST)

**Context compaction occurred mid-session** - Continued work on MJPEG load optimization.

### Work Completed This Session

#### 1. Phase 1 MJPEG Load Time Optimization

**Problem:** MJPEG streams loading too slowly (5-10 seconds per camera)

**Changes Made:**

1. **MediaServer initial_wait reduced** (1.0s → 0.2s)
   - File: `services/mediaserver_mjpeg_service.py` line 279
   - Reduces delay before FFmpeg starts reading from MediaMTX

2. **MJPEG frame polling interval reduced** (200ms → 100ms)
   - File: `static/js/streaming/mjpeg-stream.js` line 84
   - Faster detection of first frame via naturalWidth check

**Commit:** `4f63c17`

#### 2. Restored Missing iOS MJPEG Code

**Problem:** After Phase 1 optimizations, iOS MJPEG stopped working entirely (only UniFi came up).

**Root Cause:** Branch was created from main which had old `mjpeg-stream.js` without mediaserver fallback. The ios_hls_traditional_buffering_JAN_5_2026_b branch had the working code but was never merged.

**Fix:** Restored files from ios branch:
- `static/js/streaming/mjpeg-stream.js` - mediaserver fallback for eufy/sv3c/neolink
- `static/js/streaming/stream.js` - iOS MJPEG detection
- `services/mediaserver_mjpeg_service.py` - backend service
- `app.py` - `/api/mediaserver/<camera_id>/stream/mjpeg` endpoint was missing!

**Commits:** `b1a13a1`, `12a3d92`, `e4f7af0`

#### 3. Adaptive MediaMTX Polling

**Problem:** Fixed 5s wait per camera was too slow (5s × 16 cameras = 80s total)

**Fix:** Added `waitForMediaMTXStream()` method in `mjpeg-stream.js`:
- Polls `/hls/{cameraId}/index.m3u8` every 500ms instead of fixed wait
- Returns immediately when stream is ready
- Max timeout 10s (vs fixed 5s regardless of readiness)

**Commit:** `f0472c7`

#### 4. Parallel HLS Pre-warm for MJPEG

**Problem:** Even with adaptive polling, streams loaded sequentially. Each camera:
1. Started HLS (API call)
2. Polled MediaMTX up to 10s
3. Started MJPEG
4. Moved to next camera

**Fix:** Added `preWarmHLSStreams()` method in `stream.js`:
- Fires ALL HLS start requests in parallel at page load
- Identifies mediaserver cameras (eufy, sv3c, neolink) vs native MJPEG (reolink, unifi, amcrest)
- 1s pause after pre-warm to let MediaMTX register streams
- By time MJPEG connections start, streams are already publishing

**Commit:** `b2ed6dc`

**Expected Result:** Total load time reduced from O(n × wait_per_camera) to O(wait_once + n × small_delay)

---

### Previous Session Summary (ios_hls_traditional_buffering_JAN_5_2026_b)

Completed iOS MJPEG support:
- Fixed nginx buffering for MJPEG (proxy_buffering off)
- Fixed MJPEG element swap (video→img) - commit `fc9eda7`
- Fixed NEOLINK MJPEG routing - commit `03aadd0`
- Created MediaServer MJPEG service for iOS Safari

---

## Previous Session Reference (reolink_aio_stability_JAN_5_2026_b)

**Branch:** `reolink_aio_stability_JAN_5_2026_b`
**Date:** January 5, 2026 (14:39-19:35 EST)

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

#### 6. iOS Safari 502/Reloading Loop Fix

**Problem:** On iOS devices, the UI would cycle through 502.html → reloading.html → main page → back to reloading, creating an infinite loop. This worked fine on desktop Chrome/Firefox.

**Root Cause:** Two iOS Safari-specific issues:

1. `location.reload(true)` is deprecated and iOS Safari ignores the `true` parameter (no cache bypass)
2. Missing `cache: 'no-store'` in fetch calls causes iOS to return cached responses

**Fix:** Modified 4 files to use cache-busting query params instead of `location.reload(true)`:

- `nginx/502.html`:
  - Added `cache: 'no-store'` to `/api/status` fetch
  - Replace `location.reload(true)` with `window.location.replace(url + '?_t=' + Date.now())`

- `nginx/reloading.html` and `templates/reloading.html`:
  - Add cache-busting param to return URL navigation

- `static/js/connection-monitor.js`:
  - Replace `location.reload(true)` with cache-busting URL in offline modal recovery

**Commit:** `350e214`

#### 7. WebRTC Fullscreen Main Stream Fix (CRITICAL BUG FIX)

**Problem:** WebRTC cameras showed low-res (320x240) in fullscreen despite requesting main stream.

**User Report:** "This doesn't look HD to me" (Living_REOLINK in fullscreen)

**Investigation:**

1. Frontend correctly calls `/api/stream/start/{camera}` with `type: 'main'`
2. Backend correctly returns `_main` URL
3. WebRTC correctly connects to `{camera}_main/whep`
4. But video was still 320x240 scaled up!

**Root Cause:** FFmpeg dual-output with passthrough was using **camera's sub stream** as input:

- Input: `h264Preview_01_sub` (320x240 on Reolink)
- Sub output: scale down (no-op, already 320x240)
- Main output: passthrough copies input → still 320x240!

When `video_main.c:v = "copy"` (passthrough), FFmpeg copies the INPUT directly. But if input is sub stream, passthrough just copies sub stream - NOT the camera's main stream.

**Fix:** Modified `streaming/stream_manager.py` to detect passthrough mode and use camera's main stream as input:

```python
# When dual-output with passthrough detected:
if protocol in ('LL_HLS', 'NEOLINK', 'WEBRTC') and main_is_passthrough:
    url_stream_type = 'main'  # ALWAYS use camera's main stream
```

**Result:** FFmpeg now uses:

- Input: `h264Preview_01_main` (2304x1296 native)
- Sub output: scale to 320x240 (transcoded)
- Main output: passthrough → 2304x1296 (copy)

**Commit:** `39e53e1`

**Verification:** After restart, FFmpeg commands show:

```text
-i rtsp://...192.168.10.186:554/h264Preview_01_main (BEFORE: _sub)
```

#### 8. WebRTC Backend API Integration

**Problem:** WebRTC manager went directly to WHEP endpoint without notifying backend.

**Fix:** Added backend API call in `static/js/streaming/webrtc-stream.js`:

- Calls `/api/stream/start/{cameraId}` with `type: streamType` before WHEP connection
- Ensures FFmpeg is publishing to `_main` path for fullscreen

**Commit:** `bb35e2a`

#### 9. iOS Safari WebRTC Fallback to HLS

**Problem:** iOS devices showed no streams in grid or fullscreen for WebRTC cameras.

**Root Cause:** iOS Safari requires encrypted WebRTC (DTLS-SRTP). Our MediaMTX config has `webrtcEncryption: no` for LAN-only performance.

**Fix:** Added iOS detection in `static/js/streaming/stream.js`:

- Detects iOS via user agent and platform check
- When iOS detected for WEBRTC stream type, falls back to HLS
- Updates `stream-type` data attribute so fullscreen/recovery works correctly

**Commit:** `c1ac72a`

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
