---
title: "Session Handoff Buffer"
layout: default
---

<!-- markdownlint-disable MD025 -->

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

*Last updated: January 1, 2026 (post-compaction)*
