#!/usr/bin/env python3
"""
SV3C MJPEG Capture Service - Single source, multiple client architecture
Prevents resource multiplication for SV3C MJPEG streams via snapshot polling.
Follows the same modular pattern as reolink_mjpeg_capture_service.py

SV3C cameras use the hi3510 chipset which provides HTTP snapshot endpoints:
- /tmpfs/auto.jpg (with Basic Auth) - TESTED WORKING
- /snapshot.cgi?user={user}&pwd={pass} - NOT available on all models

This service polls snapshots and serves them as MJPEG to multiple browser clients,
bypassing the unstable RTSP stream that breaks MediaMTX.
"""

import threading
import time
import logging
import requests
import os
from typing import Dict, Optional, Tuple
from collections import defaultdict
from urllib.parse import urlencode, quote

logger = logging.getLogger(__name__)

# Import CameraStateTracker for state reporting (lazy import to avoid circular deps)
_camera_state_tracker = None


def _get_state_tracker():
    """Lazy import of camera_state_tracker to avoid circular imports"""
    global _camera_state_tracker
    if _camera_state_tracker is None:
        try:
            from services.camera_state_tracker import camera_state_tracker
            _camera_state_tracker = camera_state_tracker
        except ImportError:
            logger.warning("CameraStateTracker not available - MJPEG state reporting disabled")
    return _camera_state_tracker


