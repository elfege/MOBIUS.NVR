---
title: "Session Handoff Buffer"
layout: default
---

<!-- markdownlint-disable MD025 -->
<!-- markdownlint-disable MD024 -->
<!-- markdownlint-disable MD036 -->

# Session Handoff Buffer

This file is updated after each file modification during a Claude Code session.
It serves as a buffer before content is transferred to `README_project_history.md`.

---

## Current Session: January 1, 2026

**Branch:** `mediamtx_centralization_and_workflow_setup_JAN_1_2026_a`

### Work Completed This Session

#### 1. Project History Updated

- Added missing December 31, 2024 entries (ONVIF port fix, stale camera removal)
- Added January 1, 2026 entries (MediaMTX centralization, SV3C LL_HLS, scene detection threshold discovery)
- **File:** `docs/README_project_history.md`

#### 2. CLAUDE.md Created

- Project instructions for Claude Code CLI
- Context compaction handling instructions
- Project overview and architecture notes
- **File:** `CLAUDE.md`

#### 3. Workflow & Branching Setup

- Created feature branch: `mediamtx_centralization_and_workflow_setup_JAN_1_2026_a`
- Updated this handoff buffer file
- Established branching strategy: `[description]_[MONTH]_[DAY]_[YEAR]_[a,b,c...]`
- Commit trigger: every file modification (Edit/Write tool use)
- **Files:** `docs/README_handoff.md`, `CLAUDE.md`

#### 4. LL_HLS Scene Detection Threshold Fix (03:25)

- Auto-adjust sensitivity for LL_HLS cameras from 0.3 to 0.01
- Re-encoded streams via MediaMTX have low scene scores due to `scenecut=0` encoder param
- Only auto-adjusts if sensitivity >= 0.1 (allows explicit low config to override)
- **File:** `services/motion/ffmpeg_motion_detector.py`

**Technical Context:**

- `scenecut=0` in ffmpeg_params.py (line 137) disables keyframe insertion at scene changes
- This is intentional for LL-HLS: ensures predictable keyframe intervals for smooth playback
- Side effect: frame-to-frame differences are smoothed, scene detection sees ~0.001 instead of 0.3+
- Solution: Lower threshold for LL_HLS cameras, keep `scenecut=0` for streaming quality

**Future VCA Considerations:**

- Current LL_HLS streams are 320x240 (downscaled for grid view)
- For object detection/tracking, would need higher resolution analysis stream
- Options: passthrough mode, separate analysis stream, or tap camera directly (not viable for single-connection cameras)

### Session Completed - Merged to Main

**Branch merged:** `mediamtx_centralization_and_workflow_setup_JAN_1_2026_a` → `main`

**Verification:** 12 motion recordings captured for SV3C camera - threshold 0.01 working.

---

## Research Session: January 1, 2026 (Continued)

### Research: Pre-Buffer, Recording Resolution, HD Fullscreen

#### 1. Pre-Detection Recording

- UI/config exists (`pre_buffer_sec`, `post_buffer_sec`) but backend NOT implemented
- `recording_service.py:start_motion_recording()` ignores buffer settings
- **Solution:** Rolling segment approach with FFmpeg segment muxer
- **Plan file:** `~/.claude/plans/flickering-swinging-taco.md`

#### 2. Recording Resolution Issue

- LL_HLS cameras record from MediaMTX (320x240 re-encoded stream)
- Direct RTSP cameras use main stream (hardcoded in `recording_service.py:102`)
- **Future fix:** Passthrough mode or dual MediaMTX publish

#### 3. HD Fullscreen (Composite Key History)

- November 2025: Attempted composite key refactor (`camera:stream_type`)
- Failed: Key format mismatch across 7+ files, reverted
- **Alternative:** Dual MediaMTX publish (deferred)

#### Priority

1. Pre-buffer implementation (first) ✅ DONE
2. Passthrough mode for HD recording
3. HD fullscreen (deferred)

---

## Implementation Session: January 1, 2026 (17:00-18:40 EST)

**Branch:** `pre_buffer_implementation_JAN_1_2026_a` → Merged to main

### Pre-Buffer Recording Implementation - COMPLETE

Implemented rolling segment pre-buffer for motion-triggered recordings.

#### Files Created

