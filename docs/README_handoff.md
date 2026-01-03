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

*Last updated: January 2, 2026 17:54 EST*

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Current Session: January 2, 2026 (13:00-17:54 EST)

### Branch: `sub_main_stream_switching_JAN_2_2026_a`

### Tasks

1. **PTZ Preset Improvements** - Fixed multiple issues with preset loading and execution ✅
2. **Connection Monitoring** - Implemented client-side server disconnect detection (untested)
3. **ONVIF GotoHomePosition** - Added stepper calibration command ✅
4. **Preset Loading Race Conditions** - Fixed browser request queue overflow and retry logic ✅
5. **502 Error Page Enhancement** - Expanded funny messages for 30s+ startup time ✅

---

### 1. PTZ Preset Loading and Execution Fixes

#### Problem 1: Presets Not Loading on Page Load

- **Issue**: Presets only loaded when clicking PTZ buttons
- **Root Cause**: `loadPresets()` only called via `setCurrentCamera()` when PTZ buttons clicked
- **Fix**: Added `loadPresetsForAllCameras()` method called on page init
- **Files Modified**: [`static/js/controllers/ptz-controller.js`](static/js/controllers/ptz-controller.js:438-451)

#### Problem 2: Preset Dropdown Cut Off at Bottom

- **Issue**: Dropdown extended beyond viewport bottom in fullscreen
- **Root Cause**: Dropdown positioned after PTZ grid, extends downward
- **Fix**: Added flexbox to `.fullscreen-ptz-controls` and `order: -1` to move dropdown above grid
- **Files Modified**:
  - [`static/css/components/fullscreen-ptz.css`](static/css/components/fullscreen-ptz.css:11-12)
  - [`static/css/components/ptz-presets.css`](static/css/components/ptz-presets.css:9-13)

#### Problem 3: Preset Selection Not Triggering Camera Selection

- **Issue**: `gotoPreset()` requires `currentCamera` but wasn't set when clicking dropdown
- **Fix**: Modified dropdown change handler to detect camera from parent `.stream-item` and call `setCurrentCamera()` first
- **Files Modified**: [`static/js/controllers/ptz-controller.js`](static/js/controllers/ptz-controller.js:416-435)

#### Problem 4: Preset Movement Interrupted by Stop Command

- **Issue**: Mouseup/touchend event from dropdown click immediately fired `stopMovement()`, interrupting preset
- **Root Cause**: Document-level mouseup handler stops PTZ movement whenever mouse released
- **Fix**: Added `executingPreset` flag to skip `stopMovement()` during preset execution
- **Files Modified**: [`static/js/controllers/ptz-controller.js`](static/js/controllers/ptz-controller.js:12,175-178,195-198,483,504)
- **Console Evidence**:

  ```
  [PTZ] Going to preset: 0 for camera: XCPTP369388MNVTG
  [PTZ] Mouse up - stopping. States: {isExecuting: true}
  [PTZ] Sending stop command for: XCPTP369388MNVTG
  ```

#### Commits

- `8b90842` - Fix preset selection - set current camera before goto
- `024d896` - Load presets for all PTZ cameras on page load
- `559de65` - Fix preset dropdown cutoff in fullscreen
- `8b90ab2` - Show PTZ preset dropdown always (debugging)
- `6598e4e` - Fix PTZ touch handling - stop movement on any touchend

---

### 2. ONVIF GotoHomePosition for Stepper Calibration

#### Problem

- Reolink Living camera lost stepper calibration, needed reset without power cycle

#### Solution

- Added ONVIF `GotoHomePosition` command support
- New direction: `'home'` in DIRECTION_VECTORS
- Triggers camera to return to home position and recalibrate steppers

#### Usage

```bash
curl -X POST http://localhost:5000/api/ptz/XCPTP369388MNVTG/home
```

#### Files Modified

- [`services/onvif/onvif_ptz_handler.py`](services/onvif/onvif_ptz_handler.py:40,120-121,453-481)

#### Commit

- `6598e4e` - Add ONVIF GotoHomePosition for PTZ stepper calibration

---

