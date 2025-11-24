# Neolink Integration - Backend & Frontend Updates Plan

## Overview
Adding `"stream_type": "neolink"` support requires updates across multiple layers:
- **cameras.json** schema (add new stream_type value)
- **Backend** (Python handlers, FFmpeg params, stream manager)
- **Frontend** (JavaScript stream routing)
- **Configuration** (auto-generate neolink.toml from cameras.json)

---

## Current Stream Types

**Existing values in cameras.json:**
- `"HLS"` - Standard HLS (6+ cameras)
- `"LL_HLS"` - Low-latency HLS via MediaMTX (1 camera - REOLINK_OFFICE)
- `"RTMP"` - RTMP/FLV streaming (tested, not production)
- `"mjpeg_proxy"` - MJPEG proxy (1 camera - Eufy)
- `null` - No streaming

**New value to add:**
- `"NEOLINK"` - Reolink cameras via Neolink bridge (Baichuan protocol)

---

## Phase 1: Configuration Schema Updates

### 1.1 Update cameras.json Schema

**For Reolink cameras using Neolink, add/modify:**

```json
{
  "REOLINK_OFFICE": {
    "name": "CAM OFFICE",
    "model": "RLC-410-5MP",
    "type": "reolink",
    "serial": "REOLINK_OFFICE",
    "host": "192.168.10.88",
    "mac": "ec:71:db:3e:93:f5",
    "capabilities": ["streaming"],
    "stream_type": "NEOLINK",  // <-- Changed from "LL_HLS"
    
    // New section for Neolink-specific config
    "neolink": {
      "baichuan_port": 9000,
      "rtsp_path": "mainStream",  // or "subStream"
      "enabled": true
    },
    
    // Keep existing rtsp_input/rtsp_output for FFmpeg processing
    "rtsp_input": {
      "rtsp_transport": "tcp",
      "timeout": 5000000,
      // ... existing params
    },
    "rtsp_output": {
      // ... existing HLS output params
    },
    
    // Player settings remain the same
    "player_settings": {
      "hls_js": {
        "enableWorker": true,
        "lowLatencyMode": true,
        // ... existing params
      }
    }
  }
}
```

**Key points:**
- `stream_type: "NEOLINK"` tells system to route through Neolink bridge
- `neolink` section contains Neolink-specific config
- FFmpeg still outputs HLS for browser consumption
- Frontend doesn't need to know about Baichuan - it just gets HLS

---

## Phase 2: Backend Updates

### 2.1 Create Neolink Configuration Generator

**New file: `0_MAINTENANCE_SCRIPTS/generate_neolink_config.py`**

```python
#!/usr/bin/env python3
"""
Generate neolink.toml from cameras.json
Filters for cameras with stream_type = "NEOLINK"
"""

import json
import sys
from pathlib import Path

def generate_neolink_config():
    # Load cameras.json
    cameras_file = Path(__file__).parent.parent / 'config' / 'cameras.json'
    with open(cameras_file) as f:
        data = json.load(f)
    
    # Filter for Neolink cameras
    neolink_cameras = []
    for serial, config in data.get('devices', {}).items():
        if config.get('stream_type') == 'NEOLINK' and config.get('type') == 'reolink':
            neolink_cameras.append({
                'serial': serial,
                'name': config.get('name', serial),
                'host': config.get('host'),
                'neolink': config.get('neolink', {}),
                'credentials': config.get('credentials', {})
            })
    
    if not neolink_cameras:
        print("No cameras with stream_type='NEOLINK' found")
        return
    
    # Generate neolink.toml
    output_file = Path(__file__).parent.parent / 'config' / 'neolink.toml'
    
    with open(output_file, 'w') as f:
        f.write("""################################################################################
# NEOLINK CONFIGURATION - AUTO-GENERATED
# Generated from cameras.json
# DO NOT EDIT MANUALLY - Use generate_neolink_config.py
################################################################################

bind = "0.0.0.0:8554"
log_level = "info"

""")
        
        for cam in neolink_cameras:
            baichuan_port = cam['neolink'].get('baichuan_port', 9000)
            stream_path = cam['neolink'].get('rtsp_path', 'mainStream')
            enabled = cam['neolink'].get('enabled', True)
            
            # Get credentials (implement credential provider logic)
            username = cam['credentials'].get('username', 'admin')
            password = cam['credentials'].get('password', '')
            
            f.write(f"""
################################################################################
# {cam['name']} ({cam['serial']})
################################################################################

[[cameras]]
name = "{cam['serial']}"
username = "{username}"
password = "{password}"
uid = ""
address = "{cam['host']}:{baichuan_port}"
stream = "{stream_path}"
enabled = {str(enabled).lower()}

""")
    
    print(f"✓ Generated {output_file}")
    print(f"✓ Configured {len(neolink_cameras)} camera(s)")

if __name__ == '__main__':
    generate_neolink_config()
```

