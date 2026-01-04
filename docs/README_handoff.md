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

### Key Finding: Nuclear Cleanup

Found that `_kill_all_ffmpeg_for_camera()` ("Nuclear cleanup") is called at the START of every `_start_stream()` (line 376 in stream_manager.py). This means every watchdog restart triggers nuclear cleanup, which may be overly aggressive. The cleanup was intended as a fallback, but it's being called proactively.

**Current observation:** Streams are healthy when watchdog is disabled (STREAM_WATCHDOG_ENABLED=0).

### Files Modified

| File | Change |
|------|--------|
| `services/motion/ffmpeg_motion_detector.py` | Use CameraStateTracker.publisher_active instead of ffprobe |
| `app.py` | Pass camera_state_tracker to FFmpegMotionDetector |
| `static/js/streaming/stream.js` | handleBackendRecovery uses full stop+start cycle |

---

## TODO List

**Completed:**

- [x] Fix motion detector to use CameraStateTracker (no extra RTSP connections)
- [x] Fix UI recovery to use stop+start instead of refresh

**Pending:**

- [ ] Investigate nuclear cleanup being called on every stream start
- [ ] Re-enable watchdog after nuclear cleanup investigation
- [ ] Test motion detection with watchdog enabled
- [ ] Monitor for "torn down" messages in MediaMTX logs

**Optional:**

- [ ] Remove/reduce false positive health checks in HealthMonitor
