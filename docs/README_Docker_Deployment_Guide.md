# Unified NVR - Docker Deployment Guide

Complete containerization guide for the unified camera NVR supporting Eufy and UniFi Protect cameras.

## 📋 Prerequisites

- Docker Engine 24.0+ and Docker Compose V2
- UniFi Protect Console (UCKG2 Plus) at 192.168.10.3
- Local admin account created on Protect (username: `user-api`)
- Network access to cameras on 192.168.10.0/24

## 🚀 Quick Start

### 1. Initial Setup

```bash
# Clone/navigate to project directory
cd ~/0_NVR

# Copy environment template
cp .env.template .env

# Edit credentials
nano .env
```

### 2. Configure Credentials

**Option A: Manual (Simple)**
Edit `.env` directly:
```bash
PROTECT_USERNAME=user-api
PROTECT_SERVER_PASSWORD=your_actual_password
```

**Option B: AWS Secrets Manager (Recommended)**
```bash
# Pull credentials from AWS (if .bash_utils is configured)
source ~/.bash_utils
pull_secrets_from_aws UniFi-Camera-Credentials
```

The deployment script will automatically use AWS credentials if available.

### 3. Prepare Configuration

Ensure `config/cameras.json` exists with  camera definitions. See the example in this repository.

**Critical for UniFi Protect cameras:**
- Use `camera_id` from Protect (not IP address)
- Set `protect_host` to  UCKG2 Plus IP
- Set `type` to `"unifi"`
- Credentials can be placeholders (environment variables override)

Example entry:
```json
{
  "68d49398005cf203e400043f": {
    "type": "unifi",
    "name": "G5 Flex",
    "protect_host": "192.168.10.3",
    "camera_id": "68d49398005cf203e400043f",
    "credentials": {
      "username": "PLACEHOLDER",
      "password": "PLACEHOLDER"
    },
    "capabilities": ["streaming"],
    "stream_type": "mjpeg_proxy"
  }
}
```

### 4. Deploy

```bash
# Make deploy script executable
chmod +x deploy.sh

# Run deployment
./deploy.sh
```

The script will:
- ✅ Check for `.env` file
- ✅ Optionally pull AWS secrets
- ✅ Validate credentials
- ✅ Build Docker image
- ✅ Start container
- ✅ Verify health

## 🔧 Architecture Changes

### Before (Direct Camera Access)
```
app.py → unifi_service.py → http://192.168.10.104/api/1.1/login
                           → http://192.168.10.104/snap.jpeg
```

### After (Protect API)
```
app.py → unifi_protect_service.py → https://192.168.10.3/api/auth/login
                                   → https://192.168.10.3/proxy/protect/api/cameras/{id}/snapshot
```

## 📁 Project Structure

```
~/0_NVR/
├── Dockerfile                    # Container definition
├── docker-compose.yml           # Service orchestration
├── deploy.sh                    # Deployment automation
├── .env.template               # Environment variable template
├── .env                        # Your credentials (gitignored)
├── requirements.txt            # Python dependencies
├── package.json                # Node.js dependencies (Eufy bridge)
├── app.py                      # Flask application
├── config/
│   └── cameras.json           # Camera configuration
├── services/
│   ├── unifi_protect_service.py  # NEW: Protect API integration
│   ├── unifi_service.py          # OLD: Direct camera access (deprecated)
│   ├── eufy_service.py
│   └── camera_base.py
├── streams/                    # HLS output (ephemeral)
└── logs/                       # Application logs
```

## 🔐 Security Considerations

### Credentials Hierarchy
1. **Environment variables** (highest priority)
   - `PROTECT_USERNAME`
   - `PROTECT_SERVER_PASSWORD`
2. **cameras.json** (fallback)
   - `credentials.username`
   - `credentials.password`

### Best Practices
- ✅ Use AWS Secrets Manager for production
- ✅ Keep `.env` out of Git (already in `.gitignore`)
- ✅ Use local Protect account (no MFA complexity)
- ✅ Disable remote access for local account
- ✅ Restrict network access via firewall rules

## 🎯 Service Integration

### Update app.py

Replace direct camera initialization with:

```python
# At the top with other imports
from services.unifi_protect_service import UniFiProtectService

# In the camera initialization section (around line 70)
unifi_cameras = {}
try:
    with open('config/cameras.json', 'r') as f:
        camera_config = json.load(f)
    
    for camera_id, config in camera_config.get('devices', {}).items():
        if config.get('type') == 'unifi':
            config['id'] = camera_id
            # NEW: Use UniFiProtectService instead of UniFiCameraService
            unifi_cameras[camera_id] = UniFiProtectService(config)
            print(f"✓ Loaded UniFi camera: {config['name']}")
except Exception as e:
    traceback.print_exc()
    print(f"Warning: UniFi camera initialization failed: {e}")
```

## 📊 Monitoring & Operations

### View Logs
```bash
# All logs
docker-compose logs -f

# Last 100 lines
docker-compose logs --tail=100

# Specific service
docker-compose logs -f nvr
```

