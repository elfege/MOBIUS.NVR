# Plan: User-Based Settings Implementation

*Created: January 19, 2026*
*Status: DEFERRED - Focus on playback first*

---

## Architectural Decision

### Current State

| File | Purpose | Reload Behavior |
|------|---------|-----------------|
| `cameras.json` | Camera definitions, FFmpeg params | Requires container restart |
| `recording_settings.json` | Recording/storage config | Requires container restart |
| PostgreSQL | Recordings, motion events, PTZ data | Live |

### Problem

JSON configs are cached until container recreation. Users cannot modify settings at runtime without SSH access and manual container restart.

---

## Proposed Hybrid Architecture

### Tier 1: Immutable Config (JSON - Bootstrap)

Stays in JSON, version-controlled, requires restart:

- FFmpeg parameters (`ffmpeg_params`)
- MediaMTX path templates
- Docker volume mappings
- Stream type capabilities per camera type
- Audio codec settings

### Tier 2: Mutable Config (Database - Live)

Migrated to PostgreSQL, editable via UI:

**Camera Settings:**
- `name` - Display name
- `host` - Camera IP address
- `username` / `password` - Credentials (encrypted)
- `stream_type` - Constrained dropdown (LL_HLS, WEBRTC, MJPEG, HLS)
- `video_sub` resolution - Grid view (constrained options)
- `video_main` resolution - Fullscreen (constrained options)

**Recording Settings:**
- All of `recording_settings.json` content
- Storage paths (recent base, archive base)
- Retention periods per camera
- Motion detection settings

### Tier 3: Bootstrap Behavior

1. On first startup (or "reset to defaults"):
   - Seed database from JSON files
   - JSON becomes the "factory defaults"

2. On subsequent startups:
   - Database takes precedence
   - JSON ignored unless DB empty

---

## Camera Settings UI Fields

| JSON Key | UI Label | Type | Notes |
|----------|----------|------|-------|
| `name` | Camera Name | text | Free text |
| `host` | Camera IP | text | Validation: IP format |
| `username` | Username | text | Optional override |
| `password` | Password | password | Optional override, encrypted |
| `type` | Brand | dropdown | reolink, eufy, unifi, amcrest, sv3c |
| `model` | Model | dropdown | Filtered by brand |
| `stream_type` | Stream Type | dropdown | LL_HLS, WEBRTC, MJPEG, HLS |
| `serial` | Serial Number | readonly | Primary key, not editable |

### Resolution Constraints

**Grid View (video_sub):**
- 320x180 (Minimum - low-end devices)
- 640x360 (Default)
- 854x480 (HD-ready)
- 1280x720 (Warning: High CPU)

**Fullscreen (video_main):**
- 1280x720 (720p)
- 1920x1080 (1080p - Default)
- 2560x1440 (2K - Warning)
- 3840x2160 (4K - Warning: May damage hardware)

### MJPEG Mode Options

The `true_mjpeg` boolean is deprecated. New field `mjpeg_source`:

| Value | Description |
|-------|-------------|
| `mediamtx_tap` | Tap MediaMTX republished stream |
| `snapshot_poll` | Poll camera snapshot endpoint |
| `native_stream` | Direct camera MJPEG stream (Amcrest `/video.cgi`) |
| `disabled` | No MJPEG fallback |

---

## Container Self-Restart

### Current: OHVD Pattern (SSH-based)

From user's OHVD project - uses SSH to host to trigger restart. Not ideal for containerized deployments.

### Proposed: Docker Socket Mount

```yaml
# docker-compose.yml
services:
  nvr:
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
```

```python
# Flask route
@app.route('/api/system/restart', methods=['POST'])
def restart_container():
    """
    Restart the NVR container via Docker API.

    WARNING: Requires docker.sock mount with appropriate permissions.
    """
    import docker
    client = docker.from_env()
    container = client.containers.get('unified-nvr')
    container.restart()
    return jsonify({'status': 'restarting'})
```

### Alternative: Supervisor + Signal

Use supervisord inside container, send SIGHUP to reload config without full restart.

---

## Database Schema for Settings

```sql
-- Camera overrides (mutable settings only)
CREATE TABLE camera_settings (
    camera_id VARCHAR(50) PRIMARY KEY,  -- Serial number
    display_name VARCHAR(100),
    host VARCHAR(45),  -- IPv4/IPv6
    username_override VARCHAR(100),
    password_override_encrypted TEXT,
    stream_type VARCHAR(20),
    mjpeg_source VARCHAR(20),
    video_sub_resolution VARCHAR(20),
    video_main_resolution VARCHAR(20),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Recording settings (replaces recording_settings.json)
CREATE TABLE recording_settings (
    id BIGSERIAL PRIMARY KEY,
    camera_id VARCHAR(50),  -- NULL = global defaults
    category VARCHAR(30) NOT NULL,
    settings JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT recording_settings_unique UNIQUE (camera_id, category)
);

-- Storage configuration
CREATE TABLE storage_settings (
    setting_key VARCHAR(50) PRIMARY KEY,
    setting_value JSONB NOT NULL,
    description TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## Implementation Phases

### Phase 1: Storage Migration (CURRENT PRIORITY)

- Keep `recording_settings.json` as-is
- Add storage paths to JSON
- Implement file migration recent → archive
- Fix timeline playback
- **No database settings migration yet**

### Phase 2: Recording Settings → Database

- Create `recording_settings` table
- Migrate JSON on first startup
- Add API endpoints for CRUD
- Build settings UI

### Phase 3: Camera Settings → Database

- Create `camera_settings` table
- Credential encryption (Fernet or similar)
- Build camera management UI
- Implement container restart via docker.sock

### Phase 4: Advanced Features

- ONVIF capability discovery for resolution options
- Per-user settings (if multi-user needed)
- Config export/import
- Audit log for setting changes

---

## Security Considerations

1. **Credential Storage**: Never store plaintext passwords
   - Use Fernet symmetric encryption
   - Key derived from environment variable
   - Or use Docker secrets

2. **Docker Socket**: Mount read-only, limit to restart only

3. **Input Validation**: Strict validation on all user inputs
   - IP address format
   - Resolution within allowed list
   - Stream type from enum

---

## Decision: Why Defer This

1. **Playback is broken** - Users can't view recordings at all
2. **Storage migration missing** - Files never move to archive
3. **Settings work** - Current JSON approach functions, just requires restart
4. **Scope creep risk** - This is a significant architectural change

**Recommendation**: Fix playback and storage first. Settings overhaul is Phase 2+.

---

## References

- Current schema: `psql/init-db.sql`
- Recording config: `config/recording_settings.json`
- Camera config: `config/cameras.json`
- Config loader: `config/recording_config_loader.py`
- OHVD restart pattern: `ssh hvtmc:OHVD_APP_PROD` (user's other project)
