# NVR Recording System - Tomorrow's Testing Guide

**Date**: 2025-11-12
**Status**: Code complete, ready for integration testing

---

## What Was Completed Tonight

### 1. Configuration Restructuring ✅
- **File**: `config/recording_settings.json`
- **Change**: Camera-centric structure with `global_defaults` + `camera_settings`
- **Features**: 
  - Per-camera recording source (auto/rtsp/mjpeg_service/mediamtx)
  - Per-camera detection method (onvif/ffmpeg/manual_only)
  - Per-camera retention periods
  - Quality selection (main/sub streams)

### 2. Configuration Loader Update ✅
- **File**: `config/recording_config_loader.py`
- **New Methods**:
  - `get_camera_config(camera_id, stream_type)` - Auto-resolves recording source
  - `get_recording_source()` - Returns resolved source type
  - `is_recording_enabled()` - Per-camera enable checks
- **Auto-resolution Logic**:
  - LL_HLS/HLS → mediamtx
  - MJPEG → mjpeg_service
  - Other → rtsp

### 3. Storage Manager Update ✅
- **File**: `services/recording/storage_manager.py`
- **Changes**:
  - Per-camera cleanup with camera-specific retention
  - `cleanup_all_cameras()` for scheduled cleanup
  - Storage stats with usage percentages

### 4. Recording Service Implementation ✅
- **File**: `services/recording/recording_service.py`
- **Core Features**:
  - Hybrid source support (MediaMTX/RTSP/MJPEG service)
  - `start_motion_recording()` with auto-detection
  - `stop_recording()` with graceful termination
  - `get_active_recordings()` with progress
  - PostgREST metadata integration

---

## Tomorrow's Tasks

### Step 1: Update Configuration File ⏳

**Replace** `~/0_NVR/config/recording_settings.json` with new structure:

```bash
cd ~/0_NVR/config
cp recording_settings.json recording_settings.json.backup
# Then paste new JSON structure from implementation package
```

### Step 2: Update Python Modules ⏳

**Files to replace:**
1. `~/0_NVR/config/recording_config_loader.py`
2. `~/0_NVR/services/recording/storage_manager.py`
3. `~/0_NVR/services/recording/recording_service.py`

**Command:**
```bash
# Copy updated files from implementation package
# Or apply changes manually
```

### Step 3: Test Configuration Loading ⏳

**Python test script:**
```python
from config.recording_config_loader import RecordingConfig

config = RecordingConfig()

# Test 1: Load global defaults
defaults = config.get_global_defaults()
print("Global defaults loaded:", bool(defaults))

# Test 2: Camera with overrides
amcrest_cfg = config.get_camera_config('AMCREST_LOBBY', 'MJPEG')
print(f"AMCREST recording source: {amcrest_cfg['motion_recording']['recording_source']}")
print(f"AMCREST retention: {amcrest_cfg['motion_recording']['max_age_days']} days")
# Expected: source=rtsp, retention=30 days

# Test 3: Auto-resolution
unifi_cfg = config.get_camera_config('68d49398005cf203e400043f', 'LL_HLS')
print(f"UniFi auto-resolved source: {unifi_cfg['motion_recording']['recording_source']}")
# Expected: mediamtx

print("\nConfiguration tests passed!")
```

### Step 4: Test Storage Manager ⏳

**Python test script:**
```python
from services.recording.storage_manager import StorageManager

storage = StorageManager()

# Test 1: Storage statistics
stats = storage.get_storage_stats()
print("\nStorage Statistics:")
for tier, data in stats.items():
    print(f"  {tier}: {data['total_mb']} MB / {data['max_size_mb']} MB "
          f"({data['usage_percent']}%)")

# Test 2: Generate paths
test_cameras = ['AMCREST_LOBBY', 'REOLINK_OFFICE', '68d49398005cf203e400043f']
for camera_id in test_cameras:
    path = storage.generate_recording_path(camera_id, 'motion')
    print(f"\n{camera_id} path: {path}")

# Test 3: Check limits
limits = storage.check_storage_limits('motion')
print(f"\nMotion storage: {limits['usage_percent']}% used")
print(f"Cleanup recommended: {limits['cleanup_recommended']}")

print("\nStorage manager tests passed!")
```

### Step 5: Test Recording Service (Simple) ⏳

**Note**: Requires full app context with camera_repo

**Python test script:**
```python
from services.recording.recording_service import RecordingService
from services.camera_repository import CameraRepository

# Initialize
camera_repo = CameraRepository('/app/config/cameras.json')
recording_service = RecordingService(camera_repo)

print("RecordingService initialized successfully!")

# Test source resolution
test_cameras = [
    ('AMCREST_LOBBY', 'MJPEG'),
    ('68d49398005cf203e400043f', 'LL_HLS'),
    ('REOLINK_OFFICE', 'MJPEG')
]

for camera_id, stream_type in test_cameras:
    try:
        source_url, source_type = recording_service._get_recording_source_url(camera_id)
        print(f"\n{camera_id}:")
        print(f"  Source type: {source_type}")
        print(f"  Source URL: {source_url[:50]}...")  # Truncate for readability
    except Exception as e:
        print(f"\n{camera_id}: ERROR - {e}")

print("\nRecording service tests passed!")
```

