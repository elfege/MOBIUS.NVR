# Proposal: Database-Based Camera Configuration Migration

**Date:** February 16, 2026
**Status:** Draft for Review
**Branch:** `ipad_grid_force_webrtc_fix_FEB_16_2026_a`

---

## Current State Analysis

### What We Have (Feb 8-16 Implementation)

**Stream Type Preferences (Partially Implemented):**
- ✅ Backend API: `GET/PUT /api/user/stream-preferences/<camera_serial>`
- ✅ Database table: `user_camera_preferences` (migration 005)
- ✅ Frontend: `loadUserStreamPreferences()` loads from DB, overrides `data-stream-type`
- ✅ Frontend: `switchStreamType()` does live switch + saves to DB
- ❌ **Does NOT modify cameras.json**
- ❌ **Does NOT restart backend services**

**Camera Configuration:**
- `config/cameras.json` — canonical source (gitignored, not in DB)
- Changes affect ALL users globally
- Restart required for most changes
- No per-user camera visibility/selection

**User Preferences:**
- Camera access control: admin sets which cameras user can see (via `user_cameras` table)
- Stream type preference: user can set preferred type (via `user_camera_preferences` table)
- ❌ **NO localStorage for camera selection/visibility**
- ❌ **Dropdown selection resets on page reload**

---

## Issues Identified

### 1. Stream Type Switching Not Working (URGENT)

**Problem:** User selects MJPEG in UI, nothing happens. Snapshot MJPEG cameras don't switch.

**Root Cause Analysis:**

The current `switchStreamType()` implementation:
1. Updates browser-side `data-stream-type` attribute ✅
2. Starts new stream type client-side ✅
3. Saves preference to DB ✅

**But:**
- Backend services (stream_manager.py, segment_buffer.py, etc.) still use `cameras.json` stream_type
- MediaMTX paths are created based on cameras.json
- FFmpeg processes launched with cameras.json parameters
- No backend restart triggered

**Why It Partially Works:**
- Browser can start HLS/WebRTC/MJPEG clients independently
- MediaMTX paths exist if camera is LL_HLS in cameras.json
- Switching between MediaMTX-based types (WebRTC ↔ HLS) works
- **Switching TO/FROM MJPEG fails** because backend expects different stream source

**Example Failure:**
- Camera in cameras.json: `"stream_type": "LL_HLS"`
- User selects: MJPEG
- Frontend tries to load `/mjpeg/<camera_serial>`
- Backend MJPEG service not running for this camera
- Result: nothing happens

### 2. Camera Selection Not Persistent

**Problem:** Dropdown camera selection resets on page reload.

**Current Behavior:**
- User can see cameras authorized by admin (from `user_cameras` table)
- Dropdown shows all authorized cameras
- Selection state not saved anywhere
- Page reload → back to default view (all cameras visible)

**User Expectation:**
- Select subset of authorized cameras to display
- Selection persists in localStorage
- Page reload → sees same cameras they selected

### 3. No Per-User Configuration

**Problem:** cameras.json changes affect everyone.

**Current:**
- cameras.json is global configuration
- Admin changes stream type → affects all users
- No way for user A to prefer WebRTC while user B prefers HLS **with backend support**

---

## Proposed Solutions

### Option A: Quick Fix (2-4 hours) — Hybrid Approach

**Keep cameras.json as primary, add localStorage for UI state.**

#### A.1 Fix Stream Type Switching (Immediate)

**Approach:** Backend service restart on stream type change.

**Implementation:**
1. Modify `switchStreamType()` to call backend restart endpoint
2. Backend temporarily overrides stream type for this camera (in-memory)
3. Restart only affected services (stream manager, MJPEG if needed)
4. Update MediaMTX path if needed
5. Show loading modal during restart (~5-10s)

**Pros:**
- Works within current architecture
- No database migration needed
- Backend services use correct stream type

**Cons:**
- Service restart per switch (slow)
- Still no true per-user backend config
- In-memory override lost on full restart

**Code Changes:**
- `static/js/streaming/stream.js`: Add `/api/stream/reconfigure/<camera_serial>` call
- `app.py`: New endpoint to update stream type + restart services
- `streaming/stream_manager.py`: Support in-memory stream type override

