"""
Camera State Tracker Service

Provides centralized tracking of camera availability and publisher state across
the NVR system. Coordinates retry logic between all services (motion detection,
recording, UI health monitoring) to prevent redundant connection attempts when
cameras are offline or publishers have failed.

Key Features:
- Polls MediaMTX API every 5 seconds for publisher state
- Tracks camera availability: ONLINE | STARTING | OFFLINE | DEGRADED
- Implements exponential backoff for failed cameras (5s → 120s max)
- Thread-safe state management for concurrent service access
- State change callbacks for reactive service updates

Architecture:
    Camera Hardware → LL-HLS Publisher (FFmpeg) → MediaMTX
                                                      ↓
                                            CameraStateTracker (this service)
                                                      ↓
                        Services query: can_retry(), get_camera_state()
                        (Motion Detection, Recording, UI)

Author: NVR System
Date: January 3, 2026
"""

import logging
import threading
import time
import requests
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Callable, Optional

logger = logging.getLogger(__name__)


class CameraAvailability(Enum):
    """
    Camera availability states for coordinated service behavior.

    ONLINE: Publisher active, stream confirmed healthy
    STARTING: Publisher initializing, not yet verified
    OFFLINE: Camera unreachable or hardware failure (3+ consecutive failures)
    DEGRADED: Publisher active but experiencing intermittent issues (1-2 failures)
    """
    ONLINE = "online"
    STARTING = "starting"
    OFFLINE = "offline"
    DEGRADED = "degraded"


@dataclass
class CameraState:
    """
    Complete state information for a camera.

    Attributes:
        camera_id: Camera serial number (e.g., "T8416P0023352DA9")
        availability: Current availability state (ONLINE/STARTING/OFFLINE/DEGRADED)
        publisher_active: Whether MediaMTX reports an active publisher for this path
        ffmpeg_process_alive: Whether the FFmpeg publisher process is running
        last_seen: Timestamp of last successful health check
        failure_count: Consecutive connection/health check failures
        next_retry: When services should next attempt connection (exponential backoff)
        backoff_seconds: Current backoff duration in seconds
        error_message: Last error encountered (for UI display)
        starting_since: Timestamp when camera entered STARTING state (for timeout detection)
    """
    camera_id: str
    availability: CameraAvailability = CameraAvailability.STARTING
    publisher_active: bool = False
    ffmpeg_process_alive: bool = False
    last_seen: datetime = field(default_factory=datetime.now)
    failure_count: int = 0
    next_retry: datetime = field(default_factory=datetime.now)
    backoff_seconds: int = 0
    error_message: Optional[str] = None
    starting_since: datetime = field(default_factory=datetime.now)


