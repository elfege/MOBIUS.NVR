"""
Recording Service
Manages FFmpeg recording processes with hybrid source support (MediaMTX, RTSP, MJPEG service).

Supports:
- Motion-triggered recording (event-based)
- Pre-buffer recording (capture footage before motion event)
- Continuous recording (24/7)
- Per-camera configuration
"""

import os
import subprocess
import threading
import logging
import time
import shutil
import requests
from datetime import datetime
from typing import Dict, Optional, List, Tuple
from pathlib import Path

# Import config and storage
import sys
sys.path.append('/app/config')
from recording_config_loader import RecordingConfig
from services.recording.storage_manager import StorageManager
from services.recording.segment_buffer import SegmentBufferManager

logger = logging.getLogger(__name__)


class RecordingService:
    """
    Manages video recording processes with support for multiple source types.
    
    Recording Sources:
    - mediamtx: Tap existing MediaMTX RTSP output (LL_HLS/HLS cameras)
    - rtsp: Direct camera RTSP connection
    
    Supports:
    - Motion-triggered recording (event-based)
    - Continuous recording (24/7)
    - Per-camera configuration
    - Metadata storage via PostgREST
    """
    
    def __init__(self, 
                 camera_repo,
                 storage_manager: Optional[StorageManager] = None,
                 config_path: Optional[str] = None,
                 postgrest_url: str = "http://postgrest:3001"):
        """
        Initialize recording service.
        
        Args:
            camera_repo: CameraRepository instance for camera metadata
            storage_manager: Optional StorageManager (creates new if None)
            config_path: Optional path to recording_settings.json
            postgrest_url: PostgREST API endpoint
        """
        self.camera_repo = camera_repo
        self.config = RecordingConfig(config_path)
        self.storage = storage_manager or StorageManager(config_path)
        self.postgrest_url = postgrest_url

        # Recording process tracking
        self.active_recordings: Dict[str, Dict] = {}  # recording_id -> metadata
        self.recording_lock = threading.RLock()

        # Segment buffer manager for pre-buffer recording
        self.segment_buffer_manager = SegmentBufferManager(self.config, camera_repo)

        logger.info(f"RecordingService initialized - PostgREST: {self.postgrest_url}")
    
    
    def _get_recording_source_url(self, camera_id: str) -> Tuple[str, str]:
        """
        Get recording source URL and type for camera.
        
        Args:
            camera_id: Camera identifier
        
        Returns:
            Tuple of (source_url, source_type)
            source_type: 'mediamtx' | 'rtsp'
        
        Raises:
            ValueError: If camera not found or invalid configuration
        """
        # Get camera configuration
        camera = self.camera_repo.get_camera(camera_id)
        if not camera:
            raise ValueError(f"Camera not found: {camera_id}")
        
        stream_type = (camera.get('stream_type') or '').upper()
        
        # Get recording configuration for this camera
        camera_cfg = self.config.get_camera_config(camera_id, stream_type)
        recording_source = camera_cfg.get('motion_recording', {}).get('recording_source', 'auto')
        
        logger.debug(f"Camera {camera_id}: stream_type={stream_type}, recording_source={recording_source}")
        
        # Resolve source URL based on configuration
        # LL_HLS/HLS/NEOLINK/WEBRTC cameras MUST use MediaMTX (they don't have direct RTSP access)
        # NEOLINK and WEBRTC route through MediaMTX for LL-HLS/WebRTC packaging
        # This overrides any config setting since direct RTSP isn't possible for these cameras
        if stream_type in ('LL_HLS', 'HLS', 'NEOLINK', 'WEBRTC'):
            if recording_source != 'mediamtx':
                logger.debug(f"Overriding recording_source '{recording_source}' to 'mediamtx' for {camera_id} (stream_type={stream_type})")
            recording_source = 'mediamtx'
        elif recording_source == 'auto':
            if stream_type == 'MJPEG':
                # MJPEG cameras use dedicated capture service
                recording_source = 'mjpeg_service'
            else:
                # Fallback to direct RTSP for unknown types
                recording_source = 'rtsp'
            logger.debug(f"Auto-resolved recording_source to '{recording_source}' for {camera_id}")

        if recording_source == 'mediamtx':
            # Tap MediaMTX RTSP output (required for single-connection cameras)
            packager_path = camera.get('packager_path', camera_id)
            return (f"rtsp://nvr-packager:8554/{packager_path}", 'mediamtx')

        elif recording_source == 'rtsp':
            # Direct camera RTSP connection
            handler = self._get_camera_handler(camera)
            rtsp_url = handler.build_rtsp_url(camera, stream_type='main')
            return (rtsp_url, 'rtsp')

        elif recording_source == 'mjpeg_service':
            # MJPEG cameras use dedicated capture service
            # TODO: Implement MJPEG recording by tapping the capture service buffer
            logger.warning(f"MJPEG recording not yet implemented for {camera_id}")
            raise NotImplementedError(f"MJPEG recording source not yet implemented for camera {camera_id}")

        else:
            raise ValueError(f"Unknown recording source: {recording_source}")
        
    def _get_camera_handler(self, camera_config: Dict):
        """Get appropriate stream handler for camera type."""
        camera_type = camera_config.get('type', '').lower()
        
        # Import handlers lazily
        if camera_type == 'eufy':
            from streaming.handlers.eufy_stream_handler import EufyStreamHandler
            from services.credentials.eufy_credential_provider import EufyCredentialProvider
            return EufyStreamHandler(
                EufyCredentialProvider(),
                self.camera_repo.get_eufy_bridge_config()
            )
        elif camera_type == 'unifi':
            from streaming.handlers.unifi_stream_handler import UniFiStreamHandler
            from services.credentials.unifi_credential_provider import UniFiCredentialProvider
            return UniFiStreamHandler(
                UniFiCredentialProvider(),
                self.camera_repo.get_unifi_protect_config()
            )
        elif camera_type == 'reolink':
            from streaming.handlers.reolink_stream_handler import ReolinkStreamHandler
            from services.credentials.reolink_credential_provider import ReolinkCredentialProvider
            return ReolinkStreamHandler(
                ReolinkCredentialProvider(),
                self.camera_repo.get_reolink_config()
            )
        elif camera_type == 'amcrest':
            from streaming.handlers.amcrest_stream_handler import AmcrestStreamHandler
            from services.credentials.amcrest_credential_provider import AmcrestCredentialProvider
            return AmcrestStreamHandler(
                AmcrestCredentialProvider(),
                {} # Amcrest has no vendor config
            )
        elif camera_type == 'sv3c':
            from streaming.handlers.sv3c_stream_handler import SV3CStreamHandler
            from services.credentials.sv3c_credential_provider import SV3CCredentialProvider
            return SV3CStreamHandler(
                SV3CCredentialProvider(),
                {} # SV3C has no vendor config
            )
        else:
            raise ValueError(f"Unknown camera type: {camera_type}")
        
    def start_motion_recording(self, camera_id: str, duration: int = 30, event_id: Optional[str] = None) -> Optional[str]:
        """
        Start motion-triggered recording for camera.

        If pre-buffer is enabled for the camera, captures buffered footage from
        before motion was detected and concatenates it with the live recording.

        Args:
            camera_id: Camera identifier
            duration: Recording duration in seconds (default: 30)
            event_id: Optional motion event ID for linking

        Returns:
            Recording ID (filename without extension) if successful, None if failed
        """
        try:
            # Check if recording is enabled for this camera
            if not self.config.is_recording_enabled(camera_id, 'motion'):
                logger.info(f"Motion recording disabled for {camera_id}")
                return None

            # Get camera configuration
            camera = self.camera_repo.get_camera(camera_id)
            if not camera:
                logger.error(f"Camera not found: {camera_id}")
                return None

            camera_name = camera.get('name', camera_id)

            # Check if pre-buffer is enabled
            pre_buffer_enabled = self.config.is_pre_buffer_enabled(camera_id)
            pre_buffer_sec = self.config.get_pre_buffer_seconds(camera_id) if pre_buffer_enabled else 0

            # Generate recording path with camera name for per-camera directory
            recording_path = self.storage.generate_recording_path(camera_id, 'motion', camera_name)
            recording_id = recording_path.stem  # Filename without extension

            # Get recording source
            source_url, source_type = self._get_recording_source_url(camera_id)

            logger.info(f"Starting motion recording for {camera_name} ({camera_id})")
            logger.info(f"  Source: {source_type}")
            logger.info(f"  Duration: {duration}s")
            logger.info(f"  Pre-buffer: {pre_buffer_sec}s (enabled: {pre_buffer_enabled})")
            logger.info(f"  Output: {recording_path.name}")

            if pre_buffer_enabled and pre_buffer_sec > 0:
                # Use pre-buffer + live recording approach
                return self._start_prebuffered_recording(
                    camera_id, camera_name, source_url, source_type,
                    recording_path, recording_id, duration, event_id,
                    pre_buffer_sec
                )
            else:
                # Standard live-only recording
                return self._start_live_recording(
                    camera_id, camera_name, source_url, source_type,
                    recording_path, recording_id, duration, event_id
                )

        except Exception as e:
            logger.error(f"Failed to start motion recording for {camera_id}: {e}")
            return None

    def _start_live_recording(self, camera_id: str, camera_name: str,
                              source_url: str, source_type: str,
                              recording_path: Path, recording_id: str,
                              duration: int, event_id: Optional[str]) -> Optional[str]:
        """
        Start standard live-only recording (no pre-buffer).

        This is the original recording logic, extracted into a separate method.

        Args:
            camera_id: Camera identifier
            camera_name: Human-readable camera name
            source_url: RTSP source URL
            source_type: Source type ('mediamtx' or 'rtsp')
            recording_path: Output file path
            recording_id: Recording identifier
            duration: Recording duration in seconds
            event_id: Optional motion event ID

        Returns:
            Recording ID if successful, None if failed
        """
        # RTSP sources (mediamtx or direct camera)
        ffmpeg_cmd = self._build_ffmpeg_command(source_url, recording_path, duration)

        # Start FFmpeg process
        process = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL
        )

        # Store recording metadata
        with self.recording_lock:
            self.active_recordings[recording_id] = {
                'camera_id': camera_id,
                'camera_name': camera_name,
                'recording_path': str(recording_path),
                'source_url': source_url,
                'source_type': source_type,
                'process': process,
                'start_time': time.time(),
                'duration': duration,
                'event_id': event_id,
                'recording_type': 'motion',
                'has_prebuffer': False
            }

        # Store metadata in database
        self._store_recording_metadata(recording_id, camera_id, 'motion', event_id)

        logger.info(f"Motion recording started (live): {recording_id}")
        return recording_id

    def _start_prebuffered_recording(self, camera_id: str, camera_name: str,
                                      source_url: str, source_type: str,
                                      recording_path: Path, recording_id: str,
                                      duration: int, event_id: Optional[str],
                                      pre_buffer_sec: int) -> Optional[str]:
        """
        Start recording with pre-buffer segments concatenated.

        Approach:
        1. Get buffered segments from SegmentBufferManager
        2. Start live recording to temporary TS file
        3. When complete, concatenate: [buffer segments] + [live recording]
        4. Output final MP4 file

        Args:
            camera_id: Camera identifier
            camera_name: Human-readable camera name
            source_url: RTSP source URL
            source_type: Source type ('mediamtx' or 'rtsp')
            recording_path: Output file path (final MP4)
            recording_id: Recording identifier
            duration: Live recording duration in seconds
            event_id: Optional motion event ID
            pre_buffer_sec: Seconds of pre-buffer footage to include

        Returns:
            Recording ID if successful, None if failed
        """
        # Create temporary directory for this recording
        temp_dir = self.storage.buffer_path / camera_id / f"rec_{recording_id}"
        temp_dir.mkdir(parents=True, exist_ok=True)

        # Get pre-buffer segments (copied to temp dir)
        prebuffer_segments = self.segment_buffer_manager.get_pre_buffer_segments(
            camera_id, temp_dir
        )

        if not prebuffer_segments:
            logger.warning(f"No pre-buffer segments available for {camera_name}, falling back to live-only")
            # Clean up temp dir
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass
            return self._start_live_recording(
                camera_id, camera_name, source_url, source_type,
                recording_path, recording_id, duration, event_id
            )

        logger.info(f"Got {len(prebuffer_segments)} pre-buffer segments for {camera_name}")

        # Start live recording to temporary TS file
        live_ts_path = temp_dir / "live.ts"

        live_cmd = [
            'ffmpeg',
            '-rtsp_transport', 'tcp',
            '-i', source_url,
            '-t', str(duration),
            '-c', 'copy',
            '-f', 'mpegts',
            str(live_ts_path)
        ]

        process = subprocess.Popen(
            live_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL
        )

        # Store recording metadata with pre-buffer info
        with self.recording_lock:
            self.active_recordings[recording_id] = {
                'camera_id': camera_id,
                'camera_name': camera_name,
                'recording_path': str(recording_path),
                'temp_dir': str(temp_dir),
                'prebuffer_segments': [str(s) for s in prebuffer_segments],
                'live_ts_path': str(live_ts_path),
                'source_url': source_url,
                'source_type': source_type,
                'process': process,
                'start_time': time.time(),
                'duration': duration,
                'event_id': event_id,
                'recording_type': 'motion',
                'has_prebuffer': True,
                'prebuffer_seconds': pre_buffer_sec
            }

        # Store metadata in database
        self._store_recording_metadata(recording_id, camera_id, 'motion', event_id)

        logger.info(f"Motion recording started (with {len(prebuffer_segments)} pre-buffer segments): {recording_id}")
        return recording_id
    
    def start_manual_recording(self, camera_id: str, duration: int = 30) -> Optional[str]:
        """
        Start user-initiated manual recording for camera.
        
        Unlike motion-triggered recording, this:
        - Does NOT check if motion recording is enabled (user override)
        - Uses 'manual' recording type for separate storage/tracking
        - Does NOT require an event_id
        
        Args:
            camera_id: Camera identifier
            duration: Recording duration in seconds (default: 30)
        
        Returns:
            Recording ID (filename without extension) if successful, None if failed
        """
        try:
            # Get camera configuration
            camera = self.camera_repo.get_camera(camera_id)
            if not camera:
                logger.error(f"Camera not found: {camera_id}")
                return None
            
            camera_name = camera.get('name', camera_id)

            # Generate recording path with camera name for per-camera directory
            recording_path = self.storage.generate_recording_path(camera_id, 'manual', camera_name)
            recording_id = recording_path.stem
            
            # Get recording source
            source_url, source_type = self._get_recording_source_url(camera_id)
            
            logger.info(f"Starting MANUAL recording for {camera_name} ({camera_id})")
            logger.info(f"  Source: {source_type}")
            logger.info(f"  Duration: {duration}s")
            logger.info(f"  Output: {recording_path.name}")
            
            # RTSP sources (mediamtx or direct camera)
            ffmpeg_cmd = self._build_ffmpeg_command(source_url, recording_path, duration)
            
            # Start FFmpeg process
            process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL
            )
            
            # Store recording metadata
            with self.recording_lock:
                self.active_recordings[recording_id] = {
                    'camera_id': camera_id,
                    'camera_name': camera_name,
                    'recording_path': str(recording_path),
                    'source_url': source_url,
                    'source_type': source_type,
                    'process': process,
                    'start_time': time.time(),
                    'duration': duration,
                    'event_id': None,
                    'recording_type': 'manual'
                }
            
            # Store metadata in database
            self._store_recording_metadata(recording_id, camera_id, 'manual', event_id=None)
            
            logger.info(f"Manual recording started: {recording_id}")
            return recording_id
        
        except Exception as e:
            logger.error(f"Failed to start manual recording for {camera_id}: {e}")
            return None
    
    def _build_ffmpeg_command(self, source_url: str, output_path: Path, duration: int) -> List[str]:
        """
        Build FFmpeg command for RTSP recording.
        
        Args:
            source_url: Input RTSP URL
            output_path: Output file path
            duration: Recording duration in seconds
        
        Returns:
            FFmpeg command as list
        """
        cmd = [
            'ffmpeg',
            '-rtsp_transport', 'tcp',
            '-i', source_url,
            '-t', str(duration),  # Duration limit
            '-c', 'copy',  # Stream copy (no re-encoding)
            '-f', 'mp4',
            '-movflags', '+faststart',  # Enable streaming playback
            str(output_path)
        ]
        
        return cmd
    
    def start_continuous_recording(self, camera_id: str) -> Optional[str]:
        """
        Start continuous 24/7 recording for camera.
        
        Uses configured segment duration and auto-restarts when segment completes.
        
        Args:
            camera_id: Camera identifier
        
        Returns:
            Recording ID if successful, None if failed
        """
        try:
            # Check if continuous recording is enabled
            if not self.config.is_recording_enabled(camera_id, 'continuous'):
                logger.info(f"Continuous recording disabled for {camera_id}")
                return None
            
            # Get camera configuration
            camera = self.camera_repo.get_camera(camera_id)
            if not camera:
                logger.error(f"Camera not found: {camera_id}")
                return None
            
            camera_name = camera.get('name', camera_id)
            
            # Get segment duration from config
            camera_cfg = self.config.get_camera_config(camera_id)
            segment_duration = camera_cfg.get('continuous_recording', {}).get('segment_duration_sec', 3600)

            # Generate recording path with camera name for per-camera directory
            recording_path = self.storage.generate_recording_path(camera_id, 'continuous', camera_name)
            recording_id = recording_path.stem
            
            # Get recording source
            source_url, source_type = self._get_recording_source_url(camera_id)
            
            logger.info(f"Starting CONTINUOUS recording for {camera_name} ({camera_id})")
            logger.info(f"  Source: {source_type}")
            logger.info(f"  Segment duration: {segment_duration}s")
            logger.info(f"  Output: {recording_path.name}")
            
            # Build FFmpeg command
            ffmpeg_cmd = self._build_ffmpeg_command(source_url, recording_path, segment_duration)
            
            # Start FFmpeg process
            process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL
            )
            
            # Store recording metadata with auto_restart flag
            with self.recording_lock:
                self.active_recordings[recording_id] = {
                    'camera_id': camera_id,
                    'camera_name': camera_name,
                    'recording_path': str(recording_path),
                    'source_url': source_url,
                    'source_type': source_type,
                    'process': process,
                    'start_time': time.time(),
                    'duration': segment_duration,
                    'event_id': None,
                    'recording_type': 'continuous',
                    'auto_restart': True  # Flag for auto-restart on completion
                }
            
            # Store metadata in database
            self._store_recording_metadata(recording_id, camera_id, 'continuous', event_id=None)
            
            logger.info(f"Continuous recording started: {recording_id}")
            return recording_id
        
        except Exception as e:
            logger.error(f"Failed to start continuous recording for {camera_id}: {e}")
            return None
    
    def stop_recording(self, recording_id: str, graceful: bool = True) -> bool:
        """
        Stop an active recording process.
        
        Args:
            recording_id: Recording identifier to stop
            graceful: If True, send SIGTERM; if False, send SIGKILL
        
        Returns:
            True if stopped successfully, False otherwise
        """
        with self.recording_lock:
            if recording_id not in self.active_recordings:
                logger.warning(f"Recording not found: {recording_id}")
                return False
            
            recording = self.active_recordings[recording_id]
            process = recording.get('process')
            
            if not process:
                logger.warning(f"No process found for recording: {recording_id}")
                return False
            
            try:
                if graceful:
                    process.terminate()  # SIGTERM
                    process.wait(timeout=5)
                else:
                    process.kill()  # SIGKILL
                    process.wait(timeout=2)
                
                # Update metadata
                recording['end_time'] = time.time()
                recording['status'] = 'completed'
                
                # Update database
                self._update_recording_metadata(recording_id, 'completed')
                
                logger.info(f"Recording stopped: {recording_id}")
                return True
            
            except subprocess.TimeoutExpired:
                logger.warning(f"Recording did not stop gracefully, forcing: {recording_id}")
                process.kill()
                return True
            
            except Exception as e:
                logger.error(f"Failed to stop recording {recording_id}: {e}")
                return False
    
    def cleanup_finished_recordings(self) -> int:
        """
        Clean up metadata for finished recording processes.
        For pre-buffered recordings, performs segment concatenation.
        Auto-restarts continuous recordings when segment completes.

        Returns:
            Number of finished recordings cleaned up
        """
        with self.recording_lock:
            finished_ids = []

            for recording_id, metadata in self.active_recordings.items():
                process = metadata.get('process')

                # Check if process has finished
                if process and process.poll() is not None:
                    # Handle pre-buffered recording concatenation
                    if metadata.get('has_prebuffer', False):
                        try:
                            self._finalize_prebuffered_recording(recording_id, metadata)
                            metadata['status'] = 'completed'
                        except Exception as e:
                            logger.error(f"Failed to finalize pre-buffered recording {recording_id}: {e}")
                            metadata['status'] = 'failed'

                    # Check for auto-restart (continuous recordings)
                    elif metadata.get('auto_restart', False):
                        camera_id = metadata['camera_id']
                        logger.info(f"Continuous recording segment completed: {recording_id}")
                        logger.info(f"Auto-restarting next segment for {camera_id}")

                        # Start new segment (this will create new recording_id)
                        self.start_continuous_recording(camera_id)
                        metadata['status'] = 'completed' if process.returncode == 0 else 'failed'

                    else:
                        # Standard recording completed
                        metadata['status'] = 'completed' if process.returncode == 0 else 'failed'

                    finished_ids.append(recording_id)

                    # Update metadata
                    metadata['end_time'] = time.time()

                    # Update database
                    self._update_recording_metadata(recording_id, metadata['status'])

                    logger.info(f"Recording finished: {recording_id} (status: {metadata['status']})")

            # Remove from active recordings
            for recording_id in finished_ids:
                del self.active_recordings[recording_id]

            return len(finished_ids)

    def _finalize_prebuffered_recording(self, recording_id: str, metadata: Dict):
        """
        Concatenate pre-buffer segments with live recording.

        Uses FFmpeg concat demuxer for lossless concatenation of TS segments
        into final MP4 file.

        Args:
            recording_id: Recording identifier
            metadata: Recording metadata dict

        Raises:
            Exception: If concatenation fails
        """
        temp_dir = Path(metadata['temp_dir'])
        final_path = Path(metadata['recording_path'])
        live_ts_path = Path(metadata['live_ts_path'])
        prebuffer_segments = [Path(s) for s in metadata['prebuffer_segments']]
        camera_name = metadata.get('camera_name', recording_id)

        logger.info(f"Finalizing pre-buffered recording {recording_id}")

        # Check live recording exists
        if not live_ts_path.exists():
            raise FileNotFoundError(f"Live recording not found: {live_ts_path}")

        # Create concat list file for FFmpeg
        concat_list = temp_dir / "concat_list.txt"
        with open(concat_list, 'w') as f:
            # Pre-buffer segments first (already in chronological order)
            for seg in prebuffer_segments:
                if seg.exists():
                    f.write(f"file '{seg}'\n")
            # Then live recording
            f.write(f"file '{live_ts_path}'\n")

        logger.debug(f"Concat list created with {len(prebuffer_segments)} prebuffer + live.ts")

        # Concatenate to final MP4 using FFmpeg concat demuxer
        concat_cmd = [
            'ffmpeg',
            '-f', 'concat',
            '-safe', '0',
            '-i', str(concat_list),
            '-c', 'copy',
            '-f', 'mp4',
            '-movflags', '+faststart',
            str(final_path)
        ]

        logger.debug(f"Concat command: {' '.join(concat_cmd)}")

        result = subprocess.run(
            concat_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120  # 2 minute timeout for concatenation
        )

        if result.returncode != 0:
            error_msg = result.stderr.decode()[:500] if result.stderr else "Unknown error"
            raise RuntimeError(f"FFmpeg concat failed: {error_msg}")

        # Verify output file exists
        if not final_path.exists():
            raise FileNotFoundError(f"Concatenated file not created: {final_path}")

        file_size = final_path.stat().st_size
        prebuffer_sec = metadata.get('prebuffer_seconds', 0)

        logger.info(f"Pre-buffered recording finalized: {final_path.name} "
                   f"({file_size / 1024 / 1024:.2f} MB, {prebuffer_sec}s pre-buffer)")

        # Cleanup temp files
        try:
            shutil.rmtree(temp_dir)
            logger.debug(f"Cleaned up temp dir: {temp_dir}")
        except Exception as e:
            logger.warning(f"Failed to cleanup temp dir {temp_dir}: {e}")
        
    def get_active_recordings(self) -> List[Dict]:
        """
        Get list of currently active recording processes.
        
        Returns:
            List of recording metadata dictionaries
        """
        with self.recording_lock:
            active = []
            for recording_id, metadata in self.active_recordings.items():
                process = metadata.get('process')
                
                # Check if process is still running
                if process and process.poll() is None:
                    elapsed = time.time() - metadata['start_time']
                    
                    active.append({
                        'recording_id': recording_id,
                        'camera_id': metadata['camera_id'],
                        'camera_name': metadata['camera_name'],
                        'recording_type': metadata['recording_type'],
                        'start_time': metadata['start_time'],
                        'elapsed_seconds': round(elapsed, 1),
                        'duration': metadata['duration'],
                        'progress_percent': round((elapsed / metadata['duration']) * 100, 1),
                        'source_type': metadata['source_type']
                    })
            
            return active

    def _store_recording_metadata(self, recording_id: str, camera_id: str, recording_type: str, event_id: Optional[str] = None):
        """Store recording metadata in PostgreSQL via PostgREST."""
        try:
            # Get recording metadata from active_recordings
            with self.recording_lock:
                recording = self.active_recordings.get(recording_id)
                if not recording:
                    logger.warning(f"Recording not found for metadata storage: {recording_id}")
                    return
            
            # Build metadata matching database schema
            metadata = {
                'camera_id': camera_id,
                'camera_name': recording['camera_name'],
                'timestamp': datetime.now().isoformat(),
                'file_path': str(recording['recording_path']),
                'file_name': f"{recording_id}.mp4",
                'storage_tier': 'recent',  # All recordings start in 'recent' tier
                'motion_triggered': recording_type == 'motion',
                'motion_source': 'manual' if event_id is None else 'onvif',  # Can enhance later
                'motion_event_id': int(event_id) if event_id and event_id.isdigit() else None,
                'status': 'recording'
            }
            
            response = requests.post(
                f"{self.postgrest_url}/recordings",
                json=metadata,
                headers={'Content-Type': 'application/json'},
                timeout=5
            )
            
            if response.status_code not in [200, 201]:
                logger.warning(f"Failed to store recording metadata: {response.status_code} - {response.text}")
        
        except Exception as e:
            logger.error(f"Error storing recording metadata: {e}")
    
    def _update_recording_metadata(self, recording_id: str, status: str):
        """Update recording metadata in PostgreSQL via PostgREST."""
        try:
            # Get file info for completion
            with self.recording_lock:
                recording = self.active_recordings.get(recording_id)
            
            update_data = {
                'end_timestamp': datetime.now().isoformat(),
                'status': status,
                'updated_at': datetime.now().isoformat()
            }
            
            # Add file size if recording completed successfully
            if recording and status == 'completed':
                file_path = Path(recording['recording_path'])
                if file_path.exists():
                    update_data['file_size_bytes'] = file_path.stat().st_size
                    update_data['duration_seconds'] = int(time.time() - recording['start_time'])
            
            # Update by file_name (since we don't have the DB id)
            response = requests.patch(
                f"{self.postgrest_url}/recordings?file_name=eq.{recording_id}.mp4",
                json=update_data,
                headers={'Content-Type': 'application/json'},
                timeout=5
            )
            
            if response.status_code not in [200, 204]:
                logger.warning(f"Failed to update recording metadata: {response.status_code} - {response.text}")
        
        except Exception as e:
            logger.error(f"Error updating recording metadata: {e}")