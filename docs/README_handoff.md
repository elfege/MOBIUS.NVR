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

*Last updated: April 24, 2026 21:45 EDT*

**Branch:** `external_stream_api_APR_13_2026_a`

**Previous session:** See April 13 section below for cert install UI + HTTPS redirect + proxy cert re-sign work.

---

## Current Session: April 24, 2026 (~00:00–22:00 EDT) — Amcrest Lobby fix + /light view + APK + credentials race

### Amcrest Lobby stream dead — root cause chain

Three compounding bugs. All fixed.

**1. `/dev/shm/nvr-go2rtc/` owned by `root:root` after boot.** On reboot, tmpfs is wiped. Docker's `restart: unless-stopped` on the `nvr-go2rtc` service starts the container before `start.sh` has a chance to prep the bind-mount target — so `dockerd` (root) auto-creates the dir. Later, `scripts/generate_streaming_configs.py` runs as `elfege` and hits `PermissionError` writing `go2rtc.yaml`. Timestamp proof: boot 23:37:45, dir created 23:40:35.

**2. `start.sh` swallowed the generator failure** — called `venv/bin/python3 scripts/generate_streaming_configs.py` with no exit-code check. Post-reboot, the failure was silent for days. `/dev/shm/nvr-go2rtc/` stayed empty, go2rtc container had no config for any of its 5 cameras.

**3. `pull_aws_secrets` race in `~/.bash_utils`.** When called with `--temp=` (as `get_cameras_credentials` does), the function backgrounded its AWS work and returned immediately instead of `wait`-ing. Outer `wait "${pids[@]}"` saw exited wrapper processes and moved on before AWS calls finished → `secrets.env` / temp file sourced empty → env-var path for creds broken. Independent of root cause 1 but overlapped with it in diagnosis.

### Fixes (this repo)

- **`start.sh` (early, ~line 152)** — unconditional `mkdir -p /dev/shm/nvr-go2rtc` + ownership check/fix every run (`sudo chown -R "$USER:$USER" /dev/shm/nvr-go2rtc` if stale).
- **`start.sh` (~line 253)** — config regen now gated. Triggers: `--regenerate-configs` / `-r` / `--reset` flag, or `NVR_FROM_DEPLOY=1` env var, or missing `go2rtc.yaml`. Generator failure now aborts with red error. Otherwise live config is preserved (regen is expensive + invalidates tmpfs).
- **`deploy.sh:133`** — now calls `NVR_FROM_DEPLOY=1 ./start.sh --regenerate-configs` (belt + suspenders).
- **`scripts/generate_streaming_configs.py:382`** — header comment updated to reflect on-demand semantics.
- **`packager/mediamtx.yml`** — regenerated output reflecting cameras that moved to go2rtc hub (AMC, T821451, T8419P, 95270001NT3KNA67, 95270000YPTKLLD6 removed from mediamtx paths).

### Fixes (outside this repo)

- **`~/.bash_utils` `pull_aws_secrets`** — lines ~8238-8250. Now always `wait $pid` on inner AWS subshell regardless of `caller_will_source` flag. Only the sourcing is conditional.
- **`~/.bash_utils` `get_cameras_credentials`** — added full `--help`/`-h` block, cleaned up leftover debug echoes, prints temp file path at end (success + failure paths).

### /light endpoint + WebView APK (earlier in same session, pre-existing in initial git status)

- **`templates/streams_light.html`** — snapshot-based minimal viewer. 2x2/2x3/3x3/4x4 grid cycle + pagination + swipe. Double-tap → fullscreen modal with swipe-to-navigate across all cameras. Hourly auto-reload. localStorage memoization: grid size, last-fullscreen camera, stretch/fit toggle.
- **`routes/config.py`** — `/streams` now auto-redirects mobile UAs (silk/, android, iphone, ipad, mobile, fire) to `/light` unless `?full=1` is explicitly passed.
- **`templates/streams.html` + `static/images/apple-touch-icon.png`** — proper 180x180 Apple touch icon so iOS home-screen installs show the eye logo.
- **WebView APK** built on dellserver using OpenJDK 17 + Android SDK cmdline-tools; installed on Fire tablet via ADB. Wraps `https://192.168.10.20:8444/light` in a chrome-less Android activity.

### Off-project side work

- **`server:~/0_HEALTH/`** initialized (canonical CLAUDE.md from `0_CLAUDE_IC/CLAUDE.md.standard.md` + 8 project-specific HEALTH-N rules). No git. Instance ID: `server-health`. Registry updated.
- **`~/.bash_aliases`** — added `health`/`codehealth`; converted non-goto project aliases (`scripts`/`codescripts`/`academics`/`arduino`/`codearduino`) to goto/gotocode pattern.
- **Intercom MSG-123** posted to `office-network` re: future secret-rotation framework, scoping + reusable pieces in codebase.

---

## Previous Session: April 13, 2026 (16:55–17:10 EDT) — Certificate Install UI + HTTPS Redirect

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
