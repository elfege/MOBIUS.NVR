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

*Last updated: January 24, 2026 16:15 EST*

Branch: `ptz_reversal_settings_JAN_24_2026_a`

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Current Session Continued (January 24, 2026 ~15:30-16:15 EST)

### PTZ Reversal Double-Action Bug - ROOT CAUSE FOUND & FIX APPLIED

**Symptom**: When Rev Pan checkbox is checked, camera moves in correct (reversed) direction, THEN moves backward (original direction). Affects BOTH Eufy AND Amcrest cameras.

**Root Cause**: DUPLICATE EVENT HANDLERS on `.ptz-btn` buttons!

Two separate files bind handlers to PTZ buttons that BOTH fire on the same button press:

1. **`ptz-controller.js:339`** - `mousedown/touchstart` handler:
   - Calls `applyReversal()` → sends REVERSED direction
   - Example: User clicks "left" with Rev Pan → sends "right"

2. **`stream.js:556`** - `click` handler:
   - Calls `executePTZ()` directly → NO reversal applied
   - Example: Same click → sends "left" (original)

**Event firing order**:
1. `mousedown` → ptz-controller.js → reversed direction → camera moves correctly
2. `mouseup` → triggers stop
3. `click` → stream.js → original direction → camera moves backward!

**Fix Applied**: Removed duplicate `.ptz-btn` click handler from `stream.js:556-565`

**Files Modified**:

| File | Change |
|------|--------|
| `static/js/streaming/stream.js:556-565` | REMOVED duplicate PTZ click handler |

**Commit**: `5a5ab17` - Fix PTZ reversal double-action: remove duplicate click handler in stream.js

---

## Earlier Session (January 24, 2026 ~00:15-10:11 EST)

### PTZ & Camera Control Enhancements - COMPLETE

#### 1. PTZ Reversal Settings for Upside-Down Cameras

User requested ability to reverse PTZ pan/tilt controls for cameras mounted upside down. Eufy cameras don't respect native app mirror settings for PTZ.

**Implementation:**

1. **Added `reversed_pan` and `reversed_tilt` to cameras.json** - All 19 cameras now have these boolean fields (defaulting to false)

2. **Backend `camera_repository.py` methods:**
   - `update_camera_ptz_reversal(serial, reversed_pan, reversed_tilt)` - Updates settings and saves to JSON
   - `get_camera_ptz_reversal(serial)` - Returns dict with both settings

3. **API Endpoints** - `app.py:2823-2888`
   - `GET /api/ptz/<serial>/reversal` - Get current reversal settings
   - `POST /api/ptz/<serial>/reversal` - Update settings (accepts `reversed_pan` and/or `reversed_tilt`)

4. **Frontend `ptz-controller.js`:**
   - Replaced localStorage with API-based persistence
   - `loadReversalSettings(serial)` - Fetches from API on load
   - `updateReversalSettings(serial, pan, tilt)` - **Uses optimistic update pattern** (cache updated immediately, API call is fire-and-forget)
   - `applyReversal(serial, direction)` - Corrects direction before sending command
   - Staggered loading (200ms) to avoid overwhelming server on page load

5. **HTML `templates/streams.html:194-203`:**
   - Added "Rev. Pan" and "Rev. Tilt" checkboxes to PTZ controls

6. **CSS `static/css/components/ptz-controls.css:157-216`:**
   - Styled checkbox container with flex-wrap for two checkboxes
   - Custom checkbox appearance with green checkmark when enabled

**Code Flow:**

```text
User clicks direction → startMovement(direction)
  → applyReversal(serial, direction) [swaps left↔right or up↔down if enabled]
  → fetch(`/api/ptz/${serial}/${correctedDirection}`)
```

**Optimistic Update Pattern (added 09:39 EST):**

- Checkbox change immediately updates in-memory cache
- Reversal works instantly without waiting for API
- API call runs in background for persistence (non-blocking)
- Works even if container hasn't been restarted (API endpoints not yet available)

#### 2. PTZ Double-Action Bug Fix (10:00 EST)

