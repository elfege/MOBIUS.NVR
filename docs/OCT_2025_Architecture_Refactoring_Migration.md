# 🔄 Architecture Refactoring Migration Guide

## Overview

This guide walks through migrating from the old monolithic architecture to the new modular, strategy-pattern based architecture.

---

## 📁 New File Structure

```
~/0_NVR/
├── config/
│   ├── cameras.json              # ✅ UPDATED (cleaned, no credentials/URLs)
│   ├── unifi_protect.json        # 🆕 NEW
│   ├── eufy_bridge.json          # 🆕 NEW
│   └── reolink.json              # 🆕 NEW
│
├── services/
│   ├── credentials/              # 🆕 NEW DIRECTORY
│   │   ├── __init__.py
│   │   ├── credential_provider.py
│   │   └── aws_credential_provider.py
│   │
│   ├── camera_repository.py      # 🆕 NEW
│   ├── ptz_validator.py          # 🆕 NEW
│   │
│   ├── unifi_protect_service.py  # ✅ KEEP (for MJPEG)
│   └── ... (other existing services)
│
├── streaming/                    # 🆕 NEW DIRECTORY
│   ├── __init__.py
│   ├── stream_handler.py         # Abstract base
│   ├── stream_manager.py      # New stream manager
│   │
│   └── handlers/                 # 🆕 NEW SUBDIRECTORY
│       ├── __init__.py
│       ├── eufy_stream_handler.py
│       ├── unifi_stream_handler.py
│       └── reolink_stream_handler.py
│
├── app.py                        # ✅ UPDATED (new initialization)
├── device_manager.py             # ⚠️  DEPRECATED (replaced by repository)
└── stream_manager.py             # ⚠️  DEPRECATED (replaced by v2)
```

---

## 🔧 Step-by-Step Migration

### **Step 1: Create New Directories**

```bash
cd ~/0_NVR

# Create new directory structure
mkdir -p services/credentials
mkdir -p streaming/handlers

# Create __init__.py files
touch services/credentials/__init__.py
touch streaming/__init__.py
touch streaming/handlers/__init__.py
```

---

### **Step 2: Add New Config Files**

Copy the config files from the artifacts:

```bash
# These are in the artifacts above:
# - config/unifi_protect.json
# - config/eufy_bridge.json
# - config/reolink.json
```

**Update `unifi_protect.json` with  actual rtsp_alias:**
```json
{
  "console": {
    "host": "192.168.10.3",
    "port": 7447,
    ...
  }
}
```

---

### **Step 3: Add New Service Files**

Copy these files from artifacts:

```bash
# Credential providers
services/credentials/credential_provider.py
services/credentials/aws_credential_provider.py

# Repository and validators
services/camera_repository.py
services/ptz_validator.py

# Stream handlers
streaming/stream_handler.py
streaming/stream_manager.py
streaming/handlers/eufy_stream_handler.py
streaming/handlers/unifi_stream_handler.py
streaming/handlers/reolink_stream_handler.py
```

---

### **Step 4: Update cameras.json**

Replace  current `config/cameras.json` with the cleaned version (see artifact above).

**Key changes:**
- ❌ Removed all `credentials` objects
- ❌ Removed embedded RTSP URLs with credentials
- ❌ Removed `protect_host`/`protect_port` from UniFi cameras (now in unifi_protect.json)
- ✅ Kept only RTSP structure (host, port, path) - NO credentials
- ✅ Removed deprecated `ptz_cameras` section

---

### **Step 5: Update app.py Initialization**

Replace the initialization section in `app.py`:

**OLD CODE (lines ~40-75):**
```python
device_manager = DeviceManager()
stream_manager = StreamManager()
stream_manager.set_device_manager(device_manager)
```

**NEW CODE:**
```python
from services.camera_repository import CameraRepository
from services.credentials.aws_credential_provider import AWSCredentialProvider
from services.ptz_validator import PTZValidator
from streaming.stream_manager import StreamManager

# Initialize core services
credential_provider = AWSCredentialProvider()
camera_repo = CameraRepository('./config')
ptz_validator = PTZValidator(camera_repo)

# Initialize stream manager with dependencies
stream_manager = StreamManager(
    camera_repo=camera_repo,
    credential_provider=credential_provider
)

# Keep bridge for PTZ (unchanged)
eufy_bridge = EufyBridge()
bridge_watchdog = BridgeWatchdog(eufy_bridge)
```

---

### **Step 6: Update Flask Route Functions**

#### **Update stream start endpoint:**

