# Publisher State Coordination Architecture

**Author:** Design Session
**Date:** January 3, 2026
**Status:** Design Phase
**Branch:** `recording_motion_detection_isolation_JAN_3_2026_a`

---

## Problem Statement

**Current Issue:**
When a camera's LL-HLS publisher fails or dies, there is no coordinated state management between MediaMTX and the NVR services. This causes:

1. **Resource Waste**: Motion detection, recording, and UI health monitors continuously attempt to connect to dead publishers
2. **Log Spam**: MediaMTX logs fill with "torn down by 172.19.0.6:XXXX" events (10+ per 30 seconds per dead camera)
3. **Poor UX**: UI shows "Starting..." indefinitely instead of accurate "Camera Unavailable" status
4. **No Smart Retry**: Services hammer MediaMTX paths with no exponential backoff or coordination

**User Requirement:**
> "MediaMTX fails to stream from a camera => communicate with the other containers (mostly nvr) so recorder and motion managers don't attempt until MediaMTX says 'hey, this one is online now, you can try again'"

---

## Current Architecture Analysis

### Existing State Tracking (Backend)

**File:** `/home/elfege/0_NVR/streaming/stream_manager.py`

**Stream Entry Structure:**
```python
self.active_streams[stream_key] = {
    'process': None,              # subprocess.Popen object
    'status': 'starting',         # 'starting' | 'active' | 'stopped'
    'start_time': None,
    'camera_name': camera_name,
    'camera_serial': camera_serial,
    # ...
}
```

**Status Detection:**
- `is_stream_alive()` - Checks if FFmpeg process running (Line 1014-1028)
- `get_active_streams()` - Returns all active streams with status (Line 1030-1067)
- Thread-safe access via `self._streams_lock` (RLock)

### Existing Health Monitoring (UI)

**File:** `/home/elfege/0_NVR/static/js/streaming/health.js`

**Detection Mechanisms:**
1. **Frame Progression Tracking** - Monitors `currentTime` advancement
2. **Frame Signature Detection** - FNV-1a hash of frame data
3. **Black Screen Detection** - Luminance/deviation analysis
4. **Stale Detection** - Frozen frame detection (20s threshold)

**Recovery Strategy:**
- First 3 failures: Refresh/reconnect only
- Subsequent failures: Nuclear restart (stop + start)
- Exponential backoff: 5s, 10s, 20s, 40s, 60s max
- Max attempts: 10 (configurable)

### Gap Analysis

**What's Missing:**

1. **No Publisher State Broadcast**
   - MediaMTX knows path has no publisher, but NVR services don't
   - Each service independently discovers failures by attempting connection
   - No shared state = redundant failure detection

2. **No Coordinated Retry Logic**
   - UI health monitor retries independently
   - Motion detection retries independently
   - Recording service retries independently
   - No exponential backoff coordination

3. **No Camera Availability Tracker**
   - No centralized "camera is unreachable" state
   - Services can't distinguish between "publisher starting" vs "camera hardware dead"

4. **MediaMTX API Not Enabled**
   - MediaMTX has REST API for path/publisher queries (port 9997)
   - Currently not configured in `packager/mediamtx.yml`
   - No programmatic access to publisher state

---

## Proposed Architecture

### Design Principles

