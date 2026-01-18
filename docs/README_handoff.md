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

*Last updated: January 18, 2026 16:21 EST*

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Previous Session Context

**See:** `docs/README_project_history.md` for full history

---

## Current Session - January 18, 2026 (02:00-16:21 EST)

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

**Implementation COMPLETED (15:30 EST):**

1. ✅ Added `webrtc_global_settings.enable_dtls` to `cameras.json`
2. ✅ Updated `update_mediamtx_paths.sh` to sync DTLS setting to `mediamtx.yml`
3. ✅ Added `/api/config/streaming` endpoint in `app.py`
4. ✅ Updated `static/js/streaming/stream.js`:
   - Added `getStreamingConfig()` and `isDTLSEnabled()` helpers
   - iOS now checks DTLS before WebRTC attempt
   - Falls back to HLS only if DTLS disabled
5. ✅ Created teaching document: `docs/teachings/README_teaching_DTLS_WebRTC_01_18_2026.md`

**To activate:**

1. Ensure `cameras.json` has `"webrtc_global_settings": { "enable_dtls": true }`
2. Run `./start.sh` (or manually run `./update_mediamtx_paths.sh`)
3. Restart packager: `docker compose restart packager`
4. iOS Safari will now use WebRTC (~200ms latency)

**Commits:**

- `dc19ed6` - Add DTLS/WebRTC teaching document
- `1b97a7a` - Implement DTLS encryption toggle for iOS WebRTC support

---

## Session Continuation - January 18, 2026 (15:50-16:21 EST)

**Branch:** `dtls_webrtc_ios_JAN_18_2026_a` (continued)

### COMPLETED: DTLS Implementation Fixes

After context compaction, continued debugging DTLS/WebRTC issues.

#### 1. iOS Device Detection Fix (16:00 EST)

**Problem:** Mac Safari with Touch Bar was detected as iOS device due to `maxTouchPoints > 1`.

**Solution:** Changed threshold to `maxTouchPoints >= 5` (iPads have 5+ touch points, Touch Bar has 1-2).

**Files modified:**

- `static/js/streaming/stream.js` - Updated `isIOSDevice()` function

**Commit:** `2ff5c72` - Fix iOS detection: distinguish iPad from Mac with Touch Bar

#### 2. WebRTC Mixed Content Fix (16:05 EST)

**Problem:** Browser blocked WHEP fetch requests - page served over HTTPS (8443), WHEP endpoint was HTTP (8889).

**Solution:** Added nginx proxy for WHEP signaling to avoid mixed content issues.

**Files modified:**

- `nginx/nginx.conf` - Added `/webrtc/` location block
- `static/js/streaming/webrtc-stream.js` - Changed WHEP URL to use `window.location.origin`

**Commit:** `9bc52de` - Fix WebRTC mixed content: proxy WHEP through nginx HTTPS

#### 3. Snapshot API Log Spam Fix (16:10 EST)

**Problem:** iOS snapshot polling generating thousands of log lines.

**Solution:** Silenced logs for `/api/snap/` endpoint.

**Files modified:**

- `nginx/nginx.conf` - Added `/api/snap/` location with `access_log off`
- `app.py` - Added `SnapAPIFilter` class to filter werkzeug logs

**Commit:** `d197fc8` - Silence snapshot API logs

#### 4. DTLS/WHEP HTTPS Proxy Fix (16:20 EST)

**Problem:** When `webrtcEncryption: yes` in MediaMTX, WHEP endpoint requires HTTPS connections. nginx was proxying HTTP → MediaMTX rejected with "HTTP request to HTTPS server".

**Solution:** Changed nginx proxy from `http://` to `https://` with SSL verification disabled (MediaMTX uses self-signed cert).

**Files modified:**

- `nginx/nginx.conf` - Changed `proxy_pass http://nvr-packager:8889` to `proxy_pass https://nvr-packager:8889` with `proxy_ssl_verify off`
- `config/cameras.json` - Re-enabled `enable_dtls: true`

