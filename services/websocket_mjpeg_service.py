#!/usr/bin/env python3
"""
WebSocket MJPEG Service - Multiplexed MJPEG streaming over WebSocket

This service delivers MJPEG frames from all cameras over a single WebSocket connection,
bypassing the browser's ~6 HTTP connections per domain limit.

Architecture:
- Single WebSocket connection per browser
- Server sends binary frames with camera ID prefix
- Frontend demultiplexes frames to appropriate canvas elements
- Uses existing frame_buffers from mediaserver_mjpeg_service

Protocol:
- Frame format: [1 byte length][camera_id bytes][jpeg_data]
  - First byte: length of camera_id string
  - Next N bytes: camera_id as UTF-8
  - Remaining bytes: JPEG image data

Performance benefits:
- HTTP MJPEG: Browser limited to ~6 concurrent streams per domain
- WebSocket MJPEG: All 16 camera streams over single TCP connection
- Eliminates connection queuing that causes 10/16 cameras to wait
"""

import base64
import json
import logging
import threading
import time
from typing import Dict, Set, Optional

logger = logging.getLogger(__name__)

# Lazy import of mediaserver_mjpeg_service to avoid circular imports
_mediaserver_mjpeg_service = None


def _get_mjpeg_service():
    """Lazy import to avoid circular dependencies at module load time"""
    global _mediaserver_mjpeg_service
    if _mediaserver_mjpeg_service is None:
        from services.mediaserver_mjpeg_service import mediaserver_mjpeg_service
        _mediaserver_mjpeg_service = mediaserver_mjpeg_service
    return _mediaserver_mjpeg_service


