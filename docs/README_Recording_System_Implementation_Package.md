---
title: "NVR Recording System - Complete Implementation Package"
layout: default
---

# NVR Recording System - Complete Implementation Package

**Generated**: 2025-11-12 23:15 UTC
**Status**: Ready for testing

---

## File 1: Updated Recording Configuration

**File**: `~/0_NVR/config/recording_settings.json`

```json
{
  "global_defaults": {
    "motion_recording": {
      "enabled": true,
      "detection_method": "onvif",
      "recording_source": "auto",
      "segment_duration_sec": 30,
      "pre_buffer_sec": 5,
      "post_buffer_sec": 10,
      "max_age_days": 7,
      "quality": "main"
    },
    "continuous_recording": {
      "enabled": false,
      "segment_duration_sec": 3600,
      "max_age_days": 3,
      "quality": "sub"
    },
    "snapshots": {
      "enabled": true,
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
    "enabled": true,
    "cron": "0 3 * * *",
    "run_on_startup": false
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
  
  "camera_settings": {
    "AMCREST_LOBBY": {
      "motion_recording": {
        "enabled": true,
        "detection_method": "onvif",
        "recording_source": "rtsp",
        "segment_duration_sec": 60,
        "max_age_days": 30
      },
      "continuous_recording": {
        "enabled": true,
        "segment_duration_sec": 1800
      },
      "snapshots": {
        "interval_sec": 1
      }
    },
    
    "REOLINK_OFFICE": {
      "motion_recording": {
        "enabled": true,
        "detection_method": "onvif",
        "recording_source": "mjpeg_service",
        "max_age_days": 14
      }
    },
    
    "68d49398005cf203e400043f": {
      "motion_recording": {
        "enabled": true,
        "detection_method": "ffmpeg",
        "recording_source": "auto",
        "max_age_days": 14
      },
      "snapshots": {
        "interval_sec": 60
      }
    }
  }
}
```

**Key Changes:**
- Restructured to `global_defaults` + `camera_settings`
- Added `recording_source` field (auto/rtsp/mjpeg_service)
- Added `detection_method` field (onvif/ffmpeg/manual_only)
- Added `quality` field (main/sub) for recording stream selection
- Moved encoding and motion detection to top level
- Storage limits separated from per-tier settings

---

## File 2: Updated Configuration Loader

**File**: `~/0_NVR/config/recording_config_loader.py`

```python
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
            recording_type: 'motion' or 'continuous'
        
        Returns:
            True if recording enabled for this camera and type
        """
        camera_cfg = self.get_camera_config(camera_id)
        
        if recording_type == 'motion':
            return camera_cfg.get('motion_recording', {}).get('enabled', False)
        elif recording_type == 'continuous':
            return camera_cfg.get('continuous_recording', {}).get('enabled', False)
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
    
    
    def reload(self):
        """Reload configuration from file."""
        logger.info("Reloading recording configuration")
        self._load_config()
```

---

## File 3: Updated Storage Manager

**File**: `~/0_NVR/services/recording/storage_manager.py`

