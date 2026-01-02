"""
Segment Buffer Service
Location: ~/0_NVR/services/recording/segment_buffer.py

Maintains rolling video segment buffers for pre-detection recording.
Uses FFmpeg segment muxer to continuously capture short video segments
that can be concatenated with live recording when motion is detected.
"""

import subprocess
import threading
import logging
import time
import shutil
from pathlib import Path
from typing import Dict, Optional, List
from collections import deque

logger = logging.getLogger(__name__)


class SegmentBuffer:
    """
    Manages rolling segment buffer for a single camera.

    Uses FFmpeg segment muxer to write continuous 5-second segments.
    Maintains deque of segment paths for concatenation on motion trigger.

    Storage: /recordings/buffer/{camera_id}/
    Segments: seg_YYYYMMDD_HHMMSS.ts (transport stream for seamless concat)
    """

    SEGMENT_DURATION = 5  # seconds per segment
    SEGMENT_FORMAT = "mpegts"  # Use TS for lossless concatenation

    def __init__(self,
                 camera_id: str,
                 camera_name: str,
                 source_url: str,
                 max_buffer_seconds: int = 60,
                 buffer_base_path: str = "/recordings/buffer",
                 use_udp: bool = False):
        """
        Initialize segment buffer for camera.

        Args:
            camera_id: Camera identifier (serial number)
            camera_name: Human-readable camera name for logging
            source_url: RTSP source URL (MediaMTX or direct camera)
            max_buffer_seconds: Maximum seconds to buffer (determines segment count)
            buffer_base_path: Base directory for segment storage
            use_udp: If True, use UDP transport (required for Neolink sources)
        """
        self.camera_id = camera_id
        self.camera_name = camera_name
        self.source_url = source_url
        self.max_buffer_seconds = max_buffer_seconds
        self.max_segments = max(1, max_buffer_seconds // self.SEGMENT_DURATION)
        self.use_udp = use_udp

        # Segment storage path
        self.buffer_path = Path(buffer_base_path) / camera_id
        self.buffer_path.mkdir(parents=True, exist_ok=True)

        # Rolling segment deque (FIFO) - automatically evicts oldest when full
        self.segments: deque = deque(maxlen=self.max_segments)
        self.segments_lock = threading.RLock()

        # FFmpeg process management
        self.process: Optional[subprocess.Popen] = None
        self.running = False
        self.monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        logger.info(f"SegmentBuffer initialized for {camera_name} ({camera_id}): "
                   f"max {max_buffer_seconds}s ({self.max_segments} segments)")

    def start(self) -> bool:
        """
        Start continuous segment recording.

        Launches FFmpeg process with segment muxer to write rolling .ts files.

        Returns:
            True if started successfully, False otherwise
        """
        if self.running:
            logger.warning(f"Segment buffer already running for {self.camera_name}")
            return True

        try:
            # Clean up any old segments from previous runs
            self._cleanup_old_segments()

            # Build FFmpeg command for segment output
            # strftime format for segment filenames with timestamp
            segment_pattern = str(self.buffer_path / "seg_%Y%m%d_%H%M%S.ts")

            # Build base command with reconnect flags for stream resilience
            # FFmpeg 7.1.3 supports full reconnect functionality
            cmd = [
                'ffmpeg',
                '-rtsp_transport', 'tcp',
                # Reconnect flags for handling temporary stream disconnects
                '-reconnect', '1',
                '-reconnect_at_eof', '1',
                '-reconnect_streamed', '1',
                '-reconnect_on_network_error', '1',
                '-reconnect_delay_max', '5',  # Max 5 seconds between reconnect attempts
                '-i', self.source_url,
                '-c', 'copy',  # Stream copy - no re-encoding for speed
                '-f', 'segment',
                '-segment_time', str(self.SEGMENT_DURATION),
                '-segment_format', self.SEGMENT_FORMAT,
                '-strftime', '1',  # Enable strftime for segment names
                '-reset_timestamps', '1',  # Reset timestamps per segment
                '-segment_list', str(self.buffer_path / 'segments.txt'),
                '-segment_list_type', 'flat',
                '-y',  # Overwrite output files
                segment_pattern
            ]

            logger.info(f"Starting segment buffer for {self.camera_name}")
            logger.debug(f"FFmpeg command: {' '.join(cmd)}")

            # Start FFmpeg process
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL
            )

            self.running = True
            self._stop_event.clear()

            # Start segment monitor thread to track new segments and cleanup old ones
            self.monitor_thread = threading.Thread(
                target=self._monitor_segments,
                daemon=True,
                name=f"SegmentMonitor-{self.camera_id[:8]}"
            )
            self.monitor_thread.start()

            logger.info(f"Segment buffer started for {self.camera_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to start segment buffer for {self.camera_name}: {e}")
            return False

    def stop(self):
        """
        Stop segment buffer and cleanup.

        Terminates FFmpeg process and removes all buffered segments.
        """
        if not self.running:
            return

        logger.info(f"Stopping segment buffer for {self.camera_name}")
        self.running = False
        self._stop_event.set()

        # Terminate FFmpeg process
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning(f"FFmpeg for {self.camera_name} didn't terminate, killing...")
                self.process.kill()
                self.process.wait(timeout=2)
            except Exception as e:
                logger.error(f"Error stopping FFmpeg for {self.camera_name}: {e}")
            finally:
                self.process = None

        # Wait for monitor thread to finish
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=3)

        # Clean up all segments
        self._cleanup_old_segments()

        logger.info(f"Segment buffer stopped for {self.camera_name}")

    def get_buffer_segments(self, seconds: int) -> List[Path]:
        """
        Get most recent segments covering specified duration.

        Args:
            seconds: Number of seconds of footage needed

        Returns:
            List of segment file paths (oldest first for concatenation order)
        """
        segments_needed = max(1, (seconds + self.SEGMENT_DURATION - 1) // self.SEGMENT_DURATION)

        with self.segments_lock:
            # Get most recent N segments from the deque
            recent = list(self.segments)[-segments_needed:]

            # Filter to only existing files (in case of race condition)
            existing = [s for s in recent if s.exists()]

            logger.info(f"Retrieved {len(existing)} buffer segments for {self.camera_name} "
                       f"({seconds}s requested, {len(recent)} in buffer)")
            return existing

    def copy_buffer_to_temp(self, seconds: int, temp_dir: Path) -> List[Path]:
        """
        Copy pre-buffer segments to temporary directory for concatenation.

        This prevents segments from being deleted during recording.

        Args:
            seconds: Number of seconds of footage needed
            temp_dir: Temporary directory to copy segments to

        Returns:
            List of copied segment paths (in temp_dir)
        """
        segments = self.get_buffer_segments(seconds)

        if not segments:
            logger.warning(f"No buffer segments available for {self.camera_name}")
            return []

        temp_dir.mkdir(parents=True, exist_ok=True)
        copied_segments = []

        for i, seg in enumerate(segments):
            try:
                dest = temp_dir / f"prebuf_{i:03d}.ts"
                shutil.copy2(seg, dest)
                copied_segments.append(dest)
            except Exception as e:
                logger.error(f"Failed to copy segment {seg}: {e}")

        logger.info(f"Copied {len(copied_segments)} pre-buffer segments for {self.camera_name}")
        return copied_segments

    def _monitor_segments(self):
        """
        Monitor segment directory and maintain rolling buffer.

        Runs in background thread, tracks new segments, deletes old ones.
        """
        logger.info(f"Segment monitor started for {self.camera_name}")

        last_segment_count = 0
        last_health_log = time.time()

        while self.running and not self._stop_event.is_set():
            try:
                # Find all segment files sorted by modification time
                segment_files = sorted(
                    self.buffer_path.glob("seg_*.ts"),
                    key=lambda p: p.stat().st_mtime
                )

                # Update segment deque with new segments
                with self.segments_lock:
                    for seg_file in segment_files:
                        if seg_file not in self.segments:
                            self.segments.append(seg_file)
                            logger.debug(f"New segment: {seg_file.name}")

                    # Remove deleted/missing segments from tracking
                    current_set = set(segment_files)
                    self.segments = deque(
                        [s for s in self.segments if s in current_set],
                        maxlen=self.max_segments
                    )

                # Delete segments beyond our buffer limit (deque handles tracking,
                # but files remain on disk until explicitly deleted)
                segment_count = len(segment_files)
                if segment_count > self.max_segments + 2:  # +2 buffer for race conditions
                    for old_seg in segment_files[:-self.max_segments]:
                        try:
                            old_seg.unlink()
                            logger.debug(f"Deleted old segment: {old_seg.name}")
                        except Exception as e:
                            logger.warning(f"Failed to delete segment {old_seg}: {e}")

                # Periodic health logging (every 60 seconds)
                if time.time() - last_health_log > 60:
                    logger.debug(f"Segment buffer {self.camera_name}: "
                                f"{len(self.segments)}/{self.max_segments} segments")
                    last_health_log = time.time()

                # Check if FFmpeg is still running
                if self.process and self.process.poll() is not None:
                    exit_code = self.process.returncode
                    logger.warning(f"FFmpeg exited with code {exit_code} for {self.camera_name}, restarting...")

                    # Auto-restart FFmpeg after short delay
                    time.sleep(5)
                    if not self._stop_event.is_set() and self.running:
                        if self._restart_ffmpeg():
                            logger.info(f"FFmpeg restarted successfully for {self.camera_name}")
                        else:
                            logger.error(f"FFmpeg restart failed for {self.camera_name}")
                            break

                time.sleep(1)  # Check every second

            except Exception as e:
                logger.error(f"Segment monitor error for {self.camera_name}: {e}")
                time.sleep(5)

        logger.info(f"Segment monitor stopped for {self.camera_name}")

    def _cleanup_old_segments(self):
        """Remove all segments from buffer directory."""
        try:
            for seg_file in self.buffer_path.glob("seg_*.ts"):
                seg_file.unlink()

            # Also remove segment list file
            segment_list = self.buffer_path / "segments.txt"
            if segment_list.exists():
                segment_list.unlink()

            with self.segments_lock:
                self.segments.clear()

            logger.debug(f"Cleaned up segments for {self.camera_name}")

        except Exception as e:
            logger.error(f"Segment cleanup error for {self.camera_name}: {e}")

    def _restart_ffmpeg(self) -> bool:
        """
        Restart FFmpeg process for segment buffer.

        Called by monitor thread when FFmpeg exits unexpectedly.

        Returns:
            True if restarted successfully, False otherwise
        """
        try:
            # Clean up any old process
            if self.process:
                try:
                    self.process.kill()
                except:
                    pass
                self.process = None

            # Build FFmpeg command (same as in start())
            segment_pattern = str(self.buffer_path / "seg_%Y%m%d_%H%M%S.ts")

            # Include reconnect flags for stream resilience (FFmpeg 7.1.3+)
            cmd = [
                'ffmpeg',
                '-rtsp_transport', 'tcp',
                '-reconnect', '1',
                '-reconnect_at_eof', '1',
                '-reconnect_streamed', '1',
                '-reconnect_on_network_error', '1',
                '-reconnect_delay_max', '5',
                '-i', self.source_url,
                '-c', 'copy',
                '-f', 'segment',
                '-segment_time', str(self.SEGMENT_DURATION),
                '-segment_format', self.SEGMENT_FORMAT,
                '-strftime', '1',
                '-reset_timestamps', '1',
                '-segment_list', str(self.buffer_path / 'segments.txt'),
                '-segment_list_type', 'flat',
                '-y',
                segment_pattern
            ]

            logger.debug(f"Restarting FFmpeg for {self.camera_name}")

            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL
            )

            return True

        except Exception as e:
            logger.error(f"Failed to restart FFmpeg for {self.camera_name}: {e}")
            return False

    def is_healthy(self) -> bool:
        """
        Check if segment buffer is healthy (FFmpeg running, recent segments exist).

        Returns:
            True if buffer is operating normally
        """
        if not self.running:
            return False

        if not self.process or self.process.poll() is not None:
            return False

        # Check for recent segment activity
        with self.segments_lock:
            if not self.segments:
                return False

            # Most recent segment should be less than 15 seconds old
            latest = self.segments[-1] if self.segments else None
            if latest and latest.exists():
                age = time.time() - latest.stat().st_mtime
                return age < 15

        return False

    def get_status(self) -> Dict:
        """
        Get status information for this buffer.

        Returns:
            Dict with buffer status information
        """
        with self.segments_lock:
            return {
                'camera_id': self.camera_id,
                'camera_name': self.camera_name,
                'running': self.running,
                'healthy': self.is_healthy(),
                'segment_count': len(self.segments),
                'max_segments': self.max_segments,
                'buffer_seconds': len(self.segments) * self.SEGMENT_DURATION,
                'max_buffer_seconds': self.max_buffer_seconds,
                'ffmpeg_running': self.process is not None and self.process.poll() is None
            }


