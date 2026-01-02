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

*Last updated: January 2, 2026 04:29 EST*

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Current Session: January 2, 2026 (02:30-04:01 EST)

### Branch: `sub_main_stream_switching_JAN_2_2026_a`

### Objective

Implement sub/main stream switching for HLS/LL_HLS cameras:

- **Grid view**: Low-resolution sub stream (efficient for multi-camera display)
- **Fullscreen**: High-resolution main stream (better quality for single camera focus)

---

## Research Summary: November 2025 Failure Analysis

### What Failed: Composite Key Pattern

The previous attempt used composite keys like `"camera:main"` and `"camera:sub"` throughout the system. This touched **7+ interconnected files**:

1. `streaming/stream_manager.py` - Stream dictionary keys
2. `app.py` - API endpoints and route parameters
3. `static/js/streaming/stream.js` - Frontend stream management
4. `static/js/streaming/hls-stream.js` - HLS player instances
5. `static/js/pages/index.js` - Grid initialization
6. `static/js/components/fullscreen.js` - Fullscreen handler
7. `config/cameras.json` - Camera configuration

**Root Cause**: Systemic key format mismatch - not all files were updated consistently, causing:

- Streams starting but not being tracked
- Multiple instances created for same camera
- Streams not stopping properly
- Frontend/backend key mismatches

### What Works: MJPEG Pattern (For Reference)

The MJPEG implementation successfully supports dual streams:

- Separate API endpoints: `/api/mjpeg/<camera_id>/sub` and `/api/mjpeg/<camera_id>/main`
- Key suffix pattern: `camera_id_main = f"{camera_id}_main"`
- Both streams can run simultaneously

---

## Design Decision: Single FFmpeg, Dual Output

### The Multi-Client Problem

**Initial Idea**: Stop sub stream before starting main stream (one stream per camera).

**Problem Identified**: This breaks multi-client scenarios:

- Client A viewing grid (sub stream)
- Client B clicks fullscreen → stops sub → **Client A loses video!**
- FFmpeg process is per-camera, not per-client

### Solution Chosen: Option D - Single FFmpeg with Dual Outputs

**User Decision**: One FFmpeg process pulls from camera main stream, produces TWO outputs:

```
Camera (main RTSP) → FFmpeg → sub (transcoded, scaled per cameras.json) → MediaMTX /camera
                           ↘ main (passthrough, -c:v copy) → MediaMTX /camera_main
```

**Benefits:**

- Single camera connection (budget cameras only allow one)
- Server controls quality via `cameras.json` settings
- Multi-client safe (sub always available)
- Main stream is full resolution passthrough (no re-encoding latency)

**Trade-off:**

