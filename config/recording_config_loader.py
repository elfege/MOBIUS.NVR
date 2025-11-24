"""
Recording Configuration Loader
Handles camera-centric recording configuration with global defaults and per-camera overrides.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Optional, Any
from copy import deepcopy

logger = logging.getLogger(__name__)


class RecordingConfig:
    """
    Manages recording system configuration with camera-specific settings.
    
    New structure:
    - global_defaults: Base settings for all cameras
    - camera_settings: Per-camera overrides
    - Auto-resolution of recording sources based on camera type
    """
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize configuration loader.
        
        Args:
            config_path: Path to recording_settings.json
        """
        if config_path is None:
            config_dir = Path(__file__).parent
            config_path = config_dir / "recording_settings.json"
        
        self.config_path = Path(config_path)
        self.base_config: Dict[str, Any] = {}
        self._load_config()
    
    
    def _load_config(self):
        """Load configuration from JSON file."""
        try:
            with open(self.config_path, 'r') as f:
                self.base_config = json.load(f)
            logger.info(f"Loaded recording configuration from {self.config_path}")
        except FileNotFoundError:
            logger.error(f"Configuration file not found: {self.config_path}")
            self._use_defaults()
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in config file: {e}")
            self._use_defaults()
    
    
    def _use_defaults(self):
        """Fallback to hardcoded defaults if config file unavailable."""
        logger.warning("Using hardcoded default configuration")
        self.base_config = {
            "global_defaults": {
                "motion_recording": {
                    "enabled": True,
                    "detection_method": "onvif",
                    "recording_source": "auto",
                    "segment_duration_sec": 30,
                    "pre_buffer_sec": 5,
                    "post_buffer_sec": 10,
                    "max_age_days": 7,
                    "quality": "main"
                },
                "continuous_recording": {
                    "enabled": False,
                    "segment_duration_sec": 3600,
                    "max_age_days": 3,
                    "quality": "sub"
                },
                "snapshots": {
                    "enabled": True,
                    "interval_sec": 300,
                    "max_age_days": 14,
                    "quality": 85
                }
            },
            "storage_limits": {
                "motion_max_mb": 50000,
                "continuous_max_mb": 900000,
                "snapshots_max_mb": 5000,
                "min_free_space_mb": 10000
            },
            "cleanup_schedule": {
                "enabled": True,
                "cron": "0 3 * * *",
                "run_on_startup": False
            },
            "encoding": {
                "video_codec": "libx264",
                "preset": "veryfast",
                "crf": 23,
                "audio_codec": "aac",
                "audio_bitrate": "128k"
            },
            "motion_detection": {
                "cooldown_sec": 60,
                "min_event_duration_sec": 3,
                "debounce_sec": 2
            },
            "camera_settings": {}
        }
    
    
    def get_camera_config(self, camera_id: str, camera_stream_type: Optional[str] = None) -> Dict[str, Any]:
        """
        Get complete configuration for specific camera with overrides applied.
        
        Args:
            camera_id: Camera identifier from cameras.json
            camera_stream_type: Optional stream type for auto-resolution
        
        Returns:
            Complete configuration dict with camera-specific overrides merged
        """
        # Start with deep copy of global defaults
        camera_config = deepcopy(self.base_config.get('global_defaults', {}))
        
        # Apply camera-specific overrides
        camera_overrides = self.base_config.get('camera_settings', {}).get(camera_id, {})
        
        if camera_overrides:
            logger.debug(f"Applying overrides for camera: {camera_id}")
            
            # Merge each category (motion_recording, continuous_recording, snapshots)
            for category in ['motion_recording', 'continuous_recording', 'snapshots']:
                if category in camera_overrides:
                    if category not in camera_config:
                        camera_config[category] = {}
                    camera_config[category].update(camera_overrides[category])
        
        # Resolve "auto" recording source if stream type provided
        if camera_stream_type and camera_config.get('motion_recording', {}).get('recording_source') == 'auto':
            camera_config['motion_recording']['recording_source'] = \
                self._resolve_auto_source(camera_stream_type)
        
        # Add global config sections
        camera_config['storage_limits'] = self.base_config.get('storage_limits', {})
        camera_config['encoding'] = self.base_config.get('encoding', {})
        camera_config['motion_detection'] = self.base_config.get('motion_detection', {})
        
        return camera_config
    
    
    def _resolve_auto_source(self, stream_type: str) -> str:
        """
        Resolve 'auto' recording source based on camera stream type.
        
        Args:
            stream_type: Camera stream type (LL_HLS, HLS, MJPEG, etc.)
        
        Returns:
            Resolved source: 'mediamtx', 'rtsp', or 'mjpeg_service'
        """
        stream_type = stream_type.upper()
        
        if stream_type in ['LL_HLS', 'HLS']:
            return 'mediamtx'  # Tap MediaMTX RTSP output
        elif stream_type == 'MJPEG':
            return 'mjpeg_service'  # Tap MJPEG capture service (default for MJPEG)
        else:
            return 'rtsp'  # Direct RTSP fallback
    
    
    def get_global_defaults(self) -> Dict[str, Any]:
        """Get global default settings."""
        return deepcopy(self.base_config.get('global_defaults', {}))
    
    
    def get_storage_limits(self) -> Dict[str, int]:
        """Get storage limit configuration."""
        return deepcopy(self.base_config.get('storage_limits', {}))
    
    
    def get_cleanup_schedule(self) -> Dict[str, Any]:
        """Get cleanup schedule configuration."""
        return deepcopy(self.base_config.get('cleanup_schedule', {}))
    
    
    def is_recording_enabled(self, camera_id: str, recording_type: str = 'motion') -> bool:
        """
        Check if recording is enabled for camera.
        
        Args:
            camera_id: Camera identifier
            recording_type: 'motion', 'continuous', or 'snapshots'
        
        Returns:
            True if recording enabled for this camera and type
        """
        camera_cfg = self.get_camera_config(camera_id)
        
        if recording_type == 'motion':
            return camera_cfg.get('motion_recording', {}).get('enabled', False)
        elif recording_type == 'continuous':
            return camera_cfg.get('continuous_recording', {}).get('enabled', False)
        elif recording_type == 'snapshots':
            return camera_cfg.get('snapshots', {}).get('enabled', False)
        else:
            return False
    
    
    def get_recording_source(self, camera_id: str, camera_stream_type: str) -> str:
        """
        Get recording source for camera (with auto-resolution).
        
        Args:
            camera_id: Camera identifier
            camera_stream_type: Camera stream type from cameras.json
        
        Returns:
            Recording source: 'mediamtx', 'rtsp', or 'mjpeg_service'
        """
        camera_cfg = self.get_camera_config(camera_id, camera_stream_type)
        return camera_cfg.get('motion_recording', {}).get('recording_source', 'auto')
    
    
    def get_motion_detection_method(self, camera_id: str) -> str:
        """
        Get motion detection method for camera.
        
        Args:
            camera_id: Camera identifier
        
        Returns:
            Detection method: 'onvif', 'ffmpeg', 'baichuan', or 'none'
        """
        camera_cfg = self.get_camera_config(camera_id)
        return camera_cfg.get('motion_recording', {}).get('detection_method', 'none')
    
    
    def reload(self):
        """Reload configuration from file."""
        logger.info("Reloading recording configuration")
        self._load_config()
        
    def get_camera_settings(self, camera_id: str) -> Dict[str, Any]:
        """
        Get camera settings in UI-friendly format (merged global + camera overrides).
        
        Args:
            camera_id: Camera identifier
        
        Returns:
            Dictionary with motion_recording, continuous_recording, snapshots sections
        """
        camera_cfg = self.get_camera_config(camera_id)
        
        return {
            'motion_recording': camera_cfg.get('motion_recording', {}),
            'continuous_recording': camera_cfg.get('continuous_recording', {}),
            'snapshots': camera_cfg.get('snapshots', {})
        }
    
    
    def update_camera_settings(self, camera_id: str, settings: Dict[str, Any]):
        """
        Update camera-specific settings and save to file.
        
        Args:
            camera_id: Camera identifier
            settings: Settings dictionary with motion_recording, continuous_recording, snapshots
        """
        if 'camera_settings' not in self.base_config:
            self.base_config['camera_settings'] = {}
        
        self.base_config['camera_settings'][camera_id] = settings
        
        try:
            with open(self.config_path, 'w') as f:
                json.dump(self.base_config, f, indent=2)
            logger.info(f"Updated recording settings for camera: {camera_id}")
        except Exception as e:
            logger.error(f"Failed to save recording settings: {e}")
            raise