```python
"""
Storage Manager
Handles file path generation, storage space management, and recording lifecycle.
"""

import os
import shutil
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict

# Import recording config loader
import sys
sys.path.append('/app/config')
from recording_config_loader import RecordingConfig

logger = logging.getLogger(__name__)


class StorageManager:
    """
    Manages recording file storage, paths, and cleanup operations.
    
    Storage structure:
    - /recordings/motion/     : Motion-triggered clips
    - /recordings/continuous/ : 24/7 recordings
    - /recordings/snapshots/  : Thumbnail images
    """
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize storage manager.
        
        Args:
            config_path: Optional path to recording_settings.json
        """
        # Load configuration
        self.config = RecordingConfig(config_path)
        
        # Define storage paths
        self.base_path = Path("/recordings")
        self.motion_path = self.base_path / "motion"
        self.continuous_path = self.base_path / "continuous"
        self.snapshots_path = self.base_path / "snapshots"
        
        # Verify directories exist
        self._verify_directories()
        
        logger.info(f"StorageManager initialized - base: {self.base_path}")
    
    
    def _verify_directories(self):
        """Verify all storage directories exist and are writable."""
        for path in [self.motion_path, self.continuous_path, self.snapshots_path]:
            if not path.exists():
                logger.error(f"Storage directory does not exist: {path}")
                raise FileNotFoundError(f"Storage directory missing: {path}")
            
            if not os.access(path, os.W_OK):
                logger.error(f"Storage directory not writable: {path}")
                raise PermissionError(f"Cannot write to: {path}")
        
        logger.info("All storage directories verified")
    
    
    def generate_recording_path(self, camera_id: str, recording_type: str = "motion") -> Path:
        """
        Generate unique file path for a new recording.
        
        Args:
            camera_id: Camera identifier
            recording_type: Type of recording ("motion", "continuous", "snapshot")
        
        Returns:
            Full path to recording file
        
        Format: {camera_id}_YYYYMMDD_HHMMSS.mp4
        Example: AMCREST_LOBBY_20251112_143052.mp4
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if recording_type == "snapshot":
            filename = f"{camera_id}_{timestamp}.jpg"
            return self.snapshots_path / filename
        else:
            filename = f"{camera_id}_{timestamp}.mp4"
            
            if recording_type == "motion":
                return self.motion_path / filename
            elif recording_type == "continuous":
                return self.continuous_path / filename
            else:
                raise ValueError(f"Unknown recording type: {recording_type}")
    
    
    def get_storage_stats(self) -> Dict[str, Dict]:
        """
        Get storage usage statistics for all tiers with configured limits.
        
        Returns:
            Dictionary with usage stats per tier
        """
        limits = self.config.get_storage_limits()
        stats = {}
        
        tier_mapping = {
            "motion": ("motion_max_mb", self.motion_path),
            "continuous": ("continuous_max_mb", self.continuous_path),
            "snapshots": ("snapshots_max_mb", self.snapshots_path)
        }
        
        for tier_name, (limit_key, path) in tier_mapping.items():
            total_size = sum(f.stat().st_size for f in path.glob("*") if f.is_file())
            file_count = len(list(path.glob("*")))
            max_size_mb = limits.get(limit_key, 0)
            
            stats[tier_name] = {
                "path": str(path),
                "total_bytes": total_size,
                "total_mb": round(total_size / 1024 / 1024, 2),
                "file_count": file_count,
                "max_size_mb": max_size_mb,
                "usage_percent": round((total_size / 1024 / 1024 / max_size_mb * 100), 2) if max_size_mb > 0 else 0
            }
        
        return stats
    
    
    def check_storage_limits(self, recording_type: str = "motion") -> Dict[str, any]:
        """
        Check if storage tier is approaching or exceeding configured limits.
        
        Args:
            recording_type: Type of recordings to check
        
        Returns:
            Dict with limit status and metrics
        """
        stats = self.get_storage_stats()
        tier_stats = stats.get(recording_type, {})
        
        usage_percent = tier_stats.get('usage_percent', 0)
        
        return {
            'size_limit_exceeded': usage_percent >= 100,
            'cleanup_recommended': usage_percent >= 90,
            'usage_percent': usage_percent,
            'current_mb': tier_stats.get('total_mb', 0),
            'max_mb': tier_stats.get('max_size_mb', 0)
        }
    
    
    def cleanup_old_recordings(self, camera_id: str, recording_type: str = "motion") -> int:
        """
        Delete recordings older than configured retention period for camera.
        
        Args:
            camera_id: Camera identifier (for camera-specific retention)
            recording_type: Type of recordings to clean
        
        Returns:
            Number of files deleted
        """
        # Get camera-specific retention settings
        camera_cfg = self.config.get_camera_config(camera_id)
        
        if recording_type == "motion":
            max_age_days = camera_cfg.get('motion_recording', {}).get('max_age_days', 7)
            target_path = self.motion_path
        elif recording_type == "continuous":
            max_age_days = camera_cfg.get('continuous_recording', {}).get('max_age_days', 3)
            target_path = self.continuous_path
        elif recording_type == "snapshots":
            max_age_days = camera_cfg.get('snapshots', {}).get('max_age_days', 14)
            target_path = self.snapshots_path
        else:
            raise ValueError(f"Unknown recording type: {recording_type}")
        
        deleted_count = 0
        cutoff_time = datetime.now().timestamp() - (max_age_days * 86400)
        
        # Only delete files for this specific camera
        pattern = f"{camera_id}_*.mp4" if recording_type != "snapshots" else f"{camera_id}_*.jpg"
        
        for file_path in target_path.glob(pattern):
            if file_path.stat().st_mtime < cutoff_time:
                try:
                    file_path.unlink()
                    deleted_count += 1
                    logger.info(f"Deleted old recording: {file_path.name}")
                except Exception as e:
                    logger.error(f"Failed to delete {file_path}: {e}")
        
        if deleted_count > 0:
            logger.info(f"Cleanup complete: {deleted_count} files deleted for {camera_id} "
                       f"({recording_type}, retention: {max_age_days}d)")
        
        return deleted_count
    
    
    def cleanup_all_cameras(self, recording_type: str = "motion") -> Dict[str, int]:
        """
        Run cleanup for all cameras with recordings.
        
        Args:
            recording_type: Type of recordings to clean
        
        Returns:
            Dict mapping camera_id to number of files deleted
        """
        if recording_type == "motion":
            target_path = self.motion_path
            pattern = "*.mp4"
        elif recording_type == "continuous":
            target_path = self.continuous_path
            pattern = "*.mp4"
        elif recording_type == "snapshots":
            target_path = self.snapshots_path
            pattern = "*.jpg"
        else:
            raise ValueError(f"Unknown recording type: {recording_type}")
        
        # Extract unique camera IDs from filenames
        camera_ids = set()
        for file_path in target_path.glob(pattern):
            # Format: CAMERA_ID_YYYYMMDD_HHMMSS.ext
            camera_id = file_path.stem.rsplit('_', 2)[0]
            camera_ids.add(camera_id)
        
        # Run cleanup for each camera
        cleanup_results = {}
        for camera_id in camera_ids:
            deleted = self.cleanup_old_recordings(camera_id, recording_type)
            if deleted > 0:
                cleanup_results[camera_id] = deleted
        
        total_deleted = sum(cleanup_results.values())
        logger.info(f"Total cleanup: {total_deleted} files deleted across {len(cleanup_results)} cameras")
        
        return cleanup_results
```

