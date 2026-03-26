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

*Last updated: March 20, 2026 16:05 EDT*

**Branch:** `app_modularization_MAR_20_2026_a` (just created)

**Previous Branch:** `repo_security_licensing_MAR_20_2026_b` → merged into `main`

---

## Current Session: March 20, 2026 (16:00 - ??:?? EDT)

### app.py Modularization — Flask Blueprints

**What was done:**

1. **`start.sh` + `_start.sh`** (16:02 EDT)
   - `ip route get 1.1.1.1` result only exported if non-empty
   - If detection fails, docker compose falls back to `.env` value

2. **Merged `repo_security_licensing_MAR_20_2026_b` → `main`**, created `app_modularization_MAR_20_2026_a`

3. **app.py modularized** — 8,616 lines → ~1,276 lines. All routes moved to `routes/`:

   | File | Contents |
   |---|---|
   | `routes/__init__.py` | Package marker |
   | `routes/shared.py` | Service registry (singletons, PostgREST session, `set_services()`) |
   | `routes/helpers.py` | Shared helpers: `csrf_exempt`, session, device, camera access, env vars, trusted-network |
   | `routes/auth.py` | login, logout, change-password, users CRUD, devices, preferences |
   | `routes/camera.py` | Camera display, order, credentials, streaming config, FLV, reboot |
   | `routes/config.py` | UI pages (/, /streams, /reloading), health, license, trusted-network, status, camera list |
   | `routes/eufy.py` | Eufy auth flow (`/api/eufy-auth/*`), Amcrest MJPEG (`/api/amcrest/*/stream/mjpeg`) |
   | `routes/power.py` | Hubitat power, UniFi POE |
   | `routes/presence.py` | Household presence tracking |
   | `routes/ptz.py` | PTZ move, presets, latency, reversal |
   | `routes/recording.py` | Recording, timeline (`/api/timeline/*`), file browser, exports, preview-merge |
   | `routes/storage.py` | Storage stats, migration, cleanup, motion detection routes |
   | `routes/streaming.py` | Stream lifecycle, HLS, MJPEG, WebSocket namespaces `/mjpeg` + `/stream_events` |
   | `routes/talkback.py` | Talkback WebSocket `/talkback` + capabilities route |

4. **Commit:** `024a99a` on branch `app_modularization_MAR_20_2026_a`

---

## ⚠️ CRITICAL: NOT YET DEPLOYED — DO NOT MARK COMPLETE

### Known Issue: SocketIO decorator import-time race

`routes/streaming.py` and `routes/talkback.py` use:
```python
@shared.socketio.on('connect', namespace='/mjpeg')
```

These decorators execute at **module import time**, but `shared.socketio` is `None` at import time — `set_services()` runs AFTER blueprints are registered. This will crash on startup.

**Fix needed (next session):**

- Option A: Register SocketIO handlers via `socketio.on(...)` calls inside a `register_socketio_handlers(socketio)` function called from app.py after `set_services()`
- Option B: Move SocketIO namespaces back into app.py (simpler, less modular)
- Option C: Use `socketio.on_namespace()` with Namespace classes (cleanest but most work)

**Recommended:** Option A. In streaming.py and talkback.py, replace decorator-style handlers with a function `def init_socketio(sio): sio.on('connect', handler, namespace='/mjpeg')` etc., called from app.py.

---

## Next Session TODO

- [ ] Fix SocketIO import-time crash in `routes/streaming.py` + `routes/talkback.py`
- [ ] Run `python3 -c "import app"` inside container to check for other import errors
- [ ] Deploy with `./start.sh` and verify all endpoints work
- [ ] If deploy fails: create `app_modularization_MAR_20_2026_b` and fix
- [ ] When confirmed working: merge to main

---

## Previous Session: March 20, 2026 (00:00 - 16:00 EDT)

### Repository Security Overhaul — Two-Repo Model + Git-Crypt + License System

**Motivation:** Former employer (Mindhop) may clone and use NVR codebase commercially. BSL 1.1 license alone insufficient without enforcement. User decided on multi-layered IP protection.

**What was done:**

1. **Repo migration:**
   - `MOBIUS.NVR` renamed to `MOBIUS.NVR-dev` (private) — full history preserved as authorship proof
   - Fresh `MOBIUS.NVR` created (public) — encrypted from commit #1 via git-crypt
   - 152 source files encrypted (.py, .js, .html, .sql, Dockerfile, docker-compose.yml, requirements.txt)
   - Plaintext: LICENSE (BSL 1.1), README.md, _deploy.sh, _start.sh, .gitattributes

