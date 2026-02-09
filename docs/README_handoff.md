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

*Last updated: February 9, 2026 00:16 EST*

Branch: `stream_type_preferences_FEB_08_2026_a`

**Previous Session (Feb 7-8):** Full user auth system, user management, camera access control, PostgREST resilience. See `docs/README_project_history.md` Feb 7-8 section.

Always read `CLAUDE.md` — RULE 9 was updated: `docker compose restart` is now ALLOWED (not `./start.sh` due to AWS MFA hang).

---

## Current Session: February 8, 2026 (19:35 EST) → February 9, 2026 (00:16 EST)

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

### What's Still Needed

- **Backend restart required** — `docker compose restart nvr` needed for Python API endpoints
- **End-to-end testing** — verify stream type switching works live

### Key Files

- `app.py` lines ~1468-1540: stream preference endpoints
- `static/js/streaming/stream.js`: `loadUserStreamPreferences()` (~line 1813), `switchStreamType()` (~line 1867)
- Plan file: `/home/elfege/.claude/plans/curious-rolling-meerkat.md`

---

## TODO List

**Done this session:**

- [x] Backend API endpoints (GET/PUT stream preferences)
- [x] Frontend preference loader (`loadUserStreamPreferences`)
- [x] Live stream switch method (`switchStreamType`)
- [x] Stream type selector UI in controls panel
- [x] JS event handlers for stream type buttons
- [x] Fullscreen button fix (enters fullscreen from expanded modal)

**Needs testing:**

- [ ] Restart NVR container (`docker compose restart nvr`) for backend changes
- [ ] End-to-end test: switch stream type, verify live switch works
- [ ] Verify preferences persist across page reload
- [ ] Verify per-user isolation (different users see their own preferences)

**Pending:**

- [ ] file_operations_log cleanup: 98M rows (34GB) needs retention/pruning policy
- [ ] VACUUM ANALYZE on recordings table (never been vacuumed)
- [ ] Security: Implement stricter RLS policies (currently permissive)
- [ ] WebRTC HD/SD fallback - falls back too fast, doesn't retry HD
- [ ] Add snapshot feature in fullscreen mode

---