class WebSocketMJPEGService:
    """
    Service for streaming MJPEG frames over WebSocket.

    Manages client subscriptions and broadcasts frames from the shared
    mediaserver_mjpeg_service buffers to connected WebSocket clients.
    """

    def __init__(self):
        """Initialize WebSocket MJPEG service"""
        # Client subscriptions: sid -> set of camera_ids
        self.client_subscriptions: Dict[str, Set[str]] = {}

        # Active clients: sid -> client info
        self.active_clients: Dict[str, dict] = {}

        # Last frame sent to each client per camera (for change detection)
        self.last_frame_numbers: Dict[str, Dict[str, int]] = {}

        # Lock for thread-safe access
        self.lock = threading.Lock()

        # Frame broadcast thread
        self.broadcast_thread: Optional[threading.Thread] = None
        self.broadcast_stop_flag = threading.Event()

        # SocketIO instance (set by app.py after initialization)
        self.socketio = None

        # Broadcast rate (frames per second per camera)
        self.target_fps = 2  # Match mediaserver MJPEG rate

        logger.info("WebSocket MJPEG Service initialized")

    def set_socketio(self, socketio):
        """
        Set the SocketIO instance after Flask-SocketIO initialization.

        Args:
            socketio: Flask-SocketIO instance
        """
        self.socketio = socketio
        logger.info("WebSocket MJPEG Service: SocketIO instance set")

    def add_client(self, sid: str, camera_ids: list) -> bool:
        """
        Register a client and their camera subscriptions.

        Args:
            sid: SocketIO session ID
            camera_ids: List of camera serial numbers to subscribe to

        Returns:
            True if successful
        """
        with self.lock:
            self.client_subscriptions[sid] = set(camera_ids)
            self.active_clients[sid] = {
                'connected_at': time.time(),
                'camera_count': len(camera_ids),
                'frames_sent': 0
            }
            self.last_frame_numbers[sid] = {}

            logger.info(f"WebSocket MJPEG: Client {sid[:8]}... subscribed to {len(camera_ids)} cameras")

            # Start broadcast thread if not running
            self._ensure_broadcast_thread()

            return True

    def remove_client(self, sid: str):
        """
        Unregister a client.

        Args:
            sid: SocketIO session ID
        """
        with self.lock:
            if sid in self.client_subscriptions:
                del self.client_subscriptions[sid]
            if sid in self.active_clients:
                del self.active_clients[sid]
            if sid in self.last_frame_numbers:
                del self.last_frame_numbers[sid]

            logger.info(f"WebSocket MJPEG: Client {sid[:8]}... disconnected")

            # Stop broadcast thread if no clients
            if not self.client_subscriptions:
                self._stop_broadcast_thread()

    def update_subscription(self, sid: str, camera_ids: list):
        """
        Update a client's camera subscriptions.

        Args:
            sid: SocketIO session ID
            camera_ids: New list of camera serial numbers
        """
        with self.lock:
            if sid in self.client_subscriptions:
                self.client_subscriptions[sid] = set(camera_ids)
                if sid in self.active_clients:
                    self.active_clients[sid]['camera_count'] = len(camera_ids)
                logger.debug(f"WebSocket MJPEG: Client {sid[:8]}... updated to {len(camera_ids)} cameras")

    def _ensure_broadcast_thread(self):
        """Start the broadcast thread if not already running"""
        if self.broadcast_thread is None or not self.broadcast_thread.is_alive():
            self.broadcast_stop_flag.clear()
            self.broadcast_thread = threading.Thread(
                target=self._broadcast_loop,
                daemon=True,
                name="ws-mjpeg-broadcast"
            )
            self.broadcast_thread.start()
            logger.info("WebSocket MJPEG: Broadcast thread started")

    def _stop_broadcast_thread(self):
        """Stop the broadcast thread"""
        if self.broadcast_thread and self.broadcast_thread.is_alive():
            self.broadcast_stop_flag.set()
            self.broadcast_thread.join(timeout=2.0)
            self.broadcast_thread = None
            logger.info("WebSocket MJPEG: Broadcast thread stopped")

    def _broadcast_loop(self):
        """
        Main broadcast loop - sends frames to all connected clients.

        Runs at target FPS, checking each camera's frame buffer for new frames
        and sending to subscribed clients.
        """
        logger.info("WebSocket MJPEG: Broadcast loop started")

        frame_interval = 1.0 / self.target_fps  # Time between frames

        while not self.broadcast_stop_flag.is_set():
            loop_start = time.time()

            try:
                mjpeg_service = _get_mjpeg_service()

                with self.lock:
                    if not self.client_subscriptions:
                        # No clients, wait and check again
                        time.sleep(0.5)
                        continue

                    # Collect all cameras that at least one client wants
                    all_cameras = set()
                    for cameras in self.client_subscriptions.values():
                        all_cameras.update(cameras)

                # Get frames for all requested cameras
                frames_to_send = {}
                for camera_id in all_cameras:
                    frame_info = mjpeg_service.frame_buffers.get(camera_id)
                    if frame_info and frame_info.get('data'):
                        frames_to_send[camera_id] = frame_info

                # Send frames to each client (only cameras they're subscribed to)
                with self.lock:
                    for sid, subscribed_cameras in list(self.client_subscriptions.items()):
                        try:
                            # Collect frames for this client
                            client_frames = []
                            client_last_frames = self.last_frame_numbers.get(sid, {})

                            for camera_id in subscribed_cameras:
                                if camera_id in frames_to_send:
                                    frame_info = frames_to_send[camera_id]
                                    frame_num = frame_info.get('frame_number', 0)

                                    # Only send if frame is new
                                    if frame_num != client_last_frames.get(camera_id, -1):
                                        client_last_frames[camera_id] = frame_num

                                        # Build frame message with camera ID prefix
                                        # Format: camera_id|base64_jpeg_data
                                        frame_data = frame_info['data']
                                        base64_data = base64.b64encode(frame_data).decode('ascii')

                                        client_frames.append({
                                            'camera_id': camera_id,
                                            'frame': base64_data,
                                            'frame_num': frame_num,
                                            'is_error': frame_info.get('is_error', False)
                                        })

                            self.last_frame_numbers[sid] = client_last_frames

                            # Send all frames in one batch
                            if client_frames and self.socketio:
                                self.socketio.emit('mjpeg_frames', {
                                    'frames': client_frames,
                                    'timestamp': time.time()
                                }, room=sid, namespace='/mjpeg')

                                # Update stats
                                if sid in self.active_clients:
                                    self.active_clients[sid]['frames_sent'] += len(client_frames)

                        except Exception as e:
                            logger.warning(f"WebSocket MJPEG: Error sending to client {sid[:8]}...: {e}")

            except Exception as e:
                logger.error(f"WebSocket MJPEG: Broadcast loop error: {e}")

            # Sleep to maintain target FPS
            elapsed = time.time() - loop_start
            sleep_time = max(0, frame_interval - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

        logger.info("WebSocket MJPEG: Broadcast loop ended")

    def get_status(self) -> dict:
        """
        Get service status for API/debugging.

        Returns:
            Status dict with client count, subscriptions, etc.
        """
        with self.lock:
            return {
                'active_clients': len(self.active_clients),
                'broadcast_running': self.broadcast_thread is not None and self.broadcast_thread.is_alive(),
                'target_fps': self.target_fps,
                'clients': {
                    sid[:8] + '...': {
                        'cameras': len(cameras),
                        'connected_at': self.active_clients.get(sid, {}).get('connected_at', 0),
                        'frames_sent': self.active_clients.get(sid, {}).get('frames_sent', 0)
                    }
                    for sid, cameras in self.client_subscriptions.items()
                }
            }


# Singleton instance
websocket_mjpeg_service = WebSocketMJPEGService()
