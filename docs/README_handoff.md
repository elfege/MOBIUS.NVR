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

*Last updated: January 22, 2026 07:20 EST*

Branch: `timeline_playback_JAN_19_2026_a`

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Current Session (January 22, 2026 ~07:00 EST)

### Eufy Bridge Re-enablement Investigation

User created a dedicated Eufy account (`eufy@elfege.com`) with 2FA disabled for API use, then accidentally hit "Leave Home" which removed access to all cameras. After re-adding cameras to the new account, user requested Eufy bridge re-enablement.

**Investigation Findings:**

1. **Bridge code is heavily commented out in `app.py`** - Lines ~491-530 disabled
2. **Environment variables exist but disabled:**
   - `.env`: `USE_EUFY_BRIDGE=0` and `USE_EUFY_BRIDGE_WATCHDOG=0`
3. **Credentials flow:**
   - AWS Secrets Manager → `pull_nvr_secrets` → exports `EUFY_BRIDGE_USERNAME`/`PASSWORD`
   - Bridge scripts: `eufy_bridge.sh` and `services/eufy/eufy_bridge.sh`
4. **Authentication flow:**
   - Browser-based: `https://server:8443/eufy-auth`
   - Captcha + 2FA handled via Flask API endpoints (still present but `raise` statements added)

**Files Involved:**

| File | Status | Notes |
|------|--------|-------|
| `app.py` | Disabled | Bridge imports commented, PTZ dispatch disabled |
| `.env` | Disabled | `USE_EUFY_BRIDGE=0` |
| `services/eufy_service.py` | Ready | `EufyCameraService` class intact |
| `services/eufy/eufy_bridge_client.py` | Ready | WebSocket client intact |
| `services/eufy/eufy_bridge.sh` | Ready | Container startup script |
| `config/eufy_bridge.json` | Placeholder | Says "NO LONGER IN USE" |

**Steps to Re-enable (for user when ready):**

1. **Update AWS Secrets Manager:**
   - Change `EUFY_BRIDGE_USERNAME` to `eufy@elfege.com`
   - Update `EUFY_BRIDGE_PASSWORD` accordingly

2. **Enable in `.env`:**

   ```bash
   USE_EUFY_BRIDGE=1
   USE_EUFY_BRIDGE_WATCHDOG=1
   ```

3. **Restart container with full credential reload:**
   ```bash
   ./start.sh  # NOT just docker compose restart
   ```

4. **Complete browser authentication:**
   - Navigate to `https://192.168.10.20:8443/eufy-auth`
   - Enter captcha, then verification code from email

5. **Test PTZ:**
   - Use web UI PTZ controls on any Eufy camera

**Code changes NOT made** (waiting for user confirmation):

- Uncommenting bridge initialization in `app.py`
- Removing `raise` statements from auth endpoints

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
- [ ] Set `USE_EUFY_BRIDGE=1` in `.env`
- [ ] Uncomment bridge code in `app.py` (see investigation notes above)
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
