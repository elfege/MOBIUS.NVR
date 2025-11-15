# Recording System Implementation - Session Handoff

**Date:** November 15, 2025  
**Status:** Partially Complete - Manual Recording Works, Auto-Services Pending  
**Next Session:** Implement Auto-Start Services + Fix Race Conditions

---

## Quick Status

✅ **Working:**

- Settings modal saves/loads correctly
- Manual recording button works for RTSP/MediaMTX cameras
- Flask API routes functional

❌ **Not Working:**

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
    print("✅ Recording service initialized")
except Exception as e:
    print(f"⚠️  Recording service initialization failed: {e}")
    recording_service = None
```

**Add after initialization:**

```python
# Auto-start continuous recordings
if recording_service:
    print("🎬 Auto-starting enabled recordings...")
    
    for camera_id in camera_repo.cameras.keys():
        try:
            camera = camera_repo.get_camera(camera_id)
            camera_name = camera.get('name', camera_id)
            
            # Start continuous recording if enabled
            if recording_service.config.is_recording_enabled(camera_id, 'continuous'):
                if recording_service.start_continuous_recording(camera_id):
                    print(f"  ✅ Continuous: {camera_name}")
                else:
                    print(f"  ❌ Failed continuous: {camera_name}")
            
            # TODO: Start motion detection if enabled
            # TODO: Start snapshot service if enabled
            
        except Exception as e:
            print(f"  ❌ Error starting services for {camera_id}: {e}")
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
        return 'mediamtx'  # ✅ Works
    elif stream_type == 'MJPEG':
        return 'mjpeg_service'  # ❌ Not implemented!
    else:
        return 'rtsp'  # ✅ Works
```

**Fix options:**

1. Implement MJPEG service recording (preferred)
2. Change MJPEG auto → 'rtsp' instead of 'mjpeg_service'
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

1. ✅ Manual recording works for all cameras
2. ✅ Continuous recording auto-starts and rotates segments
3. ✅ Snapshots capture at configured intervals
4. ✅ Race conditions prevented
5. ⚠️ Motion detection (ONVIF/FFmpeg) - can remain skeleton for now

**Post-MVP:**

- Complete ONVIF event listener
- Complete FFmpeg motion detector
- Add UI status indicators (recording active, motion detection active)
- Implement storage quota management
- Add recording playback UI

---

**Good night. See you next session.**