- **`services/recording/segment_buffer.py`** - NEW
  - `SegmentBuffer`: Per-camera FFmpeg segment muxer (5-sec .ts files)
  - `SegmentBufferManager`: Multi-camera buffer lifecycle management
  - Rolling deque evicts old segments beyond configured buffer duration

#### Files Modified

- **`config/recording_config_loader.py`**
  - Added `pre_buffer_enabled: False` to defaults
  - Added `is_pre_buffer_enabled()` and `get_pre_buffer_seconds()` helpers

- **`static/js/forms/recording-settings-form.js`**
  - Added "Enable Pre-Buffer Recording" checkbox toggle
  - Updated `extractFormData()` to include new field

- **`services/recording/storage_manager.py`**
  - Added `/recordings/buffer/` directory management
  - Added `cleanup_buffer_directory()` for orphan cleanup

- **`services/recording/recording_service.py`**
  - Integrated `SegmentBufferManager`
  - `start_motion_recording()` checks config and dispatches
  - Added `_start_prebuffered_recording()` for pre-buffer flow
  - Added `_finalize_prebuffered_recording()` for FFmpeg concat
  - Fixed `auto` recording_source: now resolves to `mediamtx` for LL_HLS cameras

- **`app.py`**
  - Initialize segment buffers at startup for enabled cameras
  - Added periodic buffer cleanup (every 5 min)
  - Fixed: `get_all_cameras()` returns IDs, not dicts

#### Technical Flow

1. If `pre_buffer_enabled=true`: FFmpeg segment muxer writes 5-sec `.ts` files to `/recordings/buffer/{camera_id}/`
2. On motion: copy buffered segments to temp dir, start live recording as `.ts`
3. On recording end: FFmpeg concat demuxer joins `[prebuffer] + [live]` → final `.mp4`
4. Cleanup temp files

#### Key Design Points

- Segment buffer taps **MediaMTX RTSP** for LL_HLS cameras (no additional camera connection)
- `pre_buffer_enabled` toggle required (separate from `pre_buffer_sec > 0`) due to continuous FFmpeg process overhead
- Fallback to live-only recording if no segments available

#### Testing Checklist

- [ ] Toggle appears in UI and saves correctly
- [ ] Segment buffer starts only for cameras with toggle enabled
- [ ] Old segments deleted after buffer duration
- [ ] Motion triggers concatenation of prebuffer + live
- [ ] Final MP4 plays with prebuffer content
- [ ] Fallback to live-only when no segments available

#### Commits

1. `Add segment buffer service for pre-detection recording`
2. `Add buffer directory support to storage manager`
3. `Add pre-buffer UI toggle`
4. `Implement pre-buffer recording for motion detection`
5. `Fix pre-buffer init: get_all_cameras returns IDs not dicts`
6. `Fix auto recording_source: use MediaMTX for LL_HLS cameras`

---

## Future Work

### Short-term

- [ ] Monitor for false positives with 0.01 threshold
- [ ] Tune threshold if needed (currently appears good based on recording patterns)

### Medium-term: Full MediaMTX Centralization

**Goal:** Route ALL streams through MediaMTX so recording and motion detection don't create additional camera connections.

**Current state:**

- LL_HLS cameras → MediaMTX (done)
- HLS cameras → direct to disk (not centralized)
- MJPEG cameras → fake MJPEG service (special handling needed)

**Tasks:**

- [ ] Move HLS streams to publish through MediaMTX instead of writing directly to disk
- [ ] Recording service taps MediaMTX RTSP for all stream types
- [ ] Motion detector taps MediaMTX RTSP for all stream types
- [ ] Handle fake MJPEG cameras (Eufy bridge outputs MJPEG, may need special path)

**Benefits:**

- Single camera connection regardless of how many consumers
- Consistent motion detection behavior across all stream types
- Simplified recording source logic

---

---

## Parallel Session: January 1, 2026 (12:30-14:25 EST)

**Branch:** `mobile_ptz_grid_hide_JAN_1_2026_a`

### Mobile PTZ UI Improvements

#### 1. Hide PTZ Controls in Grid View on Touch Devices

- PTZ buttons were too large on iPhone/iPad, obscuring play/stop/refresh buttons
- Added CSS media query `@media (hover: none)` to hide `.ptz-controls` in grid view
- PTZ remains available in fullscreen mode (`.css-fullscreen` class)
- **File:** `static/css/components/ptz-controls.css`

#### 2. PTZ Touch Event Handling Fixes