1. **Single Source of Truth**: One authoritative state tracker for camera availability
2. **Event-Driven Updates**: Push state changes instead of continuous polling
3. **Graceful Degradation**: Individual camera failures don't crash system
4. **Minimal Configuration**: Use existing infrastructure where possible
5. **Backwards Compatible**: Maintain current behavior as fallback

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         Camera Hardware                          │
│                   (Eufy, Reolink, UniFi, etc.)                  │
└────────────────────────┬────────────────────────────────────────┘
                         │ RTSP
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                    LL-HLS Publisher (FFmpeg)                     │
│                  streaming/stream_manager.py                     │
│                                                                   │
│  • Spawns FFmpeg per camera                                      │
│  • Publishes to MediaMTX via RTSP                                │
│  • Detects process death (poll())                                │
│  • Updates CameraStateTracker on state change                    │
└────────────────────────┬────────────────────────────────────────┘
                         │ RTSP Publish
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                    MediaMTX (nvr-packager)                       │
│                  packager/mediamtx.yml                           │
│                                                                   │
│  • Receives RTSP from publishers                                 │
│  • Re-broadcasts to consumers (RTSP/HLS/LL-HLS)                  │
│  • **NEW**: Exposes API on port 9997 (path state)                │
│  • **NEW**: Polled by CameraStateTracker                         │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│            **NEW**: CameraStateTracker (Singleton)               │
│                services/camera_state_tracker.py                  │
│                                                                   │
│  State Per Camera:                                                │
│  • availability: ONLINE | STARTING | OFFLINE | DEGRADED          │
│  • publisher_active: bool (MediaMTX has publisher)               │
│  • last_seen: timestamp                                          │
│  • failure_count: int                                            │
│  • next_retry: timestamp (exponential backoff)                   │
│                                                                   │
│  Methods:                                                         │
│  • get_camera_state(camera_id) -> CameraState                    │
│  • update_state(camera_id, state)                                │
│  • can_retry(camera_id) -> bool                                  │
│  • register_failure(camera_id)                                   │
│  • register_success(camera_id)                                   │
│                                                                   │
│  Background Tasks:                                                │
│  • Poll MediaMTX API every 5s for publisher state                │
│  • Update availability based on FFmpeg process state             │
│  • Trigger state change callbacks                                │
└────────────────────────┬────────────────────────────────────────┘
                         │ Query State
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Consumer Services                           │
│                                                                   │
│  Motion Detection:                                                │
│  • Check can_retry() before attempting connection                │
│  • Respect exponential backoff from tracker                      │
│  • Register failures/successes with tracker                      │
│                                                                   │
│  Recording Service:                                               │
│  • Check publisher_active before starting recording              │
│  • Skip recording if camera OFFLINE                              │
│  • Register failures/successes with tracker                      │
│                                                                   │
│  UI Health Monitor:                                               │
│  • Query state for accurate status display                       │
│  • Show "Camera Unavailable" if OFFLINE                          │
│  • Reduce polling frequency for OFFLINE cameras                  │
│                                                                   │
│  API Endpoints:                                                   │
│  • /api/camera/state/<camera_id> - Get state                     │
│  • /api/camera/states - Get all states                           │
└─────────────────────────────────────────────────────────────────┘
```

---

## Component Design

### 1. CameraStateTracker (New Singleton Service)

**File:** `/home/elfege/0_NVR/services/camera_state_tracker.py`

**State Model:**
```python
from enum import Enum
from dataclasses import dataclass
from datetime import datetime

class CameraAvailability(Enum):
    ONLINE = "online"          # Publisher active, stream healthy
    STARTING = "starting"      # Publisher starting, not yet confirmed
    OFFLINE = "offline"        # Camera unreachable or hardware failure
    DEGRADED = "degraded"      # Publisher active but stream quality issues

@dataclass
class CameraState:
    camera_id: str
    availability: CameraAvailability
    publisher_active: bool          # MediaMTX has active publisher
    ffmpeg_process_alive: bool      # FFmpeg process running
    last_seen: datetime             # Last successful health check
    failure_count: int              # Consecutive failures
    next_retry: datetime            # When to attempt next retry
    backoff_seconds: int            # Current backoff duration
    error_message: str | None       # Last error (for UI display)
