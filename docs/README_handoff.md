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

*Last updated: January 5, 2026 04:15 EST*

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Previous Session Summary

**Branch merged to main:** `docs_update_JAN_4_2026_a`

---

## Current Session

**Branch:** `mjpeg_status_fix_JAN_4_2026_b`
**Date:** January 5, 2026 (03:30 - 04:15 EST)

**Context compaction occurred at 03:30 EST** - Continued from `ui_performance_JAN_5_2026_b`

### What Was Accomplished This Session

1. **Reduced FFmpeg analyzeduration/probesize** for two cameras:
   - Office Desk (T8416P0023370398): 1000000 → 500000
   - REOLINK OFFICE (95270001CSO4BPDZ): 1000000 → 500000
   - Purpose: Test if lower analysis time reduces WebRTC latency

2. **WebRTC Latency Testing Results:**
   - Observed: ~6s initially, ~2s after warmup
   - Expected: ~200ms
   - Pixelization and random pauses observed
   - UniFi Office Kitchen: Buggy with WebRTC, freezes

3. **Research Findings Documented:**
   - WebRTC ~2s latency floor due to FFmpeg transcoding requirement
   - Single-connection constraint on budget cameras forces transcoding
   - 200ms/100ms HLS segments only work with direct RTSP passthrough
   - Neolink is protocol translator only (Baichuan ↔ RTSP), cannot output WebRTC
   - UniFi Office Kitchen worked best with LL_HLS per project history

4. **UniFi MJPEG Investigation:**
   - User set camera 68d49398005cf203e400043f to MJPEG - it doesn't work
   - Root cause: `USE_PROTECT=1` env var causes `get_snapshot()` to return None
   - Code at [unifi_protect_service.py:87-89](services/unifi_protect_service.py#L87-L89)
   - **Historical context found:** FFmpeg couldn't process Protect's RTSP ("Invalid data")
   - Protect's snapshot API (`/proxy/protect/api/cameras/{id}/snapshot`) was suggested as "Next Steps Required" in Sept 2025 but **was never implemented**

### Key Discovery: UniFi MJPEG Broken Due to Missing Implementation

**History (September 24, 2025 - see README_project_history.md lines 1480-1510):**

1. FFmpeg couldn't parse Protect's RTSPS stream → "Invalid data found when processing input"
2. `get_snapshot()` also failed with same error (it uses FFmpeg to extract frames)
3. Three options were proposed:
   - Option A: Proxy Protect's native HLS streams
   - Option B: GStreamer instead of FFmpeg
   - Option C: Use Protect's snapshot API for MJPEG
4. "Next Steps Required" listed implementing Protect snapshot API - **never done**
5. `USE_PROTECT=true` bypass was added as temporary workaround

**To fix UniFi MJPEG:** Implement Protect snapshot API in `get_snapshot()`:
```python
# Instead of FFmpeg-based snapshot:
snapshot_url = f"https://{protect_host}/proxy/protect/api/cameras/{camera_id}/snapshot"
# Requires authentication token from Protect API
```

---

## TODO List

**Immediate (Next Session):**

- [ ] Implement Protect snapshot API in UniFiProtectService.get_snapshot()
- [ ] Switch UniFi Office Kitchen back to LL_HLS (WebRTC buggy for this camera)
- [ ] Add blank-frame detection to health monitor (new monitor doesn't detect browser-side black frames)

**Future Enhancements:**

- [ ] Add ICE state monitoring to health.js for better WebRTC health detection
- [ ] Consider STUN server for remote access (currently LAN-only)
- [ ] Test fullscreen main/sub stream switching with WEBRTC

---

## Key Files Reference

- [unifi_protect_service.py:87-89](services/unifi_protect_service.py#L87-L89) - USE_PROTECT bypass returning None
- [unifi_mjpeg_capture_service.py](services/unifi_mjpeg_capture_service.py) - MJPEG capture service
- [app.py:949-1003](app.py#L949-L1003) - UniFi MJPEG streaming endpoint
- [README_project_history.md:1480-1510](docs/README_project_history.md#L1480-L1510) - Historical context on FFmpeg/Protect issues

---