- Touch devices weren't reliably detecting finger release (touchend)
- Added document-level `touchend`/`touchcancel` handlers
- Track `lastInputType` to avoid mouse emulation conflicts
- **File:** `static/js/controllers/ptz-controller.js`

#### 3. Added Stop Button to PTZ Grid

- Red stop button in center of directional grid
- Provides manual fallback when automatic stop doesn't trigger
- **Files:** `templates/streams.html`, `static/css/components/ptz-controls.css`, `static/js/controllers/ptz-controller.js`

#### 4. Timestamped PTZ Logging

- Added ISO timestamps and elapsed time to all PTZ log messages
- `stopMovement()` now awaits and logs camera confirmation
- Revealed: ONVIF stop takes 700-2300ms (camera/protocol latency, not code issue)
- **File:** `static/js/controllers/ptz-controller.js`

#### 5. PTZ Race Condition Fix (14:00 EST)

- **Problem:** Stop command sometimes ignored - camera keeps moving
- **Root cause:** Move command used `await fetch()`, blocking until camera acknowledged
- **Effect:** Stop sent while move still processing at camera level; camera ignores stop (nothing to stop yet)
- **Fix 1:** Changed `startMovement()` to fire-and-forget (no await)
- **Fix 2:** `stopMovement()` waits for move acknowledgment before sending stop
- **File:** `static/js/controllers/ptz-controller.js`

#### 6. Adaptive PTZ Latency Learning - Database Backed (14:15-14:25 EST) - TO BE TESTED

- **Feature:** Learn per-camera ONVIF latency and adapt stop timing
- **Storage:** PostgreSQL database via PostgREST API
- **Client ID:** Browser-generated UUID stored in `localStorage` key `nvr_client_uuid`

**Database Changes:**

- **New table:** `ptz_client_latency`
  - `client_uuid` VARCHAR(36) - browser instance identifier
  - `camera_serial` VARCHAR(50) - camera identifier
  - `avg_latency_ms` INTEGER - rolling average latency
  - `samples` JSONB - last 10 latency samples
  - `sample_count` INTEGER - number of samples collected
- **Migration:** `psql/migrations/001_add_ptz_client_latency.sql`
- **Schema update:** `psql/init-db.sql`

**Backend API (app.py):**

- `GET /api/ptz/latency/<client_uuid>/<camera_serial>` - retrieve learned latency
- `POST /api/ptz/latency/<client_uuid>/<camera_serial>` - update with new sample
- Uses PostgREST for database access

**Frontend (ptz-controller.js):**

- `getOrCreateClientUuid()` - generates/retrieves UUID from localStorage
- `loadCameraLatency(serial)` - fetches from API when camera selected
- `updateCameraLatency(serial, latency)` - posts to API after each move
- `latencyCache` - in-memory cache for immediate responsiveness

**How it works:**

1. On camera select, `loadCameraLatency()` fetches stored data from DB
2. When move command sent, `moveStartTime` recorded
3. When move acknowledged, latency measured and sent to API
4. API maintains rolling average of last 10 samples
5. On next stop, uses learned latency as max wait time before sending stop

**Benefits over localStorage:**

- Persists across browser cache clears
- Visible in database for debugging/monitoring
- Per-client tracking (same camera can have different latency from different networks)
- Could enable admin dashboard view in future

### Technical Notes

- ONVIF PTZ uses ContinuousMove - camera keeps moving until Stop command received
- ONVIF PTZ latency (700-2300ms) is inherent to the protocol + Amcrest/Reolink cameras
- ONVIF connections ARE cached (`services/onvif/onvif_client.py:56-58`)
- Delay is in camera processing SOAP requests, not connection overhead
- Frontend stop triggers immediately; delay is backend/camera response
- Race condition at camera level: if stop arrives before move is fully processed, camera ignores it

---

## Session Continued: January 1, 2026 (16:00-17:00 EST)

### PTZ Zoom Controls Added

#### 1. Zoom Buttons in UI

- Added zoom in/out buttons below the directional PTZ grid
- Styled with distinct colors (green for zoom in, cyan for zoom out)
- **Files:** `templates/streams.html`, `static/css/components/ptz-controls.css`

#### 2. SV3C ONVIF PTZ Support Fixed

- **Problem:** SV3C cameras returned "PTZ not supported for camera type: sv3c"
- **Root cause:** `app.py` PTZ routes only checked for `amcrest` and `reolink` types
- **Fix:** Added `'sv3c'` to camera type checks in three locations:
  - PTZ move endpoint (line 1432)
  - Get presets endpoint (line 1478)
  - Goto preset endpoint (line 1510)
