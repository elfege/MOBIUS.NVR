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

*Last updated: March 07, 2026 00:45 EST*

**Branch:** `stream_switch_mjpeg_fix_MAR_04_2026_a` (latest of 3 issue branches)

**Previous Session (Feb 19):** Database camera config migration (Option B), CameraRepository DB-first loading.

**Previous Branch:** `db_camera_config_migration_FEB_19_2026_a`

---

## Current Session: March 04, 2026 (00:14 - 01:00 EST)

### Issues 1-3 from `docs/ISSUES_March_4_2026.md`

User filed 7 issues; this session addressed the top 3 by priority.

#### Issue 1: Fullscreen close button unresponsive on frozen streams

**Branch:** `fullscreen_exit_frozen_fix_MAR_04_2026_a` (pushed)

**Files Modified:**

1. `static/js/streaming/stream.js` (00:30 EST)
   - Changed fullscreen exit from async `closeFullscreen()` to synchronous `forceExitFullscreen()`
   - Added 2-second watchdog on `_fullscreenProcessing` debounce flag
   - NOTE: Click-capture overlay was added then reverted — conflicted with PTZ controls (same z-index 10000)
2. `static/css/components/fullscreen.css` (00:30 EST)
   - Overlay CSS added then reverted (same commit sequence)

#### Issue 2: Eufy PTZ preset overwriting fails (503)

**Branch:** `eufy_preset_retry_MAR_04_2026_a` (pushed)

**Files Modified:**

1. `services/eufy/eufy_bridge.py` (00:45 EST)
   - `_run_bridge_command()`: retry loop with backoff (up to 2 attempts, 5s/10s waits)
   - New `_keepalive_loop()`: pings port 3000 every 30 min, proactive restart on failure
   - `MAX_AUTO_RESTARTS` increased from 1 to 2
   - Keepalive thread started in `start()`
2. `app.py` (00:45 EST)
   - Preset save 503 response now includes `retry_available: true` + `bridge_status`
3. `static/js/controllers/ptz-controller.js` (00:45 EST)
   - Error handler shows inline "Retry" button when `retry_available` is true

#### Issue 3: Stream type switching broken (MJPEG → WebRTC)

**Branch:** `stream_switch_mjpeg_fix_MAR_04_2026_a` (pushed)

**Files Modified:**

1. `app.py` (01:00 EST)
   - New endpoint: `POST /api/mediamtx/create-path/<serial>` — creates sub+main MediaMTX paths via v3 API, starts FFmpeg publisher
2. `static/js/streaming/stream.js` (01:00 EST)
   - `switchStreamType()` Phase 0: when switching from MJPEG and no path exists, calls create-path endpoint instead of failing
   - MJPEG restart button now refreshes the stream (stop+start) instead of silently returning

---

## Previous Session: February 19, 2026 (10:00 EST) -> Ongoing

### Option B: Full Database Camera Configuration Migration

**User chose Option B** from `docs/PROPOSAL_database_config_migration.md`: Full migration of camera config from cameras.json to PostgreSQL database.

**Key architectural insight:** `CameraRepository` is the **sole abstraction layer** for camera config. All 14+ services use it via dependency injection. Modifying it to read from DB means zero changes to consuming services.

### Files Created

1. **`psql/migrations/011_cameras_table.sql`** (10:15 EST)
   - New `cameras` table mirroring cameras.json structure (serial PK, JSONB for nested configs)
   - New `camera_state` table for runtime state tracking
   - Extended `user_camera_preferences` with `visible` and `display_order` columns
   - RLS policies (permissive, consistent with existing migrations)
   - Indexes on type, hidden, stream_type, capabilities (GIN)

2. **`scripts/migrate_cameras_to_db.py`** (10:20 EST)
   - One-time migration script: cameras.json -> DB via PostgREST
   - Maps all fields: direct scalars, JSONB nested objects, extra_config catch-all
   - Handles boolean string parsing ("false" -> False)
   - Idempotent: uses upsert (--force flag for update existing)
   - Dry-run mode available

3. **`services/camera_config_sync.py`** (10:30 EST)
   - Auto-sync module: runs at app startup
   - Detects new cameras in JSON not in DB -> auto-migrates
   - Cameras in DB but not JSON -> log warning (no delete)
   - `force_sync_from_json()` for admin reset operations
   - `sync_cameras_json_to_db()` for startup sync

### Files Modified