---

## File 4: Recording Service Implementation

**File**: `~/0_NVR/services/recording/recording_service.py`

```python
"""
Recording Service
Manages FFmpeg recording processes with hybrid source support (MediaMTX, RTSP, MJPEG service).
"""

import os
import subprocess
import threading
import logging
import time
import requests
from datetime import datetime
from typing import Dict, Optional, List, Tuple
from pathlib import Path

# Import config and storage
import sys
sys.path.append('/app/config')
from recording_config_loader import RecordingConfig
from storage_manager import StorageManager

logger = logging.getLogger(__name__)


class RecordingService:
    """
    Manages video recording processes with support for multiple source types.
    
    Recording Sources:
    - mediamtx: Tap existing MediaMTX RTSP output (LL_HLS/HLS cameras)
    - rtsp: Direct camera RTSP connection
    - mjpeg_service: Tap MJPEG capture service buffer
    
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
        
        logger.info(f"RecordingService initialized - PostgREST: {self.postgrest_url}")
    
    
    def _get_recording_source_url(self, camera_id: str) -> Tuple[str, str]:
        """
        Get recording source URL and type for camera.
        
        Args:
            camera_id: Camera identifier
        
        Returns:
            Tuple of (source_url, source_type)
            source_type: 'mediamtx' | 'rtsp' | 'mjpeg_service'
        
        Raises:
            ValueError: If camera not found or invalid configuration
        """
        # Get camera configuration
        camera = self.camera_repo.get_camera(camera_id)
        if not camera:
            raise ValueError(f"Camera not found: {camera_id}")
        
        stream_type = camera.get('stream_type', '').upper()
        
        # Get recording configuration for this camera
        camera_cfg = self.config.get_camera_config(camera_id, stream_type)
        recording_source = camera_cfg.get('motion_recording', {}).get('recording_source', 'auto')
        
        logger.debug(f"Camera {camera_id}: stream_type={stream_type}, recording_source={recording_source}")
        
        # Resolve source URL based on configuration
        if recording_source == 'mediamtx':
            # Tap MediaMTX RTSP output
            packager_path = camera.get('packager_path', camera_id)
            return (f"rtsp://nvr-packager:8554/{packager_path}", 'mediamtx')
        
        elif recording_source == 'rtsp':
            # Direct camera RTSP connection
            handler = self._get_camera_handler(camera)
            rtsp_url = handler.build_rtsp_url(camera, stream_type='main')
            return (rtsp_url, 'rtsp')
        
        elif recording_source == 'mjpeg_service':
            # Tap MJPEG capture service
            return (f"mjpeg_service://{camera_id}", 'mjpeg_service')
        
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
        else:
            raise ValueError(f"Unknown camera type: {camera_type}")
    
    
    def start_motion_recording(self, camera_id: str, duration: int = 30, event_id: Optional[str] = None) -> Optional[str]:
        """
        Start motion-triggered recording for camera.
        
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
            
            # Generate recording path
            recording_path = self.storage.generate_recording_path(camera_id, 'motion')
            recording_id = recording_path.stem  # Filename without extension
            
            # Get recording source
            source_url, source_type = self._get_recording_source_url(camera_id)
            
            logger.info(f"Starting motion recording for {camera_name} ({camera_id})")
            logger.info(f"  Source: {source_type}")
            logger.info(f"  Duration: {duration}s")
            logger.info(f"  Output: {recording_path.name}")
            
            # Build FFmpeg command based on source type
            if source_type == 'mjpeg_service':
                # MJPEG service requires special handling
                success = self._start_mjpeg_recording(camera_id, recording_path, duration)
                if not success:
                    return None
            else:
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
                        'recording_type': 'motion'
                    }
            
            # Store metadata in database
            self._store_recording_metadata(recording_id, camera_id, 'motion', event_id)
            
            logger.info(f"Motion recording started: {recording_id}")
            return recording_id
        
        except Exception as e:
            logger.error(f"Failed to start motion recording for {camera_id}: {e}")
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
    
    
    def _start_mjpeg_recording(self, camera_id: str, output_path: Path, duration: int) -> bool:
        """
        Start recording from MJPEG capture service.
        
        This is more complex as we need to read JPEG frames from the service
        and pipe them to FFmpeg for MP4 muxing.
        
        Args:
            camera_id: Camera identifier
            output_path: Output file path
            duration: Recording duration in seconds
        
        Returns:
            True if started successfully
        """
        # TODO: Implement MJPEG service recording
        # This requires:
        # 1. Determine if camera uses reolink or amcrest service
        # 2. Start thread to read frames from service buffer
        # 3. Pipe frames to FFmpeg with -f image2pipe
        # 4. Handle timing and duration
        
        logger.warning(f"MJPEG service recording not yet implemented for {camera_id}")
        return False
    
    
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
    
    
    def cleanup_finished_recordings(self) -> int:
        """
        Clean up metadata for finished recording processes.
        
        Returns:
            Number of finished recordings cleaned up
        """
        with self.recording_lock:
            finished_ids = []
            
            for recording_id, metadata in self.active_recordings.items():
                process = metadata.get('process')
                
                # Check if process has finished
                if process and process.poll() is not None:
                    finished_ids.append(recording_id)
                    
                    # Update metadata
                    metadata['end_time'] = time.time()
                    metadata['status'] = 'completed' if process.returncode == 0 else 'failed'
                    
                    # Update database
                    self._update_recording_metadata(recording_id, metadata['status'])
                    
                    logger.info(f"Recording finished: {recording_id} (status: {metadata['status']})")
            
            # Remove from active recordings
            for recording_id in finished_ids:
                del self.active_recordings[recording_id]
            
            return len(finished_ids)
    
    
    def _store_recording_metadata(self, recording_id: str, camera_id: str, 
                                   recording_type: str, event_id: Optional[str] = None):
        """Store recording metadata in PostgreSQL via PostgREST."""
        try:
            metadata = {
                'recording_id': recording_id,
                'camera_id': camera_id,
                'recording_type': recording_type,
                'event_id': event_id,
                'start_time': datetime.now().isoformat(),
                'status': 'recording'
            }
            
            response = requests.post(
                f"{self.postgrest_url}/recordings",
                json=metadata,
                headers={'Content-Type': 'application/json'},
                timeout=5
            )
            
            if response.status_code not in [200, 201]:
                logger.warning(f"Failed to store recording metadata: {response.status_code}")
        
        except Exception as e:
            logger.error(f"Error storing recording metadata: {e}")
    
    
    def _update_recording_metadata(self, recording_id: str, status: str):
        """Update recording metadata in PostgreSQL via PostgREST."""
        try:
            update_data = {
                'end_time': datetime.now().isoformat(),
                'status': status
            }
            
            response = requests.patch(
                f"{self.postgrest_url}/recordings?recording_id=eq.{recording_id}",
                json=update_data,
                headers={'Content-Type': 'application/json'},
                timeout=5
            )
            
            if response.status_code not in [200, 204]:
                logger.warning(f"Failed to update recording metadata: {response.status_code}")
        
        except Exception as e:
            logger.error(f"Error updating recording metadata: {e}")
```