```

**Core Methods:**
```python
class CameraStateTracker:
    def __init__(self):
        self._states: Dict[str, CameraState] = {}
        self._lock = threading.RLock()
        self._callbacks: Dict[str, List[Callable]] = {}
        self._mediamtx_api_url = "http://nvr-packager:9997"
        self._poll_thread: threading.Thread | None = None
        self._running = False

    def start(self):
        """Start background polling thread"""
        self._running = True
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def stop(self):
        """Stop background polling"""
        self._running = False
        if self._poll_thread:
            self._poll_thread.join(timeout=5)

    def get_camera_state(self, camera_id: str) -> CameraState:
        """Get current state for camera"""
        with self._lock:
            return self._states.get(camera_id, self._create_default_state(camera_id))

    def can_retry(self, camera_id: str) -> bool:
        """Check if service can attempt connection to camera"""
        state = self.get_camera_state(camera_id)

        # Always allow if ONLINE or STARTING
        if state.availability in (CameraAvailability.ONLINE, CameraAvailability.STARTING):
            return True

        # For OFFLINE/DEGRADED, check exponential backoff timer
        now = datetime.now()
        return now >= state.next_retry

    def register_failure(self, camera_id: str, error: str):
        """Register connection failure, update backoff"""
        with self._lock:
            state = self._states.get(camera_id)
            if not state:
                state = self._create_default_state(camera_id)
                self._states[camera_id] = state

            state.failure_count += 1
            state.error_message = error

            # Exponential backoff: 5s, 10s, 20s, 40s, 80s, 120s max
            state.backoff_seconds = min(120, 5 * (2 ** (state.failure_count - 1)))
            state.next_retry = datetime.now() + timedelta(seconds=state.backoff_seconds)

            # Update availability
            if state.failure_count >= 3:
                state.availability = CameraAvailability.OFFLINE
            elif state.failure_count >= 1:
                state.availability = CameraAvailability.DEGRADED

            self._trigger_callbacks(camera_id, state)

    def register_success(self, camera_id: str):
        """Register successful connection, reset backoff"""
        with self._lock:
            state = self._states.get(camera_id)
            if not state:
                state = self._create_default_state(camera_id)
                self._states[camera_id] = state

            state.failure_count = 0
            state.backoff_seconds = 0
            state.next_retry = datetime.now()
            state.last_seen = datetime.now()
            state.error_message = None
            state.availability = CameraAvailability.ONLINE

            self._trigger_callbacks(camera_id, state)

    def update_publisher_state(self, camera_id: str, active: bool):
        """Update publisher active state from MediaMTX API or stream manager"""
        with self._lock:
            state = self._states.get(camera_id)
            if not state:
                state = self._create_default_state(camera_id)
                self._states[camera_id] = state

            state.publisher_active = active

            # If publisher just became active, mark as STARTING (not yet verified healthy)
            if active and state.availability == CameraAvailability.OFFLINE:
                state.availability = CameraAvailability.STARTING

            self._trigger_callbacks(camera_id, state)

    def _poll_loop(self):
        """Background thread: poll MediaMTX API for publisher states"""
        while self._running:
            try:
                self._poll_mediamtx_api()
            except Exception as e:
                logger.error(f"MediaMTX API poll failed: {e}")

            time.sleep(5)  # Poll every 5 seconds

    def _poll_mediamtx_api(self):
        """Query MediaMTX API for all path states"""
        try:
            response = requests.get(f"{self._mediamtx_api_url}/v3/paths/list", timeout=3)
            if response.status_code == 200:
                data = response.json()

                # Update publisher state for each path
                for path_info in data.get('items', []):
                    camera_id = path_info.get('name', '')

                    # Skip _main paths (only track base camera ID)
                    if camera_id.endswith('_main'):
                        continue

                    # Check if path has active publisher
                    has_publisher = path_info.get('sourceReady', False)
                    self.update_publisher_state(camera_id, has_publisher)

        except requests.exceptions.RequestException as e:
            logger.warning(f"MediaMTX API unreachable: {e}")

    def register_callback(self, camera_id: str, callback: Callable[[CameraState], None]):
        """Register callback for state changes"""
        with self._lock:
            if camera_id not in self._callbacks:
                self._callbacks[camera_id] = []
            self._callbacks[camera_id].append(callback)

    def _trigger_callbacks(self, camera_id: str, state: CameraState):
        """Trigger all registered callbacks for camera"""
        callbacks = self._callbacks.get(camera_id, [])
        for callback in callbacks:
            try:
                callback(state)
            except Exception as e:
                logger.error(f"Callback error for {camera_id}: {e}")

    def _create_default_state(self, camera_id: str) -> CameraState:
        """Create default state for new camera"""
        return CameraState(
            camera_id=camera_id,
            availability=CameraAvailability.STARTING,
            publisher_active=False,
            ffmpeg_process_alive=False,
            last_seen=datetime.now(),
            failure_count=0,
            next_retry=datetime.now(),
            backoff_seconds=0,
            error_message=None
        )