**Usage:**
```bash
cd ~/0_NVR
python3 0_MAINTENANCE_SCRIPTS/generate_neolink_config.py
```

---

### 2.2 Update reolink_stream_handler.py

**File: `streaming/handlers/reolink_stream_handler.py`**

**Current behavior:**
- `build_rtsp_url()` connects directly to camera:554

**New behavior:**
- Check `stream_type` in camera config
- If `"NEOLINK"`, return Neolink bridge URL instead
- If `"HLS"` or `"LL_HLS"`, use direct camera URL

```python
def build_rtsp_url(self, camera_config: Dict, stream_type: str = 'sub') -> str:
    """
    Build RTSP URL - either direct camera or via Neolink bridge
    """
    serial = camera_config.get('serial', 'UNKNOWN')
    config_stream_type = camera_config.get('stream_type', 'HLS').upper()
    
    # NEOLINK: Use bridge instead of direct camera connection
    if config_stream_type == 'NEOLINK':
        neolink_config = camera_config.get('neolink', {})
        rtsp_path = neolink_config.get('rtsp_path', 'mainStream')
        
        # Neolink runs in same container, use localhost
        rtsp_url = f"rtsp://localhost:8554/{serial}/{rtsp_path}"
        
        logger.info(f"Using Neolink bridge for {serial}: {rtsp_url}")
        return rtsp_url
    
    # STANDARD: Direct camera connection (existing logic)
    else:
        # ... existing direct camera RTSP URL logic ...
        camera_ip = camera_config.get('host')
        # ... rest of existing code ...
```

**Location in file:** Around line 40-60 in `build_rtsp_url()` method

---

### 2.3 Update stream_manager.py

**File: `stream_manager.py`**

**Current code (line 253, 344):**
```python
st = (cam or {}).get('stream_type', 'HLS').upper()
protocol = camera.get('stream_type', 'HLS').upper()
```

**Update needed:**
Add NEOLINK to valid stream types check:

```python
# Around line 253
st = (cam or {}).get('stream_type', 'HLS').upper()
if st not in ['HLS', 'LL_HLS', 'MJPEG_PROXY', 'RTMP', 'NEOLINK']:
    logger.warning(f"Unknown stream_type '{st}' for {serial}, defaulting to HLS")
    st = 'HLS'
```

**Behavior:**
- NEOLINK cameras still output HLS (via Neolink bridge → FFmpeg → HLS)
- Just the input source changes (localhost:8554 instead of camera:554)

---

### 2.4 Update ffmpeg_params.py (if needed)

**File: `ffmpeg_params.py`**

**Check line 228:**
```python
builder = FFmpegHLSParamBuilder(camera_name=camera_name, stream_type=stream_type, ...)
```

**Likely no change needed** - NEOLINK cameras still produce HLS output
- Input changes (from Neolink bridge)
- Output format stays the same (HLS)

---

## Phase 3: Frontend Updates

### 3.1 Update stream.js

**File: `stream.js`**

**Current routing (line 296-305):**
```javascript
// Use streamType to determine which manager to use
if (streamType === 'mjpeg_proxy') {
    success = await this.mjpegManager.startStream(serial, streamElement);
} else if (streamType === 'HLS' || streamType === 'LL_HLS' || streamType === 'NEOLINK' || streamType === 'NEOLINK_LL_HLS') {
    success = await this.hlsManager.startStream(serial, streamElement, 'sub');
} else if (streamType === 'RTMP') {
    success = await this.flvManager.startStream(serial, streamElement);
} else {
    throw new Error(`Unknown stream type: ${streamType}`);
}
```

