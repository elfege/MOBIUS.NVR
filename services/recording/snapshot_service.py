"""
Snapshot Service
Captures periodic JPEG snapshots from camera streams.
"""

import threading
import time
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class SnapshotService:
    """
    Periodic snapshot capture service using FFmpeg.
    
    Captures single frames at configured intervals and stores as JPEG.
    """
    
    def __init__(self, camera_repo, storage_manager, recording_config):
        """
        Initialize snapshot service.
        
        Args:
            camera_repo: CameraRepository instance
            storage_manager: StorageManager instance
            recording_config: RecordingConfig instance
        """
        self.camera_repo = camera_repo
        self.storage = storage_manager
        self.config = recording_config
        
        # Track active snapshot timers per camera
        self.active_timers: Dict[str, threading.Timer] = {}
        self.stop_flags: Dict[str, bool] = {}
        
        logger.info("SnapshotService initialized")
    
    
    def start_snapshots(self, camera_id: str) -> bool:
        """
        Start periodic snapshots for camera.
        
        Args:
            camera_id: Camera identifier
        
        Returns:
            True if started successfully
        """
        try:
            
            # Check if snapshots enabled
            if not self.config.is_recording_enabled(camera_id, 'snapshots'):
                logger.info(f"Snapshots disabled for {camera_id}")
                return False
            
            # Get camera config
            camera = self.camera_repo.get_camera(camera_id)
            if not camera:
                logger.error(f"Camera not found: {camera_id}")
                return False
            
            camera_name = camera.get('name', camera_id)
            
            # Get snapshot interval
            camera_cfg = self.config.get_camera_config(camera_id)
            interval = camera_cfg.get('snapshots', {}).get('interval_sec', 300)
            
            logger.info(f"Starting snapshots for {camera_name} every {interval}s")
            
            # Reset stop flag and schedule first snapshot
            self.stop_flags[camera_id] = False
            self._schedule_next_snapshot(camera_id, interval)
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to start snapshots for {camera_id}: {e}")
            return False
    
    
    def stop_snapshots(self, camera_id: str):
        """
        Stop snapshots for camera.
        
        Args:
            camera_id: Camera identifier
        """
        logger.info(f"Stopping snapshots for {camera_id}")
        
        # Set stop flag
        self.stop_flags[camera_id] = True
        
        # Cancel active timer if exists
        if camera_id in self.active_timers:
            self.active_timers[camera_id].cancel()
            del self.active_timers[camera_id]
    
    
    def _schedule_next_snapshot(self, camera_id: str, interval: int):
        """
        Schedule next snapshot capture.
        
        Args:
            camera_id: Camera identifier
            interval: Seconds until next capture
        """
        # Check stop flag
        if self.stop_flags.get(camera_id, False):
            logger.info(f"Snapshot scheduling stopped for {camera_id}")
            return
        
        # Create timer for next capture
        timer = threading.Timer(interval, self._capture_snapshot, args=(camera_id, interval))
        timer.daemon = True
        timer.start()
        
        self.active_timers[camera_id] = timer
    
    
    def _capture_snapshot(self, camera_id: str, interval: int):
        """
        Capture single snapshot via FFmpeg.
        
        Args:
            camera_id: Camera identifier
            interval: Interval for next scheduled capture
        """
        try:
            # Get camera
            camera = self.camera_repo.get_camera(camera_id)
            if not camera:
                logger.error(f"Camera not found: {camera_id}")
                return

            camera_name = camera.get('name', camera_id)

            # Generate output path with camera name for per-camera directory
            output_path = self.storage.generate_recording_path(camera_id, 'snapshot', camera_name)
            
            # Get RTSP URL (similar to recording service)
            source_url = self._get_snapshot_source_url(camera)
            
            # Build FFmpeg command for single frame
            cmd = [
                'ffmpeg',
                '-rtsp_transport', 'tcp',
                '-i', source_url,
                '-frames:v', '1',  # Single frame only
                '-q:v', '2',  # High quality JPEG (1-31, lower is better)
                '-y',  # Overwrite output file
                str(output_path)
            ]
            
            # Execute FFmpeg
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10
            )
            
            if result.returncode == 0:
                logger.debug(f"Snapshot captured: {output_path.name}")
            else:
                logger.warning(f"Snapshot failed for {camera_id}: {result.stderr.decode()[:200]}")
            
            # Schedule next snapshot
            self._schedule_next_snapshot(camera_id, interval)
            
        except subprocess.TimeoutExpired:
            logger.error(f"Snapshot timeout for {camera_id}")
            self._schedule_next_snapshot(camera_id, interval)
        except Exception as e:
            logger.error(f"Snapshot capture error for {camera_id}: {e}")
            self._schedule_next_snapshot(camera_id, interval)
    
    
    def _get_snapshot_source_url(self, camera: Dict) -> str:
        """
        Get RTSP URL for snapshot capture.
        
        Args:
            camera: Camera configuration dict
        
        Returns:
            RTSP URL string
        """
        camera_type = camera.get('type', '').lower()
        stream_type = camera.get('stream_type', '').upper()
        
        # For cameras using a streaming hub, tap hub RTSP (single-consumer policy)
        from services.streaming_hub import get_streaming_hub, get_rtsp_source_url
        hub = get_streaming_hub(camera)
        if stream_type in ('HLS', 'LL_HLS', 'NEOLINK', 'WEBRTC', 'GO2RTC') or hub == 'go2rtc':
            return get_rtsp_source_url(camera.get('serial', ''), camera)
        
        # For RTSP/MJPEG cameras, build direct RTSP URL
        # Import handler for camera type
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
                ReolinkCredentialProvider(),
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
            raise ValueError(f"Unknown camera type: {camera_type}")
        
        return handler.build_rtsp_url(camera, stream_type='sub')  # Use sub stream for snapshots
    
    
    def get_active_snapshots(self) -> Dict[str, Dict]:
        """
        Get status of active snapshot services.
        
        Returns:
            Dict mapping camera_id to snapshot status
        """
        active = {}
        
        for camera_id, timer in self.active_timers.items():
            if timer.is_alive():
                camera = self.camera_repo.get_camera(camera_id)
                camera_cfg = self.config.get_camera_config(camera_id)
                interval = camera_cfg.get('snapshots', {}).get('interval_sec', 300)
                
                active[camera_id] = {
                    'camera_name': camera.get('name', camera_id) if camera else camera_id,
                    'interval_sec': interval,
                    'active': True
                }
        
        return active