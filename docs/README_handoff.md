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

*Last updated: January 5, 2026 03:30 EST*

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Previous Session Summary

**Branch merged to main:** `docs_update_JAN_4_2026_a`

**What was accomplished:**

1. **Motion Detection Fix for WEBRTC** - Added WEBRTC to MediaMTX stream checks in ffmpeg_motion_detector.py and recording_service.py
2. **README.md Update** - Added WebRTC documentation, updated architecture diagram
3. **Engineering Architecture Update** - Added WebRTC to all diagrams in nvr_engineering_architecture.html

---

## Current Session

**Branch:** `ui_performance_JAN_5_2026_a`
**Date:** January 5, 2026 (03:30 - EST)

### Plan for This Session

1. **Fix slow UI startup** - UI takes forever to load after restart (71+ retry attempts observed)
   - Root cause: Flask takes long to become ready while starting all FFmpeg processes
   - Multiple clients (192.168.10.47, 192.168.10.110, 192.168.10.15) all waiting
   - All get "Connection refused" from upstream (172.19.0.6:5000)

2. **Fix WebRTC latency** - Currently 4-5s instead of expected ~200ms
   - User changed mediamtx.yml hlsSegmentDuration to 200ms/100ms
   - This is documented as only working with direct RTSP passthrough (no transcode)
   - With FFmpeg transcoding, these settings cause MediaMTX instability
   - Need to investigate why WebRTC latency is so high despite being UDP-based

### Observed Issues from Logs

**nvr-edge logs during startup:**
```
[error] connect() failed (111: Connection refused) while connecting to upstream
client: 192.168.10.110, request: "GET /api/status", attempt=74
client: 192.168.10.47, request: "GET /api/camera/state/T8416P0023370398", attempt=3
```

**High CPU FFmpeg processes:**
- T8416P0023370398 (Office Desk): 73% CPU
- T8416P00233717CB: 69% CPU
- C6F0SgZ0N0PoL2 (SV3C_Living_3): 45% CPU

### Key Files to Investigate

- [app.py](app.py) - Flask startup, camera initialization
- [stream_manager.py](streaming/stream_manager.py) - Stream startup sequencing
- [mediamtx.yml](packager/mediamtx.yml) - HLS segment settings (200ms/100ms may be too aggressive)
- [webrtc-stream.js](static/js/streaming/webrtc-stream.js) - WebRTC latency measurement

### Potential Solutions to Explore

**For slow UI startup:**
1. Add `/api/health` endpoint that returns quickly before all cameras are ready
2. Start streams in background, let UI show "starting" state
3. Implement graceful degradation - show UI immediately, streams populate as ready

**For WebRTC latency:**
1. Revert mediamtx.yml to stable 500ms/250ms settings
2. Check if WebRTC is actually using UDP or falling back to TCP
3. Verify ICE candidates are being exchanged correctly
4. Check browser network tab for actual WebRTC connection stats

---

## TODO List

**This Session:**

- [ ] Investigate Flask startup time and why it blocks UI
- [ ] Add health check endpoint that responds before cameras ready
- [ ] Revert mediamtx.yml HLS settings if causing WebRTC issues
- [ ] Measure actual WebRTC latency with browser dev tools

**Future Enhancements:**

- [ ] Add ICE state monitoring to health.js for better WebRTC health detection
- [ ] Consider STUN server for remote access (currently LAN-only)
- [ ] Test fullscreen main/sub stream switching with WEBRTC

---