```

---

### 2. StreamManager Integration

**File:** `/home/elfege/0_NVR/streaming/stream_manager.py`

**Changes Required:**

```python
# Add import
from services.camera_state_tracker import CameraStateTracker, CameraAvailability

class StreamManager:
    def __init__(self, ...):
        # ... existing init ...
        self.state_tracker = CameraStateTracker()
        self.state_tracker.start()

    def start_stream(self, camera_serial, ...):
        # Before starting, check if we should retry
        if not self.state_tracker.can_retry(camera_serial):
            state = self.state_tracker.get_camera_state(camera_serial)
            logger.info(f"Skipping stream start for {camera_serial}, "
                       f"next retry in {state.backoff_seconds}s")
            return None

        # Mark as STARTING
        self.state_tracker.update_publisher_state(camera_serial, active=False)
        self.state_tracker.get_camera_state(camera_serial).availability = CameraAvailability.STARTING

        # ... existing start_stream logic ...

        # After successful FFmpeg spawn
        self.state_tracker.register_success(camera_serial)
        self.state_tracker.update_publisher_state(camera_serial, active=True)

    def _monitor_stream(self, camera_serial):
        """Existing background monitor (if enabled)"""
        # ... existing monitoring ...

        # On process death
        if process.poll() is not None:
            self.state_tracker.register_failure(camera_serial, "FFmpeg process died")
            self.state_tracker.update_publisher_state(camera_serial, active=False)

    def is_stream_alive(self, camera_serial):
        # Update state tracker with FFmpeg process state
        alive = super().is_stream_alive(camera_serial)  # existing logic

        state = self.state_tracker.get_camera_state(camera_serial)
        state.ffmpeg_process_alive = alive

        if not alive and state.publisher_active:
            # FFmpeg died but MediaMTX still thinks publisher active
            self.state_tracker.update_publisher_state(camera_serial, active=False)

        return alive
```

---

### 3. Motion Detection Service Integration

**File:** `/home/elfege/0_NVR/services/motion/ffmpeg_motion_detector.py`

**Changes Required:**

```python
from services.camera_state_tracker import CameraStateTracker

class FFmpegMotionDetector:
    def __init__(self, ...):
        # ... existing init ...
        self.state_tracker = CameraStateTracker()  # Singleton, shared instance

    def start_detector(self, camera_id, sensitivity):
        # Check if camera available
        if not self.state_tracker.can_retry(camera_id):
            state = self.state_tracker.get_camera_state(camera_id)
            logger.info(f"Skipping motion detection for {camera_id}, "
                       f"camera {state.availability.value}, retry in {state.backoff_seconds}s")
            return False

        # Check if publisher active
        state = self.state_tracker.get_camera_state(camera_id)
        if not state.publisher_active:
            logger.info(f"Skipping motion detection for {camera_id}, no active publisher")
            return False

        # ... existing start_detector logic ...

        # On successful start
        self.state_tracker.register_success(camera_id)

    def _monitor_detector(self, camera_id):
        """Background monitor for detector process"""
        # ... existing monitoring ...

        # On connection failure
        if "Connection refused" in error_output:
            self.state_tracker.register_failure(camera_id, "Motion detector connection refused")
```

---

### 4. Recording Service Integration

**File:** `/home/elfege/0_NVR/services/recording/recording_service.py`

**Changes Required:**

```python
from services.camera_state_tracker import CameraStateTracker, CameraAvailability

