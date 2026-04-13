"""
Storage Manager
Handles file path generation, storage space management, and recording lifecycle.
"""

import os
import re
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


def normalize_camera_name(camera_name: str) -> str:
    """
    Normalize camera name for filesystem-safe directory names.

    Rules:
    - Convert to uppercase
    - Replace spaces with underscores
    - Remove special characters (keep only A-Z, 0-9, underscore, hyphen)
    - Collapse multiple underscores
    - Limit to 50 characters

    Args:
        camera_name: Raw camera name (e.g., "REOLINK OFFICE", "Laundry Room")

    Returns:
        Normalized name (e.g., "REOLINK_OFFICE", "LAUNDRY_ROOM")
    """
    if not camera_name:
        return "UNKNOWN_CAMERA"

    # Convert to uppercase
    normalized = camera_name.upper()

    # Replace spaces with underscores
    normalized = normalized.replace(' ', '_')

    # Remove special characters (keep A-Z, 0-9, underscore, hyphen)
    normalized = re.sub(r'[^A-Z0-9_-]', '', normalized)

    # Collapse multiple underscores
    normalized = re.sub(r'_+', '_', normalized)

    # Remove leading/trailing underscores
    normalized = normalized.strip('_')

    # Limit to 50 characters
    if len(normalized) > 50:
        normalized = normalized[:50].rstrip('_')

    return normalized or "UNKNOWN_CAMERA"


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
        self.manual_path = self.base_path / "manual"
        self.buffer_path = self.base_path / "buffer"  # Pre-buffer rolling segments

        # Verify directories exist
        self._verify_directories()
        
        logger.info(f"StorageManager initialized - base: {self.base_path}")
    
    
    def _verify_directories(self):
        """Verify all storage directories exist and are writable."""
        # Core directories that must exist
        for path in [self.motion_path, self.continuous_path, self.snapshots_path, self.manual_path]:
            if not path.exists():
                logger.warning(f"Storage directory missing, creating: {path}")
                path.mkdir(parents=True, exist_ok=True)

            if not os.access(path, os.W_OK):
                logger.error(f"Storage directory not writable: {path}")
                raise PermissionError(f"Cannot write to: {path}")

        # Buffer directory is optional - create if it doesn't exist
        if not self.buffer_path.exists():
            try:
                self.buffer_path.mkdir(parents=True, exist_ok=True)
                logger.info(f"Created buffer directory: {self.buffer_path}")
            except Exception as e:
                logger.warning(f"Could not create buffer directory: {e}")

        logger.info("All storage directories verified")

    def _cleanup_empty_dirs(self, start_dir: Path, stop_at: Path):
        """
        Remove empty directories up to (but not including) stop_at.
        Used after deleting files to clean up empty YYYY/MM/DD directories.

        Args:
            start_dir: Directory to start checking (innermost)
            stop_at: Directory to stop at (camera directory - don't delete)
        """
        current = start_dir
        while current != stop_at and current.exists():
            try:
                if current.is_dir() and not any(current.iterdir()):
                    current.rmdir()
                    logger.debug(f"Removed empty directory: {current}")
                else:
                    break  # Not empty, stop climbing
            except Exception as e:
                logger.warning(f"Could not remove directory {current}: {e}")
                break
            current = current.parent

    def generate_recording_path(self, camera_id: str, recording_type: str = "motion",
                                  camera_name: Optional[str] = None) -> Path:
        """
        Generate unique file path for a new recording with per-camera directories.

        Args:
            camera_id: Camera identifier (serial number)
            recording_type: Type of recording ("motion", "continuous", "snapshot", "manual")
            camera_name: Human-readable camera name for directory naming

        Returns:
            Full path to recording file

        Directory structure:
            /recordings/motion/CAMERA_NAME/YYYY/MM/DD/
            /recordings/continuous/CAMERA_NAME/YYYY/MM/DD/
            /recordings/snapshots/CAMERA_NAME/YYYY/MM/DD/
            /recordings/manual/CAMERA_NAME/YYYY/MM/DD/

        File format: {camera_id}_YYYYMMDD_HHMMSS.mp4
        Example: /recordings/motion/REOLINK_OFFICE/2025/12/31/95270001CSO4BPDZ_20251231_143052.mp4
        """
        now = datetime.now()
        timestamp = now.strftime("%Y%m%d_%H%M%S")

        # Date-based subdirectories
        year = now.strftime("%Y")
        month = now.strftime("%m")
        day = now.strftime("%d")

        # Normalize camera name for directory - use camera_id if no name provided
        dir_name = normalize_camera_name(camera_name) if camera_name else normalize_camera_name(camera_id)

        if recording_type == "snapshot":
            filename = f"{camera_id}_{timestamp}.jpg"
            base_path = self.snapshots_path
        elif recording_type == "motion":
            filename = f"{camera_id}_{timestamp}.mp4"
            base_path = self.motion_path
        elif recording_type == "continuous":
            filename = f"{camera_id}_{timestamp}.mp4"
            base_path = self.continuous_path
        elif recording_type == "manual":
            filename = f"{camera_id}_{timestamp}.mp4"
            base_path = self.manual_path
        else:
            raise ValueError(f"Unknown recording type: {recording_type}")

        # Build full path: base/CAMERA_NAME/YYYY/MM/DD/
        camera_dir = base_path / dir_name / year / month / day

        # Ensure camera directory exists
        camera_dir.mkdir(parents=True, exist_ok=True)

        return camera_dir / filename
    
    
    def get_storage_stats(self) -> Dict[str, Dict]:
        """
        Get storage usage statistics for all tiers with configured limits.
        Handles per-camera subdirectories.

        Returns:
            Dictionary with usage stats per tier
        """
        limits = self.config.get_storage_limits()
        stats = {}

        tier_mapping = {
            "motion": ("motion_max_mb", self.motion_path),
            "continuous": ("continuous_max_mb", self.continuous_path),
            "snapshots": ("snapshots_max_mb", self.snapshots_path),
            "manual": ("manual_max_mb", self.manual_path)
        }

        for tier_name, (limit_key, path) in tier_mapping.items():
            # Count files in root and all subdirectories
            total_size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
            file_count = len([f for f in path.rglob("*") if f.is_file()])
            max_size_mb = limits.get(limit_key, 0)

            # Get per-camera breakdown (handles CAMERA/YYYY/MM/DD structure)
            camera_stats = {}
            for subdir in path.iterdir():
                if subdir.is_dir():
                    # Use rglob to find all files in nested date directories
                    cam_size = sum(f.stat().st_size for f in subdir.rglob("*") if f.is_file())
                    cam_count = len([f for f in subdir.rglob("*") if f.is_file()])
                    camera_stats[subdir.name] = {
                        "total_bytes": cam_size,
                        "total_mb": round(cam_size / 1024 / 1024, 2),
                        "file_count": cam_count
                    }

            stats[tier_name] = {
                "path": str(path),
                "total_bytes": total_size,
                "total_mb": round(total_size / 1024 / 1024, 2),
                "file_count": file_count,
                "max_size_mb": max_size_mb,
                "usage_percent": round((total_size / 1024 / 1024 / max_size_mb * 100), 2) if max_size_mb > 0 else 0,
                "cameras": camera_stats
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
    
    
    def cleanup_old_recordings(self, camera_id: str, recording_type: str = "motion",
                                camera_name: Optional[str] = None) -> int:
        """
        Delete recordings older than configured retention period for camera.
        Handles both flat structure (legacy) and per-camera subdirectories.

        Args:
            camera_id: Camera identifier (serial number)
            recording_type: Type of recordings to clean
            camera_name: Human-readable camera name for directory lookup

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
        elif recording_type == "manual":
            max_age_days = camera_cfg.get('manual_recording', {}).get('max_age_days', 30)
            target_path = self.manual_path
        else:
            raise ValueError(f"Unknown recording type: {recording_type}")

        deleted_count = 0
        cutoff_time = datetime.now().timestamp() - (max_age_days * 86400)

        # Determine file pattern
        pattern = f"{camera_id}_*.mp4" if recording_type != "snapshots" else f"{camera_id}_*.jpg"

        # Check per-camera subdirectory (handles CAMERA/YYYY/MM/DD structure)
        dir_name = normalize_camera_name(camera_name) if camera_name else normalize_camera_name(camera_id)
        camera_dir = target_path / dir_name

        # Search recursively in camera subdirectory (for date-based structure)
        if camera_dir.exists() and camera_dir.is_dir():
            for file_path in camera_dir.rglob(pattern):
                if file_path.stat().st_mtime < cutoff_time:
                    try:
                        file_path.unlink()
                        deleted_count += 1
                        logger.info(f"Deleted old recording: {file_path.name}")
                        # Clean up empty parent directories
                        self._cleanup_empty_dirs(file_path.parent, camera_dir)
                    except Exception as e:
                        logger.error(f"Failed to delete {file_path}: {e}")

        # Also check flat structure (legacy files in root)
        for file_path in target_path.glob(pattern):
            if file_path.is_file() and file_path.stat().st_mtime < cutoff_time:
                try:
                    file_path.unlink()
                    deleted_count += 1
                    logger.info(f"Deleted old recording (legacy): {file_path.name}")
                except Exception as e:
                    logger.error(f"Failed to delete {file_path}: {e}")

        if deleted_count > 0:
            logger.info(f"Cleanup complete: {deleted_count} files deleted for {camera_id} "
                       f"({recording_type}, retention: {max_age_days}d)")

        return deleted_count
    
    
    def cleanup_all_cameras(self, recording_type: str = "motion") -> Dict[str, int]:
        """
        Run cleanup for all cameras with recordings.
        Handles both flat structure (legacy) and per-camera subdirectories.

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
        elif recording_type == "manual":
            target_path = self.manual_path
            pattern = "*.mp4"
        else:
            raise ValueError(f"Unknown recording type: {recording_type}")

        # Extract unique camera IDs from filenames (both root and subdirectories)
        camera_ids = set()

        # Check files in root (legacy flat structure)
        for file_path in target_path.glob(pattern):
            if file_path.is_file():
                # Format: CAMERA_ID_YYYYMMDD_HHMMSS.ext
                camera_id = file_path.stem.rsplit('_', 2)[0]
                camera_ids.add(camera_id)

        # Check files in per-camera subdirectories
        for subdir in target_path.iterdir():
            if subdir.is_dir():
                for file_path in subdir.glob(pattern):
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

    def cleanup_buffer_directory(self, max_age_minutes: int = 5) -> int:
        """
        Clean up old buffer segments that weren't properly cleaned.

        This catches any orphaned segment files from crashed buffer processes.
        The SegmentBuffer class normally handles its own cleanup, but this
        is a safety net for files left behind.

        Args:
            max_age_minutes: Delete segments older than this (default 5 minutes)

        Returns:
            Number of files deleted
        """
        if not self.buffer_path.exists():
            return 0

        deleted = 0
        cutoff = datetime.now().timestamp() - (max_age_minutes * 60)

        # Check each camera's buffer directory
        for camera_dir in self.buffer_path.iterdir():
            if camera_dir.is_dir():
                # Delete old .ts segment files
                for seg_file in camera_dir.glob("*.ts"):
                    try:
                        if seg_file.stat().st_mtime < cutoff:
                            seg_file.unlink()
                            deleted += 1
                            logger.debug(f"Deleted orphaned buffer segment: {seg_file}")
                    except Exception as e:
                        logger.warning(f"Failed to delete buffer segment {seg_file}: {e}")

                # Also delete old segment list files
                for list_file in camera_dir.glob("segments.txt"):
                    try:
                        if list_file.stat().st_mtime < cutoff:
                            list_file.unlink()
                            logger.debug(f"Deleted orphaned segment list: {list_file}")
                    except Exception as e:
                        logger.warning(f"Failed to delete segment list {list_file}: {e}")

                # Clean up empty directories
                try:
                    if camera_dir.is_dir() and not any(camera_dir.iterdir()):
                        camera_dir.rmdir()
                        logger.debug(f"Removed empty buffer directory: {camera_dir}")
                except Exception as e:
                    logger.warning(f"Could not remove buffer directory {camera_dir}: {e}")

        if deleted > 0:
            logger.info(f"Cleaned up {deleted} orphaned buffer segments")

        return deleted

    def get_buffer_stats(self) -> Dict:
        """
        Get statistics about buffer directory usage.

        Returns:
            Dict with buffer storage statistics
        """
        if not self.buffer_path.exists():
            return {
                'exists': False,
                'total_bytes': 0,
                'total_mb': 0,
                'file_count': 0,
                'cameras': {}
            }

        total_size = 0
        file_count = 0
        camera_stats = {}

        for camera_dir in self.buffer_path.iterdir():
            if camera_dir.is_dir():
                cam_size = sum(f.stat().st_size for f in camera_dir.glob("*.ts") if f.is_file())
                cam_count = len([f for f in camera_dir.glob("*.ts") if f.is_file()])
                total_size += cam_size
                file_count += cam_count
                camera_stats[camera_dir.name] = {
                    'total_bytes': cam_size,
                    'total_mb': round(cam_size / 1024 / 1024, 2),
                    'segment_count': cam_count
                }

        return {
            'exists': True,
            'path': str(self.buffer_path),
            'total_bytes': total_size,
            'total_mb': round(total_size / 1024 / 1024, 2),
            'file_count': file_count,
            'cameras': camera_stats
        }