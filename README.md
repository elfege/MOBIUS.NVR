# MOBIUS.NVR

A multi-vendor Network Video Recorder built with Flask, PostgreSQL, FFmpeg, MediaMTX, and go2rtc. Provides unified streaming, PTZ control, motion detection, recording, and per-user preferences across Eufy, Reolink, UniFi, Amcrest, and SV3C cameras.

## Overview

MOBIUS.NVR abstracts vendor-specific camera protocols behind a common streaming interface. Each camera's streaming hub (MediaMTX or go2rtc) is configurable per-camera. The system supports 17+ cameras with multiple latency options: WebRTC (~200ms), Low-Latency HLS (~2s), Classic HLS (~4s), MJPEG, or snapshot polling.

The database is the runtime source of truth — `cameras.json` is a seed file synced to PostgreSQL on startup. All runtime configuration, credentials, and user preferences live in the database.

## Features

- **Multi-Vendor Support**: Eufy, Reolink, UniFi Protect, Amcrest, SV3C cameras
- **Dual Streaming Hub**: Per-camera choice of MediaMTX or go2rtc as the streaming relay
- **Streaming Protocols**: WebRTC (~200ms), Low-Latency HLS (~2s), Classic HLS, MJPEG proxy, snapshot polling
- **Neolink Bridge**: Baichuan-to-RTSP protocol bridge for Reolink E1 cameras via go2rtc
- **Two-Way Audio**: Talkback for Eufy cameras; ONVIF backchannel via go2rtc for SV3C/Amcrest
- **PTZ Control**: ONVIF, Amcrest CGI, Reolink Baichuan pan/tilt/zoom with preset caching
- **Recording**: Continuous (24/7), motion-triggered, and manual recording with timeline playback
- **Motion Detection**: Reolink Baichuan, ONVIF PullPoint, FFmpeg scene detection
- **Health Monitoring**: Backend watchdog with exponential backoff + frontend blank-frame/stale-frame detection
- **Per-User Preferences**: Stream type, display order, camera visibility, grid layout mode, video fit — all per-user in DB
- **Grid Layout Modes**: Uniform, last-row stretch, auto-fit, masonry — with 3 video fit options (cover/contain/fill)
- **User Authentication**: Login with bcrypt, per-user camera access control, trusted network auto-login, viewer role
- **Monitor Standby Detection**: Page Visibility API tears down streams when tab hidden, auto-reloads on wake
- **Credential Security**: AES-256-GCM encrypted credentials in PostgreSQL (AWS Secrets Manager for initial seeding)
- **Power Cycle Safety**: Optional auto power-cycle for Hubitat-connected cameras (disabled by default, 24h cooldown)
- **HTTPS/TLS**: Nginx reverse proxy with HTTP/2 support
- **Docker Deployment**: Full containerization with docker-compose (7 services)

## Architecture

```
                              +------------------------------------------------------+
                              |                    Browser                           |
                              |  +--------------+  +--------------+  +-------------+ |
                              |  |  WebRTC      |  |   HLS.js     |  |   MJPEG     | |
                              |  |  (~200ms)    |  |  (~2-4s)     |  |  (direct)   | |
                              |  +------+-------+  +------+-------+  +------+------+ |
                              +---------+-----------------+------------------+-------+
                                        |                 |                  |
                              +---------v-----------------v------------------v--------+
                              |           Nginx (nvr-edge) - HTTPS/HTTP2              |
                              |              :8443 (HTTPS) / :8081 (HTTP->301)        |
                              +----------------------------+--------------------------+
                                                           |
                              +----------------------------v--------------------------+
                              |                 Flask (app.py:5000)                   |
                              |          StreamManager + Camera Repository            |
                              |              PostgreSQL (source of truth)             |
                              +------+----------------------------+-------------------+
                                     |                            |
                    +----------------v-----------+   +------------v------------------+
                    |     MediaMTX (packager)     |   |       go2rtc (hub)           |
                    |  HLS :8888 | WebRTC :8889   |   |  WebRTC :8556 | RTSP :8555   |
                    |  RTSP :8554 (internal)      |   |  API :1984                   |
                    +----------------+-----------+   +------------+------------------+
                                     |                            |
                    +----------------v----------------------------v------------------+
                    |                     FFmpeg Processes                           |
                    |      (transcode, publish to streaming hub, record)             |
                    +----------------+----------------------------+------------------+
                                     |                            |
                    +----------------v-----------+   +------------v------------------+
                    |      IP Cameras (RTSP)      |   |    Neolink (Baichuan->RTSP)   |
                    | Eufy, Reolink, UniFi,       |   |    E1 cameras via go2rtc      |
                    | Amcrest, SV3C               |   |    :8554 RTSP output          |
                    +-----------------------------+   +-------------------------------+
```

