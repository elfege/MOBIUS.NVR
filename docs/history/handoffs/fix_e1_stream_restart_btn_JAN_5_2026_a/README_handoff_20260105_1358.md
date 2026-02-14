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

*Last updated: January 5, 2026 13:42 EST*

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Current Session

**Branch:** `fix_e1_stream_restart_btn_JAN_5_2026_a`
**Date:** January 5, 2026 (10:15-13:42 EST)

### Issue 1: E1 Stream Looping + Missing Restart Button

User reported:

1. E1 camera (95270000YPTKLLD6) playing same content in loops
2. Need a "restart camera" button that restarts backend FFmpeg (like Blue-Iris)

### Implementation

**1. Backend API Endpoint:** `app.py` (10:20 EST)

Added `/api/stream/restart/<camera_serial>` endpoint:

- Stops FFmpeg process
- Brief socket release delay (0.5s)
- Starts fresh stream
- Returns new stream URL
- Excludes MJPEG (stateless protocol)

**2. Frontend Restart Button:** `templates/streams.html`, `static/js/streaming/stream.js` (10:25 EST)

- Added orange "restart" button (`fa-redo-alt` icon) to stream controls
- Click handler calls `/api/stream/restart` then reconnects HLS.js/WebRTC
- Shows "Restarting..." status during operation

**3. CSS Styling:** `static/css/components/buttons.css`

- Added `.btn-warning` style (orange color)

### Button Differences

| Button | Icon | Action |
|--------|------|--------|
| Play (green) | fa-play | Start stream (backend + frontend) |
| Stop (red) | fa-stop | Stop stream (backend + frontend) |
| Refresh (blue) | fa-sync-alt | HLS.js client reconnect only |
| Restart (orange) | fa-redo-alt | Kill FFmpeg + restart + reconnect |

---

### Issue 2: E1 PTZ Control (13:35 EST)

**Decision**: E1 camera does NOT support direct PTZ via `reolink_aio` library.

**Fix Applied**: Updated `is_baichuan_capable()` in `services/ptz/baichuan_ptz_handler.py`:

- Now checks `capabilities` array first
- Returns `False` if `'ptz'` not in capabilities
- E1 has `capabilities: ["streaming"]` only (no ptz)
- This prevents PTZ routing attempts for non-PTZ cameras

**Note**: Neolink MQTT-based PTZ still TBD as alternative approach.

### Commits

- `81097c3` - Add /api/stream/restart endpoint for backend FFmpeg restart
- `8307bfa` - Add restart button to camera UI for backend FFmpeg restart
- `147246e` - Disable PTZ for cameras without 'ptz' capability

---

## Previous Session Reference

**Branch merged:** `connection_monitor_fix_JAN_5_2026_a`
**Date:** January 5, 2026 (04:03-04:15 EST)

See `docs/README_project_history.md` for full session details including:

- Connection monitor rapid retry loop fix (duplicate modal/interval prevention)

Archived handoff: `docs/archive/handoffs/connection_monitor_fix_JAN_5_2026_a/README_handoff_20260105_0410.md`

---

## TODO List

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
