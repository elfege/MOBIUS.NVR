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

*Last updated: January 19, 2026 02:45 EST*

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Previous Session Context

**See:** `docs/README_project_history.md` for full history

**Previous session (Jan 18-19, 2026):** WebSocket stream restart notification implemented for instant HLS recovery after backend restarts FFmpeg.

---

## Current Session (Jan 19, 2026 01:00-02:45 EST)

### Branch Info

**Branch:** `audio_restoration_JAN_19_2026_b` (new branch after _a merged)
**Previous branch:** `audio_restoration_JAN_19_2026_a` (needs PR due to branch protection)

### Completed Work

#### 1. WebSocket Stream Recovery (from _a branch) - COMPLETED

Fixed timing issues with WebSocket stream restart notifications for WebRTC cameras.

**Solution (3-tier recovery):**
1. **Immediate:** WebSocket notification triggers `forceRefreshStream()` (no delay for WebRTC)
2. **5-second fallback:** If video still black (`readyState<2` or `videoWidth=0`), trigger refresh button click
3. **Poll-based secondary:** When CameraStateMonitor detects `degraded → online`, check if WebRTC still black and retry

#### 2. Frame Preservation During Refresh - COMPLETED

Added canvas overlay to preserve last frame during stream refresh (no black flash).
- `_captureFrameOverlay()` added to both webrtc-stream.js and hls-stream.js
- Canvas positioned over video, removed after 2 seconds

#### 3. Quiet Status Messages Toggle - COMPLETED

Added UI toggle in settings to hide verbose status messages (Degraded, Refreshing, etc.)
- Only shows: Starting, Connecting, Live, Failed, Error, Stopped
- Saved to localStorage: `quietStatusMessages`

#### 4. User-Stopped Stream Tracking - COMPLETED (commit `37188de`)

**Problem:** When user clicks stop on a stream, watchdog/health monitor would restart it.

**Solution:** Track user-stopped streams in localStorage:
- `markStreamAsUserStopped()` called when `userInitiated: true`
- `clearUserStoppedStream()` called when stream is started
- `isUserStoppedStream()` checked in `handleBackendRecovery()` and `onRecovery()`
- Recovery logic skips streams user manually stopped

**Files Modified:**
- [static/js/streaming/stream.js](static/js/streaming/stream.js) - User-stopped tracking

**WARNING ADDED:** Never use `.click()` on stop button programmatically - see comments in code.

---

## Audio Restoration Progress

### Camera Audio Codec Probing - COMPLETED

Probed cameras via FFprobe inside container:

| Camera | Type | Native Audio Codec | Sample Rate | Channels |
|--------|------|-------------------|-------------|----------|
| UniFi G5 Flex (68d49398005cf203e400043f) | unifi | **AAC LC** | 16kHz | mono |
| Reolink RLC-423WS (XCPTP369388MNVTG) | reolink | **AAC LC** | 16kHz | mono |

**Key Finding:** Both cameras output AAC natively (HLS-compatible). Historical note about "Reolink outputs PCM" appears incorrect for this model.

### Audio Enabled on UniFi Camera (TEST PENDING)

Modified `config/cameras.json` for UniFi G5 Flex:

```json
"audio": {
  "enabled": true,
  "c:a": "copy",
  "_note": "UniFi outputs AAC LC @ 16kHz mono - passthrough, no transcode needed"
}
```

**Note:** config/ is gitignored - change is local only.

**TO TEST:**
1. Restart NVR: `source ~/.bash_aliases && startnvr`
2. Check UniFi camera stream for audio
3. Verify no HLS errors in console
4. If works, enable audio on Reolink camera too

---

## TODO List

**Testing (User):**

- [ ] Restart NVR and test UniFi camera audio
- [ ] Check for HLS.js errors in console
- [ ] Test mute/unmute button functionality
- [x] Test user-stopped stream tracking (stop stream, verify it doesn't auto-restart)

**Pending PRs:**

- [ ] Create PR for `audio_restoration_JAN_19_2026_a` → main
- [ ] Create PR for `audio_restoration_JAN_19_2026_b` → main (after testing)

**Next Steps (Audio):**

- [x] Probe each camera type for native audio codec
- [ ] Test UniFi audio (pending restart)
- [ ] If UniFi works, enable audio on Reolink camera
- [ ] Consider separate audio stream approach if transcoding issues arise

---