#### A.2 Camera Selection Persistence (Easy)

**Approach:** localStorage for visible camera list.

**Implementation:**
```javascript
// On dropdown selection change
const selectedCameras = getSelectedCamerasFromDropdown();
localStorage.setItem('selectedCameras', JSON.stringify(selectedCameras));

// On page load
const selected = JSON.parse(localStorage.getItem('selectedCameras') || '[]');
if (selected.length > 0) {
    // Show only selected cameras
    hideUnselectedCameras(selected);
}
```

**Pros:**
- Simple, fast implementation (30 min)
- No backend changes
- Works immediately

**Cons:**
- localStorage only (not synced across devices)

---

### Option B: Database Migration (8-12 hours) — Full Solution

**Migrate ALL camera configuration to database, keep cameras.json as canonical source for resets.**

#### B.1 Database Schema

**New Tables:**

```sql
-- Camera base configuration (canonical from cameras.json)
CREATE TABLE cameras (
    serial VARCHAR(255) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    brand VARCHAR(100),
    model VARCHAR(100),
    ip_address VARCHAR(45),
    rtsp_port INTEGER,
    http_port INTEGER,
    username VARCHAR(255),
    password_encrypted TEXT,
    capabilities JSONB,  -- array of strings: ptz, audio, motion, etc.
    stream_configs JSONB,  -- rtsp_input, go2rtc, etc.
    ptz_config JSONB,
    recording_config JSONB,
    hidden BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Per-user camera preferences (overrides global config)
CREATE TABLE user_camera_config (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    camera_serial VARCHAR(255) REFERENCES cameras(serial) ON DELETE CASCADE,
    preferred_stream_type VARCHAR(50),  -- Overrides cameras.stream_type
    visible BOOLEAN DEFAULT TRUE,  -- Camera shown in user's UI
    display_order INTEGER,  -- Grid position
    custom_name VARCHAR(255),  -- User can rename camera for themselves
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, camera_serial)
);

-- Camera state (runtime, not config)
CREATE TABLE camera_state (
    camera_serial VARCHAR(255) PRIMARY KEY REFERENCES cameras(serial),
    current_stream_type VARCHAR(50),
    health_status VARCHAR(50),
    last_seen TIMESTAMP,
    active_connections INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT NOW()
);
```

#### B.2 Migration Strategy

**Phase 1: cameras.json → Database (One-Time Import)**

```python
# scripts/migrate_cameras_to_db.py
def migrate_cameras_json_to_db():
    """
    One-time migration: Read cameras.json, populate cameras table.
    """
    with open('config/cameras.json') as f:
        cameras_json = json.load(f)

    for serial, config in cameras_json['cameras'].items():
        db.execute('''
            INSERT INTO cameras (serial, name, brand, model, ...)
            VALUES (%s, %s, %s, %s, ...)
            ON CONFLICT (serial) DO NOTHING
        ''', (...))
```

**Phase 2: Auto-Sync Module (Detect New Cameras in JSON)**

```python
# services/camera_config_sync.py
class CameraConfigSync:
    """
    Monitors cameras.json for new entries not in database.
    Triggers automatic migration when new cameras added manually.
    """

    def check_for_new_cameras(self):
        json_cameras = self._load_cameras_json()
        db_cameras = self._load_db_cameras()

        new_cameras = set(json_cameras.keys()) - set(db_cameras.keys())

        if new_cameras:
            logger.info(f"Found {len(new_cameras)} new cameras in JSON, migrating...")
            for serial in new_cameras:
                self._migrate_camera(serial, json_cameras[serial])

    def _migrate_camera(self, serial, config):
        """Insert new camera from JSON into database."""
        # Insert into cameras table
        # Create default user_camera_config entries (all users can see it)
```

**Phase 3: Backend Services Use Database**