**OLD:**
```python
@app.route('/api/stream/start/<camera_serial>', methods=['POST'])
def api_stream_start(camera_serial):
    camera_name = device_manager.get_camera_name(camera_serial)

    if not device_manager.is_valid_streaming_camera(camera_serial):
        return jsonify({'success': False, 'error': 'Invalid camera'}), 400

    stream_url = stream_manager.start_stream(camera_serial)
    # ...
```

**NEW:**
```python
@app.route('/api/stream/start/<camera_serial>', methods=['POST'])
def api_stream_start(camera_serial):
    camera_name = camera_repo.get_camera_name(camera_serial)

    if not ptz_validator.is_streaming_capable(camera_serial):
        return jsonify({'success': False, 'error': 'Invalid camera'}), 400

    stream_url = stream_manager.start_stream(camera_serial)
    # ...
```

#### **Update PTZ endpoint:**

**OLD:**
```python
@app.route('/api/ptz/<camera_serial>/<direction>', methods=['POST'])
def api_ptz_move(camera_serial, direction):
    if not device_manager.is_valid_ptz_camera(camera_serial):
        return jsonify({'success': False, 'error': 'Invalid camera'}), 400
    # ...
```

**NEW:**
```python
@app.route('/api/ptz/<camera_serial>/<direction>', methods=['POST'])
def api_ptz_move(camera_serial, direction):
    if not ptz_validator.is_ptz_capable(camera_serial):
        return jsonify({'success': False, 'error': 'Invalid camera'}), 400

    if not ptz_validator.validate_ptz_direction(direction):
        return jsonify({'success': False, 'error': 'Invalid direction'}), 400
    # ...
```

#### **Update streams page:**

**OLD:**
```python
@app.route('/streams')
def streams_page():
    cameras = device_manager.get_streaming_cameras()
    return render_template('streams.html', cameras=cameras)
```

**NEW:**
```python
@app.route('/streams')
def streams_page():
    cameras = camera_repo.get_streaming_cameras()
    return render_template('streams.html', cameras=cameras)
```

#### **Update status endpoint:**

**OLD:**
```python
@app.route('/api/status')
def api_status():
    return jsonify({
        'eufy': {
            'total_devices': device_manager.get_device_count(),
            'ptz_cameras': len(device_manager.get_ptz_cameras())
        }
    })
```

**NEW:**
```python
@app.route('/api/status')
def api_status():
    return jsonify({
        'eufy': {
            'total_devices': camera_repo.get_camera_count(),
            'ptz_cameras': len(camera_repo.get_ptz_cameras())
        },
        'active_streams': stream_manager.get_active_streams()
    })
```

---

### **Step 7: Load AWS Secrets Before Starting**

**Update  start script or manually load:**

```bash
# In start.sh or run manually before starting app
source ~/.bash_utils --no-exec

# Pull all camera secrets
pull_secrets_from_aws EUFY_CAMERAS
pull_secrets_from_aws Unifi-Camera-Credentials
pull_secrets_from_aws REOLINK_CAMERAS  # When ready

# Start app
python3 app.py
```

---

### **Step 8: Test Migration**

#### **Test 1: Camera Repository**
```python
# Test script
from services.camera_repository import CameraRepository

repo = CameraRepository('./config')
print(f"Total cameras: {repo.get_camera_count()}")
print(f"Eufy cameras: {len(repo.get_cameras_by_type('eufy'))}")
print(f"UniFi cameras: {len(repo.get_cameras_by_type('unifi'))}")
print(f"Streaming cameras: {len(repo.get_streaming_cameras())}")
print(f"PTZ cameras: {len(repo.get_ptz_cameras())}")
```

**Expected output:**
```
Total cameras: 10
Eufy cameras: 9
UniFi cameras: 1
Streaming cameras: 9
PTZ cameras: 5
```

#### **Test 2: Credential Provider**
```python
from services.credentials.aws_credential_provider import AWSCredentialProvider
import os

# Make sure secrets are loaded first!
provider = AWSCredentialProvider()

# Test Eufy camera
username, password = provider.get_credentials('eufy', 'T8416P0023352DA9')
print(f"Eufy camera credentials: {username}, {password[:4]}****")

# Test UniFi Protect
username, password = provider.get_credentials('unifi', 'protect')
print(f"Protect credentials: {username}, {password[:4]}****")
```

**Expected output:**
```
Eufy camera credentials: Hs07XmLA7MT8zb3j, Kq5P****
Protect credentials: user-api, ****
```

#### **Test 3: Stream Handler**
```python
from streaming.handlers.eufy_stream_handler import EufyStreamHandler
from services.credentials.aws_credential_provider import AWSCredentialProvider
from services.camera_repository import CameraRepository

provider = AWSCredentialProvider()
repo = CameraRepository('./config')
handler = EufyStreamHandler(provider, repo.get_eufy_bridge_config())

camera = repo.get_camera('T8416P0023352DA9')
rtsp_url = handler.build_rtsp_url(camera, stream_type=stream_type)
print(f"RTSP URL: {rtsp_url}")
```