Key design patterns:
- **Strategy Pattern**: Vendor-specific handlers implement common StreamHandler interface
- **Streaming Hub Abstraction**: `streaming_hub.py` resolves RTSP source URLs based on per-camera hub assignment
- **Database-Driven Config**: `cameras.json` seeds DB on startup; runtime reads from PostgreSQL via `camera_repository.py`
- **Credential Providers**: Per-vendor classes read encrypted credentials from DB, with env var fallback
- **Thread-Safe State**: RLock-protected stream dictionary with per-camera restart locks

## Data Architecture

```
cameras.json (seed file)
    |
    v  synced on startup by camera_config_sync.py
DB: cameras table (RUNTIME SOURCE OF TRUTH)
    |
    v  overridden per-user at runtime
DB: user_camera_preferences table (per-user overrides)
```

- **`cameras.json`** is a seed file, not the runtime source of truth
- **DB `cameras` table** is what the app reads at runtime via `camera_repository.py`
- **`camera_credentials` table** stores all credentials (AES-256-GCM encrypted)
- **`user_camera_preferences`** stores per-user overrides (stream type, display order, visibility, grid layout)

## Quick Start

### Prerequisites

- Docker Engine 20.10+
- Docker Compose 2.0+
- AWS credentials configured (for initial credential seeding) or manual DB entry via UI
- Network access to cameras

### Deployment

1. **Clone and configure:**
   ```bash
   git clone https://github.com/elfege/MOBIUS.NVR.git
   cd MOBIUS.NVR
   cp config/cameras.json.example config/cameras.json
   # Edit cameras.json with your camera details
   ```

2. **Set environment variables:**
   ```bash
   cp .env.example .env
   # Edit .env with ports, feature flags, etc.
   ```

3. **Deploy:**
   ```bash
   ./deploy.sh
   ```

4. **Install git hooks (for development):**
   ```bash
   ./scripts/hooks/install-hooks.sh
   ```

5. **Access the interface:**
   ```
   https://<server-ip>:8443/streams
   ```

## Directory Structure

