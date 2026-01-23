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

*Last updated: January 22, 2026 07:35 EST*

Branch: `main`

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Current Session (January 22, 2026 ~07:00-07:35 EST)

### Eufy Bridge Re-enablement - COMPLETED

User created a dedicated Eufy account (`eufy@elfege.com`) with 2FA disabled for API use. After re-adding cameras to the new account, user requested Eufy bridge re-enablement.

**Code Changes Made:**

| File | Change |
|------|--------|
| `app.py` | Uncommented bridge imports, initialization, PTZ dispatch, status endpoint |
| `app.py` | Removed `raise` statements from auth endpoints |
| `app.py` | Updated cleanup handlers to pass bridge parameters |
| `.env` | Set `USE_EUFY_BRIDGE=1` and `USE_EUFY_BRIDGE_WATCHDOG=1` |
| `services/app_restart_handler.py` | Added null checks for bridge/watchdog |
| `low_level_handlers/cleanup_handler.py` | Updated `stop_all_services` signature, added null checks |

**Commits:**

- `e64e69f` - Re-enable Eufy bridge for PTZ control

**Remaining Steps for User:**

1. **Update AWS Secrets Manager:**
   - Change `EUFY_BRIDGE_USERNAME` to `eufy@elfege.com`
   - Update `EUFY_BRIDGE_PASSWORD` accordingly

2. **Restart container with full credential reload:**

   ```bash
   ./start.sh  # NOT just docker compose restart
   ```

3. **Complete browser authentication:**
   - Navigate to `https://192.168.10.20:8443/eufy-auth`
   - Enter captcha (no email code needed since 2FA disabled)

4. **Test PTZ:**
   - Use web UI PTZ controls on any Eufy camera

---

## Previous Session (January 21, 2026 21:35-21:50 EST)

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

**Eufy Bridge Re-enablement:**

- [ ] Update AWS Secrets Manager with `eufy@elfege.com` credentials
- [x] Set `USE_EUFY_BRIDGE=1` in `.env` (done in code, verify on server)
- [x] Uncomment bridge code in `app.py`
- [ ] Run `./start.sh` for full credential reload
- [ ] Complete browser authentication at `/eufy-auth`
- [ ] Test PTZ controls

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