2. **Git-crypt setup:**
   - Symmetric key generated, backed up to AWS Secrets Manager (`nvr-git-crypt-key`)
   - Local key file shredded after backup
   - `.gitattributes` defines encryption patterns

3. **Dual remotes configured locally:**
   - `origin` → `elfege/MOBIUS.NVR-dev` (private, daily dev)
   - `public` → `elfege/MOBIUS.NVR` (public, encrypted storefront)

4. **Generic scripts created (tracked, plaintext):**
   - `_deploy.sh` — no personal env dependencies, flag-driven, calls `_start.sh`
   - `_start.sh` — ENV mode only, no AWS, portable for anyone with Docker
   - `deploy.sh` / `start.sh` added to `.gitignore` (personal, untracked)

5. **Phone-home system scaffolded:**
   - `scripts/phone_home.sh` — heartbeat client (SHA-256 hardware fingerprint)
   - `infrastructure/lambda/phone_home/` — Lambda + DynamoDB deployment scripts
   - `entrypoint.sh` modified for periodic 24h heartbeat

6. **License validation system created (not yet deployed):**
   - `infrastructure/lambda/license/validator.py` — validates keys, binds hardware on first activation
   - `infrastructure/lambda/license/issuer.py` — creates keys (admin-protected)
   - `infrastructure/lambda/license/deploy_license.sh` — AWS CLI deployment
   - `services/license_service.py` — NVR client-side: 7-day demo mode, hardware-locked, offline grace

7. **Public repo policies:**
   - Auto-tag GitHub Action (semantic versioning on PR merge)
   - Branch protection: PRs only to `main`

8. **Intercom posted:** MSG-197 (to office-sync: register new repos), MSG-198 (to ALL: FYI)

**Note:** app.py is 8,401 lines (not 300K — that was byte count misread). Modularization needed but separate scope.

**Files created/modified:**
- `.gitattributes` (new)
- `.gitignore` (modified — added deploy.sh, start.sh)
- `_deploy.sh` (new)
- `_start.sh` (new)
- `scripts/phone_home.sh` (new)
- `infrastructure/lambda/phone_home/lambda_function.py` (new)
- `infrastructure/lambda/phone_home/deploy_lambda.sh` (new)
- `infrastructure/lambda/phone_home/trust_policy.json` (new)
- `infrastructure/lambda/license/validator.py` (new)
- `infrastructure/lambda/license/issuer.py` (new)
- `infrastructure/lambda/license/deploy_license.sh` (new)
- `services/license_service.py` (new)
- `entrypoint.sh` (modified — phone-home periodic heartbeat)
- `.github/workflows/auto-tag.yml` (new, public repo only)

---

## Previous Session: March 10, 2026 (14:00 - 23:22 EDT)

### Security Hardening + DB-Based Credentials + Idempotent Startup (22:00 - 23:22 EDT)

**Motivation:** Hardcoded Flask SECRET_KEY and camera credentials in docker-compose.yml — security concern + bad for public portfolio.

**Phase 1 complete: DB-based credential storage + idempotent startup scripts.**

#### Security Fixes

1. **`app.py`** (22:05 EDT)
   - SECRET_KEY now reads from `NVR_SECRET_KEY` env var, fallback to `os.urandom(32).hex()`
   - SESSION_COOKIE_SECURE now defaults to True unless FLASK_ENV=development
   - Added credential migration hook at startup (env vars -> DB, idempotent)
   - Added credential CRUD API endpoints: GET/PUT/DELETE `/api/camera/<serial>/credentials`

#### DB-Based Camera Credentials

2. **`psql/migrations/017_camera_credentials.sql`** + **`psql/init-db.sql`** (22:10 EDT)
   - New `camera_credentials` table with AES-256-GCM encrypted columns
   - Supports per-camera (keyed by serial) and service-level (keyed by vendor) credentials
   - Full RLS + permissions matching existing pattern

3. **`services/credentials/credential_db_service.py`** (22:15 EDT) — NEW
   - CRUD operations via PostgREST for camera_credentials table
   - AES-256-GCM encryption using pycryptodomex (key derived from NVR_SECRET_KEY via SHA-256)
   - Thread-safe in-memory cache with lazy loading
   - PostgREST upsert for store, cache invalidation on delete

4. **`services/credentials/migrate_env_to_db.py`** (22:20 EDT) — NEW
   - One-time migration: scans env vars, stores in DB (idempotent)
   - Auto-discovers Eufy per-camera vars via regex pattern matching
   - Called at app startup; migrated 15 credentials successfully in testing