**Expected output:**
```
Built RTSP URL for Living Room: rtsp://Hs07XmLA7MT8zb3j:****@192.168.10.84:554/live0
RTSP URL: rtsp://Hs07XmLA7MT8zb3j:Kq5PKqHhHaVLopXL@192.168.10.84:554/live0
```

#### **Test 4: Full Stream Start**
```bash
# Start the Flask app
python3 app.py

# In another terminal, test stream start
curl -X POST http://localhost:5000/api/stream/start/T8416P0023352DA9
```

**Expected response:**
```json
{
  "success": true,
  "camera_serial": "T8416P0023352DA9",
  "camera_name": "Living Room",
  "stream_url": "/streams/T8416P0023352DA9/playlist.m3u8",
  "message": "Stream started for Living Room"
}
```

---

### **Step 9: Verify Streams Work**

Open browser and test:
```
http://192.168.10.8:5000/streams
```

All 9 cameras should appear and stream successfully.

---

## 🔥 Rollback Plan (If Something Breaks)

If the migration fails:

```bash
# Restore old files from git
git checkout main -- device_manager.py stream_manager.py app.py

# Restore old cameras.json (with embedded credentials)
git checkout main -- config/cameras.json

# Remove new directories
rm -rf streaming/
rm -rf services/credentials/
rm services/camera_repository.py services/ptz_validator.py

# Restart app
python3 app.py
```

---

## ✅ Post-Migration Cleanup

Once everything works:

```bash
# Rename old files for reference
mv device_manager.py device_manager.py.old
mv stream_manager.py stream_manager.py.old

# Or delete them
rm device_manager.py stream_manager.py

# Commit changes
git add .
git commit -m "refactor: modular architecture with strategy pattern"
git push
```

---

## 🆕 Adding Reolink Cameras Later

With the new architecture, adding Reolink is trivial:

### **Step 1: Update reolink.json**
```json
{
  "nvr": {
    "host": "192.168.10.50",
    "port": 554
  }
}
```

### **Step 2: Add cameras to cameras.json**
```json
{
  "reolink_001": {
    "name": "Reolink Front Door",
    "type": "reolink",
    "channel": 1,
    "capabilities": ["streaming", "ptz"],
    "stream_type": "ll_hls"
  }
}
```

### **Step 3: Load credentials**
```bash
pull_secrets_from_aws REOLINK_CAMERAS
```

### **Step 4: Start streaming**
```python
# No code changes needed! Handler is already registered
stream_manager.start_stream('reolink_001')
```

---

## 📊 Before vs After Comparison

| Task | Old Architecture | New Architecture |
|------|------------------|------------------|
| Add new camera vendor | Modify `stream_manager.py` + `device_manager.py` | Add 1 handler file, no existing code changes |
| Change credential source | Find/replace `os.getenv()` everywhere | Swap credential provider class |
| Test Eufy streaming | Mock entire `device_manager` + `stream_manager` | Test `EufyStreamHandler` in isolation |
| Update Protect IP | Edit 10+ camera entries in JSON | Edit 1 line in `unifi_protect.json` |
| Fix RTSP URL bug | Search through monolithic `stream_manager.py` | Fix in 1 handler file |

---

## 🐛 Common Issues

### Issue 1: "No credentials found"
**Cause:** Secrets not loaded from AWS
**Fix:**
```bash
source ~/.bash_utils --no-exec
pull_secrets_from_aws EUFY_CAMERAS
pull_secrets_from_aws Unifi-Camera-Credentials
```

### Issue 2: "No handler found for camera type"
**Cause:** Camera type mismatch in cameras.json
**Fix:** Ensure `"type"` field is exactly `"eufy"`, `"unifi"`, or `"reolink"` (lowercase)

### Issue 3: "Missing rtsp_alias for UniFi camera"
**Cause:** PLACEHOLDER not replaced in cameras.json
**Fix:** Update UniFi camera with actual rtsp_alias from Protect bootstrap

### Issue 4: Import errors
**Cause:** Missing `__init__.py` files
**Fix:**
```bash
touch services/credentials/__init__.py
touch streaming/__init__.py
touch streaming/handlers/__init__.py
```

---

## 📞 Support

If you encounter issues:
1. Check logs: `tail -f logs/app.log`
2. Test components individually (see Step 8)
3. Use rollback plan if needed

---

**Migration complete! 🎉**
