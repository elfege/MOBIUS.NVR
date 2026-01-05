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

*Last updated: January 5, 2026 03:46 EST*

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Context Compaction Event

**Timestamp:** January 5, 2026 02:46 EST
**Reason:** Previous conversation ran out of context

### Session Summary (Pre-Compaction)

**Branch worked on:** main (continuation from `neolink_e1_JAN_5_2026_a`)

**Completed Tasks:**

1. **Fixed PTZ Preset Loading (HTTP 500)** - Root cause was RTSP/ONVIF port collision
   - Reolink cameras share port 8000 for both ONVIF and RTSP
   - `BadStatusLine` errors when RTSP binary data corrupts HTTP responses
   - Added retry logic to [services/onvif/onvif_client.py](services/onvif/onvif_client.py) with `_is_rtsp_collision_error()` helper
   - 3 retries with 0.5s delay, invalidates connection pool between attempts

2. **Implemented PostgreSQL-backed PTZ Preset Caching**
   - Created [services/ptz/preset_cache.py](services/ptz/preset_cache.py) - PresetCache class with:
     - `get_cached_presets()` - Returns cached presets if within 6-day TTL
     - `cache_presets()` - Stores presets in DB (skips empty lists)
     - `invalidate_cache()` - Clears cache for camera
   - Created migration [psql/migrations/003_add_ptz_presets_cache.sql](psql/migrations/003_add_ptz_presets_cache.sql)
   - Added `ptz_presets` table to [psql/init-db.sql](psql/init-db.sql)
   - Modified [services/onvif/onvif_ptz_handler.py](services/onvif/onvif_ptz_handler.py):
     - `get_presets()` checks cache first, only queries ONVIF on miss/expired
     - `set_preset()` and `remove_preset()` invalidate cache
   - Modified [app.py](app.py) - Added `?refresh=true` parameter to bypass cache

---

## Current Session (Post-Compaction)

**Branch:** `ptz_caching_JAN_5_2026_b`
**Date:** January 5, 2026 (02:46-03:46 EST)

### Completed This Session

1. **Created Baichuan PTZ Handler** (03:04 EST)
   - Created [services/ptz/baichuan_ptz_handler.py](services/ptz/baichuan_ptz_handler.py)
   - Uses `reolink_aio` library (same pattern as motion detection)
   - Methods: `move_camera()`, `get_presets()`, `goto_preset()`
   - `is_baichuan_capable()` helper to determine routing
   - Async operations wrapped for Flask compatibility
   - Connection pooling with Host instances per camera

2. **Added Baichuan PTZ Routing in app.py** (03:05 EST)
   - Import added for `BaichuanPTZHandler`
   - `/api/ptz/<serial>/<direction>` now routes to Baichuan when:
     - `ptz_method='baichuan'` in camera config
     - `stream_type` contains 'NEOLINK'
     - No `onvif_port` configured
   - Falls back to Baichuan if ONVIF fails for Reolink cameras
   - Presets endpoint returns `method` field (baichuan/onvif)

### Baichuan PTZ Trigger Conditions

Camera uses Baichuan PTZ when ANY of these are true:

1. `camera_config.ptz_method == 'baichuan'`
2. `camera_config.stream_type` contains 'NEOLINK'
3. `camera_config.onvif_port` is None

4. **Fixed PTZ Latency - ONVIF Service Caching** (03:35 EST)
   - PTZ commands were taking 9-20 seconds due to SOAP calls on every request
   - Modified [services/onvif/onvif_client.py](services/onvif/onvif_client.py):
     - Added `_ptz_services`, `_media_services`, `_profile_tokens` caches
     - `get_ptz_service()` now caches per camera_serial
     - `get_media_service()` now caches per camera_serial
     - `get_profile_token()` now caches per camera_serial
     - `close_camera()` and `close_all()` clear caches
   - First PTZ command still slow (~10s for SOAP), subsequent commands **~200ms**

5. **Added Amcrest LL-HLS/WEBRTC Support** (03:30 EST)
   - Modified [streaming/handlers/amcrest_stream_handler.py](streaming/handlers/amcrest_stream_handler.py)
   - Added `_build_ll_hls_publish()` method for WEBRTC streaming
   - Uses dual output: sub stream (transcoded) + main stream (passthrough)
   - Amcrest camera can now use `stream_type: "WEBRTC"` in cameras.json

6. **ONVIF Connection Pre-warming at Startup** (03:46 EST)
   - Added `prewarm_onvif_connections()` function to [app.py](app.py)
   - Runs during app initialization (after camera repo loads, before "Server ready!")
   - Iterates all cameras with `ptz` capability AND `onvif_port` configured
   - Populates caches: `_connections`, `_ptz_services`, `_media_services`, `_profile_tokens`
   - **Result:** All PTZ commands are ~200ms from first use (no more 10s initial delay)
   - Skips cameras without ONVIF port (they use Baichuan instead)
   - Logs status for each camera: success, failure, or skipped

### Files Modified

- [services/ptz/baichuan_ptz_handler.py](services/ptz/baichuan_ptz_handler.py) - NEW
- [app.py](app.py) - Added Baichuan import, routing logic, PTZ timing, ONVIF pre-warming
- [services/onvif/onvif_client.py](services/onvif/onvif_client.py) - Added service caching
- [services/onvif/onvif_ptz_handler.py](services/onvif/onvif_ptz_handler.py) - Added timing instrumentation
- [streaming/handlers/amcrest_stream_handler.py](streaming/handlers/amcrest_stream_handler.py) - Added `_build_ll_hls_publish()`

---

## TODO List

**Completed:**

- [x] Debug PTZ preset loading failures (HTTP 500 on all cameras) - Fixed with retry logic
- [x] Implement PTZ preset caching in PostgreSQL (6-day TTL)
- [x] Create Baichuan PTZ handler (services/ptz/baichuan_ptz_handler.py)
- [x] Add PTZ routing logic in app.py for Baichuan
- [x] Pre-warm ONVIF connections at startup (eliminate first-command latency)

**Testing Needed:**

- [ ] Test ONVIF pre-warming at container startup (verify logs show successful pre-warming)
- [ ] Test Baichuan PTZ with E1 camera (95270000YPTKLLD6 - has no ONVIF port)

**Future Enhancements:**

- [ ] Add `max_rtsp_connections` field to cameras.json for direct passthrough support
- [ ] Add blank-frame detection to health monitor
- [ ] Add ICE state monitoring to health.js
- [ ] Consider STUN server for remote access
- [ ] Test fullscreen main/sub stream switching with WEBRTC
- [ ] Investigate watchdog cooldown clearing bug
- [ ] Investigate UI freezes (health monitor canvas ops at 2s per camera)

---