class RecordingService:
    def __init__(self, ...):
        # ... existing init ...
        self.state_tracker = CameraStateTracker()

    def start_motion_recording(self, camera_id, event_id):
        # Check camera availability
        state = self.state_tracker.get_camera_state(camera_id)

        if state.availability == CameraAvailability.OFFLINE:
            logger.warning(f"Skipping recording for {camera_id}, camera offline")
            return None

        if not state.publisher_active:
            logger.warning(f"Skipping recording for {camera_id}, no active publisher")
            return None

        if not self.state_tracker.can_retry(camera_id):
            logger.info(f"Skipping recording for {camera_id}, "
                       f"backoff active, retry in {state.backoff_seconds}s")
            return None

        # ... existing recording logic ...

        # On successful recording start
        self.state_tracker.register_success(camera_id)

        # On recording failure
        # self.state_tracker.register_failure(camera_id, "Recording failed: ...")
```

---

### 5. API Endpoints

**File:** `/home/elfege/0_NVR/app.py`

**New Endpoints:**

```python
from services.camera_state_tracker import camera_state_tracker  # Global singleton

@app.route('/api/camera/state/<camera_id>', methods=['GET'])
def get_camera_state(camera_id):
    """Get state for specific camera"""
    state = camera_state_tracker.get_camera_state(camera_id)

    return jsonify({
        'success': True,
        'camera_id': camera_id,
        'state': {
            'availability': state.availability.value,
            'publisher_active': state.publisher_active,
            'ffmpeg_process_alive': state.ffmpeg_process_alive,
            'last_seen': state.last_seen.isoformat(),
            'failure_count': state.failure_count,
            'next_retry': state.next_retry.isoformat(),
            'backoff_seconds': state.backoff_seconds,
            'error_message': state.error_message
        }
    })

@app.route('/api/camera/states', methods=['GET'])
def get_all_camera_states():
    """Get states for all cameras"""
    from config.camera_config import camera_config

    all_states = {}
    for camera_id in camera_config.get_all_camera_ids():
        state = camera_state_tracker.get_camera_state(camera_id)
        all_states[camera_id] = {
            'availability': state.availability.value,
            'publisher_active': state.publisher_active,
            'last_seen': state.last_seen.isoformat(),
            'error_message': state.error_message
        }

    return jsonify({
        'success': True,
        'states': all_states
    })
```

---

### 6. UI Integration

**File:** `/home/elfege/0_NVR/static/js/streaming/stream.js`

**Changes Required:**

```javascript
// New method: query camera state from API
async queryCameraState(cameraSerial) {
    try {
        const response = await fetch(`/api/camera/state/${cameraSerial}`);
        const data = await response.json();

        if (data.success) {
            return data.state;
        }
    } catch (error) {
        console.error(`Failed to query camera state: ${error}`);
    }

    return null;
}

// Modified: handle camera state in stream start
async startStream(cameraSerial, $streamItem) {
    // Check camera state before attempting start
    const state = await this.queryCameraState(cameraSerial);

    if (state && state.availability === 'offline') {
        this.setStreamStatus($streamItem, 'error',
            `Camera Unavailable (retry in ${state.backoff_seconds}s)`);

        // Schedule retry based on backoff timer
        setTimeout(() => {
            this.startStream(cameraSerial, $streamItem);
        }, state.backoff_seconds * 1000);

        return;
    }

    if (state && !state.publisher_active) {
        this.setStreamStatus($streamItem, 'loading',
            'Publisher Starting...');
    }

    // ... existing start_stream logic ...
}

// New method: poll camera states for UI updates
startCameraStatePolling() {
    setInterval(async () => {
        const response = await fetch('/api/camera/states');
        const data = await response.json();

        if (data.success) {
            for (const [cameraId, state] of Object.entries(data.states)) {
                const $streamItem = $(`.stream-item[data-camera="${cameraId}"]`);

                // Update UI based on state
                if (state.availability === 'offline') {
                    this.setStreamStatus($streamItem, 'error',
                        `Camera Unavailable${state.error_message ? ': ' + state.error_message : ''}`);
                } else if (!state.publisher_active) {
                    this.setStreamStatus($streamItem, 'loading', 'Publisher Starting...');
                }
            }
        }
    }, 10000);  // Poll every 10 seconds
}
```

---

### 7. MediaMTX Configuration

**File:** `/home/elfege/0_NVR/packager/mediamtx.yml`

**Changes Required:**

```yaml
# === API Configuration ===
api: yes
apiAddress: :9997  # Internal port for NVR container