5. **Credential providers refactored** (22:25 EDT)
   - `eufy_credential_provider.py` — DB first, env var fallback
   - `reolink_credential_provider.py` — DB first, env var fallback
   - `unifi_credential_provider.py` — DB first, env var fallback
   - `amcrest_credential_provider.py` — DB first, env var fallback (removed debug prints)
   - `sv3c_credential_provider.py` — DB first, env var fallback (removed hardcoded admin/01234567)

6. **Direct env var reads updated** (22:30 EDT)
   - `services/power/unifi_poe_service.py` — tries DB 'unifi_controller' service key
   - `services/unifi_protect_service.py` — tries DB 'unifi_protect' service key
   - `services/reolink_mjpeg_capture_service.py` — tries DB 'reolink_api' service key

#### Docker-Compose Cleanup

7. **`docker-compose.yml`** (22:35 EDT)
   - Removed entire `x-all-camera-credentials` anchor (40+ lines of per-camera env vars)
   - Removed `<<: *all-camera-credentials` merge from nvr service
   - Added `env_file: secrets.env` (required: false) for go2rtc, packager, nvr containers
   - Parameterized all hardcoded volume paths: `NVR_RECORDING_PATH`, `NVR_STORAGE_PATH`, `NVR_POSTGRES_DATA`, `NVR_ALTERNATE_RECORDING_STORAGE`
   - Parameterized network name: `NVR_NETWORK_NAME`
   - Parameterized edge ports: `NVR_EDGE_HTTP_PORT`, `NVR_EDGE_HTTPS_PORT`

#### Idempotent Startup Scripts

8. **`start.sh`** (22:40 EDT) — Full rewrite
   - `ENV_BASED_CONFIG=true/false` toggle in .env
   - Portable color/utility setup (works without ~/.env.colors, ~/logger.sh, ~/.bash_utils)
   - AWS mode: unchanged behavior (pull from Secrets Manager)
   - ENV mode: read from secrets.env, auto-generate POSTGRES_PASSWORD if missing
   - Generates `secrets.env` from AWS cache for docker compose env_file
   - Auto-generates NVR_SECRET_KEY if not set
   - Validates project root, creates config from example if missing

9. **`deploy.sh`** (22:45 EDT) — Same idempotency pattern
   - Portable color/utility setup
   - Loads secrets from secrets.env or bash_utils

10. **`.env`** (22:50 EDT)
    - Added `ENV_BASED_CONFIG=false` toggle
    - Added storage path variables (NVR_RECORDING_PATH, etc.)
    - Added network/port variables

11. **`secrets.env.example`** (22:50 EDT) — NEW
    - Template for non-AWS users
    - Documents all credential env vars with comments
    - Camera credentials marked optional (can add via UI instead)

12. **`.gitignore`** — Added `secrets.env`

13. **`requirements.txt`** — Added `pycryptodomex`

#### Testing Results

- Migration: 15 credentials migrated from env vars to DB
- All 5 credential providers verified reading from DB
- Encryption roundtrip: AES-256-GCM encrypt/decrypt verified
- PostgREST: table visible, CRUD operations confirmed
- app.py syntax validation: passed

#### Pending (Phase 2+)

- [ ] Camera settings modal: add credential username/password fields (API endpoints ready)
- [ ] Container restart required (`./start.sh`) to load new Python code
- [ ] Full deploy test with `./deploy.sh --prune --no-cache` to verify idempotent startup
- [ ] go2rtc config generation from DB (currently still uses env var interpolation)

---

### Two issues: Eufy preset 503 + MJPEG→WebRTC stream switching

#### Issue A: MJPEG → WebRTC/HLS stream type switching (backend fix applied)

**Root cause:** Two independent MJPEG bailout points in the backend read the camera's *stored* `stream_type` instead of the *requested* target type. When a camera is configured as MJPEG, `start_stream()` and `/api/stream/start/` both return early without starting FFmpeg, even when the user requests WebRTC.

**Files Modified:**

1. **`streaming/stream_manager.py`** (14:15 EDT)
   - Added `protocol_override` parameter to `start_stream()` and `_start_stream()`
   - When set, bypasses MJPEG bailout and uses override for all protocol branching
   - Three protocol resolution points updated (lines 278, 443, 468)

2. **`app.py`** (14:20 EDT)
   - `/api/mediamtx/create-path/`: accepts `target_type` from request body, passes as `protocol_override`
   - `/api/stream/start/`: passes resolved effective stream type as `protocol_override`

