#!/usr/bin/env python3
"""
MediaServer MJPEG Service - Tap MediaMTX streams for MJPEG output

This service creates MJPEG streams by tapping existing MediaMTX RTSP outputs.
Used for cameras with mjpeg_source: "mediaserver" (single-connection cameras
like Eufy, SV3C, Neolink that can't open a second connection for native MJPEG).

Architecture:
- One FFmpeg process per camera extracts JPEG frames from MediaMTX RTSP
- Frames are stored in a shared buffer
- Multiple HTTP clients read from the buffer (no camera connection multiplication)
"""

import threading
import subprocess
import time
import logging
import os
import io
from typing import Dict, Optional
from collections import defaultdict

logger = logging.getLogger(__name__)

# Lazy import PIL for error frame generation
_pil_available = None

def _get_pil():
    """Lazy import PIL to generate error frames with text"""
    global _pil_available
    if _pil_available is None:
        try:
            from PIL import Image, ImageDraw, ImageFont
            _pil_available = (Image, ImageDraw, ImageFont)
        except ImportError:
            logger.warning("PIL not available - error frames will be blank")
            _pil_available = False
    return _pil_available


def _create_error_frame(message: str, width: int = 320, height: int = 240) -> bytes:
    """
    Create a JPEG frame with an error message displayed.

    Args:
        message: Error text to display
        width: Frame width
        height: Frame height

    Returns:
        JPEG bytes
    """
    pil = _get_pil()
    if not pil:
        # Return minimal valid JPEG (1x1 black pixel)
        return bytes([
            0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00, 0x01,
            0x01, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00, 0xFF, 0xDB, 0x00, 0x43,
            0x00, 0x08, 0x06, 0x06, 0x07, 0x06, 0x05, 0x08, 0x07, 0x07, 0x07, 0x09,
            0x09, 0x08, 0x0A, 0x0C, 0x14, 0x0D, 0x0C, 0x0B, 0x0B, 0x0C, 0x19, 0x12,
            0x13, 0x0F, 0x14, 0x1D, 0x1A, 0x1F, 0x1E, 0x1D, 0x1A, 0x1C, 0x1C, 0x20,
            0x24, 0x2E, 0x27, 0x20, 0x22, 0x2C, 0x23, 0x1C, 0x1C, 0x28, 0x37, 0x29,
            0x2C, 0x30, 0x31, 0x34, 0x34, 0x34, 0x1F, 0x27, 0x39, 0x3D, 0x38, 0x32,
            0x3C, 0x2E, 0x33, 0x34, 0x32, 0xFF, 0xC0, 0x00, 0x0B, 0x08, 0x00, 0x01,
            0x00, 0x01, 0x01, 0x01, 0x11, 0x00, 0xFF, 0xC4, 0x00, 0x1F, 0x00, 0x00,
            0x01, 0x05, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00,
            0x00, 0x00, 0x00, 0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08,
            0x09, 0x0A, 0x0B, 0xFF, 0xC4, 0x00, 0xB5, 0x10, 0x00, 0x02, 0x01, 0x03,
            0x03, 0x02, 0x04, 0x03, 0x05, 0x05, 0x04, 0x04, 0x00, 0x00, 0x01, 0x7D,
            0x01, 0x02, 0x03, 0x00, 0x04, 0x11, 0x05, 0x12, 0x21, 0x31, 0x41, 0x06,
            0x13, 0x51, 0x61, 0x07, 0x22, 0x71, 0x14, 0x32, 0x81, 0x91, 0xA1, 0x08,
            0x23, 0x42, 0xB1, 0xC1, 0x15, 0x52, 0xD1, 0xF0, 0x24, 0x33, 0x62, 0x72,
            0x82, 0x09, 0x0A, 0x16, 0x17, 0x18, 0x19, 0x1A, 0x25, 0x26, 0x27, 0x28,
            0x29, 0x2A, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3A, 0x43, 0x44, 0x45,
            0x46, 0x47, 0x48, 0x49, 0x4A, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59,
            0x5A, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6A, 0x73, 0x74, 0x75,
            0x76, 0x77, 0x78, 0x79, 0x7A, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89,
            0x8A, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9A, 0xA2, 0xA3,
            0xA4, 0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 0xB2, 0xB3, 0xB4, 0xB5, 0xB6,
            0xB7, 0xB8, 0xB9, 0xBA, 0xC2, 0xC3, 0xC4, 0xC5, 0xC6, 0xC7, 0xC8, 0xC9,
            0xCA, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8, 0xD9, 0xDA, 0xE1, 0xE2,
            0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xE8, 0xE9, 0xEA, 0xF1, 0xF2, 0xF3, 0xF4,
            0xF5, 0xF6, 0xF7, 0xF8, 0xF9, 0xFA, 0xFF, 0xDA, 0x00, 0x08, 0x01, 0x01,
            0x00, 0x00, 0x3F, 0x00, 0xFB, 0xD5, 0xDB, 0x20, 0xB8, 0xF8, 0x5F, 0xBC,
            0xC0, 0xB6, 0xB1, 0x08, 0xF1, 0x04, 0xCD, 0xFD, 0xD3, 0xB5, 0x7F, 0xFF,
            0xD9
        ])

    Image, ImageDraw, ImageFont = pil

    # Create dark gray background
    img = Image.new('RGB', (width, height), color=(40, 40, 40))
    draw = ImageDraw.Draw(img)

    # Try to use a readable font, fall back to default
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
    except (IOError, OSError):
        try:
            font = ImageFont.truetype("/usr/share/fonts/TTF/DejaVuSans.ttf", 14)
        except (IOError, OSError):
            font = ImageFont.load_default()

    # Word wrap the message
    lines = []
    words = message.split()
    current_line = ""
    max_width = width - 20  # 10px margin on each side

    for word in words:
        test_line = f"{current_line} {word}".strip()
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)

    # Draw text centered vertically
    line_height = 18
    total_height = len(lines) * line_height
    y = (height - total_height) // 2

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_width = bbox[2] - bbox[0]
        x = (width - text_width) // 2
        draw.text((x, y), line, fill=(200, 200, 200), font=font)
        y += line_height

    # Convert to JPEG bytes
    buffer = io.BytesIO()
    img.save(buffer, format='JPEG', quality=70)
    return buffer.getvalue()

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
            logger.warning("CameraStateTracker not available - mediaserver MJPEG state reporting disabled")
    return _camera_state_tracker


