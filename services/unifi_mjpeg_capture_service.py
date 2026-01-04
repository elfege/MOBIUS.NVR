#!/usr/bin/env python3
"""
MJPEG Capture Service - Single source, multiple client architecture
Prevents resource multiplication for UniFi MJPEG streams
Follows the same modular pattern as stream_manager.py
"""

import threading
import time
import logging
from typing import Dict, Optional
from collections import defaultdict

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

class UNIFIMJPEGCaptureService:
    """
    Manages single camera capture processes serving multiple clients
    Modeled after stream_manager.py architecture for consistency
    """
    
    def __init__(self):
        self.active_captures = {}  # camera_id -> capture_info
        self.frame_buffers = {}    # camera_id -> latest_frame_data
        self.client_counts = defaultdict(int)  # camera_id -> client_count
        self.lock = threading.Lock()
        
        logger.info("MJPEG Capture Service initialized")
        
    def start_capture(self, camera_id: str, camera_service) -> bool:
        """Start single capture process for camera if not already running"""
        with self.lock:
            if camera_id not in self.active_captures:
                capture_info = {
                    'camera_service': camera_service,
                    'thread': None,
                    'stop_flag': threading.Event(),
                    'start_time': time.time(),
                    'frame_count': 0,
                    'last_error': None,
                    'last_frame_time': 0
                }
                
                # Start capture thread using same daemon pattern as stream_manager
                capture_thread = threading.Thread(
                    target=self._capture_loop,
                    args=(camera_id, capture_info),
                    daemon=True,
                    name=f"mjpeg-capture-{camera_id}"
                )
                capture_info['thread'] = capture_thread
                self.active_captures[camera_id] = capture_info
                
                capture_thread.start()
                logger.info(f"Started MJPEG capture for {camera_id}")
                return True
            else:
                logger.debug(f"MJPEG capture already running for {camera_id}")
                return True
    
    def _capture_loop(self, camera_id: str, capture_info: dict):
        """
        Main capture loop - single snapshot source for multiple clients
        Similar to stream_manager's FFmpeg management but for MJPEG
        """
        camera_service = capture_info['camera_service']
        stop_flag = capture_info['stop_flag']

        logger.info(f"MJPEG capture loop started for {camera_id} ({camera_service.name})")

        # Report initial state to CameraStateTracker
        tracker = _get_state_tracker()
        if tracker:
            tracker.update_mjpeg_capture_state(camera_id, active=True)

        consecutive_errors = 0
        max_consecutive_errors = 5  # Report offline after this many consecutive failures

        while not stop_flag.is_set():
            try:
                # Single snapshot request regardless of client count
                # This prevents the N-browser = N-camera-connections problem
                snapshot = camera_service.get_snapshot()

                if snapshot:
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

                    # Reset error counter on successful frame
                    if consecutive_errors > 0:
                        consecutive_errors = 0
                        if tracker:
                            tracker.update_mjpeg_capture_state(camera_id, active=True)
                else:
                    with self.lock:
                        capture_info['last_error'] = "Snapshot failed"
                    consecutive_errors += 1
                    logger.warning(f"Snapshot failed for {camera_id}")
                    if consecutive_errors >= max_consecutive_errors and tracker:
                        tracker.update_mjpeg_capture_state(camera_id, active=False, error="Snapshot failed")

                # 2 FPS interval - same as original implementation
                stop_flag.wait(0.5)

            except Exception as e:
                error_msg = str(e)
                with self.lock:
                    capture_info['last_error'] = error_msg
                consecutive_errors += 1
                logger.error(f"Capture error for {camera_id}: {e}")
                if consecutive_errors >= max_consecutive_errors and tracker:
                    tracker.update_mjpeg_capture_state(camera_id, active=False, error=error_msg)

                # Longer wait on error to prevent spam
                stop_flag.wait(2.0)

        # Report capture stopped to CameraStateTracker
        if tracker:
            tracker.update_mjpeg_capture_state(camera_id, active=False)

        logger.info(f"MJPEG capture loop ended for {camera_id}")
    
    def add_client(self, camera_id: str, camera_service) -> bool:
        """
        Add client for camera stream, start capture if needed
        Returns True if successful, False if failed to start capture
        """
        try:
            with self.lock:
                self.client_counts[camera_id] += 1
                client_count = self.client_counts[camera_id]
            
            logger.info(f"Added MJPEG client for {camera_id} (total: {client_count})")
            
            # Start capture if this is first client
            if client_count == 1:
                if not self.start_capture(camera_id, camera_service):
                    # If capture fails, remove the client we just added
                    with self.lock:
                        self.client_counts[camera_id] -= 1
                    return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error adding MJPEG client for {camera_id}: {e}")
            return False
    
    def remove_client(self, camera_id: str):
        """Remove client, stop capture if no more clients"""
        try:
            with self.lock:
                if camera_id in self.client_counts and self.client_counts[camera_id] > 0:
                    self.client_counts[camera_id] -= 1
                    client_count = self.client_counts[camera_id]
                    
                    logger.info(f"Removed MJPEG client for {camera_id} (remaining: {client_count})")
                    
                    # Stop capture if no more clients
                    if client_count <= 0:
                        self._stop_capture(camera_id)
                        if camera_id in self.client_counts:
                            del self.client_counts[camera_id]
                        
        except Exception as e:
            logger.error(f"Error removing MJPEG client for {camera_id}: {e}")
    
    def _stop_capture(self, camera_id: str):
        """Stop capture process for camera - similar to stream_manager stop logic"""
        try:
            if camera_id in self.active_captures:
                capture_info = self.active_captures[camera_id]
                
                logger.info(f"Stopping MJPEG capture for {camera_id}")
                
                # Signal stop
                capture_info['stop_flag'].set()
                
                # Wait for thread to finish gracefully
                if capture_info['thread'] and capture_info['thread'].is_alive():
                    capture_info['thread'].join(timeout=5)
                    
                    if capture_info['thread'].is_alive():
                        logger.warning(f"MJPEG capture thread for {camera_id} didn't stop gracefully")
                
                # Cleanup
                del self.active_captures[camera_id]
                if camera_id in self.frame_buffers:
                    del self.frame_buffers[camera_id]
                    
                logger.info(f"Stopped MJPEG capture for {camera_id}")
                
        except Exception as e:
            logger.error(f"Error stopping MJPEG capture for {camera_id}: {e}")

    def restart_capture(self, camera_id: str, camera_config: dict) -> bool:
        """
        Restart MJPEG capture for a camera.

        Called by StreamWatchdog when MJPEG camera health check fails.
        Performs clean stop + start cycle.

        Args:
            camera_id: Camera serial number
            camera_config: Camera configuration dict from camera repository

        Returns:
            bool: True if restart was successful, False otherwise
        """
        logger.info(f"[RESTART] Restarting UniFi MJPEG capture for {camera_id}")

        try:
            # Step 1: Stop existing capture
            with self.lock:
                # Remember client count so we can restore clients
                client_count = self.client_counts.get(camera_id, 0)

            if camera_id in self.active_captures:
                logger.info(f"[RESTART] Stopping existing capture for {camera_id}")
                self._stop_capture(camera_id)
                # Brief pause for cleanup
                import time
                time.sleep(0.5)

            # Step 2: Start fresh capture
            logger.info(f"[RESTART] Starting fresh capture for {camera_id}")

            # Restore client count (capture needs at least 1 client to start)
            if client_count == 0:
                client_count = 1  # Force at least 1 client for restart

            with self.lock:
                self.client_counts[camera_id] = client_count

            # For UniFi, we need the camera_service object
            # Try to get it from camera_config or active_captures backup
            camera_service = camera_config.get('_camera_service')
            if not camera_service:
                logger.error(f"[RESTART] UniFi camera_service not available for {camera_id}")
                return False

            success = self.start_capture(camera_id, camera_service)

            if success:
                logger.info(f"[RESTART] UniFi MJPEG restart successful for {camera_id}")
                return True
            else:
                logger.error(f"[RESTART] UniFi MJPEG restart failed for {camera_id}")
                return False

        except Exception as e:
            logger.error(f"[RESTART] UniFi MJPEG restart error for {camera_id}: {e}", exc_info=True)
            return False

    def get_latest_frame(self, camera_id: str) -> Optional[dict]:
        """Get latest frame data for camera"""
        with self.lock:
            frame_data = self.frame_buffers.get(camera_id)
            
            # Return None if frame is too old (> 5 seconds)
            if frame_data and (time.time() - frame_data['timestamp']) > 5.0:
                # logger.warning(f"Frame for {camera_id} is stale ({time.time() - frame_data['timestamp']:.1f}s old)")
                return None
                
            return frame_data
    
    def get_status(self, camera_id: str) -> Optional[dict]:
        """Get capture status for camera - matches stream_manager pattern"""
        with self.lock:
            if camera_id in self.active_captures:
                capture_info = self.active_captures[camera_id]
                frame_info = self.frame_buffers.get(camera_id, {})
                
                return {
                    'camera_id': camera_id,
                    'camera_name': capture_info['camera_service'].name,
                    'active': True,
                    'clients': self.client_counts.get(camera_id, 0),
                    'start_time': capture_info['start_time'],
                    'uptime': time.time() - capture_info['start_time'],
                    'frame_count': capture_info['frame_count'],
                    'last_frame_time': capture_info.get('last_frame_time', 0),
                    'frame_age': time.time() - capture_info.get('last_frame_time', time.time()),
                    'last_error': capture_info['last_error'],
                    'frame_size': frame_info.get('size', 0),
                    'thread_alive': capture_info['thread'].is_alive() if capture_info['thread'] else False
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
        logger.info("Cleaning up MJPEG capture service")
        
        # Get list of active captures to avoid modifying dict during iteration
        with self.lock:
            camera_ids = list(self.active_captures.keys())
        
        # Stop all captures
        for camera_id in camera_ids:
            try:
                self._stop_capture(camera_id)
            except Exception as e:
                logger.error(f"Error stopping capture for {camera_id} during cleanup: {e}")
        
        # Clear client counts
        with self.lock:
            self.client_counts.clear()
        
        logger.info("MJPEG capture service cleanup complete")
    
    def emergency_cleanup(self):
        """Emergency cleanup for unhandled situations"""
        try:
            self.cleanup()
        except Exception as e:
            logger.error(f"Error during emergency cleanup: {e}")

# Global instance following stream_manager pattern
unifi_mjpeg_capture_service = UNIFIMJPEGCaptureService()