- **File:** `app.py`

#### 3. SV3C Zoom Limitation Discovered

- **Finding:** SV3C 1080P PTZ cameras have **digital zoom only** (no optical zoom motor)
- **Behavior:** ONVIF zoom commands are sent and accepted, but camera doesn't respond
- **Evidence:**
  - Logs show `ONVIF PTZ zoom_in started for C6F0SgZ0N0PoL2` (command sent successfully)
  - Camera reports ZoomLimits in ONVIF configuration (nominal, not functional)
  - Pan/tilt work because those have actual motors
- **Conclusion:** ONVIF PTZ zoom is designed for motorized optical zoom lenses; budget PTZ cameras with digital-only zoom don't respond to these commands
- Amcrest and Reolink cameras with optical zoom motors work correctly

### Files Modified

1. `templates/streams.html` - Added zoom button HTML
2. `static/css/components/ptz-controls.css` - Zoom button styling
3. `app.py` - Added 'sv3c' to ONVIF PTZ camera type checks

---

## Session Continued: January 1, 2026 (Context Compaction Recovery)

**Branch:** `mediamtx_centralization_and_workflow_setup_JAN_1_2026_a`

### SV3C Preset Investigation

#### Issue Reported

- User reported SV3C preset goto "moves vaguely towards the position but stops short of reaching it"
- Preset worked from camera's built-in Web UI but not as accurately from NVR ONVIF

#### Investigation Findings

1. **SV3C Preset Token Format:** Camera uses tokens like `Preset001` not `1`
   - API correctly fetches presets with full token names from ONVIF
   - Frontend correctly passes `preset.token` to API

2. **SV3C Reports Position as (0.0, 0.0):** Camera's ONVIF GetStatus always returns pan=0, tilt=0
   - Cannot programmatically verify if preset was reached
   - Budget camera ONVIF implementation doesn't expose actual position

3. **256 Presets Pre-Configured:** SV3C reports Preset001-Preset256 all with position (0,0)

4. **User Confirmed Working:** After investigation, user confirmed presets are working acceptably

#### Technical Notes

- SV3C ONVIF port: 8080 (not default 80)
- Camera IP: 192.168.10.90 (per cameras.json)
- Credentials via SV3CCredentialProvider

### Files Read (No Modifications)

- `services/onvif/onvif_ptz_handler.py` - Reviewed goto_preset implementation
- `static/js/controllers/ptz-controller.js` - Verified preset token handling
- `app.py` - Verified API endpoint routing

---

## MediaMTX Centralization Session: January 1, 2026 (19:00+ EST)

**Branch:** `mediamtx_full_centralization_JAN_1_2026_a`

### Goal

Route all HLS-type streams through MediaMTX so recording and motion detection tap MediaMTX RTSP instead of creating additional camera connections.

### Changes Made

#### 1. Converted HLS Cameras to LL_HLS

- **Office Desk** (`T8416P0023370398`): `stream_type: "HLS"` → `"LL_HLS"`
- **Hot Tub** (`T8441P122428038A`): `stream_type: "HLS"` → `"LL_HLS"`
- Both already had `ll_hls` config sections with MediaMTX publisher settings
- **File:** `config/cameras.json` (gitignored, manual change)

#### 2. Updated Recording Service Auto-Resolution

- `recording_service.py:_get_recording_source_url()` now routes:
  - `LL_HLS`, `HLS`, `NEOLINK_LL_HLS` → `mediamtx` (tap MediaMTX RTSP)
  - `MJPEG` → `mjpeg_service` (placeholder, not yet implemented)
  - Others → `rtsp` (fallback to direct camera)
- **File:** `services/recording/recording_service.py`

### Stream Type Summary After Centralization

| Stream Type | Count | Recording Source | Notes |
|-------------|-------|------------------|-------|
| **LL_HLS** | 12 | MediaMTX RTSP | All HLS cameras now use this |
| **MJPEG** | 5 | Capture service | Recording not implemented |
| **HLS** | 0 | (deprecated) | Converted to LL_HLS |

### MJPEG Recording Status

- MJPEG cameras use dedicated capture services (reolink, amcrest, unifi)
- Recording for MJPEG is NOT implemented - raises `NotImplementedError`
- Capture services provide lowest latency for grid view
- Future work: implement recording by tapping capture service buffer

