# Unified NVR System

A multi-vendor Network Video Recorder built with Flask, FFmpeg, and MediaMTX. Provides unified streaming, PTZ control, motion detection, and recording across Eufy, Reolink, UniFi, and Amcrest cameras.

## Overview

The Unified NVR abstracts vendor-specific camera protocols behind a common streaming interface. Camera RTSP sources are ingested by FFmpeg, transcoded as needed, and packaged via MediaMTX for browser playback. The system supports 17+ cameras with multiple latency options: WebRTC (~200ms), Low-Latency HLS (~2s), or Classic HLS (~4s).

## Features

- **Multi-Vendor Support**: Eufy, Reolink, UniFi Protect, Amcrest cameras
- **Streaming Protocols**: WebRTC (~200ms), Low-Latency HLS (~2s), Classic HLS, MJPEG proxy
- **PTZ Control**: ONVIF and vendor-specific (Amcrest CGI) pan/tilt/zoom
- **Recording**: Continuous (24/7) and motion-triggered recording
- **Snapshots**: Periodic JPEG capture from streams
- **Health Monitoring**: Backend watchdog + frontend blank-frame detection
- **Credential Security**: AWS Secrets Manager integration
- **Docker Deployment**: Full containerization with docker-compose

## Architecture

```
                              ┌──────────────────────────────────────────────────────┐
                              │                    Browser                            │
                              │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐   │
                              │  │  WebRTC     │  │   HLS.js    │  │   MJPEG     │   │
                              │  │  (~200ms)   │  │  (~2-4s)    │  │  (direct)   │   │
                              │  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘   │
                              └─────────┼────────────────┼────────────────┼──────────┘
                                        │                │                │
                              ┌─────────▼────────────────▼────────────────▼──────────┐
                              │                 Flask (app.py:5000)                   │
                              │                   StreamManager                       │
                              └───────────────────────┬───────────────────────────────┘
                                                      │
                              ┌───────────────────────▼───────────────────────────────┐
                              │                  FFmpeg Processes                      │
                              │    (transcode, split sub/main, publish to MediaMTX)   │
                              └───────────────────────┬───────────────────────────────┘
                                                      │
                              ┌───────────────────────▼───────────────────────────────┐
                              │              MediaMTX (nvr-packager)                   │
                              │  ┌──────────┐  ┌──────────┐  ┌──────────────────────┐ │
                              │  │  :8888   │  │  :8889   │  │      :8554           │ │
                              │  │  HLS     │  │  WebRTC  │  │  RTSP (internal)     │ │
                              │  │  LL-HLS  │  │  WHEP    │  │  motion/recording    │ │
                              │  └──────────┘  └──────────┘  └──────────────────────┘ │
                              └───────────────────────┬───────────────────────────────┘
                                                      │
                              ┌───────────────────────▼───────────────────────────────┐
                              │                 IP Cameras (RTSP)                      │
                              │      Eufy, Reolink, UniFi, Amcrest, SV3C, Neolink     │
                              └───────────────────────────────────────────────────────┘
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
│   ├── streaming/              # HLS, WebRTC, MJPEG, health modules
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

| Vendor | Auth Method | RTSP URL Format | Notes |
|--------|-------------|-----------------|-------|
| Eufy | Direct camera credentials | `rtsp://user:pass@cam:554/live0` | No PTZ (bridge removed due to authentication issues) |
| Reolink | Camera credentials | `rtsp://user:pass@cam:554/h264Preview_01_sub` | Full PTZ via Baichuan protocol |
| UniFi | Protect console API | `rtsps://console:7441/proxy_url` | Requires valid console session |
| Amcrest | Camera credentials | `rtsp://user:pass@cam:554/cam/realmonitor` | PTZ via ONVIF or CGI |

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

### WebRTC (Lowest Latency)

- Latency: ~200-500ms (sub-second)
- Config: `"stream_type": "WEBRTC"` in cameras.json
- Flow: Camera → FFmpeg → MediaMTX → WebRTC (WHEP) → Browser
- Ports: 8889 (WHEP signaling), 8189/UDP (media)
- Best for: Real-time monitoring, PTZ control, interactive use
- Limitation: LAN-only (no STUN/TURN configured)

### Low-Latency HLS

- Latency: ~2-4 seconds
- Config: `"stream_type": "LL_HLS"` in cameras.json
- Flow: Camera → FFmpeg → MediaMTX (RTMP) → HLS.js
- Best for: Multi-device viewing, more compatible than WebRTC

### Classic HLS

- Latency: 4-8 seconds
- Config: `"stream_type": "HLS"` in cameras.json
- Flow: Camera → FFmpeg → Local segments → Browser
- Best for: Maximum compatibility, archive playback

### MJPEG Proxy

- Latency: Sub-second
- Config: `"stream_type": "MJPEG"` in cameras.json
- Use case: Legacy browsers, cameras with native MJPEG support (Reolink)

### MJPEG for Mobile Grid View (Beta)

- **Purpose**: Fast multi-camera grid loading on mobile devices
- **Problem solved**: Browsers limit HTTP connections to ~6 per domain; with 16 cameras, 10 must wait
- **Solution**: WebSocket-based MJPEG multiplexing - all cameras over single connection
- **URL parameters**:
  - `?forceMJPEG=true` - Use MJPEG instead of HLS/WebRTC
  - `?useWebSocketMJPEG=true` - Use WebSocket multiplexing (recommended)
- **Mobile**: `forceMJPEG` is automatic; only need `useWebSocketMJPEG=true`
- **Example**: `https://server:8443/streams?forceMJPEG=true&useWebSocketMJPEG=true`

## Docker Services

```yaml
services:
  unified-nvr:        # Flask application (:5000)
  nvr-packager:       # MediaMTX - HLS (:8888), WebRTC (:8889), RTSP (:8554)
  nvr-neolink:        # Neolink Baichuan→RTSP bridge
  nvr-postgrest:      # Recording metadata API
  nvr-postgres:       # Recording database
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

- **WebRTC for Lowest Latency**: Use `stream_type: "WEBRTC"` for ~200ms latency (vs 2-4s for LL-HLS)
- **HLS Latency Floor**: Browser-based HLS has ~2s minimum latency due to segment buffering
- **WebRTC LAN-Only**: Current config uses direct ICE without STUN/TURN (add STUN for remote access)
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

- `docs/nvr_engineering_architecture.html` - Visual architecture diagrams
- `docs/README_project_history.md` - Complete development history
- `docs/README_handoff.md` - Recent session changes (for latest modifications)
- `docs/README_Docker_Deployment_Guide.md` - Detailed deployment instructions
- `docs/README_Motion_Detection_Recording_Architecture.md` - Recording system details

## Known Limitations

- Dual-stream (simultaneous sub + main) requires composite key architecture (not yet stable)
- Eufy cameras: Streaming works via direct RTSP, but PTZ is unavailable (bridge removed due to authentication issues)
- ONVIF event listener partially implemented
- UniFi Protect requires valid console session

## License

Private project. Not for redistribution.
