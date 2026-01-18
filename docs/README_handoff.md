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

*Last updated: January 18, 2026 15:15 EST*

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Previous Session Context

**See:** `docs/README_project_history.md` for full history

---

## Current Session - January 18, 2026 (02:00-15:05 EST)

**Branch:** `dtls_webrtc_ios_JAN_18_2026_a` (merged from `ui_health_enable_JAN_18_2026_a`)

### COMPLETED: Force MJPEG Setting + UI Health Re-enable

#### 1. Force MJPEG Desktop Setting

Added checkbox in Settings panel to force MJPEG mode for desktop users:

**Files modified:**

- `static/js/settings/settings-ui.js` - Added Force MJPEG toggle (hidden on mobile)
- `static/js/streaming/stream.js` - Added `isForceMJPEGEnabled()` check
- `app.py` - Index route preserves `?forceMJPEG=true` query param

**Behavior:**

- Toggle redirects to `/streams?forceMJPEG=true`
- Stored in localStorage for persistence
- Hidden on portable devices (they always use MJPEG in grid)

#### 2. Expanded Modal Mode (Cherry-picked)

Restored expanded modal feature from another branch:

**Files affected:**

- `static/css/components/fullscreen.css` - `.expanded` and `.expanded-backdrop` classes
- `static/css/components/stream-item.css` - Grid mode button hiding
- `templates/streams.html` - `#expanded-backdrop` div
- `static/js/streaming/stream.js` - `expandCamera()` and `collapseExpandedCamera()` methods

**Behavior:**

- Grid mode: All buttons hidden (clean look)
- Tap camera → Expanded modal with buttons visible
- Fullscreen: All buttons visible

#### 3. UI Health Monitoring Re-enabled

**Problem:** Black streams not being detected/refreshed automatically. Manual HLS refresh works.

**Solution:** Enabled UI health with tuned thresholds in `config/cameras.json`:

| Setting | Old | New | Effect |
|---------|-----|-----|--------|
| ENABLED | false | **true** | Activates detection |
| CONSECUTIVE_BLANK | 100 | **3** | Detect in 6s (was 300s!) |
| STALE_AFTER_MS | 30000 | 10000 | 10s stale detection |
| WARMUP_MS | 60000 | 15000 | Start monitoring sooner |

#### 4. Camera RTSP Issue Diagnosed

Camera `95270001CSHLPO74` (Terrace South) stays in "starting" mode with WEBRTC.

**Root cause:** RTSP port not responding (camera-level issue, not NVR).

- Ping works, HTTP MJPEG works, but `rtsp://192.168.10.89:554/...` times out
- Sister camera `95270001CSO4BPDZ` (same model/firmware) works fine
- **Workaround:** Set to MJPEG in cameras.json

#### 5. iOS Snapshot Polling Implementation

**Problem:** MJPEG streams don't work reliably on iOS Safari (multipart parsing issues).

**Solution:** Implemented snapshot polling for iOS grid view:

**New files:**

- `static/js/streaming/snapshot-stream.js` - SnapshotStreamManager class
  - Polls `/api/snap/<camera_id>` every 1 second
  - Single HTTP request per snapshot (no long-lived connections)
  - Works reliably on iOS Safari

**Modified files:**

- `app.py` - Added `/api/snap/<camera_id>` endpoint
  - Checks reolink, unifi, mediaserver frame buffers
  - Returns latest cached JPEG frame
- `static/js/streaming/stream.js` - iOS detection and snapshot mode
  - iOS in grid view → uses SNAPSHOT (not MJPEG)
  - Fullscreen → switches to HLS for audio/quality
  - Proper pause/resume for snapshot streams

**Behavior by device/mode:**

| Device | Grid View | Expanded Modal | Fullscreen |
|--------|-----------|----------------|------------|
| **iOS** | Snapshots (1s polling) | Snapshots | HLS main stream |
| **Android** | MJPEG | MJPEG | HLS main stream |
| **Desktop** | HLS/WebRTC | HLS/WebRTC | HLS main stream |

**Fullscreen resource management:**

- When entering fullscreen, all other streams are paused (including snapshot polling)
- When exiting fullscreen, paused streams resume automatically
- This saves bandwidth/CPU during fullscreen viewing

#### 6. PTZ Controls Bug Fix

**Bug:** Opening PTZ controls in expanded modal, then collapsing back to grid view left PTZ controls visible and blocking all interaction.

**Files modified:**

- `static/js/streaming/stream.js` - `collapseExpandedCamera()` now hides PTZ and stream controls
- `static/css/components/ptz-controls.css` - Added rule to force-hide in grid mode

#### 7. Stream Controls Bug Fix

**Bug:** Stream controls (play/stop/refresh/restart) showing in grid view after hard refresh.

**Files modified:**

- `static/css/components/stream-controls.css` - Added rule to force-hide in grid mode

**CSS Rules Added:**

```css
/* PTZ controls - hidden in grid (non-expanded, non-fullscreen) */
.stream-item:not(.css-fullscreen):not(.expanded) .ptz-controls {
    display: none !important;
}

/* Stream controls - hidden in grid (non-expanded, non-fullscreen) */
.stream-item:not(.css-fullscreen):not(.expanded) .stream-controls {
    display: none !important;
}
```

#### 8. iOS Pagination Disabled

**Problem:** iOS was showing only 6 cameras per page with pagination controls.

**Root cause:** Pagination was designed for iOS video decode limits when using HLS/MJPEG.

**Solution:** Disabled pagination since snapshots use `<img>` tags (no video decode limit).