### 3. Client-Side Connection Monitoring (UNTESTED)

#### Objective

Detect server disconnection/restart and show reloading page instead of error

#### Implementation

**Backend** ([`app.py`](app.py)):

- Added `AppState` class for thread-safe shutdown tracking (lines 70-83)
- Added `/api/health` endpoint:
  - Returns `200 OK` when healthy
  - Returns `503` with `status: 'shutting_down'` when server shutting down
- Modified `cleanup_handler()` to set shutdown flag immediately (line 2199)

**Frontend** ([`static/js/connection-monitor.js`](static/js/connection-monitor.js)):

- Health polling every 5 seconds
- Detects 503 response for immediate redirect
- Tracks 2 consecutive failures → triggers redirect
- Fetch interceptor: 3 consecutive network errors → triggers redirect
- Extensive emoji logging for debugging

**Graceful Degradation**:

1. Try to reach `/reloading` page first (HEAD request, 2s timeout)
2. If accessible: redirect to reloading page
3. If not accessible: show inline modal overlay with auto-retry

**Reloading Page** ([`templates/reloading.html`](templates/reloading.html)):

- Orange theme (vs blue for initial 502)
- Faster retry (3s vs 5s)
- Aggressive polling (1s)
- Stores return URL in localStorage
- Returns to saved page after reconnection

**Offline Modal** (when server completely down):

- Full-screen overlay (z-index 999999)
- Spinner animation
- "Server Unavailable" message
- Auto-retry every 5s
- Manual "Retry Now" button
- Returns to saved URL when recovered

#### User Experience Flow

1. Server shutdown initiated → `AppState.set_shutting_down()`
2. Client polls `/api/health` → Gets 503 response
3. Immediate redirect → Client goes to `/reloading` before connection breaks
4. Reloading page polls → Every 1s checking for server recovery
5. Server back online → User returned to original page

#### Testing Status

**NOT YET TESTED** - waiting for user to test with `docker compose restart`

#### Files Modified

- [`app.py`](app.py:70-83,448-451,2199) - AppState, /api/health, shutdown flag
- [`templates/reloading.html`](templates/reloading.html) - Reconnection page
- [`static/js/connection-monitor.js`](static/js/connection-monitor.js) - Monitoring module
- [`templates/streams.html`](templates/streams.html:203,237) - Initialize monitor

#### Commits

- `5147655` - Add client-side connection monitoring and server shutdown detection
- `8fe343f` - Add extensive logging to connection monitor for debugging
- `1926021` - Move reloading page to Flask template route
- `9725246` - Add fallback modal when reloading page is unreachable

---

### 4. PTZ Preset Loading Race Conditions (RESOLVED)

#### Problem Identified

Multiple PTZ cameras loading presets simultaneously on page load caused:

- Browser connection limits (typically 6 per domain)
- AJAX requests aborted before being sent (`readyState: 0`)
- Error: `{readyState: 0, getResponseHeader: ƒ, ...}`
- Presets eventually loading after multiple retry attempts

#### Root Causes

**Frontend Race Condition:**

- All cameras loading presets simultaneously on page init
- Browser queue overflow causing request abortion
- No retry mechanism for transient failures

**Potential Backend Factors:**

- ONVIF connection creation takes time (synchronous)
- Multiple concurrent ONVIF requests could overwhelm cameras
- Flask app startup timing

#### Solutions Implemented