4. **`services/camera_repository.py`** (10:35 EST) - **CORE CHANGE**
   - DB-first loading via PostgREST with JSON fallback
   - `_load_cameras_from_db()`: fetches all cameras, transforms to devices dict
   - `_db_row_to_camera_config()`: maps DB row back to cameras.json format
   - `_update_camera_in_db()`: PATCH specific fields via PostgREST
   - `_save_camera_to_db()`: upsert full camera record
   - `get_effective_stream_type(serial, user_id)`: NEW - resolves per-user stream type
   - `get_data_source()`: NEW - returns 'database', 'json', or 'none'
   - Write methods: update DB + JSON backup
   - `reload()`: tries DB first, falls back to JSON
   - Vendor configs (unifi, eufy, reolink, amcrest) still from JSON (static infra config)
   - **Interface unchanged**: all 14+ consuming services work without modification

5. **`app.py`** (10:40 EST)
   - Added import: `from services.camera_config_sync import sync_cameras_json_to_db`
   - Startup: calls `sync_cameras_json_to_db()` after CameraRepository init
   - Stream start endpoint (line ~1913): uses `get_effective_stream_type(serial, user_id)`
   - Stream restart endpoint (line ~2040): same per-user stream type resolution
   - New endpoint: `POST /api/cameras/force-sync` - admin force-sync from JSON
   - New endpoint: `GET /api/cameras/data-source` - check current data source

### Frontend Verification

6. **`static/js/controllers/camera-selector-controller.js`** - No changes needed
   - Already implements localStorage-first with server sync
   - `_loadFromCache()` -> `_applyInitialFilter()` -> `_loadFromServer()` (correct order)
   - Saves to both localStorage and DB on every change
   - Camera selection persistence is already working correctly

### Intercom Update

7. **MSG-040** posted to `server:~/0_CLAUDE_IC/intercom.md`
   - Informed all instances of DB migration in progress
   - Listed files not to touch during migration

---

## Architecture Summary

```
Before:
  cameras.json -> CameraRepository (in-memory) -> 14+ services

After:
  cameras.json -> auto-sync -> PostgreSQL DB -> CameraRepository (in-memory) -> 14+ services
                                     ^                      |
                                     |                      v
                          user_camera_preferences    get_effective_stream_type()
                          (per-user overrides)       (user pref > camera default)
```

---

## Status Summary

**Branch:** `db_camera_config_migration_FEB_19_2026_a` (created from iPad fix branch)

**DB migration code written + SQL migration run + 19 cameras migrated to DB.**

Completed:
1. [x] SQL migration 011 run on `nvr-postgres` container
2. [x] `NOTIFY pgrst, 'reload schema'` — PostgREST schema cache refreshed
3. [x] Migration script: all 19 cameras inserted successfully
4. [x] Fixed `global POSTGREST_URL` SyntaxError in migrate_cameras_to_db.py

Requires:
1. Restart containers: `./start.sh` (user must run, per RULE 9)
2. Verify cameras load from DB (check logs for "source: database")
3. Verify stream type switching works end-to-end

### Post-Compaction Updates (11:50 EST)

8. **`app.py`** — Registered `cert_bp` Blueprint (from side-chat MSG-042)
   - Import: `from services.cert_routes import cert_bp`
   - Registration: `app.register_blueprint(cert_bp)`
   - Enables `/install-cert`, `/install-cert/download`, `/api/cert/status` endpoints

9. **`deploy.sh`** — Improved with dDMSC flag parsing pattern
   - Added `--prune` and `--no-cache` CLI flags
   - Flags skip interactive prompts; without flags, prompts with timeouts
   - `--no-cache` defaults to yes on timeout (clean builds preferred)
   - Cleaner usage header with examples

10. **Intercom MSG-043** — ACKed MSG-041 and MSG-042

---

## Quick Reference

**Container Restart:**
```bash
./start.sh                      # User only (AWS MFA, per RULE 9)
```

**Camera Credentials:**
```bash
export AWS_PROFILE=personal bash -c "source ~/.bash_utils && get_cameras_credentials"
```

**Key Endpoints (NEW):**
- Force sync from JSON: `POST /api/cameras/force-sync` (admin only)
- Data source status: `GET /api/cameras/data-source`
- Stream preferences: `GET/PUT /api/user/stream-preferences/<camera_serial>`
- MediaMTX path status: `GET /api/mediamtx/path-status/<serial>`
- Stream restart: `POST /api/stream/restart/<camera_serial>`

**Documentation:**
- Migration proposal: `docs/PROPOSAL_database_config_migration.md`
- Engineering architecture: `docs/nvr_engineering_architecture.html`
- Session timeline: `docs/SESSION_TIMELINE_FEB_08_16_2026.md`
- Project history: `docs/README_project_history.md`

