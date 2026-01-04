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

*Last updated: January 4, 2026 04:05 EST*

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
**Context compaction occurred at 04:15 EST**

### Investigation Findings (from previous session)

**Original Goal:** Re-enable motion detection and recording, verify they no longer break MediaMTX streams.

**Key Discovery:** The "torn down by 172.19.0.6" messages in MediaMTX logs are NOT caused by motion detection or recording (both are DISABLED).

**Root Cause Found:** StreamWatchdog restart loop on `T8416P0023352DA9` (Living Room/Eufy camera):

- Watchdog repeatedly thinks `publisher_active=false`
- Triggers restart approximately 20 times in quick succession
- Each restart causes a "torn down" message in MediaMTX

**Evidence from logs:**

```log
2026/01/04 09:12:41 INF [RTSP] [session ...] destroyed: torn down by 172.19.0.6:47514
INFO:services.stream_watchdog:[WATCHDOG] Camera T8416P0023352DA9 needs restart (type: LL_HLS)
```

**Verification Status:**

- Motion detection: Uses MediaMTX for LL_HLS cameras (confirmed in `ffmpeg_motion_detector.py:206-211`)
- Recording: Uses MediaMTX for LL_HLS cameras (confirmed in `recording_service.py:109-124`)
- Both are currently DISABLED in `recording_settings.json`

### Next Steps

1. Investigate why StreamWatchdog repeatedly restarts Living Room (Eufy) camera
2. Once watchdog stability confirmed, re-enable motion detection for testing
3. Verify no "torn down" messages occur from motion detection requests

---

## TODO List

**Pending (from previous session):**

- [ ] Container restart to apply Python/JS changes
- [ ] Verify ffmpeg_process_alive fix (STAIRS should show "Live" not "Degraded")
- [ ] Test MJPEG camera restart (no MJPEG failures observed yet)
- [ ] Motion detection/recording services respect can_retry()
- [ ] Monitor MediaMTX "torn down" logs

**Optional:**

- [ ] Remove/reduce false positive health checks in HealthMonitor (low priority - backend recovery takes precedence)
