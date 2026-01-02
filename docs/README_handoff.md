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

*Last updated: January 2, 2026 02:59 EST*

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Current Session: January 2, 2026 (02:30-02:59 EST)

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

## Design Decision: One Stream Per Camera

**User Decision**: Stop existing stream before starting main stream. Never have more than one stream per camera.

This is **simpler** than the MJPEG dual-stream approach:
- Grid view starts sub stream
- Entering fullscreen: stop sub, start main
- Exiting fullscreen: stop main, start sub
- No need for dual-key tracking

### Implications

1. **Backend already supports this** - `stream_manager.py` changes allow starting main stream with same camera_serial
2. **Frontend needs update** - `openFullscreen()` must call API to switch streams
3. **Brief interruption acceptable** - User will see loading state while stream switches

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

### Option: Stream Switching on Fullscreen

```javascript
// In fullscreen.js or stream.js

async function openFullscreen(cameraId, videoElement) {
    // 1. Stop current sub stream
    await fetch(`/api/stream/stop/${cameraId}`, { method: 'POST' });

    // 2. Show loading indicator
    showLoadingIndicator(videoElement);

    // 3. Start main stream
    await hlsManager.startStream(cameraId, videoElement, 'main');

    // 4. Enter fullscreen
    requestFullscreen(videoElement.parentElement);
}

async function exitFullscreen(cameraId, videoElement) {
    // 1. Stop main stream
    await fetch(`/api/stream/stop/${cameraId}`, { method: 'POST' });

    // 2. Restart sub stream
    await hlsManager.startStream(cameraId, videoElement, 'sub');
}
```

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

## Pending Tasks

1. **Frontend stream switching** (in progress)
   - Update `openFullscreen()` to stop sub, start main
   - Update fullscreen exit handler to stop main, start sub
   - Add loading state during stream switch

2. **Audio support** (after stream switching)
   - User explicitly requested this as next feature

3. **Optional future**: Rename `stream_type` to `protocol` in cameras.json (deferred)

---

## Files Modified This Session

| File | Change |
|------|--------|
| `streaming/stream_manager.py` | Renamed `stream_type` → `resolution`, added resolution param support |
| `app.py` | Pass `resolution` parameter to `start_stream()` |
| `static/js/streaming/hls-stream.js` | Moved latency badge from top-right to bottom-left |
| `docs/README_project_history.md` | Added January 2 session documentation |
| `docs/README_handoff.md` | This file |

---

## Commits

- Branch: `sub_main_stream_switching_JAN_2_2026_a`
- Backend changes committed
- Frontend changes pending

---

## Key Terminology Clarification

| Term | Location | Meaning |
|------|----------|---------|
| `stream_type` | `cameras.json` | Protocol: HLS, LL_HLS, MJPEG, NEOLINK, RTMP |
| `resolution` | `stream_manager.py` | Quality: 'sub' (low-res) or 'main' (high-res) |
| `stream_key` | Internal | Dictionary key for tracking active streams |
