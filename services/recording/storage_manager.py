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