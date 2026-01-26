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

*Last updated: January 26, 2026 08:17 EST*

Branch: `main` (session wrapped up)

**Context compaction occurred at 08:16 EST on January 26, 2026**

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Session Summary (Jan 26, 2026 01:00-02:45 EST)

### SV3C MJPEG Snap-Polling Implementation

Created dedicated MJPEG snap-polling service for SV3C camera (hi3510 chipset).

**Files Created:**
- `services/sv3c_mjpeg_capture_service.py` - Single-source, multi-client MJPEG via HTTP snapshots

**Files Modified:**
- `app.py` - Added `/api/sv3c/<camera_id>/stream/mjpeg` route + MJPEG camera isolation in `/api/stream/start`
- `static/js/streaming/mjpeg-stream.js` - Added SV3C case routing to dedicated endpoint
- `static/js/streaming/snapshot-stream.js` - Skip HLS/RTSP for native MJPEG cameras
- `static/js/streaming/stream.js` - Added SV3C to native MJPEG list, skip pre-warm for MJPEG cameras
- `services/motion/ffmpeg_motion_detector.py` - Skip FFmpeg motion for MJPEG cameras

**Key Discovery:**
- `/snapshot.cgi` returns 404 on this SV3C model
- `/tmpfs/auto.jpg` with Basic Auth works (HTTP 200, ~100KB images)
- Camera can only handle ~1 concurrent connection

### MJPEG Camera Isolation

Implemented complete isolation of MJPEG cameras from MediaMTX/RTSP paths:

1. **Backend `/api/stream/start`** - Returns MJPEG URL directly for `stream_type: MJPEG` cameras
2. **Frontend `preWarmHLSStreams()`** - Skips MJPEG cameras entirely
3. **Frontend `snapshot-stream.js`** - Checks camera type before calling HLS start
4. **FFmpeg Motion Detection** - Skips MJPEG cameras (would require RTSP)

**Rationale:** Budget cameras like SV3C can only handle 1 connection. MJPEG snap-polling is the ONLY connection - no RTSP, no FFmpeg, no MediaMTX.

### max_connections Schema Addition

Added `max_connections` parameter to all 19 cameras in `cameras.json`:

| max_connections | Cameras |
|-----------------|---------|
| **3** | UniFi (OFFICE KITCHEN), Amcrest (LOBBY), Reolink Living |
| **2** | Other Reolink cameras (6 total) |
| **1** | All Eufy (9) + SV3C (1) |

**Schema placement:** Right after `stream_type` with `_max_connections_note` documentation.

**Purpose:** Future enforcement of connection limits, UI warnings, automatic snap-polling selection.

### Commits Made

1. `c10880b` - Fix SV3C snapshot endpoint: use /tmpfs/auto.jpg with Basic Auth
2. `ba10e76` - Fix: Skip HLS/RTSP start for native MJPEG cameras in snapshot mode
3. `beaba78` - Isolate MJPEG cameras from MediaMTX/RTSP paths completely
4. `51bc355` - Add max_connections parameter to all cameras in cameras.json
5. `66ec151` - Update cameras.json: power_supply_device_id for LAUNDRY ROOM

### Final State

- **SV3C** is set to `stream_type: WEBRTC` (user preference over slow MJPEG)
- **SV3C MJPEG service** is available if needed (`/api/sv3c/{id}/stream/mjpeg`)
- **max_connections: 1** for SV3C enables future automatic MJPEG selection
- All changes merged to main and pushed

---

## Previous Session Context

See `docs/README_project_history.md` for:
- Eufy two-way audio implementation (Phase 1 complete)
- go2rtc ONVIF backchannel setup
- Camera schema `two_way_audio` settings

---

## TODO List

**HIGH PRIORITY - Security:**

- [ ] **Eufy bridge credentials**: Stop writing `username`/`password` to `eufy_bridge.json`

**Two-Way Audio - Phase 2:**

- [x] Test Eufy talkback end-to-end (Phase 1) - WORKING!
- [x] Add `two_way_audio` capability to cameras.json
- [x] Deploy go2rtc container for ONVIF backchannel
- [x] Configure go2rtc.yaml with ONVIF streams
- [ ] Run `./start.sh` to reload go2rtc with credentials - **USER ACTION REQUIRED**
- [ ] Test Reolink E1 Zoom ONVIF two-way audio
- [ ] Create Flask handler for `protocol: onvif` routing

**SV3C / Budget Camera:**

- [x] Create SV3C MJPEG snap-polling service
- [x] Add max_connections schema to cameras.json
- [ ] Implement automatic MJPEG selection when `max_connections: 1`
- [ ] UI warning when enabling features that exceed connection limit

**Testing Needed:**

- [ ] Test SV3C with WEBRTC (current config)
- [ ] Test MJPEG endpoint if WEBRTC fails: `/api/sv3c/C6F0SgZ0N0PoL2/stream/mjpeg`

---
