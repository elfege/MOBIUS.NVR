# Motion Detection & Recording System Architecture

**Project**: Unified NVR System  
**Created**: 2025-11-10  
**Purpose**: Complete reference and implementation plan for adding motion detection and recording capabilities to the multi-vendor camera streaming system.

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Storage Architecture](#storage-architecture)
3. [Database Design](#database-design)
4. [Motion Detection Strategy](#motion-detection-strategy)
5. [Recording Service](#recording-service)
6. [PostgREST API Layer](#postgrest-api-layer)
7. [Frontend Integration](#frontend-integration)
8. [Implementation Phases](#implementation-phases)
9. [Configuration Reference](#configuration-reference)
10. [Testing & Validation](#testing--validation)
11. [Operational Procedures](#operational-procedures)

---

## System Overview

### Current State
- **17 cameras**: Eufy (6), UniFi (9), Reolink (1), Amcrest (1)
- **Streaming**: Flask + FFmpeg + MediaMTX (HLS/MJPEG)
- **Storage**: No recording capability (streaming only)
- **Database**: None (JSON configuration files)

### Target State
- **Motion-triggered recording**: Pre/post-buffered clips
- **Continuous recording**: 24/7 hourly segments (optional per camera)
- **Two-tier storage**: Recent (fast local) + Archive (large USB)
- **PostgreSQL + PostgREST**: Metadata storage and REST API
- **Multi-source motion detection**: ONVIF → Vendor Events → FFmpeg fallback
- **Frontend playback**: Timeline UI with direct PostgREST queries

### Design Principles
1. **Separate processes**: Recording FFmpeg ≠ Streaming FFmpeg
2. **Per-camera configuration**: Independent motion priorities and settings
3. **Graceful degradation**: Fallback chain for motion detection
4. **Local-only**: No cloud dependencies (except unavoidable Eufy token)
5. **Thread-safe**: Lock-protected recording state management
6. **Minimal Flask bloat**: PostgREST handles data queries, Flask manages processes

---

## Storage Architecture

### Hardware Configuration

**Available Disks** (from `df -h`):
```
/dev/sda3 (ubuntu-vg-lv)  1.1TB   587GB used   466GB free   56%   /
/dev/sdb                  1.1TB   427GB used   617GB free   41%   /mnt/sdb
/dev/sdc                  1.1TB    60GB used   984GB free    6%   /mnt/sdc
/dev/sde1 (USB)            21TB   1.3TB used    19TB free    7%   /mnt/THE_BIG_DRIVE
```

### Storage Tier Design

**Selected Architecture**:

```
Recent Tier:   /mnt/sdc/NVR_Recent/          (700GB allocation, ~2-7 days)
Archive Tier:  /mnt/THE_BIG_DRIVE/           (19TB available, 30+ days)
Database:      /mnt/sdc/postgres_data/       (200GB allocation)
```

**Rationale**:
- **sdc chosen**: 984GB free, only 6% used, same disk type as boot drive (fast, not USB)
- **Co-location**: Recent recordings + database on same fast disk for optimal I/O
- **sde (USB)**: Cold storage only, acceptable latency for old recordings

### Directory Structure

```
/mnt/sdc/
├── NVR_Recent/                    # Hot storage (2-7 days retention)
│   ├── motion/                    # Motion-triggered clips
│   │   ├── OFFICE_KITCHEN/
│   │   │   └── 2025-11-09/
│   │   │       ├── 14-23-15_motion.mp4
│   │   │       └── 14-45-30_motion.mp4
│   │   ├── Living_Room/
│   │   └── ... (all cameras)
│   ├── continuous/                # 24/7 recordings (if enabled)
│   │   ├── OFFICE_KITCHEN/
│   │   │   └── 2025-11-09/
│   │   │       ├── 00-00-00.mp4  # Hourly segments
│   │   │       ├── 01-00-00.mp4
│   │   │       └── ...
│   │   └── ...
│   └── snapshots/                 # Motion event thumbnails (optional)
│       └── OFFICE_KITCHEN/
│           └── 2025-11-09/
│               └── 14-23-15.jpg
│
├── postgres_data/                 # PostgreSQL database files
│   └── ... (managed by PostgreSQL)
│
└── (existing 60GB content)

/mnt/THE_BIG_DRIVE/
└── NVR_RECORDINGS/                # Cold storage (30+ days retention)
    ├── motion/
    │   └── (same structure as Recent)
    ├── continuous/
    │   └── (same structure as Recent)
    └── snapshots/
        └── (same structure as Recent)
```

### File Naming Convention

**Motion clips**:
```
{camera_name}/{YYYY-MM-DD}/{HH-MM-SS}_motion.mp4
Example: OFFICE_KITCHEN/2025-11-09/14-23-15_motion.mp4
```

**Continuous segments**:
```
{camera_name}/{YYYY-MM-DD}/{HH-MM-SS}.mp4
Example: OFFICE_KITCHEN/2025-11-09/14-00-00.mp4  (14:00-15:00 recording)
```

**Snapshots**:
```
{camera_name}/{YYYY-MM-DD}/{HH-MM-SS}.jpg
Example: OFFICE_KITCHEN/2025-11-09/14-23-15.jpg
```

### Storage Migration Flow

```
┌─────────────────────────────────────────────────────────────┐
│                     Recording Started                        │
│                           ↓                                   │
│              /mnt/sdc/NVR_Recent/motion/                     │
│              (Hot tier - immediate playback)                 │
│                           ↓                                   │
│              [Age: 2-7 days, configurable]                   │
│                           ↓                                   │
│           Archive Daemon (runs every 6 hours)                │
│                           ↓                                   │
│         Move to /mnt/THE_BIG_DRIVE/NVR_RECORDINGS/          │
│              (Cold tier - long-term storage)                 │
│                           ↓                                   │
│              [Age: 7-30 days, configurable]                  │
│                           ↓                                   │
│           Cleanup Daemon (runs every 6 hours)                │
│                           ↓                                   │
│              Delete oldest files (retention policy)          │
└─────────────────────────────────────────────────────────────┘
```

### Retention Policies

**Default per-camera settings**:
```json
{
  "retention": {
    "recent_tier_hours": 48,        // 2 days in recent
    "archive_tier_days": 7,         // 7 days in archive (then delete)
    "continuous_retention_days": 3, // Continuous recording (shorter retention)
    "max_storage_gb_per_camera": 100 // Per-camera quota
  }
}
```

**Storage estimates** (17 cameras):
```
Motion-only recording:
- Avg 2 Mbps × 17 cameras = 34 Mbps aggregate
- Assume 20% motion activity = 6.8 Mbps effective
- 7 days retention = ~518 GB

Continuous recording (all cameras):
- 34 Mbps × 24h × 3 days = ~881 GB per 3 days
- Not recommended for all cameras simultaneously
```

---

## Database Design

### Why PostgreSQL + PostgREST?

**Advantages over Flask + SQLAlchemy**:
1. **Frontend direct queries**: No Flask route proxy needed
2. **Auto-generated API**: CRUD operations without manual coding
3. **Flask stays lean**: Recording system adds ~100 lines vs 500+ with ORM
4. **PostgreSQL RLS**: Database-enforced security policies
5. **OpenAPI docs**: Swagger documentation auto-generated
6. **Scalability**: Better for complex timeline queries

### Database Schema

#### recordings table

```sql
CREATE TABLE recordings (
    -- Primary key
    id BIGSERIAL PRIMARY KEY,
    
    -- Camera identification
    camera_id VARCHAR(50) NOT NULL,
    camera_name VARCHAR(100),
    
    -- Temporal data
    timestamp TIMESTAMPTZ NOT NULL,
    end_timestamp TIMESTAMPTZ,
    duration_seconds INTEGER,
    
    -- File location
    file_path TEXT NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    storage_tier VARCHAR(10) NOT NULL 
        CHECK (storage_tier IN ('recent', 'archive')),
    file_size_bytes BIGINT,
    
    -- Motion metadata
    motion_triggered BOOLEAN DEFAULT true,
    motion_source VARCHAR(20) 
        CHECK (motion_source IN ('onvif', 'ffmpeg', 'eufy_bridge', 'manual', NULL)),
    motion_event_id BIGINT REFERENCES motion_events(id),
    
    -- Encoding information
    codec VARCHAR(20),            -- 'copy', 'h264', etc.
    resolution VARCHAR(20),       -- '640x480', '1280x720', etc.
    fps INTEGER,
    bitrate_kbps INTEGER,
    
    -- Recording status
    status VARCHAR(20) DEFAULT 'recording' 
        CHECK (status IN ('recording', 'completed', 'archived', 'error')),
    error_message TEXT,
    
    -- Timestamps for lifecycle tracking
    created_at TIMESTAMPTZ DEFAULT NOW(),
    archived_at TIMESTAMPTZ,     -- When moved to archive tier
    
    -- Unique constraint
    CONSTRAINT recordings_camera_timestamp_unique 
        UNIQUE (camera_id, timestamp)
);

-- Performance indexes
CREATE INDEX idx_recordings_camera_timestamp 
    ON recordings(camera_id, timestamp DESC);
CREATE INDEX idx_recordings_timestamp 
    ON recordings(timestamp DESC);
CREATE INDEX idx_recordings_storage_tier 
    ON recordings(storage_tier);
CREATE INDEX idx_recordings_status 
    ON recordings(status);
CREATE INDEX idx_recordings_motion_source 
    ON recordings(motion_source) 
    WHERE motion_source IS NOT NULL;

-- Composite index for common queries
CREATE INDEX idx_recordings_camera_tier_timestamp 
    ON recordings(camera_id, storage_tier, timestamp DESC);
```

#### motion_events table

```sql
CREATE TABLE motion_events (
    -- Primary key
    id BIGSERIAL PRIMARY KEY,
    
    -- Camera identification
    camera_id VARCHAR(50) NOT NULL,
    
    -- Event timing
    timestamp TIMESTAMPTZ NOT NULL,
    
    -- Motion source
    source VARCHAR(20) NOT NULL 
        CHECK (source IN ('onvif', 'ffmpeg', 'eufy_bridge', 'manual')),
    
    -- Source-specific confidence metrics
    confidence FLOAT,              -- General confidence score
    scene_score FLOAT,             -- FFmpeg scene change score
    
    -- Recording linkage
    triggered_recording BOOLEAN DEFAULT false,
    recording_id BIGINT REFERENCES recordings(id),
    
    -- ONVIF-specific metadata
    onvif_rule_name VARCHAR(100),
    onvif_event_type VARCHAR(100),
    
    -- Creation timestamp
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Performance indexes
CREATE INDEX idx_motion_events_camera_timestamp 
    ON motion_events(camera_id, timestamp DESC);
CREATE INDEX idx_motion_events_timestamp 
    ON motion_events(timestamp DESC);
CREATE INDEX idx_motion_events_source 
    ON motion_events(source);
CREATE INDEX idx_motion_events_recording 
    ON motion_events(recording_id) 
    WHERE recording_id IS NOT NULL;
```

#### camera_recording_config table (optional)

**Note**: This could stay in `cameras.json` for simplicity. Including here for completeness.

```sql
CREATE TABLE camera_recording_config (
    camera_id VARCHAR(50) PRIMARY KEY,
    
    -- Recording mode
    enabled BOOLEAN DEFAULT true,
    mode VARCHAR(20) DEFAULT 'motion' 
        CHECK (mode IN ('motion', 'continuous', 'disabled')),
    
    -- Motion detection priorities (JSON array)
    motion_detection_priority JSONB DEFAULT '["ffmpeg"]',
    
    -- Retention settings
    recent_retention_hours INTEGER DEFAULT 48,
    archive_retention_days INTEGER DEFAULT 7,
    max_storage_gb INTEGER DEFAULT 100,
    
    -- Motion settings
    pre_buffer_seconds INTEGER DEFAULT 30,
    post_buffer_seconds INTEGER DEFAULT 30,
    cooldown_seconds INTEGER DEFAULT 5,
    
    -- FFmpeg motion detection settings
    ffmpeg_sensitivity FLOAT DEFAULT 0.02,
    
    -- ONVIF settings
    onvif_poll_interval_ms INTEGER DEFAULT 1000,
    
    -- Timestamps
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Database Initialization Script

**init-db.sql** (mounted to PostgreSQL container):
```sql
-- Create database
-- (handled by POSTGRES_DB env var in docker-compose)

-- Create roles
CREATE ROLE nvr_anon NOLOGIN;
CREATE ROLE nvr_api LOGIN PASSWORD 'REPLACE_WITH_SECURE_PASSWORD';

-- Grant permissions
GRANT USAGE ON SCHEMA public TO nvr_anon;
GRANT nvr_anon TO nvr_api;

-- Create tables (schemas above)
-- ... (insert full schemas here)

-- Grant table permissions
GRANT SELECT ON recordings, motion_events TO nvr_anon;
GRANT INSERT, UPDATE, DELETE ON recordings, motion_events TO nvr_api;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO nvr_api;

-- Enable Row Level Security (RLS) - optional for future multi-user
ALTER TABLE recordings ENABLE ROW LEVEL SECURITY;
ALTER TABLE motion_events ENABLE ROW LEVEL SECURITY;

-- RLS policies (example - all access for now)
CREATE POLICY "Allow all for nvr_api" ON recordings
    FOR ALL
    TO nvr_api
    USING (true);

CREATE POLICY "Allow all for nvr_api" ON motion_events
    FOR ALL
    TO nvr_api
    USING (true);
```

---

## Motion Detection Strategy

### Multi-Source Priority Chain

**Per-Camera Configurable Priorities**:

```json
{
  "camera_id": {
    "recording": {
      "motion_detection_priority": ["onvif", "ffmpeg"]
    }
  }
}
```

**Default Priorities by Vendor**:

```python
DEFAULT_MOTION_PRIORITIES = {
    'eufy': ['ffmpeg', 'eufy_bridge'],      # FFmpeg first (bridge 2FA issues)
    'reolink': ['onvif', 'ffmpeg'],         # ONVIF reliable
    'amcrest': ['onvif', 'ffmpeg'],         # ONVIF reliable
    'unifi': ['ffmpeg', 'onvif']            # TBD - need to verify UniFi ONVIF Events
}
```

**Why per-camera priorities?**:
- **Eufy**: Bridge requires 2FA (not user-friendly), FFmpeg more reliable
- **Reolink**: Excellent ONVIF support, use native motion first
- **Amcrest**: Full ONVIF compliance, native motion optimal
- **UniFi**: ONVIF capability present, but event support needs validation

### Motion Detection Sources

#### 1. ONVIF Events (Priority for Reolink, Amcrest)

**Implementation**: `services/onvif/onvif_event_handler.py`

**Mechanism**:
- PullPoint subscription to ONVIF Events service
- Polling interval: 1000ms (configurable)
- Detects native camera motion algorithms

**Advantages**:
- ✅ Native camera motion detection (optimized by manufacturer)
- ✅ Instant trigger (no processing delay)
- ✅ Zero CPU overhead on server
- ✅ Most accurate (hardware-level detection)

**Disadvantages**:
- ❌ Requires ONVIF Events support (not all cameras have it)
- ❌ Configuration varies by manufacturer
- ❌ May need per-camera tuning

**Example ONVIF subscription**:
```python
from onvif import ONVIFCamera

def subscribe_motion_events(camera_host, username, password):
    camera = ONVIFCamera(camera_host, 80, username, password)
    event_service = camera.create_events_service()
    
    # Create PullPoint subscription
    subscription = event_service.CreatePullPointSubscription()
    
    while True:
        # Pull messages
        messages = event_service.PullMessages({
            'MessageLimit': 10,
            'Timeout': timedelta(seconds=30)
        })
        
        for msg in messages.NotificationMessage:
            if 'MotionAlarm' in str(msg):
                yield {
                    'timestamp': datetime.now(),
                    'source': 'onvif',
                    'camera_id': camera_id,
                    'confidence': 1.0
                }
```

#### 2. FFmpeg Scene Detection (Universal Fallback)

**Implementation**: `services/motion/ffmpeg_motion_detector.py`

**Mechanism**:
- Analyzes existing HLS stream (no separate FFmpeg process needed)
- Uses FFmpeg `select` filter with scene detection
- Threshold-based triggering (configurable sensitivity)

**Advantages**:
- ✅ Works with ALL cameras (universal)
- ✅ No camera configuration needed
- ✅ Reuses existing stream (no extra RTSP connections)
- ✅ Adjustable sensitivity per camera

**Disadvantages**:
- ❌ Higher CPU usage (~10% per camera)
- ❌ Processing delay (~1-2 seconds)
- ❌ May have false positives (lighting changes)

**FFmpeg scene detection command**:
```bash
# Separate FFmpeg process for motion detection
ffmpeg -i rtsp://camera/stream \
  -vf "select='gt(scene,0.02)',metadata=print:file=-" \
  -f null - 2>&1 | grep lavfi.scene_score

# Output example:
# frame:123 pts:5123456 pts_time:5.123456 lavfi.scene_score=0.034567

# When lavfi.scene_score > threshold (e.g., 0.02):
#   → Motion detected → Trigger recording
```

**Python integration**:
```python
import subprocess
import re

def start_ffmpeg_motion_monitor(camera_config, motion_callback):
    """
    Start FFmpeg motion detection process
    
    Args:
        camera_config: Camera RTSP URL and settings
        motion_callback: Function to call on motion detection
    """
    rtsp_url = camera_config['rtsp_url']
    sensitivity = camera_config.get('ffmpeg_sensitivity', 0.02)
    
    cmd = [
        'ffmpeg',
        '-rtsp_transport', 'tcp',
        '-i', rtsp_url,
        '-vf', f"select='gt(scene,{sensitivity})',metadata=print:file=-",
        '-f', 'null', '-'
    ]
    
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        bufsize=1
    )
    
    scene_pattern = re.compile(r'lavfi\.scene_score=([\d\.]+)')
    
    for line in process.stdout:
        match = scene_pattern.search(line)
        if match:
            score = float(match.group(1))
            if score > sensitivity:
                motion_callback({
                    'timestamp': datetime.now(),
                    'source': 'ffmpeg',
                    'camera_id': camera_config['camera_id'],
                    'scene_score': score,
                    'confidence': min(score / sensitivity, 1.0)
                })
```

#### 3. Eufy Bridge Events (Eufy cameras only)

**Implementation**: `services/eufy/eufy_motion_handler.py`

**Mechanism**:
- WebSocket listener for eufy-security-ws motion events
- Bridge emits motion notifications from Eufy cloud

**Advantages**:
- ✅ Native Eufy motion detection
- ✅ Instant notification (push-based)
- ✅ Zero server CPU overhead

**Disadvantages**:
- ❌ Requires valid Eufy token (2FA renewal issue)
- ❌ Token expires periodically (no auto-refresh yet)
- ❌ Not user-friendly (manual 2FA intervention)
- ❌ Eufy-only (not universal)

**Status**: **Low priority** due to 2FA token renewal complexity.

**Future improvement**: Implement user-friendly 2FA UI flow for token refresh.

### Motion Event Debouncing

**Problem**: Rapid motion triggers (e.g., person walking) create many events.

**Solution**: Cooldown period between triggers.

```python
class MotionDebouncer:
    def __init__(self, cooldown_seconds=5):
        self.cooldown = cooldown_seconds
        self.last_trigger = {}  # camera_id -> timestamp
    
    def should_trigger(self, camera_id):
        """Check if enough time has passed since last trigger"""
        now = time.time()
        last = self.last_trigger.get(camera_id, 0)
        
        if now - last >= self.cooldown:
            self.last_trigger[camera_id] = now
            return True
        return False
```

**Configuration** (per camera):
```json
{
  "motion_settings": {
    "cooldown_seconds": 5  // Minimum time between motion triggers
  }
}
```

---

## Recording Service

### Architecture Overview

**Service Location**: `services/recording/recording_service.py`

**Responsibilities**:
1. Manage recording FFmpeg processes (separate from streaming)
2. Handle pre-buffering for motion clips
3. Start/stop recordings based on motion events
4. File management and storage tier handling
5. Database metadata insertion

### Key Design Principles

**Separate Recording Processes**:
```
Streaming FFmpeg (Port 8554) → MediaMTX → HLS to browser
    ↑
    └─── Independent of recording

Recording FFmpeg → Disk files (/mnt/sdc/NVR_Recent/)
    ↑
    └─── Separate process, doesn't affect streaming
```

**Why separate?**:
- Streaming health monitor restarts don't interrupt recordings
- Recording failures don't crash live streams
- Independent lifecycle management

### Recording Modes

#### Mode 1: Motion-Triggered Recording

**Behavior**:
```
Motion detected
    ↓
Check if already recording? → Yes: Extend post-buffer timer
    ↓ No
Start FFmpeg recording (includes 30s pre-buffer)
    ↓
Set post-buffer timer (30s after last motion)
    ↓
No motion for 30s? → Stop recording
    ↓
Move/rename file to permanent location
    ↓
Insert metadata to database
```

**Pre-buffer implementation**:
```bash
# FFmpeg circular buffer approach
ffmpeg -rtsp_transport tcp \
  -i rtsp://camera/stream \
  -c:v copy \          # Copy codec (no re-encode)
  -an \                # No audio
  -f segment \
  -segment_time 60 \
  -segment_wrap 2 \    # Keep only 2 segments (60s × 2 = 120s buffer)
  -reset_timestamps 1 \
  -strftime 1 \
  "/tmp/buffer_%Y%m%d_%H%M%S.mp4"

# On motion trigger:
# 1. Stop circular buffer FFmpeg
# 2. Rename latest 2 segments to permanent location
# 3. Start new FFmpeg writing to permanent file
```

**Post-buffer timer**:
```python
class RecordingSession:
    def __init__(self, camera_id, post_buffer_seconds=30):
        self.camera_id = camera_id
        self.post_buffer = post_buffer_seconds
        self.stop_timer = None
        self.ffmpeg_process = None
    
    def on_motion(self):
        """Called when motion detected - resets stop timer"""
        if self.stop_timer:
            self.stop_timer.cancel()
        
        self.stop_timer = threading.Timer(
            self.post_buffer,
            self.stop_recording
        )
        self.stop_timer.start()
    
    def stop_recording(self):
        """Stop FFmpeg and finalize file"""
        if self.ffmpeg_process:
            self.ffmpeg_process.terminate()
            self.ffmpeg_process.wait(timeout=5)
            self.finalize_recording()
```

#### Mode 2: Continuous Recording

**Behavior**:
```
Start at boot (if enabled)
    ↓
FFmpeg records in hourly segments
    ↓
New file every hour (00:00:00, 01:00:00, etc.)
    ↓
Continues until stopped or error
```

**Hourly segmentation**:
```bash
# FFmpeg segment muxer
ffmpeg -rtsp_transport tcp \
  -i rtsp://camera/stream \
  -c:v copy \
  -an \
  -f segment \
  -segment_time 3600 \     # 1 hour segments
  -segment_format mp4 \
  -reset_timestamps 1 \
  -strftime 1 \
  "/mnt/sdc/NVR_Recent/continuous/CAMERA_NAME/%Y-%m-%d/%H-00-00.mp4"
```

### Recording Service Implementation

**Class structure**:
```python
class RecordingService:
    def __init__(self, camera_repo, storage_config):
        self.camera_repo = camera_repo
        self.storage_config = storage_config
        
        # Recording state tracking
        self.active_recordings = {}  # camera_id -> RecordingSession
        self._lock = threading.RLock()
        
        # Motion detector integration
        self.motion_detector = MotionDetector(self)
        
        # Storage manager
        self.storage_manager = StorageManager(storage_config)
    
    def start_motion_recording(self, camera_id, motion_event):
        """
        Start motion-triggered recording
        
        Args:
            camera_id: Camera identifier
            motion_event: Motion event data (source, timestamp, etc.)
        """
        with self._lock:
            # Check if already recording
            if camera_id in self.active_recordings:
                # Extend recording (reset post-buffer timer)
                self.active_recordings[camera_id].on_motion()
                return
            
            # Get camera config
            camera = self.camera_repo.get_camera(camera_id)
            recording_config = camera.get('recording', {})
            
            # Create output path
            timestamp = datetime.now()
            output_path = self.storage_manager.get_motion_clip_path(
                camera_id, 
                timestamp
            )
            
            # Build FFmpeg command
            cmd = self._build_recording_command(
                camera,
                output_path,
                recording_config
            )
            
            # Start FFmpeg process
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            # Create recording session
            session = RecordingSession(
                camera_id=camera_id,
                process=process,
                start_time=timestamp,
                output_path=output_path,
                motion_event=motion_event,
                post_buffer_seconds=recording_config.get('post_buffer_seconds', 30)
            )
            
            self.active_recordings[camera_id] = session
            
            # Insert database record
            self._insert_recording_metadata(session, status='recording')
            
            logger.info(f"Started motion recording for {camera_id}")
    
    def stop_recording(self, camera_id):
        """
        Stop active recording
        
        Args:
            camera_id: Camera identifier
        """
        with self._lock:
            session = self.active_recordings.pop(camera_id, None)
            if not session:
                return
            
            # Stop FFmpeg gracefully
            session.process.terminate()
            try:
                session.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                session.process.kill()
            
            # Calculate final duration
            duration = (datetime.now() - session.start_time).total_seconds()
            
            # Update database record
            self._update_recording_metadata(
                session.recording_id,
                status='completed',
                duration_seconds=duration,
                end_timestamp=datetime.now()
            )
            
            logger.info(f"Stopped recording for {camera_id}, duration: {duration}s")
    
    def _build_recording_command(self, camera, output_path, config):
        """
        Build FFmpeg command for recording
        
        Returns:
            List of command arguments
        """
        rtsp_url = self._get_camera_rtsp_url(camera)
        
        cmd = [
            'ffmpeg',
            '-rtsp_transport', 'tcp',
            '-i', rtsp_url,
            '-c:v', config.get('codec', 'copy'),
            '-an',  # No audio for now
        ]
        
        # Resolution scaling (if not 'copy' codec)
        if config.get('codec') != 'copy':
            resolution = config.get('resolution', 'native')
            if resolution != 'native':
                cmd.extend(['-vf', f'scale={resolution}'])
        
        # Output
        cmd.extend([
            '-y',  # Overwrite output file
            output_path
        ])
        
        return cmd
```

### Storage Manager

**Class structure**:
```python
class StorageManager:
    def __init__(self, config):
        self.recent_dir = Path(config['recent_dir'])
        self.archive_dir = Path(config['archive_dir'])
    
    def get_motion_clip_path(self, camera_id, timestamp):
        """
        Generate path for motion clip
        
        Args:
            camera_id: Camera identifier
            timestamp: Recording start time
        
        Returns:
            Path object
        """
        date_str = timestamp.strftime('%Y-%m-%d')
        time_str = timestamp.strftime('%H-%M-%S')
        
        camera_dir = self.recent_dir / 'motion' / camera_id / date_str
        camera_dir.mkdir(parents=True, exist_ok=True)
        
        return camera_dir / f'{time_str}_motion.mp4'
    
    def get_continuous_segment_path(self, camera_id, timestamp):
        """Generate path for continuous recording segment"""
        date_str = timestamp.strftime('%Y-%m-%d')
        hour_str = timestamp.strftime('%H-00-00')
        
        camera_dir = self.recent_dir / 'continuous' / camera_id / date_str
        camera_dir.mkdir(parents=True, exist_ok=True)
        
        return camera_dir / f'{hour_str}.mp4'
    
    def find_recording(self, camera_id, timestamp):
        """
        Find recording file (searches both tiers)
        
        Args:
            camera_id: Camera identifier
            timestamp: Recording timestamp
        
        Returns:
            Path to file or None
        """
        # Try recent tier first (faster)
        recent_path = self._search_tier(self.recent_dir, camera_id, timestamp)
        if recent_path and recent_path.exists():
            return recent_path
        
        # Fallback to archive tier
        archive_path = self._search_tier(self.archive_dir, camera_id, timestamp)
        if archive_path and archive_path.exists():
            return archive_path
        
        return None
```

---

## PostgREST API Layer

### Docker Compose Configuration

**docker-compose.yml additions**:

```yaml
version: '3.8'

services:
  # ... existing services (unified-nvr, nvr-packager) ...

  postgres:
    image: postgres:16-alpine
    container_name: nvr-postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: nvr
      POSTGRES_USER: nvr_api
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}  # Set in .env file
      POSTGRES_INITDB_ARGS: "--encoding=UTF8 --locale=en_US.UTF-8"
    volumes:
      - /mnt/sdc/postgres_data:/var/lib/postgresql/data
      - ./init-db.sql:/docker-entrypoint-initdb.d/01-schema.sql:ro
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U nvr_api -d nvr"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - nvr-network

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
      - nvr-network

  unified-nvr:
    # ... existing config ...
    environment:
      # ... existing env vars ...
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

networks:
  nvr-network:
    driver: bridge
```

**Environment variables** (`.env` file):
```bash
# PostgreSQL
POSTGRES_PASSWORD=REPLACE_WITH_SECURE_RANDOM_PASSWORD_HERE

# Generate secure password:
# openssl rand -base64 32
```

### PostgREST API Examples

**Query recordings** (GET requests):

```bash
# Get all recordings for a camera
curl "http://localhost:3001/recordings?camera_id=eq.OFFICE_KITCHEN"

# Get recordings for date range
curl "http://localhost:3001/recordings?\
camera_id=eq.OFFICE_KITCHEN&\
timestamp=gte.2025-11-09T00:00:00&\
timestamp=lte.2025-11-09T23:59:59&\
order=timestamp.desc"

# Get recent tier recordings only
curl "http://localhost:3001/recordings?\
storage_tier=eq.recent&\
order=timestamp.desc&\
limit=50"

# Get motion events with recordings
curl "http://localhost:3001/motion_events?\
camera_id=eq.OFFICE_KITCHEN&\
triggered_recording=eq.true&\
select=*,recordings(*)"
```

**Insert recording** (POST request):

```bash
curl -X POST "http://localhost:3001/recordings" \
  -H "Content-Type: application/json" \
  -d '{
    "camera_id": "OFFICE_KITCHEN",
    "camera_name": "OFFICE KITCHEN",
    "timestamp": "2025-11-09T14:23:15Z",
    "file_path": "/mnt/sdc/NVR_Recent/motion/OFFICE_KITCHEN/2025-11-09/14-23-15_motion.mp4",
    "file_name": "14-23-15_motion.mp4",
    "storage_tier": "recent",
    "motion_triggered": true,
    "motion_source": "ffmpeg",
    "codec": "copy",
    "status": "recording"
  }'
```

**Update recording** (PATCH request):

```bash
curl -X PATCH "http://localhost:3001/recordings?id=eq.123" \
  -H "Content-Type: application/json" \
  -d '{
    "status": "completed",
    "end_timestamp": "2025-11-09T14:25:30Z",
    "duration_seconds": 135,
    "file_size_bytes": 15728640
  }'
```

### Python Client Integration

**Flask service using PostgREST**:

```python
import requests

class PostgRESTClient:
    def __init__(self, base_url='http://postgrest:3001'):
        self.base_url = base_url
    
    def insert_recording(self, recording_data):
        """Insert new recording metadata"""
        response = requests.post(
            f'{self.base_url}/recordings',
            json=recording_data,
            headers={'Prefer': 'return=representation'}
        )
        response.raise_for_status()
        return response.json()[0]
    
    def update_recording(self, recording_id, updates):
        """Update existing recording"""
        response = requests.patch(
            f'{self.base_url}/recordings',
            params={'id': f'eq.{recording_id}'},
            json=updates
        )
        response.raise_for_status()
    
    def insert_motion_event(self, event_data):
        """Insert motion event"""
        response = requests.post(
            f'{self.base_url}/motion_events',
            json=event_data,
            headers={'Prefer': 'return=representation'}
        )
        response.raise_for_status()
        return response.json()[0]
    
    def get_recordings(self, camera_id, start_time=None, end_time=None, limit=50):
        """Get recordings with filters"""
        params = {
            'camera_id': f'eq.{camera_id}',
            'order': 'timestamp.desc',
            'limit': limit
        }
        
        if start_time:
            params['timestamp'] = f'gte.{start_time.isoformat()}'
        if end_time:
            params['timestamp'] = f'lte.{end_time.isoformat()}'
        
        response = requests.get(f'{self.base_url}/recordings', params=params)
        response.raise_for_status()
        return response.json()
```

**Usage in Flask routes**:

```python
# app.py
postgrest_client = PostgRESTClient(os.getenv('POSTGREST_URL'))

@app.route('/api/recording/start/<camera_id>', methods=['POST'])
def start_recording(camera_id):
    """Start recording - Flask manages process, PostgREST stores metadata"""
    # Start FFmpeg process
    success = recording_service.start_recording(camera_id)
    
    if success:
        # Insert metadata via PostgREST
        session = recording_service.active_recordings[camera_id]
        recording_data = {
            'camera_id': camera_id,
            'camera_name': session.camera_name,
            'timestamp': session.start_time.isoformat(),
            'file_path': str(session.output_path),
            'file_name': session.output_path.name,
            'storage_tier': 'recent',
            'motion_triggered': False,  # Manual trigger
            'status': 'recording'
        }
        result = postgrest_client.insert_recording(recording_data)
        session.recording_id = result['id']
    
    return jsonify({'success': success})
```

---

## Frontend Integration

### JavaScript API Client

**static/js/recording/api-client.js**:

```javascript
/**
 * PostgREST API client for recordings
 */
class RecordingAPIClient {
    constructor(baseUrl = 'http://localhost:3001') {
        this.baseUrl = baseUrl;
    }

    /**
     * Get recordings for a camera
     * @param {string} cameraId - Camera identifier
     * @param {Object} options - Query options
     * @returns {Promise<Array>} - Array of recording objects
     */
    async getRecordings(cameraId, options = {}) {
        const params = new URLSearchParams({
            camera_id: `eq.${cameraId}`,
            order: 'timestamp.desc',
            limit: options.limit || 50
        });

        if (options.startTime) {
            params.append('timestamp', `gte.${options.startTime.toISOString()}`);
        }
        if (options.endTime) {
            params.append('timestamp', `lte.${options.endTime.toISOString()}`);
        }
        if (options.storageTier) {
            params.append('storage_tier', `eq.${options.storageTier}`);
        }

        const response = await fetch(`${this.baseUrl}/recordings?${params}`);
        if (!response.ok) throw new Error('Failed to fetch recordings');
        return response.json();
    }

    /**
     * Get motion events for a camera
     * @param {string} cameraId - Camera identifier
     * @param {Object} options - Query options
     * @returns {Promise<Array>} - Array of motion event objects
     */
    async getMotionEvents(cameraId, options = {}) {
        const params = new URLSearchParams({
            camera_id: `eq.${cameraId}`,
            order: 'timestamp.desc',
            limit: options.limit || 100
        });

        if (options.startTime) {
            params.append('timestamp', `gte.${options.startTime.toISOString()}`);
        }
        if (options.endTime) {
            params.append('timestamp', `lte.${options.endTime.toISOString()}`);
        }

        const response = await fetch(`${this.baseUrl}/motion_events?${params}`);
        if (!response.ok) throw new Error('Failed to fetch motion events');
        return response.json();
    }

    /**
     * Get recordings for a specific date
     * @param {string} cameraId - Camera identifier
     * @param {Date} date - Date to query
     * @returns {Promise<Array>} - Array of recording objects
     */
    async getRecordingsForDate(cameraId, date) {
        const startOfDay = new Date(date);
        startOfDay.setHours(0, 0, 0, 0);
        
        const endOfDay = new Date(date);
        endOfDay.setHours(23, 59, 59, 999);

        return this.getRecordings(cameraId, {
            startTime: startOfDay,
            endTime: endOfDay
        });
    }
}

// Global instance
const recordingAPI = new RecordingAPIClient();
```

### Recording Controls UI

**HTML additions to streams.html**:

```html
<!-- Add to each camera tile -->
<div class="stream-item" data-camera-id="${camera.camera_id}">
    <!-- Existing stream video element -->
    
    <!-- Recording indicator (red dot) -->
    <div class="recording-indicator" style="display: none;">
        <span class="recording-dot"></span>
        <span class="recording-time">00:00</span>
    </div>
    
    <!-- Recording controls -->
    <div class="recording-controls">
        <button class="btn-record" title="Start Recording">
            <i class="icon-record"></i>
        </button>
        <button class="btn-playback" title="View Recordings">
            <i class="icon-playback"></i>
        </button>
    </div>
</div>
```

**CSS additions** (static/css/components/recording-controls.css):

```css
.recording-indicator {
    position: absolute;
    top: 10px;
    right: 10px;
    background: rgba(0, 0, 0, 0.7);
    padding: 5px 10px;
    border-radius: 15px;
    display: flex;
    align-items: center;
    gap: 5px;
    z-index: 10;
}

.recording-dot {
    width: 10px;
    height: 10px;
    background: #ff0000;
    border-radius: 50%;
    animation: pulse 1.5s infinite;
}

@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
}

.recording-time {
    color: #fff;
    font-size: 12px;
    font-family: monospace;
}

.recording-controls {
    position: absolute;
    bottom: 10px;
    right: 10px;
    display: flex;
    gap: 5px;
}

.btn-record, .btn-playback {
    width: 36px;
    height: 36px;
    border-radius: 50%;
    background: rgba(0, 0, 0, 0.7);
    border: none;
    color: #fff;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: background 0.2s;
}

.btn-record:hover {
    background: rgba(255, 0, 0, 0.8);
}

.btn-playback:hover {
    background: rgba(0, 123, 255, 0.8);
}

.btn-record.recording {
    background: #ff0000;
}
```

**JavaScript controller** (static/js/recording/recording-controller.js):

```javascript
/**
 * Recording controller - manages recording UI and state
 */
class RecordingController {
    constructor() {
        this.recordingStates = new Map(); // cameraId -> {recording: bool, startTime: Date}
        this.recordingTimers = new Map(); // cameraId -> interval ID
        this.init();
    }

    init() {
        // Initialize recording controls for all cameras
        document.querySelectorAll('.btn-record').forEach(btn => {
            btn.addEventListener('click', (e) => this.handleRecordClick(e));
        });

        document.querySelectorAll('.btn-playback').forEach(btn => {
            btn.addEventListener('click', (e) => this.handlePlaybackClick(e));
        });

        // Poll recording status every 5 seconds
        setInterval(() => this.updateRecordingStates(), 5000);
    }

    async handleRecordClick(event) {
        const cameraId = event.target.closest('.stream-item').dataset.cameraId;
        const isRecording = this.recordingStates.get(cameraId)?.recording || false;

        if (isRecording) {
            await this.stopRecording(cameraId);
        } else {
            await this.startRecording(cameraId);
        }
    }

    async startRecording(cameraId) {
        try {
            const response = await fetch(`/api/recording/start/${cameraId}`, {
                method: 'POST'
            });
            
            if (response.ok) {
                this.recordingStates.set(cameraId, {
                    recording: true,
                    startTime: new Date()
                });
                this.updateRecordingUI(cameraId);
                this.startRecordingTimer(cameraId);
            }
        } catch (error) {
            console.error('Failed to start recording:', error);
        }
    }

    async stopRecording(cameraId) {
        try {
            const response = await fetch(`/api/recording/stop/${cameraId}`, {
                method: 'POST'
            });
            
            if (response.ok) {
                this.recordingStates.set(cameraId, {
                    recording: false,
                    startTime: null
                });
                this.updateRecordingUI(cameraId);
                this.stopRecordingTimer(cameraId);
            }
        } catch (error) {
            console.error('Failed to stop recording:', error);
        }
    }

    updateRecordingUI(cameraId) {
        const streamItem = document.querySelector(`[data-camera-id="${cameraId}"]`);
        const indicator = streamItem.querySelector('.recording-indicator');
        const recordBtn = streamItem.querySelector('.btn-record');
        
        const state = this.recordingStates.get(cameraId);
        
        if (state?.recording) {
            indicator.style.display = 'flex';
            recordBtn.classList.add('recording');
        } else {
            indicator.style.display = 'none';
            recordBtn.classList.remove('recording');
        }
    }

    startRecordingTimer(cameraId) {
        const streamItem = document.querySelector(`[data-camera-id="${cameraId}"]`);
        const timeDisplay = streamItem.querySelector('.recording-time');
        
        const state = this.recordingStates.get(cameraId);
        
        const intervalId = setInterval(() => {
            const elapsed = Math.floor((Date.now() - state.startTime) / 1000);
            const minutes = Math.floor(elapsed / 60).toString().padStart(2, '0');
            const seconds = (elapsed % 60).toString().padStart(2, '0');
            timeDisplay.textContent = `${minutes}:${seconds}`;
        }, 1000);
        
        this.recordingTimers.set(cameraId, intervalId);
    }

    stopRecordingTimer(cameraId) {
        const intervalId = this.recordingTimers.get(cameraId);
        if (intervalId) {
            clearInterval(intervalId);
            this.recordingTimers.delete(cameraId);
        }
    }

    async updateRecordingStates() {
        // Poll Flask API for current recording states
        try {
            const response = await fetch('/api/recording/status');
            const states = await response.json();
            
            states.forEach(state => {
                this.recordingStates.set(state.camera_id, {
                    recording: state.recording,
                    startTime: state.start_time ? new Date(state.start_time) : null
                });
                this.updateRecordingUI(state.camera_id);
            });
        } catch (error) {
            console.error('Failed to update recording states:', error);
        }
    }

    handlePlaybackClick(event) {
        const cameraId = event.target.closest('.stream-item').dataset.cameraId;
        this.openPlaybackModal(cameraId);
    }

    openPlaybackModal(cameraId) {
        // Show playback modal (implemented in next section)
        const modal = document.getElementById('playback-modal');
        modal.style.display = 'block';
        modal.dataset.cameraId = cameraId;
        
        // Load recordings for camera
        playbackUI.loadRecordings(cameraId);
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    window.recordingController = new RecordingController();
});
```

### Playback UI

**Playback modal HTML**:

```html
<!-- Add to streams.html -->
<div id="playback-modal" class="modal" style="display: none;">
    <div class="modal-content">
        <div class="modal-header">
            <h2>Playback - <span id="playback-camera-name"></span></h2>
            <button class="modal-close">&times;</button>
        </div>
        
        <div class="modal-body">
            <!-- Date picker -->
            <div class="playback-controls">
                <input type="date" id="playback-date" />
                <button id="btn-load-recordings">Load</button>
            </div>
            
            <!-- Timeline visualization -->
            <div class="timeline-container">
                <canvas id="timeline-canvas"></canvas>
            </div>
            
            <!-- Video player -->
            <div class="playback-player">
                <video id="playback-video" controls></video>
            </div>
            
            <!-- Recording list -->
            <div class="recording-list">
                <h3>Recordings</h3>
                <div id="recording-items"></div>
            </div>
        </div>
    </div>
</div>
```

**Playback UI controller** (static/js/recording/playback-ui.js):

```javascript
/**
 * Playback UI - timeline and video playback
 */
class PlaybackUI {
    constructor() {
        this.recordings = [];
        this.motionEvents = [];
        this.currentCameraId = null;
        this.init();
    }

    init() {
        // Modal close button
        document.querySelector('.modal-close').addEventListener('click', () => {
            document.getElementById('playback-modal').style.display = 'none';
        });

        // Load recordings button
        document.getElementById('btn-load-recordings').addEventListener('click', () => {
            const date = new Date(document.getElementById('playback-date').value);
            this.loadRecordings(this.currentCameraId, date);
        });

        // Set default date to today
        document.getElementById('playback-date').valueAsDate = new Date();
    }

    async loadRecordings(cameraId, date = new Date()) {
        this.currentCameraId = cameraId;
        
        try {
            // Load recordings from PostgREST
            this.recordings = await recordingAPI.getRecordingsForDate(cameraId, date);
            
            // Load motion events
            this.motionEvents = await recordingAPI.getMotionEvents(cameraId, {
                startTime: new Date(date.setHours(0, 0, 0, 0)),
                endTime: new Date(date.setHours(23, 59, 59, 999))
            });
            
            // Update UI
            this.renderRecordingList();
            this.renderTimeline();
            
        } catch (error) {
            console.error('Failed to load recordings:', error);
        }
    }

    renderRecordingList() {
        const container = document.getElementById('recording-items');
        container.innerHTML = '';
        
        if (this.recordings.length === 0) {
            container.innerHTML = '<p>No recordings found for this date.</p>';
            return;
        }
        
        this.recordings.forEach(recording => {
            const item = document.createElement('div');
            item.className = 'recording-item';
            
            const timestamp = new Date(recording.timestamp);
            const duration = recording.duration_seconds || 0;
            const size = this.formatBytes(recording.file_size_bytes || 0);
            
            item.innerHTML = `
                <div class="recording-info">
                    <span class="recording-time">${timestamp.toLocaleTimeString()}</span>
                    <span class="recording-duration">${this.formatDuration(duration)}</span>
                    <span class="recording-size">${size}</span>
                    <span class="recording-source">${recording.motion_source || 'manual'}</span>
                </div>
                <div class="recording-actions">
                    <button class="btn-play" data-file="${recording.file_path}">Play</button>
                    <button class="btn-download" data-file="${recording.file_path}">Download</button>
                </div>
            `;
            
            // Play button handler
            item.querySelector('.btn-play').addEventListener('click', (e) => {
                this.playRecording(e.target.dataset.file);
            });
            
            // Download button handler
            item.querySelector('.btn-download').addEventListener('click', (e) => {
                this.downloadRecording(e.target.dataset.file);
            });
            
            container.appendChild(item);
        });
    }

    renderTimeline() {
        const canvas = document.getElementById('timeline-canvas');
        const ctx = canvas.getContext('2d');
        
        // Set canvas size
        canvas.width = canvas.offsetWidth;
        canvas.height = 100;
        
        // Clear canvas
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        
        // Draw 24-hour timeline
        const hoursWidth = canvas.width / 24;
        
        // Draw hour markers
        ctx.strokeStyle = '#ccc';
        ctx.lineWidth = 1;
        for (let hour = 0; hour <= 24; hour++) {
            const x = hour * hoursWidth;
            ctx.beginPath();
            ctx.moveTo(x, 0);
            ctx.lineTo(x, 10);
            ctx.stroke();
            
            // Hour labels
            ctx.fillStyle = '#666';
            ctx.font = '10px sans-serif';
            ctx.fillText(hour.toString().padStart(2, '0'), x - 10, 25);
        }
        
        // Draw recordings as bars
        this.recordings.forEach(recording => {
            const start = new Date(recording.timestamp);
            const startHour = start.getHours() + start.getMinutes() / 60;
            const durationHours = (recording.duration_seconds || 0) / 3600;
            
            const x = startHour * hoursWidth;
            const width = durationHours * hoursWidth;
            
            ctx.fillStyle = recording.motion_triggered ? '#ff6b6b' : '#4ecdc4';
            ctx.fillRect(x, 35, width, 20);
        });
        
        // Draw motion events as markers
        this.motionEvents.forEach(event => {
            const eventTime = new Date(event.timestamp);
            const eventHour = eventTime.getHours() + eventTime.getMinutes() / 60;
            const x = eventHour * hoursWidth;
            
            ctx.fillStyle = '#ff0000';
            ctx.beginPath();
            ctx.arc(x, 70, 3, 0, 2 * Math.PI);
            ctx.fill();
        });
        
        // Add click handler for timeline
        canvas.addEventListener('click', (e) => {
            const rect = canvas.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const hour = (x / canvas.width) * 24;
            
            // Find recording at clicked time
            const clickedRecording = this.recordings.find(rec => {
                const recStart = new Date(rec.timestamp);
                const recHour = recStart.getHours() + recStart.getMinutes() / 60;
                const recDuration = (rec.duration_seconds || 0) / 3600;
                return hour >= recHour && hour <= recHour + recDuration;
            });
            
            if (clickedRecording) {
                this.playRecording(clickedRecording.file_path);
            }
        });
    }

    playRecording(filePath) {
        const video = document.getElementById('playback-video');
        
        // Determine which tier the file is in (recent or archive)
        const apiPath = filePath.includes('/NVR_Recent/')
            ? '/api/recording/stream/recent'
            : '/api/recording/stream/archive';
        
        // Extract relative path
        const relativePath = filePath.split('/').slice(-4).join('/'); // camera/date/file
        
        video.src = `${apiPath}/${relativePath}`;
        video.play();
    }

    downloadRecording(filePath) {
        const relativePath = filePath.split('/').slice(-4).join('/');
        const tier = filePath.includes('/NVR_Recent/') ? 'recent' : 'archive';
        
        window.location.href = `/api/recording/download/${tier}/${relativePath}`;
    }

    formatBytes(bytes) {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
    }

    formatDuration(seconds) {
        const mins = Math.floor(seconds / 60);
        const secs = seconds % 60;
        return `${mins}:${secs.toString().padStart(2, '0')}`;
    }
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    window.playbackUI = new PlaybackUI();
});
```

---

## Implementation Phases

### Phase 1: Database Foundation (Week 1)

**Objectives**:
- Set up PostgreSQL + PostgREST containers
- Create database schema
- Validate API functionality

**Tasks**:
1. Add PostgreSQL and PostgREST to docker-compose.yml
2. Create `/mnt/sdc/postgres_data/` directory
3. Write `init-db.sql` with full schema
4. Test database connection and API endpoints
5. Write Python PostgREST client class

**Validation**:
```bash
# Test PostgreSQL
docker exec -it nvr-postgres psql -U nvr_api -d nvr -c "\dt"

# Test PostgREST API
curl http://localhost:3001/
curl http://localhost:3001/recordings

# Insert test record
curl -X POST http://localhost:3001/recordings \
  -H "Content-Type: application/json" \
  -d '{"camera_id": "TEST", "timestamp": "2025-11-10T00:00:00Z", "file_path": "/test.mp4", "storage_tier": "recent"}'
```

**Deliverables**:
- ✅ PostgreSQL container running
- ✅ PostgREST API responding
- ✅ Database schema created
- ✅ Python client class implemented
- ✅ Documentation updated

---

### Phase 2: Recording Service Core (Week 2)

**Objectives**:
- Implement recording service
- Manual recording functionality
- Flask API routes

**Tasks**:
1. Create `services/recording/recording_service.py`
2. Create `services/recording/storage_manager.py`
3. Implement manual recording start/stop
4. Add Flask routes: `/api/recording/start`, `/api/recording/stop`, `/api/recording/status`
5. Integrate PostgREST client for metadata storage
6. Create `/mnt/sdc/NVR_Recent/` directory structure

**Validation**:
```bash
# Start manual recording
curl -X POST http://localhost:5000/api/recording/start/OFFICE_KITCHEN

# Check status
curl http://localhost:5000/api/recording/status

# Stop recording
curl -X POST http://localhost:5000/api/recording/stop/OFFICE_KITCHEN

# Verify file created
ls -lh /mnt/sdc/NVR_Recent/motion/OFFICE_KITCHEN/

# Verify database record
curl http://localhost:3001/recordings?camera_id=eq.OFFICE_KITCHEN
```

**Deliverables**:
- ✅ Recording service implemented
- ✅ Manual recording working
- ✅ Files written to disk
- ✅ Metadata in database
- ✅ Flask routes functional

---

### Phase 3: FFmpeg Motion Detection (Week 3)

**Objectives**:
- Universal motion detection
- Motion-triggered recording
- Debouncing and cooldown logic

**Tasks**:
1. Create `services/motion/ffmpeg_motion_detector.py`
2. Create `services/motion/motion_debouncer.py`
3. Integrate with recording service
4. Add per-camera sensitivity configuration
5. Implement pre/post-buffer logic
6. Add motion event insertion to database

**Validation**:
```bash
# Enable FFmpeg motion detection for one camera
# Edit cameras.json:
{
  "OFFICE_KITCHEN": {
    "recording": {
      "enabled": true,
      "mode": "motion",
      "motion_detection_priority": ["ffmpeg"],
      "motion_settings": {
        "ffmpeg": {
          "enabled": true,
          "sensitivity": 0.02
        }
      }
    }
  }
}

# Restart app, trigger motion (wave hand in front of camera)
# Verify recording started automatically

# Check motion events
curl http://localhost:3001/motion_events?camera_id=eq.OFFICE_KITCHEN

# Check recordings linked to motion events
curl "http://localhost:3001/recordings?camera_id=eq.OFFICE_KITCHEN&motion_triggered=eq.true"
```

**Deliverables**:
- ✅ FFmpeg motion detection working
- ✅ Motion-triggered recording functional
- ✅ Motion events stored in database
- ✅ Recordings linked to motion events
- ✅ Configurable sensitivity per camera

---

### Phase 4: ONVIF Motion Detection (Week 4)

**Objectives**:
- ONVIF event subscriptions
- Priority fallback logic
- Camera-specific optimizations

**Tasks**:
1. Create `services/onvif/onvif_event_handler.py`
2. Implement PullPoint subscription
3. Add ONVIF event parsing
4. Integrate with motion detection service
5. Implement priority chain (ONVIF → FFmpeg fallback)
6. Test with Reolink, Amcrest cameras
7. Validate UniFi ONVIF Events support

**Validation**:
```bash
# Enable ONVIF motion for Reolink camera
{
  "REOLINK_LAUNDRY": {
    "recording": {
      "motion_detection_priority": ["onvif", "ffmpeg"],
      "motion_settings": {
        "onvif": {
          "enabled": true,
          "poll_interval_ms": 1000
        }
      }
    }
  }
}

# Trigger motion, verify ONVIF event captured
curl "http://localhost:3001/motion_events?camera_id=eq.REOLINK_LAUNDRY&source=eq.onvif"

# Disable ONVIF, verify FFmpeg fallback works
# (temporarily block port 80 on camera)
curl "http://localhost:3001/motion_events?camera_id=eq.REOLINK_LAUNDRY&source=eq.ffmpeg"
```

**Deliverables**:
- ✅ ONVIF event subscriptions working
- ✅ Priority fallback functional
- ✅ Reolink motion detection optimized
- ✅ Amcrest motion detection optimized
- ✅ UniFi ONVIF Events validated

---

### Phase 5: Frontend Integration (Week 5)

**Objectives**:
- Recording controls in UI
- Playback interface
- Timeline visualization

**Tasks**:
1. Create `static/js/recording/api-client.js`
2. Create `static/js/recording/recording-controller.js`
3. Create `static/js/recording/playback-ui.js`
4. Add recording indicator to camera tiles
5. Add manual recording buttons
6. Implement playback modal
7. Create timeline canvas visualization
8. Add video playback controls

**Validation**:
- ✅ Recording indicator shows when recording
- ✅ Manual start/stop buttons work
- ✅ Playback modal opens and loads recordings
- ✅ Timeline shows recordings and motion events
- ✅ Clicking timeline plays correct video
- ✅ Download button works

**Deliverables**:
- ✅ Full UI integration complete
- ✅ Recording controls functional
- ✅ Playback interface working
- ✅ Timeline visualization implemented

---

### Phase 6: Storage Management (Week 6)

**Objectives**:
- Archive tier migration
- Retention cleanup
- Storage quota management

**Tasks**:
1. Create `services/storage/archive_daemon.py`
2. Create `services/storage/cleanup_daemon.py`
3. Implement migration from recent → archive tier
4. Implement retention policy enforcement
5. Add per-camera storage quota checking
6. Schedule daemons (cron or systemd timer)
7. Add storage metrics API endpoint

**Validation**:
```bash
# Create old recording (manually adjust timestamp in DB)
# Run archive daemon
python services/storage/archive_daemon.py --dry-run

# Verify migration logic
ls -lh /mnt/sdc/NVR_Recent/motion/OFFICE_KITCHEN/
ls -lh /mnt/THE_BIG_DRIVE/NVR_RECORDINGS/motion/OFFICE_KITCHEN/

# Check database updated
curl "http://localhost:3001/recordings?storage_tier=eq.archive"

# Test cleanup daemon
python services/storage/cleanup_daemon.py --dry-run

# Verify old files deleted
```

**Deliverables**:
- ✅ Archive migration working
- ✅ Retention cleanup functional
- ✅ Storage quotas enforced
- ✅ Daemons scheduled
- ✅ Storage metrics API available

---

### Phase 7: Optimization & Polish (Week 7)

**Objectives**:
- Performance tuning
- Error handling improvements
- Documentation finalization

**Tasks**:
1. Optimize database queries (add missing indexes)
2. Implement recording process health monitoring
3. Add graceful error recovery
4. Tune FFmpeg parameters per camera
5. Add comprehensive logging
6. Write operational runbooks
7. Create troubleshooting guide
8. Performance testing (all 17 cameras)

**Validation**:
- ✅ All 17 cameras recording simultaneously
- ✅ No stream interruptions
- ✅ Database queries < 100ms
- ✅ Storage tier migrations working
- ✅ Error recovery tested
- ✅ Logs comprehensive and useful

**Deliverables**:
- ✅ Production-ready system
- ✅ Complete documentation
- ✅ Operational procedures
- ✅ Performance validated

---

## Configuration Reference

### Per-Camera Recording Configuration

**Location**: `config/cameras.json`

**Full example**:

```json
{
  "OFFICE_KITCHEN": {
    "packager_path": "OFFICE_KITCHEN",
    "name": "OFFICE KITCHEN",
    "type": "unifi",
    "capabilities": ["ONVIF", "streaming"],
    
    "recording": {
      "enabled": true,
      "mode": "motion",
      
      "motion_detection_priority": ["ffmpeg", "onvif"],
      
      "storage_tiers": {
        "recent_retention_hours": 48,
        "archive_retention_days": 7
      },
      
      "motion_settings": {
        "pre_buffer_seconds": 30,
        "post_buffer_seconds": 30,
        "cooldown_seconds": 5,
        
        "ffmpeg": {
          "enabled": true,
          "sensitivity": 0.02
        },
        
        "onvif": {
          "enabled": true,
          "poll_interval_ms": 1000
        },
        
        "eufy_bridge": {
          "enabled": false
        }
      },
      
      "continuous_settings": {
        "enabled": false,
        "segment_duration_minutes": 60
      },
      
      "retention": {
        "motion_days": 7,
        "continuous_days": 3,
        "max_storage_gb": 100
      },
      
      "quality": {
        "codec": "copy",
        "resolution": "native",
        "fps": "native",
        "bitrate": "2M"
      }
    }
  }
}
```

**Configuration fields**:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | boolean | `false` | Enable recording for this camera |
| `mode` | string | `"motion"` | Recording mode: `"motion"`, `"continuous"`, `"disabled"` |
| `motion_detection_priority` | array | `["ffmpeg"]` | Priority chain: `["onvif", "ffmpeg", "eufy_bridge"]` |
| `recent_retention_hours` | integer | `48` | Hours to keep in recent tier before archive |
| `archive_retention_days` | integer | `7` | Days to keep in archive tier before deletion |
| `pre_buffer_seconds` | integer | `30` | Seconds to include before motion trigger |
| `post_buffer_seconds` | integer | `30` | Seconds to continue after last motion |
| `cooldown_seconds` | integer | `5` | Minimum time between motion triggers |
| `ffmpeg.sensitivity` | float | `0.02` | Scene change threshold (0.01-0.10) |
| `onvif.poll_interval_ms` | integer | `1000` | ONVIF event polling interval |
| `max_storage_gb` | integer | `100` | Per-camera storage quota |
| `codec` | string | `"copy"` | Video codec: `"copy"` (no re-encode) or `"h264"` |

---

## Testing & Validation

### Unit Tests

**Test recording service**:
```python
# tests/test_recording_service.py
import pytest
from services.recording.recording_service import RecordingService

def test_start_recording(mock_camera_repo, mock_storage_config):
    service = RecordingService(mock_camera_repo, mock_storage_config)
    
    # Start recording
    success = service.start_recording('TEST_CAMERA')
    assert success
    
    # Verify active recording
    assert 'TEST_CAMERA' in service.active_recordings
    
    # Verify FFmpeg process running
    session = service.active_recordings['TEST_CAMERA']
    assert session.process.poll() is None

def test_motion_debouncing(mock_recording_service):
    debouncer = MotionDebouncer(cooldown_seconds=5)
    
    # First trigger should succeed
    assert debouncer.should_trigger('CAMERA_1')
    
    # Immediate second trigger should fail
    assert not debouncer.should_trigger('CAMERA_1')
    
    # After cooldown, should succeed
    time.sleep(5)
    assert debouncer.should_trigger('CAMERA_1')
```

### Integration Tests

**Test motion detection → recording → database flow**:
```bash
# tests/integration/test_motion_to_recording.sh

# 1. Enable FFmpeg motion detection
curl -X POST http://localhost:5000/api/recording/config/TEST_CAMERA \
  -d '{"motion_detection_priority": ["ffmpeg"]}'

# 2. Trigger motion (simulate by sending test frame with large scene change)
curl -X POST http://localhost:5000/api/test/motion/TEST_CAMERA

# 3. Verify motion event created
EVENTS=$(curl -s http://localhost:3001/motion_events?camera_id=eq.TEST_CAMERA)
echo $EVENTS | jq '.[] | .source' | grep "ffmpeg"

# 4. Wait for recording to start (1-2 seconds)
sleep 2

# 5. Verify recording created
RECORDINGS=$(curl -s http://localhost:3001/recordings?camera_id=eq.TEST_CAMERA&status=eq.recording)
echo $RECORDINGS | jq '.[0] | .id'

# 6. Verify file exists
FILE_PATH=$(echo $RECORDINGS | jq -r '.[0] | .file_path')
test -f "$FILE_PATH" && echo "✅ Recording file exists"

# 7. Stop recording after 5 seconds
sleep 5
curl -X POST http://localhost:5000/api/recording/stop/TEST_CAMERA

# 8. Verify recording completed
COMPLETED=$(curl -s "http://localhost:3001/recordings?camera_id=eq.TEST_CAMERA&status=eq.completed")
echo $COMPLETED | jq '.[0] | .duration_seconds'
```

### Performance Tests

**Test concurrent recording (all 17 cameras)**:
```bash
# tests/performance/test_concurrent_recording.sh

# Start recording on all cameras
for CAMERA_ID in $(curl -s http://localhost:5000/api/cameras | jq -r '.[].camera_id'); do
    curl -X POST http://localhost:5000/api/recording/start/$CAMERA_ID &
done
wait

# Monitor system resources
top -b -n 1 | head -20
df -h /mnt/sdc

# Let run for 5 minutes
sleep 300

# Check all recordings are active
ACTIVE_COUNT=$(curl -s http://localhost:3001/recordings?status=eq.recording | jq '. | length')
echo "Active recordings: $ACTIVE_COUNT / 17"

# Stop all recordings
for CAMERA_ID in $(curl -s http://localhost:5000/api/cameras | jq -r '.[].camera_id'); do
    curl -X POST http://localhost:5000/api/recording/stop/$CAMERA_ID &
done
wait

# Verify no recording process leaks
ps aux | grep ffmpeg | grep recording
```

---

## Operational Procedures

### Daily Operations

**Monitor storage usage**:
```bash
# Check recent tier usage
du -sh /mnt/sdc/NVR_Recent/*

# Check archive tier usage
du -sh /mnt/THE_BIG_DRIVE/NVR_RECORDINGS/*

# Check per-camera usage
du -sh /mnt/sdc/NVR_Recent/motion/*
```

**Monitor database size**:
```bash
# PostgreSQL database size
docker exec nvr-postgres psql -U nvr_api -d nvr -c "\l+"

# Table sizes
docker exec nvr-postgres psql -U nvr_api -d nvr -c "\dt+"
```

**Check recording health**:
```bash
# Active recordings
curl http://localhost:5000/api/recording/status | jq

# Recent motion events (last hour)
curl "http://localhost:3001/motion_events?timestamp=gte.$(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S)Z" | jq

# Failed recordings
curl "http://localhost:3001/recordings?status=eq.error" | jq
```

### Maintenance Tasks

**Weekly: Verify archive migration**:
```bash
# Check recordings older than 48 hours in recent tier
curl "http://localhost:3001/recordings?storage_tier=eq.recent&timestamp=lte.$(date -u -d '2 days ago' +%Y-%m-%dT%H:%M:%S)Z" | jq

# Should be zero - if not, archive daemon isn't running
```

**Monthly: Database maintenance**:
```bash
# Vacuum database (reclaim space)
docker exec nvr-postgres psql -U nvr_api -d nvr -c "VACUUM ANALYZE;"

# Check for bloat
docker exec nvr-postgres psql -U nvr_api -d nvr -c "
SELECT schemaname, tablename, 
       pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables 
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
"
```

### Troubleshooting

**Problem: Recording not starting**

1. Check camera configuration:
   ```bash
   curl http://localhost:5000/api/cameras/CAMERA_ID | jq '.recording'
   ```

2. Check motion detection service status:
   ```bash
   docker logs unified-nvr | grep "motion detection"
   ```

3. Test manual recording:
   ```bash
   curl -X POST http://localhost:5000/api/recording/start/CAMERA_ID
   ```

4. Check FFmpeg process:
   ```bash
   docker exec unified-nvr ps aux | grep ffmpeg
   ```

**Problem: No motion detection**

1. Check FFmpeg motion detector logs:
   ```bash
   docker logs unified-nvr | grep "scene_score"
   ```

2. Verify sensitivity setting:
   ```bash
   curl http://localhost:5000/api/cameras/CAMERA_ID | jq '.recording.motion_settings.ffmpeg.sensitivity'
   ```

3. Test ONVIF events (if enabled):
   ```bash
   docker exec unified-nvr python -c "
   from services.onvif.onvif_event_handler import test_onvif_events
   test_onvif_events('CAMERA_ID')
   "
   ```

**Problem: Database connection failed**

1. Check PostgreSQL container:
   ```bash
   docker ps | grep postgres
   docker logs nvr-postgres
   ```

2. Test connection:
   ```bash
   docker exec nvr-postgres pg_isready -U nvr_api -d nvr
   ```

3. Check PostgREST:
   ```bash
   curl http://localhost:3001/
   docker logs nvr-postgrest
   ```

**Problem: Disk full**

1. Check storage usage:
   ```bash
   df -h /mnt/sdc
   df -h /mnt/THE_BIG_DRIVE
   ```

2. Manually run cleanup daemon:
   ```bash
   docker exec unified-nvr python services/storage/cleanup_daemon.py
   ```

3. Check retention settings:
   ```bash
   curl http://localhost:3001/camera_recording_config | jq
   ```

4. Temporarily disable recording:
   ```bash
   curl -X POST http://localhost:5000/api/recording/stop-all
   ```

---

## Next Steps

**Immediate Actions** (before starting Phase 1):

1. ✅ Convert `/mnt/THE_BIG_DRIVE` to ext4
2. ✅ Create `/mnt/sdc/NVR_Recent/` directory structure
3. ✅ Generate secure PostgreSQL password
4. ✅ Backup current `cameras.json`
5. ✅ Review and approve architecture

**Phase 1 Checklist**:
- [ ] Update docker-compose.yml
- [ ] Create init-db.sql
- [ ] Create /mnt/sdc/postgres_data/
- [ ] Start PostgreSQL container
- [ ] Verify database schema created
- [ ] Test PostgREST API
- [ ] Write Python PostgREST client
- [ ] Update project documentation

**Questions to Resolve**:
1. UniFi ONVIF Events support - need to test
2. Eufy Bridge 2FA - defer or improve?
3. Audio recording - include or video-only?
4. Snapshot generation - on motion trigger?
5. Multi-user authentication - needed?

---

## Appendix

### Glossary

- **Motion-triggered recording**: Recording started by motion detection event
- **Continuous recording**: 24/7 recording regardless of motion
- **Pre-buffer**: Video recorded before motion trigger (requires circular buffer)
- **Post-buffer**: Video recorded after last motion event
- **Recent tier**: Fast local storage for recent recordings
- **Archive tier**: Large USB storage for long-term retention
- **Storage quota**: Per-camera maximum storage allocation
- **Debouncing**: Preventing rapid successive motion triggers

### References

- [FFmpeg Documentation](https://ffmpeg.org/documentation.html)
- [PostgREST Documentation](https://postgrest.org/en/stable/)
- [PostgreSQL Documentation](https://www.postgresql.org/docs/)
- [ONVIF Specifications](https://www.onvif.org/specs/)
- [Project History](./README_project_history.md)

### Change Log

- 2025-11-10: Initial architecture document created
- (Future updates as implementation progresses)

---

**End of Document**