class CameraStateTracker:
    """
    Singleton service for tracking camera availability and publisher state.

    This service acts as the single source of truth for camera availability,
    preventing redundant connection attempts when cameras are offline and
    coordinating exponential backoff across all NVR services.

    Usage:
        tracker = CameraStateTracker()
        tracker.start()

        # Before attempting connection
        if tracker.can_retry(camera_id):
            # Attempt connection
            try:
                connect_to_camera(camera_id)
                tracker.register_success(camera_id)
            except Exception as e:
                tracker.register_failure(camera_id, str(e))

        # Query current state
        state = tracker.get_camera_state(camera_id)
        if state.availability == CameraAvailability.OFFLINE:
            logger.warning(f"Camera {camera_id} offline, skipping operation")

    Thread Safety:
        All public methods are thread-safe via RLock. Safe for concurrent
        access from multiple services (motion detection, recording, UI).
    """

    def __init__(self, mediamtx_api_url: str = "http://nvr-packager:9997"):
        """
        Initialize camera state tracker.

        Args:
            mediamtx_api_url: Base URL for MediaMTX API (default: internal Docker network)
        """
        self._states: Dict[str, CameraState] = {}
        self._lock = threading.RLock()  # Re-entrant lock for nested calls
        self._callbacks: Dict[str, List[Callable[[CameraState], None]]] = {}
        self._mediamtx_api_url = mediamtx_api_url
        self._poll_thread: Optional[threading.Thread] = None
        self._running = False

        logger.info("CameraStateTracker initialized")


    def start(self):
        """
        Start background polling thread for MediaMTX API.

        Launches daemon thread that polls MediaMTX every 5 seconds to update
        publisher state for all cameras. Thread automatically stops when
        main application exits.
        """
        if self._running:
            logger.warning("CameraStateTracker already running")
            return

        self._running = True
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            name="CameraStateTracker-Poller",
            daemon=True
        )
        self._poll_thread.start()
        logger.info("CameraStateTracker background polling started")


    def stop(self):
        """
        Stop background polling thread gracefully.

        Signals polling thread to stop and waits up to 5 seconds for
        graceful shutdown. Called automatically on application shutdown.
        """
        if not self._running:
            return

        logger.info("Stopping CameraStateTracker polling thread...")
        self._running = False

        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=5)
            if self._poll_thread.is_alive():
                logger.warning("Polling thread did not stop within timeout")
            else:
                logger.info("Polling thread stopped successfully")


    def get_camera_state(self, camera_id: str) -> CameraState:
        """
        Get current state for camera (thread-safe).

        Args:
            camera_id: Camera serial number

        Returns:
            CameraState object (creates default STARTING state if not exists)
        """
        with self._lock:
            if camera_id not in self._states:
                self._states[camera_id] = self._create_default_state(camera_id)
            return self._states[camera_id]


    def can_retry(self, camera_id: str) -> bool:
        """
        Check if services can attempt connection to camera.

        Implements coordinated exponential backoff across all services.
        Services should check this before attempting any camera connection
        to prevent redundant failure attempts.

        Args:
            camera_id: Camera serial number

        Returns:
            True if connection attempt allowed, False if in backoff period

        Backoff Logic:
            - ONLINE/STARTING: Always allow (state 0-1 failures)
            - DEGRADED: Check backoff timer (state 1-2 failures)
            - OFFLINE: Check backoff timer (state 3+ failures)
        """
        state = self.get_camera_state(camera_id)

        # Always allow if ONLINE or STARTING (no failures or just 1)
        if state.availability in (CameraAvailability.ONLINE, CameraAvailability.STARTING):
            return True

        # For DEGRADED/OFFLINE, check exponential backoff timer
        now = datetime.now()
        can_retry = now >= state.next_retry

        if not can_retry:
            remaining = (state.next_retry - now).total_seconds()
            logger.debug(
                f"Camera {camera_id} in backoff, retry in {remaining:.1f}s "
                f"(state: {state.availability.value}, failures: {state.failure_count})"
            )

        return can_retry


    def register_failure(self, camera_id: str, error: str):
        """
        Register connection failure and update backoff timer.

        Increments failure count and applies exponential backoff:
        - Failure 1: 5 seconds
        - Failure 2: 10 seconds
        - Failure 3: 20 seconds (transition to OFFLINE)
        - Failure 4: 40 seconds
        - Failure 5: 80 seconds
        - Failure 6+: 120 seconds (max)

        Args:
            camera_id: Camera serial number
            error: Error message from connection attempt
        """
        with self._lock:
            state = self._states.get(camera_id)
            if not state:
                state = self._create_default_state(camera_id)
                self._states[camera_id] = state

            # Increment failure count
            state.failure_count += 1
            state.error_message = error

            # Calculate exponential backoff: min(120s, 5 * 2^(failures-1))
            state.backoff_seconds = min(120, 5 * (2 ** (state.failure_count - 1)))
            state.next_retry = datetime.now() + timedelta(seconds=state.backoff_seconds)

            # Update availability based on failure count
            if state.failure_count >= 3:
                # 3+ failures = OFFLINE
                state.availability = CameraAvailability.OFFLINE
                logger.warning(
                    f"Camera {camera_id} marked OFFLINE after {state.failure_count} failures, "
                    f"next retry in {state.backoff_seconds}s. Error: {error}"
                )
            elif state.failure_count >= 1:
                # 1-2 failures = DEGRADED
                state.availability = CameraAvailability.DEGRADED
                logger.info(
                    f"Camera {camera_id} marked DEGRADED after {state.failure_count} failure(s), "
                    f"next retry in {state.backoff_seconds}s"
                )

            # Trigger state change callbacks
            self._trigger_callbacks(camera_id, state)


    def register_success(self, camera_id: str):
        """
        Register successful connection and reset failure counters.

        Resets failure count, backoff timer, and marks camera as ONLINE.
        Should be called after any successful camera operation (stream start,
        motion detection connection, recording start, etc.).

        Args:
            camera_id: Camera serial number
        """
        with self._lock:
            state = self._states.get(camera_id)
            if not state:
                state = self._create_default_state(camera_id)
                self._states[camera_id] = state

            # Reset all failure tracking
            old_availability = state.availability
            state.failure_count = 0
            state.backoff_seconds = 0
            state.next_retry = datetime.now()
            state.last_seen = datetime.now()
            state.error_message = None
            state.availability = CameraAvailability.ONLINE

            if old_availability != CameraAvailability.ONLINE:
                logger.info(
                    f"Camera {camera_id} recovered: {old_availability.value} → ONLINE"
                )

            # Trigger state change callbacks
            self._trigger_callbacks(camera_id, state)


    def update_mjpeg_capture_state(self, camera_id: str, active: bool, error: Optional[str] = None):
        """
        Update MJPEG capture state from MJPEG capture services.

        Called by:
        - ReolinkMJPEGCaptureService when capture starts/stops/fails
        - AmcrestMJPEGCaptureService when capture starts/stops/fails
        - UnifiMJPEGCaptureService when capture starts/stops/fails

        MJPEG cameras don't use MediaMTX - they stream directly from hardware.
        This method tracks their state separately from MediaMTX-based LL-HLS cameras.

        Args:
            camera_id: Camera serial number
            active: True if MJPEG capture thread is running and receiving frames
            error: Optional error message if capture failed
        """
        with self._lock:
            state = self._states.get(camera_id)
            if not state:
                state = self._create_default_state(camera_id)
                self._states[camera_id] = state

            old_availability = state.availability

            if active:
                # Capture is running and receiving frames
                state.publisher_active = True  # Repurpose for MJPEG "capture active"
                state.availability = CameraAvailability.ONLINE
                state.failure_count = 0
                state.last_seen = datetime.now()
                state.error_message = None
                if old_availability != CameraAvailability.ONLINE:
                    logger.info(f"MJPEG camera {camera_id} capture active, state: {old_availability.value} → ONLINE")
            else:
                # Capture stopped or failed
                state.publisher_active = False
                if error:
                    state.error_message = error
                    state.failure_count += 1
                    state.backoff_seconds = min(120, 5 * (2 ** (state.failure_count - 1)))
                    state.next_retry = datetime.now() + timedelta(seconds=state.backoff_seconds)

                    if state.failure_count >= 3:
                        state.availability = CameraAvailability.OFFLINE
                        logger.warning(f"MJPEG camera {camera_id} marked OFFLINE after {state.failure_count} failures: {error}")
                    else:
                        state.availability = CameraAvailability.DEGRADED
                        logger.info(f"MJPEG camera {camera_id} marked DEGRADED after {state.failure_count} failure(s)")
                else:
                    # Graceful stop (no error)
                    state.availability = CameraAvailability.STARTING
                    state.starting_since = datetime.now()  # Reset starting timer
                    logger.debug(f"MJPEG camera {camera_id} capture stopped (no error)")

            # Trigger callbacks if state changed
            if old_availability != state.availability:
                self._trigger_callbacks(camera_id, state)

    def update_publisher_state(self, camera_id: str, active: bool):
        """
        Update publisher active state from MediaMTX API or stream manager.

        Called by:
        - Background MediaMTX API polling (_poll_mediamtx_api)
        - StreamManager when FFmpeg publisher starts/stops

        Args:
            camera_id: Camera serial number
            active: True if MediaMTX reports active publisher
        """
        with self._lock:
            state = self._states.get(camera_id)
            if not state:
                state = self._create_default_state(camera_id)
                self._states[camera_id] = state

            old_active = state.publisher_active
            state.publisher_active = active

            # If publisher becomes active and camera is STARTING, mark as ONLINE
            # This allows automatic transition when MediaMTX reports publisher as ready
            if active and state.availability == CameraAvailability.STARTING:
                state.availability = CameraAvailability.ONLINE
                state.failure_count = 0
                state.last_seen = datetime.now()
                logger.info(f"Camera {camera_id} publisher ready, state: STARTING → ONLINE")

            # If publisher just became active and camera was OFFLINE, mark as STARTING
            # (not yet verified healthy by actual stream/connection)
            if active and not old_active and state.availability == CameraAvailability.OFFLINE:
                state.availability = CameraAvailability.STARTING
                state.starting_since = datetime.now()  # Reset starting timer
                logger.info(f"Camera {camera_id} publisher activated, state: OFFLINE → STARTING")

            # If publisher just died and camera was ONLINE, mark as DEGRADED
            if not active and old_active and state.availability == CameraAvailability.ONLINE:
                state.availability = CameraAvailability.DEGRADED
                state.failure_count = 1
                logger.warning(f"Camera {camera_id} publisher died, state: ONLINE → DEGRADED")

            # Trigger callbacks if state changed
            if old_active != active:
                self._trigger_callbacks(camera_id, state)


    def register_callback(self, camera_id: str, callback: Callable[[CameraState], None]):
        """
        Register callback for camera state changes.

        Callbacks are invoked whenever camera state changes (availability,
        publisher state, failure count, etc.). Useful for reactive services
        that need to respond to state transitions.

        Args:
            camera_id: Camera serial number to monitor
            callback: Function accepting CameraState argument

        Example:
            def on_camera_offline(state: CameraState):
                if state.availability == CameraAvailability.OFFLINE:
                    logger.error(f"Camera {state.camera_id} went offline!")

            tracker.register_callback("T8416P0023352DA9", on_camera_offline)
        """
        with self._lock:
            if camera_id not in self._callbacks:
                self._callbacks[camera_id] = []
            self._callbacks[camera_id].append(callback)
            logger.debug(f"Registered state change callback for {camera_id}")


    def _poll_loop(self):
        """
        Background thread: continuously poll MediaMTX API for publisher states.

        Runs every 5 seconds, queries all camera paths from MediaMTX API,
        and updates publisher_active state based on API response.

        Handles transient API failures gracefully (logs warning, continues polling).
        """
        logger.info("MediaMTX API polling loop started (interval: 5s)")

        while self._running:
            try:
                self._poll_mediamtx_api()
            except Exception as e:
                logger.error(f"MediaMTX API poll failed: {e}", exc_info=True)

            # Sleep 5 seconds before next poll
            time.sleep(5)

        logger.info("MediaMTX API polling loop stopped")


    def _poll_mediamtx_api(self):
        """
        Query MediaMTX API for all path states and update publisher flags.

        Endpoint: GET /v3/paths/list

        Response format:
        {
            "itemCount": 22,
            "items": [
                {
                    "name": "T8416P0023352DA9",
                    "ready": true,  // ← publisher active indicator
                    "source": {...},
                    "tracks": [...]
                },
                ...
            ]
        }

        Updates:
            - publisher_active flag for each camera
            - Skips _main paths (only track base camera ID)
        """
        try:
            response = requests.get(
                f"{self._mediamtx_api_url}/v3/paths/list",
                auth=('nvr-api', ''),  # Username: nvr-api, Password: empty
                timeout=3
            )

            if response.status_code != 200:
                logger.warning(
                    f"MediaMTX API returned status {response.status_code}: {response.text}"
                )
                return

            data = response.json()
            paths = data.get('items', [])

            # Update publisher state for each path
            for path_info in paths:
                camera_id = path_info.get('name', '')

                # Skip _main paths (we only track base camera ID)
                if camera_id.endswith('_main'):
                    continue

                # Check if path has active publisher
                # "ready": true means publisher is active and streaming
                has_publisher = path_info.get('ready', False)

                # Update state
                self.update_publisher_state(camera_id, has_publisher)

            logger.debug(f"MediaMTX API poll: {len(paths)} paths checked")

            # Check for cameras stuck in STARTING state for too long (60+ seconds)
            # If a camera has been STARTING but publisher never became active,
            # transition to DEGRADED so StreamWatchdog picks it up for restart
            self._check_starting_timeouts()

        except requests.exceptions.RequestException as e:
            logger.warning(f"MediaMTX API unreachable: {e}")
        except Exception as e:
            logger.error(f"Error parsing MediaMTX API response: {e}", exc_info=True)


    def wait_for_publisher_ready(self, camera_id: str, timeout: float = 15.0) -> bool:
        """
        Block until MediaMTX reports publisher as ready for the given camera.

        Polls MediaMTX API directly (not waiting for background poll cycle)
        every 1 second until the path reports "ready: true" or timeout expires.

        This closes the race condition where FFmpeg is marked 'active' after
        a fixed sleep (3s) but MediaMTX hasn't accepted the publisher yet
        (takes 5-15s depending on camera connection speed).

        Args:
            camera_id: Camera serial number
            timeout: Maximum seconds to wait (default: 15)

        Returns:
            True if publisher became ready within timeout, False otherwise
        """
        start = time.time()
        poll_interval = 1.0

        while (time.time() - start) < timeout:
            try:
                response = requests.get(
                    f"{self._mediamtx_api_url}/v3/paths/list",
                    auth=('nvr-api', ''),
                    timeout=2
                )

                if response.status_code == 200:
                    data = response.json()
                    for path_info in data.get('items', []):
                        if path_info.get('name') == camera_id:
                            if path_info.get('ready', False):
                                elapsed = time.time() - start
                                logger.info(
                                    f"Camera {camera_id} publisher ready after {elapsed:.1f}s"
                                )
                                # Update internal state to match
                                self.update_publisher_state(camera_id, True)
                                return True
                            break  # Found path but not ready yet

            except requests.exceptions.RequestException as e:
                logger.debug(f"MediaMTX API check for {camera_id}: {e}")

            time.sleep(poll_interval)

        elapsed = time.time() - start
        logger.warning(
            f"Camera {camera_id} publisher not ready after {elapsed:.1f}s (timeout: {timeout}s)"
        )
        return False

    def _check_starting_timeouts(self):
        """
        Check for cameras stuck in STARTING state and transition to DEGRADED.

        If a camera has been in STARTING state for 20+ seconds without
        publisher_active becoming True, it's likely stuck (FFmpeg never started
        or died immediately). Transition to DEGRADED so StreamWatchdog can
        pick it up and attempt a restart.

        This fixes the issue where cameras get stuck showing "Starting..."
        forever because the watchdog skips cameras in STARTING state.
        """
        STARTING_TIMEOUT_SECONDS = 20

        with self._lock:
            now = datetime.now()

            for camera_id, state in self._states.items():
                if state.availability == CameraAvailability.STARTING:
                    elapsed = (now - state.starting_since).total_seconds()

                    # Only timeout if still no publisher after 60 seconds
                    if elapsed > STARTING_TIMEOUT_SECONDS and not state.publisher_active:
                        logger.warning(
                            f"Camera {camera_id} stuck in STARTING for {elapsed:.0f}s without publisher, "
                            f"transitioning to DEGRADED for watchdog pickup"
                        )
                        state.availability = CameraAvailability.DEGRADED
                        state.failure_count = 1
                        state.error_message = "Startup timeout - FFmpeg never published"
                        state.backoff_seconds = 5  # Short backoff for first retry
                        state.next_retry = now + timedelta(seconds=5)
                        self._trigger_callbacks(camera_id, state)


    def _trigger_callbacks(self, camera_id: str, state: CameraState):
        """
        Invoke all registered callbacks for camera state changes.

        Callbacks are executed in registration order. Exceptions in callbacks
        are caught and logged to prevent one failing callback from blocking others.

        Args:
            camera_id: Camera that changed state
            state: New camera state
        """
        callbacks = self._callbacks.get(camera_id, [])

        for callback in callbacks:
            try:
                callback(state)
            except Exception as e:
                logger.error(
                    f"Callback error for {camera_id}: {e}",
                    exc_info=True
                )


    def _create_default_state(self, camera_id: str) -> CameraState:
        """
        Create default STARTING state for new camera.

        Args:
            camera_id: Camera serial number

        Returns:
            New CameraState with STARTING availability
        """
        logger.debug(f"Creating default state for camera: {camera_id}")
        now = datetime.now()
        return CameraState(
            camera_id=camera_id,
            availability=CameraAvailability.STARTING,
            publisher_active=False,
            ffmpeg_process_alive=False,
            last_seen=now,
            failure_count=0,
            next_retry=now,
            backoff_seconds=0,
            error_message=None,
            starting_since=now
        )


# Global singleton instance
# Services should import and use this instance for coordinated state tracking
camera_state_tracker = CameraStateTracker()
