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

*Last updated: January 5, 2026 01:00 EST*

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Previous Session Summary

**Branch merged to main:** `ui_performance_JAN_5_2026_b`

See `README_project_history.md` (January 5, 2026 entry) for full session details:

- Fixed database schema - added missing `updated_at` column via migration
- Fixed Neolink config error - added placeholder camera entry
- WebRTC latency issue resolved (was stale browser state)
- Implemented Protect Snapshot API for UniFi MJPEG
- Switched OFFICE KITCHEN from MJPEG to WEBRTC
- Key finding: Neolink supports Baichuan protocol (port 9000) for E1/Argus cameras

Archived to: `docs/archive/handoffs/ui_performance_JAN_5_2026_b/README_handoff_20260105_005922.md`

---

## Current Session

**Branch:** `neolink_e1_JAN_5_2026_a`
**Date:** January 5, 2026

### Planned Work: Add Reolink E1 Camera via Neolink

**Camera Info (from screenshot):**

- IP: 192.168.10.123
- MAC: 44:ef:bf:27:0d:30
- Type: Reolink E1 (Baichuan protocol, port 9000, no RTSP/ONVIF)

**Implementation Steps:**

1. **Update `config/neolink.toml`** - Replace placeholder with real E1 camera
2. **Add entry to `config/cameras.json`**
3. **Run `./update_mediamtx_paths.sh`** to add MediaMTX path
4. **Restart containers:** `./start.sh`

**Questions for user:**

- What name/serial to use for this camera?
- Camera credentials (username/password)?
- Are there other E1 cameras to add?

---

## TODO List

**Pending:**

- [ ] Add Reolink E1 camera (192.168.10.123) via Neolink

**Future Enhancements:**

- [ ] Add `max_rtsp_connections` field to cameras.json for direct passthrough support
- [ ] Add blank-frame detection to health monitor (browser-side black frames not detected)
- [ ] Add ICE state monitoring to health.js for better WebRTC health detection
- [ ] Consider STUN server for remote access (currently LAN-only)
- [ ] Test fullscreen main/sub stream switching with WEBRTC
- [ ] Investigate watchdog cooldown clearing bug

---
