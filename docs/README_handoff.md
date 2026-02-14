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

*Last updated: February 14, 2026 14:30 EST*

Branch: `stream_type_preferences_FEB_08_2026_a`

**Previous Session (Feb 7-8):** Full user auth system, user management, camera access control, PostgREST resilience. See `docs/README_project_history.md` Feb 7-8 section.

Always read `CLAUDE.md` — RULE 9 was updated: `docker compose restart` is now ALLOWED (not `./start.sh` due to AWS MFA hang).

---

## Current Session: February 8, 2026 (19:35 EST) → February 14, 2026 (ongoing)

**Feature:** Per-User Stream Type Preferences (live switching) + Stream Stability Fixes

### What's Done

1. **Backend API** (app.py, commit `9df1b90`):
   - `GET /api/user/stream-preferences` — returns user's saved preferences
   - `PUT /api/user/stream-preferences/<camera_serial>` — upserts preference
   - Uses existing `user_camera_preferences` table from migration 005
   - Validates against `VALID_STREAM_TYPES` set

2. **Frontend preference loader** (stream.js, commit `6c9daf6`):
   - `loadUserStreamPreferences()` — fetches prefs, overrides `data-stream-type` before streams init
   - Called in `init()` before `startAllStreams()`

3. **Live stream switch method** (stream.js, commit `6c9daf6`):
   - `switchStreamType(cameraSerial, newStreamType)` — stops current, handles video↔img swap for MJPEG, starts new, saves to DB
   - Reuses existing stop/start patterns from HD toggle and fullscreen transitions

4. **RULE 9 updated** (CLAUDE.md, commit `6c9daf6`):
   - `docker compose restart` now allowed
   - `./start.sh` and `./deploy.sh` still forbidden (AWS MFA hang rationale documented)

5. **Stream type selector UI** (streams.html, stream-item.css, commit `7993e31`):
   - Inline button row inside `.stream-controls` panel (accessible via sliders toggle in expanded/fullscreen)
   - Buttons: WebRTC, HLS, LL-HLS, MJPEG — active button highlighted in blue
   - CSS: `.stream-type-row` with `.stream-type-option` buttons

6. **JS event handlers updated** (stream.js, commit `7993e31`):
   - Replaced old `.stream-type-btn`/`.stream-type-selector`/`.stream-type-dropdown` handlers
   - New handlers use `.stream-type-row` and `.stream-type-option` classes
   - Controls panel open syncs active type highlight

7. **Fullscreen button fix** (stream.js, fullscreen.css, commit `7993e31`):
   - Fullscreen button in expanded modal now enters fullscreen instead of closing modal
   - Removed mobile CSS that changed icon to X in expanded mode
   - Users close modal via backdrop click

8. **MediaMTX path validation** (app.py, stream.js, commit `61d667b`):
   - Backend: `GET /api/mediamtx/path-status/<serial>` checks if camera has active MediaMTX publisher
   - Frontend: `switchStreamType()` pre-checks path before switching to WebRTC/HLS/LL_HLS
   - Toast notification tells user if path missing (server may need `start.sh` restart)
   - MJPEG bypasses the check (connects directly to camera)

9. **SV3C WebRTC fix** (cameras.json):
   - Removed `-reconnect`, `-reconnect_streamed`, `-reconnect_delay_max` from SV3C `rtsp_input`
   - These flags are HTTP-only in FFmpeg 7.x, caused `Option reconnect not found` crash
   - Also removed `-stimeout` (deprecated in FFmpeg 7.x)
   - SV3C_Living_3 now configured as `stream_type: "WEBRTC"` (was MJPEG)

10. **E1 Cat Feeders PTZ** (baichuan_ptz_handler.py, cameras.json):
    - Added `"ptz"` to Cat Feeders capabilities in cameras.json
    - Fixed Baichuan PTZ handler to skip speed param for E1 cameras (`host.supported(channel, "ptz_speed")`)

