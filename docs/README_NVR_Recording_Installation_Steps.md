# NVR Recording System - Installation Steps

**Document Purpose**: Track all installation steps for creating automated deployment script.

**Date Started**: 2025-11-10  
**Last Updated**: 2025-11-12 22:30 UTC

---

## Prerequisites

### System Requirements
- Ubuntu 24.04 LTS (or compatible)
- Docker + Docker Compose v2
- AWS CLI configured with SSO
- Minimum 500GB free space on `/mnt/sdc` (or equivalent fast disk)
- 19TB+ free space on `/mnt/THE_BIG_DRIVE` (or equivalent USB/archive disk)

### Required Tools
- `jq` (JSON processor)
- `openssl` (for password generation)
- `aws` CLI (for Secrets Manager)
- `curl` (for testing APIs)

### Network Access
- Outbound: AWS Secrets Manager (us-east-1 or configured region)
- Local: Ports 5432 (PostgreSQL), 3001 (PostgREST), 5000 (Flask), 8443 (HTTPS)

---

## Phase 1: Database Foundation

### Step 1: Update docker-compose.yml

**File**: `~/0_NVR/docker-compose.yml`

**Add PostgreSQL service:**
```yaml
  postgres:
    image: postgres:16-alpine
    container_name: nvr-postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: nvr
      POSTGRES_USER: nvr_api
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_INITDB_ARGS: "--encoding=UTF8 --locale=en_US.UTF-8"
    volumes:
      - /mnt/sdc/postgres_data:/var/lib/postgresql/data
      - ./psql/init-db.sql:/docker-entrypoint-initdb.d/01-schema.sql:ro
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U nvr_api -d nvr"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - nvr-net
```

**Add PostgREST service:**
```yaml
  postgrest:
    image: postgrest/postgrest:v12.0.2
    container_name: nvr-postgrest
    restart: unless-stopped
    environment:
      PGRST_DB_URI: postgres://nvr_api:${POSTGRES_PASSWORD}@postgres:5432/nvr
      PGRST_DB_SCHEMA: public
      PGRST_DB_ANON_ROLE: nvr_anon
      PGRST_DB_POOL: 10
      PGRST_SERVER_HOST: "*"
      PGRST_SERVER_PORT: 3001
      PGRST_OPENAPI_SERVER_PROXY_URI: http://localhost:3001
    ports:
      - "3001:3001"
    depends_on:
      postgres:
        condition: service_healthy
    networks:
      - nvr-net
```

**Update unified-nvr service environment:**
```yaml
  nvr:
    # ... existing config ...
    environment:
      # ... existing vars ...
      
      # PostgreSQL Database Configuration
      - POSTGRES_HOST=postgres
      - POSTGRES_PORT=5432
      - POSTGRES_DB=nvr
      - POSTGRES_USER=nvr_api
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
      - POSTGREST_URL=http://postgrest:3001
    
    depends_on:
      postgres:
        condition: service_healthy
      postgrest:
        condition: service_started
```

---

### Step 2: Generate PostgreSQL Password

**Commands:**
```bash
# Generate simple alphanumeric password (no special chars for URL compatibility)
openssl rand -base64 32 | tr -d '/+='

# Example output: lkjdfkljsdfeuuyUU333
```

**Note**: Avoid special characters (`/`, `+`, `=`) that break PostgreSQL URI parsing in PostgREST.

---

### Step 3: Create AWS Secret

**Secret Name**: `NVR-Secrets`

**Commands:**
```bash
# Create secret in AWS Secrets Manager
push_secret_to_aws "NVR-Secrets" '{"POSTGRES_PASSWORD":"YOUR_GENERATED_PASSWORD"}'

# Verify it was created
aws secretsmanager list-secrets --query 'SecretList[].Name' --output table | grep NVR-Secrets
```

**Alternative (if push_secret_to_aws not available):**
```bash
aws secretsmanager create-secret \
    --name NVR-Secrets \
    --description "NVR recording system database credentials" \
    --secret-string '{"POSTGRES_PASSWORD":"YOUR_GENERATED_PASSWORD"}'
```

---

### Step 4: Create pull_nvr_secrets Function

**File**: `~/.bash_utils`