# ... rest of existing config ...
```

**Docker Compose Change:**

**File:** `/home/elfege/0_NVR/docker-compose.yml`

```yaml
services:
  nvr-packager:
    # ... existing config ...
    ports:
      - "8888:8888"   # LL-HLS HTTP
      - "8554:8554"   # RTSP
      - "1935:1935"   # RTMP
      # No need to expose 9997 externally - only internal Docker network
```

---

## Implementation Plan

### Phase 1: Foundation (1-2 hours)

1. **Enable MediaMTX API**
   - Edit `packager/mediamtx.yml`: add `api: yes` and `apiAddress: :9997`
   - Test API accessibility: `curl http://nvr-packager:9997/v3/paths/list`

2. **Create CameraStateTracker**
   - New file: `services/camera_state_tracker.py`
   - Implement core state model (CameraState, CameraAvailability enum)
   - Implement singleton pattern with thread-safe access
   - Add basic get/update methods (no polling yet)

3. **Unit Tests**
   - Test state transitions
   - Test exponential backoff calculation
   - Test thread safety (concurrent access)

### Phase 2: StreamManager Integration (2-3 hours)

1. **Integrate StateTracker into StreamManager**
   - Add tracker initialization in `__init__`
   - Update `start_stream()` to check `can_retry()`
   - Update `is_stream_alive()` to update tracker state
   - Register failures on FFmpeg process death

2. **Add MediaMTX API Polling**
   - Implement `_poll_mediamtx_api()` in CameraStateTracker
   - Start background polling thread
   - Update `publisher_active` state from API responses

3. **Testing**
   - Manually kill FFmpeg process, verify state updates
   - Verify exponential backoff prevents immediate retry
   - Check MediaMTX API polling works correctly

### Phase 3: Service Integration (2-3 hours)

1. **Motion Detection Services**
   - Update `ffmpeg_motion_detector.py` to check `can_retry()`
   - Update `reolink_motion_service.py` to check publisher state
   - Register failures on connection errors

2. **Recording Service**
   - Update `recording_service.py` to check publisher state
   - Skip recording attempts for OFFLINE cameras
   - Register failures on recording start errors

3. **Testing**
   - Disable a camera, verify motion detection respects backoff
   - Verify recording service skips offline cameras
   - Check log spam reduction

### Phase 4: API and UI (2-3 hours)

1. **Add API Endpoints**
   - `/api/camera/state/<camera_id>` - Single camera state
   - `/api/camera/states` - All camera states
   - Test endpoints return correct JSON

2. **UI Integration**
   - Add `queryCameraState()` method in stream.js
   - Update stream start logic to check state first
   - Add camera state polling (10s interval)
   - Display accurate "Camera Unavailable" status

3. **Testing**
   - Verify UI shows accurate status for offline cameras
   - Test backoff timer display
   - Verify state polling updates UI in real-time

### Phase 5: Validation and Optimization (1-2 hours)

1. **End-to-End Testing**
   - Restart system, verify all cameras tracked correctly
   - Manually disconnect a camera, verify:
     - State transitions to OFFLINE after 3 failures
     - Backoff timer prevents retry spam
     - UI shows "Camera Unavailable"
     - MediaMTX logs show reduced "torn down" events
   - Reconnect camera, verify:
     - Publisher auto-starts when camera comes back
     - State transitions to ONLINE
     - UI updates to "Live"

2. **Performance Validation**
   - Check MediaMTX API polling overhead (should be negligible)
   - Verify thread-safe access doesn't block
   - Monitor log reduction (expect 90%+ reduction in "torn down" spam)

