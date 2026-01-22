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

*Last updated: January 21, 2026 21:50 EST*

Branch: `timeline_playback_JAN_19_2026_a`

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Current Session (January 21, 2026 21:35-21:50 EST)

### Eufy PTZ Local Control Research

User asked about achieving local PTZ control for Eufy cameras without cloud authentication.

**Research Conducted:**

1. **Confirmed current integration uses bropat/eufy-security-client** via `eufy-security-ws` bridge
2. **Documented why cloud auth is required:**
   - P2P session establishment needs cloud for NAT hole punching
   - Encryption keys derived from cloud authentication
   - Device verification against Eufy cloud
3. **Found reverse-engineered PTZ command IDs:**
   - `CMD_INDOOR_PAN_CALIBRATION = 6017`
   - `CMD_INDOOR_ROTATE = 6030`
   - Direction values: LEFT=1, RIGHT=2, UP=3, DOWN=4
4. **Network ports documented:** UDP 32108 (discovery), UDP 32100 (P2P)
5. **Academic research found:** USENIX WOOT 24 paper on Eufy reverse engineering
6. **Blue Iris finding:** Eufy PTZ doesn't work there either (same ONVIF limitation)
7. **Custom firmware option:** Thingino (untested for PTZ)

**File Created:**
- `docs/README_eufy_ptz_research.md` - Comprehensive research documentation

**Conclusion:** No fully local PTZ solution exists. Cloud auth required for P2P session keys.

---

## Previous Session Context

**Last session (Jan 20-21, 2026)** completed and ported to `docs/README_project_history.md`.

For full context on timeline playback iOS export features, see the January 20-21 section in project history (search for "Timeline Playback iOS Export").

Key work completed:

- Mobile preview visibility fixes
- iOS export with Share/Open in Tab buttons
- Export optimization (skip redundant encoding)
- Ultra-slow device tier for connection monitor

---

## TODO List

**Testing Needed:**

- [ ] Test iOS inline download with Share/Open in Tab buttons
- [ ] Test connection monitor on slower tablets

**Future Enhancements:**

- [ ] Scheduler integration (APScheduler) for automated migrations
- [ ] Add pan/scroll for zoomed timeline

**Deferred:**

- [ ] Database-backed recording settings
- [ ] Camera settings UI
- [ ] Container self-restart mechanism

---