**1. Request Staggering** ([`ptz-controller.js:470-493`](static/js/controllers/ptz-controller.js#L470-L493))

```javascript
loadPresetsForAllCameras() {
    let delay = 0;
    const staggerMs = 500; // 500ms between each camera

    $('.stream-item').each((index, streamItem) => {
        // ...
        setTimeout(() => {
            this.loadPresets(serial);
        }, delay);
        delay += staggerMs;
    });
}
```

**Benefits:**

- Camera 1: loads immediately (0ms)
- Camera 2: loads after 500ms
- Camera 3: loads after 1000ms
- Prevents browser connection queue overflow

**2. Exponential Backoff Retry** ([`ptz-controller.js:495-533`](static/js/controllers/ptz-controller.js#L495-L533))

```javascript
async loadPresets(cameraSerial, retryCount = 0) {
    const maxRetries = 3;
    const retryDelay = Math.min(1000 * Math.pow(2, retryCount), 5000);

    try {
        // Load presets...
    } catch (error) {
        const isNetworkError = error.readyState === 0 || error.status === 0 || error.status >= 500;

        if (isNetworkError && retryCount < maxRetries) {
            // Retry: 1s, 2s, 4s delays
            setTimeout(() => {
                this.loadPresets(cameraSerial, retryCount + 1);
            }, retryDelay);
        }
    }
}
```

**3. On-Demand Loading Fallback** ([`ptz-controller.js:415-432`](static/js/controllers/ptz-controller.js#L415-L432))

```javascript
$(document).on('click focus', '.ptz-preset-select', (event) => {
    // Check if presets loaded
    if (!presetsForCamera || presetsForCamera.length === 0) {
        this.loadPresets(serial); // Load now if missing
    }
});
```

#### Files Modified

| File | Change |
|------|--------|
| `static/js/controllers/ptz-controller.js` | Staggered loading + retry logic + dropdown click handler |

#### Commits

- `4accb9c` - Add preset dropdown click handler to load missing presets
- `dfa757a` - Add retry logic with exponential backoff for preset loading
- `cadfbc6` - Stagger preset loading to prevent simultaneous request abortion

#### Status

**RESOLVED** - Presets now load reliably on page load with graceful degradation

---

### 5. 502 Error Page Funny Messages Enhancement

#### Problem

Not enough silly messages to sustain typical 10-30 second NVR startup time.

#### Solution

Expanded message array from 20 to 135+ messages:

**Categories Added:**

- Docker/container operations jokes
- Network protocol puns ("Waiting for UDP to maybe show up...")
- Programming recursion/meta humor ("Debugging the debugger...")
- DevOps buzzword satire ("Kubernetes-ing without Kubernetes...")
- Existential contemplation ("Questioning life choices...")
- Encouraging messages ("You're the best...")

**Coverage:**

- Original: 20 messages = 40 seconds before repeating
- Updated: 135+ messages = 270+ seconds (4.5+ minutes) before repeating
- More than enough for typical 10-30s startup

#### Files Modified

| File | Change |
|------|--------|
| [`nginx/502.html`](nginx/502.html:126-262) | Expanded silly messages from 20 to 135+ |

#### Commit

- `c39a960` - Expand 502.html silly loading messages for startup entertainment

---

## Previous Session: January 2, 2026 (02:30-04:01 EST)

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

---

## Audio Timestamp Issue Fix: January 2, 2026 (05:00-05:08 EST)

### Problem Identified

**Symptoms:**

- LL-HLS publishers (FFmpeg) exiting with code 0 after running briefly
- MediaMTX logs showing publishers "torn down" shortly after starting
- Streams would start, publish to MediaMTX, then terminate cleanly

**Root Cause Found:**

Budget Eufy cameras produce audio streams with timestamp discontinuities. When FFmpeg tries to encode audio (AAC) for the dual-output LL-HLS, it encounters:

```
[aost#0:1/aac @ ...] Non-monotonic DTS; previous: X, current: Y
[aac @ ...] Queue input is backward in time
```

After enough timestamp errors, FFmpeg exits cleanly (code 0) instead of crashing.

**Additional Factor:** The dual-output implementation (sub + main streams from single FFmpeg process) was added along with audio support, making the issue harder to isolate.

### Fix Applied

1. **Disabled audio in cameras.json** for all cameras:
   - Changed `ll_hls.audio.enabled` from `true` to `false` for all 17 cameras
   - Audio from budget cameras is unreliable and causes stream instability

2. **Added FFmpeg safeguards** in `streaming/ffmpeg_params.py`:
   - Added `-async 1` to resync audio timestamps (if audio enabled in future)
   - Added `-max_muxing_queue_size 1024` to handle buffer issues

3. **Result:** FFmpeg commands now use `-an` (no audio) for both sub and main outputs:

   ```
   ffmpeg ... -map 0:v:0 ... -an -max_muxing_queue_size 1024 -f rtsp ... /sub
              -map 0:v:0 -c:v copy -an -max_muxing_queue_size 1024 -f rtsp ... /main
   ```

### Testing Observations

After fix:

- STAIRS FFmpeg log shows 87+ seconds of continuous encoding at 15fps
- No audio-related errors in FFmpeg output
- Publishers still being torn down periodically (separate issue - likely segment buffer or motion detector connections)

### Files Modified

| File | Change |
|------|--------|
| `config/cameras.json` | Disabled `ll_hls.audio.enabled` for all 17 cameras |
| `streaming/ffmpeg_params.py` | Added `-async 1` and `-max_muxing_queue_size 1024` for audio resilience |

### Lessons Learned

1. **Audio from budget cameras is problematic** - timestamp issues cause FFmpeg to exit cleanly
2. **Test one change at a time** - dual-output + audio were added together, making root cause harder to find
3. **Exit code 0 doesn't mean success** - FFmpeg exits cleanly when it decides to stop due to errors

---

## Current Session: January 2, 2026 (19:00-19:51 EST)

### Branch: `sub_main_stream_switching_JAN_2_2026_a`

### Task: Fix PTZ Controls Toggle Button Bug

#### Problem Identified

PTZ controls were not showing when toggled, despite JavaScript correctly adding the `.ptz-visible` class. Investigation revealed:

1. **Root Cause**: PTZ controls HTML was nested inside the `.stream-controls` div
2. **Impact**: When `.stream-controls` had `display: none`, all children (including PTZ controls) were also hidden
3. **Symptom**: PTZ controls only appeared when stream controls toggle was active

#### Files Modified

<!-- markdownlint-disable MD060 -->
| File | Change |
|------|--------|
| [`templates/streams.html`](templates/streams.html#L121-L168) | Moved `.ptz-controls` div outside of `.stream-controls` div (lines 121-168) |
| [`static/css/components/ptz-controls.css`](static/css/components/ptz-controls.css#L5-L16) | Added positioning CSS: absolute, bottom, z-index 21 (lines 5-16) |
<!-- markdownlint-enable MD060 -->

#### Solution Implemented

**1. HTML Structure Fix** ([templates/streams.html:121-168](templates/streams.html#L121-L168))

Changed PTZ controls from being a child of `.stream-controls` to a sibling:

```html
<!-- Before: PTZ inside stream-controls -->
<div class="stream-controls">
    <div class="control-row">...</div>
    <div class="ptz-controls">...</div> <!-- Nested! -->
</div>

<!-- After: PTZ as sibling -->
<div class="stream-controls">
    <div class="control-row">...</div>
</div>
<div class="ptz-controls">...</div> <!-- Independent! -->
```

**2. CSS Positioning** ([ptz-controls.css:5-16](static/css/components/ptz-controls.css#L5-L16))

Added positioning properties PTZ controls needed when moved out:

```css
.ptz-controls {
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    padding: 0.75rem;
    background: linear-gradient(to top, rgba(0, 0, 0, 0.7) 0%, rgba(0, 0, 0, 0) 100%);
    z-index: 21; /* Above stream controls (z-index: 20) */
    display: none; /* Hidden by default */
}
```

#### Commits

- `9d539dd` - Restore PTZ toggle icon to arrows (fa-arrows-alt)
- `a9089ec` - Restore stream controls toggle button functionality
- `d8fa0b5` - Fix PTZ controls being hidden when stream controls are hidden
- `b5908a4` - Add positioning CSS to PTZ controls after moving them out of stream-controls

#### Result

✅ Both toggle buttons now work independently:

- **PTZ Toggle** (fa-arrows-alt) → Shows/hides PTZ directional controls
- **Stream Controls Toggle** (fa-sliders-h) → Shows/hides start/stop/refresh buttons
- Both can be shown simultaneously or separately
- PTZ controls appear above stream controls when both are visible (z-index stacking)

---

*Last updated: January 2, 2026 19:51 EST*