**Files modified:**

- `static/js/streaming/stream.js` - Set `iosPagination.enabled = false`

#### 9. Grid View Detection Fix

**Problem:** `isGridView` check was looking for non-existent `fullscreen-stream` class.

**Solution:** Changed to check for `css-fullscreen` class instead.

**Files modified:**

- `static/js/streaming/stream.js` - Fixed: `!$streamItem.hasClass('css-fullscreen')`

---

## Architecture Summary: iOS Mobile Streaming

```text
┌─────────────────────────────────────────────────────────────────┐
│                      iOS STREAMING FLOW                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  PAGE LOAD (Grid View)                                          │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ isIOSDevice() = true                                     │   │
│  │ isGridView = true (not css-fullscreen)                   │   │
│  │ → streamType = 'SNAPSHOT'                                │   │
│  │ → Swap <video> for <img class="stream-snapshot-img">     │   │
│  │ → snapshotManager.startStream() polls /api/snap/         │   │
│  └─────────────────────────────────────────────────────────┘   │
│                           │                                     │
│                           ▼                                     │
│  TAP TO EXPAND (Modal View)                                     │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ .expanded class added                                    │   │
│  │ → Still SNAPSHOT mode (no stream change)                 │   │
│  │ → Buttons become visible (CSS override)                  │   │
│  │ → PTZ/controls available if toggled                      │   │
│  └─────────────────────────────────────────────────────────┘   │
│                           │                                     │
│                           ▼                                     │
│  FULLSCREEN BUTTON (Full Screen View)                           │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ .css-fullscreen class added                              │   │
│  │ → Stop snapshot polling                                  │   │
│  │ → Restore <video> element                                │   │
│  │ → Start HLS main stream (high quality + audio)           │   │
│  │ → PAUSE all other cameras' snapshot polling              │   │
│  └─────────────────────────────────────────────────────────┘   │
│                           │                                     │
│                           ▼                                     │
│  EXIT FULLSCREEN                                                │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Remove .css-fullscreen class                             │   │
│  │ → Stop HLS stream                                        │   │
│  │ → Create <img> element                                   │   │
│  │ → Restart snapshot polling                               │   │
│  │ → RESUME all paused cameras' snapshot polling            │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## TODO List

**Completed:**

- [x] **iOS grid: Use static snapshots, not MJPEG** - Implemented snapshot polling
- [x] Grid mode: All cams low-res snaps (refresh every ~1s)
- [x] Fullscreen: HLS on iOS (WebRTC not possible due to DTLS requirement)
- [x] Force MJPEG setting should NOT default for iOS (iOS uses snapshots now)
- [x] Fix PTZ controls blocking grid after modal collapse
- [x] Fix stream controls showing in grid mode
- [x] Disable iOS pagination (not needed with snapshots)
- [x] Fix isGridView detection (css-fullscreen class)
- [x] Snapshot pause/resume during fullscreen

**Pending:**

- [ ] Test UI health monitoring after container restart
- [ ] Verify black stream detection triggers refresh
- [ ] Test iOS snapshot implementation on actual iOS device

**Remaining Issues:**

- [ ] Camera 95270001CSHLPO74 RTSP port issue (needs reboot or investigation)

---

## Git Status

**Current branch:** `ui_health_enable_JAN_18_2026_a`

**Commits this session:**

- `8ecf4a9` - Enable UI health monitoring with tuned thresholds
- `c85f824` - Add expanded modal mode for camera grid (cherry-picked)
- `7e10b87` - Add Force MJPEG setting for desktop users
- `8b445fa` - Implement iOS snapshot polling for grid view
- `0dcc043` - Fix PTZ controls blocking grid view after modal collapse
- `952f370` - Update handoff documentation with iOS snapshot and PTZ fix
- `339933e` - Hide stream controls in grid mode (non-expanded, non-fullscreen)
- `af44dde` - Fix iOS: disable pagination, fix grid view detection
- `adaefcc` - Update architecture documentation with iOS mobile streaming

---

## Session Wrap-up Notes (15:05 EST)

### Final Discussion: iOS WebRTC Latency

**Why iOS uses HLS instead of WebRTC:**

- MediaMTX configured with `webrtcEncryption: no` for LAN-only deployment
- iOS Safari *requires* DTLS-SRTP encryption for WebRTC
- Result: iOS falls back to HLS (~2-4s latency) instead of WebRTC (~200ms)

**DTLS Latency Impact (if enabled):**

- **Backend/transport layer** overhead, not frontend
- Handshake: ~100-200ms one-time at connection establishment
- Ongoing: ~10-20ms per frame for encryption/decryption
- Network overhead: ~16 bytes extra per RTP packet

**Trade-off analysis:**

- Current: iOS uses HLS = 2-4 seconds latency
- With DTLS: iOS could use WebRTC = ~200ms latency
- Net gain: 2-4 seconds improvement for iOS users

### CLAUDE.md Updated

Added **RULE 3: Teaching Sessions** to project instructions:

- Create teaching documents for significant implementations
- Store in `docs/teachings/` directory
- Maintain catalog for reference

### Next Branch: `dtls_webrtc_ios_JAN_18_2026_a`

**Objective:** Enable DTLS in MediaMTX for iOS WebRTC support

**Implementation plan:**

1. Add `enable_dtls` setting to `cameras.json` (source of truth)
2. Update MediaMTX configuration generator to conditionally enable DTLS
3. Update frontend to detect DTLS support and use WebRTC on iOS
4. Create teaching document explaining DTLS/WebRTC architecture

---