**Add function (after existing AWS CLI section):**
```bash
######################################################## - ########################################################
#                                                    NVR RECORDING SECRETS
######################################################## - ########################################################
pull_nvr_secrets() {
    local reset="${1:-false}"
    
    echo "Pulling all NVR secrets (cameras + database)..."
    
    # Pull camera credentials first
    get_cameras_credentials "$reset"
    
    # Then pull database secrets
    echo "Pulling database secrets from NVR-Secrets..."
    pull_aws_secrets "NVR-Secrets" >/dev/null
    
    # Verify database password was loaded
    if [ -z "$POSTGRES_PASSWORD" ]; then
        log $RED "Failed to load POSTGRES_PASSWORD from NVR-Secrets"
        return 1
    fi
    
    echo "✓ All NVR secrets loaded (cameras + database)"
    return 0
}
```

**Test the function:**
```bash
# Source bash_utils
. ~/.bash_utils --no-exec

# Test pulling secrets
pull_nvr_secrets

# Verify password is set
echo $POSTGRES_PASSWORD
# Should output: lkjdfkljsdfeuuyUU333 (or your password)
```

---

### Step 5: Update start.sh

**File**: `~/0_NVR/start.sh`

**Change line 46 from:**
```bash
get_cameras_credentials # >/dev/null
```

**To:**
```bash
pull_nvr_secrets # >/dev/null
```

---

### Step 6: Update deploy.sh

**File**: `~/0_NVR/deploy.sh`

**Change line 57 from:**
```bash
echo "Fetching camera credentials..."
get_cameras_credentials >/dev/null
```

**To:**
```bash
echo "Fetching NVR secrets (cameras + database)..."
pull_nvr_secrets >/dev/null
```

---

### Step 7: Create PostgreSQL Data Directory

**Commands:**
```bash
# Create directory
sudo mkdir -p /mnt/sdc/postgres_data

# Set ownership
sudo chown -R $USER:$USER /mnt/sdc/postgres_data

# Verify
ls -ld /mnt/sdc/postgres_data
# Should show: drwx------ with your user:group
```

---

### Step 8: Create Database Schema

**Create directory structure:**
```bash
mkdir -p ~/0_NVR/psql
```

**Create file**: `~/0_NVR/psql/init-db.sql`

**Key components:**
- Creates roles: `nvr_anon` (read-only), `nvr_api` (full access)
- Creates table: `recordings` (recording metadata)
- Creates table: `motion_events` (motion detection events)
- Creates indexes for performance
- Configures Row Level Security (RLS) policies

---

### Step 9: Initial Database Startup

**Important**: If PostgreSQL data directory already exists, remove it for clean initialization:

```bash
# Stop all containers
cd ~/0_NVR
docker compose down

# Remove old data (if exists)
sudo rm -rf /mnt/sdc/postgres_data/*

# Start containers via start.sh (handles secret loading)
./start.sh
```

**Verify PostgreSQL initialized:**
```bash
# Check logs
docker logs nvr-postgres | grep "database system is ready"

# Should see: "database system is ready to accept connections"
```

---

### Step 10: Verify Database Schema

**Check tables exist:**
```bash
docker exec nvr-postgres psql -U nvr_api -d nvr -c "\dt"
```

**Expected output:**
```
            List of relations
 Schema |     Name      | Type  |  Owner
--------+---------------+-------+---------
 public | motion_events | table | nvr_api
 public | recordings    | table | nvr_api
(2 rows)
```

---

### Step 11: Verify PostgREST API

**Test connection:**
```bash
# Check PostgREST logs
docker logs nvr-postgrest | tail -10

# Should see:
# - "Connection successful"
# - "Listening on port 3001"
# - "Schema cache loaded"
```

**Test API endpoints:**
```bash
# Test recordings endpoint (should return empty array)
curl http://localhost:3001/recordings
# Expected: []

# Test motion_events endpoint
curl http://localhost:3001/motion_events
# Expected: []
```

---

## Phase 1 Validation Checklist

