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

*Last updated: January 19, 2026 01:00 EST*

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Previous Session Context

**See:** `docs/README_project_history.md` for full history

**Previous session (Jan 18-19, 2026):** WebSocket stream restart notification implemented for instant HLS recovery after backend restarts FFmpeg.

---

## Current Session (Jan 19, 2026 01:00 EST)

### Branch Info

**Branch:** `audio_restoration_JAN_19_2026_a`
**Previous branch merged to main:** `dtls_webrtc_ios_JAN_18_2026_a` (needs PR due to branch protection)

### WebSocket Logging Update

Changed console log prefix from `[StreamEvents]` to `[WEBSOCKET]` for easier visibility in browser console.

**Commit:** `fdfb61c Change WebSocket log prefix to [WEBSOCKET] for easier visibility`

---

## Audio Restoration Investigation

### Historical Context (from README_project_history.md)

**Previous Audio Attempts Failed Due To:**

1. **Nov 24, 2025 (Session: Composite Key Revert)**
   - Audio enabled: `"audio": { "enabled": true }` in cameras.json
   - Error: `HLS fatal error: {type: 'mediaError', parent: 'audio', details: 'bufferAppendError'}`
   - Disabled audio → error shifted to video buffer error
   - Root cause: NOT audio itself, but composite key format mismatch across 7+ files

2. **Key Lesson:** "Symptom Chasing: Spent cycles on audio codecs, health monitors, process handlers - all red herrings from the real issue (key format mismatch)"

### Current Audio Configuration

**cameras.json structure (all cameras):**

```json
"audio": {
  "enabled": false,
  "codec": "aac",
  "bitrate": "64k",
  "rate": 44100,
  "channels": 1
}
```

**Audio codecs from cameras (per project history):**

| Camera Type | Native Audio Codec | Notes |
|------------|-------------------|-------|
| Reolink | PCM 16-bit big-endian, 16kHz stereo, 512 kbps | Heavy transcoding needed |
| SV3C | PCM A-Law, 8kHz mono, 64 kbps | Simpler codec |
| Eufy | Unknown | Need to probe |
| UniFi | AAC/Opus (per bootstrap.json) | Native HLS-compatible |

### The Problem (User's Description)

"Some camera streams tend to have some fucked up FFmpeg audio output that breaks the whole stream."

**Hypothesis:** Some cameras (especially cheap ones like Reolink) output non-standard audio that:

1. Causes FFmpeg transcoding issues
2. Results in corrupted muxed output
3. Breaks HLS.js playback entirely (not just audio)

### Potential Approaches (Research Phase)

**Option 1: Separate Audio/Video Streams**
- Pro: Isolate audio failures from video
- Con: Cheap cameras may not support multiple concurrent RTSP connections
- Implementation: Two FFmpeg processes, one `-an` video-only, one `-vn` audio-only, rejoin in browser

**Option 2: Robust Error Handling in FFmpeg**
- Use `-ignore_unknown` and audio filters to handle malformed audio
- Add fallback: if audio transcode fails, drop audio automatically
- Implementation: FFmpeg error parsing + automatic retry without audio

**Option 3: Per-Camera Audio Enable Flag**
- Current config already supports this (`"enabled": true/false`)
- Just need reliable detection of which cameras have bad audio
- Whitelist approach: only enable audio for known-good cameras

**Option 4: Audio Probe Before Streaming**
- FFprobe camera before starting stream
- Detect audio codec and sample rate
- Adjust FFmpeg parameters dynamically per camera

### Frontend Audio UI (Already Implemented)

The frontend already has:
- Mute/unmute button per camera (`.stream-audio-btn`)
- LocalStorage preference persistence (`cameraAudioPreferences`)
- `applyAudioPreference()` method on stream start
- All videos start muted by default

### Files Relevant to Audio

| File | Purpose |
|------|---------|
| [streaming/ffmpeg_params.py:179-191](streaming/ffmpeg_params.py#L179-L191) | Audio flag generation for FFmpeg |
| [config/cameras.json](config/cameras.json) | Per-camera `audio.enabled` config |
| [static/js/streaming/stream.js:643-885](static/js/streaming/stream.js#L643-L885) | Frontend audio UI handling |

---

## TODO List

**Testing (User):**

- [ ] Test WebSocket stream restart notification (verify `[WEBSOCKET]` logs appear)
- [ ] Kill FFmpeg process, verify automatic HLS refresh
- [ ] Test "Terrace Shed" (T8441P12242302AC) STARTING timeout recovery

**Pending Investigation:**

- [ ] Create PR for `dtls_webrtc_ios_JAN_18_2026_a` → main merge
- [ ] Camera 95270001CSHLPO74 RTSP port issue (needs reboot or investigation)

**Audio Restoration (Next Session):**

- [ ] Probe each camera type for native audio codec
- [ ] Test enabling audio on ONE known-good camera (UniFi G5 Flex)
- [ ] If UniFi works, investigate Reolink audio issues specifically
- [ ] Consider separate audio stream approach if transcoding continues to fail

---