### Testing Required

- [x] Restart NVR to apply cameras.json changes
- [x] Verify Office Desk and Hot Tub streams work via MediaMTX
- [x] Test motion recording uses MediaMTX source for these cameras
- [x] Confirm no additional camera connections created

---

## Debugging Session: January 1, 2026 (20:00-20:45 EST)

**Branch:** `mediamtx_full_centralization_JAN_1_2026_a`

### Critical Bug Fixed: Duplicate `cleanup_finished_recordings()` Method

**Problem:** Pre-buffered recordings were not being finalized - temp files existed with `live.ts` and `prebuf_000.ts` but no final MP4 was created.

**Root Cause:** Two `cleanup_finished_recordings()` methods existed in `recording_service.py`:

- Line 626: Correct version with pre-buffer finalization logic
- Line 792: Duplicate version without finalization logic (Python uses last definition)

**Fix:** Removed duplicate method at line 792.

**File:** `services/recording/recording_service.py`

### Motion Detection Fixes

1. **NoneType Error for stream_type**
   - Changed `camera.get('stream_type', '').upper()` to `(camera.get('stream_type') or '').upper()`
   - **File:** `services/recording/recording_service.py:85`

2. **FFmpeg Motion Detector Race Condition**
   - Thread started before `active_detectors[camera_id]` was set
   - Moved assignment before `thread.start()`
   - **File:** `services/motion/ffmpeg_motion_detector.py:93-112`

3. **LL_HLS Scene Detection Threshold**
   - Office Desk producing scores 0.0005-0.002, below 0.005 threshold
   - Lowered LL_HLS default threshold from 0.005 to 0.002
   - **File:** `services/motion/ffmpeg_motion_detector.py:86`

### Doorbell Camera Filter

- Pre-buffer initialization skips cameras without `streaming` capability
- **File:** `app.py:254-257`

### Timezone Fix

- Added volume mounts for timezone sync:

```yaml
- /etc/localtime:/etc/localtime:ro
- /etc/timezone:/etc/timezone:ro
```

- **File:** `docker-compose.yml:95-96`

### Segment Buffer Auto-Restart

- Added `_restart_ffmpeg()` method for auto-restart when FFmpeg exits
- **File:** `services/recording/segment_buffer.py:283-374`

### Verification

- Motion detection triggers for Office Desk camera (score 0.003-0.007)
- Recordings finalized with pre-buffer: `T8416P0023370398_20260101_203221.mp4` (1.1 MB)

---

## Session: January 1-2, 2026 (21:30-02:50 EST)

**Branch:** `ui_health_monitor_black_frames_JAN_1_2026_a`

### UI Health Monitor Fixes

**Problem:** SV3C_Living_3 camera showed "Failed" status on initial page load, but worked after manual refresh.

**Root Cause:** Race condition between stream initialization and health monitor - health monitor checked before video element had decoded frames.

**Fixes Applied:**

1. **Health monitor waits for video readyState** (health.js:143-149)
   - Added check `if (t.el.readyState < 2)` before health checking
   - Video must have decoded frames before checking for black screens

2. **HLS streams show "Connecting..." instead of "Failed"** (stream.js:404-413)
   - HLS streams have retry logic, initial error doesn't mean permanent failure
   - Changed immediate "Failed" to "Connecting..." for HLS/LL_HLS types

3. **Added stream status events** (hls-stream.js:171-195)
   - `streamlive` event fired when first fragment received
   - `streamretrying` event fired during 404 retry loops with retry count

4. **UI listens for status events** (stream.js:603-620)
   - Updates status indicator in real-time based on HLS events
   - Shows "Retry X/20..." during retry attempts

### Laundry Room Camera Issue (Ongoing)

**Problem:** Laundry Room (95270001NT3KNA67) completely broken - black screen, never loads.

**Investigation:**

- RTSP connection times out (even in VLC with correct credentials)
- Camera works fine in native Reolink app (uses Baichuan protocol, port 9000)
- FFmpeg exit code 8 in constant restart loop
- MediaMTX logs: `no stream is available on path '95270001NT3KNA67'`

**Root Cause:** Camera's RTSP service is likely crashed/hung. Works via Baichuan (native app) but not RTSP.

**Status:** Needs camera reboot (physically inaccessible currently)