class SegmentBufferManager:
    """
    Manages segment buffers for multiple cameras.

    Singleton-like manager that creates/destroys buffers based on
    camera configuration. Only cameras with pre_buffer_enabled=True
    will have active segment buffers.
    """

    def __init__(self, recording_config, camera_repo):
        """
        Initialize segment buffer manager.

        Args:
            recording_config: RecordingConfig instance for reading camera settings
            camera_repo: CameraRepository instance for camera info
        """
        self.config = recording_config
        self.camera_repo = camera_repo

        self.buffers: Dict[str, SegmentBuffer] = {}
        self.buffers_lock = threading.RLock()

        logger.info("SegmentBufferManager initialized")

    def start_buffer(self, camera_id: str, source_url: str) -> bool:
        """
        Start segment buffer for camera if pre-buffer is enabled.

        Args:
            camera_id: Camera identifier
            source_url: RTSP source URL for recording

        Returns:
            True if buffer started (or already running), False if disabled or failed
        """
        # Check if pre-buffer is enabled for this camera
        if not self.config.is_pre_buffer_enabled(camera_id):
            logger.debug(f"Pre-buffer not enabled for {camera_id}")
            return False

        pre_buffer_sec = self.config.get_pre_buffer_seconds(camera_id)
        if pre_buffer_sec <= 0:
            logger.debug(f"Pre-buffer seconds is 0 for {camera_id}")
            return False

        # Get camera name for logging
        camera = self.camera_repo.get_camera(camera_id)
        camera_name = camera.get('name', camera_id) if camera else camera_id

        with self.buffers_lock:
            # Stop existing buffer if any
            if camera_id in self.buffers:
                self.buffers[camera_id].stop()

            # Create new buffer with extra margin for safety
            buffer = SegmentBuffer(
                camera_id=camera_id,
                camera_name=camera_name,
                source_url=source_url,
                max_buffer_seconds=pre_buffer_sec + 10  # Extra buffer for timing margin
            )

            if buffer.start():
                self.buffers[camera_id] = buffer
                logger.info(f"Pre-buffer started for {camera_name} ({pre_buffer_sec}s)")
                return True
            else:
                logger.error(f"Failed to start pre-buffer for {camera_name}")
                return False

    def stop_buffer(self, camera_id: str):
        """
        Stop segment buffer for camera.

        Args:
            camera_id: Camera identifier
        """
        with self.buffers_lock:
            if camera_id in self.buffers:
                self.buffers[camera_id].stop()
                del self.buffers[camera_id]
                logger.info(f"Buffer stopped for {camera_id}")

    def stop_all(self):
        """Stop all segment buffers."""
        with self.buffers_lock:
            for camera_id in list(self.buffers.keys()):
                self.buffers[camera_id].stop()
            self.buffers.clear()
        logger.info("All segment buffers stopped")

    def get_pre_buffer_segments(self, camera_id: str, temp_dir: Path) -> List[Path]:
        """
        Get pre-buffer segments for camera, copied to temp directory.

        Args:
            camera_id: Camera identifier
            temp_dir: Temporary directory to copy segments to

        Returns:
            List of copied segment paths, or empty list if no buffer
        """
        with self.buffers_lock:
            if camera_id not in self.buffers:
                logger.debug(f"No buffer running for {camera_id}")
                return []

            # Get configured pre-buffer duration
            pre_buffer_sec = self.config.get_pre_buffer_seconds(camera_id)

            return self.buffers[camera_id].copy_buffer_to_temp(pre_buffer_sec, temp_dir)

    def is_buffer_running(self, camera_id: str) -> bool:
        """Check if buffer is running for camera."""
        with self.buffers_lock:
            return camera_id in self.buffers and self.buffers[camera_id].running

    def is_buffer_healthy(self, camera_id: str) -> bool:
        """Check if buffer is healthy for camera."""
        with self.buffers_lock:
            if camera_id not in self.buffers:
                return False
            return self.buffers[camera_id].is_healthy()

    def get_status(self) -> Dict[str, Dict]:
        """
        Get status of all buffers.

        Returns:
            Dict mapping camera_id to status info
        """
        status = {}
        with self.buffers_lock:
            for camera_id, buffer in self.buffers.items():
                status[camera_id] = buffer.get_status()
        return status

    def get_buffer_count(self) -> int:
        """Get number of active buffers."""
        with self.buffers_lock:
            return len(self.buffers)
