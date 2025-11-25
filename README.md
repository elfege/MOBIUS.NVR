# Unified NVR System

A multi-vendor Network Video Recorder built with Flask, FFmpeg, and MediaMTX. Provides unified streaming, PTZ control, motion detection, and recording across Eufy, Reolink, UniFi, and Amcrest cameras.

## Overview

The Unified NVR abstracts vendor-specific camera protocols behind a common streaming interface. Camera RTSP sources are ingested by FFmpeg, transcoded as needed, and packaged as HLS streams via MediaMTX for browser playback. The system supports 17+ cameras with sub-2-second latency using Low-Latency HLS.

## Features

- **Multi-Vendor Support**: Eufy, Reolink, UniFi Protect, Amcrest cameras
- **Streaming Protocols**: HLS, Low-Latency HLS, MJPEG proxy, RTMP/FLV
- **PTZ Control**: ONVIF and vendor-specific (Amcrest CGI) pan/tilt/zoom
- **Recording**: Continuous (24/7) and motion-triggered recording
- **Snapshots**: Periodic JPEG capture from streams
- **Health Monitoring**: Backend watchdog + frontend blank-frame detection
- **Credential Security**: AWS Secrets Manager integration
- **Docker Deployment**: Full containerization with docker-compose

## Architecture

```
Browser (HLS.js) --> Flask (app.py:5000) --> StreamManager --> Vendor Handlers
                                                                    |
                                                              FFmpeg Processes
                                                                    |
                                                              MediaMTX (:8889)
                                                                    |
                                                              IP Cameras (RTSP)
```

Key design patterns:
- **Strategy Pattern**: Vendor-specific handlers implement common StreamHandler interface
- **Thread-Safe State**: RLock-protected stream dictionary with per-camera restart locks
- **Credential Providers**: Per-vendor classes fetch credentials from AWS Secrets Manager

## Quick Start

### Prerequisites

- Docker Engine 20.10+
- Docker Compose 2.0+
- AWS credentials configured (for Secrets Manager)
- Network access to cameras

### Deployment

1. **Clone and configure:**
   ```bash
   cd ~/0_NVR
   cp config/cameras.json.example config/cameras.json
   # Edit cameras.json with your camera details
   ```

2. **Set environment variables:**
   ```bash
   cp .env.example .env
   # Edit .env with AWS credentials, ports, etc.
   ```

3. **Deploy:**
   ```bash
   ./deploy.sh
   ```

4. **Access the interface:**
   ```
   http://<server-ip>:5000/streams
   ```

## Directory Structure

```
0_NVR/
├── app.py                      # Flask application entry point
├── config/
│   ├── cameras.json            # Camera configurations
│   ├── recording_settings.json # Recording parameters
│   ├── eufy_bridge.json        # Eufy bridge config
│   ├── unifi_protect.json      # UniFi Protect config
│   └── reolink.json            # Reolink config
├── streaming/
│   ├── stream_manager.py       # Stream orchestration
│   ├── stream_handler.py       # Base handler class
│   ├── ffmpeg_params.py        # FFmpeg parameter builder
│   └── handlers/
│       ├── eufy_stream_handler.py
│       ├── reolink_stream_handler.py
│       ├── unifi_stream_handler.py
│       └── amcrest_stream_handler.py
├── services/
│   ├── camera_repository.py    # Camera config access
│   ├── credentials/            # Vendor credential providers
│   ├── recording/
│   │   ├── recording_service.py
│   │   ├── snapshot_service.py
│   │   └── storage_manager.py
│   ├── motion/
│   │   └── reolink_motion_service.py
│   ├── ptz/
│   │   ├── amcrest_ptz_handler.py
│   │   └── ptz_validator.py
│   ├── onvif/
│   │   ├── onvif_ptz_handler.py
│   │   └── onvif_event_listener.py
│   └── eufy/
│       ├── eufy_bridge.py
│       └── eufy_bridge_watchdog.py
├── static/js/
│   ├── streaming/              # HLS, MJPEG, health modules
│   ├── controllers/            # PTZ, recording controllers
│   └── settings/               # UI settings handlers
├── templates/
│   └── streams.html            # Main streaming interface
├── docker-compose.yml
├── Dockerfile
├── deploy.sh
└── requirements.txt
```

## Configuration

### cameras.json

Each camera entry requires:

```json
{
  "CAMERA_SERIAL": {
    "name": "Front Door",
    "type": "reolink",
    "serial": "CAMERA_SERIAL",
    "capabilities": ["streaming", "PTZ"],
    "stream_type": "LL_HLS",
    "rtsp": {
      "host": "192.168.1.100",
      "port": 554,
      "path": "/h264Preview_01_sub"
    },
    "rtsp_input": {
      "rtsp_transport": "tcp",
      "timeout": "30000000"
    },
    "rtsp_output": {
      "c:v": "copy",
      "resolution_sub": "320x240",
      "resolution_main": "1280x720"
    },
    "ll_hls": {
      "publisher": {
        "protocol": "rtmp",
        "host": "nvr-packager",
        "port": 1935,
        "path": "front_door"
      },
      "video": {
        "c:v": "libx264",
        "preset": "veryfast",
        "tune": "zerolatency"
      },
      "audio": {
        "enabled": false
      }
    }
  }
}
```

### Vendor-Specific Configuration

| Vendor | Auth Method | RTSP URL Format |
|--------|-------------|-----------------|
| Eufy | Per-camera via bridge | `rtsp://user:pass@bridge:554/live0` |
| Reolink | Camera credentials | `rtsp://user:pass@cam:554/h264Preview_01_sub` |
| UniFi | Protect console API | `rtsps://console:7441/proxy_url` |
| Amcrest | Camera credentials | `rtsp://user:pass@cam:554/cam/realmonitor` |

