#!/usr/bin/env python3
"""
Amcrest MJPEG Capture Service - Single source, multiple client architecture
Prevents resource multiplication for Amcrest MJPEG streams
Uses continuous MJPEG stream (multipart/x-mixed-replace) instead of polling
"""

import threading
import time
import logging
import requests
import os
from typing import Dict, Optional, Tuple
from requests.auth import HTTPDigestAuth
from urllib.parse import quote
from collections import defaultdict
from services.credentials.amcrest_credential_provider import AmcrestCredentialProvider



logger = logging.getLogger(__name__)

class AmcrestMJPEGCaptureService:
    """
    Manages single camera MJPEG stream processes serving multiple clients
    Uses Amcrest's native MJPEG streaming API for continuous frame delivery
    """
    
    def __init__(self):
        self.active_captures = {}  # camera_id -> capture_info
        self.frame_buffers = {}    # camera_id -> latest_frame_data
        self.client_counts = defaultdict(int)  # camera_id -> client_count
        self.credential_provider = AmcrestCredentialProvider()
        self.lock = threading.Lock()
        
        
        logger.info("Amcrest MJPEG Capture Service initialized")
        
       
    def start_capture(self, camera_id: str, camera_config: dict, camera_repo) -> bool:
        """Start single capture process for camera if not already running"""
        with self.lock:
            if camera_id not in self.active_captures:
                # Get credentials
                username, password = self.credential_provider.get_credentials(camera_id)
                if not username or not password:
                    logger.error(f"Missing Amcrest credentials for {camera_id}")
                    return False
                
                # Extract camera configuration
                host = camera_config.get('host')
                if not host:
                    logger.error(f"Missing host configuration for {camera_id}")
                    return False
                
                # The mjpeg_snap config passed in should already be the correct sub/main config
                mjpeg_config = camera_config.get('mjpeg_snap', {})
                snap_type = mjpeg_config.get('snap_type', 'sub')
                
                # Map snap_type to Amcrest subtype parameter
                # sub -> subtype=1 (sub stream)
                # main -> subtype=0 (main stream)
                subtype = 2 if snap_type == 'sub' else 0

                capture_info = {
                    'camera_id': camera_id,
                    'camera_name': camera_config.get('name', camera_id),
                    'host': host,
                    'username': username,
                    'password': password,
                    'subtype': subtype,
                    'snap_type': snap_type,
                    'fps': mjpeg_config.get('fps', 10),
                    'timeout_ms': mjpeg_config.get('timeout_ms', 5000),
                    'thread': None,
                    'stop_flag': threading.Event(),
                    'start_time': time.time(),
                    'frame_count': 0,
                    'last_error': None,
                    'last_frame_time': 0
                }
                
                # Start capture thread
                capture_thread = threading.Thread(
                    target=self._capture_loop,
                    args=(camera_id, capture_info),
                    daemon=True,
                    name=f"amcrest-mjpeg-{camera_id}"
                )
                capture_info['thread'] = capture_thread
                self.active_captures[camera_id] = capture_info
                
                capture_thread.start()
                logger.info(f"Started Amcrest MJPEG capture for {camera_id} "
                           f"({capture_info['camera_name']}) at {host} "
                           f"[subtype={subtype}, snap_type={snap_type}]")
                return True
            else:
                logger.debug(f"Amcrest MJPEG capture already running for {camera_id}")
                return True
    
    def _capture_loop(self, camera_id: str, capture_info: dict):
        """
        Main capture loop - continuous MJPEG stream for multiple clients
        Parses multipart/x-mixed-replace stream from Amcrest camera
        """
        
        try: 
            camera_name = capture_info['camera_name']
            stop_flag = capture_info['stop_flag']
            
            print(f"[{camera_id}] Starting MJPEG capture loop...")
            
            # Build Amcrest MJPEG stream URL
            # Format: http://username:password@ip/cgi-bin/mjpg/video.cgi?channel=0&subtype=X
            host = capture_info['host']
            username = capture_info['username']
            password = capture_info['password']
            subtype = capture_info['subtype']
            timeout_sec = capture_info['timeout_ms'] / 1000.0
            
            print(f"host:{host}")
            print(f"username:{username}")
            print(f"password:{password}")
            print(f"subtype:{subtype}")
            print(f"timeout_sec:{timeout_sec}")
            
            # stream_url = f"http://{username}:{password}@{host}/cgi-bin/mjpg/video.cgi?channel=0&subtype={subtype}"
            stream_url = f"http://{host}/cgi-bin/mjpg/video.cgi?channel=1&subtype={subtype}"

                    
            print(f"Amcrest MJPEG capture loop started for {camera_id} ({camera_name})")
            print(f"Stream URL: {stream_url}")
            
            retry_delay = 1.0
            max_retry_delay = 30.0
        except Exception as e:
            logger.error(f"Error initializing Amcrest MJPEG capture loop for {camera_id}: {e}")
            return
        
        while not stop_flag.is_set():
            try:
                # Open streaming connection with timeout
                logger.debug(f"[{camera_id}] Opening MJPEG stream connection...")
                response = requests.get(
                    stream_url,
                    auth=HTTPDigestAuth(username, password),
                    stream=True,
                    timeout=timeout_sec
                )
                
                if response.status_code != 200:
                    error_msg = f"HTTP {response.status_code} from MJPEG API"
                    with self.lock:
                        capture_info['last_error'] = error_msg
                    logger.error(f"[{camera_id}] {error_msg}")
                    stop_flag.wait(retry_delay)
                    retry_delay = min(retry_delay * 2, max_retry_delay)
                    continue
                
                # Reset retry delay on successful connection
                retry_delay = 1.0
                logger.info(f"[{camera_id}] MJPEG stream connected successfully")
                
                # Parse multipart/x-mixed-replace stream
                # Content-Type: multipart/x-mixed-replace;boundary=<boundary>
                content_type = response.headers.get('Content-Type', '')
                boundary = None
                
                # Extract boundary from Content-Type header
                if 'boundary=' in content_type:
                    boundary = content_type.split('boundary=')[1].strip()
                    if boundary.startswith('"') and boundary.endswith('"'):
                        boundary = boundary[1:-1]
                    # Add leading dashes as they appear in the stream
                    boundary = f"--{boundary}".encode('utf-8')
                    logger.debug(f"[{camera_id}] Using boundary: {boundary}")
                else:
                    logger.warning(f"[{camera_id}] No boundary found in Content-Type, "
                                 f"attempting auto-detection")
                
                # Read and process MJPEG frames
                self._process_mjpeg_stream(camera_id, capture_info, response, boundary, stop_flag)
                
            except requests.exceptions.Timeout:
                error_msg = "Connection timeout"
                with self.lock:
                    capture_info['last_error'] = error_msg
                logger.warning(f"[{camera_id}] {error_msg}, retrying in {retry_delay}s")
                stop_flag.wait(retry_delay)
                retry_delay = min(retry_delay * 2, max_retry_delay)
                
            except requests.exceptions.RequestException as e:
                error_msg = f"Request error: {e}"
                with self.lock:
                    capture_info['last_error'] = error_msg
                logger.error(f"[{camera_id}] RequestException {error_msg}")
                stop_flag.wait(retry_delay)
                retry_delay = min(retry_delay * 2, max_retry_delay)
                
            except Exception as e:
                error_msg = f"Unexpected error: {e}"
                with self.lock:
                    capture_info['last_error'] = error_msg
                logger.error(f"[{camera_id}] {error_msg}", exc_info=True)
                stop_flag.wait(retry_delay)
                retry_delay = min(retry_delay * 2, max_retry_delay)
        
        logger.info(f"Amcrest MJPEG capture loop ended for {camera_id}")
    
    def _process_mjpeg_stream(self, camera_id: str, capture_info: dict, 
                              response, boundary: bytes, stop_flag: threading.Event):
        """
        Process multipart/x-mixed-replace MJPEG stream
        Extracts individual JPEG frames and updates frame buffer
        """
        buffer = b''
        jpeg_start = b'\xff\xd8'  # JPEG SOI (Start of Image)
        jpeg_end = b'\xff\xd9'    # JPEG EOI (End of Image)
        
        for chunk in response.iter_content(chunk_size=8192):
            if stop_flag.is_set():
                break
                
            buffer += chunk
            
            # Look for complete JPEG frames in buffer
            while True:
                # Find JPEG start marker
                start_idx = buffer.find(jpeg_start)
                if start_idx == -1:
                    # No JPEG start found, clear old data and keep last chunk
                    if len(buffer) > 100000:  # Keep last 100KB
                        buffer = buffer[-100000:]
                    break
                
                # Find JPEG end marker after start
                end_idx = buffer.find(jpeg_end, start_idx + 2)
                if end_idx == -1:
                    # Incomplete JPEG, wait for more data
                    # But remove any data before the start marker
                    if start_idx > 0:
                        buffer = buffer[start_idx:]
                    break
                
                # Extract complete JPEG frame
                jpeg_data = buffer[start_idx:end_idx + 2]
                buffer = buffer[end_idx + 2:]  # Remove processed frame
                
                # Validate frame size
                if len(jpeg_data) < 1000:
                    logger.warning(f"[{camera_id}] Frame too small ({len(jpeg_data)} bytes), skipping")
                    continue
                
                # Update shared buffer with latest frame
                with self.lock:
                    self.frame_buffers[camera_id] = {
                        'data': jpeg_data,
                        'timestamp': time.time(),
                        'frame_number': capture_info['frame_count'],
                        'size': len(jpeg_data)
                    }
                    capture_info['frame_count'] += 1
                    capture_info['last_frame_time'] = time.time()
                    capture_info['last_error'] = None
                
                # Log occasionally (every 100 frames)
                if capture_info['frame_count'] % 100 == 1:
                    logger.debug(f"[{camera_id}] Frame {capture_info['frame_count']}, "
                               f"size={len(jpeg_data)} bytes, clients={self.client_counts[camera_id]}")
    
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
            logger.info(f"Added Amcrest MJPEG client for {camera_id} ({camera_name}) "
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
            logger.error(f"Error adding Amcrest MJPEG client for {camera_id}: {e}")
            return False
    
    def remove_client(self, camera_id: str):
        """Remove client, stop capture if no more clients"""
        try:
            with self.lock:
                if camera_id in self.client_counts and self.client_counts[camera_id] > 0:
                    self.client_counts[camera_id] -= 1
                    client_count = self.client_counts[camera_id]
                    
                    camera_name = self.active_captures.get(camera_id, {}).get('camera_name', camera_id)
                    logger.info(f"Removed Amcrest MJPEG client for {camera_id} ({camera_name}) "
                               f"(remaining clients: {client_count})")
                    
                    # Stop capture if no more clients
                    if client_count <= 0:
                        self._stop_capture(camera_id)
                        if camera_id in self.client_counts:
                            del self.client_counts[camera_id]
                        
        except Exception as e:
            logger.error(f"Error removing Amcrest MJPEG client for {camera_id}: {e}")
    
    def _stop_capture(self, camera_id: str):
        """Stop capture process for camera"""
        try:
            if camera_id in self.active_captures:
                capture_info = self.active_captures[camera_id]
                camera_name = capture_info.get('camera_name', camera_id)
                
                logger.info(f"Stopping Amcrest MJPEG capture for {camera_id} ({camera_name})")
                
                # Signal stop
                capture_info['stop_flag'].set()
                
                # Wait for thread to finish gracefully
                if capture_info['thread'] and capture_info['thread'].is_alive():
                    capture_info['thread'].join(timeout=5)
                    
                    if capture_info['thread'].is_alive():
                        logger.warning(f"Amcrest MJPEG capture thread for {camera_id} "
                                     f"didn't stop gracefully")
                
                # Cleanup
                del self.active_captures[camera_id]
                if camera_id in self.frame_buffers:
                    del self.frame_buffers[camera_id]
                    
                logger.info(f"Stopped Amcrest MJPEG capture for {camera_id}")
                
        except Exception as e:
            logger.error(f"Error stopping Amcrest MJPEG capture for {camera_id}: {e}")
    
    def get_latest_frame(self, camera_id: str) -> Optional[dict]:
        """Get latest frame data for camera"""
        with self.lock:
            frame_data = self.frame_buffers.get(camera_id)
            
            # Return None if frame is too old (> 5 seconds)
            if frame_data and (time.time() - frame_data['timestamp']) > 5.0:
                # logger.warning(f"Frame for {camera_id} is stale "
                #              f"({time.time() - frame_data['timestamp']:.1f}s old)")
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
                    'subtype': capture_info['subtype'],
                    'snap_type': capture_info['snap_type']
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
        logger.info("Cleaning up Amcrest MJPEG capture service")
        
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
        
        logger.info("Amcrest MJPEG capture service cleanup complete")
    
    def emergency_cleanup(self):
        """Emergency cleanup for unhandled situations"""
        try:
            self.cleanup()
        except Exception as e:
            logger.error(f"Error during Amcrest MJPEG emergency cleanup: {e}")

# Global instance
amcrest_mjpeg_capture_service = AmcrestMJPEGCaptureService()