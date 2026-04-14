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

*Last updated: April 13, 2026 17:10 EDT*

**Branch:** `external_stream_api_APR_13_2026_a`

**Previous session:** See `docs/README_project_history.md` last ~200 lines for April 9 repo consolidation + March 31 E1 camera fix + grid layout modes.

---

## Current Session: April 13, 2026 (16:55–17:10 EDT) — Certificate Install UI + HTTPS Redirect

### Certificate Install UI Entry Points

Added 4 entry points to the `/install-cert` page (route + template already existed):

1. **Navbar shield icon** — `streams.html` navbar actions, before settings gear
2. **Slide-in nav menu** — "Install Certificate" item in Settings section
3. **Login page link** — "Seeing security warnings?" text link below login form
4. **Auto-banner** — dismissable banner on streams + login pages (localStorage persistence)

**Banner positioning fix:** Changed from `container.insertBefore(banner, container.firstChild)` to `container.parentNode.insertBefore(banner, container.nextSibling)` — banner was hidden behind fixed navbar when prepended to body.

**Files modified:**
- `templates/streams.html` — navbar icon, nav menu item, cert-install.css link, banner script
- `templates/login.html` — cert link, banner script
- `static/js/cert-install.js` — fixed banner insertion to go after container, not inside
- `static/css/components/cert-install.css` — already existed, now linked from streams.html

### MOBIUS.PROXY HTTPS Redirect

**Problem:** `http://mobius.nvr/streams` was served over plain HTTP — proxy had `listen 80` + `listen 443 ssl` in same server block with no redirect.

**Fix:** Split into two server blocks in `/home/elfege/0_MOBIUS.PROXY/nginx/nginx.conf`:
- **Port 80** — `return 301 https://$host$request_uri` for all paths EXCEPT `/install-cert`, `/api/cert/`, `/static/` (proxied to NVR HTTP port 8081 for chicken-and-egg cert download)
- **Port 443** — unchanged HTTPS proxy to `192.168.10.20:8444`

### MOBIUS.PROXY Cert Re-signed with NVR CA

**Problem:** Proxy cert was self-signed (`issuer=CN = mobius.proxy`). Installing NVR CA didn't help because proxy's cert wasn't signed by that CA. Two different trust chains = two warnings.

**Fix:** Generated new proxy cert signed by NVR CA:
- Same SANs: `mobius.jira, mobius.nvr, mobius.smarthome, mobius.tiles, mobius.backup, mobius.alexa, mobius.hub, mobius.proxy`
- Old certs backed up as `.bak` in `/home/elfege/0_MOBIUS.PROXY/certs/`
- Now one CA install trusts the entire chain: browser → proxy → NVR

### Other Changes

- `packager/mediamtx.yml` — added missing publisher paths for AMC, T8419, 95270001NT3KNA67, 95270000YPTKLLD6
- `start.sh` — moved `get_cameras_credentials` block after `docker compose down`
- Added `certs/` to `.gitignore` (contains private keys)

### Cert Banner Fix (continued)

Banner was rendering behind fixed navbar (z-index issue). Fixed: `position: fixed; top: 44px; z-index: 1100` — floating overlay, centered, glassmorphism background. No longer pushes content.

### Commits

- `c4ee334` — feat: add certificate install UI entry points + fix banner positioning
- `443a5ff` — fix: add missing mediamtx paths + move credential loading after docker compose down
- `38ecdd1` — fix: cert banner as fixed overlay + add lightweight stream viewer

---

## TODO

- [ ] **Health monitor toggle** — per-camera on/off in UI (admin-only, password required)
- [ ] **Eufy doorbell streaming** — needs Home Base 3 pairing (physical access required)
- [ ] **0_VIDEO_TRAFFIC_ANALYZER** — install deps, test cert gen, first MITM capture
- [ ] go2rtc audio — user says it's working; verify and close
- [ ] Eufy PTZ on go2rtc hub — preset saving broken on Eufy cameras
- [ ] Confirm modal on hub/stream-type change
- [ ] Page load speed (parallel stream starts in UI + backend)
- [ ] "Add Camera" UI (DB is sole source, no cameras.json)
- [ ] Hot-swap hub without restart (MediaMTX path add/delete API + go2rtc stream PATCH)
- [ ] Unhide cameras UI toggle
- [ ] DTLS cleanup (intercom MSG-005)
- [ ] Grid attached mode default (intercom MSG-006/007)
- [ ] Camera name styling (intercom MSG-008)
- [ ] E1 PTZ latency improvement (currently 4-5s per command)