```python
# streaming/stream_manager.py
def start_stream(self, camera_serial, user_id=None):
    # OLD: Load from cameras.json
    # camera_config = cameras_json['cameras'][camera_serial]

    # NEW: Load from database with user preferences
    camera = db.query('SELECT * FROM cameras WHERE serial = %s', camera_serial)

    if user_id:
        user_pref = db.query('''
            SELECT preferred_stream_type FROM user_camera_config
            WHERE user_id = %s AND camera_serial = %s
        ''', user_id, camera_serial)

        stream_type = user_pref.preferred_stream_type or camera.stream_type
    else:
        stream_type = camera.stream_type

    # Start stream with resolved stream_type
    ...
```

**Phase 4: cameras.json Remains Canonical for Resets**

```python
# scripts/reset_camera_config.py
def reset_camera_to_canonical(camera_serial):
    """
    Reset camera configuration to cameras.json canonical source.
    Use case: user preferences broke camera, admin wants to reset.
    """
    canonical = load_cameras_json()[camera_serial]

    db.execute('''
        UPDATE cameras SET
            stream_type = %s,
            ...
        WHERE serial = %s
    ''', canonical['stream_type'], camera_serial)

    db.execute('''
        DELETE FROM user_camera_config
        WHERE camera_serial = %s
    ''', camera_serial)
```

#### B.3 UI Changes

**Camera Selection (Persistent):**
- Load visible cameras from `user_camera_config.visible`
- Dropdown updates `visible` field in DB
- Syncs across devices (unlike localStorage)

**Stream Type Switching (Real):**
- Update `user_camera_config.preferred_stream_type`
- Backend reads user preference, uses it
- No service restart needed (just reconnect stream)
- Works per-user (user A: WebRTC, user B: HLS)

---

### Option C: Hybrid Pro (4-6 hours) — Best of Both

**Database for user preferences, cameras.json for base config.**

**Keep:**
- cameras.json as base/canonical configuration
- Auto-sync module detects new cameras
- Simple DB schema (no full camera config, just user prefs)

**Add:**
- `user_camera_config` table (stream type, visibility, order)
- Backend services check user preferences before cameras.json
- localStorage as backup (offline-first)

**Schema:**
```sql
CREATE TABLE user_camera_preferences_v2 (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    camera_serial VARCHAR(255),
    preferred_stream_type VARCHAR(50),
    visible BOOLEAN DEFAULT TRUE,
    display_order INTEGER,
    UNIQUE(user_id, camera_serial)
);
```

**Backend Logic:**
```python
def get_stream_type_for_user(camera_serial, user_id):
    # 1. Check user preference in DB
    pref = db.query('SELECT preferred_stream_type FROM user_camera_preferences_v2 ...')
    if pref:
        return pref.preferred_stream_type

    # 2. Fall back to cameras.json
    return cameras_json[camera_serial]['stream_type']
```

**Pros:**
- Simpler than full migration (Option B)
- Faster than service restart (Option A)
- Per-user config works immediately
- cameras.json remains canonical

**Cons:**
- Still requires backend code changes
- Doesn't solve ALL config-in-DB needs (only stream type + visibility)

---

## Recommendation

**Immediate (Today):**
- **Option A.2** — Camera selection in localStorage (30 min fix)
- Investigate why stream switching broken (may be simpler bug than expected)

**Short-Term (Next Session):**
- **Option C** — Hybrid Pro approach
  - Extends existing `user_camera_preferences` to include visibility
  - Backend checks user pref before cameras.json
  - Simple, effective, reversible

**Long-Term (Future):**
- **Option B** — Full database migration
  - After Option C proves the pattern works
  - When ready to deprecate cameras.json as runtime source
  - Requires schema design review, migration testing

---

## Questions for User

1. **Stream switching urgency:** Should I investigate why current implementation broken before redesigning? (May be simpler bug)

2. **Restart tolerance:** Is 5-10s service restart per stream switch acceptable? (Option A.1)

3. **Scope priority:**
   - Just fix stream switching + camera selection? (Quick wins)
   - OR full database migration now? (Bigger investment)

4. **cameras.json future:** Keep as canonical forever? Or eventual deprecation?

5. **go2rtc pipeline:** Investigate go2rtc for WebRTC on mobile? (Noted as curious, not urgent)

---

**Next Steps:** User approval of approach → Implementation plan → Execute.
