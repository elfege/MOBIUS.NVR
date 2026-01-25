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

*Last updated: January 25, 2026 15:15 EST*

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

**Status**: Phase 1 (Eufy cameras) implementation complete with waiting modal. Ready for testing.

### Files Modified/Created

| Time (EST) | File | Change |
|------------|------|--------|
| 10:56 | `services/eufy/eufy_bridge.py` | Added talkback methods + P2P livestream wrapper |
| 11:00 | `app.py` | Added `/talkback` WebSocket namespace with session management |
| 11:05 | `static/js/streaming/talkback-manager.js` | **NEW** - Microphone capture, waiting modal with funny messages |
| 11:08 | `static/css/components/talkback-button.css` | **NEW** - PTT button + waiting modal styles |
| 11:10 | `templates/streams.html` | Added talkback button for Eufy cameras, added CSS import |
| 11:12 | `static/js/streaming/stream.js` | Added PTT event handlers with camera name pass-through |
| 11:25 | `static/js/streaming/talkback-manager.js` | Added `_waitForTalkbackStart()`, waiting modal with min 2s display |
| 12:30 | `services/eufy/eufy_bridge.py` | Added persistent WebSocket sessions (`_talkback_sessions`) |
| 13:00 | `services/eufy/eufy_bridge.py` | Added `_wait_for_livestream_ready()` - accepts video/audio data events |
| 13:15 | `static/js/streaming/stream.js` | Added `resetAllControlStates()` - clears PTZ, audio, talkback on reload |
| 13:45 | `services/eufy/eufy_bridge.py` | Fixed websockets 10+ compatibility (`.state` vs `.closed`) |
| 14:30 | `services/talkback_transcoder.py` | **NEW** - FFmpeg PCM→AAC transcoder for Eufy |
| 14:35 | `app.py` | Integrated transcoder manager with talkback namespace |
| 14:45 | `static/js/streaming/talkback-manager.js` | Added mic selector, waveform visualization, toggle mode |
| 14:50 | `static/css/components/talkback-button.css` | Added visualization canvas and mic dropdown styles |
| 15:10 | `services/talkback_transcoder.py` | Fixed reader thread: non-blocking I/O with select() |

### Debugging Session (12:30-14:30 EST)

**Issue 1: `device_livestream_not_running` error**

- Root cause: P2P started on one WebSocket, talkback on another (different sessions)
- Fix: Combined into `_start_talkback_session()` with persistent WebSocket

**Issue 2: Timeout waiting for `livestream started` event**

- Root cause: When P2P already running, only `livestream video data` events fire
- Fix: `_wait_for_livestream_ready()` accepts multiple event types as proof of ready

**Issue 3: `'ClientConnection' object has no attribute 'closed'`**

- Root cause: websockets library v16.0 changed API from `.closed` to `.state`
- Fix: Added compatibility check for both attributes

**Issue 4: Button states persisting on reload**

- Fix: `resetAllControlStates()` clears localStorage preferences and DOM classes on init

**Issue 5: Audio not playing on camera speaker**

- Root cause: Eufy requires AAC ADTS format, not raw PCM
- Discovery: Found via GitHub eufy-security-client issue #153
- Fix: Created `services/talkback_transcoder.py` - FFmpeg transcoder PCM→AAC
- Audio specs: 16kHz, mono, 20kbps, ADTS container

**Issue 6: Transcoder reader thread blocking forever**

- Root cause: `read()` call blocks indefinitely waiting for FFmpeg output
- Fix: Use non-blocking I/O with `select()` and `O_NONBLOCK` flag

### P2P Requirement Discovery

**Problem**: Initial test showed `device_livestream_not_running` error.

**Root cause**: eufy-security-client requires an active P2P video stream before talkback - audio piggybacks on the video tunnel.

**Solution**: Added P2P livestream wrapper methods:

- `start_p2p_livestream()` - starts temporary P2P for talkback
- `stop_p2p_livestream()` - stops P2P after talkback ends
- `is_p2p_streaming()` - check if P2P active

This is separate from RTSP streaming (can run simultaneously).

### Waiting Modal Implementation

User requested funny messages while P2P connects. Implementation:

- 12 funny messages (e.g., "Summoning the audio gnomes...")
- Minimum 2-second display time (even if P2P connects fast)
- Spinner animation with microphone icon
- Cancel button to abort
- "Ready! Hold to talk..." message when P2P ready

### Architecture

```text
Browser                    Flask Backend              Camera
   |                            |                        |
   | getUserMedia()             |                        |
   | (microphone capture)       |                        |
   |                            |                        |
   | Show waiting modal         |                        |
   | with funny message         |                        |
   |                            |                        |
   |--- WebSocket ------------>|                        |
   |    start_talkback          |                        |
   |                            |--- start P2P -------->|
   |                            |    (video tunnel)      |
   |                            |                        |
   |<-- talkback_started -------|                        |
   |                            |                        |
   | Hide modal (ready state)   |                        |
   |                            |                        |
   |--- WebSocket audio ------->|                        |
   |    /talkback namespace     |                        |
   |                            |--- audio via P2P ---->|
   |                            |    (piggybacks video)  |
```

**Key decisions:**

- Used WebSocket (not WebRTC ingress) because MediaMTX does NOT support WHIP
- Push-to-talk (PTT) model: hold button to talk, release to stop
- Audio format: Browser captures 16kHz mono 16-bit PCM → FFmpeg transcodes to AAC ADTS → sent to Eufy bridge
- P2P started on talkback, stopped on release (not kept alive)
- Toggle mode: Click to start, click again to stop (10-min auto-timeout)
- Mic selector: User can choose microphone device from dropdown
- Waveform visualization: Real-time oscilloscope display of microphone input

### Testing Required

1. **Browser test**:
   - Navigate to streams page
   - Find Eufy camera (should show microphone button)
   - Click and hold talkback button
   - **NEW**: Should see waiting modal with funny message
   - Wait for "Ready! Hold to talk..." message
   - Speak into computer microphone
   - Verify audio plays through camera speaker
   - Release button, verify transmission stops

2. **Console checks**:
   - `getUserMedia` permission granted
   - WebSocket connected to `/talkback`
   - `[TalkbackManager] Sending start_talkback for {serial}`
   - `[TalkbackManager] Talkback started:` (P2P ready)
   - Audio frames being sent

3. **Backend logs**:
   - `[Talkback] Client connected`
   - `[Eufy] Starting P2P livestream for {serial}`
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
