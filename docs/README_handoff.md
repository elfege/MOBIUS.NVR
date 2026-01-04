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

*Last updated: January 4, 2026 03:10 EST*

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Current Session: January 4, 2026

### Branch: `stream_watchdog_redesign_JAN_4_2026_a`

### Context from Previous Session

**Previous branch merged to main:** `stream_watchdog_backend_restart_JAN_3_2026_a`

**Key accomplishments (Jan 3-4):**

1. **FFmpeg reconnect flags investigation** - Result: `-reconnect` flags NOT supported for RTSP (HTTP/HTTPS only)

2. **Unified camera state tracking** - All MJPEG capture services now report state to CameraStateTracker via `update_mjpeg_capture_state()`

3. **Stream Watchdog Redesign Plan** - Complete and ready for implementation

### Current Task: Stream Watchdog Implementation

**Plan location:** `/home/elfege/.claude/plans/jolly-whistling-parnas.md`

**Implementation steps:**

1. [ ] Create `services/stream_watchdog.py` - New unified watchdog
2. [ ] Add `restart_stream()` to StreamManager
3. [ ] Add `restart_capture()` to MJPEG services (4 files)
4. [ ] Remove old watchdog code from StreamManager
5. [ ] Update app.py integration
6. [ ] Update .env configuration

### Key Files to Reference

- `services/camera_state_tracker.py` - State management (has `can_retry()`, callbacks)
- `streaming/stream_manager.py` - Old watchdog code to remove (lines 856-984)
- `services/*_mjpeg_capture_service.py` - 4 MJPEG services need `restart_capture()`

---

### TODO List

**Stream Watchdog Redesign (CURRENT):**

- [x] FFmpeg reconnect flags investigation
- [x] Unified MJPEG/LL-HLS state tracking
- [x] Plan new watchdog architecture
- [ ] **NEXT**: Create `services/stream_watchdog.py`
- [ ] Add restart methods to StreamManager and MJPEG services
- [ ] Remove old watchdog from StreamManager
- [ ] Integrate and test

**Deferred:**

- [ ] Motion detection/recording services respect can_retry()
- [ ] Monitor MediaMTX "torn down" logs
