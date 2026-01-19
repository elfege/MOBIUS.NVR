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

*Last updated: January 19, 2026 00:32 EST*

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Previous Session Context

**See:** `docs/README_project_history.md` for full history

**Last session (Jan 18, 2026):** DTLS/WebRTC iOS support successfully implemented. iOS Safari now achieves ~200ms WebRTC latency instead of 2-4s HLS fallback.

---

## Current Session (Jan 19, 2026 00:32 EST)

### WebSocket Stream Restart Notification Implementation

**Branch:** `dtls_webrtc_ios_JAN_18_2026_a`

**Problem Statement:**
Three related stream reliability issues:
1. Cameras show BLACK video despite "Live" status after backend StreamWatchdog restarts FFmpeg (HLS.js stays connected to stale MediaMTX session)
2. Cameras stuck on "Starting..." forever on frontend (no success event received)
3. Cameras stuck in STARTING state forever on backend (watchdog skips STARTING cameras)

**Root Cause Analysis:**
- When StreamWatchdog restarts FFmpeg, HLS.js stays connected to old MediaMTX session
- CameraStateMonitor polls every 10 seconds but only detects state transitions (degraded/offline → online)
- If UI was showing "Live" when backend restarted, no recovery event fires (online→online = no transition)
- Backend watchdog explicitly skips cameras in STARTING state (line 243 in stream_watchdog.py)

**Solution Implemented:**

1. **WebSocket `/stream_events` namespace** for instant restart notifications:
   - Backend broadcasts `stream_restarted` via SocketIO when StreamWatchdog restarts FFmpeg successfully
   - Frontend subscribes and triggers `handleBackendRecovery()` immediately
   - Eliminates black screen after backend restarts

2. **15-second frontend startup timeout**:
   - Prevents cameras stuck on "Starting..." forever
   - Dispatches `streamerror` event to trigger health monitor retry
   - Clears timeout on success or when stream is stopped

3. **60-second backend STARTING timeout**:
   - Added `starting_since` timestamp to CameraState
   - `_poll_mediamtx_api()` checks for stuck STARTING cameras
   - Transitions to DEGRADED so StreamWatchdog can pick them up

### Files Modified

| File | Changes |
|------|---------|
| [app.py](app.py#L1728-L1745) | Added `/stream_events` SocketIO namespace handlers |
| [services/stream_watchdog.py](services/stream_watchdog.py) | Added `set_socketio()`, `_broadcast_stream_restarted()`, broadcast on restart |
| [services/camera_state_tracker.py](services/camera_state_tracker.py) | Added `starting_since` field, `_check_starting_timeouts()` method |
| [static/js/streaming/stream.js](static/js/streaming/stream.js) | Added `connectStreamEventsSocket()`, startup timeout, timeout clearing |

### Commit

```
64a3eea Add WebSocket stream restart notification for instant HLS recovery
```

---

## TODO List

**Pending:**

- [ ] Test UI health monitoring after container restart (verify WebSocket solution works)
- [ ] Camera 95270001CSHLPO74 RTSP port issue (needs reboot or investigation)
- [ ] Verify "Terrace Shed" (T8441P12242302AC) camera recovers with new timeout logic

---
