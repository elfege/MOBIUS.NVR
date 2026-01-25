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

*Last updated: January 25, 2026 10:45 EST*

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

### Two-Way Audio Implementation - PLANNING

Task: Implement two-way audio for cameras that support it.

**Current status**: Audio playback already works. Need to add microphone capture and transmission.

---

## TODO List

**Two-Way Audio:**

- [ ] Research ONVIF AudioBackChannel specification
- [ ] Research camera-specific audio input protocols (Eufy, Reolink, Amcrest)
- [ ] Design WebRTC sendrecv architecture
- [ ] Implement getUserMedia for microphone capture
- [ ] Backend audio routing

**Testing Needed:**

- [ ] Test fullscreen quality recovery (WebRTC retry)
- [ ] Test iOS inline download

**Future Enhancements:**

- [ ] Re-enable SonicWall camera blocking with Eufy domain whitelist
- [ ] Scheduler integration (APScheduler)

**Deferred:**

- [ ] Database-backed recording settings
- [ ] Camera settings UI (power settings modal)
- [ ] Container self-restart mechanism

---