### Baichuan Protocol Research

**History reviewed:** October 2025 - Neolink integration attempted for low-latency streaming via Baichuan (port 9000).

**Why it was abandoned:**

- Neolink successfully connects via Baichuan
- But outputs RTSP, which still needs HLS conversion for browser
- HLS segmentation adds ~1.5s minimum latency regardless of source
- Native app is faster (~300ms) because: direct binary stream, GPU decode, no HTTP overhead

**Potential future approach:**

- Use Neolink `image` command for snapshot polling via Baichuan
- Similar to existing MJPEG capture services
- Would work when RTSP is broken (different protocol)
- Laundry camera NOT in neolink.toml - would need to be added

### Files Modified This Session

| File | Changes |
|------|---------|
| `static/js/streaming/health.js` | Added readyState check before health monitoring |
| `static/js/streaming/stream.js` | HLS "Connecting..." status, stream event listeners |
| `static/js/streaming/hls-stream.js` | Added streamlive/streamretrying custom events |

### Testing Checklist

- [x] Health monitor waits for video readyState >= 2
- [x] HLS streams show "Connecting..." on initial error
- [x] Stream status events fire correctly
- [ ] SV3C camera loads without manual refresh (needs user verification)
- [ ] Laundry camera (needs reboot to test)

---

## Session: January 2, 2026 (02:50+ EST)

**Branch:** `mediamtx_centralization_and_workflow_setup_JAN_1_2026_a`

### Custom 502 Error Page Implementation

**Problem:** When NVR container restarts, nginx shows ugly default 502 Bad Gateway error page.

**Solution:** Created custom 502 error page with auto-retry functionality.

#### Files Created/Modified

1. **`nginx/502.html`** - NEW
   - Dark themed "NVR Starting Up..." page
   - Animated spinner and progress bar
   - 5-second countdown timer with auto-retry
   - Tracks retry attempts in URL parameter
   - Background health check at `/api/health`
   - Manual "Retry Now" button

2. **`nginx/nginx.conf`** - MODIFIED
   - Added custom error page directive for 502/503/504 errors:

   ```nginx
   error_page 502 503 504 /502.html;
   location = /502.html {
       root /usr/share/nginx/html;
       internal;
   }
   ```

3. **`docker-compose.yml`** - MODIFIED
   - Added volume mount for 502.html:

   ```yaml
   - ./nginx/502.html:/usr/share/nginx/html/502.html:ro
   ```

#### Testing Required

- [x] Restart nvr-edge container to load new nginx config
- [ ] Stop nvr container and verify custom 502 page appears
- [ ] Verify auto-retry works and loads app when available

#### Fix: 403 Forbidden Instead of Custom Error Page (06:15-06:45 EST)

**Problem:** User got 403 Forbidden instead of custom 502 page.

**Root Cause:** `internal` directive with `root` was preventing the error page from being served.

**Fix Applied:**

1. Added `proxy_intercept_errors on;` - tells nginx to intercept HTTP errors from backend
2. Changed from `root` + `internal` to `alias` - serves file directly without restrictions
3. Used different location path (`/custom_error.html`) mapped to actual file via alias

Final working nginx.conf:

```nginx
error_page 502 503 504 /custom_error.html;
location = /custom_error.html {
    alias /usr/share/nginx/html/502.html;
}
proxy_intercept_errors on;
```

**Result:** ✅ Custom error page now displays correctly when NVR backend is down!

---

## Session: January 2, 2026 (05:00-06:00 EST)

**Branch:** `ui_health_monitor_black_frames_JAN_1_2026_a` (continued)

### Neolink/Baichuan Integration for Laundry Camera

**Problem:** Laundry Room camera (95270001NT3KNA67) RTSP was unresponsive but native Reolink app worked (uses Baichuan protocol on port 9000).

**Investigation Results:**

1. **Port 9000 (Baichuan) was reachable** even when RTSP (port 554) was hung
2. **Neolink successfully connected** via Baichuan and logged in
3. **Root cause identified:** Camera's RTSP service gets saturated and TCP layer doesn't flush properly
4. **RTSP can be reset** from native app without full camera reboot

### Changes Made

1. **Added Laundry camera to neolink.toml**
   - Uses serial as name for consistency: `name = "95270001NT3KNA67"`
   - Configured for subStream via Baichuan: `address = "192.168.10.118:9000"`
   - Increased buffer_size to 100 for E1 Zoom's variable bitrate

