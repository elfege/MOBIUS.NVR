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

*Last updated: January 4, 2026 03:35 EST*

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Current Session: January 4, 2026 (continued)

### Branch: `stream_watchdog_redesign_JAN_4_2026_a`

### Context from Previous Session

**Previous branch merged to main:** `stream_watchdog_backend_restart_JAN_3_2026_a`

### Stream Watchdog Implementation - COMPLETED

**All implementation steps completed:**

1. [x] Created `services/stream_watchdog.py` - New unified watchdog (345 lines)
2. [x] Added `restart_stream()` to StreamManager
3. [x] Added `restart_capture()` to all 4 MJPEG services
4. [x] Removed old watchdog code from StreamManager (~140 lines deleted)
5. [x] Updated app.py integration (import, start, cleanup)
6. [x] Updated .env: `STREAM_WATCHDOG_ENABLED=1` (replaces `ENABLE_WATCHDOG`)

### Files Modified This Session

| File | Action | Description |
|------|--------|-------------|
| `services/stream_watchdog.py` | **CREATED** | Unified watchdog using CameraStateTracker |
| `streaming/stream_manager.py` | Modified | Added restart_stream(), removed old watchdog |
| `services/reolink_mjpeg_capture_service.py` | Modified | Added restart_capture() |
| `services/amcrest_mjpeg_capture_service.py` | Modified | Added restart_capture() |
| `services/unifi_mjpeg_capture_service.py` | Modified | Added restart_capture() |
| `services/mjpeg_capture_service.py` | Modified | Added restart_capture() |
| `app.py` | Modified | Integrated StreamWatchdog startup/cleanup |
| `.env` | Modified | Added STREAM_WATCHDOG_ENABLED=1 (gitignored) |

### Architecture Summary

```
CameraStateTracker (polls MediaMTX every 5s)
         |
         v
StreamWatchdog (polls every 10s)
         |
         +---> StreamManager.restart_stream() for LL-HLS
         +---> MJPEG service.restart_capture() for MJPEG
```

**Key features:**

- Uses CameraStateTracker.can_retry() for exponential backoff (5s→120s max)
- Reports restart success/failure back to CameraStateTracker
- Configurable via `STREAM_WATCHDOG_ENABLED` env var
- Coexists with UI Health monitoring (UI detects browser/network, backend detects server)

**Race condition prevention:**

- `STARTUP_WARMUP_SECONDS=60`: Wait before first check (streams initializing)
- `RESTART_COOLDOWN_SECONDS=30`: Per-camera cooldown after restart attempt
- Skips cameras in STARTING state (still initializing)
- Respects exponential backoff from CameraStateTracker

---

### TODO List

**Stream Watchdog Redesign - COMPLETED:**

- [x] FFmpeg reconnect flags investigation
- [x] Unified MJPEG/LL-HLS state tracking
- [x] Plan new watchdog architecture
- [x] Create `services/stream_watchdog.py`
- [x] Add restart methods to StreamManager and MJPEG services
- [x] Remove old watchdog from StreamManager
- [x] Integrate and test

**Testing - VERIFIED (03:35 EST):**

- [x] Container restart to apply changes
- [x] Verify watchdog starts (check logs)
- [x] Test: Auto-restart on stream failure
  - `C6F0SgZ0N0PoL2`: publisher died → DEGRADED → restart successful → ONLINE
  - `T8416P0023352DA9` (Living Room): publisher died → restart successful → ONLINE
  - `T8441P12242302AC` (Terrace Shed): restart successful → ONLINE
- [x] UI vs Backend watchdog coexistence verified
  - `T8419P0024110C6A` (STAIRS): UI-only issue, STOP/START in UI fixed it (backend stream was fine)

**Deferred:**

- [ ] Test MJPEG camera restart (no MJPEG failures observed yet)
- [ ] Motion detection/recording services respect can_retry()
- [ ] Monitor MediaMTX "torn down" logs