---

### Side-Chat Session: External API + TILES Integration (Feb 20, 09:00-09:45 EST)

**Context:** Running as `office-nvr` side-chat, coordinating with `office-tiles` via intercom.

13. **`services/external_api_routes.py`** (CREATED earlier, updated 09:30 EST)
    - Created Flask Blueprint with 4 endpoints for TILES integration (MSG-044)
    - Added `has_audio` field to `/api/external/cameras` response (MSG-062)
    - **Bearer token auth implemented** (MSG-069/MSG-070 directive):
      - `NVR_API_TOKEN` env var read at module init
      - `@require_auth` decorator replaces `@lan_only` on all endpoints
      - Production: requires `Authorization: Bearer <token>`, returns 401
      - Dev mode (no token): falls back to LAN-only IP check, logs warning
      - CORS updated to allow `Authorization` header
      - Follows dDMSC pattern (`~/dDMSC/api/app.py` lines 51-128)

14. **`docker-compose.yml`** (updated 09:35 EST)
    - Added `NVR_API_TOKEN: ${NVR_API_TOKEN:-}` to unified-nvr environment section

15. **`app.py`** (updated earlier in session)
    - Blueprint registration: import, register, init_external_api(camera_repo)

16. **7 HTML templates** (updated earlier)
    - Favicon added: `<link rel="icon" type="image/png" href="...mobius.png">`

17. **Intercom messages posted:**
    - MSG-045: ACK MSG-044, implementation details
    - MSG-047: Container restart notification
    - MSG-048: Port corrections (8081/8443, not 5000)
    - MSG-056: Corrections to JIRA's MSG-054
    - MSG-063: has_audio field added
    - MSG-071: Bearer token auth implemented, ACK MSG-069/MSG-070

18. **AWS Secrets Manager** (updated 10:00 EST)
    - `NVR_API_TOKEN` added to existing `NVR-Secrets` secret (merged, not replaced)
    - `pull_nvr_secrets()` in `start.sh` already pulls `NVR-Secrets` — token auto-exported
    - No changes needed to `pull_nvr_secrets()` or `start.sh`
    - MSG-072 posted with retrieval instructions for TILES

**Pending:**
- Container restart needed for all changes (`./start.sh`)
- TILES needs to pull `NVR_API_TOKEN` from AWS and add to their env

---

### Post-Compaction #2 Updates (13:30 EST)

**Stream loading investigation concluded:**

11. **Root cause analysis complete** — The slow stream loading was caused by two bugs in `_db_row_to_camera_config()`:
    - Bug 1: `row[field] is not None` filter dropped null-valued fields (rtsp_alias, power_supply_device_id)
    - Bug 2: No `id` field synthesized (migration script excluded it, but all stream handlers use `camera_config.get("id")`)
    - **Both fixed in commit 53df957**

12. **Remaining slow cameras are PRE-EXISTING** — not DB migration related:
    - T8416P0023352DA9 (Living Room), T8416P0023370398 (Office Desk), T8419P0024110C6A (STAIRS) — Eufy bridge dependency, bridge fails to start (3/5 restart attempts failed)
    - 95270000YPTKLLD6 (REOLINK Cat Feeders) — Neolink bridge dependency
    - These cameras timeout at 15s, watchdog recovers them eventually
    - User confirmed: "but faster now" (DB migration regression resolved)

---

## Next Session TODO

**Testing Required (DB Migration):**
- [x] Run SQL migration 011
- [x] Run cameras.json -> DB migration script (19 cameras migrated)
- [x] Restart containers (user runs `./start.sh`)
- [x] Verify cameras load from database (check logs for "source: database") — confirmed "19 cameras from database"
- [x] Verify all streams start correctly (no regressions) — regression fixed (commit 53df957)
- [ ] Test stream type switching: select MJPEG in UI -> backend serves MJPEG
- [ ] Test per-user preferences: user A sets WebRTC, verify backend uses it
- [ ] Test force-sync: `POST /api/cameras/force-sync` resets from JSON
- [ ] Test fallback: stop PostgREST -> verify app still works from JSON
- [ ] 24-hour stability test

**Testing Required (Previous):**
- [ ] Phase 1 stream stability fixes (still untested from Feb 14)
- [ ] Manual restart button: works within 10-15 seconds
- [ ] Monitor for Entrance door RTSP null exceptions

**Cleanup:**
- [ ] Decide on recursive `docs/docs/docs/...` deletions
- [ ] Commit or revert `.gitignore` changes

