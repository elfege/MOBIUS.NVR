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

*Last updated: January 18, 2026 04:38 EST*

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Previous Session Context

**See:** `docs/README_project_history.md` for full history

---

## Current Session - January 18, 2026 (02:00-04:38 EST)

**Branch:** `ui_health_enable_JAN_18_2026_a`

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
- Grid mode: All buttons hidden
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

**Behavior:**

- iOS grid: Static snapshots refreshing every 1s
- iOS fullscreen: HLS main stream (with audio)
- Android grid: MJPEG (works fine on Android)
- Desktop: Normal HLS/WebRTC

#### 6. PTZ Controls Bug Fix

**Bug:** Opening PTZ controls in expanded modal, then collapsing back to grid view left PTZ controls visible and blocking all interaction.

**Files modified:**

- `static/js/streaming/stream.js` - `collapseExpandedCamera()` now hides PTZ
- `static/css/components/ptz-controls.css` - Grid view hides PTZ controls

---

## TODO List

**Completed:**

- [x] **iOS grid: Use static snapshots, not MJPEG** - Implemented snapshot polling
- [x] Grid mode: All cams low-res snaps (refresh every ~1s)
- [x] Fullscreen: HLS on iOS (WebRTC not possible due to DTLS requirement)
- [x] Force MJPEG setting should NOT default for iOS (iOS uses snapshots now)
- [x] Fix PTZ controls blocking grid after modal collapse

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

---