class SV3CMJPEGCaptureService:
    """
    Manages single camera snapshot polling processes serving multiple clients.
    Modeled after reolink_mjpeg_capture_service.py architecture for consistency.

    SV3C cameras (hi3510 chipset) have unstable RTSP but reliable HTTP snapshots.
    This service polls the snapshot endpoint and serves as pseudo-MJPEG stream.
    """

    def __init__(self):
        self.active_captures = {}  # camera_id -> capture_info
        self.frame_buffers = {}    # camera_id -> latest_frame_data
        self.client_counts = defaultdict(int)  # camera_id -> client_count
        self.lock = threading.Lock()

        logger.info("SV3C MJPEG Capture Service initialized")

    def _get_sv3c_credentials(self) -> Tuple[Optional[str], Optional[str]]:
        """
        Get SV3C credentials from database first, with env var fallback.
        SV3C cameras typically use simple username/password auth.
        """
        # Try database first
        try:
            from services.credentials.credential_db_service import get_credential
            username, password = get_credential('sv3c', 'service')
            if username and password:
                return (username, password)
        except Exception:
            pass

        # Fall back to environment variables
        username = os.getenv('NVR_SV3C_USERNAME')
        password = os.getenv('NVR_SV3C_PASSWORD')

        if not username or not password:
            logger.warning("SV3C credentials not found in DB or environment")

        return (username, password)

    def _build_snapshot_url(self, host: str, username: Optional[str], password: Optional[str],
                            sv3c_snap_config: Optional[Dict] = None) -> str:
        """
        Build the snapshot URL for SV3C/hi3510 cameras.

        TESTED WORKING endpoint for this SV3C model:
        - /tmpfs/auto.jpg with Basic Auth (returns HTTP 200)

        NOT working (returns 404):
        - /snapshot.cgi
        - /cgi-bin/hi3510/param.cgi?cmd=snap

        Config can override the endpoint via sv3c_snap.endpoint field.
        Default is now /tmpfs/auto.jpg with basic_auth (tested working).
        """
        # Check for custom endpoint in config
        if sv3c_snap_config:
            endpoint = sv3c_snap_config.get('endpoint', '/tmpfs/auto.jpg')
            auth_type = sv3c_snap_config.get('auth_type', 'basic_auth')
        else:
            # Default: use /tmpfs/auto.jpg with basic auth (TESTED WORKING)
            endpoint = '/tmpfs/auto.jpg'
            auth_type = 'basic_auth'

        # Build URL based on auth type
        if auth_type == 'query_params' and username and password:
            # Query parameter authentication (not supported by all models)
            encoded_user = quote(username, safe='')
            encoded_pwd = quote(password, safe='')
            return f"http://{host}{endpoint}?user={encoded_user}&pwd={encoded_pwd}"
        elif auth_type == 'none' or (not username and not password):
            # No auth required
            return f"http://{host}{endpoint}"
        else:
            # Basic auth will be handled by requests session
            return f"http://{host}{endpoint}"

    def start_capture(self, camera_id: str, camera_config: dict, camera_repo) -> bool:
        """Start single capture process for camera if not already running"""
        with self.lock:
            if camera_id not in self.active_captures:
                # Get credentials
                username, password = self._get_sv3c_credentials()

                # Extract camera configuration
                host = camera_config.get('host')
                if not host:
                    logger.error(f"Missing host configuration for {camera_id}")
                    return False

                # Get MJPEG snap config (same structure as Reolink for consistency)
                mjpeg_config = camera_config.get('mjpeg_snap', {})
                snap_type = mjpeg_config.get('snap_type', 'sub')

                # Get SV3C-specific snap config if present
                sv3c_snap_config = camera_config.get('sv3c_snap', {})

                # Build snapshot URL
                snap_url = self._build_snapshot_url(host, username, password, sv3c_snap_config)

                capture_info = {
                    'camera_id': camera_id,
                    'camera_name': camera_config.get('name', camera_id),
                    'host': host,
                    'username': username,
                    'password': password,
                    'snap_url': snap_url,
                    'auth_type': sv3c_snap_config.get('auth_type', 'basic_auth'),
                    'width': mjpeg_config.get('width'),
                    'height': mjpeg_config.get('height'),
                    'fps': mjpeg_config.get('fps', 7),  # SV3C default lower than Reolink
                    'timeout_ms': mjpeg_config.get('timeout_ms', 5000),
                    'snap_type': snap_type,
                    'thread': None,
                    'stop_flag': threading.Event(),
                    'start_time': time.time(),
                    'frame_count': 0,
                    'last_error': None,
                    'last_frame_time': 0,
                    'session': requests.Session()  # Reuse connection
                }

                # Configure session auth if using basic auth
                if capture_info['auth_type'] == 'basic_auth' and username and password:
                    capture_info['session'].auth = (username, password)

                # Start capture thread
                capture_thread = threading.Thread(
                    target=self._capture_loop,
                    args=(camera_id, capture_info),
                    daemon=True,
                    name=f"sv3c-mjpeg-{camera_id}"
                )
                capture_info['thread'] = capture_thread
                self.active_captures[camera_id] = capture_info

                capture_thread.start()
                logger.info(f"Started SV3C MJPEG capture for {camera_id} "
                           f"({capture_info['camera_name']}) at {host} "
                           f"[{capture_info['width']}x{capture_info['height']} @ {capture_info['fps']} FPS]")
                return True
            else:
                logger.debug(f"SV3C MJPEG capture already running for {camera_id}")
                return True

    def _capture_loop(self, camera_id: str, capture_info: dict):
        """
        Main capture loop - single snapshot polling for multiple clients.
        Similar to reolink_mjpeg_capture_service but uses hi3510 snapshot endpoints.
        """
        camera_name = capture_info['camera_name']
        stop_flag = capture_info['stop_flag']
        session = capture_info['session']
        snap_url = capture_info['snap_url']

        frame_interval = 1.0 / capture_info['fps']
        timeout_sec = capture_info['timeout_ms'] / 1000.0

        logger.info(f"SV3C MJPEG capture loop started for {camera_id} ({camera_name})")
        logger.info(f"[SV3C:{camera_id[:8]}] Snapshot URL: {snap_url}")

        # Report initial state to CameraStateTracker
        tracker = _get_state_tracker()
        if tracker:
            tracker.update_mjpeg_capture_state(camera_id, active=True)

        consecutive_errors = 0
        max_consecutive_errors = 5

        while not stop_flag.is_set():
            try:
                loop_start = time.time()

                # Add cache-busting timestamp to URL
                # Some hi3510 cameras cache aggressively without this
                cache_bust_url = f"{snap_url}&t={int(time.time() * 1000)}" if '?' in snap_url else f"{snap_url}?t={int(time.time() * 1000)}"

                # Single snapshot request
                response = session.get(cache_bust_url, timeout=timeout_sec, stream=False)

                if response.status_code == 200:
                    snapshot = response.content

                    # Validate JPEG data
                    # Check for JPEG magic bytes (FFD8) and minimum size
                    if len(snapshot) < 1000:
                        error_msg = f"Response too small ({len(snapshot)} bytes)"
                        with self.lock:
                            capture_info['last_error'] = error_msg
                        consecutive_errors += 1
                        logger.warning(f"[SV3C:{camera_id[:8]}] {error_msg}")
                        elapsed = time.time() - loop_start
                        remaining = max(0, frame_interval - elapsed)
                        if remaining > 0:
                            stop_flag.wait(remaining)
                        continue

                    # Check JPEG magic bytes
                    if not (snapshot[:2] == b'\xff\xd8'):
                        error_msg = f"Invalid JPEG data (magic bytes: {snapshot[:2].hex()})"
                        with self.lock:
                            capture_info['last_error'] = error_msg
                        consecutive_errors += 1
                        logger.warning(f"[SV3C:{camera_id[:8]}] {error_msg}")
                        elapsed = time.time() - loop_start
                        remaining = max(0, frame_interval - elapsed)
                        if remaining > 0:
                            stop_flag.wait(remaining)
                        continue

                    # Update shared buffer with latest frame
                    with self.lock:
                        self.frame_buffers[camera_id] = {
                            'data': snapshot,
                            'timestamp': time.time(),
                            'frame_number': capture_info['frame_count'],
                            'size': len(snapshot)
                        }
                        capture_info['frame_count'] += 1
                        capture_info['last_frame_time'] = time.time()
                        capture_info['last_error'] = None

                    # Reset error counter on success
                    if consecutive_errors > 0:
                        consecutive_errors = 0
                        if tracker:
                            tracker.update_mjpeg_capture_state(camera_id, active=True)

                    # Log occasionally
                    if capture_info['frame_count'] % 100 == 1:
                        logger.debug(f"[SV3C:{camera_id[:8]}] Frame {capture_info['frame_count']}, "
                                   f"size={len(snapshot)} bytes, clients={self.client_counts[camera_id]}")

                elif response.status_code == 401:
                    error_msg = "Authentication failed (401) - check NVR_SV3C_USERNAME/NVR_SV3C_PASSWORD"
                    with self.lock:
                        capture_info['last_error'] = error_msg
                    consecutive_errors += 1
                    logger.error(f"[SV3C:{camera_id[:8]}] {error_msg}")
                    if consecutive_errors >= max_consecutive_errors and tracker:
                        tracker.update_mjpeg_capture_state(camera_id, active=False, error=error_msg)
                    # Longer wait on auth errors - no point hammering
                    stop_flag.wait(5.0)
                    continue

                else:
                    error_msg = f"HTTP {response.status_code} from snapshot endpoint"
                    with self.lock:
                        capture_info['last_error'] = error_msg
                    consecutive_errors += 1
                    logger.warning(f"[SV3C:{camera_id[:8]}] {error_msg}")
                    if consecutive_errors >= max_consecutive_errors and tracker:
                        tracker.update_mjpeg_capture_state(camera_id, active=False, error=error_msg)

                # Sleep remaining time to maintain target FPS
                elapsed = time.time() - loop_start
                remaining = max(0, frame_interval - elapsed)
                if remaining > 0:
                    stop_flag.wait(remaining)

            except requests.exceptions.Timeout:
                error_msg = "Snapshot timeout"
                with self.lock:
                    capture_info['last_error'] = error_msg
                consecutive_errors += 1
                logger.warning(f"[SV3C:{camera_id[:8]}] {error_msg}")
                if consecutive_errors >= max_consecutive_errors and tracker:
                    tracker.update_mjpeg_capture_state(camera_id, active=False, error=error_msg)
                stop_flag.wait(frame_interval)

            except requests.exceptions.ConnectionError as e:
                error_msg = f"Connection error: {str(e)}"
                with self.lock:
                    capture_info['last_error'] = error_msg
                consecutive_errors += 1
                logger.error(f"[SV3C:{camera_id[:8]}] {error_msg}")
                if consecutive_errors >= max_consecutive_errors and tracker:
                    tracker.update_mjpeg_capture_state(camera_id, active=False, error=error_msg)
                # Longer wait on connection errors
                stop_flag.wait(2.0)

            except Exception as e:
                error_msg = f"Capture error: {str(e)}"
                with self.lock:
                    capture_info['last_error'] = error_msg
                consecutive_errors += 1
                logger.error(f"[SV3C:{camera_id[:8]}] {error_msg}")
                if consecutive_errors >= max_consecutive_errors and tracker:
                    tracker.update_mjpeg_capture_state(camera_id, active=False, error=error_msg)
                stop_flag.wait(2.0)

        # Cleanup session on exit
        session.close()

        if tracker:
            tracker.update_mjpeg_capture_state(camera_id, active=False)

        logger.info(f"SV3C MJPEG capture loop ended for {camera_id}")

    def add_client(self, camera_id: str, camera_config: dict, camera_repo) -> bool:
        """
        Add client for camera stream, start capture if needed.
        Returns True if successful, False if failed to start capture.
        """
        try:
            with self.lock:
                self.client_counts[camera_id] += 1
                client_count = self.client_counts[camera_id]

            camera_name = camera_config.get('name', camera_id)
            logger.info(f"Added SV3C MJPEG client for {camera_id} ({camera_name}) "
                       f"(total clients: {client_count})")

            # Start capture if first client
            if client_count == 1:
                if not self.start_capture(camera_id, camera_config, camera_repo):
                    with self.lock:
                        self.client_counts[camera_id] -= 1
                    logger.error(f"Failed to start capture for {camera_id}")
                    return False

            return True

        except Exception as e:
            logger.error(f"Error adding SV3C MJPEG client for {camera_id}: {e}")
            return False

    def remove_client(self, camera_id: str):
        """Remove client, stop capture if no more clients"""
        try:
            with self.lock:
                if camera_id in self.client_counts and self.client_counts[camera_id] > 0:
                    self.client_counts[camera_id] -= 1
                    client_count = self.client_counts[camera_id]

                    camera_name = self.active_captures.get(camera_id, {}).get('camera_name', camera_id)
                    logger.info(f"Removed SV3C MJPEG client for {camera_id} ({camera_name}) "
                               f"(remaining clients: {client_count})")

                    if client_count <= 0:
                        self._stop_capture(camera_id)
                        if camera_id in self.client_counts:
                            del self.client_counts[camera_id]

        except Exception as e:
            logger.error(f"Error removing SV3C MJPEG client for {camera_id}: {e}")

    def _stop_capture(self, camera_id: str):
        """Stop capture process for camera"""
        try:
            if camera_id in self.active_captures:
                capture_info = self.active_captures[camera_id]
                camera_name = capture_info.get('camera_name', camera_id)

                logger.info(f"Stopping SV3C MJPEG capture for {camera_id} ({camera_name})")

                capture_info['stop_flag'].set()

                if capture_info['thread'] and capture_info['thread'].is_alive():
                    capture_info['thread'].join(timeout=5)

                    if capture_info['thread'].is_alive():
                        logger.warning(f"SV3C MJPEG capture thread for {camera_id} didn't stop gracefully")

                if 'session' in capture_info:
                    capture_info['session'].close()

                del self.active_captures[camera_id]
                if camera_id in self.frame_buffers:
                    del self.frame_buffers[camera_id]

                logger.info(f"Stopped SV3C MJPEG capture for {camera_id}")

        except Exception as e:
            logger.error(f"Error stopping SV3C MJPEG capture for {camera_id}: {e}")

    def restart_capture(self, camera_id: str, camera_config: dict) -> bool:
        """
        Restart MJPEG capture for a camera.
        Called by StreamWatchdog when health check fails.
        """
        logger.info(f"[RESTART] Restarting SV3C MJPEG capture for {camera_id}")

        try:
            with self.lock:
                client_count = self.client_counts.get(camera_id, 0)

            if camera_id in self.active_captures:
                logger.info(f"[RESTART] Stopping existing capture for {camera_id}")
                self._stop_capture(camera_id)
                time.sleep(0.5)

            logger.info(f"[RESTART] Starting fresh capture for {camera_id}")

            if client_count == 0:
                client_count = 1

            with self.lock:
                self.client_counts[camera_id] = client_count

            success = self.start_capture(camera_id, camera_config, None)

            if success:
                logger.info(f"[RESTART] SV3C MJPEG restart successful for {camera_id}")
                return True
            else:
                logger.error(f"[RESTART] SV3C MJPEG restart failed for {camera_id}")
                return False

        except Exception as e:
            logger.error(f"[RESTART] SV3C MJPEG restart error for {camera_id}: {e}", exc_info=True)
            return False

    def get_latest_frame(self, camera_id: str) -> Optional[dict]:
        """Get latest frame data for camera"""
        with self.lock:
            frame_data = self.frame_buffers.get(camera_id)

            # Return None if frame is too old (> 5 seconds)
            if frame_data and (time.time() - frame_data['timestamp']) > 5.0:
                return None

            return frame_data

    def get_status(self, camera_id: str) -> Optional[dict]:
        """Get capture status for camera"""
        with self.lock:
            if camera_id in self.active_captures:
                capture_info = self.active_captures[camera_id]
                frame_info = self.frame_buffers.get(camera_id, {})

                return {
                    'camera_id': camera_id,
                    'camera_name': capture_info['camera_name'],
                    'host': capture_info['host'],
                    'active': True,
                    'clients': self.client_counts.get(camera_id, 0),
                    'start_time': capture_info['start_time'],
                    'uptime': time.time() - capture_info['start_time'],
                    'frame_count': capture_info['frame_count'],
                    'last_frame_time': capture_info.get('last_frame_time', 0),
                    'frame_age': time.time() - capture_info.get('last_frame_time', time.time()),
                    'last_error': capture_info['last_error'],
                    'frame_size': frame_info.get('size', 0),
                    'thread_alive': capture_info['thread'].is_alive() if capture_info['thread'] else False,
                    'fps': capture_info['fps'],
                    'resolution': f"{capture_info['width']}x{capture_info['height']}"
                }
            return None

    def get_all_status(self) -> dict:
        """Get status for all active captures"""
        status = {}
        with self.lock:
            for camera_id in self.active_captures.keys():
                status[camera_id] = self.get_status(camera_id)
        return status

    def is_capture_active(self, camera_id: str) -> bool:
        """Check if capture is active for camera"""
        with self.lock:
            return camera_id in self.active_captures

    def get_client_count(self, camera_id: str) -> int:
        """Get number of clients for camera"""
        with self.lock:
            return self.client_counts.get(camera_id, 0)

    def cleanup(self):
        """Stop all captures and cleanup - called during app shutdown"""
        logger.info("Cleaning up SV3C MJPEG capture service")

        with self.lock:
            camera_ids = list(self.active_captures.keys())

        for camera_id in camera_ids:
            try:
                self._stop_capture(camera_id)
            except Exception as e:
                logger.error(f"Error stopping capture for {camera_id} during cleanup: {e}")

        with self.lock:
            self.client_counts.clear()

        logger.info("SV3C MJPEG capture service cleanup complete")

    def emergency_cleanup(self):
        """Emergency cleanup for unhandled situations"""
        try:
            self.cleanup()
        except Exception as e:
            logger.error(f"Error during SV3C MJPEG emergency cleanup: {e}")


# Global instance
sv3c_mjpeg_capture_service = SV3CMJPEGCaptureService()