**Pending (Phase 2 -- Code Quality):**
- [ ] Centralize 30+ hardcoded timeouts to config/timeouts.yaml
- [ ] Centralize hardcoded MediaMTX addresses to config/services.yaml
- [ ] Remove commented-out code from MJPEG service files
- [ ] Fix bare except clauses in talkback_transcoder.py

**Pending (Phase 3 -- Refactoring):**
- [ ] Extract MJPEG handler base class (reduce ~300 lines duplication)
- [ ] Fix circular import architecture
- [ ] Add camera state audit trail (90-day retention)

**Pending (Other):**
- [ ] file_operations_log cleanup: 98M rows (34GB) needs retention/pruning policy
- [ ] VACUUM ANALYZE on recordings table (never been vacuumed)
- [ ] Security: Implement stricter RLS policies (currently permissive)
- [ ] Security: Investigate container encryption (secrets are cleartext in container env)
- [ ] WebRTC HD/SD fallback -- falls back too fast, doesn't retry HD
- [ ] Add snapshot feature in fullscreen mode
- [ ] Investigate segment buffer failures -- pre-alarm recording broken
- [ ] Warm restart sub-service (`restart_warm.sh`)

---

### Post-Compaction #3 Updates (14:00 EST)

**Context compaction occurred at ~13:50 EST.** Previous session was investigating stream loading after DB migration (resolved — network-level camera failures, not code) and working on CLAUDE.md utilities propagation.

19. **`CLAUDE.md`** (updated 14:00 EST)
    - Added RULE 19: Shell Utilities Available to All Instances
    - Covers: `.bash_utils`, `.bash_aliases`, `custom-env.sh`, `.env.colors`, `logger.sh`
    - Sections 19.1-19.5: Files overview, key functions, key aliases, key variables, usage notes

20. **`server:~/0_CLAUDE_IC/CLAUDE.md.standard.md`** (updated ~13:40 EST, pre-compaction)
    - Appended "STANDARD: Shell Utilities Available to All Instances" section
    - Canonical source — all projects should propagate from this

21. **Intercom MSG-074** posted to `server:~/0_CLAUDE_IC/intercom.md`
    - To: ALL instances
    - Subject: CLAUDE.md Standard Updated — New Shell Utilities Section
    - Action: All instances must read updated standard and add utilities to their local CLAUDE.md

---

### Eufy PTZ Bridge Fix (Feb 20, 01:55 EST)

**Root Cause:** Eufy bridge crashing immediately — `eufy-security-server` binary missing from container.

22. **Missing `node_modules/`** — Lost during Feb 15 catastrophic wipe. The bind mount `./:/app` overlays
    the image-built node_modules with the host directory (which had none). Fix: `npm ci --production` on host.
    Binary verified: `node_modules/.bin/eufy-security-server` present.

23. **Double `NVR_` prefix bug in `services/eufy/eufy_bridge.sh`** — Lines 92, 117, 160, 161, 185 used
    `NVR_NVR_EUFY_BRIDGE_USERNAME` instead of `NVR_EUFY_BRIDGE_USERNAME`. Also `NVR_TRUSTED_DEVICE_NAME`
    fixed to `TRUSTED_DEVICE_NAME`. Another Claude instance had namespaced AWS secret keys, causing double prefix.

24. **Intercom MSG-078** — Notified all instances about duplicate namespaced keys in EUFY_CAMERAS AWS secret.

**Node version note:** Host has Node 18, container has Node 20. eufy-security-ws@1.9.3 requires >= 20.
Host npm warnings are cosmetic — packages run fine on container's Node 20.

**Requires:** Container restart (`./start.sh`) to pick up node_modules and fixed bridge script.

---

### Environment Variable Namespace Migration: `NVR_` Prefix (Feb 20, 00:30-02:00 EST)

**Branch:** `env_var_nvr_prefix_FEB_20_2026_a`

**Goal:** Prefix all custom NVR environment variables with `NVR_` to prevent collision with other projects on the same host.

**Scope:** Only custom NVR vars. Framework/external vars unchanged: `PGRST_*`, `FLASK_*`, `POSTGRES_*`, `PYTHONUNBUFFERED`, `TZ`.

**Pre-existing bug fixed:** `config/go2rtc.yaml:47` used `${REOLINK_API_USERNAME}` but actual var was `REOLINK_API_USER` — fixed as `${NVR_REOLINK_API_USER}`.

**Double-prefix bug fixed:** `scripts/update_neolink_config.sh` had `NVR_NVR_REOLINK_*` (from compaction-related re-edit). Fixed to `NVR_REOLINK_*`.

