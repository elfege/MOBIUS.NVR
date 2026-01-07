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

*Last updated: January 6, 2026 23:45 EST*

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Previous Session Summary

**Branch merged:** `mjpeg_load_optimization_JAN_7_2026_a`
**Date:** January 6, 2026 (22:47-23:38 EST)

See `docs/README_project_history.md` section "MJPEG Load Time Optimization - January 6, 2026" for full details including:

- Phase 1 MJPEG load time optimization (initial_wait, polling interval)
- Restored missing iOS MJPEG code from ios branch
- Adaptive MediaMTX polling (waitForMediaMTXStream)
- Parallel HLS pre-warm (preWarmHLSStreams)
- Skip HLS start for already-publishing streams
- Auto-start all HLS streams at container startup
- Pre-warm polling loop

**Outcome:** MJPEG fast loading NOT fully achieved - streams still load slowly despite multiple optimizations. Further investigation needed.

Archived handoff: `docs/archive/handoffs/mjpeg_load_optimization_JAN_7_2026_a/README_handoff_20260106_2338.md`

---

## Current Session

*(No active session)*

---

## TODO List

**MJPEG Optimization (Incomplete):**

- [ ] Investigate why MJPEG streams still load slowly despite all optimizations
- [ ] Profile actual bottleneck (backend FFmpeg startup? MediaMTX? Browser?)
- [ ] Consider alternative approaches (lazy loading, pagination on iOS)

**Future Enhancements:**

- [ ] Research Neolink MQTT PTZ for E1 camera (direct reolink_aio doesn't work)
- [ ] Add `max_rtsp_connections` field to cameras.json for direct passthrough support
- [ ] Add blank-frame detection to health monitor
- [ ] Add ICE state monitoring to health.js
- [ ] Consider STUN server for remote access
- [ ] Test fullscreen main/sub stream switching with WEBRTC
- [ ] Investigate watchdog cooldown clearing bug
- [ ] Investigate UI freezes (health monitor canvas ops at 2s per camera)

---
