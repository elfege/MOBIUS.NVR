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

*Last updated: January 19, 2026 02:22 EST*

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Previous Session Context

**See:** `docs/README_project_history.md` for full history

---

## Current Session (Jan 19, 2026 01:00-02:22 EST)

### Branch Info

**Branch:** `audio_restoration_JAN_19_2026_b`
**Previous branch:** `audio_restoration_JAN_19_2026_a` (needs PR to merge to main - branch protection)

### Commits This Session

1. `37188de` - Fix: User-stopped streams being auto-restarted by watchdog
2. `e8a9f8d` - Update handoff: user-stopped tracking, audio probing results
3. `bb73c51` - Fix .gitignore: remove duplicate config/ rule that overrode exceptions
4. `85c84f6` - Switch UniFi camera to LL_HLS for audio support (REVERTED by user)
5. `9082b89` - Switch UniFi audio from AAC passthrough to Opus for WebRTC compatibility
6. `fecf5dd` - Fix: Apply audio codec config to both sub and main streams
7. `73d1c2d` - Enable Opus audio transcoding for all cameras

### Completed Work

#### 1. User-Stopped Stream Tracking (commit `37188de`)

**Problem:** When user clicks stop on a stream, watchdog/health monitor would restart it.

**Solution:** Track user-stopped streams in localStorage:

- `markStreamAsUserStopped(cameraId)` - called when `userInitiated: true`
- `clearUserStoppedStream(cameraId)` - called when stream is started
- `isUserStoppedStream(cameraId)` - checked in `handleBackendRecovery()` and `onRecovery()`

**Files Modified:**

- [static/js/streaming/stream.js](static/js/streaming/stream.js) - User-stopped tracking with localStorage

**WARNING:** Never use `.click()` on stop button programmatically - see comments in code.

#### 2. Audio Codec Probing - COMPLETED

Probed cameras via FFprobe inside container:

| Camera | Type | Native Audio Codec | Sample Rate | Channels |
|--------|------|-------------------|-------------|----------|
| UniFi G5 Flex (68d49398005cf203e400043f) | unifi | **AAC LC** | 16kHz | mono |
| Reolink RLC-423WS (XCPTP369388MNVTG) | reolink | **AAC LC** | 16kHz | mono |

**Key Finding:** Both cameras output AAC natively. WebRTC does NOT support AAC - requires Opus.

#### 3. WebRTC Audio Fix - COMPLETED

**Root Cause:** MediaMTX logs showed:
```
WAR [WebRTC] [session efb8efc9] skipping track 2 (MPEG-4 Audio)
```

WebRTC only supports **Opus** audio codec, NOT AAC/MPEG-4 Audio.

**Solution implemented:**

1. Changed `cameras.json` audio config from `"c:a": "copy"` to `"c:a": "libopus"` with `"b:a": "32k"`
2. Fixed `ffmpeg_params.py` - main stream was hardcoded to `-c:a copy`, now reads from config
3. Enabled Opus transcoding for ALL cameras

**Result:** Audio now works in grid view, modal view, AND fullscreen mode (all WebRTC).

#### 4. .gitignore Fix (commit `bb73c51`)

Removed duplicate `**/config/` rule that was overriding exceptions. `config/cameras.json` now tracked.

---

## Audio Architecture Notes

### Why Opus for WebRTC

- WebRTC specification only supports Opus (and G.711 for telephony)
- MediaMTX explicitly skips AAC tracks for WebRTC sessions
- Opus transcoding is audio-only (video stays passthrough) - minimal CPU impact

### Stream Type vs Audio Codec

| Stream Type | Audio Codec | Notes |
|-------------|-------------|-------|
| WebRTC | Opus required | AAC skipped by MediaMTX |
| LL-HLS | AAC preferred | Opus has limited Safari support |
| MJPEG | N/A | Video only |

Current config uses Opus for all since WebRTC is primary playback method. HLS latency (4+ seconds) unacceptable for fullscreen.

---

## Files Modified This Session

| File | Changes |
|------|---------|
| [static/js/streaming/stream.js](static/js/streaming/stream.js) | User-stopped stream tracking with localStorage |
| [config/cameras.json](config/cameras.json) | Opus audio enabled for all cameras |
| [streaming/ffmpeg_params.py](streaming/ffmpeg_params.py) | Main stream now uses audio config (was hardcoded copy) |
| [.gitignore](.gitignore) | Removed duplicate config/ rule |
| [docs/README_handoff.md](docs/README_handoff.md) | Session documentation |

---

## TODO List

**Pending PRs (branch protection requires PRs):**

- [ ] Create PR for `audio_restoration_JAN_19_2026_a` → main
- [ ] Create PR for `audio_restoration_JAN_19_2026_b` → main

**Completed This Session:**

- [x] Probe each camera type for native audio codec
- [x] Identify why no audio in browser (WebRTC skips AAC)
- [x] Transcode audio AAC → Opus for WebRTC
- [x] Fix main stream to use config (not hardcoded copy)
- [x] Enable audio on all cameras
- [x] Test audio in grid, modal, and fullscreen modes

**Other:**

- [ ] User-stopped test: Stop a stream manually, verify it doesn't auto-restart

---