- [x] PostgreSQL container running and healthy
- [x] PostgREST container running and listening on port 3001
- [x] Database tables created: `recordings`, `motion_events`
- [x] Database roles created: `nvr_anon`, `nvr_api`
- [x] API responding with `[]` on `/recordings` and `/motion_events`
- [x] `pull_nvr_secrets` function working in shell
- [x] `POSTGRES_PASSWORD` environment variable set correctly
- [x] All containers start successfully via `./start.sh`

---

## Phase 2: Recording Service Core

### Step 1: Create Storage Directory Structure

**Commands:**
```bash
# Create directories for recordings
sudo mkdir -p /mnt/sdc/NVR_Recent/motion
sudo mkdir -p /mnt/sdc/NVR_Recent/continuous
sudo mkdir -p /mnt/sdc/NVR_Recent/snapshots

# Set ownership
sudo chown -R $USER:$USER /mnt/sdc/NVR_Recent

# Verify structure
ls -la /mnt/sdc/NVR_Recent/
```

**Expected output:**
```
drwxr-xr-x  5 elfege elfege 4096 Nov 12 04:15 .
drwxr-xr-x  4 elfege elfege 4096 Nov 12 04:15 ..
drwxr-xr-x  2 elfege elfege 4096 Nov 12 04:15 motion
drwxr-xr-x  2 elfege elfege 4096 Nov 12 04:15 continuous
drwxr-xr-x  2 elfege elfege 4096 Nov 12 04:15 snapshots
```

**Status**: ✅ **COMPLETE**

---

### Step 2: Mount Recording Storage in Docker

**File**: `~/0_NVR/docker-compose.yml`

**Modified the `nvr` service volumes section:**
```yaml
  nvr:
    # ... existing config ...
    volumes:
      # recording volumes (add these three lines)
      - /mnt/sdc/NVR_Recent/motion:/recordings/motion
      - /mnt/sdc/NVR_Recent/continuous:/recordings/continuous
      - /mnt/sdc/NVR_Recent/snapshots:/recordings/snapshots
      
      # existing mounts below...
      - ./:/app
```

**Status**: ✅ **COMPLETE**

---

### Step 3: Create Recording Service Directory Structure

**Commands:**
```bash
mkdir -p ~/0_NVR/services/recording
touch ~/0_NVR/services/recording/__init__.py
```

**Status**: ✅ **COMPLETE**

---

### Step 4: Create Base Recording Service File

**File**: `~/0_NVR/services/recording/recording_service.py`

Created base class structure with method stubs for:
- `start_motion_recording()` - Start FFmpeg recording process
- `stop_recording()` - Stop recording gracefully
- `get_active_recordings()` - List active recordings

**Status**: ✅ **COMPLETE**

---

### Step 5a: Create Recording Configuration

**File**: `~/0_NVR/config/recording_settings.json`

Created comprehensive configuration with:

**Storage tiers:**
- **motion**: 7-day retention, 50GB limit, 30s segments
- **continuous**: 3-day retention, 900GB limit, 1hr segments (disabled by default)
- **snapshots**: 14-day retention, 5GB limit, 5min intervals

**Other settings:**
- Cleanup schedule: 3 AM daily
- Motion detection cooldown: 60s
- Encoding: H.264 veryfast preset, CRF 23

**Camera overrides structure:**
```json
"camera_overrides": {
  "68d49398005cf203e400043f": {
    "motion": {"max_age_days": 14, "segment_duration_sec": 60},
    "snapshots": {"interval_sec": 60}
  },
  "AMCREST_LOBBY": {
    "motion": {"max_age_days": 30},
    "continuous": {"enabled": true, "segment_duration_sec": 1800},
    "snapshots": {"interval_sec": 1}
  }
}
```

**Camera ID formats by vendor:**
- UniFi: Hex strings (e.g., `68d49398005cf203e400043f`)
- Eufy: Serial format (e.g., `T8416P0023370398`)
- Reolink: Descriptive uppercase (e.g., `REOLINK_MEBO`)
- Amcrest: Descriptive uppercase (e.g., `AMCREST_LOBBY`)

**Status**: ✅ **COMPLETE**

---

### Step 5b: Create Configuration Loader Utility

**File**: `~/0_NVR/config/recording_config_loader.py`

Created `RecordingConfig` class providing:

