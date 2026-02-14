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

*Last updated: February 9, 2026 02:02 EST*

Branch: `stream_type_preferences_FEB_08_2026_a`

**Previous Session (Feb 7-8):** Full user auth system, user management, camera access control, PostgREST resilience. See `docs/README_project_history.md` Feb 7-8 section.

Always read `CLAUDE.md` — RULE 9 was updated: `docker compose restart` is now ALLOWED (not `./start.sh` due to AWS MFA hang).

---

## Current Session: February 8, 2026 (19:35 EST) → February 9, 2026 (02:02 EST)

**Feature:** Per-User Stream Type Preferences (live switching)

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

### Key Files

- `app.py` lines ~1468-1600: stream preference + MediaMTX path endpoints
- `static/js/streaming/stream.js`: `loadUserStreamPreferences()`, `switchStreamType()`, `_showStreamTypeToast()`
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
- [x] **Storage stats bug fix** - simplified docker mounts to fix overlay FS issue (UI showed 92% full when actually 14% used)

**Needs testing (restart done):**

- [ ] End-to-end test: switch stream type, verify live switch works
- [ ] Verify preferences persist across page reload
- [ ] Verify per-user isolation (different users see their own preferences)

**Pending:**

- [ ] file_operations_log cleanup: 98M rows (34GB) needs retention/pruning policy
- [ ] VACUUM ANALYZE on recordings table (never been vacuumed)
- [ ] Security: Implement stricter RLS policies (currently permissive)
- [ ] WebRTC HD/SD fallback - falls back too fast, doesn't retry HD
- [ ] Add snapshot feature in fullscreen mode
- [ ] Investigate segment buffer failures (HALLWAY, Office Desk) — pre-alarm recording broken
- [ ] Warm restart sub-service: a `restart_warm.sh` script (separate from `start.sh`) that skips AWS secrets pull — captures env vars from running container, regenerates MediaMTX paths, runs service init scripts, restarts container with saved env. No MFA hang = Claude Code can run it (solves RULE 9 restriction). Triggered from UI or by Claude directly. `start.sh` remains for cold starts only.

---