**AWS Secrets Manager** (00:45 EST):
- 47 `NVR_`-prefixed duplicate keys added across 7 secrets
- Old non-prefixed keys retained for rollback

**Files Modified:**

25. **Credential providers** (5 files, 01:00 EST):
    - `services/credentials/reolink_credential_provider.py` — 4 vars
    - `services/credentials/amcrest_credential_provider.py` — 2 vars + dynamic
    - `services/credentials/sv3c_credential_provider.py` — 2 vars
    - `services/credentials/eufy_credential_provider.py` — dynamic + 2 static
    - `services/credentials/unifi_credential_provider.py` — 2 vars

26. **`app.py`** (01:10 EST) — ~15 env var replacements

27. **15 other Python files** (01:15 EST) — services, models, scripts, streaming handlers

28. **`docker-compose.yml`** (01:20 EST) — All custom vars, added missing HUBITAT_*_4 vars

29. **`config/go2rtc.yaml`** (01:25 EST) — All `${VAR}` → `${NVR_VAR}`

30. **`.env`** (01:30 EST) — All custom var definitions

31. **Shell scripts** (01:35 EST) — start.sh, eufy_bridge.sh (root+container), eufy_bridge_login.sh, update_neolink_config.sh

32. **`~/.bash_utils`** (01:55 EST) — `get_cameras_credentials()` 40 validation checks

33. **Intercom MSG-081** posted — notified all instances

**Verification required:** User must run `./start.sh` to test all credentials load correctly.

---

### Camera Rename Feature (Feb 28, 16:15-16:25 EST)

**Branch:** `camera_rename_ui_FEB_28_2026_a`

**Goal:** Allow renaming cameras from the UI settings modal. Updates DB + cameras.json.

**Files Modified:**

34. **`app.py`** (16:18 EST) — New `PUT /api/camera/<serial>/name` endpoint
    - Validates name (non-empty, max 255 chars)
    - Uses `camera_repo.update_camera_setting(serial, 'name', value)` (updates DB + JSON + cache)
    - Logs old name -> new name transition
    - Returns success with serial, new name, previous name

35. **`static/js/forms/recording-settings-form.js`** (16:20 EST) — Camera Info section at top of form
    - New "Camera Info" section with editable name field + Rename button
    - Read-only serial number field below it
    - `handleRename()` method: calls PUT endpoint, updates modal title + stream tile
    - Enter key in name field triggers rename
    - Inline status messages (success/error/no-change)
    - `generateForm()` signature extended with `cameraName` parameter

36. **`static/js/modals/camera-settings-modal.js`** (16:22 EST)
    - Passes `cameraName` to `generateForm()` call

37. **`templates/streams.html`** (16:23 EST)
    - Modal header changed: "Recording Settings" -> "Camera Settings" (with cog icon)

38. **Intercom MSG-150** posted to `office-network` — context for SonicWall lease naming workflow

**Requires:** Container restart (`./start.sh`) for backend changes to take effect.

**TODO (Phase 2):**
- [ ] Rename camera on device itself via vendor API (Baichuan for Reolink, ONVIF for others)
- [ ] "Add a Camera" UI — form + POST endpoint + MediaMTX path setup

---

### Eufy PTZ Self-Healing & Error Reporting (Feb 28, 20:00-21:50 EST)

**Branch:** `env_var_nvr_prefix_FEB_20_2026_a`

**Root Cause:** Eufy PTZ was broken because `eufy-security-server` (Node.js on port 3000) crashed due to P2P session key expiration after 2 days uptime (decryption errors, `ERROR_COMMAND_TIMEOUT`). The shell wrapper (`eufy_bridge.sh`) stayed alive, so `is_running()` returned True. Result: PTZ moves hit `ConnectionRefusedError` on port 3000 but returned only "Movement failed" with no explanation.

**Two Problems Fixed:**
1. **No self-healing** — bridge crashes required manual `./start.sh`
2. **No useful error info** — users saw "Movement failed" with no details

**Files Modified:**

39. **`services/eufy/eufy_bridge.py`** (20:30-21:30 EST) — **Major rewrite** (+437/-104 lines)
    - Added `BridgeCrashedError` and `BridgeAuthRequiredError` exception classes
    - `is_running()` now checks actual TCP port 3000 via socket, not just `process.poll()`
    - New methods: `_mark_bridge_dead(reason)`, `_check_port_alive()`, `restart()`, `get_status()`, `_run_bridge_command()`
    - `restart()` is thread-safe with Lock and 30s cooldown between attempts
    - `_monitor_bridge()` continues monitoring after ready event (previously broke out of loop)
    - `move_camera()` returns `(success, message)` tuple with auto-restart-on-crash
    - `_run_bridge_command()` DRY helper for preset methods with same pattern
    - `goto_preset()`, `save_preset()`, `delete_preset()` all return `(success, message)` tuples