```
MOBIUS.NVR/
├── app.py                         # Flask application entry point
├── config/
│   ├── cameras.json               # Camera seed config (synced to DB on startup)
│   ├── recording_settings.json    # Recording parameters
│   └── go2rtc.yaml.template       # go2rtc config template
├── routes/                        # Flask route blueprints
│   ├── camera.py                  # Camera state, config, health APIs
│   ├── streaming.py               # Stream start/stop/restart
│   ├── recording.py               # Recording management
│   ├── ptz.py                     # PTZ control
│   ├── auth.py                    # Authentication, user management
│   └── settings_routes.py         # User preferences API
├── streaming/
│   ├── stream_manager.py          # Stream orchestration
│   ├── stream_handler.py          # Base handler class
│   ├── ffmpeg_params.py           # FFmpeg parameter builder
│   └── handlers/                  # Vendor-specific stream handlers
├── services/
│   ├── camera_repository.py       # Camera config access (DB-backed)
│   ├── camera_config_sync.py      # cameras.json -> DB sync on startup
│   ├── camera_state_tracker.py    # Health state machine (ONLINE/DEGRADED/OFFLINE)
│   ├── stream_watchdog.py         # Backend stream restart watchdog
│   ├── streaming_hub.py           # RTSP source URL resolution (MediaMTX vs go2rtc)
│   ├── credentials/               # Per-vendor credential providers (DB + env fallback)
│   ├── recording/                 # Recording, snapshots, storage, timeline
│   ├── motion/                    # Motion detection (Baichuan, ONVIF, FFmpeg)
│   ├── ptz/                       # PTZ handlers (ONVIF, Amcrest, Baichuan)
│   └── eufy/                      # Eufy bridge client and watchdog
├── static/
│   ├── css/components/            # Modular CSS (grid, fullscreen, PTZ, etc.)
│   └── js/
│       ├── streaming/             # WebRTC, HLS, MJPEG, health, visibility modules
│       ├── controllers/           # PTZ, recording, camera selector, power
│       ├── modals/                # Settings, timeline, user management modals
│       ├── layout/                # Grid layout engine (4 modes)
│       └── settings/              # Fullscreen handler, settings manager
├── templates/
│   └── streams.html               # Main streaming interface
├── psql/
│   ├── init-db.sql                # Database initialization
│   └── migrations/                # Schema migrations (026+)
├── scripts/
│   ├── generate_streaming_configs.py  # MediaMTX + go2rtc + neolink config generator
│   ├── hooks/                     # Git hooks (post-merge auto-push to public)
│   └── ...                        # Credential seeding, recording indexing
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

## Configuration

### cameras.json

Each camera entry requires a serial number as its key:

```json
{
  "T8416P0023352DA9": {
    "name": "Front Door",
    "type": "reolink",
    "serial": "T8416P0023352DA9",
    "capabilities": ["streaming", "PTZ"],
    "stream_type": "WEBRTC",
    "streaming_hub": "mediamtx",
    "rtsp": {
      "host": "192.168.1.100",
      "port": 554,
      "path": "/h264Preview_01_sub"
    }
  }
}
```

### Streaming Hub Options

| Hub | Config Value | Use Case |
|-----|-------------|----------|
| MediaMTX | `"streaming_hub": "mediamtx"` | Default. Camera -> FFmpeg -> MediaMTX -> browser |
| go2rtc | `"streaming_hub": "go2rtc"` | Single-consumer cameras, Neolink/Baichuan devices |

### Vendor-Specific Configuration

| Vendor | Auth Method | RTSP URL Format | Notes |
|--------|-------------|-----------------|-------|
| Eufy | Camera credentials | `rtsp://user:pass@cam:554/live0` | PTZ cameras via RTSP; doorbell requires Home Base 3 |
| Reolink | Camera credentials | `rtsp://user:pass@cam:554/h264Preview_01_sub` | Full PTZ via Baichuan protocol |
| UniFi | Protect console API | `rtsps://console:7441/proxy_url` | Requires valid console session |
| Amcrest | Camera credentials | `rtsp://user:pass@cam:554/cam/realmonitor` | PTZ via ONVIF or CGI |
| SV3C | Camera credentials | `rtsp://user:pass@cam:554/stream1` | Budget cameras, single RTSP connection |

## Docker Services

```yaml
services:
  nvr-edge:           # Nginx reverse proxy - HTTPS (:8443), HTTP redirect (:8081)
  unified-nvr:        # Flask application (:5000 internal)
  nvr-packager:       # MediaMTX - HLS (:8888), WebRTC (:8889), RTSP (:8554)
  nvr-go2rtc:         # go2rtc - WebRTC (:8556), RTSP (:8555), API (:1984)
  nvr-neolink:        # Neolink Baichuan->RTSP bridge (:8554)
  nvr-postgrest:      # PostgREST API (:3001)
  nvr-postgres:       # PostgreSQL database (:5432)
```