class MediaServerMJPEGService:
    """
    Creates MJPEG streams by tapping MediaMTX RTSP outputs.

    For cameras with mjpeg_source: "mediaserver", this service:
    1. Connects to MediaMTX RTSP stream (already published by dual-output FFmpeg)
    2. Uses FFmpeg to extract JPEG frames at 2 FPS
    3. Stores frames in shared buffer for multiple clients
    """

    def __init__(self, mediamtx_host: str = "nvr-packager", mediamtx_rtsp_port: int = 8554):
        """
        Initialize the mediaserver MJPEG service.

        Args:
            mediamtx_host: MediaMTX host (default: nvr-packager for Docker network)
            mediamtx_rtsp_port: MediaMTX RTSP port (default: 8554)
        """
        self.mediamtx_host = mediamtx_host
        self.mediamtx_rtsp_port = mediamtx_rtsp_port

        self.active_captures = {}  # camera_id -> capture_info
        self.frame_buffers = {}    # camera_id -> latest_frame_data
        self.client_counts = defaultdict(int)  # camera_id -> client_count
        self.lock = threading.Lock()

        logger.info(f"MediaServer MJPEG Service initialized (MediaMTX: {mediamtx_host}:{mediamtx_rtsp_port})")

    def _get_mediamtx_rtsp_url(self, camera_id: str) -> str:
        """
        Build MediaMTX RTSP URL for a camera's sub stream.

        Uses the sub stream path (H.264 320x240) published by the dual-output FFmpeg.
        The MediaServer MJPEG service will decode this and extract JPEG frames.

        Args:
            camera_id: Camera serial number

        Returns:
            RTSP URL for MediaMTX sub stream
        """
        # MediaMTX sub stream path (published by dual-output FFmpeg)
        # This is H.264 encoded - FFmpeg will decode and extract JPEG frames
        return f"rtsp://{self.mediamtx_host}:{self.mediamtx_rtsp_port}/{camera_id}"

    def start_capture(self, camera_id: str, camera_config: dict) -> bool:
        """
        Start FFmpeg capture process to extract JPEG frames from MediaMTX.

        Args:
            camera_id: Camera serial number
            camera_config: Camera configuration dict

        Returns:
            True if capture started successfully
        """
        with self.lock:
            if camera_id in self.active_captures:
                logger.debug(f"MediaServer MJPEG capture already running for {camera_id}")
                return True

            rtsp_url = self._get_mediamtx_rtsp_url(camera_id)
            camera_name = camera_config.get('name', camera_id)

            capture_info = {
                'camera_id': camera_id,
                'camera_name': camera_name,
                'rtsp_url': rtsp_url,
                'process': None,
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
                name=f"mediaserver-mjpeg-{camera_id}"
            )
            capture_info['thread'] = capture_thread
            self.active_captures[camera_id] = capture_info

            # Initialize buffer with "Connecting..." frame so clients see something immediately
            connecting_frame = _create_error_frame(f"Connecting to {camera_name}...")
            self.frame_buffers[camera_id] = {
                'data': connecting_frame,
                'timestamp': time.time(),
                'frame_number': 0,
                'size': len(connecting_frame),
                'is_error': True
            }

            capture_thread.start()
            logger.info(f"Started MediaServer MJPEG capture for {camera_id} ({camera_name})")
            return True

    def _capture_loop(self, camera_id: str, capture_info: dict):
        """
        Main capture loop - runs FFmpeg to extract JPEG frames from MediaMTX.

        FFmpeg command extracts frames at 2 FPS and outputs raw JPEG to stdout.
        Each frame is stored in shared buffer for HTTP clients to read.
        """
        rtsp_url = capture_info['rtsp_url']
        stop_flag = capture_info['stop_flag']
        camera_name = capture_info['camera_name']

        logger.info(f"MediaServer MJPEG capture loop started for {camera_id} ({camera_name})")
        logger.debug(f"MediaServer MJPEG source: {rtsp_url}")

        # Report initial state to CameraStateTracker
        tracker = _get_state_tracker()
        if tracker:
            tracker.update_mjpeg_capture_state(camera_id, active=True)

        # Brief initial delay to let MediaMTX stream stabilize
        # Streams may still be initializing when iOS clients first connect
        initial_wait = 1.0
        logger.debug(f"MediaServer MJPEG {camera_id}: waiting {initial_wait}s for stream to stabilize")
        if stop_flag.wait(initial_wait):
            return  # Stopped during wait

        consecutive_errors = 0
        max_consecutive_errors = 5
        total_errors = 0
        max_total_errors = 20  # Stop entirely after 20 total failures

        while not stop_flag.is_set():
            try:
                # FFmpeg command to extract JPEG frames from MediaMTX H.264 sub stream
                # Decodes H.264, re-encodes as MJPEG at 2 FPS for grid view
                ffmpeg_cmd = [
                    'ffmpeg',
                    '-hide_banner',
                    '-loglevel', 'error',
                    '-rtsp_transport', 'tcp',
                    '-timeout', '5000000',          # 5s connection timeout (microseconds)
                    '-stimeout', '5000000',         # 5s socket timeout (microseconds)
                    '-analyzeduration', '2000000',  # 2s - time to detect H.264 codec
                    '-probesize', '2000000',        # 2MB probe buffer
                    '-i', rtsp_url,
                    '-r', '2',              # 2 FPS for MJPEG grid
                    '-f', 'image2pipe',
                    '-vcodec', 'mjpeg',
                    '-q:v', '5',            # Quality (2-31, lower is better)
                    '-'
                ]

                logger.debug(f"Starting FFmpeg for {camera_id}: {' '.join(ffmpeg_cmd)}")

                process = subprocess.Popen(
                    ffmpeg_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=10**6  # 1MB buffer
                )
                capture_info['process'] = process

                # Read JPEG frames from FFmpeg stdout
                # JPEG files start with FFD8 and end with FFD9
                buffer = b''
                read_count = 0

                logger.debug(f"MediaServer MJPEG {camera_id}: Starting frame read loop")

                while not stop_flag.is_set() and process.poll() is None:
                    chunk = process.stdout.read(4096)
                    read_count += 1

                    if not chunk:
                        logger.debug(f"MediaServer MJPEG {camera_id}: Empty chunk after {read_count} reads, buffer={len(buffer)} bytes")
                        break

                    buffer += chunk

                    if read_count <= 5:
                        logger.debug(f"MediaServer MJPEG {camera_id}: Read #{read_count}: {len(chunk)} bytes, buffer now {len(buffer)} bytes")

                    # Find JPEG boundaries (FFD8...FFD9)
                    while True:
                        start = buffer.find(b'\xff\xd8')
                        if start == -1:
                            # No start marker, clear buffer up to last 2 bytes
                            buffer = buffer[-2:] if len(buffer) > 2 else buffer
                            break

                        end = buffer.find(b'\xff\xd9', start + 2)
                        if end == -1:
                            # No end marker yet, keep buffer from start
                            buffer = buffer[start:]
                            break

                        # Extract complete JPEG frame
                        frame = buffer[start:end + 2]
                        buffer = buffer[end + 2:]

                        # Store in shared buffer
                        with self.lock:
                            frame_num = capture_info['frame_count']
                            self.frame_buffers[camera_id] = {
                                'data': frame,
                                'timestamp': time.time(),
                                'frame_number': frame_num,
                                'size': len(frame),
                                'is_error': False  # Real frame, not error
                            }
                            capture_info['frame_count'] += 1
                            capture_info['last_frame_time'] = time.time()
                            capture_info['last_error'] = None

                            # Log first few frames
                            if frame_num < 3:
                                logger.info(f"MediaServer MJPEG {camera_id}: Stored frame #{frame_num} ({len(frame)} bytes)")

                        # Reset error counter on successful frame
                        if consecutive_errors > 0:
                            consecutive_errors = 0
                            if tracker:
                                tracker.update_mjpeg_capture_state(camera_id, active=True)

                # Process ended - check why
                if process.poll() is not None:
                    stderr = process.stderr.read().decode('utf-8', errors='ignore')
                    if stderr:
                        logger.warning(f"FFmpeg stderr for {camera_id}: {stderr[:500]}")

                    consecutive_errors += 1
                    total_errors += 1
                    error_msg = f"Stream unavailable (attempt {consecutive_errors}, total {total_errors})"

                    # Parse common error patterns for better messages
                    if "404 Not Found" in stderr:
                        error_msg = f"Stream not published yet (attempt {consecutive_errors}/{total_errors})"
                    elif "400 Bad Request" in stderr:
                        error_msg = f"MediaMTX rejected request (attempt {consecutive_errors}/{total_errors})"
                    elif "Connection refused" in stderr:
                        error_msg = f"MediaMTX not reachable (attempt {consecutive_errors}/{total_errors})"

                    capture_info['last_error'] = error_msg

                    # Update buffer with error frame so clients see what's wrong
                    with self.lock:
                        error_frame = _create_error_frame(f"{camera_name}: {error_msg}")
                        self.frame_buffers[camera_id] = {
                            'data': error_frame,
                            'timestamp': time.time(),
                            'frame_number': capture_info['frame_count'],
                            'size': len(error_frame),
                            'is_error': True
                        }

                    if consecutive_errors >= max_consecutive_errors and tracker:
                        tracker.update_mjpeg_capture_state(camera_id, active=False, error=error_msg)

                    # Stop entirely after too many total errors
                    if total_errors >= max_total_errors:
                        logger.error(f"MediaServer MJPEG {camera_id}: Stopping after {total_errors} total failures")
                        break

                    # Wait before retry (exponential backoff starting at 4s, capped at 30s)
                    wait_time = min(4 * (2 ** consecutive_errors), 30)
                    logger.warning(f"MediaServer MJPEG capture for {camera_id} failed, retrying in {wait_time}s")
                    stop_flag.wait(wait_time)

            except Exception as e:
                error_msg = str(e)
                capture_info['last_error'] = error_msg
                consecutive_errors += 1
                total_errors += 1
                logger.error(f"MediaServer MJPEG capture error for {camera_id}: {e}")

                # Update buffer with error frame
                with self.lock:
                    error_frame = _create_error_frame(f"{camera_name}: Error - {error_msg[:50]}")
                    self.frame_buffers[camera_id] = {
                        'data': error_frame,
                        'timestamp': time.time(),
                        'frame_number': capture_info['frame_count'],
                        'size': len(error_frame),
                        'is_error': True
                    }

                if consecutive_errors >= max_consecutive_errors and tracker:
                    tracker.update_mjpeg_capture_state(camera_id, active=False, error=error_msg)

                # Stop entirely after too many total errors
                if total_errors >= max_total_errors:
                    logger.error(f"MediaServer MJPEG {camera_id}: Stopping after {total_errors} total failures (exception)")
                    break

                stop_flag.wait(5.0)

            finally:
                # Cleanup process
                if capture_info.get('process'):
                    try:
                        capture_info['process'].terminate()
                        capture_info['process'].wait(timeout=2)
                    except:
                        try:
                            capture_info['process'].kill()
                        except:
                            pass
                    capture_info['process'] = None

        # Report capture stopped
        if tracker:
            tracker.update_mjpeg_capture_state(camera_id, active=False)

        logger.info(f"MediaServer MJPEG capture loop ended for {camera_id}")

    def add_client(self, camera_id: str, camera_config: dict) -> bool:
        """
        Add client for camera stream, start capture if needed.

        Args:
            camera_id: Camera serial number
            camera_config: Camera configuration dict

        Returns:
            True if successful
        """
        try:
            with self.lock:
                self.client_counts[camera_id] += 1
                client_count = self.client_counts[camera_id]

                # Always ensure frame buffer has something for immediate response
                # This prevents race conditions when multiple clients connect simultaneously
                if camera_id not in self.frame_buffers:
                    camera_name = camera_config.get('name', camera_id)
                    connecting_frame = _create_error_frame(f"Connecting to {camera_name}...")
                    self.frame_buffers[camera_id] = {
                        'data': connecting_frame,
                        'timestamp': time.time(),
                        'frame_number': 0,
                        'size': len(connecting_frame),
                        'is_error': True
                    }
                    logger.debug(f"MediaServer MJPEG {camera_id}: Pre-initialized frame buffer for client")

            logger.info(f"Added MediaServer MJPEG client for {camera_id} (total: {client_count})")

            # Start capture if first client (or if capture somehow stopped)
            if client_count == 1 or camera_id not in self.active_captures:
                if not self.start_capture(camera_id, camera_config):
                    with self.lock:
                        self.client_counts[camera_id] -= 1
                    return False

            return True

        except Exception as e:
            logger.error(f"Error adding MediaServer MJPEG client for {camera_id}: {e}")
            return False

    def remove_client(self, camera_id: str):
        """
        Remove client from camera stream.

        NOTE: Does NOT stop capture when client count reaches 0.
        MJPEG captures are pre-warmed at startup and kept running for instant
        reconnect. This trades ~3% CPU per camera for instant MJPEG loading.
        """
        try:
            with self.lock:
                if camera_id in self.client_counts and self.client_counts[camera_id] > 0:
                    self.client_counts[camera_id] -= 1
                    client_count = self.client_counts[camera_id]

                    logger.info(f"Removed MediaServer MJPEG client for {camera_id} (remaining: {client_count})")

                    if client_count <= 0:
                        # Don't stop capture - keep MJPEG running for instant reconnect
                        # Captures are pre-warmed at startup and should stay running
                        logger.debug(f"MediaServer MJPEG {camera_id}: No clients, keeping capture alive for instant reconnect")
                        if camera_id in self.client_counts:
                            del self.client_counts[camera_id]

        except Exception as e:
            logger.error(f"Error removing MediaServer MJPEG client for {camera_id}: {e}")

    def _stop_capture(self, camera_id: str):
        """Stop FFmpeg capture process for camera."""
        try:
            if camera_id in self.active_captures:
                capture_info = self.active_captures[camera_id]

                logger.info(f"Stopping MediaServer MJPEG capture for {camera_id}")

                # Signal stop
                capture_info['stop_flag'].set()

                # Terminate FFmpeg process
                if capture_info.get('process'):
                    try:
                        capture_info['process'].terminate()
                        capture_info['process'].wait(timeout=2)
                    except:
                        try:
                            capture_info['process'].kill()
                        except:
                            pass

                # Wait for thread
                if capture_info['thread'] and capture_info['thread'].is_alive():
                    capture_info['thread'].join(timeout=5)

                # Cleanup
                del self.active_captures[camera_id]
                if camera_id in self.frame_buffers:
                    del self.frame_buffers[camera_id]

                logger.info(f"Stopped MediaServer MJPEG capture for {camera_id}")

        except Exception as e:
            logger.error(f"Error stopping MediaServer MJPEG capture for {camera_id}: {e}")

    def get_latest_frame(self, camera_id: str) -> Optional[dict]:
        """Get latest frame data for camera."""
        with self.lock:
            frame_data = self.frame_buffers.get(camera_id)

            if not frame_data:
                return None

            # For error frames, allow longer display (30s) since they're informational
            # For real frames, 5 second timeout
            max_age = 30.0 if frame_data.get('is_error') else 5.0
            if (time.time() - frame_data['timestamp']) > max_age:
                return None

            return frame_data

    def get_status(self, camera_id: str) -> Optional[dict]:
        """Get capture status for camera."""
        with self.lock:
            if camera_id in self.active_captures:
                capture_info = self.active_captures[camera_id]
                frame_info = self.frame_buffers.get(camera_id, {})

                return {
                    'camera_id': camera_id,
                    'camera_name': capture_info['camera_name'],
                    'active': True,
                    'clients': self.client_counts.get(camera_id, 0),
                    'start_time': capture_info['start_time'],
                    'uptime': time.time() - capture_info['start_time'],
                    'frame_count': capture_info['frame_count'],
                    'last_frame_time': capture_info.get('last_frame_time', 0),
                    'frame_age': time.time() - capture_info.get('last_frame_time', time.time()),
                    'last_error': capture_info['last_error'],
                    'frame_size': frame_info.get('size', 0),
                    'rtsp_url': capture_info['rtsp_url'],
                    'thread_alive': capture_info['thread'].is_alive() if capture_info['thread'] else False,
                    'process_running': capture_info['process'].poll() is None if capture_info.get('process') else False
                }
            return None

    def get_all_status(self) -> dict:
        """Get status for all active captures."""
        status = {}
        # Get list of camera IDs while holding lock, then build status outside lock
        # to avoid deadlock (get_status also acquires lock)
        with self.lock:
            camera_ids = list(self.active_captures.keys())

        for camera_id in camera_ids:
            status[camera_id] = self.get_status(camera_id)
        return status

    def is_capture_active(self, camera_id: str) -> bool:
        """Check if capture is active for camera."""
        with self.lock:
            return camera_id in self.active_captures

    def cleanup(self):
        """Stop all captures and cleanup."""
        logger.info("Cleaning up MediaServer MJPEG service")

        with self.lock:
            camera_ids = list(self.active_captures.keys())

        for camera_id in camera_ids:
            try:
                self._stop_capture(camera_id)
            except Exception as e:
                logger.error(f"Error stopping capture for {camera_id} during cleanup: {e}")

        with self.lock:
            self.client_counts.clear()

        logger.info("MediaServer MJPEG service cleanup complete")


# Global instance
mediaserver_mjpeg_service = MediaServerMJPEGService()