3. **`static/js/streaming/stream.js`** — **DEFERRED** (go2rtc branch conflict per MSG-183)
   - Still needed: move preference save before Phase 0, pass `target_type` to create-path
   - Without this frontend fix, backend fixes help but the full flow isn't complete

#### Issue B: Eufy bridge persistent 503 (self-recovery race condition fixed)

**Root cause:** Race condition between old and new bridge generations. When `restart()` calls `stop()` then `start()`, the OLD `_monitor_bridge` thread is still reading stdout from the old process. When it detects EOF, it calls `_mark_bridge_dead()` which sets `_running = False` — **corrupting the NEW bridge instance's state**. The new keepalive loop exits, and all subsequent commands get 503. Additionally, zombie shell wrapper processes (`eufy_bridge.sh`) accumulated across restarts.

**Files Modified:**

1. **`services/eufy/eufy_bridge.py`** (14:30 EDT)
   - Added `_generation` counter to `__init__`, incremented on each `start()`
   - `_monitor_bridge(generation)`: checks generation before calling `_mark_bridge_dead()`, exits silently if stale
   - `_keepalive_loop(generation)`: checks generation in sleep loop and before restart, retires after successful proactive restart
   - `start()`: kills orphan `eufy_bridge.sh` wrappers (not just node process), logs generation number
   - Thread names include generation for debugging (e.g., `eufy-monitor-g3`)

2. **`services/eufy/eufy_bridge_watchdog.py`** (14:32 EDT)
   - Fixed log message: said "15 seconds" but sleep was 120s

---

## Previous Session: March 04, 2026 (00:14 - 01:00 EST)

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

### Side Task:External API + TILES Integration (Feb 20, 09:00-09:45 EST)

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

## Session: March 09, 2026 (21:30–22:09 EDT)

### 55. HLS frozen stream recovery — `static/js/streaming/hls-stream.js` (21:35 EDT)

**Problem:** Streams with lag > 30s (e.g. Cat Feeders showing 338.9s badge) would stay frozen forever.

**Fix:** Extended `_attachLatencyMeter()` with 2-stage auto-recovery:
- **Stage 1 (10s of lag > 30s):** `forceRefreshStream()` — HLS.js destroy+reinit, zero backend involvement. 120s cooldown prevents loops.
- **Stage 2 (60s after Stage 1, if still frozen):** `GET /api/camera/state/<id>` — logs backend pipeline state. Frontend never forces backend restart; StreamWatchdog owns that.
- `cameraId` parameter added to `_attachLatencyMeter(hls, videoEl, cameraId)` and call site updated.
- Recovery state (`_lagStartMs`, `_frozenUiRestarted`, `_frozenLastRestartMs`, `_frozenPostRestartTimer`) lives on `videoEl` to survive HLS.js reinits.

### 56. Fullscreen exit — grid streams not resuming — `static/js/streaming/stream.js` (21:45 EDT)

**Problem:** Exiting fullscreen via button/ESC left all grid streams paused. Root cause: **two bugs**:
1. `forceExitFullscreen()` cleared `pausedStreams = []` before deferred `closeFullscreen()` ran
2. `closeFullscreen()` returned early (`.css-fullscreen` already removed) → resume loop never reached

**Fix:**
- Extracted resume loop into new `_resumePausedStreams()` async method
- `closeFullscreen()` early-return path now calls `await this._resumePausedStreams()` before returning
- Removed `this.pausedStreams = []` from `forceExitFullscreen()`

### 57. Cat Feeders PTZ (95270000YPTKLLD6) — partial fix — `services/ptz/baichuan_ptz_handler.py` (22:00 EDT)

**Root cause 1 — FIXED:** `reolink_aio 0.19.1` defaults to HTTPS port 443 (closed on E1). Added `bc_only=True` to both `Host()` calls in the handler.

**Root cause 2 — UNSOLVED:** PtzCtrl (Baichuan cmd_id 18) returns `error code 1, response code 400` from the camera.

**Investigation findings:**
- Camera reachable (192.168.10.123 pings, port 9000 open)
- Baichuan connects OK, `ptz: True`, `ptzType: 3`, full capabilities confirmed
- `bc_only=True` does NOT populate `_channels` (requires HTTP API) — channel must be manually injected
- Even with `h._channels = [0]` injected AND `bc_only=True`, PtzCtrl still returns 400
- Both XML formats (with/without `<channelId>`) tested — both rejected

**Next steps for PTZ:**
- Try `admin` user credentials instead of `api-user`
- Check E1 firmware version vs when PTZ last worked
- Try downgrading `reolink_aio` to previous version
- Separate issue: T8416P cameras returning 503 on ONVIF presets after restart — likely timing (auto-resolves)

