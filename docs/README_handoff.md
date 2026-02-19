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

*Last updated: February 19, 2026 10:45 EST*

**Branch:** `db_camera_config_migration_FEB_19_2026_a`

**Previous Session (Feb 16):** iPad WebRTC fix, database migration proposal (3 options), user chose Option B.

**Previous Branch:** `ipad_grid_force_webrtc_fix_FEB_16_2026_a` (iPad fix + proposal doc)

---

## Current Session: February 19, 2026 (10:00 EST) -> Ongoing

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

## Next Session TODO

**Testing Required (DB Migration):**
- [x] Run SQL migration 011
- [x] Run cameras.json -> DB migration script (19 cameras migrated)
- [ ] Restart containers (user runs `./start.sh`)
- [ ] Verify cameras load from database (check logs for "source: database")
- [ ] Verify all streams start correctly (no regressions)
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
- [ ] WebRTC HD/SD fallback -- falls back too fast, doesn't retry HD
- [ ] Add snapshot feature in fullscreen mode
- [ ] Investigate segment buffer failures -- pre-alarm recording broken
- [ ] Warm restart sub-service (`restart_warm.sh`)

---