### Container Management
```bash
# Stop container
docker-compose down

# Restart container
docker-compose restart

# Rebuild and restart
docker-compose up -d --build

# Shell access
docker exec -it unified-nvr /bin/bash
```

### Health Checks
```bash
# Check container health
docker ps

# API status endpoint
curl http://192.168.10.8:5000/api/status

# Individual camera stats
curl http://192.168.10.8:5000/api/unifi/68d49398005cf203e400043f/stats
```

## 🐛 Troubleshooting

### Container Won't Start

**Check logs:**
```bash
docker-compose logs
```

**Common issues:**
- Port 5000 already in use → Change in `docker-compose.yml`
- Missing credentials → Check `.env` file
- FFmpeg not installed → Image build issue, rebuild with `--no-cache`

### Authentication Failures

**Verify credentials:**
```bash
# Test Protect login manually
curl -k -X POST https://192.168.10.3/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"user-api","password":"YOUR_PASSWORD"}'
```

**Expected response:** HTTP 200 with session cookies

### Camera Not Found

**Check camera_id:**
```bash
# Get bootstrap data (lists all cameras)
curl -k -b cookies.txt https://192.168.10.3/proxy/protect/api/bootstrap | jq '.cameras[] | {id, name}'
```

**Verify:**
- `camera_id` matches Protect ID exactly
- Camera is adopted and connected in Protect
- Camera isn't being deleted or provisioning

### Snapshot/Stream Issues

**Test snapshot endpoint:**
```bash
curl -k -b cookies.txt https://192.168.10.3/proxy/protect/api/cameras/68d49398005cf203e400043f/snapshot -o test.jpg
```

**Check for:**
- Valid session (re-authenticate if 401)
- Correct camera_id
- Camera recording enabled in Protect
- Network connectivity to Protect console

## 🔄 Migration from Direct Camera Access

### Step 1: Update Service Import
Change `UniFiCameraService` to `UniFiProtectService` in `app.py`

### Step 2: Update cameras.json
Add Protect-specific fields:
- `protect_host`
- `camera_id` (from Protect, not IP)

### Step 3: Test Authentication
```bash
# From host (before containerizing)
export PROTECT_USERNAME=user-api
export PROTECT_SERVER_PASSWORD=your_password
python3 -c "from services.unifi_protect_service import UniFiProtectService; \
    config={'name':'Test','protect_host':'192.168.10.3','camera_id':'68d49398005cf203e400043f','credentials':{'username':'x','password':'x'}}; \
    svc=UniFiProtectService(config); \
    print('Auth:', svc.authenticate())"
```

### Step 4: Deploy Container
```bash
./deploy.sh
```

## 📈 Performance Tuning

### Resource Limits
Adjust in `docker-compose.yml`:
```yaml
deploy:
  resources:
    limits:
      cpus: '4.0'        # Based on camera count
      memory: 2G         # Increase for more cameras
```

**Rough estimates:**
- 1-3 cameras: 1 CPU, 512MB RAM
- 4-8 cameras: 2 CPUs, 1GB RAM
- 9-15 cameras: 4 CPUs, 2GB RAM

### HLS Settings
Adjust in `.env`:
```bash
HLS_SEGMENT_TIME=4      # Lower = less latency, more CPU
HLS_MAX_SEGMENTS=6      # Lower = less storage, shorter buffer
```

## 🔌 Integration with Blue Iris

### Add Camera in Blue Iris
1. **Camera type:** HTTP Live Streaming (HLS, M3U8), MP2TS
2. **Network/IP:** 192.168.10.8 (or Docker host IP)
3. **Port:** 5000
4. **Path:** `/api/streams/CAMERA_ID/playlist.m3u8`
   - For Eufy: Use serial number (e.g., `T8416P0023390DE9`)
   - For UniFi: Use camera_id (e.g., `68d49398005cf203e400043f`)
5. **Authentication:** None (if container on trusted network)

## 🚦 Next Steps

1. ✅ Verify all cameras appear in `/api/status`
2. ✅ Test snapshots via `/api/unifi/{camera_id}/snapshot`
3. ✅ Test MJPEG streams via `/api/unifi/{camera_id}/stream/mjpeg`
4. ✅ Configure Blue Iris to consume streams
5. ✅ Set up monitoring/alerting
6. ✅ Configure automatic container updates (Watchtower)

## 📝 Notes

- **SSL Verification:** Disabled for self-signed Protect certificates
- **Session Management:** 1-hour sessions, auto-renewal
- **Resource Cleanup:** Sessions recycled every 2 hours
- **Bootstrap Caching:** Camera metadata cached for 5 minutes
- **RTSP Support:** Available via `get_camera_rtsp_url()` method

## 🆘 Support

Check project documentation:
- `README_project_history.md` - Development timeline
- `DOCS/system_overview.html` - Architecture diagrams
- GitHub Issues - Community support