**All changes uncommitted.** Container restart already run by user.

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

### Stream Reload Overlay + Favicon (Mar 07, 01:30-02:06 EST)

**Branch:** `stream_switch_mjpeg_fix_MAR_04_2026_a` (unchanged)

52. **`static/images/mobius.nvr.png`** (01:30 EST)
    - New favicon image added (was untracked)

53. **7 HTML templates** (01:32 EST)
    - Favicon refs: `images/mobius.png` → `images/mobius.nvr.png`

54. **`static/css/components/stream-reload-overlay.css`** (01:45 EST) — NEW FILE
    - Per-tile reload animation: scaled-down standby ring motif, `position: absolute`
    - 5 rings + pulsing eye + ambient particles + step log area
    - `.waking` state: rings accelerate, eye turns green on success
    - Log lines staggered 700ms apart, 0.55s fade+slide animation

55. **`templates/streams.html`** (01:46 EST)
    - CSS `<link>` added; `.stream-reload-overlay` HTML injected in every `.stream-item`

56. **`static/js/streaming/stream.js`** (01:50 EST)
    - `_showStreamReloadOverlay`, `_hideStreamReloadOverlay`, `_logStreamReloadStep` added
    - `_logStreamReloadStep` queues messages 700ms apart via `_sroNextLogAt` timestamp
    - Both reload button handlers updated with step-by-step overlay messages
    - Success: green waking transition; error: red message stays 3-4s then dismisses

**Status:** Untested — needs `./start.sh` then UI test of both reload buttons.

---

---

### Session: March 09, 2026 (branch: stream_switch_mjpeg_fix_MAR_04_2026_a)

#### 1. Golden Ratio Tiles (aspect-ratio 13/8)

**Files Modified:**

- `static/css/components/stream-item.css` — `aspect-ratio: 16/9` → `13/8` (Fibonacci approximation of φ)
- `static/js/streaming/stream.js` — `setupLayout()` updated to match

#### 2. Video Fit Mode (per-camera + per-user default)

- New DB columns: `cameras.video_fit_mode`, `user_preferences.default_video_fit`
- Exposed in settings UI (user default) and per-camera recording settings modal
- Migration: `psql/migrations/012_add_video_fit_settings.sql` (new)

**Files Modified:** `app.py`, `static/js/settings/settings-ui.js`, `static/js/forms/recording-settings-form.js`, `static/js/modals/camera-settings-modal.js`

#### 3. iOS-style Tile Rearrange (TileArrangeManager)

- Long-press (500ms) on any tile enters arrange mode — all tiles jiggle
- SortableJS drag-and-drop reorder; amber "Done" pill saves order via `PUT /api/my-camera-order`
- Arrange mode blocked when: any tile is fullscreen or in expanded modal; target is button/PTZ/controls
- Entering fullscreen or expanded modal calls `tileArrangeManager.exitArrangeMode(false)` automatically
- Navbar `#arrange-tiles-btn` wired to `tileArrangeManager.toggle()`

**New Files:** `static/css/components/tile-arrange.css`, `static/js/streaming/tile-arrange-manager.js`

**Files Modified:** `static/js/streaming/stream.js` (import + integration), `templates/streams.html`

#### 4. HD Button in Expanded Modal

- Toggle HD/SD from within the expanded modal view
- State synced with `hdCameras` localStorage + DB

**Files Modified:** `static/js/streaming/stream.js`

#### 5. Pin Button in Expanded Modal

- Persists a camera to auto-expand on every page reload
- Backdrop click and internal click are blocked while camera is pinned
- New DB column: `user_preferences.pinned_camera`; Migration: `psql/migrations/013_add_pinned_camera.sql` (new)

**Files Modified:** `static/js/streaming/stream.js`, `app.py`

#### 6. Floating Pinned Window (HD + Pin = floating window)

- When both HD and pin are active simultaneously, `.stream-item` detaches from the CSS grid and becomes a `position:fixed` draggable + resizable floating window
- Background streams blur + pause while window is at home position; drag away → blur lifts
- Multiple pinned windows supported
- Positions persist to localStorage + DB (`user_preferences.pinned_windows JSONB`); Migration: `psql/migrations/014_add_pinned_windows.sql` (new)

**New Files:** `static/css/components/pinned-window.css`, `static/js/streaming/pinned-window-manager.js`

**Files Modified:** `static/js/streaming/stream.js` (import + PinnedWindowManager integration)

#### 7. Pin Click-Inside-Modal Fix

- Added pin guard to `.stream-item` click handler to prevent clicking inside a pinned modal from collapsing it

