---
title: "Recording System Implementation - Session Handoff"
layout: default
---

# Recording System Implementation - Session Handoff

**Date:** November 15, 2025  
**Status:** Partially Complete - Manual Recording Works, Auto-Services Pending  
**Next Session:** Implement Auto-Start Services + Fix Race Conditions

---

## Quick Status

âœ… **Working:**

- Settings modal saves/loads correctly
- Manual recording button works for RTSP/MediaMTX cameras
- Flask API routes functional

âŒ **Not Working:**

- MJPEG service recording (shows warning)
- Continuous recording (enabled but doesn't auto-start)
- Snapshots (enabled but doesn't capture)
- Motion detection (skeleton only)

---

## Critical Issues to Fix First

### 1. Add 'manual' Recording Type to StorageManager

**File:** `~/0_NVR/services/recording/storage_manager.py`

**Current code (~line 45):**

```python
def generate_recording_path(self, camera_id: str, recording_type: str = "motion") -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if recording_type == "snapshot":
        filename = f"{camera_id}_{timestamp}.jpg"
        return self.snapshots_path / filename
    else:
        filename = f"{camera_id}_{timestamp}.mp4"
```

**Fix needed:**

```python
def generate_recording_path(self, camera_id: str, recording_type: str = "motion") -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    if recording_type == "snapshot":
        filename = f"{camera_id}_{timestamp}.jpg"
        return self.snapshots_path / filename
    elif recording_type == "manual":
        filename = f"{camera_id}_{timestamp}.mp4"
        return self.manual_path / filename  # New directory
    elif recording_type == "continuous":
        filename = f"{camera_id}_{timestamp}.mp4"
        return self.continuous_path / filename
    else:  # motion
        filename = f"{camera_id}_{timestamp}.mp4"
        return self.motion_path / filename
```

**Also add in `__init__`:**

```python
self.manual_path = self.base_path / 'manual'
self.manual_path.mkdir(parents=True, exist_ok=True)
```

---

### 2. Implement Race Condition Prevention

**File:** `~/0_NVR/services/recording/recording_service.py`

**Add before starting any recording:**

```python
def _check_recording_conflict(self, camera_id: str, recording_type: str) -> bool:
    """
    Check if camera already has active recording of this type.
    
    Returns:
        True if conflict exists, False if safe to record
    """
    with self.recording_lock:
        for rec_id, metadata in self.active_recordings.items():
            if (metadata['camera_id'] == camera_id and 
                metadata['recording_type'] == recording_type):
                logger.warning(
                    f"Recording conflict: {camera_id} already has active "
                    f"{recording_type} recording: {rec_id}"
                )
                return True
    return False
```

**Use in start methods:**

```python
def start_manual_recording(self, camera_id: str, duration: int = 30):
    # Add after camera validation
    if self._check_recording_conflict(camera_id, 'manual'):
        logger.error(f"Camera {camera_id} already has active manual recording")
        return None
    
    # ... rest of method
```

---

### 3. Fix MJPEG Service Recording

**File:** `~/0_NVR/services/recording/recording_service.py`

**Current (line ~260):**

```python
def _start_mjpeg_recording(self, camera_id: str, output_path: Path, duration: int) -> bool:
    logger.warning(f"MJPEG service recording not yet implemented for {camera_id}")
    return False
```

**Implementation needed:**

```python
def _start_mjpeg_recording(self, camera_id: str, output_path: Path, duration: int) -> bool:
    """Record from MJPEG capture service buffer"""
    try:
        # Get MJPEG service based on camera type
        camera = self.camera_repo.get_camera(camera_id)
        camera_type = camera.get('type', '').lower()
        
        if camera_type == 'amcrest':
            from services.amcrest_mjpeg_capture_service import amcrest_mjpeg_capture_service
            capture_service = amcrest_mjpeg_capture_service
        elif camera_type == 'reolink':
            from services.reolink_mjpeg_capture_service import reolink_mjpeg_capture_service
            capture_service = reolink_mjpeg_capture_service
        elif camera_type == 'unifi':
            from services.unifi_mjpeg_capture_service import unifi_mjpeg_capture_service
            capture_service = unifi_mjpeg_capture_service
        else:
            logger.error(f"No MJPEG capture service for camera type: {camera_type}")
            return False
        
        # Start thread to capture frames and write to MP4
        # TODO: Implement frame capture loop
        # - Get frames from capture_service.get_latest_frame(camera_id)
        # - Use OpenCV or FFmpeg to write MP4
        # - Run for 'duration' seconds
        
        logger.warning(f"MJPEG recording implementation incomplete for {camera_id}")
        return False
        
    except Exception as e:
        logger.error(f"MJPEG recording error for {camera_id}: {e}")
        return False
```

---

### 4. Auto-Start Continuous Recording

**File:** `~/0_NVR/app.py`

**Current initialization (~line 94):**

```python
# Recording service
try:
    recording_service = RecordingService(
        camera_repo,
        config_path='./config/recording_settings.json'
    )
    print("âœ… Recording service initialized")
except Exception as e:
    print(f"âš ï¸  Recording service initialization failed: {e}")
    recording_service = None
```

**Add after initialization:**

```python
# Auto-start continuous recordings
if recording_service:
    print("ðŸŽ¬ Auto-starting enabled recordings...")
    
    for camera_id in camera_repo.cameras.keys():
        try:
            camera = camera_repo.get_camera(camera_id)
            camera_name = camera.get('name', camera_id)
            
            # Start continuous recording if enabled
            if recording_service.config.is_recording_enabled(camera_id, 'continuous'):
                if recording_service.start_continuous_recording(camera_id):
                    print(f"  âœ… Continuous: {camera_name}")
                else:
                    print(f"  âŒ Failed continuous: {camera_name}")
            
            # TODO: Start motion detection if enabled
            # TODO: Start snapshot service if enabled
            
        except Exception as e:
            print(f"  âŒ Error starting services for {camera_id}: {e}")
```

---

### 5. Implement Snapshot Service

**New file:** `~/0_NVR/services/snapshot_service.py`

```python
"""
Snapshot Service
Captures periodic JPEG snapshots from camera streams.
"""

import threading
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class SnapshotService:
    """Periodic snapshot capture service"""
    
    def __init__(self, camera_repo, storage_manager, recording_config):
        self.camera_repo = camera_repo
        self.storage = storage_manager
        self.config = recording_config
        self.active_timers: Dict[str, threading.Timer] = {}
        self.stop_flags: Dict[str, bool] = {}
    
    def start_snapshots(self, camera_id: str) -> bool:
        """Start periodic snapshots for camera"""
        try:
            if not self.config.is_recording_enabled(camera_id, 'snapshots'):
                return False
            
            camera_cfg = self.config.get_camera_config(camera_id)
            interval = camera_cfg.get('snapshots', {}).get('interval_sec', 300)
            
            logger.info(f"Starting snapshots for {camera_id} every {interval}s")
            
            self.stop_flags[camera_id] = False
            self._schedule_next_snapshot(camera_id, interval)
            return True
            
        except Exception as e:
            logger.error(f"Failed to start snapshots for {camera_id}: {e}")
            return False
    
    def _schedule_next_snapshot(self, camera_id: str, interval: int):
        """Schedule next snapshot capture"""
        if self.stop_flags.get(camera_id, False):
            return
        
        timer = threading.Timer(interval, self._capture_snapshot, args=(camera_id, interval))
        timer.daemon = True
        timer.start()
        self.active_timers[camera_id] = timer
    
    def _capture_snapshot(self, camera_id: str, interval: int):
        """Capture single snapshot"""
        try:
            # TODO: Implement actual snapshot capture
            # Options:
            # 1. FFmpeg -frames:v 1 from RTSP
            # 2. Grab from MJPEG service buffer
            # 3. Extract from HLS playlist
            
            logger.debug(f"Snapshot captured for {camera_id}")
            
            # Schedule next
            self._schedule_next_snapshot(camera_id, interval)
            
        except Exception as e:
            logger.error(f"Snapshot capture error for {camera_id}: {e}")
    
    def stop_snapshots(self, camera_id: str):
        """Stop snapshots for camera"""
        self.stop_flags[camera_id] = True
        if camera_id in self.active_timers:
            self.active_timers[camera_id].cancel()
            del self.active_timers[camera_id]
```

---

## Architecture Notes

### Recording Type Priorities

When multiple recording types are active for same camera:

1. **Manual** - Highest priority (user override)
2. **Motion** - Event-triggered
3. **Continuous** - Background 24/7

**Conflict Resolution:**

- Allow manual + continuous simultaneously (different dirs)
- Block manual if manual already active
- Block motion if motion already active
- Continuous runs independently (long-duration, restarts on segment end)

### Recording Source Selection Issues

**Current 'auto' resolution logic is flawed:**

```python
def _resolve_auto_source(self, stream_type: str) -> str:
    if stream_type in ['LL_HLS', 'HLS']:
        return 'mediamtx'  # âœ… Works
    elif stream_type == 'MJPEG':
        return 'mjpeg_service'  # âŒ Not implemented!
    else:
        return 'rtsp'  # âœ… Works
```

**Fix options:**

1. Implement MJPEG service recording (preferred)
2. Change MJPEG auto â†’ 'rtsp' instead of 'mjpeg_service'
3. Remove 'auto' as recommended, require explicit selection

---

## Testing Checklist for Next Session

After implementing fixes above:

### Manual Recording

- [ ] Click record button on RTSP camera (should work)
- [ ] Click record button on MJPEG camera (should work after fix #3)
- [ ] Try recording same camera twice (should block after fix #2)
- [ ] Check `/mnt/sdc/NVR_Recent/manual` has files (after fix #1)

### Continuous Recording

- [ ] Enable 24/7 for a camera in settings
- [ ] Restart Flask app
- [ ] Check `/mnt/sdc/NVR_Recent/continuous` for files
- [ ] Verify FFmpeg processes running (`ps aux | grep ffmpeg`)
- [ ] Wait for segment duration, verify new file created

### Snapshots

- [ ] Enable snapshots for a camera (1s interval for testing)
- [ ] Restart Flask app  
- [ ] Check `/mnt/sdc/NVR_Recent/snapshots` for JPEGs
- [ ] Verify interval timing (should appear every 1s)

### Settings Persistence

- [ ] Change settings, save, reload page - verify settings persist
- [ ] Disable continuous, restart - verify recording stops
- [ ] Enable ONVIF detection - verify option available/grayed correctly

---

## File Locations Reference

**Configuration:**

- Settings storage: `~/0_NVR/config/recording_settings.json`
- Config loader: `~/0_NVR/config/recording_config_loader.py`

**Services:**

- Recording service: `~/0_NVR/services/recording/recording_service.py`
- Storage manager: `~/0_NVR/services/recording/storage_manager.py`
- Snapshot service: `~/0_NVR/services/snapshot_service.py` (to be created)

**Frontend:**

- Modal CSS: `~/0_NVR/static/css/components/recording-modal.css`
- Controllers: `~/0_NVR/static/js/controllers/`
- Forms: `~/0_NVR/static/js/forms/`
- Modals: `~/0_NVR/static/js/modals/`

**Flask:**

- API routes: `~/0_NVR/app.py` (lines ~1382-1500)

**Storage:**

- Motion: `/mnt/sdc/NVR_Recent/motion/`
- Continuous: `/mnt/sdc/NVR_Recent/continuous/`
- Snapshots: `/mnt/sdc/NVR_Recent/snapshots/`
- Manual: `/mnt/sdc/NVR_Recent/manual/` (to be created)

---

## Known Bugs

1. **MJPEG service recording returns False immediately** - Fix #3 needed
2. **'auto' source selects broken mjpeg_service** - Architecture decision needed
3. **No race condition prevention** - Fix #2 needed
4. **Continuous recording configured but doesn't start** - Fix #4 needed
5. **Snapshots configured but nothing captures** - Fix #5 needed

---

## Next Session Goals

**MVP (Minimum Viable Product):**

1. âœ… Manual recording works for all cameras
2. âœ… Continuous recording auto-starts and rotates segments
3. âœ… Snapshots capture at configured intervals
4. âœ… Race conditions prevented
5. âš ï¸ Motion detection (ONVIF/FFmpeg) - can remain skeleton for now

**Post-MVP:**

- Complete ONVIF event listener
- Complete FFmpeg motion detector
- Add UI status indicators (recording active, motion detection active)
- Implement storage quota management
- Add recording playback UI

---

## Session Log - November 15, 2025 (Continued)

**Chat ID:** Current session (continuation from 6e6180ff-1ae7-4d53-ba45-88cb1eb77771)

### Completed

**Fix #1: StorageManager 'manual' Recording Type** ✅

- Added `manual_path` to StorageManager.**init**
- Updated `_verify_directories()` to include manual_path
- Updated `generate_recording_path()` to handle 'manual' type
- Updated `get_storage_stats()` tier_mapping
- Updated `cleanup_old_recordings()` manual case
- Updated `cleanup_all_cameras()` manual case
- Updated RecordingService.start_manual_recording() to use 'manual' type (line 256)
- Docker volume mapping confirmed: `/mnt/sdc/NVR_Recent/manual:/recordings/manual`

**Fix #2: Race Condition Prevention** ⏭️ SKIPPED

- **Decision:** Allow parallel motion + manual recordings
- **Rationale:** Separate storage prevents conflicts; timeline feature will exclude manual recordings
- **Future:** Consider m2m database design for timeline references

**Fix #3: MJPEG Service Recording** ✅ REMOVED

- **Decision:** MJPEG service for live streaming only (sub-second latency)
- **Removed:** `_start_mjpeg_recording()` method from recording_service.py
- **Removed:** mjpeg_service handling in start_motion_recording() and start_manual_recording()
- **Removed:** mjpeg_service option from recording-settings-form.js UI
- **Updated:** recording_config_loader.py auto-resolution - MJPEG cameras now use 'rtsp'
- **Updated:** Documentation to reflect only 'mediamtx' and 'rtsp' source types
- **Result:** Simpler codebase, ~50 lines removed, no functionality lost

**Fix #4: Auto-Start Continuous Recording** ✅ COMPLETE

- **Created:** `start_continuous_recording()` method in recording_service.py
  - Uses configured segment duration from camera settings
  - Stores with 'continuous' recording type
  - Sets `auto_restart: True` flag for monitoring thread
- **Added:** Auto-start logic in app.py initialization
  - Iterates through all cameras via `camera_repo.get_all_cameras()`
  - Starts continuous recording for cameras with `continuous_recording.enabled = true`
- **Added:** Orphaned process cleanup on Flask startup
  - Kills any FFmpeg processes from previous runs (prevents duplicate recordings)
  - Uses `pgrep -f 'ffmpeg.*recordings/continuous'` + `kill -9`
- **Modified:** `cleanup_finished_recordings()` with auto-restart logic
  - Checks `auto_restart` flag when segment completes
  - Automatically starts next segment for continuous recordings
- **Added:** Background monitoring thread in app.py
  - Runs `cleanup_finished_recordings()` every 10 seconds
  - Daemon thread (exits with main app)
  - Error handling with 30s backoff
- **Result:** 24/7 continuous recording with automatic segment rotation working

**Fix #5: Snapshot Service** ✅ COMPLETE

- **Created:** `~/0_NVR/services/recording/snapshot_service.py`
  - Timer-based periodic JPEG capture (not continuous loop)
  - FFmpeg `-frames:v 1` for single frame extraction
  - Uses sub-stream for efficiency
  - Auto-schedules next capture after each snapshot
  - Configurable interval per camera
- **Methods:**
  - `start_snapshots()` - Initialize periodic capture for camera
  - `stop_snapshots()` - Cancel scheduled captures
  - `_capture_snapshot()` - FFmpeg single-frame extraction
  - `_schedule_next_snapshot()` - Timer-based scheduling
  - `_get_snapshot_source_url()` - RTSP URL resolution (MediaMTX or direct)
  - `get_active_snapshots()` - Status reporting
- **Integration in app.py:**
  - Initialized with camera_repo, storage_manager, recording_config
  - Auto-starts for cameras with `snapshots.enabled = true`
  - Reuses StorageManager for path generation
- **Bug Fix:** Added 'snapshots' case to `recording_config_loader.py:is_recording_enabled()`
  - Was returning False for all snapshot checks (line 203)
  - Added `elif recording_type == 'snapshots': return camera_cfg.get('snapshots', {}).get('enabled', False)`
- **Result:** Periodic JPEG snapshots working at configured intervals

### Remaining Fixes

- **Fix #6:** Motion detection/recording implementation **IN PROGRESS**

## **Fix #6 Status: Motion Detection - Critical Findings**

### **CRITICAL DISCOVERY: Reolink Cameras Use Proprietary Event System**

**ONVIF PullMessages does NOT deliver motion change events on Reolink cameras**, even though:

- ✅ ONVIF connection works (port 8000 for Reolink, port 80 for Amcrest)
- ✅ CreatePullPointSubscription succeeds
- ✅ PullMessages API call works
- ✅ Camera advertises motion topics in GetEventProperties
- ✅ Motion detection enabled in camera settings (sensitivity: Low, 24/7)

**What Actually Happens:**

- PullMessages only returns `PropertyOperation="Initialized"` messages with `IsMotion: false` or `State: false`
- NO motion change events fire when moving extensively in front of camera
- Both `tns1:VideoSource/MotionAlarm` (State) and `tns1:RuleEngine/CellMotionDetector/Motion` (IsMotion) topics tested - neither delivers change events

**Root Cause:**
Reolink cameras use their proprietary **"Baichuan" TCP push event protocol** instead of ONVIF events for motion detection.

**Evidence:**

- Official Reolink-authorized Python library: `reolink_aio` (<https://github.com/starkillerOG/reolink_aio>)
- Working pattern: `host.baichuan.register_callback()` + `await host.baichuan.subscribe_events()`
- Home Assistant ONVIF integration has documented Reolink motion event issues (GitHub issue #42784)

### **Tested Configurations (ipython):**

**Working ONVIF connection:**

```python
from onvif import ONVIFCamera
from datetime import timedelta

mycam = ONVIFCamera('192.168.10.88', 8000, 'admin', password, '/usr/local/lib/python3.11/site-packages/wsdl/', no_cache=True)
event_service = mycam.create_events_service()

# Subscription with topic filter
sub = event_service.CreatePullPointSubscription({
    'Filter': {
        'TopicExpression': {
            '_value_1': 'tns1:VideoSource/MotionAlarm',
            'Dialect': 'http://www.onvif.org/ver10/tev/topicExpression/ConcreteSet'
        }
    }
})

# Correct pullpoint service creation
pullpoint = event_service.zeep_client.create_service(
    '{http://www.onvif.org/ver10/events/wsdl}PullPointSubscriptionBinding',
    sub.SubscriptionReference.Address._value_1
)

# Poll for messages
messages = pullpoint.PullMessages(Timeout=timedelta(seconds=2), MessageLimit=10)
```

**Result:** Only initialization messages, no motion events despite extensive movement

### **Recommended Solution: FFmpeg Motion Detector**

**Why FFmpeg instead of ONVIF:**

1. **Universal** - works with ALL camera types (Reolink, Amcrest, Eufy, UniFi)
2. **Reliable** - doesn't depend on vendor-specific event implementations
3. **Already scaffolded** - `ffmpeg_motion_detector.py` exists as skeleton
4. **CPU efficient** - FFmpeg scene detection filter is lightweight

**Implementation:**

- File: `~/0_NVR/services/motion/ffmpeg_motion_detector.py`
- Method: Use FFmpeg's `select` filter with scene change detection
- Config: Sensitivity threshold per camera in `recording_settings.json`
- Trigger: Call `recording_service.start_motion_recording()` on threshold exceeded

**FFmpeg command pattern:**

```bash
ffmpeg -i rtsp://camera -vf "select='gt(scene,0.3)',metadata=print:file=-" -f null -
```

Parse output for scene change scores, trigger recording when > threshold.

### **Alternative: Reolink Native API (Future Enhancement)**

For Reolink-specific optimization, consider `reolink-aio` library:

```python
from reolink_aio.api import Host

host = Host('192.168.10.88', 'admin', password)
await host.get_host_data()
host.baichuan.register_callback("motion_detector", motion_callback)
await host.baichuan.subscribe_events()
```

**Pros:** Native motion events, lower latency
**Cons:** Vendor-specific, async library, additional dependency

### **Files Modified:**

- `~/0_NVR/services/onvif/onvif_event_listener.py` - Multiple iterations testing (needs cleanup or deletion)
- `~/0_NVR/config/recording_config_loader.py` - Added 'snapshots' case to `is_recording_enabled()`
- `~/0_NVR/app.py` - Added ONVIF listener initialization (currently non-functional for Reolink)

### **What Works:**

- ✅ `recording_service.start_motion_recording()` exists and functional
- ✅ Motion recording metadata storage (PostgreSQL)
- ✅ Auto-start framework in app.py
- ✅ Camera configuration with `detection_method: "onvif"` or `"ffmpeg"`

### **Next Steps:**

1. **Implement FFmpeg motion detector** (priority - universal solution)
2. Clean up/remove non-functional ONVIF listener code for Reolink
3. Test ONVIF with Amcrest cameras (may work correctly)
4. Consider Reolink native API as future enhancement

### **Token Budget:**

~84k remaining - recommend continuing in new chat for FFmpeg implementation

### Architecture Notes

**Manual Recordings Strategy:**

- Stored in separate `/recordings/manual/` directory
- Can run parallel with motion detection recordings
- Excluded from timeline UI (future: "My Recordings" feature)
- Database: Consider m2m approach for cross-referencing

**Long-term Storage:**

- Volume mappings defined in docker-compose.yml
- Archiving logic not yet implemented
- Storage tiers: /recordings/ (recent) → /recordings/STORAGE/ (archive)

---

**Good night. See you next session.**