---

## File 5: Updated Installation Tracking Document

**File**: See complete installation document with Phase 2 progress marked through Step 6.

**Phase 2 Status:**
- Steps 1-5c: ✅ COMPLETE
- Step 6: ⏳ IN PROGRESS (code written, needs testing)

**Next Steps for Testing:**
1. Update `recording_settings.json` with new structure
2. Test RecordingService initialization
3. Test recording from MediaMTX source (LL_HLS camera)
4. Test recording from direct RTSP (MJPEG camera with RTSP source)
5. Implement MJPEG service recording (deferred - requires additional architecture)
6. Add Flask routes for recording control

---

## Testing Commands

### Test Configuration Loading
```python
from config.recording_config_loader import RecordingConfig

config = RecordingConfig()

# Test global defaults
print(config.get_global_defaults())

# Test camera-specific config
camera_cfg = config.get_camera_config('AMCREST_LOBBY', 'MJPEG')
print(f"Recording source: {camera_cfg['motion_recording']['recording_source']}")
print(f"Max age: {camera_cfg['motion_recording']['max_age_days']} days")

# Test auto-resolution
camera_cfg = config.get_camera_config('68d49398005cf203e400043f', 'LL_HLS')
print(f"Auto-resolved source: {camera_cfg['motion_recording']['recording_source']}")
# Should output: mediamtx
```