3. **Documentation**
   - Update `nvr_engineering_architecture.html`
   - Document new API endpoints
   - Add troubleshooting guide

---

## Expected Outcomes

### Before Implementation

**Problem Symptoms:**
- 10+ "torn down" events per 30 seconds per dead camera
- UI shows "Starting..." indefinitely
- Motion detection continuously attempts dead cameras
- Recording service hammers MediaMTX paths
- No coordinated retry logic

### After Implementation

**Expected Improvements:**

1. **Log Spam Reduction: 90%+**
   - Services respect backoff timers
   - No redundant connection attempts
   - MediaMTX logs clean and actionable

2. **Accurate UI Status**
   - "Camera Unavailable" for OFFLINE cameras
   - Backoff timer display: "Retry in 40s"
   - No false "Starting..." states

3. **Coordinated Retry Logic**
   - Exponential backoff: 5s → 10s → 20s → 40s → 80s → 120s
   - All services respect same backoff timer
   - Automatic recovery when camera comes back online

4. **Resource Efficiency**
   - No wasted FFmpeg processes for dead cameras
   - Reduced MediaMTX connection churn
   - Lower CPU/memory footprint

5. **Better Observability**
   - API endpoints for camera state queries
   - Centralized failure tracking
   - Clear error messages propagated to UI

---

## Risks and Mitigations

### Risk 1: MediaMTX API Overhead

**Risk:** Polling MediaMTX API every 5s for 10 cameras adds latency/load.

**Mitigation:**
- API is lightweight (JSON over HTTP)
- MediaMTX already tracks publisher state internally
- 5s poll interval is conservative (can increase if needed)
- Caching: Only update state on changes

### Risk 2: Race Conditions

**Risk:** FFmpeg starts, but StateTracker hasn't polled MediaMTX yet.

**Mitigation:**
- StreamManager updates tracker immediately on FFmpeg spawn
- MediaMTX API polling is secondary verification
- Use STARTING state as transitional buffer

### Risk 3: Singleton State Corruption

**Risk:** Concurrent access to CameraStateTracker corrupts state.

**Mitigation:**
- Use `threading.RLock()` for all state access
- Atomic state updates (deepcopy or immutable dataclasses)
- Unit tests for thread safety

### Risk 4: Backoff Too Aggressive

**Risk:** Camera briefly offline causes long backoff, slow recovery.

**Mitigation:**
- Start backoff low (5s) for transient issues
- Max backoff capped at 120s (2 minutes)
- Manual "Retry Now" button in UI (future enhancement)

---

## Future Enhancements

1. **WebSocket State Broadcast**
   - Push state changes to UI instead of polling
   - Real-time updates (<1s latency)

2. **Camera Reachability Probes**
   - Ping camera IP before attempting RTSP
   - Faster failure detection
   - Distinguish network vs. hardware failures

3. **Per-Camera Backoff Tuning**
   - Cameras with known flakiness get longer backoff
   - Stable cameras get aggressive retry
   - ML-based backoff optimization (learn failure patterns)

4. **Manual Override Controls**
   - UI button: "Retry Now" (bypass backoff)
   - API endpoint: `POST /api/camera/retry/<camera_id>`
   - Admin can force camera state reset

5. **Persistent State Storage**
   - Save state to Redis or SQLite
   - Survive container restarts
   - Historical failure tracking

---

## Conclusion

This architecture solves the core coordination problem by introducing a **Single Source of Truth** for camera availability. All services (motion detection, recording, UI) query the `CameraStateTracker` before attempting operations, eliminating redundant failures and log spam.

The design is **incremental** - Phase 1 can be deployed immediately for basic state tracking, with subsequent phases adding API polling, service integration, and UI enhancements.

**Key Success Metric:** 90%+ reduction in MediaMTX "torn down" log events after implementation.

---

**Next Steps:**
1. User review and approval of architecture
2. Begin Phase 1 implementation (enable MediaMTX API, create StateTracker)
3. Iterative testing and validation
4. Document findings in README_handoff.md