**Updated routing:**
```javascript
// Use streamType to determine which manager to use
if (streamType === 'mjpeg_proxy') {
    success = await this.mjpegManager.startStream(serial, streamElement);
} else if (streamType === 'HLS' || streamType === 'LL_HLS' || streamType === 'NEOLINK' || streamType === 'NEOLINK_LL_HLS') {
    // NEOLINK cameras output HLS (via Neolink bridge)
    success = await this.hlsManager.startStream(serial, streamElement, 'sub');
} else if (streamType === 'RTMP') {
    success = await this.flvManager.startStream(serial, streamElement);
} else {
    throw new Error(`Unknown stream type: ${streamType}`);
}
```

**Also update health monitoring (line 321-329):**
```javascript
if ((streamType === 'HLS' || streamType === 'NEOLINK') && this.health) {
    const hls = this.hlsManager?.hlsInstances?.get?.(serial) || null;
    el._healthDetach = this.health.attachHls(serial, el, hls);
}
```

**Key point:** From frontend perspective, NEOLINK = HLS (browser doesn't care about Baichuan)

---

### 3.2 Update fullscreen handling

**Check line 240:**
```javascript
if (streamType === 'HLS' || streamType === 'LL_HLS' || streamType === 'NEOLINK' || streamType === 'NEOLINK_LL_HLS') {
```

**Update to:**
```javascript
if (streamType === 'HLS' || streamType === 'LL_HLS' || streamType === 'NEOLINK' || streamType === 'NEOLINK_LL_HLS') {
```

---

### 3.3 Update streams.html (if needed)

**Check data attributes:**
```html
<div class="stream-item" 
     data-serial="REOLINK_OFFICE" 
     data-stream-type="NEOLINK">  <!-- New value -->
```

**No code changes needed** - just ensure stream_type from cameras.json passes through correctly

---

## Phase 4: Docker Integration

### 4.1 Update Dockerfile

**Add Neolink binary to container:**

```dockerfile
# ... existing COPY commands ...

# Add Neolink binary and config
COPY neolink/target/release/neolink /usr/local/bin/neolink
COPY config/neolink.toml /app/config/neolink.toml
RUN chmod +x /usr/local/bin/neolink

# ... rest of Dockerfile ...
```

---

### 4.2 Update docker-compose.yml

**Expose Neolink RTSP port (internal only):**

```yaml
services:
  unified-nvr:
    # ... existing config ...
    ports:
      - "5000:5000"   # Flask app
      - "8554:8554"   # Neolink RTSP server (NEW)
    # ... rest of config ...
```

**Note:** Port 8554 is INTERNAL to container network - not exposed to host

---

### 4.3 Add Neolink to supervisord (optional)

**If using supervisord to manage processes in container:**

```ini
[program:neolink]
command=/usr/local/bin/neolink rtsp --config=/app/config/neolink.toml
autostart=true
autorestart=true
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0
```

**Or start in Dockerfile ENTRYPOINT:**
```dockerfile
CMD ["sh", "-c", "neolink rtsp --config=/app/config/neolink.toml & python3 app.py"]
```

---

## Phase 5: Testing Plan

### 5.1 Unit Testing

**Test camera config:**
```bash
# Verify NEOLINK cameras detected
python3 0_MAINTENANCE_SCRIPTS/generate_neolink_config.py

# Check generated neolink.toml
cat config/neolink.toml
```

**Test RTSP URL generation:**
```python
# In Python console
from streaming.handlers.reolink_stream_handler import ReolinkStreamHandler

config = {
    'serial': 'REOLINK_OFFICE',
    'stream_type': 'NEOLINK',
    'host': '192.168.10.88',
    'neolink': {
        'rtsp_path': 'mainStream'
    }
}

handler = ReolinkStreamHandler(None, {})
url = handler.build_rtsp_url(config)
print(url)  # Should be: rtsp://localhost:8554/REOLINK_OFFICE/mainStream
```

---

### 5.2 Integration Testing

**Step 1: Test Neolink standalone**
```bash
# Start Neolink manually
cd ~/0_NVR/neolink
./target/release/neolink rtsp --config=../config/neolink.toml

# Verify RTSP stream works
ffmpeg -rtsp_transport tcp -i rtsp://localhost:8554/REOLINK_OFFICE/mainStream -t 5 -f null -
```

**Step 2: Test in Docker container**
```bash
# Rebuild container with Neolink
docker compose build unified-nvr
docker compose up -d unified-nvr

# Check Neolink is running
docker compose exec unified-nvr ps aux | grep neolink

# Check RTSP port
docker compose exec unified-nvr netstat -tlnp | grep 8554

# Test stream from inside container
docker compose exec unified-nvr ffmpeg -rtsp_transport tcp -i rtsp://localhost:8554/REOLINK_OFFICE/mainStream -t 5 -f null -
```

**Step 3: Test full pipeline**
```bash
# Start stream via API
curl -X POST https://192.168.10.15/api/stream/start/REOLINK_OFFICE

# Check logs
docker compose logs -f unified-nvr | grep -i neolink

# Open browser: https://192.168.10.15/streams
# Camera should play with improved latency
```

---

### 5.3 Performance Testing

**Measure latency:**
- Native Reolink app: ~100-300ms (baseline)
- Direct RTSP: ~1-2 seconds (current)
- Via Neolink: ~600ms-1.5s (target)

**Test with:**
```bash
# Use VLC or ffplay with timestamp overlay
ffplay -rtsp_transport tcp rtsp://localhost:8554/REOLINK_OFFICE/mainStream
```

**Compare:**
1. Reolink native app (phone/PC)
2. Direct RTSP (current cameras.json config)
3. Neolink bridge (new config)

---

## Phase 6: Migration Strategy

### 6.1 Gradual Rollout

**Camera .88 (OFFICE) - Guinea Pig:**
1. ✅ Already configured with LL_HLS (working baseline)
2. Change to `stream_type: "NEOLINK"` in cameras.json
3. Generate neolink.toml
4. Rebuild container
5. Validate latency improvement
6. Monitor for 24-48 hours

**Camera .89 (TERRACE) - Second:**
1. Once .88 validated stable
2. Update cameras.json
3. Regenerate neolink.toml
4. Restart container (no rebuild needed)
5. Validate fixed (no more RJ45 corrosion issues affecting stream)

**Other Reolink cameras - Batch:**
1. Once both .88 and .89 proven stable
2. Update all remaining Reolink cameras
3. Regenerate neolink.toml
4. Restart container

---

### 6.2 Rollback Plan

**If Neolink causes issues:**

```bash
# 1. Revert cameras.json
git checkout cameras.json

# 2. Remove Neolink from container
docker compose exec unified-nvr pkill neolink

# 3. Restart container without Neolink
# (or rebuild from previous git commit)
docker compose down unified-nvr
docker compose up -d unified-nvr
```

**Keep backups:**
- `cameras.json.backup.{timestamp}`
- `Dockerfile.backup.{timestamp}`
- `docker-compose.yml.backup.{timestamp}`

---

## Summary of Files to Modify

### Backend (Python):
1. ✅ `0_MAINTENANCE_SCRIPTS/generate_neolink_config.py` (NEW)
2. ✅ `streaming/handlers/reolink_stream_handler.py` (MODIFY)
3. ✅ `stream_manager.py` (MODIFY - add NEOLINK to valid types)
4. ⚠️ `ffmpeg_params.py` (CHECK - likely no change needed)

### Frontend (JavaScript):
5. ✅ `stream.js` (MODIFY - add NEOLINK to HLS routing)

### Configuration:
6. ✅ `cameras.json` (MODIFY - add stream_type: "NEOLINK" + neolink section)
7. ✅ `config/neolink.toml` (AUTO-GENERATED from cameras.json)

### Docker:
8. ✅ `Dockerfile` (MODIFY - add Neolink binary)
9. ✅ `docker-compose.yml` (MODIFY - expose port 8554)

### Documentation:
10. ✅ `README_project_history.md` (UPDATE - document Neolink integration)

---

## Next Steps (Ordered)

**Step 1:** ✅ Build Neolink binary (integration script Step 1)
**Step 2:** ✅ Test Neolink standalone (integration script Steps 2-4)
**Step 3:** ⏳ Implement backend updates (this document)
**Step 4:** ⏳ Implement frontend updates (this document)
**Step 5:** ⏳ Docker integration (integration script Steps 5-7)
**Step 6:** ⏳ Testing and validation (integration script Step 8 + this doc Phase 5)
**Step 7:** ⏳ Production deployment and monitoring

---

**Created:** October 23, 2025
**Status:** Ready for implementation after Neolink build completes