### Step 6: Test Actual Recording (Careful!) ⏳

**Important**: This will create actual recording files.

**Test script:**
```python
import time
from services.recording.recording_service import RecordingService
from services.camera_repository import CameraRepository

# Initialize
camera_repo = CameraRepository('/app/config/cameras.json')
recording_service = RecordingService(camera_repo)

# Test with short duration
test_camera = 'AMCREST_LOBBY'  # Or any camera you want to test
test_duration = 10  # 10 seconds only

print(f"Starting test recording on {test_camera} for {test_duration}s...")
recording_id = recording_service.start_motion_recording(test_camera, duration=test_duration)

if recording_id:
    print(f"Recording started: {recording_id}")
    print("Recording in progress...")
    
    # Monitor progress
    for i in range(test_duration):
        time.sleep(1)
        active = recording_service.get_active_recordings()
        if active:
            rec = active[0]
            print(f"  {rec['elapsed_seconds']}s / {rec['duration']}s "
                  f"({rec['progress_percent']}%)")
    
    # Wait for completion
    time.sleep(2)
    
    # Check if file exists
    import os
    recording_path = f"/mnt/sdc/NVR_Recent/motion/{recording_id}.mp4"
    if os.path.exists(recording_path):
        size_mb = os.path.getsize(recording_path) / 1024 / 1024
        print(f"\n✅ Recording completed: {recording_path}")
        print(f"   File size: {size_mb:.2f} MB")
    else:
        print(f"\n❌ Recording file not found: {recording_path}")
    
    # Cleanup finished recordings
    cleaned = recording_service.cleanup_finished_recordings()
    print(f"   Cleaned up {cleaned} finished recordings from memory")
else:
    print("❌ Failed to start recording")
```

---

## Testing Priority Order

1. **Configuration loading** - Verify JSON structure and merging
2. **Storage manager** - Verify path generation and stats
3. **Recording service init** - Verify source resolution
4. **Single recording test** - AMCREST_LOBBY (10s duration)
5. **MediaMTX recording test** - UniFi camera (10s duration)
6. **Concurrent test** - 2 cameras simultaneously
7. **Storage cleanup test** - Create old files and test deletion

---

## Known Issues to Watch For

### Issue 1: Import Errors
**Symptom**: `ModuleNotFoundError: No module named 'recording_config_loader'`

**Cause**: Python path not including `/app/config`

**Fix**: Add to top of test scripts:
```python
import sys
sys.path.insert(0, '/app/config')
sys.path.insert(0, '/app/services/recording')
```

### Issue 2: MediaMTX Source Unreachable
**Symptom**: FFmpeg fails immediately with connection error

**Cause**: MediaMTX not running or camera not streaming

**Fix**: Verify camera is streaming first:
```bash
# Check if stream exists in MediaMTX
curl http://localhost:8888/v3/paths/list
# Should show camera ID in active paths
```

### Issue 3: Permission Denied on Recording Directory
**Symptom**: `PermissionError: Cannot write to /recordings/motion`

**Cause**: Docker volume mount permissions

**Fix**: Check ownership:
```bash
ls -la /mnt/sdc/NVR_Recent/
sudo chown -R $USER:$USER /mnt/sdc/NVR_Recent/
```

### Issue 4: Recording Stops Immediately
**Symptom**: Recording starts but stops after 1-2 seconds

**Cause**: FFmpeg input error or codec issue

**Fix**: Check FFmpeg stderr:
```python
# In recording_service.py, temporarily enable stderr capture:
process = subprocess.Popen(
    ffmpeg_cmd,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,  # Capture stderr
    stdin=subprocess.DEVNULL
)
# Then read process.stdout to see errors
```

---

## What's NOT Implemented Yet

1. **MJPEG Service Recording** - Reading frames from reolink/amcrest services
2. **Continuous Recording** - 24/7 recording mode
3. **Flask API Routes** - Web interface for recording control
4. **Cleanup Scheduler** - Automated cron-based cleanup
5. **Motion Detection** - ONVIF event listeners, FFmpeg analysis
6. **Pre/Post Buffers** - Circular buffer for pre-motion recording

These will be implemented in subsequent phases.

---

## Quick Reference: File Locations

```
~/0_NVR/
├── config/
│   ├── recording_settings.json          # Updated structure
│   └── recording_config_loader.py       # Updated loader
├── services/
│   └── recording/
│       ├── __init__.py
│       ├── recording_service.py         # New implementation
│       └── storage_manager.py           # Updated
└── /mnt/sdc/NVR_Recent/
    ├── motion/                          # Recording output
    ├── continuous/
    └── snapshots/
```

---

**Good luck with testing tomorrow! 🚀**