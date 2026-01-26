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

*Last updated: January 26, 2026 10:40 EST*

Branch: `main`

**Previous session merged:** `power_cycle_safety_fix_JAN_26_2026_a`

For context on recent work, read the last ~200 lines of `docs/README_project_history.md`.

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## TODO List

**HIGH PRIORITY - Security:**

- [ ] **Eufy bridge credentials**: Stop writing `username`/`password` to `eufy_bridge.json`

**Two-Way Audio - Phase 2:**

- [ ] Run `./start.sh` to reload go2rtc with credentials - **USER ACTION REQUIRED**
- [ ] Test Reolink E1 Zoom ONVIF two-way audio
- [ ] Create Flask handler for `protocol: onvif` routing

**Testing Needed:**

- [ ] Test SV3C with new rtsp_input parameters (15s timeout, reconnect options)
- [ ] Test power-cycle UI in settings modal
- [ ] Verify auto power-cycle is disabled by default

**Future Enhancements:**

- [ ] MJPEG resolution scaling for SV3C (FFmpeg post-processing)
- [ ] MJPEG audio hybrid approach (audio via WebRTC alongside MJPEG video)

---
