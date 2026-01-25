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

*Last updated: January 25, 2026 11:15 EST*

Branch: `two_way_audio_JAN_25_2026_a`

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Previous Work Summary

See `docs/README_project_history.md` for complete history. Recent sessions (Jan 22-25):

- PTZ reversal settings for upside-down cameras
- Eufy bridge PTZ fixes (direction mapping, cloud auth)
- Power management (Hubitat smart plugs + UniFi POE)
- WebRTC fullscreen quality retry logic

---

## Current Session (January 25, 2026)

### Two-Way Audio Implementation - Phase 1 Complete (Eufy)

Task: Implement two-way audio (talkback) for cameras that support it.

**Status**: Phase 1 (Eufy cameras) implementation complete. Ready for testing.

### Files Modified/Created

| Time (EST) | File | Change |
|------------|------|--------|
| 10:56 | `services/eufy/eufy_bridge.py` | Added talkback methods: `start_talkback()`, `stop_talkback()`, `send_talkback_audio()` |
| 11:00 | `app.py` | Added `/talkback` WebSocket namespace with session management |
| 11:05 | `static/js/streaming/talkback-manager.js` | **NEW** - Browser microphone capture via getUserMedia, WebSocket audio transmission |
| 11:08 | `static/css/components/talkback-button.css` | **NEW** - PTT button styling with states (active, connecting, denied, error) |
| 11:10 | `templates/streams.html` | Added talkback button for Eufy cameras, added CSS import |
| 11:12 | `static/js/streaming/stream.js` | Added PTT event handlers (mousedown/touchstart → start, mouseup/touchend → stop) |

### Architecture

```text
Browser                    Flask Backend              Camera
   |                            |                        |
   | getUserMedia()             |                        |
   | (microphone capture)       |                        |
   |                            |                        |
   |--- WebSocket audio ------->|                        |
   |    /talkback namespace     |                        |
   |                            |--- Eufy Bridge ------->|
   |                            |    P2P protocol        |
```

**Key decisions:**

- Used WebSocket (not WebRTC ingress) because MediaMTX does NOT support WHIP
- Push-to-talk (PTT) model: hold button to talk, release to stop
- Audio format: 16kHz mono 16-bit PCM, base64 encoded

### Testing Required

1. **Browser test**:
   - Navigate to streams page
   - Find Eufy camera (should show microphone button)
   - Click and hold talkback button
   - Speak into computer microphone
   - Verify audio plays through camera speaker
   - Release button, verify transmission stops

2. **Console checks**:
   - `getUserMedia` permission granted
   - WebSocket connected to `/talkback`
   - Audio frames being sent

3. **Backend logs**:
   - `[Talkback] Client connected`
   - `[Eufy] Talkback started for {serial}`

---

## TODO List

**HIGH PRIORITY - Security:**

- [ ] **Eufy bridge credentials**: Stop writing `username`/`password` to `eufy_bridge.json`
  - Currently `eufy_bridge.sh` writes credentials to JSON (lines 169-170)
  - JSON should only contain: `country`, `language`, `trustedDeviceName`, `persistentDir`, `stationIPAddresses`
  - Check if `eufy-security-ws` supports reading credentials from env vars instead of config file
  - If not, may need to modify bridge startup or find alternative approach

**Two-Way Audio - Phase 2:**

- [ ] Test Eufy talkback end-to-end (Phase 1)
- [ ] Reolink support via Baichuan protocol / FFmpeg RTSP backchannel
- [ ] ONVIF AudioBackChannel for UniFi/Amcrest cameras

**Testing Needed:**

- [ ] Test fullscreen quality recovery (WebRTC retry)
- [ ] Test iOS inline download
- [ ] Test two-way audio on Eufy cameras

**Future Enhancements:**

- [ ] Re-enable SonicWall camera blocking with Eufy domain whitelist
- [ ] Scheduler integration (APScheduler)

**Deferred:**

- [ ] Database-backed recording settings
- [ ] Camera settings UI (power settings modal)
- [ ] Container self-restart mechanism

---