2. **Updated cameras.json**
   - Changed Laundry camera `stream_type` from `"LL_HLS"` to `"NEOLINK"`

3. **Fixed reolink_stream_handler.py** (`_build_NEOlink_url`)
   - Was using camera name with underscores, now uses serial
   - Path: `rtsp://neolink:{port}/{serial}/{stream_type}`
   - Stream type now respects 'main' or 'sub' parameter (was hardcoded to 'main')

4. **Created update_neolink_config.sh** - NEW
   - Auto-generates neolink.toml from cameras.json
   - Filters for `stream_type: "NEOLINK"` and `type: "reolink"`
   - Uses serial as Neolink camera name for consistency
   - Injects REOLINK_USERNAME/PASSWORD from environment

### Files Modified/Created

| File | Changes |
|------|---------|
| `config/neolink.toml` | Added Laundry camera with serial as name |
| `config/cameras.json` | Changed Laundry stream_type to NEOLINK |
| `streaming/handlers/reolink_stream_handler.py` | Fixed Neolink URL building |
| `update_neolink_config.sh` | NEW - auto-sync neolink.toml |

### Neolink Buffer Issue - Deep Investigation (01:00-02:00 EST)

**Root Issue:** GStreamer internal buffers in Neolink fill faster than RTSP client can consume.

**Symptoms:**

```log
Buffer full on audsrc pausing stream until client consumes frames
Buffer full on vidsrc pausing stream until client consumes frames
Failed to send to source: App source is not linked
```

**FFmpeg error:** `Operation not permitted` after RTSP PLAY command (exit code 8: "Invalid data found")

**Research Findings:**

1. **TOML Syntax for Array Sub-tables:**
   - `[cameras.pause]` within `[[cameras]]` requires 2-space indentation
   - Without indent, TOML creates top-level table instead of nested property
   - Verified correct parsing with Python tomllib

2. **Config Options Tested:**

   ```toml
   [[cameras]]
   name = "95270001NT3KNA67"
   buffer_size = 10           # Frames (reduced from 100)
   buffer_duration = 500      # Milliseconds
   use_splash = true          # Visual feedback when paused
   idle_disconnect = true     # Disconnect after 30s inactivity
   push_notifications = false # Reduce traffic

     [cameras.pause]
     on_client = true         # Pause when no RTSP client
     timeout = 2.0            # Seconds before pausing
   ```

3. **Issue Persists Despite Correct Config:**
   - `on_client = true` SHOULD prevent streaming until client connects
   - Buffer overflow occurs THE MOMENT a client connects
   - GStreamer starts receiving from camera faster than RTSP handshake completes
   - Data arrives before client is ready to consume

4. **VLC Test from Windows:** Partial success - connected but showed "stream not ready"

5. **Frigate Users' Solution:** Use UDP transport preset (`preset-rtsp-udp`)
   - Source: <https://github.com/blakeblackshear/frigate/discussions/20612>

**Files Updated:**

- `config/neolink.toml` - Added all config options above
- `update_neolink_config.sh` - Updated template with detailed TOML syntax comments

### SOLUTION FOUND: Rollback to v0.6.2 (02:35 EST)

**Root Cause Confirmed:** Buffer overflow is a **regression in v0.6.3.rc.x**

- GitHub Issue: <https://github.com/QuantumEntangledAndy/neolink/issues/349>
- Multiple users confirm v0.6.2 works correctly

**Fix Applied:**

```yaml
# docker-compose.yml - neolink service
neolink:
  # Use v0.6.2 - v0.6.3.rc.x has buffer overflow regression
  image: quantumentangledandy/neolink:v0.6.2
```

**Result:** ✅ Buffer overflow FIXED!

- v0.6.2 shows correct behavior: `Activating Client` → `Pausing Client`
- No buffer overflow spam
- FFprobe from NVR container connects successfully
- Stream properly pauses when client disconnects

### Testing Required

- [x] Switch to v0.6.2 Docker image
- [x] FFprobe test from NVR container - WORKS
- [x] **Full NVR UI test - STREAM WORKS!** (06:42 EST)
- [ ] Test VLC from Windows: `rtsp://192.168.10.20:8554/95270001NT3KNA67/sub`
- [ ] Add update_neolink_config.sh to start.sh

### Final Result (06:42 EST)

**Laundry Room camera streaming via Neolink Baichuan bridge - CONFIRMED WORKING!**