**Commit:** `44ead33` - Fix nginx HTTPS proxy for MediaMTX WHEP when DTLS enabled

### Verification

After restart, MediaMTX logs show successful WebRTC sessions with DTLS:

```text
2026/01/18 21:21:43 INF [WebRTC] [session 6a5f3e40] peer connection established, local candidate: host/udp/127.0.0.1/8189, remote candidate: prflx/udp/192.168.10.110/49290
```

### Current State

- DTLS enabled (`webrtcEncryption: yes` in mediamtx.yml)
- nginx proxies HTTPS to MediaMTX WHEP endpoint
- Desktop WebRTC working with encryption
- iOS can now use WebRTC (when DTLS enabled) instead of HLS fallback

---

## Session Continuation - January 18, 2026 (17:00-17:15 EST)

**Branch:** `dtls_webrtc_ios_JAN_18_2026_a` (continued)

### COMPLETED: iOS WebRTC Fix + Force WebRTC Grid Setting

#### 1. DTLS API Bug Fix (17:00 EST)

**Problem:** iOS still using HLS even with DTLS enabled in config.

**Root cause:** `app.py` line 852 accessed `camera_repo.config` which doesn't exist. Should be `camera_repo.cameras_data`. Exception caused fallback to `encryption_enabled: false`.

**Why only iOS affected:** Desktop had bypass logic `|| !isIOSDevice()` allowing WebRTC without DTLS check.

**Fix:** Changed `camera_repo.config` → `camera_repo.cameras_data`

**Commit:** `1480a79` - Fix DTLS config: use cameras_data instead of non-existent config property

**Result:** iOS now achieves ~200ms WebRTC latency in fullscreen!

#### 2. iOS Force WebRTC Grid Mode Setting (17:10 EST)

**Feature:** New experimental toggle to force WebRTC in grid view on iOS (bypasses default 1fps snapshot polling).

**Files modified:**

- `static/js/streaming/stream.js` - Added `isForceWebRTCGridEnabled()` function and grid logic
- `static/js/settings/settings-ui.js` - Added toggle with red warning styling and confirmation modal

**Warning features:**

- Bold red text: "EXPERIMENTAL - USE WITH CAUTION"
- Red-highlighted description box with border
- Confirmation modal listing known issues before enabling
- Only visible on iOS devices

**Commit:** `04d8893` - Add iOS Force WebRTC Grid Mode setting (experimental)

### Key Lesson Learned

**The assumption "iOS can't do WebRTC on LAN without internet" was WRONG.**

iOS Safari CAN use WebRTC on LAN - it just **requires DTLS encryption**. The earlier decision to disable DTLS for "LAN simplicity" only worked for desktop browsers. iOS has a hard requirement for DTLS-SRTP, no exceptions.

---

## TODO List

**Completed this session:**

- [x] Fix iOS detection (Mac Touch Bar vs iPad)
- [x] Fix WebRTC mixed content (nginx WHEP proxy)
- [x] Silence snapshot API logs
- [x] Fix DTLS/WHEP HTTPS proxy issue
- [x] Add fullscreen stream type setting (HLS vs WebRTC toggle in Settings)
- [x] Test iOS WebRTC with DTLS on actual iOS device - **WORKING! ~200ms latency**
- [x] Fix DTLS API bug (`camera_repo.config` → `camera_repo.cameras_data`)
- [x] Add iOS Force WebRTC Grid Mode setting (experimental)

**Commits this session:**

- `1480a79` - Fix DTLS config: use cameras_data instead of non-existent config property
- `04d8893` - Add iOS Force WebRTC Grid Mode setting (experimental)

**Pending:**

- [ ] Test UI health monitoring after container restart
- [ ] Camera 95270001CSHLPO74 RTSP port issue (needs reboot or investigation)

---
