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

*Last updated: January 8, 2026 05:20 EST*

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
**Date:** January 8, 2026 (05:00-05:20 EST)

### COMPLETED: WebSocket MJPEG Multiplexing

**Problem solved:** MJPEG loads slowly because browsers limit HTTP connections to ~6 per domain. With 16 cameras, 10 must wait in queue.

**Solution implemented:** WebSocket-based MJPEG delivery

#### Files Created

| File | Purpose |
|------|---------|
| `services/websocket_mjpeg_service.py` | Backend WebSocket MJPEG broadcast service |
| `static/js/streaming/websocket-mjpeg-stream.js` | Frontend WebSocket client with element management |

#### Files Modified

| File | Change |
|------|--------|
| `requirements.txt` | Added flask-socketio, python-socketio, python-engineio, simple-websocket |
| `app.py` | Added Flask-SocketIO initialization, /mjpeg namespace handlers, status API |
| `static/js/streaming/stream.js` | Integrated WebSocket MJPEG manager, added startWebSocketMJPEGStreams() |

#### Architecture

**Backend (Flask-SocketIO):**
```
┌─────────────────────────────────────────────────────────┐
│ WebSocket MJPEG Service                                 │
├─────────────────────────────────────────────────────────┤
│ Namespace: /mjpeg                                       │
│                                                         │
│ Events:                                                 │
│   connect    → Client connected                         │
│   subscribe  → {'cameras': [serial1, serial2, ...]}     │
│   unsubscribe → Stop receiving frames                   │
│   disconnect → Client disconnected                      │
│                                                         │
│ Outgoing:                                               │
│   mjpeg_frames → {frames: [{camera_id, frame, ...}]}    │
│                                                         │
│ Broadcast loop:                                         │
│   - Reads from mediaserver_mjpeg_service.frame_buffers  │
│   - Sends base64-encoded frames with camera ID          │
│   - Target: 2 FPS (matches existing MJPEG rate)         │
└─────────────────────────────────────────────────────────┘
```

**Frontend (WebSocket client):**
```
┌─────────────────────────────────────────────────────────┐
│ WebSocketMJPEGStreamManager                             │
├─────────────────────────────────────────────────────────┤
│ connect()     → Connect to Socket.IO /mjpeg namespace   │
│ subscribe()   → Register cameras + element mapping      │
│ _handleFrames → Decode base64, update img.src           │
│ disconnect()  → Clean up connection                     │
└─────────────────────────────────────────────────────────┘
```

#### Usage

Enable WebSocket MJPEG mode with URL parameters:

```
https://192.168.10.20:8443/streams?forceMJPEG=true&useWebSocketMJPEG=true
```

- `forceMJPEG=true` - Use MJPEG instead of HLS/WebRTC (required for desktop)
- `useWebSocketMJPEG=true` - Use WebSocket multiplexing instead of HTTP MJPEG

On mobile/portable devices, `forceMJPEG` is automatic; only need `useWebSocketMJPEG=true`.

#### Benefits

- **Single connection**: All 16 cameras over 1 WebSocket vs 16 HTTP connections
- **No browser limit**: Bypasses ~6 connection per domain limit
- **Instant loading**: No connection queuing, all cameras get frames simultaneously
- **Fallback**: Automatically falls back to HTTP MJPEG if WebSocket fails

#### Status API

```bash
curl http://localhost:5000/api/status/websocket-mjpeg
```

Returns:
```json
{
  "success": true,
  "active_clients": 0,
  "broadcast_running": false,
  "target_fps": 2,
  "clients": {}
}
```

---

## TODO List

**WebSocket MJPEG (COMPLETED):**

- [x] Add WebSocket support (Flask-SocketIO)
- [x] Create multiplexed MJPEG WebSocket endpoint
- [x] Frontend WebSocket client with canvas rendering
- [x] Test container startup and Socket.IO availability

**Testing needed:**

- [ ] Test with browser client using `?forceMJPEG=true&useWebSocketMJPEG=true`
- [ ] Verify all 16 cameras load simultaneously
- [ ] Compare load time vs HTTP MJPEG

**Remaining Issues:**

- [ ] MJPEG status API returns null for client_count (minor bug)

**Future Enhancements:**

- [ ] Research Neolink MQTT PTZ for E1 camera (direct reolink_aio doesn't work)
- [ ] Add `max_rtsp_connections` field to cameras.json for direct passthrough support
- [ ] Add blank-frame detection to health monitor
- [ ] Add ICE state monitoring to health.js
- [ ] Consider STUN server for remote access

---
