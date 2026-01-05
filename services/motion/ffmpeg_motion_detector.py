"""
FFmpeg Motion Detector
Location: ~/0_NVR/services/motion/ffmpeg_motion_detector.py

Analyzes video streams for motion using FFmpeg's scene detection filter.
"""

import subprocess
import threading
import logging
import time
import re
import socket
from typing import Dict, Callable, Optional

logger = logging.getLogger(__name__)


class FFmpegMotionDetector:
    """
    Video analysis-based motion detection using FFmpeg scene detection filter.

    Uses FFmpeg's 'select' filter with scene change detection to identify
    motion events without relying on camera-specific protocols like ONVIF.

    Works with any camera that provides an RTSP stream.
    """

    def __init__(self, camera_repository, recording_service, recording_config=None, camera_state_tracker=None):
        """
        Initialize FFmpeg motion detector.

        Args:
            camera_repository: CameraRepository instance
            recording_service: RecordingService instance
            recording_config: Optional RecordingConfig instance for camera settings
            camera_state_tracker: Optional CameraStateTracker instance for health checks
        """
        self.camera_repo = camera_repository
        self.recording_service = recording_service
        self.recording_config = recording_config
        self._state_tracker = camera_state_tracker
        self.active_detectors: Dict[str, Dict] = {}
        self.detector_threads: Dict[str, threading.Thread] = {}
        self.ffmpeg_processes: Dict[str, subprocess.Popen] = {}
        self._stop_event = threading.Event()

        logger.info("FFmpeg Motion Detector initialized")


    def start_detector(self, camera_id: str, sensitivity: float = 0.3) -> bool:
        """
        Start motion detection for a camera.

        Args:
            camera_id: Camera identifier
            sensitivity: Detection sensitivity (0.0-1.0, lower = more sensitive)
                        Default 0.3 = detect 30% scene change

        Returns:
            True if detector started successfully
        """
        # Verify camera exists
        camera = self.camera_repo.get_camera(camera_id)
        if not camera:
            logger.error(f"Camera not found: {camera_id}")
            return False

        # Check if already detecting
        if camera_id in self.active_detectors:
            logger.info(f"Already detecting motion for {camera_id}")
            return True

        # Get camera-specific settings from recording config
        cooldown_sec = 60
        if self.recording_config:
            camera_cfg = self.recording_config.get_camera_config(camera_id)
            motion_cfg = camera_cfg.get('motion_recording', {})
            cooldown_sec = motion_cfg.get('cooldown_sec', 60)
            # Allow config to override sensitivity
            sensitivity = motion_cfg.get('ffmpeg_sensitivity', sensitivity)

        # For LL_HLS/NEOLINK/WEBRTC cameras reading from MediaMTX, use much lower threshold
        # Re-encoded streams have very low scene scores due to scenecut=0 in encoder
        # Tested scores: mostly 0.0005-0.002 with spikes to 0.005 during movement
        # WEBRTC uses the same FFmpeg→MediaMTX pipeline, just different browser delivery
        stream_type = camera.get('stream_type', '').upper()
        if stream_type in ('LL_HLS', 'NEOLINK', 'WEBRTC') and sensitivity >= 0.1:
            # Only auto-adjust if not explicitly configured to a low value
            default_ll_hls_sensitivity = 0.002  # 0.2% scene change threshold (Eufy cameras produce very low scores ~0.001)
            logger.info(f"MediaMTX camera ({stream_type}) detected, adjusting sensitivity from {sensitivity} to {default_ll_hls_sensitivity}")
            sensitivity = default_ll_hls_sensitivity

        camera_name = camera.get('name', camera_id)
        logger.info(f"Starting FFmpeg motion detector for {camera_name} "
                   f"(sensitivity: {sensitivity}, cooldown: {cooldown_sec}s)")

        # Add to active_detectors BEFORE starting thread to avoid race condition
        # where thread checks membership before we add it
        self.active_detectors[camera_id] = {
            'sensitivity': sensitivity,
            'cooldown_sec': cooldown_sec,
            'last_motion': 0,
            'camera_name': camera_name,
            'started_at': time.time()
        }

        # Start detector thread
        thread = threading.Thread(
            target=self._detection_loop,
            args=(camera_id, camera, sensitivity, cooldown_sec),
            daemon=True,
            name=f"FFmpegMotion-{camera_id[:8]}"
        )
        thread.start()
        self.detector_threads[camera_id] = thread

        return True


    def stop_detector(self, camera_id: str):
        """
        Stop motion detection for a camera.

        Args:
            camera_id: Camera identifier
        """
        if camera_id in self.active_detectors:
            self.active_detectors.pop(camera_id)

            # Kill FFmpeg process if running
            if camera_id in self.ffmpeg_processes:
                try:
                    process = self.ffmpeg_processes[camera_id]
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
                except Exception as e:
                    logger.error(f"Error stopping FFmpeg for {camera_id}: {e}")
                finally:
                    self.ffmpeg_processes.pop(camera_id, None)

            logger.info(f"Stopped FFmpeg detector for {camera_id}")


    def stop_all(self):
        """Stop all active detectors."""
        self._stop_event.set()

        for camera_id in list(self.active_detectors.keys()):
            self.stop_detector(camera_id)

        logger.info("Stopped all FFmpeg detectors")


    def _check_mediamtx_path_ready(self, camera_id: str, timeout: int = 2) -> bool:
        """
        Check if a camera's MediaMTX path is ready and has an active publisher.

        Uses CameraStateTracker (if available) to check publisher_active status
        without creating additional RTSP connections. Falls back to ffprobe if
        no state tracker is configured.

        Args:
            camera_id: Camera serial number (used as MediaMTX path)
            timeout: Timeout in seconds for fallback ffprobe check

        Returns:
            True if path is ready, False otherwise
        """
        # Use CameraStateTracker if available - no extra RTSP connections needed
        if self._state_tracker:
            try:
                state = self._state_tracker.get_camera_state(camera_id)
                if state.publisher_active:
                    return True
                else:
                    logger.debug(f"CameraStateTracker reports publisher inactive for {camera_id}")
                    return False
            except Exception as e:
                logger.warning(f"Error checking state tracker for {camera_id}: {e}")
                # Fall through to ffprobe fallback

        # Fallback: ffprobe check (creates RTSP connection - not ideal)
        rtsp_url = f"rtsp://nvr-packager:8554/{camera_id}"
        cmd = [
            'ffprobe',
            '-v', 'error',
            '-rtsp_transport', 'tcp',
            '-timeout', str(timeout * 1000000),  # microseconds
            '-i', rtsp_url,
            '-show_entries', 'stream=codec_type',
            '-of', 'default=noprint_wrappers=1'
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, timeout=timeout + 1, text=True)
            return result.returncode == 0
        except (subprocess.TimeoutExpired, Exception):
            return False

    def _get_camera_rtsp_url(self, camera: Dict) -> Optional[str]:
        """
        Get RTSP URL for camera using appropriate stream handler.

        For cameras using LL_HLS streaming, returns MediaMTX RTSP URL
        (single connection to camera, multiple readers from MediaMTX).
        For other cameras, returns direct camera RTSP URL.

        Args:
            camera: Camera configuration dict

        Returns:
            RTSP URL string or None if unable to build URL
        """
        camera_type = camera.get('type', '').lower()
        stream_type = camera.get('stream_type', '').upper()

        # For LL_HLS/NEOLINK/WEBRTC cameras, use MediaMTX RTSP output
        # This avoids opening a second connection to the camera
        # NEOLINK and WEBRTC route through MediaMTX (same FFmpeg→MediaMTX pipeline)
        if stream_type in ('LL_HLS', 'NEOLINK', 'WEBRTC'):
            packager_path = camera.get('packager_path') or camera.get('serial')
            if packager_path:
                mediamtx_url = f"rtsp://nvr-packager:8554/{packager_path}"
                logger.info(f"Using MediaMTX RTSP for {camera.get('name')}: {mediamtx_url}")
                return mediamtx_url
            else:
                logger.warning(f"{stream_type} camera {camera.get('name')} has no packager_path, falling back to direct RTSP")

        # For other cameras, use direct camera RTSP URL via stream handler
        try:
            if camera_type == 'eufy':
                from streaming.handlers.eufy_stream_handler import EufyStreamHandler
                from services.credentials.eufy_credential_provider import EufyCredentialProvider
                handler = EufyStreamHandler(
                    EufyCredentialProvider(),
                    self.camera_repo.get_eufy_bridge_config()
                )
            elif camera_type == 'unifi':
                from streaming.handlers.unifi_stream_handler import UniFiStreamHandler
                from services.credentials.unifi_credential_provider import UniFiCredentialProvider
                handler = UniFiStreamHandler(
                    UniFiCredentialProvider(),
                    self.camera_repo.get_unifi_protect_config()
                )
            elif camera_type == 'reolink':
                from streaming.handlers.reolink_stream_handler import ReolinkStreamHandler
                from services.credentials.reolink_credential_provider import ReolinkCredentialProvider
                handler = ReolinkStreamHandler(
                    ReolinkCredentialProvider(use_api_credentials=False),
                    self.camera_repo.get_reolink_config()
                )
            elif camera_type == 'amcrest':
                from streaming.handlers.amcrest_stream_handler import AmcrestStreamHandler
                from services.credentials.amcrest_credential_provider import AmcrestCredentialProvider
                handler = AmcrestStreamHandler(
                    AmcrestCredentialProvider(),
                    {}
                )
            elif camera_type == 'sv3c':
                from streaming.handlers.sv3c_stream_handler import SV3CStreamHandler
                from services.credentials.sv3c_credential_provider import SV3CCredentialProvider
                handler = SV3CStreamHandler(
                    SV3CCredentialProvider(),
                    {}
                )
            else:
                logger.error(f"Unknown camera type for FFmpeg motion: {camera_type}")
                return None

            # Use sub stream for motion detection (lower bandwidth)
            return handler.build_rtsp_url(camera, stream_type='sub')

        except Exception as e:
            logger.error(f"Failed to build RTSP URL for {camera.get('serial')}: {e}")
            return None


    def _detection_loop(self, camera_id: str, camera: Dict, sensitivity: float, cooldown_sec: int):
        """
        Main detection loop for a camera.

        Uses FFmpeg with scene detection filter to detect motion.

        Args:
            camera_id: Camera identifier
            camera: Camera configuration
            sensitivity: Detection sensitivity (0.0-1.0)
            cooldown_sec: Cooldown between detections
        """
        camera_name = camera.get('name', camera_id)
        logger.info(f"FFmpeg detection loop started for {camera_name}")

        retry_delay = 5  # Initial retry delay
        max_retry_delay = 60  # Max retry delay

        while camera_id in self.active_detectors and not self._stop_event.is_set():
            try:
                # Get RTSP URL
                rtsp_url = self._get_camera_rtsp_url(camera)
                if not rtsp_url:
                    logger.error(f"Could not get RTSP URL for {camera_name}, retrying...")
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, max_retry_delay)
                    continue

                # For LL_HLS/NEOLINK/WEBRTC cameras, check if MediaMTX path is ready before connecting
                # Uses CameraStateTracker (no extra RTSP connections) if available
                stream_type = camera.get('stream_type', '').upper()
                if stream_type in ('LL_HLS', 'NEOLINK', 'WEBRTC'):
                    if not self._check_mediamtx_path_ready(camera_id):
                        logger.debug(f"MediaMTX path not ready for {camera_name}, waiting {retry_delay}s...")
                        time.sleep(retry_delay)
                        retry_delay = min(retry_delay * 2, max_retry_delay)
                        continue

                # Reset retry delay on successful URL retrieval and path check
                retry_delay = 5

                # Build FFmpeg command for scene detection
                # -vf "select='gt(scene,X)'" filters frames with scene change > X
                # -f null - discards output (we only care about stderr metadata)
                # metadata=print outputs scene scores to stderr
                cmd = [
                    'ffmpeg',
                    '-rtsp_transport', 'tcp',
                    '-i', rtsp_url,
                    '-vf', f"select='gt(scene,{sensitivity})',metadata=print:file=-",
                    '-an',  # No audio
                    '-f', 'null',
                    '-'
                ]

                logger.debug(f"Starting FFmpeg scene detection for {camera_name}")

                # Start FFmpeg process
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,  # Combine stderr into stdout
                    text=True,
                    bufsize=1  # Line buffered
                )

                self.ffmpeg_processes[camera_id] = process

                # Read FFmpeg output for scene detections
                scene_pattern = re.compile(r'lavfi\.scene_score=(\d+\.?\d*)')

                for line in iter(process.stdout.readline, ''):
                    # Check if we should stop
                    if camera_id not in self.active_detectors or self._stop_event.is_set():
                        break

                    # Look for scene change metadata
                    match = scene_pattern.search(line)
                    if match:
                        scene_score = float(match.group(1))
                        logger.debug(f"Scene change detected for {camera_name}: {scene_score:.3f}")

                        # Handle motion detection
                        self._handle_motion_detected(camera_id, scene_score)

                # Process ended - check exit status
                process.wait()
                exit_code = process.returncode

                if camera_id in self.active_detectors:
                    if exit_code != 0:
                        logger.warning(f"FFmpeg exited with code {exit_code} for {camera_name}, restarting...")

                        # Exit code 8 = no stream available - back off more aggressively
                        if exit_code == 8:
                            retry_delay = min(retry_delay * 2, max_retry_delay)
                            logger.debug(f"No stream available for {camera_name}, waiting {retry_delay}s before retry")
                            time.sleep(retry_delay)
                        else:
                            # Other errors - short delay
                            time.sleep(2)
                    else:
                        logger.info(f"FFmpeg process ended normally for {camera_name}")
                        # Reset retry delay on normal exit
                        retry_delay = 5
                        time.sleep(2)

            except Exception as e:
                logger.error(f"FFmpeg detection error for {camera_name}: {e}")
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_retry_delay)

        # Cleanup
        if camera_id in self.ffmpeg_processes:
            try:
                self.ffmpeg_processes[camera_id].terminate()
            except:
                pass
            self.ffmpeg_processes.pop(camera_id, None)

        if camera_id in self.active_detectors:
            self.active_detectors.pop(camera_id, None)

        logger.info(f"FFmpeg detection loop ended for {camera_name}")


    def _handle_motion_detected(self, camera_id: str, scene_score: float = 0):
        """
        Handle motion detection event.

        Args:
            camera_id: Camera that detected motion
            scene_score: FFmpeg scene change score
        """
        # Check debounce (cooldown)
        detector_info = self.active_detectors.get(camera_id, {})
        last_motion = detector_info.get('last_motion', 0)
        cooldown_sec = detector_info.get('cooldown_sec', 60)
        camera_name = detector_info.get('camera_name', camera_id)

        time_since_last = time.time() - last_motion
        if time_since_last < cooldown_sec:
            logger.debug(f"Motion for {camera_name} in cooldown ({time_since_last:.0f}s < {cooldown_sec}s)")
            return

        logger.info(f"Motion detected via FFmpeg for {camera_name} (score: {scene_score:.3f})")

        try:
            # Update last motion time
            if camera_id in self.active_detectors:
                self.active_detectors[camera_id]['last_motion'] = time.time()

            # Trigger recording via recording service
            recording_id = self.recording_service.start_motion_recording(camera_id)

            if recording_id:
                logger.info(f"Started motion recording for {camera_name}: {recording_id}")
            else:
                logger.warning(f"Failed to start motion recording for {camera_name}")

        except Exception as e:
            logger.error(f"Error starting motion recording for {camera_name}: {e}")


    def get_status(self) -> Dict[str, Dict]:
        """
        Get status of all active detectors.

        Returns:
            Dict mapping camera_id to status info
        """
        status = {}

        for camera_id, info in self.active_detectors.items():
            process = self.ffmpeg_processes.get(camera_id)
            process_running = process is not None and process.poll() is None

            status[camera_id] = {
                'camera_name': info.get('camera_name', camera_id),
                'sensitivity': info.get('sensitivity', 0.3),
                'cooldown_sec': info.get('cooldown_sec', 60),
                'last_motion': info.get('last_motion', 0),
                'last_motion_ago_sec': int(time.time() - info.get('last_motion', 0)) if info.get('last_motion', 0) > 0 else None,
                'started_at': info.get('started_at', 0),
                'uptime_sec': int(time.time() - info.get('started_at', time.time())),
                'ffmpeg_running': process_running,
                'active': True
            }

        return status


def create_ffmpeg_detector(camera_repository, recording_service, recording_config=None, camera_state_tracker=None) -> FFmpegMotionDetector:
    """
    Factory function to create FFmpeg detector instance.

    Args:
        camera_repository: CameraRepository instance
        recording_service: RecordingService instance
        recording_config: Optional RecordingConfig instance
        camera_state_tracker: Optional CameraStateTracker instance for health checks

    Returns:
        FFmpegMotionDetector instance
    """
    return FFmpegMotionDetector(camera_repository, recording_service, recording_config, camera_state_tracker)