**Root Cause:** Double direction correction - `eufy_bridge.py` had legacy `_correct_direction()` based on `image_mirrored`, while frontend had new `applyReversal()` based on checkbox. Double swap = no change.

**Fix:** Removed backend correction from `eufy_bridge.py:move_camera()`. Direction correction now handled exclusively in frontend via `ptz-controller.js applyReversal()`.

**Commit:** `0de300b` - Fix PTZ double-action: remove backend direction correction for Eufy

#### 3. Stream Control Button Mutual Exclusivity (10:02 EST)

**Issue:** Fullscreen and expand buttons could both be active simultaneously.

**Fix:** `expandCamera()` now calls `closeFullscreen()` before entering expanded mode.

**Commit:** `3d5afbd` - Fix stream control button mutual exclusivity

#### 4. PTZ Home Button for Calibration (10:05 EST)

**Backend:** Already existed (`POST /api/ptz/{serial}/home` → ONVIF GotoHomePosition)

**Frontend added:**

- HTML: Home button in PTZ controls (`templates/streams.html`)
- CSS: Yellow/gold styling (`ptz-controls.css`)
- JS: Handler treats 'home' as discrete command like '360' (`ptz-controller.js:357-366`)

**Commit:** `323f115` - Add PTZ Home button for camera recalibration

#### 5. Camera Reboot Functionality (10:08 EST)

**Backend:**

- `services/onvif/onvif_ptz_handler.py:reboot_camera()` - ONVIF SystemReboot
- `services/ptz/baichuan_ptz_handler.py:reboot_camera_baichuan()` - Reolink via reolink_aio
- `app.py: POST /api/camera/{serial}/reboot` - Requires `confirm='REBOOT'`

**Frontend:**

- Reboot button in stream controls (only if 'reboot' in capabilities)
- JS handler with confirmation dialog

**cameras.json:** Added 'reboot' to capabilities for 8 cameras (7 Reolink, 1 Amcrest)

**Commits:**

- `f02a5b7` - Add camera reboot functionality
- `ed55e73` - Add reboot capability to Reolink and Amcrest cameras

**All Session Commits:**

- `d3adaf6` - Add PTZ reversal settings for upside-down mounted cameras
- `ecdd00f` - PTZ reversal: use optimistic update for non-blocking persistence
- `0de300b` - Fix PTZ double-action: remove backend direction correction for Eufy
- `3d5afbd` - Fix stream control button mutual exclusivity
- `323f115` - Add PTZ Home button for camera recalibration
- `f02a5b7` - Add camera reboot functionality
- `ed55e73` - Add reboot capability to Reolink and Amcrest cameras

---

## Previous Session (January 22, 2026 ~23:20-23:55 EST)

### Timeline Merged Preview Implementation - VERIFIED COMPLETE

Context compaction occurred. Upon review of the plan file (`recursive-soaring-wolf.md`), discovered implementation was already completed in a previous session.

**All components implemented:**

1. **Backend `PreviewJob` class** - `services/recording/timeline_service.py:124-173`
   - Dataclass with job tracking (job_id, status, progress, temp paths)
   - FFmpeg Popen storage for cancellation support
   - `to_dict()` for JSON serialization

2. **Backend preview merge methods** - `services/recording/timeline_service.py:974-1301`
   - `create_preview_merge()` - Creates job and starts background thread
   - `_process_preview_merge()` - FFmpeg concat with iOS re-encode option
   - `cancel_preview_merge()` - Terminates FFmpeg, cleans temp files
   - `cleanup_preview()` - Deletes temp directory
   - `promote_preview_to_export()` - Moves temp to permanent export

3. **API endpoints** - `app.py:3336-3580`
   - `POST /api/timeline/preview-merge` - Start merge job
   - `GET /api/timeline/preview-merge/<job_id>` - Get status
   - `POST /api/timeline/preview-merge/<job_id>/cancel` - Cancel merge
   - `GET /api/timeline/preview-merge/<job_id>/stream` - Stream video (Range support)
   - `DELETE /api/timeline/preview-merge/<job_id>/cleanup` - Delete temp files
   - `POST /api/timeline/preview-merge/<job_id>/promote` - Promote to export

