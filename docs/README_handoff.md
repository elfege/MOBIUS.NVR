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

*Last updated: January 8, 2026 00:45 EST*

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Previous Session Context

**Archived:** `docs/archive/handoffs/jan_8_2026_mjpeg_fixes/README_handoff_20260108_0045.md`

**See:** `docs/README_project_history.md` for full history

### Summary of January 8, 2026 Session (00:00-00:45 EST)

**Branch merged:** `gunicorn_thread_optimization_JAN_8_2026_b` → `main`

**Fixes completed:**

1. **Logging spam removed** - No more "zombies?" or FFmpeg param logging
2. **Client count leak fixed** - MJPEG generator uses `try/finally` to guarantee cleanup
3. **analyzeduration/probesize** - Increased to 2000000 in cameras.json
4. **cameras.json as single source of truth** - reolink_stream_handler reads ALL params from config
5. **NEOLINK separate config** - Uses `neolink` section, not `rtsp_input`

---

## Current Session

**Branch:** `websocket_mjpeg_JAN_8_2026_a`
**Date:** January 8, 2026

### NEXT TASK: WebSocket MJPEG Multiplexing

**Problem:** MJPEG loads slowly because browsers limit HTTP connections to ~6 per domain. With 16 cameras, 10 must wait in queue.

**Why WebRTC/HLS don't have this problem:**

- HLS.js uses HTTP/2 multiplexing (all requests share one TCP connection)
- WebRTC uses UDP/SRTP directly, bypassing HTTP

**Solution:** WebSocket-based MJPEG delivery

- Single WebSocket connection for ALL camera streams
- Server sends frames with camera ID prefix
- Frontend demultiplexes to appropriate `<canvas>` elements
- Eliminates browser connection limit bottleneck

**Implementation plan:**

1. Add Flask-SocketIO or similar WebSocket support
2. Create `/ws/mjpeg` endpoint that streams all cameras
3. Frontend: Single WebSocket, route frames to canvas by camera ID
4. Keep existing MJPEG endpoints as fallback

---

## TODO List

**WebSocket MJPEG (Pending):**

- [ ] Add WebSocket support (Flask-SocketIO or native)
- [ ] Create multiplexed MJPEG WebSocket endpoint
- [ ] Frontend WebSocket client with canvas rendering
- [ ] Test with 16 cameras simultaneously

**Remaining Issues:**

- [ ] MJPEG status API returns null for client_count (minor bug)

**Future Enhancements:**

- [ ] Research Neolink MQTT PTZ for E1 camera (direct reolink_aio doesn't work)
- [ ] Add `max_rtsp_connections` field to cameras.json for direct passthrough support
- [ ] Add blank-frame detection to health monitor
- [ ] Add ICE state monitoring to health.js
- [ ] Consider STUN server for remote access

---