40. **`app.py`** (20:45 EST) — 4 Eufy PTZ endpoints updated
    - PTZ move, goto_preset, save_preset, delete_preset all use `success, message = ...` tuple unpacking
    - Returns 503 with detailed error message + `bridge_status` dict on failure
    - Removed pre-check `if not eufy_bridge.is_running()` — methods handle this internally now
    - **Note:** These changes were included in prior commit 38596be

41. **`services/eufy/eufy_bridge.sh`** (21:00 EST) — Auto-restart loop
    - Replaced single `execute_bridge` call with `while true` restart loop
    - Exponential backoff: 10s → 20s → 40s → 80s → 120s (capped at MAX_RESTART_DELAY=120)
    - Re-populates config on each restart (picks up credential changes)
    - Kills stale processes before each restart attempt

**All syntax checks passed:** `py_compile` for Python, `bash -n` for shell script.

**Requires:** Container restart (`./start.sh`) to load new Python code.

**Testing plan after restart:**
- [ ] PTZ move on Eufy camera — should work or show detailed error
- [ ] Kill eufy-security-server manually → verify auto-restart kicks in
- [ ] Verify 503 errors include meaningful message in UI
- [ ] Check `eufy_bridge.sh` restart loop in docker logs

---

### Performance Refactoring (Feb 28, 19:00-20:00 EST)

**Branch:** `camera_rename_ui_FEB_28_2026_a` (continued)

**Problem:** App unresponsive — investigation revealed ~1,930 HTTP requests/minute from a single browser tab, static files served through Flask, 15-second blocking request handlers, and no push-based state updates.

**Files Modified:**

42. **`nginx/nginx.conf`** (19:05 EST) — Phase 1: Static file serving via nginx
    - Added `/static/` location block in HTTPS server (serves from nginx, not Flask)
    - Updated HTTP `/static/` block to also serve directly
    - `expires 1h`, `Cache-Control: public, immutable`, `sendfile on`

43. **`docker-compose.yml`** (19:05 EST) — Phase 1
    - Added `./static:/usr/share/nginx/static:ro` volume to nvr-edge container

44. **`app.py`** (19:10-19:40 EST) — Phases 2-4
    - New `GET /api/camera/states` batch endpoint (returns all camera states in one call)
    - Wired `camera_state_tracker.set_socketio(socketio)` at init
    - `api_stream_restart()` now non-blocking: returns immediately, notifies via SocketIO
    - Added `_postgrest_session = requests.Session()` for PostgREST connection pooling
    - Replaced 20+ `requests.get/post/patch/delete(POSTGREST_URL/...)` with `_postgrest_session.xxx()`

45. **`services/camera_state_tracker.py`** (19:15 EST) — Phase 2B
    - Added `set_socketio()` method
    - `_trigger_callbacks()` now emits `camera_state_changed` event on `/stream_events` namespace

46. **`static/js/streaming/camera-state-monitor.js`** (19:20 EST) — Phase 2: Complete rewrite
    - Replaced N+1 per-camera polling with SocketIO push + 30-second batch fallback
    - 120 req/min → 2 req/min max

47. **`static/js/streaming/snapshot-stream.js`** (19:35 EST) — Phase 5A: Visibility gating
    - Added IntersectionObserver to pause polling for off-screen cameras
    - 100px rootMargin for pre-fetching as cameras scroll into view
    - ~1200 req/min → ~300 req/min (only visible cameras poll)

**Intercom MSG-149** posted — warned ALL instances about NVR refactoring in progress.

**Impact Summary:**
- Static files: 71+ per page load removed from Gunicorn → served by nginx
- Camera state: 120 req/min → 2 req/min (SocketIO push + batch fallback)
- Stream restart: 15-second block → instant response + async notification
- PostgREST: ~50-70ms TCP overhead eliminated per interaction cycle
- Snapshots: ~1200 req/min → ~300 req/min (visibility gating)
- **Total: ~1930 req/min → ~700 req/min estimated**

**Requires:** Container restart (`./start.sh`) + nginx container rebuild for static volume mount.

---

### Context Compaction #4 (Feb 28, 22:23 EST)

**Context compaction occurred.** All 5 phases of performance refactoring were complete and verified (syntax checks passed). Committed as `f1e8b31` on branch `camera_rename_ui_FEB_28_2026_a` and pushed to remote.

