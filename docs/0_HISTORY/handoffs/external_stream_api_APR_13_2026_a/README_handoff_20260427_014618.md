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

*Last updated: April 27, 2026 01:45 EDT*

**Branch:** `external_stream_api_APR_13_2026_a`

**Previous session:** See April 24 section below (Amcrest Lobby fix + /light view + APK + credentials race).

---

## Current Session: April 27, 2026 (~00:00–01:45 EDT) — Timeline Playback UX + perf overhaul

### Problems reported
- Timeline modal closed on the slightest backdrop click — "any wrong mouse move I'm out and have to reload"
- No state memoization — every reopen reset to last-24h default
- Timeline scrolling didn't exist; couldn't browse past the explicit From/To window
- Loading the timeline took tens of seconds for any non-trivial window
- After my first round of fixes, memoization corrupted the user's `To:` time (AM/PM flip)

### Backend perf fix — `services/recording/timeline_service.py`
`get_timeline_segments()` was paying two filesystem costs **per recording row**:
1. `os.path.exists(file_path)` — N stat syscalls (extra slow on `/mnt/THE_BIG_DRIVE`)
2. `_check_audio_track(file_path)` — **`subprocess.run(['ffprobe', ...], timeout=10)` per row**

For a 24h window with ~100 recordings this stacked to 30-60s of blocking I/O before the API responded. The frontend never reads `has_audio` for timeline render anyway.

Fix: drop both checks in the list path. `has_audio=False` returned by default. Resolved on-demand when the user opens a segment for preview (`get_segment_by_id` retains the ffprobe call). Same fix applied in the filesystem-fallback scan path. Missing files now surface as a playback error at click-time instead of being filtered out invisibly.

### Frontend UX overhaul — `static/js/modals/timeline-playback-modal.js`

Added gestures:
| Gesture | Action |
|---|---|
| Left-click + drag | Range selection (existing — for export) |
| **Right-click + drag** | Pan timeline. Cursor: `grab` / `grabbing`. |
| **Mouse wheel** | Zoom centered on cursor (timestamp under cursor stays fixed) |
| **Shift + wheel** | Pan horizontally |
| **Trackpad two-finger horizontal swipe** | Pan (auto-detected via `deltaX`) |
| Esc | Close modal |
| Backdrop click | **NO-OP** — modal stays open. User insisted twice. |

Added auto-extend via `_extendRangeIfPannedPast()`: when the visible window crosses past the currently-loaded `timeRange`, it pads outward by half the visible window, refetches segments via the new `_fetchSegments()` helper, and adjusts `panOffset` so the user's view stays visually pinned.

Added localStorage memoization (`nvr_timeline_state_v1` keyed per-camera): saves date / start time / end time / zoom level on every load, every zoom-button click, and 200ms after the last wheel event settles. Restores on `show()` if state is < 7 days old; otherwise falls back to last-24h default.

### Bug introduced and fixed in same session
First memoization version had `_extendRangeIfPannedPast()` writing back to the date/time input fields. When the extended window crossed midnight, `Date.toTimeString().slice(0,5)` returned the next-day wall-clock time (e.g. 18:15 → 03:15), and persistence saved the wrong values. User reported `To: 06:15 PM` reopening as `05:15 AM`.

Fix: `_extendRangeIfPannedPast` no longer touches input fields and no longer calls `_savePersistedState`. The internal `timeRange` (loaded data window) and the user's input-field selection are now distinct — extension grows the loaded window invisibly; the inputs stay frozen at the user's last explicit choice. Persistence captures only what the user explicitly set.

One-time cleanup needed for already-corrupted state:
```js
localStorage.removeItem('nvr_timeline_state_v1')
```

### Off-repo side work
- Helped diagnose DBeaver SSH-tunnel auth failure on office: keys are in modern OpenSSH format, JSch-based DBeaver versions can't parse them. Created PEM-format copy at `office:~/.ssh/id_rsa_dellserver_pem` via `ssh-keygen -p -m PEM`.

### Files modified this session (in this repo)
- `services/recording/timeline_service.py` — drop per-row stat + ffprobe in list path
- `static/js/modals/timeline-playback-modal.js` — backdrop close removed, memoization added, pan/wheel gestures, auto-extend, AM/PM bug fix

### Backend change → container restart needed
`./start.sh` (no flag — preserves the live go2rtc config; the on-demand regen logic from the April 24 session is intact).

---

## Previous Session: April 24, 2026 (~00:00–22:00 EDT) — Amcrest Lobby fix + /light view + APK + credentials race

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