**Files Modified:** `static/js/streaming/stream.js`

#### 8. DB Migrations 012–014 + init-db.sql Consolidation

- Migrations 012 (video_fit), 013 (pinned_camera), 014 (pinned_windows) created
- `psql/init-db.sql` updated to include all three — remains the source of truth for fresh deployments

**New Files:** `psql/migrations/012_add_video_fit_settings.sql`, `psql/migrations/013_add_pinned_camera.sql`, `psql/migrations/014_add_pinned_windows.sql`

**Files Modified:** `psql/init-db.sql`

#### 9. Auto-Run Migrations on start.sh

- `start.sh` now runs all `psql/migrations/*.sql` files in sorted order on every startup
- `IF NOT EXISTS` guards make the process idempotent; per-file failure reporting

**Files Modified:** `start.sh`

#### Key Files Changed This Session (Summary)

| File | Change |
|------|--------|
| `static/css/components/stream-item.css` | Aspect-ratio 16/9 → 13/8 |
| `static/js/streaming/stream.js` | Layout, HD/pin buttons, arrange mode guards, PinnedWindowManager import |
| `static/js/streaming/tile-arrange-manager.js` | NEW — iOS-style long-press rearrange |
| `static/css/components/tile-arrange.css` | NEW — jiggle animation + arrange UI |
| `static/css/components/pinned-window.css` | NEW — floating window styles |
| `static/js/streaming/pinned-window-manager.js` | NEW — floating/draggable/resizable pinned window |
| `static/css/components/fullscreen.css` | Minor guard updates |
| `static/js/settings/settings-ui.js` | Default video fit mode preference |
| `static/js/forms/recording-settings-form.js` | Per-camera video fit mode |
| `static/js/modals/camera-settings-modal.js` | Passes cameraName to generateForm |
| `app.py` | New endpoints for pin, video_fit, migrations |
| `templates/streams.html` | TileArrangeManager button, CSS links |
| `psql/migrations/012_add_video_fit_settings.sql` | NEW |
| `psql/migrations/013_add_pinned_camera.sql` | NEW |
| `psql/migrations/014_add_pinned_windows.sql` | NEW |
| `psql/init-db.sql` | Consolidated migrations 012–014 |
| `start.sh` | Auto-run all migrations on startup |

#### 10. Arrange Mode Guard Fix (21:30 EST)

**Problem:** Long-press on held PTZ direction buttons triggered arrange mode after 500ms. Also, arrange mode persisted when entering fullscreen or expanded modal.

**Fix (tile-arrange-manager.js):**
- touchstart + mousedown: bail if any `.css-fullscreen` or `.expanded` tile exists
- touchstart + mousedown: bail if target is inside `button, a, input, select, .ptz-controls, .stream-controls, .stream-more-menu`
- touchstart + mousedown: bail if `.stream-ptz-toggle-btn.ptz-active` exists anywhere
- New `deactivate()` public method (wraps `exitArrangeMode(false)`)

**Fix (stream.js):**
- Added `import { tileArrangeManager } from './tile-arrange-manager.js'`
- `expandCamera()`: calls `tileArrangeManager.exitArrangeMode(false)` before opening modal
- Fullscreen activation: calls `tileArrangeManager.exitArrangeMode(false)` after adding `css-fullscreen`

**Files Modified:** `static/js/streaming/tile-arrange-manager.js`, `static/js/streaming/stream.js`

#### Pending Before Merge to Main

- [ ] User testing of all new features
- [ ] Run `./start.sh` (auto-applies migrations 012–014 via new auto-run logic)
- [ ] Hard refresh browser after restart to pick up new JS/CSS

---

## March 09-10, 2026 — All Tasks (Branch: `ptz_baichuan_E1_MAR_09_2026_a`)

### Task A: PTZ Baichuan E1 Fix (22:00-23:41 EST) — this chat

**Context:** Continued from compacted session. Previous session fixed HLS frozen recovery, fullscreen grid resume, and identified E1 PTZ failure.

#### A1. E1 PTZ Credential Fix (22:10 EST)
- **Root cause:** `api-user` lacked PTZ permission on E1 camera (192.168.10.123)
- Camera returned HTTP 400 (error code 1) on PtzCtrl cmd_id 18 with `api-user`
- Tested `admin` credentials — PTZ works
- User recreated `api-user` on camera with PTZ permission — confirmed working
- Reverted code back to `use_api_credentials=True`
- **Commits:** `8ff5c89` (admin fix), `3cb046a` (revert to api-user)