## API Endpoints

### Streaming

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/stream/start/<camera_id>` | POST | Start camera stream |
| `/api/stream/stop/<camera_id>` | POST | Stop camera stream |
| `/api/stream/restart/<camera_id>` | POST | Restart camera stream |
| `/api/camera/state/<camera_id>` | GET | Camera health state (availability, backoff, errors) |
| `/api/camera/states` | GET | Batch health state for all cameras |

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

### User Preferences

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/my-preferences` | GET/PUT | Get/set user preferences (grid layout, video fit) |
| `/api/cameras/<id>/display` | PUT | Per-camera display settings (stream type, order, visibility) |

## Streaming Protocols

| Protocol | Latency | Config | Best For |
|----------|---------|--------|----------|
| WebRTC (WHEP) | ~200ms | `"stream_type": "WEBRTC"` | Real-time monitoring, PTZ, interactive use |
| Low-Latency HLS | ~2-4s | `"stream_type": "LL_HLS"` | Multi-device viewing, iOS compatibility |
| Classic HLS | 4-8s | `"stream_type": "HLS"` | Maximum compatibility, archive playback |
| MJPEG | Sub-second | `"stream_type": "MJPEG"` | Legacy browsers, budget cameras |
| go2rtc WebRTC | ~200ms | `"stream_type": "GO2RTC"` | Single-consumer cameras, Neolink devices |
| Snapshot | 1fps | `"stream_type": "SNAPSHOT"` | iOS grid view, minimal bandwidth |

## Troubleshooting

### Stream not loading

```bash
# Check FFmpeg processes
docker exec unified-nvr ps aux | grep ffmpeg

# Check stream manager logs
docker logs unified-nvr --tail 100 | grep -i stream

# Verify MediaMTX paths
curl http://localhost:8889/v3/paths/list

# Check go2rtc streams
curl http://localhost:1984/api/streams
```

### Camera connection failures

```bash
# Test RTSP directly
ffprobe -rtsp_transport tcp rtsp://user:pass@camera:554/path

# Check camera health state
curl http://localhost:5000/api/camera/state/CAMERA_SERIAL
```

## Development

### Adding a new vendor

1. Create `streaming/handlers/<vendor>_stream_handler.py` implementing `StreamHandler`
2. Create `services/credentials/<vendor>_credential_provider.py`
3. Register handler in `StreamManager.__init__()`
4. Add vendor type to `cameras.json` schema

### Git hooks

After cloning, install hooks for automatic public repo sync:

```bash
./scripts/hooks/install-hooks.sh
```

The `post-merge` hook automatically pushes `main` to the public repo when feature branches are merged.

## Documentation

- `docs/nvr_engineering_architecture.html` - Visual architecture diagrams
- `docs/architecture_diagrams.html` - System architecture diagrams
- `docs/DIAGRAMS/` - Component-level diagrams (health monitor, MJPEG flow)
- `docs/EUFY_BRIDGE_DOC/` - Eufy security bridge documentation

## Known Limitations

- Eufy Video Doorbell E340: Pure P2P device (no RTSP), requires Home Base 3 for streaming
- go2rtc `eufy://` scheme not compiled in standard builds (requires CGO + native bindings)
- Neolink E1 PTZ latency: ~4-5s per command (Baichuan protocol overhead)
- ONVIF event listener partially implemented
- UniFi Protect requires valid console session

## License

Copyright (c) 2024-2026 Elfege Leylavergne.

Licensed under the [Business Source License 1.1](LICENSE).

- **Personal, educational, and non-commercial use**: Permitted without a commercial license.
- **Commercial use**: Requires a paid license. Contact elfege@elfege.com.
- **Change Date**: April 9, 2036 — on this date, the code converts to Apache License 2.0.