### Test Storage Manager
```python
from services.recording.storage_manager import StorageManager

storage = StorageManager()

# Get storage statistics
stats = storage.get_storage_stats()
print(stats)

# Generate recording path
path = storage.generate_recording_path('AMCREST_LOBBY', 'motion')
print(f"Recording path: {path}")

# Check storage limits
limits = storage.check_storage_limits('motion')
print(f"Usage: {limits['usage_percent']}%")
```

### Test Recording Service (requires full app context)
```python
from services.recording.recording_service import RecordingService
from services.camera_repository import CameraRepository

camera_repo = CameraRepository('/app/config/cameras.json')
recording_service = RecordingService(camera_repo)

# Start test recording
recording_id = recording_service.start_motion_recording('AMCREST_LOBBY', duration=10)
print(f"Started recording: {recording_id}")

# Check active recordings
active = recording_service.get_active_recordings()
print(f"Active recordings: {active}")

# Stop recording after 10 seconds
time.sleep(10)
recording_service.stop_recording(recording_id)
```

---

## Known Limitations & TODO

### Not Yet Implemented
1. **MJPEG Service Recording** - Requires frame buffer→FFmpeg piping architecture
2. **Continuous Recording** - Similar to motion but without stop condition
3. **Flask Routes** - Web API for recording control
4. **Cleanup Scheduler** - Automated cleanup based on cron schedule
5. **Motion Detection Integration** - ONVIF event listeners, FFmpeg analysis

### Testing Needed
1. MediaMTX source recording (verify `-c copy` works)
2. Direct RTSP source recording (test camera capacity)
3. Storage limit enforcement
4. Concurrent recordings (multiple cameras)
5. Recording metadata persistence

### Future Enhancements
1. Pre/post buffer implementation (requires circular buffer)
2. Dynamic bitrate adjustment based on motion activity
3. Recording quality selection (main vs sub stream)
4. Archive to USB drive integration
5. Web UI for per-camera recording configuration

---

**End of Implementation Package**