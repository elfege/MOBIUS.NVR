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

*Last updated: January 4, 2026 10:45 EST*

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Previous Session Summary

**Branch merged to main:** `ui_health_refactor_JAN_4_2026_a`

**What was accomplished:**

1. **UI Recovery Detection** - When StreamWatchdog (backend) recovers a failed stream, UI now auto-refreshes the video element
2. **ffmpeg_process_alive Bug Fix** - Field was never updated, always returned `False`. Fixed by deriving from `publisher_active` for LL-HLS cameras

**For context, see in `README_project_history.md`:**

- "UI Health Refactor - January 4, 2026 (04:00 EST)" section
- "Stream Watchdog Redesign" section (January 4, 2026)

---

## Current Session

**Branch:** `stream_watchdog_investigation_JAN_4_2026_a`
**Started:** January 4, 2026 04:15 EST
**Context compactions:** 04:15 EST, 10:30 EST

### Fixes Applied This Session

1. **Motion Detector Health Check** - Modified `ffmpeg_motion_detector.py` to use CameraStateTracker instead of ffprobe for health checks. ffprobe was creating additional RTSP connections to MediaMTX, causing stream disruptions.

2. **UI Recovery Full Stop+Start** - Fixed `handleBackendRecovery()` in `stream.js` to perform full stop+start cycle instead of just HLS.js refresh. User confirmed manual stop+start works reliably; HLS.js refresh alone may stay connected to stale MediaMTX session.

3. **Nuclear Cleanup Disabled** - Commented out unconditional `_kill_all_ffmpeg_for_camera()` call in `_start_stream()`. This was being called on EVERY stream start, causing "torn down" messages in MediaMTX. Now MediaMTX handles stream lifecycle; `stop_stream()` does graceful termination.

### Files Modified

| File | Change |
|------|--------|
| `services/motion/ffmpeg_motion_detector.py` | Use CameraStateTracker.publisher_active instead of ffprobe |
| `app.py` | Pass camera_state_tracker to FFmpegMotionDetector |
| `static/js/streaming/stream.js` | handleBackendRecovery uses full stop+start cycle |
| `streaming/stream_manager.py` | Disabled nuclear cleanup in _start_stream() |

---

## TODO List

**Completed:**

- [x] Fix motion detector to use CameraStateTracker (no extra RTSP connections)
- [x] Fix UI recovery to use stop+start instead of refresh
- [x] Disable aggressive nuclear cleanup in _start_stream()

**Ready to Test:**

- [ ] Re-enable watchdog (STREAM_WATCHDOG_ENABLED=1) and restart container
- [ ] Monitor for "torn down" messages in MediaMTX logs
- [ ] Test motion detection with watchdog enabled

**Optional:**

- [ ] Remove/reduce false positive health checks in HealthMonitor
