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

*Last updated: January 7, 2026 07:33 EST*

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Current Session

**Branch:** `main` (direct commits)
**Date:** January 7, 2026 (00:00-07:33 EST)

### Issue: Fullscreen Black Stream (WEBRTC cameras)

**Symptoms:**

- Clicking fullscreen on WEBRTC cameras (e.g., Living Room) showed black video
- Main stream passthrough not loading
- Container occasionally showed "Server Unavailable" / unhealthy

**Root Cause:**

1. Backend `start_stream()` only returned `/hls/` URLs for LL_HLS/NEOLINK protocols
2. WEBRTC cameras got `/api/streams/{serial}_main/playlist.m3u8` URLs instead
3. Frontend `hls-stream.js` only trusted `/hls/` URLs (line 133)
4. Frontend fell back to sub stream URL without `_main` suffix → black screen

**Fix Applied (commit 453053e):**

| File | Change |
|------|--------|
| `streaming/stream_manager.py:363` | Added `WEBRTC` to protocol check for `/hls/` URL return |
| `static/js/streaming/hls-stream.js:133` | Accept both `/hls/` and `/api/` URLs from backend |
| `static/js/streaming/hls-stream.js:142` | Fix fallback URL to include `_main` suffix for main streams |

**Result:**

- WEBRTC cameras now return correct `/hls/{serial}_main/index.m3u8` for fullscreen
- Frontend correctly uses backend-provided URLs
- Fallback URL construction includes `_main` suffix when needed

---

### Issue: FFmpeg Broken Pipe Errors for AMCREST LOBBY

**Symptoms:**
- `[vost#1:0/copy @ ...] Error submitting a packet to the muxer: Broken pipe` on `_main` stream
- `LL-HLS publisher died (code 0)` errors at startup
- MediaMTX logs showing `closing existing publisher` multiple times
- `🎬 Auto-starting HLS streams` appearing 2-3 times in logs

**Root Cause Analysis:**
1. **Flask debug=True** in `app.run()` spawns TWO processes (parent + reloader child)
2. **Duplicate auto-start blocks** in app.py:
   - Lines 121-153: `auto_start_all_streams()` inside `with app.app_context()`
   - Lines 413-425: Another loop at module level
3. Combined: 2 processes × 2 code blocks = up to 4 auto-start attempts
4. Race condition: Multiple FFmpeg processes try to publish to same MediaMTX path → broken pipe

**Fix Applied:**
1. Created `/home/elfege/0_NVR/entrypoint.sh` - Gunicorn startup script
2. Updated Dockerfile to use `ENTRYPOINT ["/bin/bash", "/app/entrypoint.sh"]`
3. Added `gunicorn` to requirements.txt
4. Removed duplicate auto-start block (lines 413-425) from app.py

**Files Modified:**
| File | Change |
|------|--------|
| `entrypoint.sh` | Created - Gunicorn startup (1 worker, 8 threads) |
| `Dockerfile` | Changed from `CMD ["python3", "app.py"]` to ENTRYPOINT with Gunicorn |
| `requirements.txt` | Added `gunicorn` |
| `app.py` | Removed duplicate auto-start block (was lines 413-425) |

**Result:**
- Only ONE `🎬 Auto-starting HLS streams` message in logs
- AMCREST LOBBY starts successfully without broken pipe errors
- Significantly fewer `closing existing publisher` in MediaMTX

---

## Previous Session Summary

**Branch merged:** `mjpeg_load_optimization_JAN_7_2026_a`
**Date:** January 6, 2026 (22:47-23:38 EST)

See `docs/README_project_history.md` section "MJPEG Load Time Optimization - January 6, 2026" for full details.

Archived handoff: `docs/archive/handoffs/mjpeg_load_optimization_JAN_7_2026_a/README_handoff_20260106_2338.md`

---

## TODO List

**MJPEG Optimization (Incomplete):**

- [ ] Investigate why MJPEG streams still load slowly despite all optimizations
- [ ] Profile actual bottleneck (backend FFmpeg startup? MediaMTX? Browser?)
- [ ] Consider alternative approaches (lazy loading, pagination on iOS)

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
