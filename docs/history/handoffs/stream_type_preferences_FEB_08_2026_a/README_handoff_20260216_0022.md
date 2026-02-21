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

*Last updated: February 16, 2026 10:30 EST*

Branch: `stream_type_preferences_FEB_08_2026_a`

**📊 COMPREHENSIVE TIMELINE:** See [SESSION_TIMELINE_FEB_08_16_2026.md](SESSION_TIMELINE_FEB_08_16_2026.md) for full chronological breakdown, disaster recovery details, and uncommitted work analysis.

**Previous Session (Feb 7-8):** Full user auth system, user management, camera access control, PostgREST resilience. See `docs/README_project_history.md` Feb 7-8 section.

**⚠️ UNCOMMITTED WORK DETECTED:**
- `.gitignore` modified
- **Hundreds of recursive `docs/docs/docs/...` files deleted** (appears to be disaster cleanup artifact)
- No dangerous `rm -rf $VARIABLE/` patterns found in NVR codebase (only in external ~/0_SCRIPTS/0_SYNC/remover.sh)

Always read `CLAUDE.md` — Now comprehensive version 1.0 adapted from dDMSC 2.2 template.

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

### Standby Overlay + Monitor Detection (February 14, 2026 ~14:30 EST, commit `7c49a2c`)

16. **Monitor standby detection** (visibility-manager.js, standby-overlay.css, streams.html):
    - Page Visibility API detects monitor standby / screen lock / tab switch
    - 3s grace period ignores brief flickers
    - On sleep: tears down all browser-side stream consumers, stops health/state monitors
    - On wake: shows "Reloading Streams" with sped-up animation, reloads page after 1.8s
    - CSS: 5 concentric rotating rings, pulsing center eye, floating particles

### Docker Image Recovery + TLS Cert Fix (February 15, 2026 17:00-21:20 EST)

**Docker image recovery successful:**
- Extracted 21MB (1,332 files) from `0_nvr-nvr:latest` image (built Feb 13)
- Image contained **Feb 9 snapshot** — 11 days newer than Jan 29 version on host
- Recovered cameras.json with FFmpeg 7.x fixes, PTZ updates, stream type changes
- See `retrieved_files_post_catastrophic_loss_of_feb_15_2026/README.md` for full details

**Files recovered (newer than current):**
- `cameras.json` (Feb 9) — FFmpeg 7.x compatibility, SV3C WEBRTC, Cat Feeders PTZ
- `recording_settings.json` (Feb 9)
- `go2rtc.yaml` (Jan 29 22:55)
- `persistent.json` (Feb 9)

**TLS cert auto-generation fix (commit f2bdba1):**
- `start.sh` now auto-generates self-signed certs if missing
- Prevents MediaMTX/nginx crash loop after disasters
- Lines 84-90: check + auto-generate before container start

**Issue identified:**
- Feb 9 cameras.json has Entrance door with `"rtsp": null`
- Background service expects RTSP config, causing repeated exceptions
- App became unresponsive (HTTP 000) despite Gunicorn running
- User running `./deploy.sh` to rebuild

