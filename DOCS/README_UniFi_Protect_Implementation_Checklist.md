# Implementation Checklist - UniFi Protect Containerization

## ✅ Phase 1: Project Setup (5 minutes)

- [ ] Navigate to project directory: `cd ~/0_NVR`
- [ ] Copy environment template: `cp .env.template .env`
- [ ] Update `.gitignore` to include new files
- [ ] Verify existing `package.json` and `requirements.txt` are present

## ✅ Phase 2: New Files Creation (10 minutes)

### Core Docker Files
- [ ] Create `Dockerfile` (from artifact)
- [ ] Create `docker-compose.yml` (from artifact)
- [ ] Create `.env.template` (from artifact)
- [ ] Update `.gitignore` (from artifact)

### Service Layer
- [ ] Create `services/unifi_protect_service.py` (from artifact)
- [ ] Keep existing `services/unifi_service.py` (legacy, don't delete yet)
- [ ] Verify `services/camera_base.py` exists

### Deployment
- [ ] Create `deploy.sh` (from artifact)
- [ ] Make executable: `chmod +x deploy.sh`

### Documentation
- [ ] Create `README_DOCKER.md` (from artifact)
- [ ] Save this checklist as `IMPLEMENTATION_CHECKLIST.md`

## ✅ Phase 3: Configuration (10 minutes)

### Update cameras.json
- [ ] Open `config/cameras.json`
- [ ] For UniFi camera entry, update fields:
  ```json
  {
    "68d49398005cf203e400043f": {
      "type": "unifi",
      "name": "G5 Flex",
      "protect_host": "192.168.10.3",           // ADD THIS
      "camera_id": "68d49398005cf203e400043f",  // ADD THIS (use actual ID from Protect)
      "ip": "192.168.10.104",                   // KEEP for reference
      "credentials": {
        "username": "PLACEHOLDER",               // Will use env vars
        "password": "PLACEHOLDER"
      },
      "capabilities": ["streaming"],
      "stream_type": "mjpeg_proxy"
    }
  }
  ```
- [ ] Save `cameras.json`

### Get Camera ID from Protect
If you don't know your camera_id:
```bash
# Method 1: From the bootstrap.json you showed me earlier
grep -o '"id":"[^"]*"' ~/0_UNIFI_NVR/LL-HLS/bootstrap.json | head -1

# Method 2: Use get_token.sh and curl
cd ~/0_UNIFI_NVR/LL-HLS
./get_token.sh
curl -k -b ~/0_UNIFI_NVR/cookies/cookies.txt \
  https://192.168.10.3/proxy/protect/api/bootstrap | \
  jq '.cameras[] | {id, name, type}'
```

Your camera_id: `68d49398005cf203e400043f` ✓ (from your bootstrap.json)

### Configure Credentials

**Option A: Manual**
- [ ] Edit `.env`:
  ```bash
  nano .env
  ```
- [ ] Set values:
  ```bash
  PROTECT_USERNAME=user-api
  PROTECT_SERVER_PASSWORD=your_actual_password_here
  ```

**Option B: AWS Secrets (Recommended)**
- [ ] Verify `.bash_utils` configured: `which pull_secrets_from_aws`
- [ ] Test AWS access: `aws sts get-caller-identity --profile personal`
- [ ] Pull secrets: `pull_secrets_from_aws UniFi-Camera-Credentials`
- [ ] Verify: `echo $PROTECT_USERNAME` should show "user-api"

## ✅ Phase 4: Code Updates (5 minutes)

### Update app.py
- [ ] Open `app.py`
- [ ] Find import section (around line 30), add:
  ```python
  from services.unifi_protect_service import UniFiProtectService
  ```

- [ ] Find UniFi camera initialization (around line 70):
  ```python
  # OLD CODE (comment out or replace):
  # unifi_cameras[camera_id] = UniFiCameraService(config)
  
  # NEW CODE:
  unifi_cameras[camera_id] = UniFiProtectService(config)
  ```

- [ ] Save `app.py`

### Verify Dependencies
- [ ] Check `requirements.txt` has these entries:
  ```
  Flask==3.0.0
  Flask-WTF==1.2.1
  requests==2.31.0
  websockets==12.0
  boto3==1.34.0
  ```

## ✅ Phase 5: Pre-Deployment Testing (Optional but Recommended)

### Test Service Locally (Before Container)
```bash
# Set environment variables
export PROTECT_USERNAME=user-api
export PROTECT_SERVER_PASSWORD=your_password

# Test authentication
python3 << 'EOF'
from services.unifi_protect_service import UniFiProtectService
config = {
    'name': 'Test',
    'protect_host': '192.168.10.3',
    'camera_id': '68d49398005cf203e400043f',
    'credentials': {'username': 'x', 'password': 'x'}
}
svc = UniFiProtectService(config)
print('✓ Authentication:', svc.authenticate())
print('✓ Snapshot size:', len(svc.get_snapshot()) if svc.get_snapshot() else 'FAILED')
EOF
```

Expected output:
```
✓ Authentication: True
✓ Snapshot size: 123456
```

## ✅ Phase 6: Docker Deployment (10 minutes)

### Initial Deployment
- [ ] Run deployment script:
  ```bash
  ./deploy.sh
  ```

### Verify Success
- [ ] Container running: `docker ps | grep unified-nvr`
- [ ] Check logs: `docker-compose logs -f` (Ctrl+C to exit)
- [ ] Test health: `curl http://192.168.10.8:5000/api/status`
- [ ] Test snapshot: `curl http://192.168.10.8:5000/api/unifi/68d49398005cf203e400043f/snapshot -o test.jpg`
- [ ] Open test.jpg: Should show camera image

### Expected Log Output
```
✓ Loaded UniFi camera: G5 Flex
Logging into Protect at 192.168.10.3 for camera G5 Flex
✓ Login successful for G5 Flex
✓ Managers initialized successfully
* Running on http://0.0.0.0:5000
```

## ✅ Phase 7: Integration Testing (15 minutes)

### Web Interface Test
- [ ] Open browser: `http://192.168.10.8:5000`
- [ ] Verify G5 Flex appears in camera list
- [ ] Click on G5 Flex stream
- [ ] Should see MJPEG video feed

### API Endpoint Tests
```bash
# Status
curl http://192.168.10.8:5000/api/status | jq

# Camera list
curl http://192.168.10.8:5000/api/unifi/cameras | jq

# Snapshot
curl http://192.168.10.8:5000/api/unifi/68d49398005cf203e400043f/snapshot -o test.jpg

# MJPEG stream (should download continuously)
curl http://192.168.10.8:5000/api/unifi/68d49398005cf203e400043f/stream/mjpeg --output - | head -c 10000000 > stream_test.mjpeg
```

### Blue Iris Integration (if using)
- [ ] Add new camera in Blue Iris
- [ ] Type: HTTP Live Streaming (HLS, M3U8), MP2TS
- [ ] IP: 192.168.10.8
- [ ] Port: 5000
- [ ] Path: `/api/streams/68d49398005cf203e400043f/playlist.m3u8`
- [ ] Test connection
- [ ] Verify video appears

## ✅ Phase 8: Documentation & Cleanup (5 minutes)

### Update Project History
- [ ] Add entry to `DOCS/README_project_history.md`:
  ```markdown
  ## September 29, 2025: UniFi Protect Containerization Complete
  
  ### Protect API Integration
  - Migrated from direct camera access to Protect API authentication
  - Created `UniFiProtectService` for Protect console integration
  - Camera adopted into UCKG2 Plus (192.168.10.3)
  
  ### Containerization
  - Complete Docker + Docker Compose setup
  - Environment variable credential management
  - AWS Secrets Manager integration
  - Automated deployment via deploy.sh
  
  ### Architecture Changes
  - Auth endpoint: https://192.168.10.3/api/auth/login
  - Snapshot: https://192.168.10.3/proxy/protect/api/cameras/{id}/snapshot
  - RTSP available via bootstrap API
  
  ### Status
  - ✅ Container running on Dell R730xd
  - ✅ G5 Flex streaming via Protect API
  - ✅ MJPEG proxy working
  - ✅ Blue Iris integration maintained
  ```

### Commit Changes
- [ ] Review changes: `git status`
- [ ] Stage files: `git add .`
- [ ] Commit: `git commit -m "feat: UniFi Protect containerization with AWS secrets integration"`
- [ ] Push: `git push` (if using remote)

## ✅ Phase 9: Monitoring Setup (Optional)

### Log Monitoring
- [ ] Create log rotation config
- [ ] Set up log aggregation (Loki profile in docker-compose)
- [ ] Configure alerts for authentication failures

### Health Monitoring
- [ ] Enable Prometheus metrics (optional)
- [ ] Set up Grafana dashboards (optional)
- [ ] Configure Watchtower for auto-updates (optional)

## 🐛 Troubleshooting Quick Reference

### Container won't start
```bash
docker-compose logs
docker-compose down
docker-compose up --build
```

### Auth failures
```bash
# Test Protect login manually
curl -k -X POST https://192.168.10.3/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"user-api","password":"YOUR_PASSWORD"}'
```

### Camera not found
```bash
# List all cameras
curl -k -b cookies.txt https://192.168.10.3/proxy/protect/api/bootstrap | \
  jq '.cameras[] | {id, name, state}'
```

### Snapshot fails
```bash
# Check camera is recording
# Check Protect console UI
# Verify camera_id matches exactly
# Test snapshot endpoint directly
```

## 📊 Success Criteria

- [x] Container builds successfully
- [x] Container starts and passes health check
- [x] Authentication to Protect succeeds
- [x] Snapshot endpoint returns valid JPEG
- [x] MJPEG stream works in browser
- [x] No errors in logs after 5 minutes
- [x] Blue Iris can consume streams (if applicable)

## 🎉 Completion

Once all checkboxes are complete:
- Container is deployed and running
- G5 Flex streaming via Protect API
- Credentials managed securely
- Ready for production use
- Ready to add more Protect cameras

**Estimated Total Time: 60 minutes**

---

**Questions? Check:**
- `README_DOCKER.md` - Detailed deployment guide
- `DOCS/README_project_history.md` - Project context
- Docker logs - `docker-compose logs -f`