### Environment Variables

```bash
# AWS Secrets Manager
AWS_ACCESS_KEY_ID=xxx
AWS_SECRET_ACCESS_KEY=xxx
AWS_DEFAULT_REGION=us-east-1

# Eufy Bridge
USE_EUFY_BRIDGE=true
USE_EUFY_BRIDGE_WATCHDOG=true

# Health Monitoring
UI_HEALTH_ENABLED=true
UI_HEALTH_SAMPLE_INTERVAL_MS=2000
UI_HEALTH_CONSECUTIVE_BLANK_NEEDED=10
ENABLE_WATCHDOG=false

# Server
FLASK_PORT=5000
```

## API Endpoints

### Streaming

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/stream/start/<camera_id>` | POST | Start camera stream |
| `/api/stream/stop/<camera_id>` | POST | Stop camera stream |
| `/api/stream/restart/<camera_id>` | POST | Restart camera stream |
| `/api/streams/<camera_id>/playlist.m3u8` | GET | HLS playlist (classic) |
| `/hls/<path>/index.m3u8` | GET | LL-HLS playlist (via MediaMTX) |

### Recording

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/recording/start/<camera_id>` | POST | Start manual recording |
| `/api/recording/stop/<camera_id>` | POST | Stop recording |
| `/api/recording/continuous/start/<camera_id>` | POST | Start 24/7 recording |
| `/api/recording/active` | GET | List active recordings |

### PTZ Control

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/ptz/<camera_id>/move` | POST | Move camera (pan/tilt) |
| `/api/ptz/<camera_id>/stop` | POST | Stop movement |
| `/api/ptz/<camera_id>/preset/<preset_id>` | POST | Go to preset |
| `/api/ptz/<camera_id>/presets` | GET | List presets |

### System

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/status` | GET | System status |
| `/api/cameras` | GET | Camera list |
| `/api/cameras/<camera_id>` | GET | Camera details |

## Streaming Protocols

### Low-Latency HLS (Recommended)

- Latency: ~1.8 seconds (browser floor)
- Config: `"stream_type": "LL_HLS"` in cameras.json
- Flow: Camera → FFmpeg → MediaMTX (RTMP) → HLS.js

### Classic HLS

- Latency: 3-6 seconds
- Config: `"stream_type": "HLS"` in cameras.json
- Flow: Camera → FFmpeg → Local segments → Browser

### MJPEG Proxy

- Latency: Sub-second
- Config: `"stream_type": "MJPEG"` in cameras.json
- Use case: Legacy browsers, low-bandwidth scenarios

## Docker Services

```yaml
services:
  unified-nvr:        # Flask application
  nvr-packager:       # MediaMTX HLS packager
  postgrest:          # Recording metadata API
  postgres:           # Recording database
```

## Troubleshooting

### Stream not loading

```bash
# Check FFmpeg processes
docker exec unified-nvr ps aux | grep ffmpeg

# Check stream manager logs
docker logs unified-nvr --tail 100 | grep -i stream

# Verify MediaMTX paths
curl http://localhost:8889/v3/paths/list
```

### Camera connection failures

```bash
# Test RTSP directly
ffprobe -rtsp_transport tcp rtsp://user:pass@camera:554/path

# Check credential provider
docker exec unified-nvr python -c "from services.credentials.reolink_credential_provider import ReolinkCredentialProvider; print(ReolinkCredentialProvider().get_credentials('camera_id'))"
```

### High CPU usage

- Reduce resolution in `rtsp_output.resolution_sub`
- Use `"c:v": "copy"` instead of transcode when camera supports H.264
- Decrease frame rate with `"r": 15` in rtsp_output

### Blank frames in browser

- Check `ui_health_monitor` settings in cameras.json
- Verify MediaMTX is receiving stream: `curl http://localhost:8889/v3/paths/get/<path>`
- Check browser console for HLS.js errors

## Performance Considerations

- **HLS Latency Floor**: Browser-based HLS has unavoidable ~1.8s minimum latency due to segmentation and decoding pipeline
- **Hardware Decoding**: Browser hardware acceleration is heuristic-based and cannot be forced
- **Concurrent Streams**: Each camera spawns one FFmpeg process; monitor CPU/memory accordingly
- **Audio Disable**: Set `"audio": {"enabled": false}` to reduce bandwidth and avoid codec issues

## Development

### Running locally (non-Docker)

```bash
# Install dependencies
pip install -r requirements.txt

# Start MediaMTX separately
./mediamtx

# Run Flask app
python app.py
```

### Adding a new vendor

1. Create `streaming/handlers/<vendor>_stream_handler.py`
2. Implement `StreamHandler` interface methods
3. Create `services/credentials/<vendor>_credential_provider.py`
4. Register handler in `StreamManager.__init__()`

## Documentation

- `docs/NVR_engineering_architecture.html` - Visual architecture diagrams
- `docs/README_project_history.md` - Development session logs
- `docs/README_Docker_Deployment_Guide.md` - Detailed deployment instructions
- `docs/README_Motion_Detection_Recording_Architecture.md` - Recording system details

## Known Limitations

- Dual-stream (simultaneous sub + main) requires composite key architecture (not yet stable)
- Eufy cameras require P2P bridge service running
- ONVIF event listener partially implemented
- UniFi Protect requires valid console session

## License

Private project. Not for redistribution.