#### A2. Baichuan PTZ Event Loop Rewrite (22:30 EST)
- **Problem:** `_run_async()` used `asyncio.run()` per call (new event loop each time). Cached `Host` objects held sockets bound to previous loops — commands hung until 30s timeout — minutes-long delayed movements
- **Fix:** Dedicated background event loop (`_ensure_loop()`) in daemon thread. All PTZ coroutines dispatched via `run_coroutine_threadsafe()`. Connection cache now valid (same loop = same sockets).
- **Per-camera threading.Lock** prevents flooding camera with simultaneous connections

#### A3. Cache Validity Fix (23:15 EST)
- **Bug:** `_token` is always `None` for `bc_only=True` connections (no HTTP API = no token). Cache validity check always failed — reconnect on every command (~3s each)
- **Fix:** Use `host.session_active` or `host.baichuan._logged_in` instead
- **Result:** Cold connect ~1.1s, cached commands ~100ms

#### A4. Stop Command Lock Strategy (23:25 EST)
- **Bug:** Non-blocking lock on `move_camera()` caused stop commands to be silently dropped when move was still executing — camera kept moving indefinitely
- **Fix:** Stop commands block-wait (up to 5s). Move commands remain non-blocking (skip if busy).

#### A5. Baichuan Preset Save (23:00 EST)
- **Discovery:** `SetPtzPreset` not supported via Baichuan in reolink_aio, but raw XML with `cmd_id=19` and `<command>setPos</command>` works
- Added `save_preset()` method to `BaichuanPTZHandler`
- Wired into Flask route `api_ptz_set_preset()` — Baichuan path added before ONVIF fallback
- Verified: preset save + retrieve working on E1

**Files Modified (Task A):**
| File | Change |
|------|--------|
| `services/ptz/baichuan_ptz_handler.py` | Full rewrite: dedicated event loop, connection caching, stop-priority lock, preset save via raw Baichuan XML |
| `app.py` | Added Baichuan preset save route (before ONVIF fallback) |
| `docs/README_handoff.md` | Updated branch name, session notes |

---

### Task B: Trusted Devices + Connected Clients (22:15-23:40 EST) — side chat

**Status:** Code complete, NOT committed, NOT tested (PostgREST was down — see blocker)

#### B1. Feature: Trusted Device Management
Admin can view all connected clients and mark devices as "trusted" — trusted devices auto-login via cookie, never see the login page again.

**Architecture:**
- Device token (UUID) in localStorage + httpOnly cookie
- Heartbeat piggybacks on existing ConnectionMonitor health checks
- `@app.before_request` auto-login for trusted devices
- Admin modal: online/offline status, IP, user-agent, user, trust toggle, rename, delete

**Files Created:** `psql/migrations/015_trusted_devices.sql`, `static/js/modals/device-management-modal.js`, `static/css/components/device-management.css`

**Files Modified:** `app.py` (before_request hook, login device registration, 6 API routes), `static/js/connection-monitor.js` (`_sendDeviceHeartbeat()`), `templates/streams.html` (CSS link, nav button, modal HTML, script tag)

#### B2. Blocker: PostgREST Restart Loop (pre-existing)
`~/.cache/nvr_secrets.env` has 4 different `POSTGRES_PASSWORD` values; last one is SmartHome's, not NVR's. Temporary fix: `ALTER ROLE nvr_api` to match. Needs proper secrets cache dedup.

**Intercom:** MSG-181 + MSG-182 posted (PENDING).

---

### Task C: HLS + Startup Fixes (23:00-23:41 EST) — side chat

#### C1. HLS Signal-Lost Overlay Stuck After Recovery — `static/js/streaming/hls-stream.js` (23:05 EST)
**Problem:** "Signal Lost" overlay stayed visible after stream recovered. `_firstFragReceived` one-shot flag never reset.
**Fix:** Reset `videoElement._firstFragReceived = false` in both `forceRefreshStream()` and HLS 404 retry loop.

#### C2. Parallel AWS Secrets Pull + LAN Cache — `start.sh` (23:15 EST)
**Problem:** 14 sequential AWS calls = ~15s. **Fix:** Parallel subshells + LAN cache at `~/.cache/nvr_secrets.env`.

| Scenario | Wall-clock |
|----------|------------|
| LAN cache hit | ~0s |
| Parallel pull | ~4s |
| Before (serial) | ~15s |

#### C3. Nginx 502 + MSG-181 Login — Investigation Only
Both transient startup race conditions. No code changes. MSG-181 RESOLVED. MSG-182 posted.

**Uncommitted (Task C):** `static/js/streaming/hls-stream.js`, `start.sh`

---