**Core Methods:**
- `get_camera_config(camera_id)` - Returns merged config with camera-specific overrides
- `get_tier_config(tier_name, camera_id)` - Get specific storage tier settings
- `is_tier_enabled(tier_name, camera_id)` - Check if tier enabled for camera
- `get_base_config()` - Get base configuration without overrides
- `reload()` - Reload configuration from disk

**Features:**
- Automatic override merging using deep copy
- Fallback to hardcoded defaults if config file missing
- Graceful error handling for invalid JSON

**Usage example:**
```python
config = RecordingConfig()
camera_cfg = config.get_camera_config('AMCREST_LOBBY')
motion_settings = camera_cfg['storage_tiers']['motion']
# motion_settings['max_age_days'] == 30 (from override)
```

**Status**: ✅ **COMPLETE**

---

### Step 5c: Update StorageManager to Use Configuration

**File**: `~/0_NVR/services/recording/storage_manager.py`

Updated to be configuration-driven with:

**New functionality:**
- Loads `RecordingConfig` on initialization
- Uses config for all path resolution and retention policies
- Camera-specific retention via `cleanup_old_recordings(camera_id=...)`

**New methods:**
- `check_storage_limits(recording_type)` - Returns usage percentages and limit status
- `get_storage_stats()` - Returns current usage with configured limits

**Example output from `check_storage_limits()`:**
```python
{
  'size_limit_exceeded': False,
  'cleanup_recommended': True,
  'usage_percent': 92.5,
  'current_mb': 46250,
  'max_mb': 50000
}
```

**Status**: ✅ **COMPLETE**

---

### Step 6: Restructure Configuration and Implement Recording Service

**Status**: ✅ **COMPLETE** (code written, needs testing)

**Configuration Restructured:**
- Changed from `storage_tiers` + `camera_overrides` to `global_defaults` + `camera_settings`
- Added `recording_source` field (auto/rtsp/mjpeg_service/mediamtx)
- Added `detection_method` field (onvif/ffmpeg/manual_only)
- Added `quality` field (main/sub) for stream selection
- Camera-centric structure ready for Flask-WTF UI

**RecordingConfigLoader Updated:**
- New `get_camera_config()` method with auto-resolution
- Automatic recording source resolution based on stream type
- Per-camera override merging with global defaults
- Support for hybrid recording architecture

**StorageManager Updated:**
- Integrated with new configuration structure
- Per-camera cleanup with camera-specific retention
- `cleanup_all_cameras()` method for scheduled cleanup
- Storage limit checking with usage percentages

**RecordingService Implemented:**
- Hybrid source support (MediaMTX/RTSP/MJPEG service)
- `start_motion_recording()` with source auto-detection
- `stop_recording()` with graceful/forced termination
- `get_active_recordings()` with progress tracking
- `cleanup_finished_recordings()` for process reaping
- PostgREST metadata storage integration

**Recording Source Strategy:**
- **LL_HLS/HLS cameras** (10 cameras): Record from MediaMTX → Zero new camera connections
- **MJPEG cameras** (4 cameras): Record from RTSP or tap MJPEG service → Configurable per camera
- FFmpeg `-c copy` mode for all RTSP sources → Minimal CPU usage

**Files Created/Updated:**
1. `config/recording_settings.json` - Restructured configuration
2. `config/recording_config_loader.py` - Updated loader with auto-resolution
3. `services/recording/storage_manager.py` - Updated for new config structure
4. `services/recording/recording_service.py` - Full implementation with hybrid sources

**Testing Needed:**
- Recording from MediaMTX source
- Recording from direct RTSP source
- Storage limit enforcement
- Concurrent recordings
- Metadata persistence

**Not Yet Implemented:**
- MJPEG service recording (frame buffer piping)
- Continuous recording mode
- Flask API routes
- Cleanup scheduler
- Motion detection integration

---

### Step 7: Add Flask Routes for Recording Control

**Status**: ⏳ **NEXT STEP**

**Routes to implement:**

```python
# Start motion recording
POST /api/recording/start/<camera_id>
Body: {"duration": 30, "event_id": "optional"}

# Stop recording
POST /api/recording/stop/<recording_id>

# Get active recordings
GET /api/recording/active

# Get recording history
GET /api/recording/history?camera_id=<camera_id>&limit=50

# Get storage statistics
GET /api/recording/storage/stats

# Trigger cleanup
POST /api/recording/cleanup
Body: {"recording_type": "motion", "camera_id": "optional"}
```

