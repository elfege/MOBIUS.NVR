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

*Last updated: January 23, 2026 04:25 EST*

Branch: `main`

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Current Session (January 22-23, 2026 ~22:00-04:25 EST)

### Eufy PTZ Fix - COMPLETED

**Root Cause:** Cameras blocked from WAN access in SonicWall firewall. Eufy cameras require cloud connectivity for PTZ command relay - even with `stationIPAddresses` configured for direct P2P data, control commands still route through Eufy cloud.

**Fix Applied:**

1. Added `host` and `mac` fields to all Eufy cameras in `cameras.json`
2. Updated `eufy_bridge.sh` with `populate_config()` to build `stationIPAddresses` from cameras.json
3. Added UDP ports 32100/32108 to docker-compose.yml for P2P communication
4. Changed config path from `/tmp/eufy_bridge.json` to `/app/config/eufy_bridge.json`
5. Disabled SonicWall BLOCKED_CAMERAS rule to allow cameras WAN access to Eufy cloud
6. Power cycled cameras to reconnect to Eufy cloud

**PTZ now working after firewall change.**

---

### Earlier Eufy PTZ Fix (same session)

Fixed multiple bugs preventing Eufy PTZ from working.

**Issues Found & Fixed:**

1. **`_running` flag never set** - `start()` set `is_started=True` but `is_running()` checked `_running`
2. **WebSocket response ordering** - Responses came back async, code read wrong messageId
3. **Direction mapping completely wrong** - Had custom values instead of official enum
4. **Stop command doesn't exist** - Eufy cameras auto-stop, no explicit stop in API

**Correct PTZ Direction Mapping (from `eufy-security-client` PanTiltDirection enum):**

```python
# /app/node_modules/eufy-security-client/build/p2p/types.d.ts
'360': 0,    # ROTATE360
'left': 1,   # LEFT
'right': 2,  # RIGHT
'up': 3,     # UP
'down': 4,   # DOWN
# NO STOP COMMAND - cameras auto-stop after movement
```

**Code Changes:**

| File | Change |
|------|--------|
| `services/eufy/eufy_bridge.py` | Fixed `_running` flag in `start()` |
| `services/eufy/eufy_bridge.py` | Added `_wait_for_message()` to handle async responses |
| `services/eufy/eufy_bridge.py` | Fixed direction mapping per official enum |
| `services/eufy/eufy_bridge.py` | Removed stop command (doesn't exist in API) |
| `gunicorn.conf.py` | Added `/api/camera/state/` to filtered log paths |
| `app.py` | Fixed `unifi_frame_buffers` undefined error |
| `app.py` | Skip Eufy in ONVIF warm-up (uses bridge, not ONVIF) |

**Commits:**

- `ec195da` - Silence /api/camera/state/ endpoint in access logs
- `f28c1a5` - Fix unifi_frame_buffers error, add Eufy PTZ debug logging
- `fb74aba` - Fix Eufy bridge: set _running flag, skip ONVIF for Eufy cameras
- `e049e05` - Fix Eufy PTZ: wait for correct messageId response
- `f846117` - Eufy PTZ: handle cameras that don't support stop command
- `1b4806b` - Fix Eufy PTZ direction mapping per official PanTiltDirection enum

---

## Earlier Session (January 22, 2026 ~07:00-07:35 EST)

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

**User Steps Completed:**

- [x] Updated AWS Secrets Manager with `eufy@elfege.com` credentials
- [x] Ran `./start.sh` for credential reload
- [x] Completed browser authentication at `/eufy-auth`

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

## TODO List

**Eufy PTZ - Next Steps:**

- [x] Verify PTZ physically moves cameras - WORKING after firewall fix
- [x] Test directions: up, down, left, right - WORKING
- [ ] Implement PTZ presets for Eufy cameras (currently shows 0 presets)
- [ ] Implement zoom for S350 models (lens switching, not optical zoom)
- [ ] Investigate doorbell streaming via eufy-security-ws (no RTSP port, but bridge may allow streaming like native app)

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
