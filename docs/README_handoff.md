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

*Last updated: April 27, 2026 01:46 EDT — handoff cleared after merge to main*

**Branch:** `main`

**Previous session contents** archived under `docs/history/handoffs/external_stream_api_APR_13_2026_a/README_handoff_20260427_014618.md`. Long-term record in `docs/README_project_history.md` (read its last ~300 lines for the April 13 → April 27 arc: cert install UI, /light view + APK, Amcrest Lobby credential race fix, go2rtc config regen, timeline playback UX overhaul, listing perf).

---

## Current Session

*No active session. Next session will start a new feature branch from main.*

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