**Integration points:**
- Initialize RecordingService in app.py
- Add routes to app.py
- Handle errors and return JSON responses
- Add CORS headers if needed

---

### Step 8: Test Recording System

**Test scenarios:**

1. **Single camera recording:**
   - Start motion recording on AMCREST_LOBBY (RTSP source)
   - Verify MP4 file created in /mnt/sdc/NVR_Recent/motion/
   - Check file size and duration
   - Verify metadata in PostgreSQL

2. **MediaMTX source recording:**
   - Start recording on LL_HLS camera
   - Verify recording from rtsp://nvr-packager:8554/CAMERA_ID
   - Confirm no new camera connection created

3. **Concurrent recordings:**
   - Start recordings on 3 different cameras simultaneously
   - Verify all complete successfully
   - Check system resource usage

4. **Storage cleanup:**
   - Create test recordings with old timestamps
   - Run cleanup for specific camera
   - Verify only old files deleted

---

## Phase 2 Validation Checklist

- [ ] `recording_settings.json` restructured with camera-centric config
- [ ] `RecordingConfig` loading and merging working correctly
- [ ] `StorageManager` using per-camera retention settings
- [ ] `RecordingService` initializes without errors
- [ ] Can start recording on MediaMTX source camera
- [ ] Can start recording on direct RTSP camera
- [ ] Recording files created in correct directory
- [ ] Recording metadata stored in PostgreSQL
- [ ] Can stop recording gracefully
- [ ] Storage statistics accurate
- [ ] Cleanup removes old recordings per camera
- [ ] Flask routes respond correctly

---

## Common Issues & Solutions

### Phase 1 Issues

### Issue: PostgREST "password authentication failed"

**Cause**: Password mismatch between PostgreSQL and PostgREST.

**Solution**:
```bash
# Update PostgreSQL password
docker exec -i nvr-postgres psql -U nvr_api -d nvr -c \
  "ALTER ROLE nvr_api WITH PASSWORD 'YOUR_PASSWORD';"

# Restart PostgREST
docker restart nvr-postgrest
```

---

### Issue: PostgREST "could not look up local user ID 1000"

**Cause**: Password contains URL-unsafe characters (`/`, `+`, `=`).

**Solution**: Generate new password without special characters:
```bash
openssl rand -base64 32 | tr -d '/+='
```

---

### Issue: Init script didn't run (no tables created)

**Cause**: PostgreSQL data directory had existing data.

**Solution**: Either:
1. **Clean slate** (recommended):
   ```bash
   docker compose down
   sudo rm -rf /mnt/sdc/postgres_data/*
   ./start.sh
   ```

2. **Manual schema load**:
   ```bash
   docker exec -i nvr-postgres psql -U nvr_api -d nvr < ~/0_NVR/psql/init-db.sql
   ```

---

### Issue: `POSTGRES_PASSWORD` not set in containers

**Cause**: Variable not exported before `docker compose up`.

**Solution**: Always use `./start.sh` which exports variables via `pull_nvr_secrets` before starting containers.

---

## Files Modified/Created

### Project Files (Phase 1)
- `~/0_NVR/docker-compose.yml` - Added postgres + postgrest services
- `~/0_NVR/start.sh` - Changed to call `pull_nvr_secrets`
- `~/0_NVR/deploy.sh` - Changed to call `pull_nvr_secrets`
- `~/0_NVR/psql/init-db.sql` - Database schema (new file)

### Project Files (Phase 2)
- `~/0_NVR/services/recording/__init__.py` - Package marker (new)
- `~/0_NVR/services/recording/recording_service.py` - Base recording service (new)
- `~/0_NVR/services/recording/storage_manager.py` - Storage management (new)
- `~/0_NVR/config/recording_settings.json` - Recording configuration (new)
- `~/0_NVR/config/recording_config_loader.py` - Config loader utility (new)

