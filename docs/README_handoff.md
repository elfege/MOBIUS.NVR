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

1. Pre-buffer implementation (first)
2. Passthrough mode for HD recording
3. HD fullscreen (deferred)

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

## Parallel Session: January 1, 2026 (12:30-14:00)

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

#### 6. Adaptive PTZ Latency Learning (14:15 EST) - TO BE TESTED

- **Feature:** Learn per-camera ONVIF latency and adapt stop timing
- **Storage:** `localStorage` with key `ptz_latency_{serial}`
- **Algorithm:** Rolling average of last 10 samples + 20% safety margin
- **Default:** 1000ms for cameras with no data yet
- **Methods added:**
  - `getCameraLatency(serial)` - returns learned latency for camera
  - `updateCameraLatency(serial, observedLatency)` - updates rolling average
- **File:** `static/js/controllers/ptz-controller.js`

**How it works:**

1. When move command sent, `moveStartTime` recorded
2. When move acknowledged, latency = `performance.now() - moveStartTime`
3. Latency saved to localStorage (rolling avg of 10 samples)
4. On next stop, uses learned latency as max wait time before sending stop

### Technical Notes

- ONVIF PTZ uses ContinuousMove - camera keeps moving until Stop command received
- ONVIF PTZ latency (700-2300ms) is inherent to the protocol + Amcrest/Reolink cameras
- ONVIF connections ARE cached (`services/onvif/onvif_client.py:56-58`)
- Delay is in camera processing SOAP requests, not connection overhead
- Frontend stop triggers immediately; delay is backend/camera response
- Race condition at camera level: if stop arrives before move is fully processed, camera ignores it

---

*Last updated: January 1, 2026 14:20 EST*


