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

*Last updated: April 13, 2026 21:39 EDT*

**Branch:** `external_stream_api_APR_13_2026_a`

**Previous session:** See `docs/README_project_history.md` — April 9 session covered repo consolidation, BSL license, git hooks, security sanitization.

---

## Current Session: April 13, 2026 (15:00–21:39 EDT) — External Stream API + Credential Recovery

### 1. External Stream API for Third-Party Consumers (feat)

Added streaming endpoints to `services/external_api_routes.py` behind existing Bearer token auth:

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/external/stream/<id>` | GET | Discovery — returns active protocol + available endpoint URLs |
| `/api/external/stream/<id>/mjpeg` | GET | Live MJPEG stream (multipart/x-mixed-replace), taps existing frame buffers |
| `/api/external/stream/<id>/whep` | POST | MediaMTX WHEP proxy (WebRTC signaling) |
| `/api/external/stream/<id>/whep/<session>` | PATCH/DELETE | WHEP session management (ICE trickle + teardown) |
| `/api/external/stream/<id>/go2rtc` | POST | go2rtc WebRTC proxy |

- WHEP proxy rewrites MediaMTX Location header to external API path
- CORS preflight handled at blueprint level (`before_request` returns 204)
- CSRF exempted for `external_api_bp`
- `requests` aliased as `http_requests` to avoid Flask collision

**Files:** `services/external_api_routes.py`, `app.py`

### 2. Nginx CORS Fix for WebRTC Proxies (fix, MSG-101)

MediaMTX and go2rtc send their own CORS headers. Nginx was duplicating them → Chrome rejects duplicate `Access-Control-Allow-Origin`.

**Fix:** `proxy_hide_header` strips upstream CORS, `add_header ... always` adds single set. Works on 502 errors too (upstream down).

**File:** `nginx/nginx.conf`

### 3. Credential Recovery Crisis

`camera_credentials` table was empty (0 rows). All 15 credentials lost. Cameras (19), users (3), nvr_settings intact. Root cause unconfirmed — possibly `deploy.sh --prune` run earlier today.

**Fixes applied:**

- **`scripts/seed_credentials.py`** — Extended from 1 credential type to all:
  - 6 service-level: reolink_admin, reolink_api, amcrest, sv3c, unifi_protect, eufy_bridge
  - 9 per-camera Eufy: auto-detected from `NVR_EUFY_CAMERA_{serial}_*` env vars
  - 1 per-camera UniFi: RTSP token alias from `NVR_CAMERA_{serial}_TOKEN_ALIAS`
  - Per-camera go2rtc: copied from service creds for go2rtc hub cameras
  - Uses `ON CONFLICT DO NOTHING` — never overwrites existing (UI-entered) values
  - Encryption key: reads from DB first, env second (AWS may have different key)
  - Table existence guard: skips gracefully before migrations run

- **`start.sh`** — Export `NVR_*` vars after `get_cameras_credentials` loads them so python subprocesses can see them. **NOTE: export loop still not working reliably — needs further investigation.**

- **`services/recording/storage_manager.py`** — Auto-create missing recording directories instead of crashing with FileNotFoundError (503 on camera settings).

### 4. Intercom Messages Handled

- **MSG-101** (office-tiles): Duplicate CORS headers → RESOLVED
- **MSG-102** (office-tiles): CSRF blocking external WHEP → RESOLVED
- **MSG-103** (office-tiles): CORS preflight failing → RESOLVED
- **MSG-104** (office-tiles): CORS still failing on new endpoints → RESOLVED
- **MSG-105** (dellserver-nvr → office-tiles): TILES bug — Location header null
- **MSG-106** (office-tiles): Location rewrite needed → RESOLVED

### Commits (12)

```
a4ca39d fix: auto-create missing recording directories instead of crashing
14c8f0d fix: seed UniFi RTSP token aliases from NVR_CAMERA_{serial}_TOKEN_ALIAS
72be790 fix: seed_credentials uses DO NOTHING — never overwrites existing creds
5c3fcf0 fix: rewrite WHEP Location header + add PATCH/DELETE session proxy
a5a9da7 fix: CORS preflight at blueprint level + PATCH/DELETE/If-Match support
abb51fe fix: skip auth on CORS preflight (OPTIONS) for external API
9542961 fix: exempt external_api_bp from CSRF validation
e8ca40e fix: encryption key priority + env var export for credential seeding
840c69c fix: seed ALL camera credentials from env vars on startup
7e4b7b3 fix: use proxy_hide_header + add_header for CORS on WebRTC proxies
de29e3a fix: remove duplicate CORS headers from nginx WebRTC proxy blocks
f3eecaa feat: add streaming endpoints to external API for third-party consumers
```

### Known Issues

- **SV3C (C6F0SgZ0N0PoL2)** — FFmpeg connects to camera RTSP successfully but publisher dies before MediaMTX receives stream. Pre-existing, not from this session's changes.
- **`start.sh` env var export** — `compgen -v | grep '^NVR_'` + `export` loop doesn't reliably export vars to python subprocesses. Credentials seeded manually this session; need to fix the export mechanism for future starts.
- **Cert install banner** — `cert-install.js` / `showBannerIfNeeded()` exists but was never wired into login.html or streams.html. User made manual edits (uncommitted).

---

## TODO

- [ ] **Fix `start.sh` env var export** — `get_cameras_credentials` loads vars but they don't propagate to `seed_credentials.py`. Core issue: background `pull_aws_secrets` processes can't set parent shell vars; temp file is empty.
- [ ] **Investigate SV3C stream failure** — FFmpeg publishes to MediaMTX but dies immediately. Single-connection camera? Race condition?
- [ ] **Health monitor toggle** — per-camera on/off in UI (admin-only, password required)
- [ ] **Eufy doorbell streaming** — needs Home Base 3 pairing (physical access required)
- [ ] go2rtc audio — verify and close
- [ ] Eufy PTZ on go2rtc hub — preset saving broken
- [ ] Confirm modal on hub/stream-type change
- [ ] Page load speed (parallel stream starts)
- [ ] "Add Camera" UI
- [ ] Hot-swap hub without restart
- [ ] Unhide cameras UI toggle
- [ ] Merge `external_stream_api_APR_13_2026_a` to main when tested