**Feb 9 cameras.json restored (21:23 EST):**
- Copied from recovery to config/cameras.json (gitignored, can't commit)
- Containers restarted
- Backup: config/cameras.json.backup_jan29_NOW

**Next session TODO:**
- Push 2 commits (f2bdba1 TLS fix, b294074 handoff update)
- Monitor for Entrance door RTSP null exceptions
- Verify app responds (was HTTP 000 with Jan 29 version)
- Test Phase 1 stream stability fixes
- Investigate why hidden cameras show in UI (localStorage vs cameras.json flag mismatch)

### Context Compaction & Git Recovery (February 15, 2026 ~17:00 EST)

- Context compaction occurred
- Git repo was lost (`.git` directory deleted during recursive docs cleanup from another instance)
- Recovered: `git init` → `git remote add` → `git fetch` → force checkout of `stream_type_preferences_FEB_08_2026_a`
- Verified `low_level_handlers/process_reaper.py` exists and is tracked (was already in remote)
- Container crash (`ModuleNotFoundError: No module named 'low_level_handlers.process_reaper'`) was a pre-existing issue — file was present in the remote all along

### CLAUDE.md Adaptation (February 16, 2026 ~10:00 EST)

**User Request:** "update it entirely model from server:/dDMSC/" → "I said model, not copy. Must adapt."

**Actions:**
- Commit: `74b40c5` - Copied dDMSC CLAUDE.md verbatim (rejected)
- Commit: `abde82e` - Adapted for NVR (16KB dDMSC → 19KB NVR-specific)

**Changes Applied:**
- Used dDMSC 2.2 comprehensive structure (numbered rules, sections)
- **Removed:** Flyway migrations, PostgREST-only DB access, NTCIP 1203 protocols, Jira integration, dotstream team structure, intercom system, memory sync
- **Preserved:** NVR streaming architecture, camera serial number rules, MediaMTX tap architecture, container restart restrictions
- **Added:** Section 5 (NVR-Specific Architecture), streaming troubleshooting, camera credentials access, recording paths, container services table

**Version:** 1.0 (adapted from dDMSC 2.2)

### Session Timeline Document (February 16, 2026 ~10:30 EST)

**Created:** `docs/SESSION_TIMELINE_FEB_08_16_2026.md` (commit `1bfa087`)

Comprehensive chronological breakdown of:
- 8-day session (Feb 8-16)
- 4 major phases (stream preferences, storage fix, stability fixes, monitor standby)
- Disaster recovery timeline (Feb 15)
- Safety audit results (no dangerous rm -rf patterns in NVR code)
- Uncommitted work analysis (recursive docs/ deletions)
- Testing requirements for Phase 1 stream stability

### Key Files

- `app.py` lines ~1468-1600: stream preference + MediaMTX path endpoints
- `app.py` lines ~1993-2089: stream restart endpoint (updated with publisher readiness)
- `static/js/streaming/stream.js`: `loadUserStreamPreferences()`, `switchStreamType()`, `onUnhealthy`
- `services/camera_state_tracker.py`: `wait_for_publisher_ready()`, exponential backoff
- `services/stream_watchdog.py`: `clear_cooldown()`, reduced timing
- `services/ptz/baichuan_ptz_handler.py`: E1 speed check fix
- `services/recording/segment_buffer.py`: pre-alarm buffer (investigating failures)
- **`CLAUDE.md`**: Comprehensive NVR-specific instructions (version 1.0)
- **`docs/SESSION_TIMELINE_FEB_08_16_2026.md`**: Full session timeline and analysis

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
- [x] **Monitor standby detection** — Page Visibility API with animated overlay
- [x] **Disaster recovery** — Docker image recovery (Feb 9 files), git recovery, TLS cert auto-gen
- [x] **CLAUDE.md adaptation** — Version 1.0 from dDMSC 2.2 template
- [x] **Session timeline document** — Comprehensive chronological analysis

**Immediate Next Steps:**

- [ ] **Push 6 commits to remote** (auth required): f2bdba1, b294074, d677826, 74b40c5, abde82e, 1bfa087
- [ ] **Decide on recursive docs/ deletions** — hundreds of files in git status (intentional cleanup or disaster artifact?)
- [ ] **Restart containers** to test Phase 1 stream stability fixes: `docker compose restart`

**Needs testing (requires `docker compose restart`):**

- [ ] Verify Phase 1 fixes: streams should load reliably on first try
- [ ] Manual restart button: should work within 10-15 seconds
- [ ] Verify no duplicate restart conflicts (UI + backend)
- [ ] End-to-end test: switch stream type, verify live switch works
- [ ] Verify preferences persist across page reload
- [ ] Monitor for Entrance door RTSP null exceptions

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
