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

*Last updated: January 7, 2026 22:50 EST*

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Previous Session Context

**Archived:** `docs/archive/handoffs/jan_7_2026_fixes/README_handoff_20260107_2201.md`

**See:** `docs/README_project_history.md` section "January 7, 2026 (00:00-22:01 EST): Race Condition Fix + Gunicorn Revert + WEBRTC Fullscreen"

### Key Points for Next Session

1. **CRITICAL NEXT TASK:** MJPEG load time optimization
   - `?forceMJPEG=true` mode takes 60+ seconds to load
   - Streams should be pre-warmed after container uptime
   - Investigate: Are MJPEG streams actually low-res? May have broken something

2. **Architecture Understanding:**
   - ONE camera RTSP connection produces THREE outputs:
     - Sub stream (320x240 transcoded) → `rtsp://nvr-packager:8554/{serial}`
     - Main stream (passthrough `-c:v copy`) → `rtsp://nvr-packager:8554/{serial}_main`
     - MediaServer MJPEG (separate FFmpeg tapping MediaMTX) → multipart JPEG frames
   - Single-connection cameras (Eufy, SV3C, Neolink) tap MediaMTX, not camera directly

3. **Recent Fixes Applied:**
   - Race condition in stream slot reservation (start_time at creation)
   - Gunicorn reverted to Flask (thread starvation issue)
   - WEBRTC fullscreen URL handling fixed

4. **Known Camera Issues:**
   - Living Room: Degraded state
   - Kitchen: Black/not loading
   - Several cameras in Starting... state

---

## Current Session

**Branch:** `main` (direct commits)
**Date:** January 8, 2026

### MJPEG Pre-warming Implementation (commit 1128006)

**Problem:** MJPEG streams took 60+ seconds to load because:
1. MediaServer MJPEG capture only started when first client connected
2. FFmpeg was killed when last client disconnected
3. Each reconnect required full FFmpeg startup (connect, probe, decode)

**Solution implemented:**

| File | Change |
|------|--------|
| `app.py:155-189` | Added `auto_start_mediaserver_mjpeg()` - pre-warms all mediaserver MJPEG at startup |
| `services/mediaserver_mjpeg_service.py:501-525` | Modified `remove_client()` - no longer stops capture on disconnect |

**Key findings during investigation:**
- Eufy cameras use `/api/mediaserver/` endpoint (320x240, taps MediaMTX sub)
- Reolink/Amcrest use direct `/api/reolink/`, `/api/amcrest/` endpoints (640x480, direct to camera)
- Lock contention in frame buffer causing slowness with multiple clients
- Motion detection/recording independent of MJPEG display

**Trade-off:** ~3% CPU per camera idle vs instant MJPEG loading

### MJPEG Pre-warming Fix (commit 0e931ae) - 22:37 EST

**Problem:** MJPEG captures failed with 404 errors on startup because they tried to connect before HLS streams were publishing to MediaMTX.

**Fix:** Poll MediaMTX `/v3/paths/list` API until streams are actually publishing before starting MJPEG captures.

| File | Change |
|------|--------|
| `app.py:168-222` | Added MediaMTX polling loop before MJPEG pre-warming |

### Frontend forceMJPEG Fix (commit 736df5a) - 22:41 EST

**Problem:** `?forceMJPEG=true` on desktop caused WebRTC/HLS 404 errors when entering fullscreen. Line 1428 only checked `isPortableDevice()`, not `debugForceMJPEG`.

**Fix:** Check `(isPortableDevice() || debugForceMJPEG)` in `openFullscreen()`.

| File | Change |
|------|--------|
| `static/js/streaming/stream.js:1427-1430` | Added `debugForceMJPEG` check to fullscreen MJPEG→HLS switch |

### Session Findings - 22:50 EST

**MJPEG optimization results:**
- Same client refresh: FAST (HTTP keepalive, reuses connection)
- Different client: SLOW (new connection setup)
- Root cause: Flask dev server single-threaded, limited concurrent connections
- Client counts climbing high (14+ per camera) - suggests `remove_client()` not always called on disconnect

**Next step:** Gunicorn with 40+ threads to handle concurrent MJPEG streams properly.

---

## TODO List

**CRITICAL - Gunicorn Implementation:**

- [ ] Implement Gunicorn with 40+ worker threads (server has 56 cores, 128GB RAM)
- [ ] Investigate why previous Gunicorn attempt failed (thread starvation?)
- [ ] Test MJPEG loading with multiple concurrent clients

**MJPEG Optimization (Completed):**

- [x] Add MJPEG pre-warming to app.py startup
- [x] Remove _stop_capture() from remove_client()
- [x] Fix pre-warming to poll MediaMTX until streams publishing
- [x] Fix forceMJPEG fullscreen handling

**Remaining MJPEG Issues:**

- [ ] Fix client count leak (remove_client not always called on disconnect)
- [ ] Profile actual bottleneck with Gunicorn running

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