Full chain verified:

```
E1 Zoom Camera (port 9000) → Neolink v0.6.2 (RTSP) → NVR StreamManager → MediaMTX → HLS → Browser
```

Neolink logs show proper client lifecycle:

```log
[INFO] 95270001NT3KNA67: Activating Client   # When browser requests stream
[INFO] 95270001NT3KNA67: Pausing Client      # When browser closes/navigates away
```

**Key Takeaways:**

- Neolink v0.6.3.rc.x has buffer overflow regression - use v0.6.2
- Baichuan protocol (port 9000) works when RTSP (port 554) is unresponsive
- TOML array sub-tables require 2-space indentation for nested properties

---

## Next Steps: NEOLINK → MediaMTX LL-HLS (07:15 EST)

### Current Problem

NEOLINK streams use legacy HLS path (FFmpeg writes segments directly). This means:

1. High latency (3-4s) due to HLS segment duration
2. Motion detection has no stream to tap
3. Recording source unavailable

### Planned Fix

Route NEOLINK through MediaMTX LL-HLS path (same as other LL_HLS cameras):

```
Camera:9000 → Neolink (RTSP) → MediaMTX (LL-HLS) → Browser
                                    ↓
                            Motion detection taps here
                            Recording taps here
```

### Implementation

Modify `stream_manager.py` to treat `NEOLINK` same as `LL_HLS`:

- Add `NEOLINK` to the `LL_HLS` branch condition
- Source URL comes from Neolink RTSP (`rtsp://neolink:8554/{serial}/sub`)
- MediaMTX handles LL-HLS packaging

Benefits:

- Lower latency (~1s vs 3-4s)
- Motion detection works
- Recording works
- Single connection to Neolink

---

## Session Continued: January 2, 2026 (02:30+ EST)

**Branch:** `neolink_motion_detection_JAN_2_2026_a`

### NEOLINK → MediaMTX LL-HLS Integration - COMPLETE

Successfully routed NEOLINK streams through MediaMTX LL-HLS path for lower latency and motion detection support.

#### Files Modified

1. **`streaming/stream_manager.py`**
   - Added `NEOLINK` to LL_HLS branch condition
   - NEOLINK now routes through MediaMTX like LL_HLS cameras

2. **`services/recording/recording_service.py`**
   - Added `NEOLINK` to MediaMTX recording source condition
   - Recording now taps MediaMTX RTSP for NEOLINK cameras

3. **`update_mediamtx_paths.sh`**
   - Include NEOLINK cameras in MediaMTX paths generation
   - Updated jq filter: `stream_type == "LL_HLS" or .value.stream_type == "NEOLINK"`

4. **`services/motion/ffmpeg_motion_detector.py`**
   - Added NEOLINK support for threshold adjustment (0.01 for LL_HLS/NEOLINK)
   - Added NEOLINK to MediaMTX RTSP tap condition

5. **`start.sh`**
   - Fixed: Added `. ~/.bash_utils` for credentials loading
   - Fixed: Corrected script name `update_neolink_config.sh` (was typo)
   - Removed duplicate `update_neolink_configuration.sh`

6. **`config/cameras.json`**
   - Updated LAUNDRY ROOM to use UDP transport for Neolink sources

### UI Bug Fix: Latency Badge Blocking Settings Button

**Problem:** Settings button not working for LAUNDRY ROOM and AMCREST cameras.

**Root Cause:** Latency badge overlay positioned at top-right (`right: 8px, top: 8px`) was blocking the settings button click area.

**Fix:** Moved latency badge to bottom-left corner.

**File:** `static/js/streaming/hls-stream.js`
- `right: '8px'` → `left: '8px'`
- `top: '8px'` → `bottom: '8px'`

### Testing Results

- ✅ LAUNDRY ROOM streaming via Neolink → MediaMTX LL-HLS
- ✅ PTZ controls working
- ✅ Motion detection service connected (Reolink Baichuan)
- ✅ Settings button should now work (needs UI refresh to verify)

### Verified

- [x] Verify settings button works after hard refresh
- [x] Verify motion detection triggers recording for NEOLINK cameras
  - Recording confirmed: `95270001NT3KNA67_20260102_023621.mp4` (2.2 MB at 02:36)

---

*Last updated: January 2, 2026 02:42 EST*

Always read `CLAUDE.md` in case I updated it in between sessions.
