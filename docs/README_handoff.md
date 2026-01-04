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

*Last updated: January 4, 2026 05:06 EST*

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Previous Session Summary

**Branch merged to main:** `stream_watchdog_investigation_JAN_4_2026_a`

**What was accomplished:**

1. **Motion Detector Health Check** - Use CameraStateTracker instead of ffprobe (no extra RTSP connections)
2. **UI Recovery Full Stop+Start** - handleBackendRecovery does full stop+start cycle instead of HLS.js refresh
3. **Nuclear Cleanup Disabled** - Removed aggressive _kill_all_ffmpeg_for_camera() from _start_stream()
4. **MJPEG Stream Status Fix** - Poll for naturalWidth to detect frames instead of unreliable load event

**For context, see in `README_project_history.md`:**

- "January 4, 2026: Stream Watchdog Investigation & UI Auto-Recovery" section

---

## Current Session

**Branch:** `mjpeg_status_fix_JAN_4_2026_b`
**Date:** January 4, 2026 (05:16 - 16:30 EST)

### Changes Made This Session

**05:22 - Fixed MJPEG Reolink streams stuck on "Starting" status**
- [stream.js:630-631](static/js/streaming/stream.js#L630-L631) - Pass 'sub' stream parameter
- [mjpeg-stream.js:14,20](static/js/streaming/mjpeg-stream.js#L14) - Default param `stream = 'sub'`

**05:26 - Latency documentation added to mediamtx.yml**
- Why 200ms/100ms worked before (direct RTSP passthrough)
- Why we stopped using passthrough (budget cameras = 1 RTSP connection only)
- FFmpeg GOP size (-g) change from 3 to 15 impacts latency
- Current config: 500ms/250ms segment durations

**16:25 - LAUNDRY ROOM camera unreachable**
- Camera 192.168.10.118 (E1 Zoom) - all ports refusing connections
- Needs physical reboot or network check

### PENDING FIX

**active_streams cleanup on FFmpeg connection failure**
- When FFmpeg fails to connect (camera unreachable), active_streams dict not cleaned
- Watchdog thinks stream is "starting" forever instead of marking failed
- File: [streaming/stream_manager.py](streaming/stream_manager.py)
- Location: Exception handler around line 595-606 needs to clean up properly

---

## TODO List

**Pending:**

- [ ] Monitor for "torn down" messages in MediaMTX logs (should be reduced now)

**Optional:**

- [ ] Remove/reduce false positive health checks in HealthMonitor
