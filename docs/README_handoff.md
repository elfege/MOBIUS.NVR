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

*Last updated: February 8, 2026 20:15 EST*

Branch: `stream_type_preferences_FEB_08_2026_a`

**Previous Session (Feb 7-8):** Full user auth system, user management, camera access control, PostgREST resilience. See `docs/README_project_history.md` Feb 7-8 section.

Always read `CLAUDE.md` — RULE 9 was updated: `docker compose restart` is now ALLOWED (not `./start.sh` due to AWS MFA hang).

---

## Current Session: February 8, 2026 (19:35-20:15 EST)

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

### What's Still Needed

- **Per-camera stream type selector UI** — dropdown/button in each camera tile to trigger `switchStreamType()`
  - Should show current type and available options (WEBRTC, HLS, LL_HLS, MJPEG, NEOLINK)
  - Place near HD toggle in camera controls overlay
  - CSS styling for the dropdown

### Key Files

- `app.py` lines ~1468-1540: stream preference endpoints
- `static/js/streaming/stream.js`: `loadUserStreamPreferences()` (~line 1813), `switchStreamType()` (~line 1867)
- Plan file: `/home/elfege/.claude/plans/curious-rolling-meerkat.md`

---

## TODO List

**In Progress:**

- [ ] Per-camera stream type selector UI (dropdown in camera tile)
- [ ] CSS styling for stream type selector

**Pending:**

- [ ] file_operations_log cleanup: 98M rows (34GB) needs retention/pruning policy
- [ ] VACUUM ANALYZE on recordings table (never been vacuumed)
- [ ] Security: Implement stricter RLS policies (currently permissive)
- [ ] WebRTC HD/SD fallback - falls back too fast, doesn't retry HD
- [ ] Add snapshot feature in fullscreen mode

---