**Status at compaction:** Implementation complete, awaiting container restart for testing.

---

### Session: March 07, 2026 (00:00-00:45 EST)

#### DB Tables Missing — presence + file_operations_log (00:10 EST)

**Root cause:** After power loss, DB data directory survived intact so `init-db.sql` was NOT re-run on restart. Two tables added in later migrations were absent from the live DB despite being in the consolidated `init-db.sql`.

**Missing tables:** `presence`, `file_operations_log`

**Fix:** Applied both table definitions directly via `docker exec nvr-postgres psql`. Sent `SIGUSR1` to `nvr-postgrest` to reload schema cache. Flood of `relation "public.presence" does not exist` errors in postgres logs immediately stopped.

52. **Live DB hotfix** (00:12 EST) — no file change, direct psql
    - `CREATE TABLE presence` + indexes + RLS policy + GRANT + seed rows (Elfege, Jessica)
    - `CREATE TABLE file_operations_log` + 5 indexes + RLS policy + GRANT
    - `docker kill --signal=SIGUSR1 nvr-postgrest` — schema reload confirmed

#### Favicon Update — the_eye.png (00:20 EST)

53. **`static/images/the_eye.png`** (00:20 EST) — Cropped in-place
    - Original: 1536×1024 landscape
    - Saturation-based eye detection (threshold=50) → core region: 546×540
    - Cropped to 654×654 square (10% padding, centered on eye centroid at ~x=753, y=479)
    - Saved back to same path

54. **7 HTML templates** (00:22 EST) — `sed -i` batch replace
    - `images/mobius.nvr.png` → `images/the_eye.png` in all favicon/apple-touch-icon links
    - Files: `streams.html`, `reloading.html`, `cert_install.html`, `error.html`, `login.html`, `change_password.html`, `eufy_auth.html`
    - **Requires container restart** for Flask to serve updated templates

#### Camera Network Outage Assessment (00:30 EST)

**User reported:** "many cameras offline signal lost"

**Assessment:** Network-level hardware failure, not NVR code.

| IP | Camera | Status |
|---|---|---|
| 192.168.10.121 | MEBO | DOWN |
| 192.168.10.181 | HALLWAY | DOWN |
| 192.168.10.183 | Hot Tub | DOWN |
| 192.168.10.184 | Terrace Shed | DOWN |
| 192.168.10.186 | Living_REOLINK | DOWN |
| 192.168.10.187 | Former CAM STAIRS | DOWN |

13 of 19 cameras remain reachable and operational. The 6 down cameras span `.121` and `.181-.187` range — likely same PoE switch or AP group. NVR will auto-recover when network comes back.

#### Commits Pushed This Session

| Commit | Description |
|---|---|
| `e95fd79` | Rename 0_NVR→0_MOBIUS.NVR in docs + favicon update |
| `c5f8d10` | Post-power-loss internet/AWS wait guard in start.sh |
| `37852ed` | Engineering architecture doc → March 6, 2026 (Levels 10-13) |
| `04cc343` | CLAUDE.md Rule 17-20 + handoff + package-lock |

**Intercom:** MSG-178 posted to ALL.

---

### Recovery Session: March 04, 2026 (01:15-01:45 EST)

**Context:** VSCode crashed mid-session, lost conversation. Recovered from git state + plan file + handoff.

**Repo housekeeping:**

48. **Git remote URL fix** (01:20 EST)
    - Push URL was `github.com/elfege/NVR.git` (old name), fetch was `MOBIUS.NVR.git`
    - Fixed: `git remote set-url --push origin .../MOBIUS.NVR.git`
    - GitHub was 301-redirecting, but this was a ticking time bomb

49. **`.gitignore` fix + neolink untrack** (01:25 EST) — commit `5b30e14`
    - Line 31 was `# neolink` (commented out) → changed to `neolink/target/`
    - `git rm --cached -r neolink/target/` — removed 1454 build artifacts from tracking

50. **Project rename completion** (01:30 EST) — commit `87d8030`
    - 29 files had `0_NVR` → `0_MOBIUS.NVR` path/comment updates sitting unstaged since directory rename
    - These were never committed after the pre-rename snapshot (`7e2bf18`)
    - Also added `LICENSE` file (MIT)

51. **Container restart** (01:31 EST)
    - Ran `./start.sh` with `AWS_PROFILE=personal` — non-interactive
    - All containers up: unified-nvr (healthy), postgres, postgrest, packager, edge, go2rtc, neolink
    - Both HTTP and HTTPS health checks passed
    - 30 MediaMTX paths created, 28 ready (2 offline cameras: T821451024233587, XCPTP369388MNVTG)
    - Eufy bridge port 3000 alive

