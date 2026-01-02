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

*Last updated: January 2, 2026 03:18 EST*

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Current Session: January 2, 2026 (02:30-03:18 EST)

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

## Pending Tasks

1. **Audio support** - User explicitly requested this as next feature

2. **Optional future**: Rename `stream_type` to `protocol` in cameras.json (deferred)

3. **Testing needed**: Verify dual-output FFmpeg works in practice
   - Run `./update_mediamtx_paths.sh` to create both paths in MediaMTX
   - Restart NVR with `startnvr`
   - Test fullscreen switching on LL_HLS/NEOLINK cameras

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

## Custom 502 Error Page (06:15-07:45 EST)

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

### Result

✅ Custom error page displays when NVR backend is down, with fun messages and auto-retry