11. **Grid layout fix** (stream.js):
    - Guard in `_updateGridLayoutForVisibleCameras()` skips during fullscreen
    - `setupLayout()` called in `closeFullscreen()` and `forceExitFullscreen()`

12. **NEOLINK options** (streams.html):
    - Added NEOLINK and NEOLINK_LL_HLS buttons for Reolink cameras in stream type selector

### Segment buffer issue (pre-alarm recording)

- Segment buffer ffmpeg dies with code 0 for HALLWAY, Office Desk, SV3C
- For SV3C: publisher dying (reconnect flags) caused chain reaction
- For others: need to investigate after restart — may be MediaMTX path not ready at startup
- Pre-alarm recording not working (`Retrieved 0 buffer segments`)

### Storage Stats Bug Fix (February 14, 2026 01:20-01:23 EST)

**Problem:** UI showed 1011 GB / 1097 GB (92% full) when disk actually had 903 GB free (14% used)

**Root Cause:** Atomistic docker-compose mounts caused `/recordings` parent to be overlay FS
- Each subdirectory mounted separately (`/mnt/sdc/NVR_Recent/motion:/recordings/motion`, etc.)
- `os.statvfs('/recordings')` returned overlay FS stats (wrong)
- `os.statvfs('/recordings/motion')` returned actual disk stats (correct)

**Solution:**
1. Simplified docker-compose.yml mounts:
   - `/mnt/sdc/NVR_Recent:/recordings` (was 6 separate subdirs)
   - `/mnt/THE_BIG_DRIVE/NVR_RECORDINGS:/recordings/STORAGE` (was 4 separate subdirs)
2. Removed workaround code in storage_migration.py (lines 1120-1124)
3. Recreated container with `docker compose up -d --force-recreate nvr`

**Result:** Container now sees correct stats (196 GB used / 1099 GB = 18%)

**Files changed:**
- `docker-compose.yml` (commit 0707529)
- `services/recording/storage_migration.py` (commit fc9f926)

### Stream Stability Fixes — Phase 1 (February 14, 2026 ~14:00-14:30 EST)

**Problem:** Streams frequently freeze/go black, UI health monitor constantly restarting, manual restart button unreliable.

**Root Causes Identified:**
1. FFmpeg→MediaMTX race condition: `time.sleep(3)` marked streams "active" before MediaMTX publisher ready (5-15s needed)
2. UI health monitor and backend watchdog fighting each other (no coordination)
3. Conservative watchdog timing (30s cooldown blocks restarts)

**Fixes Applied (Phase 1 — commits `8957510`, `4f23f70`, `278d6a2`):**

13. **FFmpeg→MediaMTX race condition fix** (camera_state_tracker.py, stream_manager.py):
    - Added `wait_for_publisher_ready()` to CameraStateTracker — polls MediaMTX API every 1s until path ready (15s timeout)
    - LL_HLS stream start now waits for publisher confirmation before marking active
    - `restart_stream()` waits for publisher readiness before returning success
    - Reduced `STARTING_TIMEOUT_SECONDS` from 60 to 20

14. **UI/Backend recovery coordination** (stream.js, camera-state-monitor.js):
    - `onUnhealthy` handler now checks backend camera state before scheduling UI restart
    - If backend watchdog already knows stream is degraded/offline → UI defers to watchdog
    - Added `isBackendHandling()` method to CameraStateMonitor

15. **Watchdog timing & manual restart fix** (stream_watchdog.py, app.py):
    - Reduced `RESTART_COOLDOWN_SECONDS` from 30 to 10
    - Added `clear_cooldown()` method to StreamWatchdog
    - Manual restart endpoint now clears watchdog cooldown before restarting
    - Manual restart endpoint waits for MediaMTX publisher readiness (15s timeout)
    - Returns `publisher_ready` status in response

