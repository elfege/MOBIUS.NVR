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

*Last updated: January 7, 2026 22:30 EST*

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

**Branch:** (new session - create branch as needed)
**Date:** (to be filled)

---

## TODO List

**MJPEG Optimization (Priority):**

- [ ] Investigate why MJPEG streams still load slowly despite optimizations
- [ ] Profile actual bottleneck (backend FFmpeg startup? MediaMTX? Browser?)
- [ ] Verify MJPEG streams are actually low-res
- [ ] Check MediaServer MJPEG pre-warming behavior
- [ ] Consider alternative approaches (lazy loading, pagination on iOS)

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