- Sub stream has ~100-200ms extra latency from transcoding (but we're already transcoding anyway)

---

## Backend Changes Completed

### `streaming/stream_manager.py`

**Key Change**: Renamed `stream_type` parameter to `resolution` to avoid confusion with `cameras.json` `stream_type` (which means protocol: HLS, LL_HLS, MJPEG, etc.)

```python
def start_stream(self, camera_serial: str, resolution: str = 'sub') -> Optional[str]:
    """Start stream asynchronously and return immediately

    Args:
        camera_serial: Camera identifier
        resolution: 'sub' for grid view (low-res), 'main' for fullscreen (high-res)
                    Note: Different from cameras.json 'stream_type' which is protocol

    Note: With one-stream-per-camera design, starting a new resolution
    should first stop any existing stream for that camera.
    """
```

**Stream Key Logic** (current implementation - may simplify):

```python
# Current: Different keys for sub vs main
stream_key = f"{camera_serial}_main" if resolution == 'main' else camera_serial
```

**With one-stream-per-camera**: Could simplify to always use `camera_serial` as key, just track `resolution` in stream entry.

### `app.py`

Updated `/api/stream/start/<camera_serial>` endpoint:

```python
data = request.get_json() or {}
resolution = data.get('type', 'sub')  # 'main' or 'sub'

# Start the stream with specified resolution
stream_url = stream_manager.start_stream(camera_serial, resolution=resolution)
```

---

## Frontend Changes Needed

### Dual-Stream Fullscreen (Sub Keeps Running)

```javascript
// In fullscreen.js or stream.js

async function openFullscreen(cameraId, videoElement) {
    // 1. Show loading indicator (sub stream continues in background)
    showLoadingIndicator(videoElement);

    // 2. Start main stream (uses different key: cameraId_main)
    // Sub stream keeps running for other clients
    await hlsManager.startStream(cameraId, videoElement, 'main');

    // 3. Enter fullscreen with main stream
    requestFullscreen(videoElement.parentElement);
}

async function exitFullscreen(cameraId, videoElement) {
    // 1. Stop main stream only (sub was never stopped)
    await fetch(`/api/stream/stop/${cameraId}_main`, { method: 'POST' });

    // 2. Switch video element back to sub stream (already running)
    await hlsManager.startStream(cameraId, videoElement, 'sub');
}
```

**Key Insight**: The stop endpoint needs `cameraId_main` suffix to stop main stream specifically, not the sub stream.

---

## Other Session Work

### Latency Badge Fix

**Problem**: Latency badge at top-right blocked settings button for some cameras.

**Fix** in `static/js/streaming/hls-stream.js`:

```javascript
Object.assign(badge.style, {
    position: 'absolute',
    left: '8px',    // Changed from right: '8px'
    bottom: '8px',  // Changed from top: '8px'
    // ...
});
```

### NEOLINK MediaMTX Integration

Completed in earlier part of session:

- LAUNDRY ROOM camera (Reolink E1 Pro) streaming via Neolink → MediaMTX LL-HLS
- Detection method: Baichuan (Reolink proprietary, port 9000)
- Tested successfully with motion recordings

---

## Implementation Complete

### Backend

1. **`streaming/ffmpeg_params.py`** - Added `build_ll_hls_dual_output_publish_params()`
   - Single FFmpeg produces TWO outputs from one camera connection
   - Sub stream: transcoded, scaled per cameras.json `rtsp_output.vf` settings
   - Main stream: passthrough (`-c:v copy`) for full resolution

2. **All LL_HLS handlers updated** to use dual output:
   - `streaming/handlers/reolink_stream_handler.py`
   - `streaming/handlers/eufy_stream_handler.py`
   - `streaming/handlers/sv3c_stream_handler.py`
   - `streaming/handlers/unifi_stream_handler.py`

3. **`streaming/stream_manager.py`** - Smart main stream detection
   - For LL_HLS/NEOLINK: if sub stream running and main requested → return main URL
   - No new FFmpeg process needed (dual-output already publishing both)

4. **`update_mediamtx_paths.sh`** - Generates both paths:
   - `/camera_serial` (sub)
   - `/camera_serial_main` (main)

### Frontend

1. **`static/js/streaming/stream.js`**
   - `openFullscreen()`: Switch to main stream for LL_HLS/NEOLINK cameras
   - `closeFullscreen()`: Switch back to sub stream on exit
   - Track `switched-to-main` flag to know when to switch back

---

## Testing Complete (03:30 EST)

**Sub/Main Stream Switching - VERIFIED WORKING**

After container recreation (`startnvr`), user confirmed:

- HD quality in fullscreen (main stream passthrough working)
- Closing fullscreen works correctly (switches back to sub)
- Overall feature working as designed

---

## Audio Support Implementation (03:30-03:50 EST)

### History

Audio was disabled in November 2025 due to `HLS bufferAppendError`. Investigation revealed this was a **red herring** - the real issue was the composite key refactor causing key mismatches across 7+ files. The audio error was a symptom, not the cause.

### What Was Done

1. **Backend**: Enabled audio in `cameras.json` for all 17 cameras
   - Changed `ll_hls.audio.enabled: false` → `true`
   - Codec: AAC, 64kbps, 44.1kHz, mono

2. **Frontend UI**: Added per-camera mute/unmute button
   - New `.stream-audio-btn` with speaker icon
   - Positioned next to fullscreen button
   - Green highlight when unmuted
   - Saves preference to localStorage per camera

3. **Global Controls**: Added to Settings panel
   - "Mute All" / "Unmute All" buttons
   - Applies to all video streams at once

4. **Behavior**:
   - Audio starts **muted by default** (browser autoplay policy)
   - User click required to unmute
   - Preferences persist across page reloads

### Files Modified

| File | Change |
|------|--------|
| `config/cameras.json` | `audio.enabled: true` for all cameras |
| `templates/streams.html` | Added `.stream-audio-btn` button |
| `static/css/components/stream-item.css` | Styles for audio button |
| `static/js/streaming/stream.js` | Audio toggle handler + preference storage |
| `static/js/settings/settings-ui.js` | Mute All / Unmute All buttons |

---

## UI Button Repositioning (03:50-04:00 EST)

### Changes Made

1. **Settings & Record buttons**: Moved from top-right to bottom-right
   - No longer overlap with audio button
   - Hidden by default, show on hover
   - Record button stays visible when actively recording

2. **Live/Status indicator**: Kept at top-left next to camera title
   - Changed `.stream-overlay` from `justify-content: space-between` to `flex-start`
   - Title and status now stay together on the left

3. **Audio button**: Top-right, next to fullscreen button (unchanged)

4. **Latency badge**: Bottom-left (unchanged from earlier fix)

### Files Modified

| File | Change |
|------|--------|
| `static/css/components/recording-modal.css` | Buttons to bottom-right, hover behavior |
| `static/css/components/stream-overlay.css` | Status indicator next to title |

---

## Pending Tasks

1. **Test audio playback** - Restart NVR with `startnvr` and verify audio works

2. **Optional future**: Rename `stream_type` to `protocol` in cameras.json (deferred)

---

## Files Modified This Session

| File | Change |
|------|--------|
| `streaming/ffmpeg_params.py` | Added `build_ll_hls_dual_output_publish_params()` |
| `streaming/handlers/reolink_stream_handler.py` | Use dual-output for LL_HLS |
| `streaming/handlers/eufy_stream_handler.py` | Use dual-output for LL_HLS |
| `streaming/handlers/sv3c_stream_handler.py` | Use dual-output for LL_HLS |
| `streaming/handlers/unifi_stream_handler.py` | Use dual-output for LL_HLS |
| `streaming/stream_manager.py` | Smart main stream detection for dual-output |
| `update_mediamtx_paths.sh` | Generate both sub and main paths |
| `static/js/streaming/stream.js` | Fullscreen sub/main switching |
| `static/js/streaming/hls-stream.js` | Latency badge position fix |
| `docs/README_handoff.md` | This file |

---

## Commits

- Branch: `sub_main_stream_switching_JAN_2_2026_a`
- All implementation committed and pushed

---

## Key Terminology Clarification

| Term | Location | Meaning |
|------|----------|---------|
| `stream_type` | `cameras.json` | Protocol: HLS, LL_HLS, MJPEG, NEOLINK, RTMP |
| `resolution` | `stream_manager.py` | Quality: 'sub' (low-res) or 'main' (high-res) |
| `stream_key` | Internal | Dictionary key for tracking active streams |

---

## Custom 502 Error Page (02:50-03:20 EST)

### Problem

When NVR container restarts, nginx shows ugly default 502 Bad Gateway error page.

### Solution Implemented

Created friendly custom error page with auto-retry functionality.

### Files Modified

1. **`nginx/502.html`** - Custom error page with:
   - Dark themed UI matching NVR aesthetic
   - Animated spinner
   - **Silly rotating messages** (every 2 seconds with fade):
     - "Waking up the cameras..."
     - "Brewing digital coffee..."
     - "Convincing pixels to cooperate..."
     - "Negotiating with RTSP streams..."
     - "Reticulating splines..."
     - ...and 15 more
   - Progress bar (0% → 100%, fixed direction)
   - 5-second countdown with auto-retry
   - Attempt counter
   - Manual "Retry Now" button
   - Background health check polling `/api/status` every 2 seconds

2. **`nginx/nginx.conf`** - Added error page configuration:

   ```nginx
   error_page 502 503 504 /custom_error.html;
   location = /custom_error.html {
       alias /usr/share/nginx/html/502.html;
   }
   proxy_intercept_errors on;
   ```

3. **`docker-compose.yml`** - Added volume mount:

   ```yaml
   - ./nginx/502.html:/usr/share/nginx/html/502.html:ro
   ```

### Debugging Notes

- Initial attempt with `root` + `internal` directive returned 403 Forbidden
- Fixed by using `alias` instead - serves file directly without restrictions
- `proxy_intercept_errors on;` required for nginx to intercept backend errors

### Additional Fixes

- **Progress bar sync** (03:30 EST): Changed from CSS animation to JavaScript-controlled width, syncs exactly with countdown timer (20% per second)

---

## Draggable PTZ Controls in Fullscreen (03:34 EST)

### Problem

PTZ controls in fullscreen mode had fixed position at bottom-right, which could obstruct the video or be inconvenient for users.

### Solution Implemented

Made PTZ controls draggable when in fullscreen mode.

### Implementation Details

1. **`static/css/components/fullscreen-ptz.css`** - Updated CSS:
   - Changed selectors from `.fullscreen-ptz-controls` to `.stream-item.css-fullscreen .ptz-controls`
   - Added drag handle styles with grab cursor
   - Drag handle icon: 2 horizontal lines (using `::before` pseudo-element with `box-shadow`)
   - Blur backdrop effect on controls

2. **`static/js/controllers/ptz-controller.js`** - Added drag functionality:
   - `setupDraggable()`: Uses MutationObserver to watch for `.css-fullscreen` class
   - `addDragHandle()`: Dynamically prepends drag handle div when entering fullscreen
   - `removeDragHandle()`: Removes handle and resets position when exiting fullscreen
   - `startDrag()`, `doDrag()`, `endDrag()`: Handle mouse/touch drag events
   - `restorePTZPosition()`: Restores saved position from localStorage
   - Position constrained to viewport bounds during drag
   - Position persisted to localStorage for cross-session persistence

### Key Technical Details

- Uses event delegation for drag handle events (dynamically created element)
- MutationObserver pattern to detect fullscreen mode changes
- Converts from `bottom/right` CSS positioning to `top/left` during drag for smooth movement
- Touch and mouse support for mobile/desktop compatibility

### Files Modified

| File | Change |
|------|--------|
| `static/css/components/fullscreen-ptz.css` | Drag handle styles, flexbox layout |
| `static/js/controllers/ptz-controller.js` | Drag functionality with MutationObserver |

### Touch Support Fix (January 2, 2026 ~04:00 EST)

**Issue Identified**: Document-level `touchend` handler at line 464 was calling `stopMovement()` unconditionally on every touch release, which interfered with drag handle interactions.

**Fix Applied**:

1. Added `isDraggingPTZ` flag initialization in constructor (line 16)
2. Modified document touchend handler to check `isDraggingPTZ` flag before calling `stopMovement()`
3. Only calls `stopMovement()` if PTZ movement was actually active (`ptzTouchActive || activeDirection`)

**Code Change** in `ptz-controller.js` lines 462-481:
```javascript
$(document).on('touchend touchcancel', () => {
    // Skip if we're dragging PTZ controls
    if (this.isDraggingPTZ) {
        console.log(`[PTZ] Touch ended during PTZ drag - ignoring for movement`);
        return;
    }
    // Only stop if PTZ movement was active
    if (this.ptzTouchActive || this.activeDirection) {
        this.stopMovement();
    }
});
```

**Status**: Fix committed and pushed. Testing pending on touch device.

---

## Continued Session: January 2, 2026 (04:20-04:35 EST)

### Context Compaction Recovery

Previous session ran out of context. Continued investigating stream stability issues.

### FFmpeg Segment Buffer Reconnect Flags

**Issue**: Segment buffer FFmpeg processes exiting with code 0 (normal termination) when they should run continuously.

**Investigation**:
- Found FFmpeg 7.1.3 is now installed (much newer than before)
- Checked that `-reconnect` flags are fully supported
- Previous note in README_project_history.md mentioned `-reconnect` caused crashes - now safe

**Fix Applied** in `services/recording/segment_buffer.py`:

Added reconnect flags to both `start()` and `_restart_ffmpeg()` methods:

```python
cmd = [
    'ffmpeg',
    '-rtsp_transport', 'tcp',
    '-reconnect', '1',
    '-reconnect_at_eof', '1',
    '-reconnect_streamed', '1',
    '-reconnect_on_network_error', '1',
    '-reconnect_delay_max', '5',
    '-i', self.source_url,
    # ...rest of command
]
```

**Note on cameras.json**: User asked if FFmpeg commands should be dynamically built from cameras.json. Clarification:
- The reconnect flags are RTSP **input** flags for connection resilience
- They're separate from the `rtsp_input` section in cameras.json which has FFmpeg **input params** like `timeout`, `analyzeduration`
- For segment buffer specifically, `-c copy` (no transcoding) is always used
- Could consider integrating `rtsp_input` params from cameras.json in future

### Observations from Running Processes

Found segment buffer processes using direct camera RTSP URLs instead of MediaMTX for some cameras:
- XCPTP369388MNVTG (Living_REOLINK): Direct RTSP to 192.168.10.186
- 95270001CSO4BPDZ: Direct RTSP to 192.168.10.88

These cameras have `stream_type: MJPEG` but still have RTSP capability. The current code path:
1. `stream_type == 'MJPEG'` → `recording_source = 'mjpeg_service'`
2. Raises `NotImplementedError`
3. Should be caught and skipped

**Possible Explanations**:
- Container has older code version
- Different startup path for these cameras

**Status**: Reconnect flags committed. Container restart (`startnvr`) needed to apply changes.

### Files Modified This Continuation

| File | Change |
|------|--------|
| `services/recording/segment_buffer.py` | Added FFmpeg reconnect flags |
| `docs/README_handoff.md` | This update |

---

## Race Condition Fix: January 2, 2026 (04:45-04:50 EST)

### Problem Identified

**Symptoms:**
- LL-HLS publishers starting then immediately stopping
- Streams appearing briefly then disappearing after page load/refresh
- Motion detectors and segment buffers failing with exit code 8 (no stream in MediaMTX)
- Logs showing rapid start → stop cycles

**Root Cause Found:**

In `streaming/stream_manager.py`:

1. `is_stream_alive()` (line 970-971) returns `False` when `status == 'starting'`
2. `get_active_streams()` (line 994-995) was calling `stop_stream()` for any stream where `is_stream_alive()` returned `False`
3. API endpoints `/api/status` and `/api/streams` call `get_active_streams()`
4. **Result:** Every time the frontend polled for status, streams still initializing were being killed

### Fix Applied

Modified `get_active_streams()` to:
- **NOT stop dead streams** - it now only reports status
- Include streams with `status='starting'` in the response
- Leave dead stream cleanup to the watchdog or explicit stop calls

```python
# Before (broken):
if self.is_stream_alive(camera_serial):
    # add to active
else:
    self.stop_stream(camera_serial)  # <-- RACE CONDITION!

# After (fixed):
if status == 'starting':
    # Include as starting, don't stop
elif self.is_stream_alive(camera_serial):
    # Include as active
# Dead streams: DO NOT stop here
```

### Testing Required

Container restart (`startnvr`) needed to apply this fix.

### Files Modified

| File | Change |
|------|--------|
| `streaming/stream_manager.py` | Fixed `get_active_streams()` race condition |
