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

*Last updated: January 5, 2026 04:00 EST*

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Previous Session Reference

**Branch merged:** `ptz_caching_JAN_5_2026_b`
**Date:** January 5, 2026 (02:46-04:00 EST)

See `docs/README_project_history.md` for full session details including:

- PTZ preset caching (PostgreSQL, 6-day TTL)
- Baichuan PTZ handler for Reolink cameras
- ONVIF service caching (reduced PTZ latency from 9-20s to ~200ms)
- ONVIF connection pre-warming at startup
- Amcrest LL-HLS/WEBRTC support

Archived handoff: `docs/archive/handoffs/ptz_caching_JAN_5_2026_b/README_handoff_20260105_0400.md`

---

## TODO List

**Future Enhancements:**

- [ ] Test Baichuan PTZ with E1 camera (95270000YPTKLLD6 - has no ONVIF port)
- [ ] Add `max_rtsp_connections` field to cameras.json for direct passthrough support
- [ ] Add blank-frame detection to health monitor
- [ ] Add ICE state monitoring to health.js
- [ ] Consider STUN server for remote access
- [ ] Test fullscreen main/sub stream switching with WEBRTC
- [ ] Investigate watchdog cooldown clearing bug
- [ ] Investigate UI freezes (health monitor canvas ops at 2s per camera)

---
