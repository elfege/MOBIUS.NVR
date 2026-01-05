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

*Last updated: January 5, 2026 14:39 EST*

Always read `CLAUDE.md` in case I updated it in between sessions.

---

## Current Session

**Branch:** `reolink_aio_stability_JAN_5_2026_b`
**Date:** January 5, 2026 (14:39-ongoing EST)

**Context compaction occurred at 14:39 EST** - Continuing from `_a` branch.

### Work Completed in `_a` Branch

1. **reolink_aio AttributeError fix** - Defensive cleanup for `_transport.close()` errors
2. **UnexpectedDataError exponential backoff** - Reduces log spam for transient camera errors
3. **E1 Latency Passthrough Support** - Modified `streaming/ffmpeg_params.py` to support main stream passthrough

### Issue 3: E1 Latency (~7-8 seconds behind native app)

**Root Cause:** FFmpeg re-encoding the main stream adds ~2-3 seconds of latency.

**Solution Implemented:** Added passthrough mode to `build_ll_hls_dual_output_publish_params()`:

- Detects `video_main.c:v == "copy"` in camera config
- In passthrough mode: main stream maps directly from input (no transcode)
- Sub stream still transcoded for grid thumbnails
- Commit: `7d46da4`

**Next Step:** User needs to update E1's `cameras.json` to test passthrough:

```json
"video_main": {
  "c:v": "copy"
}
```

---

### Previous Issues (from `_a` branch)

#### Issue 1: reolink_aio AttributeError on _transport.close()

**Error logs:**

```text
ERROR:services.motion.reolink_motion_service:Error monitoring Living_REOLINK: 'NoneType' object has no attribute 'close'
AttributeError: 'NoneType' object has no attribute 'close'
```

**Root Cause:** `reolink_aio` library calls `self._transport.close()` during logout, but `_transport` can be `None` if:

1. Connection was never fully established
2. Connection was already closed

**Fix Applied:** Added defensive checks in `services/motion/reolink_motion_service.py`:

- Check if `host.baichuan` exists before calling `unsubscribe_events()`
- Wrap `logout()` calls in try/except for partially initialized hosts
- Same pattern applied to `_cleanup_all()` method
- Downgraded cleanup errors from ERROR to DEBUG level

### Issue 2: UnexpectedDataError response mismatch

**Error logs:**

```text
reolink_aio.exceptions.UnexpectedDataError: Host 192.168.10.88:443 error mapping responses to requests, received 1 responses while requesting 20 responses
```

**Affected Cameras:**

- REOLINK OFFICE (95270001CSO4BPDZ) - 192.168.10.88
- Terrace South (95270001CSHLPO74) - 192.168.10.89 (same model/firmware)

**Root Cause:** Camera responds with fewer items than `reolink_aio` requested during `get_host_data()`. Common transient error when camera is under load or network has latency.

**Fix Applied:** Added exponential backoff specifically for `UnexpectedDataError`:

- Import `UnexpectedDataError` from `reolink_aio.exceptions`
- Track consecutive data errors separately
- Double retry delay on each occurrence (10s → 20s → 40s... up to 5min max)
- Reduce log verbosity: WARNING for first 3 errors, then DEBUG
- Reset backoff counters on successful connection or different error type

### Commits

- `c1fef1e` - Add defensive cleanup for reolink_aio Baichuan connections (merged to main)
- `5491e25` - Add exponential backoff for reolink_aio UnexpectedDataError

---

## Previous Session Reference

**Branch merged:** `fix_e1_stream_restart_btn_JAN_5_2026_a`
**Date:** January 5, 2026 (10:15-13:58 EST)

See `docs/README_project_history.md` for full session details including:

- E1 stream restart button implementation
- PTZ capability check fix (prevents PTZ attempts on non-PTZ cameras)
- E1 PTZ investigation results (reolink_aio doesn't support E1 PTZ)

Archived handoff: `docs/archive/handoffs/fix_e1_stream_restart_btn_JAN_5_2026_a/README_handoff_20260105_1358.md`

---

## TODO List

**Future Enhancements:**

- [ ] Research Neolink MQTT PTZ for E1 camera (direct reolink_aio doesn't work)
- [ ] Add `max_rtsp_connections` field to cameras.json for direct passthrough support
- [ ] Add blank-frame detection to health monitor
- [ ] Add ICE state monitoring to health.js
- [ ] Consider STUN server for remote access
- [ ] Test fullscreen main/sub stream switching with WEBRTC
- [ ] Investigate watchdog cooldown clearing bug
- [ ] Investigate UI freezes (health monitor canvas ops at 2s per camera)

---