4. **Frontend** - `static/js/modals/timeline-playback-modal.js`
   - State vars: `currentPreviewMergeJobId`, `previewMergePollingInterval`, `mergedPreviewReady`
   - `showPreview()` - Starts merge, shows progress
   - `startPreviewMergePolling()` - 500ms polling for status
   - `onPreviewMergeComplete()` - Loads merged video
   - `promotePreviewToExport()` - Reuses merge for download

5. **HTML** - `templates/streams.html:313-326`
   - Merge progress bar with fill, text, cancel button

6. **CSS** - `static/css/components/timeline-modal.css:689-728`
   - Progress bar animation, merge status styling

**No code changes needed** - verified working tree is clean.

---

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
- [x] Implement PTZ presets for Eufy cameras - DONE (4 slots: 0-3)
- [x] Research zoom for S350 models - NOT AVAILABLE (API doesn't expose lens switching)
- [x] Research doorbell streaming - POSSIBLE via station RTSP relay (not implemented yet)

**Firewall - Re-enable camera WAN blocking:**

- [ ] Re-enable SonicWall `BLOCKED_CAMERAS` rule with Eufy domain whitelist
  - **Why:** Currently disabled to allow PTZ - cameras need cloud for command relay
  - **Security concern:** Cameras have unrestricted WAN access while rule is disabled
  - **Solution:** Create FQDN Address Objects for Eufy domains, add Allow rule ABOVE the block rule
  - **Domains to whitelist:** `*.eufylife.com`, `*.security.eufylife.com`, `mysecurity.eufylife.com`
  - **Note:** May need to capture DNS queries from a camera to find all required domains
  - **SonicWall version:** 6.5 firmware

**Eufy PTZ Features (Jan 22-24, 2026):**

- [x] PTZ presets implemented - 4 slots (0-3), goto/save/delete
- [x] PTZ reversal settings for upside-down cameras - DONE (Jan 24, 2026)
  - Rev. Pan and Rev. Tilt checkboxes in PTZ controls UI
  - Settings stored in cameras.json, persisted via API
- [x] Research complete: Zoom/lens switching NOT available in eufy-security-client API
  - Zoom field is hardcoded to 1.0 in station.js
  - Lens switching (wide/telephoto) not exposed
  - Would require reverse-engineering native app traffic
- [x] Research complete: Doorbell streaming possible via `device.start_rtsp_livestream`
  - Requires HomeBase as RTSP relay (station-based streaming)
  - Alternative: P2P streaming via `device.start_livestream` (binary frames)
  - Not implemented yet - needs EufyBridgeClient extension
  - **User has a HomeBase** - will power it up for next session

**Timeline Playback:**

- [x] Merged preview implementation - COMPLETE (verified Jan 22, 2026)
- [ ] Test merged preview on iOS device
- [ ] Test merged preview on Android device
- [ ] Test export promotion (preview → download without re-merge)

**Testing Needed:**

- [ ] Test iOS inline download with Share/Open in Tab buttons
- [ ] Test connection monitor on slower tablets

**Future Enhancements:**

- [ ] Scheduler integration (APScheduler) for automated migrations
- [ ] Add pan/scroll for zoomed timeline
- [ ] Two-way audio (major feature - requires WebRTC sendrecv, getUserMedia, ONVIF AudioBackChannel)

**Power Management (Future):**

- [ ] Add `power_supply` field to cameras.json: 'hubitat', 'poe', 'none'
- [ ] Hubitat integration for Eufy power control (add HUBITAT_API_TOKEN + HUBITAT_API_APP_NUMBER to AWS secrets)
- [ ] Create separate power management module (backend + frontend)
- [ ] PoE switch integration for supported cameras

**Deferred:**

- [ ] Database-backed recording settings
- [ ] Camera settings UI
- [ ] Container self-restart mechanism

---