### go2rtc Infrastructure Gaps Fixed (post-compaction continuation ~16:00 EDT)

**Context:** After context compaction, continued addressing user's 4 concerns about go2rtc implementation gaps.

**Concerns addressed:**

1. **go2rtc.yaml git tracking** — Added `!config/go2rtc.yaml` exception to `.gitignore`. File contains NO credentials (only `${ENV_VAR}` references), safe to track.

2. **`update_neolink_config.sh` still functional** — Confirmed. Called by `start.sh` at line 164-166. Reads cameras.json, filters for NEOLINK cameras, generates neolink.toml. No changes needed.

3. **go2rtc.yaml dynamic updates** — Created `scripts/update_go2rtc_config.sh`:
   - Reads cameras.json, finds all NEOLINK/GO2RTC/NEOLINK_LL_HLS cameras
   - Preserves static audio backchannel section (above `# VIDEO RELAY STREAMS` marker)
   - Regenerates video relay section from cameras.json
   - Hooked into `start.sh` (runs after neolink config update)
   - Idempotent — running twice produces identical output

4. **Database workflow verified** — Full chain works:
   - Migration 016: adds GO2RTC to CHECK constraint
   - `VALID_STREAM_TYPES` in app.py includes GO2RTC
   - `PUT /api/camera/<serial>/stream-preference` saves via PostgREST upsert
   - `get_effective_stream_type()` loads user pref with camera default fallback

**Files Modified:**
- `.gitignore` — added `!config/go2rtc.yaml` exception
- `scripts/update_go2rtc_config.sh` — NEW, auto-generates go2rtc video relay streams
- `start.sh` — hooked in update_go2rtc_config.sh after neolink config update
- `config/go2rtc.yaml` — regenerated by script (identical content, now git-tracked)

---

## Next Session TODO

**Immediate (go2rtc — current work):**
- [ ] Run `./start.sh` to pick up all go2rtc changes
- [ ] Test go2rtc button on Cat Feeders camera — verify latency improvement
- [ ] Verify database migration 016 applied correctly
- [ ] Commit go2rtc infrastructure gap fixes

**Immediate (PTZ — Task A):**
- [ ] Run `./start.sh` to pick up PTZ code changes (event loop, cache fix, stop-priority lock)
- [ ] Test E1 PTZ responsiveness — should be ~100ms per cached command
- [ ] Test preset save via UI on E1

**Immediate (Trusted Devices — Task B):**
- [ ] Fix `~/.cache/nvr_secrets.env` — deduplicate POSTGRES_PASSWORD entries
- [ ] Run `./start.sh` (auto-applies migration 015)
- [ ] Test login, verify device token cookie is set
- [ ] Test admin "Manage Devices" modal
- [ ] Test trust toggle — mark trusted, logout, verify auto-login
- [ ] Commit all trusted device files once verified

**Immediate (User must test — March 09 features):**
- [ ] Hard refresh browser after restart
- [ ] Test golden ratio tile layout (13/8 aspect ratio)
- [ ] Test video fit mode toggle (per-user default + per-camera override)
- [ ] Test tile rearrange: long-press → jiggle → drag → Done pill
- [ ] Test HD button + pin button in expanded modal
- [ ] Test floating pinned window (pin + HD simultaneously)

**Immediate (Prior sessions — still untested):**
- [ ] Test Eufy preset save/overwrite (T8419P0024110C6A)
- [ ] Test MJPEG→WebRTC stream switching
- [ ] Test Eufy PTZ self-healing (kill bridge, verify auto-restart)
- [ ] Test camera rename, stream reload overlay

**Issues 4-7 (from `docs/ISSUES_March_4_2026.md`):**
- [ ] Issue 4: Mobile UI overhaul (iPhone/iPad)
- [ ] Issue 5: Older iPad stream stalling
- [ ] Issue 6: Frozen stream diagnostics
- [ ] Issue 7: Device capability assessment

**Pending (Code Quality / Refactoring / Other):**
- [ ] Preset delete via Baichuan (`delPos`) — camera ignores command
- [ ] Centralize hardcoded timeouts + MediaMTX addresses
- [ ] Extract MJPEG handler base class
- [ ] Fix circular import architecture
- [ ] file_operations_log cleanup (98M rows / 34GB)
- [ ] VACUUM ANALYZE on recordings table
- [ ] WebRTC HD/SD fallback too aggressive
- [ ] Snapshot in fullscreen mode
- [ ] Segment buffer / pre-alarm recording
- [ ] Warm restart sub-service (`restart_warm.sh`)
- [ ] "Add a Camera" UI
