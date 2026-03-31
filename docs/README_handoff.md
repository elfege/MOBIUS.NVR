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

*Last updated: March 31, 2026 17:27 EDT*

**Branch:** `main` (committed directly — small change)

**Previous session:** See `docs/README_project_history.md` for March 29–31 marathon session covering: Settings OOP refactor, cache elimination, unified streaming config generation, go2rtc WebRTC fix, root cleanup, Eufy cloud resilience, restart watcher, and bridge exponential backoff.

---

## Current Session: March 31, 2026 (16:30–17:14 EDT) — Fix E1 Camera (Reolink Cat Feeders)

**Branch:** `fix_e1_camera_March_31_2026_a`

### Problem

E1 camera (serial: `95270000YPTKLLD6`) not streaming. go2rtc shows it registered but 0 online. Error: `500 - streams: wrong response on DESCRIBE`.

### Root Cause

`generate_streaming_configs.py` enforced **exclusive hub assignment** — each camera in exactly ONE hub config. E1 had `streaming_hub = go2rtc` with `go2rtc_source = rtsp://neolink:8554/95270000YPTKLLD6/mainStream`, so it went into go2rtc.yaml. But neolink.toml had `cameras = []` — neolink didn't know about the camera, so go2rtc's RTSP DESCRIBE to neolink failed.

**Key insight:** Neolink is a **protocol bridge** (Baichuan → RTSP), not a browser-facing hub. The exclusive assignment rule applies to browser-facing hubs (go2rtc vs mediamtx), but neolink must always be configured as a bridge dependency when any hub's source references it.

### Fix Applied

1. **`scripts/generate_streaming_configs.py`** (commit `409ebb4`):
   - After splitting cameras into exclusive lists, scan all cameras for `go2rtc_source` containing "neolink" — add those as **bridge dependencies** to neolink.toml
   - Neolink credential resolution: prefer go2rtc creds `(serial, 'go2rtc')` over per-camera creds `(serial, 'camera')` — Baichuan protocol requires admin login, not api-user
   - Updated neolink.toml header comment to document bridge dependency concept

2. **DB fixes** (manual by user):
   - `neolink.port`: `8554` → `9000` (8554 is neolink's RTSP output, 9000 is camera's Baichuan port)
   - `go2rtc_source`: confirmed correct at `rtsp://neolink:8554/95270000YPTKLLD6/mainStream`
   - go2rtc per-camera credentials set to `admin` (not api-user)

### Result

- E1 streaming via go2rtc WebRTC — **working**
- PTZ via Baichuan — **working** (slow, ~4-5s per command, known limitation)
- Codec initially mismatched (JPEG vs H264) — resolved after correct neolink port

### Architecture Clarified

```
E1 Camera (:9000 Baichuan)
    ↓
Neolink (bridge: Baichuan → RTSP on :8554)
    ↓
go2rtc (pulls rtsp://neolink:8554/..., serves WebRTC to browser)
    ↓
Browser (WebRTC via go2rtc WHEP API)
```

Neolink is NOT a browser-facing hub — it's infrastructure. The `streaming_hub` field determines browser delivery (go2rtc or mediamtx). Neolink bridge dependencies are auto-detected by the config generator.

---

## Session 2: March 31, 2026 (15:00–17:27 EDT) — Multi-project: VTA + Doorbell + Grid

### 1. New Project: 0_VIDEO_TRAFFIC_ANALYZER (dellserver)

Created `~/0_VIDEO_TRAFFIC_ANALYZER/` — traffic proxy analyzer for reverse-engineering the Eufy Video Doorbell E340 streaming protocol (no Homebase). Full scaffold:
- MITM proxy addon (mitmproxy), traffic classifier (STUN/TUTK/DTLS/RTP), cert manager with iOS .mobileconfig
- Network scanner (nmap + scapy passive sniff), Eufy-specific protocol analyzer
- Flask web dashboard, CLI entry point (`run.py`)
- Canonical registrations: `.env.host_project_relations`, intercom registry, CLAUDE.md registry
- Shell aliases added to `dellserver:~/.bash_aliases`: `vta`, `codevta`, `startvta`, `vtaproxy`, etc.
- Claude Code memory seeded at `dellserver:~/.claude/projects/-home-elfege-0-VIDEO-TRAFFIC-ANALYZER/memory/`
- Anamnesis episode ingested: `vta_project_creation_20260329`

### 2. Eufy Doorbell E340 (T821451024233587) — DB Fixes

DB updated directly (no cameras.json sync):
- `stream_type`: `LL_HLS` → `GO2RTC`
- `hidden`: `true` → `false`
- `go2rtc_source`: `eufy://...@192.168.10.185` → `eufy://...@T821451024233587` (go2rtc eufy:// needs serial, not IP)
- Removed duplicate static `entrance_door` entry from `config/go2rtc.yaml` template
- Both credential rows exist: `eufy_bridge` (service) + `T821451024233587` (go2rtc)

### 3. Grid Last-Row Stretch (commit `9891d5a` on main)

Added `_stretchLastRow()` to `camera-selector-controller.js`. Distributes integer column spans across last-row items so no empty cells. e.g., 5 cols / 2 items → span 3 + span 2.

**User feedback:** Stretch works horizontally but row height stays equal — wants ALL freed space (both X and Y) redistributed across all tiles. This is a bin-packing problem that CSS Grid can't solve natively. Options discussed:
- Absolute positioning with calculated x/y/w/h (complex)
- Different column count to minimize waste
- Accept current approach (standard NVR pattern)
- **Decision: deferred — user said "handoff for now"**

### 4. Restart Modal Fix

`camera-settings-form.js` `_showRestartRequiredModal()` "Restart Now" button updated to match navbar restart flow: confirm → save return URL → POST `/api/admin/restart` → redirect `/reloading`. **User reverted this file** (CSS changes too) — only the JS grid stretch survived.

---

## TODO

- [ ] **Grid space optimization** — user wants freed space from empty last-row cells redistributed to ALL tiles (both width and height), not just last-row width stretch. Bin-packing / absolute positioning approach needed.
- [x] **Eufy doorbell with go2rtc** — DB fields fixed, needs `./start.sh` to regenerate go2rtc.yaml and test
- [ ] **0_VIDEO_TRAFFIC_ANALYZER** — install deps, test cert gen, first MITM capture (see project's own handoff)
- [ ] go2rtc audio (AAC→Opus transcoding for WebRTC — video works, no audio track)
- [ ] Eufy PTZ on go2rtc hub (only works on mediamtx currently)
- [ ] Confirm modal on hub/stream-type change
- [ ] Page load speed (parallel stream starts in UI + backend)
- [ ] Settings modal explanations + grey out incompatible options for go2rtc
- [ ] "Add Camera" UI (DB is sole source, no cameras.json)
- [ ] Hot-swap hub without restart (MediaMTX path add/delete API + go2rtc stream PATCH)
- [ ] Unhide cameras UI toggle
- [ ] DTLS toggle with warning modal
- [ ] E1 PTZ latency improvement (currently 4-5s per command)