**Issues 1-3 verification:**

| Issue | Code in container | Infrastructure | Status |
|-------|------------------|----------------|--------|
| 1 (fullscreen exit) | Yes | N/A | Already passed in prior session |
| 2 (Eufy preset retry) | Yes — retry loop, keepalive, BridgeCrashedError all loaded | Bridge alive, port 3000 open | Needs UI test: save preset on T8419P0024110C6A |
| 3 (MJPEG→WebRTC switch) | Yes — create-path endpoint registered | All cameras have MediaMTX paths | Needs UI test: switch camera to MJPEG first, then back |

**Note:** No cameras currently configured as MJPEG, so Issue 3's path-creation flow can't be auto-tested. The code path and endpoint are in place but need manual UI testing.

**User permissions granted for this session (lost in crash, re-stated):**
- Rule 9 override: Claude can restart containers
- Can run `./start.sh`, `deploy.sh --no-cache --prune`, or manual docker compose with AWS creds
- Full bash execution without prompting
- No PowerShell/Chrome remote testing (may have caused the VSCode crash)

---

## Next Session TODO

**Immediate (User must test in UI):**
- [ ] Test Issue 2: Save/overwrite preset on Eufy KITCHEN OFFICE (T8419P0024110C6A)
- [ ] Test Issue 3: Switch a camera to MJPEG, then switch back to WebRTC — verify create-path works
- [ ] Test performance refactoring: verify static Cache-Control, SocketIO push, non-blocking restart
- [ ] Test Eufy PTZ self-healing (kill bridge, verify auto-restart)
- [ ] Test camera rename feature

**Issues 4-7 (from `docs/ISSUES_March_4_2026.md`):**
- [ ] Issue 4: Mobile UI overhaul (iPhone/iPad) — needs planning session
- [ ] Issue 5: Older iPad stream stalling — likely related to Issues 6+7
- [ ] Issue 6: Frozen stream diagnostics — per-camera logging, virtual terminal, DB log tables
- [ ] Issue 7: Device capability assessment — fingerprinting, adaptive quality, ML learning

**Testing Required (DB Migration):**
- [ ] Test stream type switching: select MJPEG in UI -> backend serves MJPEG
- [ ] Test per-user preferences: user A sets WebRTC, verify backend uses it
- [ ] Test force-sync: `POST /api/cameras/force-sync` resets from JSON
- [ ] Test fallback: stop PostgREST -> verify app still works from JSON
- [ ] 24-hour stability test

**Testing Required (Previous):**
- [ ] Phase 1 stream stability fixes (still untested from Feb 14)
- [ ] Manual restart button: works within 10-15 seconds
- [ ] Monitor for Entrance door RTSP null exceptions

**Cleanup:**
- [x] Fix `.gitignore` neolink entry (done 01:25 EST)
- [x] Commit project rename files (done 01:30 EST)
- [x] Fix git remote push URL (done 01:20 EST)
- [ ] Decide on recursive `docs/docs/docs/...` deletions

**Pending (Phase 2 -- Code Quality):**
- [ ] Centralize 30+ hardcoded timeouts to config/timeouts.yaml
- [ ] Centralize hardcoded MediaMTX addresses to config/services.yaml
- [ ] Remove commented-out code from MJPEG service files
- [ ] Fix bare except clauses in talkback_transcoder.py

**Pending (Phase 3 -- Refactoring):**
- [ ] Extract MJPEG handler base class (reduce ~300 lines duplication)
- [ ] Fix circular import architecture
- [ ] Add camera state audit trail (90-day retention)

**Pending (Other):**
- [ ] file_operations_log cleanup: 98M rows (34GB) needs retention/pruning policy
- [ ] VACUUM ANALYZE on recordings table (never been vacuumed)
- [ ] Security: Implement stricter RLS policies (currently permissive)
- [ ] Security: Investigate container encryption (secrets are cleartext in container env)
- [ ] WebRTC HD/SD fallback -- falls back too fast, doesn't retry HD
- [ ] Add snapshot feature in fullscreen mode
- [ ] Investigate segment buffer failures -- pre-alarm recording broken
- [ ] Warm restart sub-service (`restart_warm.sh`)
- [ ] Rename camera on device itself via vendor API (Baichuan for Reolink, ONVIF for others)
- [ ] "Add a Camera" UI — form + POST endpoint + MediaMTX path setup
