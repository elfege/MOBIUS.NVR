#!/usr/bin/env python3
"""
Reolink MJPEG Capture Service - Single source, multiple client architecture
Prevents resource multiplication for Reolink MJPEG streams via Snap API polling
Follows the same modular pattern as mjpeg_capture_service.py
"""

import threading
import time
import logging
import requests
import os
from typing import Dict, Optional, Tuple
from collections import defaultdict
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

class ReolinkMJPEGCaptureService:
    """
    Manages single camera Snap API polling processes serving multiple clients
    Modeled after mjpeg_capture_service.py architecture for consistency
    """
    
    def __init__(self):
        self.active_captures = {}  # camera_id -> capture_info
        self.frame_buffers = {}    # camera_id -> latest_frame_data
        self.client_counts = defaultdict(int)  # camera_id -> client_count
        self.lock = threading.Lock()
        
        logger.info("Reolink MJPEG Capture Service initialized")
        
    def _get_reolink_credentials(self) -> Tuple[Optional[str], Optional[str]]:
        """
        Get Reolink API credentials from environment
        Tries API-specific credentials first, falls back to main credentials
        """
        # Try API-specific credentials first (for passwords with special characters)
        username = os.getenv('REOLINK_API_USER')
        password = os.getenv('REOLINK_API_PASSWORD')
        
        # Fallback to main credentials if API credentials not set
        if not username or not password:
            username = os.getenv('REOLINK_USERNAME')
            password = os.getenv('REOLINK_PASSWORD')
        
        return (username, password)
    
    def start_capture(self, camera_id: str, camera_config: dict, camera_repo) -> bool:
        """Start single capture process for camera if not already running"""
        with self.lock:
            if camera_id not in self.active_captures:
                # Get credentials
                username, password = self._get_reolink_credentials()
                if not username or not password:
                    logger.error(f"Missing Reolink credentials for {camera_id}")
                    return False
                
                # Extract camera configuration
                host = camera_config.get('host')
                if not host:
                    logger.error(f"Missing host configuration for {camera_id}")
                    return False
                
                # The mjpeg_snap config passed in should already be the correct sub/main config
                # from the app.py route (either mjpeg_config['sub'] or mjpeg_config['main'])
                mjpeg_config = camera_config.get('mjpeg_snap', {})
                snap_type = mjpeg_config.get('snap_type', 'sub')

                capture_info = {
                    'camera_id': camera_id,
                    'camera_name': camera_config.get('name', camera_id),
                    'host': host,
                    'username': username,
                    'password': password,
                    'width': mjpeg_config.get('width'),  # May be None for main stream
                    'height': mjpeg_config.get('height'),  # May be None for main stream
                    'fps': mjpeg_config.get('fps', 10),
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
                
                # Start capture thread using same daemon pattern as UniFi service
                capture_thread = threading.Thread(
                    target=self._capture_loop,
                    args=(camera_id, capture_info),
                    daemon=True,
                    name=f"reolink-mjpeg-{camera_id}"
                )
                capture_info['thread'] = capture_thread
                self.active_captures[camera_id] = capture_info
                
                capture_thread.start()
                logger.info(f"Started Reolink MJPEG capture for {camera_id} "
                           f"({capture_info['camera_name']}) at {host} "
                           f"[{capture_info['width']}x{capture_info['height']} @ {capture_info['fps']} FPS]")
                return True
            else:
                logger.debug(f"Reolink MJPEG capture already running for {camera_id}")
                return True
    
    def _capture_loop(self, camera_id: str, capture_info: dict):
        """
        Main capture loop - single Snap API polling for multiple clients
        Similar to mjpeg_capture_service but polls Reolink HTTP API
        """
        camera_name = capture_info['camera_name']
        stop_flag = capture_info['stop_flag']
        session = capture_info['session']
        
        # Build Snap API parameters
        snap_params = {
            'cmd': 'Snap',
            'channel': 0,
            'user': capture_info['username'],
            'password': capture_info['password']
        }

        # Only add width/height for substream (main uses camera's native resolution)
        if capture_info['width'] and capture_info['height']:
            snap_params['width'] = capture_info['width']
            snap_params['height'] = capture_info['height']
        
        host = capture_info['host']
        frame_interval = 1.0 / capture_info['fps']
        timeout_sec = capture_info['timeout_ms'] / 1000.0
        
        logger.info(f"Reolink MJPEG capture loop started for {camera_id} ({camera_name})")
        
        while not stop_flag.is_set():
            try:
                # Update cache-busting random string
                snap_params['rs'] = int(time.time() * 1000)
                snap_url = f"http://{host}/cgi-bin/api.cgi?{urlencode(snap_params)}"
                
                # Single snapshot request regardless of client count
                # This prevents the N-browser = N-camera-connections problem
                response = session.get(snap_url, timeout=timeout_sec, stream=False)
                
                if response.status_code == 200:
                    snapshot = response.content
                    
                    # Validate JPEG data (minimum size check)
                    if len(snapshot) < 1000:
                        error_msg = f"Response too small ({len(snapshot)} bytes) - likely error response"
                        with self.lock:
                            capture_info['last_error'] = error_msg
                        logger.warning(f"[{camera_id}] {error_msg}: {snapshot[:200]}")
                        stop_flag.wait(frame_interval)
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
                    
                    # Log occasionally (every 100 frames) to reduce spam
                    if capture_info['frame_count'] % 100 == 1:
                        logger.debug(f"[{camera_id}] Frame {capture_info['frame_count']}, "
                                   f"size={len(snapshot)} bytes, clients={self.client_counts[camera_id]}")
                
                else:
                    error_msg = f"HTTP {response.status_code} from Snap API"
                    with self.lock:
                        capture_info['last_error'] = error_msg
                    logger.warning(f"[{camera_id}] {error_msg}")
                
                # Sleep to maintain target FPS
                stop_flag.wait(frame_interval)
                
            except requests.exceptions.Timeout:
                error_msg = "Snap API timeout"
                with self.lock:
                    capture_info['last_error'] = error_msg
                logger.warning(f"[{camera_id}] {error_msg}")
                stop_flag.wait(frame_interval)
                
            except requests.exceptions.RequestException as e:
                error_msg = f"Request error: {str(e)}"
                with self.lock:
                    capture_info['last_error'] = error_msg
                logger.error(f"[{camera_id}] {error_msg}")
                # Longer wait on connection errors
                stop_flag.wait(2.0)
                
            except Exception as e:
                error_msg = f"Capture error: {str(e)}"
                with self.lock:
                    capture_info['last_error'] = error_msg
                logger.error(f"[{camera_id}] {error_msg}")
                # Longer wait on unexpected errors
                stop_flag.wait(2.0)
        
        # Cleanup session on exit
        session.close()
        logger.info(f"Reolink MJPEG capture loop ended for {camera_id}")
    
    def add_client(self, camera_id: str, camera_config: dict, camera_repo) -> bool:
        """
        Add client for camera stream, start capture if needed
        Returns True if successful, False if failed to start capture
        """
        try:
            with self.lock:
                self.client_counts[camera_id] += 1
                client_count = self.client_counts[camera_id]
            
            camera_name = camera_config.get('name', camera_id)
            logger.info(f"Added Reolink MJPEG client for {camera_id} ({camera_name}) "
                       f"(total clients: {client_count})")
            
            # Start capture if this is first client
            if client_count == 1:
                if not self.start_capture(camera_id, camera_config, camera_repo):
                    # If capture fails, remove the client we just added
                    with self.lock:
                        self.client_counts[camera_id] -= 1
                    logger.error(f"Failed to start capture for {camera_id}")
                    return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error adding Reolink MJPEG client for {camera_id}: {e}")
            return False
    
    def remove_client(self, camera_id: str):
        """Remove client, stop capture if no more clients"""
        try:
            with self.lock:
                if camera_id in self.client_counts and self.client_counts[camera_id] > 0:
                    self.client_counts[camera_id] -= 1
                    client_count = self.client_counts[camera_id]
                    
                    camera_name = self.active_captures.get(camera_id, {}).get('camera_name', camera_id)
                    logger.info(f"Removed Reolink MJPEG client for {camera_id} ({camera_name}) "
                               f"(remaining clients: {client_count})")
                    
                    # Stop capture if no more clients
                    if client_count <= 0:
                        self._stop_capture(camera_id)
                        if camera_id in self.client_counts:
                            del self.client_counts[camera_id]
                        
        except Exception as e:
            logger.error(f"Error removing Reolink MJPEG client for {camera_id}: {e}")
    
    def _stop_capture(self, camera_id: str):
        """Stop capture process for camera - similar to UniFi service stop logic"""
        try:
            if camera_id in self.active_captures:
                capture_info = self.active_captures[camera_id]
                camera_name = capture_info.get('camera_name', camera_id)
                
                logger.info(f"Stopping Reolink MJPEG capture for {camera_id} ({camera_name})")
                
                # Signal stop
                capture_info['stop_flag'].set()
                
                # Wait for thread to finish gracefully
                if capture_info['thread'] and capture_info['thread'].is_alive():
                    capture_info['thread'].join(timeout=5)
                    
                    if capture_info['thread'].is_alive():
                        logger.warning(f"Reolink MJPEG capture thread for {camera_id} "
                                     f"didn't stop gracefully")
                
                # Close session
                if 'session' in capture_info:
                    capture_info['session'].close()
                
                # Cleanup
                del self.active_captures[camera_id]
                if camera_id in self.frame_buffers:
                    del self.frame_buffers[camera_id]
                    
                logger.info(f"Stopped Reolink MJPEG capture for {camera_id}")
                
        except Exception as e:
            logger.error(f"Error stopping Reolink MJPEG capture for {camera_id}: {e}")
    
    def get_latest_frame(self, camera_id: str) -> Optional[dict]:
        """Get latest frame data for camera"""
        with self.lock:
            frame_data = self.frame_buffers.get(camera_id)
            
            # Return None if frame is too old (> 5 seconds)
            if frame_data and (time.time() - frame_data['timestamp']) > 5.0:
            #     logger.warning(f"Frame for {camera_id} is stale "
            #                  f"({time.time() - frame_data['timestamp']:.1f}s old)")
                return None
                
            return frame_data
    
    def get_status(self, camera_id: str) -> Optional[dict]:
        """Get capture status for camera - matches UniFi service pattern"""
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
        logger.info("Cleaning up Reolink MJPEG capture service")
        
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
        
        logger.info("Reolink MJPEG capture service cleanup complete")
    
    def emergency_cleanup(self):
        """Emergency cleanup for unhandled situations"""
        try:
            self.cleanup()
        except Exception as e:
            logger.error(f"Error during Reolink MJPEG emergency cleanup: {e}")

# Global instance following UniFi service pattern
reolink_mjpeg_capture_service = ReolinkMJPEGCaptureService()