**Files Changed:**
- `services/camera_state_tracker.py` — `wait_for_publisher_ready()`, reduced STARTING timeout
- `streaming/stream_manager.py` — publisher readiness in `_start_stream()` and `restart_stream()`
- `static/js/streaming/stream.js` — backend state check in `onUnhealthy`
- `static/js/streaming/camera-state-monitor.js` — `isBackendHandling()` method
- `services/stream_watchdog.py` — reduced cooldown, `clear_cooldown()`
- `app.py` — restart endpoint with cooldown clear + publisher readiness wait

### Key Files

- `app.py` lines ~1468-1600: stream preference + MediaMTX path endpoints
- `app.py` lines ~1993-2089: stream restart endpoint (updated with publisher readiness)
- `static/js/streaming/stream.js`: `loadUserStreamPreferences()`, `switchStreamType()`, `onUnhealthy`
- `services/camera_state_tracker.py`: `wait_for_publisher_ready()`, exponential backoff
- `services/stream_watchdog.py`: `clear_cooldown()`, reduced timing
- `services/ptz/baichuan_ptz_handler.py`: E1 speed check fix
- `services/recording/segment_buffer.py`: pre-alarm buffer (investigating failures)

---

## TODO List

**Done this session:**

- [x] Backend API endpoints (GET/PUT stream preferences)
- [x] Frontend preference loader (`loadUserStreamPreferences`)
- [x] Live stream switch method (`switchStreamType`)
- [x] Stream type selector UI in controls panel
- [x] JS event handlers for stream type buttons
- [x] Fullscreen button fix (enters fullscreen from expanded modal)
- [x] MediaMTX path validation before stream type switch
- [x] SV3C ffmpeg fix (removed reconnect/stimeout flags)
- [x] E1 Cat Feeders PTZ (capability + speed check)
- [x] Grid layout fix (fullscreen exit race condition)
- [x] NEOLINK options for Reolink cameras
- [x] **Storage stats bug fix** — simplified docker mounts to fix overlay FS issue
- [x] **Phase 1.1** — FFmpeg→MediaMTX race condition fix (publisher readiness check)
- [x] **Phase 1.2** — UI/backend recovery coordination (onUnhealthy checks backend state)
- [x] **Phase 1.3** — Reduced watchdog cooldown (30s→10s), STARTING timeout (60s→20s)
- [x] **Phase 1.4** — Fixed manual restart button (clear cooldown + publisher readiness wait)

**Needs testing (requires `docker compose restart`):**

- [ ] Verify Phase 1 fixes: streams should load reliably on first try
- [ ] Manual restart button: should work within 10-15 seconds
- [ ] Verify no duplicate restart conflicts (UI + backend)
- [ ] End-to-end test: switch stream type, verify live switch works
- [ ] Verify preferences persist across page reload

**Pending (Phase 2 — Code Quality):**

- [ ] Centralize 30+ hardcoded timeouts to config/timeouts.yaml
- [ ] Centralize hardcoded MediaMTX addresses to config/services.yaml
- [ ] Remove commented-out code from MJPEG service files
- [ ] Fix bare except clauses in talkback_transcoder.py

**Pending (Phase 3 — Refactoring):**

- [ ] Extract MJPEG handler base class (reduce ~300 lines duplication)
- [ ] Fix circular import architecture
- [ ] Add camera state audit trail (90-day retention)

**Pending (Other):**

- [ ] file_operations_log cleanup: 98M rows (34GB) needs retention/pruning policy
- [ ] VACUUM ANALYZE on recordings table (never been vacuumed)
- [ ] Security: Implement stricter RLS policies (currently permissive)
- [ ] WebRTC HD/SD fallback — falls back too fast, doesn't retry HD
- [ ] Add snapshot feature in fullscreen mode
- [ ] Investigate segment buffer failures (HALLWAY, Office Desk) — pre-alarm recording broken
- [ ] Warm restart sub-service (`restart_warm.sh`)

---
