#!/usr/bin/env python3
"""
Stream Watchdog Service

Unified watchdog service for LL-HLS and MJPEG streams.
Uses CameraStateTracker as single source of truth for camera health.

Architecture:
    CameraStateTracker (polls MediaMTX, receives MJPEG state)
           |
           v
    StreamWatchdog (polls every 10 seconds)
           |
           +---> StreamManager.restart_stream() for LL-HLS
           +---> MJPEG services.restart_capture() for MJPEG

Features:
- Single daemon thread for all camera monitoring
- Respects CameraStateTracker.can_retry() for exponential backoff
- Reports restart success/failure back to CameraStateTracker
- Configurable via STREAM_WATCHDOG_ENABLED environment variable

Author: NVR System
Date: January 4, 2026
"""

import logging
import os
import threading
import time
from typing import TYPE_CHECKING, Optional, Dict, Any

logger = logging.getLogger(__name__)

# Type hints for lazy imports (avoid circular dependencies)
if TYPE_CHECKING:
    from streaming.stream_manager import StreamManager
    from services.camera_state_tracker import CameraStateTracker, CameraAvailability


class StreamWatchdog:
    """
    Unified watchdog service for monitoring and restarting unhealthy streams.

    Uses CameraStateTracker as the single source of truth for camera health.
    Polls every 10 seconds and triggers restarts when:
    - publisher_active == False (stream is down)
    - can_retry() == True (not in backoff period)

    Race Condition Prevention:
    - STARTUP_WARMUP: Wait before first check to let streams initialize
    - RESTART_COOLDOWN: Per-camera cooldown after restart to prevent rapid cycling
    - Uses CameraStateTracker's exponential backoff for persistent failures

    Usage:
        watchdog = StreamWatchdog(stream_manager, camera_state_tracker)
        watchdog.start()
        # ... later ...
        watchdog.stop()

    Thread Safety:
        The watchdog runs in its own daemon thread. All state checks go through
        CameraStateTracker which handles its own locking.
    """

    # Watchdog poll interval in seconds
    POLL_INTERVAL = 10

    # Startup warmup: wait this long after start() before first check
    # Gives streams time to initialize on container startup
    STARTUP_WARMUP_SECONDS = 60

    # Per-camera cooldown after restart attempt (prevents rapid restart cycling)
    RESTART_COOLDOWN_SECONDS = 10

    def __init__(
        self,
        stream_manager: "StreamManager",
        camera_state_tracker: "CameraStateTracker",
        mjpeg_services: Optional[Dict[str, Any]] = None
    ):
        """
        Initialize the stream watchdog.

        Args:
            stream_manager: StreamManager instance for LL-HLS stream restarts
            camera_state_tracker: CameraStateTracker instance for health checks
            mjpeg_services: Dict of MJPEG service instances keyed by type
                           (e.g., {'reolink': reolink_service, 'amcrest': amcrest_service, ...})
        """
        self._stream_manager = stream_manager
        self._state_tracker = camera_state_tracker
        self._mjpeg_services = mjpeg_services or {}

        # Thread control
        self._watch_thread: Optional[threading.Thread] = None
        self._running = False
        self._stop_event = threading.Event()

        # Track cameras and their stream types for restart routing
        # Will be populated from camera_repo on first poll
        self._camera_types: Dict[str, str] = {}  # camera_id -> 'LL_HLS' or 'MJPEG'

        # Per-camera cooldown tracking: camera_id -> timestamp when cooldown expires
        self._restart_cooldowns: Dict[str, float] = {}

        # Startup timestamp - used for warmup period
        self._start_time: Optional[float] = None

        # SocketIO instance for broadcasting stream restart events to frontend
        self._socketio = None

        logger.info("StreamWatchdog initialized")

    def set_socketio(self, socketio) -> None:
        """
        Set SocketIO instance for broadcasting stream restart events.

        Called by app.py after Flask-SocketIO initialization.
        Enables real-time notification to frontend when streams are restarted.

        Args:
            socketio: Flask-SocketIO instance
        """
        self._socketio = socketio
        logger.info("StreamWatchdog: SocketIO instance set for event broadcasting")

    def _broadcast_stream_restarted(self, camera_id: str) -> None:
        """
        Broadcast stream_restarted event to all connected frontend clients.

        Sent via /stream_events SocketIO namespace. Frontend subscribes to this
        event and triggers HLS refresh immediately, avoiding the 10-second
        polling delay that would otherwise leave HLS.js stuck on a stale session.

        Args:
            camera_id: Camera serial number that was restarted
        """
        if self._socketio:
            self._socketio.emit('stream_restarted', {
                'camera_id': camera_id,
                'timestamp': time.time()
            }, namespace='/stream_events')
            logger.info(f"[WATCHDOG] Broadcast stream_restarted for {camera_id}")

    def start(self) -> None:
        """
        Start the watchdog monitoring thread.

        Checks STREAM_WATCHDOG_ENABLED environment variable. If disabled,
        logs warning and returns without starting.
        """
        # Check if watchdog is enabled via environment
        enabled = os.getenv('STREAM_WATCHDOG_ENABLED', '0').lower() in ('1', 'true', 'yes')
        if not enabled:
            logger.warning("StreamWatchdog DISABLED via STREAM_WATCHDOG_ENABLED=0")
            return

        if self._running:
            logger.warning("StreamWatchdog already running")
            return

        self._running = True
        self._stop_event.clear()
        self._start_time = time.time()  # Record start time for warmup

        self._watch_thread = threading.Thread(
            target=self._watch_loop,
            name="StreamWatchdog",
            daemon=True
        )
        self._watch_thread.start()
        logger.info(
            f"StreamWatchdog started (poll: {self.POLL_INTERVAL}s, "
            f"warmup: {self.STARTUP_WARMUP_SECONDS}s, "
            f"cooldown: {self.RESTART_COOLDOWN_SECONDS}s)"
        )

    def stop(self) -> None:
        """
        Stop the watchdog monitoring thread gracefully.

        Signals the thread to stop and waits up to 15 seconds for graceful
        shutdown (accounts for POLL_INTERVAL + restart time).
        """
        if not self._running:
            return

        logger.info("Stopping StreamWatchdog...")
        self._running = False
        self._stop_event.set()

        if self._watch_thread and self._watch_thread.is_alive():
            # Wait longer than poll interval to allow current check to complete
            self._watch_thread.join(timeout=15)
            if self._watch_thread.is_alive():
                logger.warning("StreamWatchdog thread did not stop within timeout")
            else:
                logger.info("StreamWatchdog stopped successfully")

    def _watch_loop(self) -> None:
        """
        Main watchdog loop - polls CameraStateTracker every POLL_INTERVAL seconds.

        For each camera:
        1. Check if publisher_active == False (stream is down)
        2. Check if can_retry() == True (not in backoff period)
        3. If both conditions met, trigger restart based on stream type
        """
        logger.info(f"StreamWatchdog loop started (interval: {self.POLL_INTERVAL}s)")

        while self._running and not self._stop_event.is_set():
            try:
                self._check_and_restart_streams()
            except Exception as e:
                logger.error(f"StreamWatchdog check failed: {e}", exc_info=True)

            # Sleep in small increments to allow faster shutdown
            for _ in range(self.POLL_INTERVAL):
                if self._stop_event.is_set():
                    break
                time.sleep(1)

        logger.info("StreamWatchdog loop stopped")

    def _check_and_restart_streams(self) -> None:
        """
        Check all cameras and restart unhealthy streams.

        Race condition prevention:
        1. Startup warmup: Skip checks until STARTUP_WARMUP_SECONDS elapsed
        2. Per-camera cooldown: Skip if recently restarted
        3. Backoff: Respect CameraStateTracker.can_retry() exponential backoff

        Triggers restart for cameras where:
        - Warmup period has elapsed
        - Camera not in cooldown period
        - publisher_active == False
        - can_retry() == True (not in exponential backoff)
        """
        # Import here to avoid circular dependency
        from services.camera_state_tracker import CameraAvailability

        # STARTUP WARMUP: Don't check until warmup period has elapsed
        # This prevents restarts while streams are still initializing
        elapsed = time.time() - self._start_time
        if elapsed < self.STARTUP_WARMUP_SECONDS:
            remaining = self.STARTUP_WARMUP_SECONDS - elapsed
            logger.debug(f"StreamWatchdog in warmup period, {remaining:.0f}s remaining")
            return

        # Get all camera states from tracker
        all_camera_ids = self._get_all_camera_ids()
        now = time.time()

        for camera_id in all_camera_ids:
            if self._stop_event.is_set():
                break

            try:
                # PER-CAMERA COOLDOWN: Skip if recently restarted
                cooldown_expires = self._restart_cooldowns.get(camera_id, 0)
                if now < cooldown_expires:
                    remaining = cooldown_expires - now
                    logger.debug(f"Camera {camera_id} in restart cooldown, {remaining:.0f}s remaining")
                    continue

                state = self._state_tracker.get_camera_state(camera_id)

                # Skip cameras that are healthy (publisher active)
                if state.publisher_active:
                    # Clear any old cooldown since camera is healthy
                    self._restart_cooldowns.pop(camera_id, None)
                    continue

                # Skip cameras still in STARTING state (give them time to initialize)
                if state.availability == CameraAvailability.STARTING:
                    continue

                # Skip cameras in exponential backoff period (CameraStateTracker handles this)
                if not self._state_tracker.can_retry(camera_id):
                    continue

                # Camera is down, not in cooldown, not in backoff - trigger restart
                stream_type = self._get_stream_type(camera_id)
                logger.info(f"[WATCHDOG] Camera {camera_id} needs restart (type: {stream_type})")

                # Set cooldown BEFORE restart attempt to prevent rapid cycling
                self._restart_cooldowns[camera_id] = now + self.RESTART_COOLDOWN_SECONDS

                if stream_type == 'LL_HLS':
                    self._restart_ll_hls(camera_id)
                elif stream_type == 'MJPEG':
                    self._restart_mjpeg(camera_id)
                else:
                    logger.warning(f"Unknown stream type for {camera_id}: {stream_type}")

            except Exception as e:
                logger.error(f"Error checking camera {camera_id}: {e}", exc_info=True)

    def _get_all_camera_ids(self) -> list:
        """
        Get list of all camera IDs being tracked.

        Returns camera IDs from CameraStateTracker's internal state.
        """
        # Access the tracker's states through its lock for thread safety
        with self._state_tracker._lock:
            return list(self._state_tracker._states.keys())

    def _get_stream_type(self, camera_id: str) -> str:
        """
        Determine stream type for camera (LL_HLS or MJPEG).

        Checks camera_repo configuration for stream_type field.
        Caches result to avoid repeated lookups.

        Args:
            camera_id: Camera serial number

        Returns:
            'LL_HLS' or 'MJPEG'
        """
        # Check cache first
        if camera_id in self._camera_types:
            return self._camera_types[camera_id]

        # Look up from camera repository
        try:
            camera = self._stream_manager.camera_repo.get_camera(camera_id)
            if camera:
                stream_type = camera.get('stream_type', 'LL_HLS').upper()
                # Normalize: MJPEG type has 'MJPEG' in stream_type or is in mjpeg_cameras list
                if 'MJPEG' in stream_type:
                    self._camera_types[camera_id] = 'MJPEG'
                else:
                    self._camera_types[camera_id] = 'LL_HLS'
                return self._camera_types[camera_id]
        except Exception as e:
            logger.warning(f"Error looking up stream type for {camera_id}: {e}")

        # Default to LL_HLS
        return 'LL_HLS'

    def _restart_ll_hls(self, camera_id: str) -> None:
        """
        Restart LL-HLS stream via StreamManager.

        Calls StreamManager.restart_stream() and reports result to
        CameraStateTracker.

        Args:
            camera_id: Camera serial number
        """
        logger.warning(f"[WATCHDOG] Restarting LL-HLS stream for {camera_id}")

        try:
            success = self._stream_manager.restart_stream(camera_id)

            if success:
                logger.info(f"[WATCHDOG] LL-HLS restart successful for {camera_id}")
                self._state_tracker.register_success(camera_id)
                # Broadcast to frontend so HLS.js can refresh immediately
                self._broadcast_stream_restarted(camera_id)
            else:
                logger.error(f"[WATCHDOG] LL-HLS restart failed for {camera_id}")
                self._state_tracker.register_failure(camera_id, "Watchdog restart failed")

        except Exception as e:
            logger.error(f"[WATCHDOG] LL-HLS restart error for {camera_id}: {e}")
            self._state_tracker.register_failure(camera_id, str(e))

    def _restart_mjpeg(self, camera_id: str) -> None:
        """
        Restart MJPEG capture via appropriate service.

        Determines camera vendor type and calls the corresponding
        MJPEG service's restart_capture() method.

        Args:
            camera_id: Camera serial number
        """
        logger.warning(f"[WATCHDOG] Restarting MJPEG capture for {camera_id}")

        try:
            # Determine which MJPEG service handles this camera
            camera = self._stream_manager.camera_repo.get_camera(camera_id)
            if not camera:
                logger.error(f"[WATCHDOG] Camera {camera_id} not found in repository")
                self._state_tracker.register_failure(camera_id, "Camera not found")
                return

            camera_type = camera.get('type', '').lower()

            # Get the appropriate MJPEG service
            mjpeg_service = self._mjpeg_services.get(camera_type)
            if not mjpeg_service:
                # Try generic mjpeg service as fallback
                mjpeg_service = self._mjpeg_services.get('generic')

            if not mjpeg_service:
                logger.error(f"[WATCHDOG] No MJPEG service for camera type: {camera_type}")
                self._state_tracker.register_failure(camera_id, f"No MJPEG service for type: {camera_type}")
                return

            # Call restart_capture if available
            if hasattr(mjpeg_service, 'restart_capture'):
                success = mjpeg_service.restart_capture(camera_id, camera)

                if success:
                    logger.info(f"[WATCHDOG] MJPEG restart successful for {camera_id}")
                    self._state_tracker.register_success(camera_id)
                    # Broadcast to frontend so it can refresh immediately
                    self._broadcast_stream_restarted(camera_id)
                else:
                    logger.error(f"[WATCHDOG] MJPEG restart failed for {camera_id}")
                    self._state_tracker.register_failure(camera_id, "MJPEG restart failed")
            else:
                logger.error(f"[WATCHDOG] MJPEG service for {camera_type} missing restart_capture method")
                self._state_tracker.register_failure(camera_id, "restart_capture not implemented")

        except Exception as e:
            logger.error(f"[WATCHDOG] MJPEG restart error for {camera_id}: {e}")
            self._state_tracker.register_failure(camera_id, str(e))

    def clear_cooldown(self, camera_id: str) -> None:
        """
        Clear restart cooldown for a specific camera.

        Called when a manual restart is triggered via the UI restart button.
        This ensures the watchdog doesn't block the manual restart due to
        a prior cooldown period.

        Args:
            camera_id: Camera serial number
        """
        if camera_id in self._restart_cooldowns:
            del self._restart_cooldowns[camera_id]
            logger.info(f"[WATCHDOG] Cleared cooldown for {camera_id} (manual restart)")

    def set_mjpeg_services(self, services: Dict[str, Any]) -> None:
        """
        Set MJPEG service instances for restart handling.

        Called during app initialization after MJPEG services are created.

        Args:
            services: Dict mapping camera type to MJPEG service instance
                     (e.g., {'reolink': ReolinkMJPEGCaptureService(), ...})
        """
        self._mjpeg_services = services
        logger.info(f"StreamWatchdog MJPEG services configured: {list(services.keys())}")