### System Files
- `~/.bash_utils` - Added `pull_nvr_secrets()` function
- `/mnt/sdc/postgres_data/` - PostgreSQL data directory (created)
- `/mnt/sdc/NVR_Recent/motion/` - Motion clips storage (created)
- `/mnt/sdc/NVR_Recent/continuous/` - Continuous recording storage (created)
- `/mnt/sdc/NVR_Recent/snapshots/` - Snapshot storage (created)

### AWS Secrets Manager
- `NVR-Secrets` - New secret containing `POSTGRES_PASSWORD`

---

## Environment Variables Reference

### PostgreSQL
- `POSTGRES_DB=nvr` - Database name
- `POSTGRES_USER=nvr_api` - Superuser username
- `POSTGRES_PASSWORD=<from AWS>` - Superuser password

### PostgREST
- `PGRST_DB_URI=postgres://nvr_api:PASSWORD@postgres:5432/nvr` - Connection string
- `PGRST_DB_SCHEMA=public` - Schema to expose via API
- `PGRST_DB_ANON_ROLE=nvr_anon` - Anonymous role for read-only access
- `PGRST_DB_POOL=10` - Connection pool size
- `PGRST_SERVER_PORT=3001` - API port

### Flask (unified-nvr)
- `POSTGRES_HOST=postgres` - Database hostname (Docker network)
- `POSTGRES_PORT=5432` - Database port
- `POSTGRES_DB=nvr` - Database name
- `POSTGRES_USER=nvr_api` - Database user
- `POSTGRES_PASSWORD=<from AWS>` - Database password
- `POSTGREST_URL=http://postgrest:3001` - PostgREST API URL

---

## Port Mapping

| Service | Internal Port | External Port | Purpose |
|---------|---------------|---------------|---------|
| PostgreSQL | 5432 | 5432 | Database access |
| PostgREST | 3001 | 3001 | REST API |
| Flask | 5000 | 5000 | Web application |
| MediaMTX | 8888 | 8888 | HLS streaming |
| Nginx | 443 | 8443 | HTTPS edge |

---

## Next Steps

### Phase 2 Remaining Tasks
1. Update `recording_service.py` to use config and storage manager
2. Implement FFmpeg recording process management
3. Add PostgREST client for metadata storage
4. Create Flask routes for recording control
5. Test manual recording start/stop

### Phase 3: Motion Detection
1. Implement FFmpeg motion detection
2. Create motion debouncer
3. Integrate with recording service
4. Add per-camera sensitivity configuration

---

### Phase 2 Issues

### Issue: Recording source resolution fails with "auto"

**Cause**: Camera stream_type not passed to config loader.

**Solution**: Always pass `camera_stream_type` parameter:
```python
camera_cfg = config.get_camera_config(camera_id, camera.get('stream_type'))
```

---

### Issue: FFmpeg recording fails immediately

**Cause 1**: Source URL unreachable (MediaMTX not running or camera offline).

**Solution**: Check MediaMTX health and camera connectivity:
```bash
# Test MediaMTX source
ffprobe rtsp://nvr-packager:8554/CAMERA_ID

# Test camera direct RTSP
ffprobe rtsp://user:pass@camera_ip:554/path
```

**Cause 2**: Codec incompatibility with `-c copy`.

**Solution**: Check camera native codec:
```bash
ffprobe -show_streams rtsp://camera_ip:554/path | grep codec_name
```

---

### Issue: Recording files empty or corrupted

**Cause**: FFmpeg terminated before proper finalization.

**Solution**: Always stop recordings gracefully:
```python
recording_service.stop_recording(recording_id, graceful=True)
```

---

### Issue: Storage cleanup not working

**Cause**: Camera ID pattern matching failing.

**Solution**: Verify filename format matches `CAMERA_ID_YYYYMMDD_HHMMSS.mp4`:
```bash
ls -la /mnt/sdc/NVR_Recent/motion/
```

---

### Issue: PostgREST metadata storage fails

**Cause**: Table schema mismatch or PostgREST not accessible.

**Solution**: Verify PostgREST connectivity:
```bash
curl http://localhost:3001/recordings
```

Check table schema matches code expectations:
```bash
docker exec nvr-postgres psql -U nvr_api -d nvr -c "\d recordings"
```

---

**End of Installation Steps Document**