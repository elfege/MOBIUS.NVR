"""
Timeline Playback Service
Manages video timeline queries, segment merging, and export functionality.

Supports:
- Query recordings by camera and time range
- Merge multiple video segments into single export file
- iOS-compatible video format conversion
- Progress tracking for long-running exports
"""

import os
import subprocess
import threading
import logging
import tempfile
import shutil
import time
import uuid
import requests
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Tuple
from pathlib import Path
from dataclasses import dataclass, field, asdict
from enum import Enum

logger = logging.getLogger(__name__)


class ExportStatus(Enum):
    """Export job status states."""
    PENDING = "pending"
    PROCESSING = "processing"
    MERGING = "merging"
    CONVERTING = "converting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TimelineSegment:
    """
    Represents a single recording segment on the timeline.

    Attributes:
        recording_id: Database recording ID
        camera_id: Camera serial number
        start_time: Segment start timestamp (UTC)
        end_time: Segment end timestamp (UTC)
        duration_seconds: Duration in seconds
        file_path: Path to video file
        file_size_bytes: File size
        recording_type: 'motion', 'continuous', 'manual'
        has_audio: Whether segment has audio track
    """
    recording_id: int
    camera_id: str
    start_time: datetime
    end_time: datetime
    duration_seconds: int
    file_path: str
    file_size_bytes: int
    recording_type: str
    has_audio: bool = False


@dataclass
class ExportJob:
    """
    Tracks a video export job through its lifecycle.

    Attributes:
        job_id: Unique job identifier
        camera_id: Camera being exported
        start_time: Export range start
        end_time: Export range end
        segments: List of segments to merge
        status: Current job status
        progress_percent: 0-100 progress
        output_path: Path to final exported file
        error_message: Error details if failed
        created_at: Job creation timestamp
        completed_at: Job completion timestamp
        ios_compatible: Whether to convert for iOS
    """
    job_id: str
    camera_id: str
    start_time: datetime
    end_time: datetime
    segments: List[TimelineSegment] = field(default_factory=list)
    status: ExportStatus = ExportStatus.PENDING
    progress_percent: float = 0.0
    output_path: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    ios_compatible: bool = False
    total_duration_seconds: int = 0
    estimated_size_bytes: int = 0

    def to_dict(self) -> Dict:
        """Convert to JSON-serializable dict."""
        return {
            'job_id': self.job_id,
            'camera_id': self.camera_id,
            'start_time': self.start_time.isoformat(),
            'end_time': self.end_time.isoformat(),
            'segment_count': len(self.segments),
            'status': self.status.value,
            'progress_percent': self.progress_percent,
            'output_path': self.output_path,
            'error_message': self.error_message,
            'created_at': self.created_at.isoformat(),
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'ios_compatible': self.ios_compatible,
            'total_duration_seconds': self.total_duration_seconds,
            'estimated_size_bytes': self.estimated_size_bytes
        }


class TimelineService:
    """
    Service for timeline queries, video merging, and export.

    Provides:
    - Query recordings by camera and time range
    - Merge multiple segments into single video
    - iOS-compatible format conversion (H.264 baseline + AAC)
    - Async export with progress tracking
    """

    # Recording base paths (matches docker-compose volumes)
    RECORDING_PATHS = {
        'motion': '/recordings/motion',
        'continuous': '/recordings/continuous',
        'manual': '/recordings/manual',
        'snapshots': '/recordings/snapshots'
    }

    # Export output directory
    EXPORT_DIR = '/recordings/exports'

    # iOS-compatible encoding settings
    # H.264 Baseline profile + AAC for maximum compatibility
    IOS_ENCODING = {
        'video_codec': 'libx264',
        'video_profile': 'baseline',
        'video_level': '3.1',
        'audio_codec': 'aac',
        'audio_bitrate': '128k',
        'pixel_format': 'yuv420p',
        'movflags': '+faststart'  # Enable streaming
    }

    def __init__(self,
                 postgrest_url: str = "http://postgrest:3001",
                 export_dir: Optional[str] = None):
        """
        Initialize timeline service.

        Args:
            postgrest_url: PostgREST API endpoint for database queries
            export_dir: Directory for export output files
        """
        self.postgrest_url = postgrest_url
        self.export_dir = export_dir or self.EXPORT_DIR

        # Active export jobs
        self.export_jobs: Dict[str, ExportJob] = {}
        self.jobs_lock = threading.RLock()

        # Ensure export directory exists
        os.makedirs(self.export_dir, exist_ok=True)

        logger.info(f"TimelineService initialized - PostgREST: {postgrest_url}, Export dir: {self.export_dir}")

    # =========================================================================
    # Timeline Query Methods
    # =========================================================================

    def get_timeline_segments(self,
                              camera_id: str,
                              start_time: datetime,
                              end_time: datetime,
                              recording_types: Optional[List[str]] = None) -> List[TimelineSegment]:
        """
        Query recordings for a camera within a time range.

        Args:
            camera_id: Camera serial number
            start_time: Range start (UTC)
            end_time: Range end (UTC)
            recording_types: Optional filter for recording types ['motion', 'continuous', 'manual']

        Returns:
            List of TimelineSegment objects sorted by start_time
        """
        logger.info(f"Querying timeline for {camera_id}: {start_time} to {end_time}")

        # Build PostgREST query
        # We want recordings where:
        # - timestamp >= start_time (recording started after our range starts)
        # - timestamp <= end_time (recording started before our range ends)
        # Note: We filter by start timestamp only since end_timestamp may not be populated
        # The Python code below handles partial overlaps at range boundaries
        #
        # PostgREST range query syntax: use 'and' operator for compound conditions
        # Format: column=operator.value&column=operator.value doesn't work for same column
        # Instead use select with filter or multiple query params with different ops

        # Query recordings that started within our time range
        # PostgREST supports multiple filters on same column when they're different operators
        url = f"{self.postgrest_url}/recordings"
        params = {
            'camera_id': f'eq.{camera_id}',
            'status': 'eq.completed',  # Only completed recordings
            'order': 'timestamp.asc',
            'limit': '1000'
        }

        # Add the timestamp range filter using PostgREST's 'and' syntax
        # We need both: timestamp >= start_time AND timestamp <= end_time
        params['and'] = f"(timestamp.gte.{start_time.isoformat()},timestamp.lte.{end_time.isoformat()})"

        try:
            response = requests.get(
                f"{self.postgrest_url}/recordings",
                params=params,
                timeout=30
            )
            response.raise_for_status()
            recordings = response.json()

        except requests.exceptions.RequestException as e:
            logger.error(f"PostgREST query failed: {e}")
            # Fall back to filesystem scan if database unavailable
            return self._scan_filesystem_for_segments(camera_id, start_time, end_time, recording_types)

        # Convert to TimelineSegment objects
        segments = []
        for rec in recordings:
            try:
                # Parse timestamps
                rec_start = datetime.fromisoformat(rec['timestamp'].replace('Z', '+00:00'))

                # Calculate end time
                if rec.get('end_timestamp'):
                    rec_end = datetime.fromisoformat(rec['end_timestamp'].replace('Z', '+00:00'))
                elif rec.get('duration_seconds'):
                    rec_end = rec_start + timedelta(seconds=rec['duration_seconds'])
                else:
                    # Estimate from file if no duration stored
                    rec_end = rec_start + timedelta(seconds=30)  # Default assumption

                # Skip if completely outside our range
                if rec_end < start_time or rec_start > end_time:
                    continue

                # Determine recording type from motion_triggered and motion_source
                if rec.get('motion_source') == 'manual':
                    recording_type = 'manual'
                elif rec.get('motion_triggered'):
                    recording_type = 'motion'
                else:
                    recording_type = 'continuous'

                # Filter by recording type if specified
                if recording_types and recording_type not in recording_types:
                    continue

                # Verify file exists
                file_path = rec.get('file_path')
                if not file_path or not os.path.exists(file_path):
                    logger.warning(f"Recording file not found: {file_path}")
                    continue

                segment = TimelineSegment(
                    recording_id=rec['id'],
                    camera_id=camera_id,
                    start_time=rec_start,
                    end_time=rec_end,
                    duration_seconds=int((rec_end - rec_start).total_seconds()),
                    file_path=file_path,
                    file_size_bytes=rec.get('file_size_bytes', 0),
                    recording_type=recording_type,
                    has_audio=self._check_audio_track(file_path)
                )
                segments.append(segment)

            except Exception as e:
                logger.error(f"Error parsing recording {rec.get('id')}: {e}")
                continue

        logger.info(f"Found {len(segments)} segments for {camera_id}")
        return segments

    def _scan_filesystem_for_segments(self,
                                      camera_id: str,
                                      start_time: datetime,
                                      end_time: datetime,
                                      recording_types: Optional[List[str]] = None) -> List[TimelineSegment]:
        """
        Fallback: Scan filesystem for recordings when database unavailable.

        Uses filename timestamp pattern: {camera_id}_{YYYYMMDD}_{HHMMSS}.mp4
        """
        logger.info(f"Scanning filesystem for {camera_id} recordings")
        segments = []

        types_to_scan = recording_types or ['motion', 'continuous', 'manual']

        for rec_type in types_to_scan:
            base_path = Path(self.RECORDING_PATHS.get(rec_type, ''))
            if not base_path.exists():
                continue

            # Search all camera subdirectories
            # Pattern: /recordings/{type}/{CAMERA_NAME}/{YYYY}/{MM}/{DD}/{camera_id}_*.mp4
            for mp4_file in base_path.rglob(f"{camera_id}_*.mp4"):
                try:
                    # Parse timestamp from filename
                    # Format: {camera_id}_{YYYYMMDD}_{HHMMSS}.mp4
                    name_parts = mp4_file.stem.split('_')
                    if len(name_parts) < 3:
                        continue

                    date_str = name_parts[-2]  # YYYYMMDD
                    time_str = name_parts[-1]  # HHMMSS

                    rec_start = datetime.strptime(f"{date_str}_{time_str}", "%Y%m%d_%H%M%S")

                    # Get duration from file
                    duration = self._get_video_duration(str(mp4_file))
                    rec_end = rec_start + timedelta(seconds=duration)

                    # Skip if outside range
                    if rec_end < start_time or rec_start > end_time:
                        continue

                    segment = TimelineSegment(
                        recording_id=0,  # No database ID
                        camera_id=camera_id,
                        start_time=rec_start,
                        end_time=rec_end,
                        duration_seconds=duration,
                        file_path=str(mp4_file),
                        file_size_bytes=mp4_file.stat().st_size,
                        recording_type=rec_type,
                        has_audio=self._check_audio_track(str(mp4_file))
                    )
                    segments.append(segment)

                except Exception as e:
                    logger.error(f"Error parsing file {mp4_file}: {e}")
                    continue

        # Sort by start time
        segments.sort(key=lambda s: s.start_time)
        logger.info(f"Filesystem scan found {len(segments)} segments for {camera_id}")
        return segments

    def _get_video_duration(self, file_path: str) -> int:
        """Get video duration in seconds using ffprobe."""
        try:
            result = subprocess.run([
                'ffprobe', '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                file_path
            ], capture_output=True, text=True, timeout=10)

            duration = float(result.stdout.strip())
            return int(duration)
        except Exception as e:
            logger.warning(f"Could not get duration for {file_path}: {e}")
            return 30  # Default assumption

    def _check_audio_track(self, file_path: str) -> bool:
        """Check if video file has an audio track using ffprobe."""
        try:
            result = subprocess.run([
                'ffprobe', '-v', 'error',
                '-select_streams', 'a',
                '-show_entries', 'stream=codec_type',
                '-of', 'csv=p=0',
                file_path
            ], capture_output=True, text=True, timeout=10)

            return 'audio' in result.stdout.lower()
        except Exception:
            return False

    # =========================================================================
    # Timeline Summary Methods
    # =========================================================================

    def get_timeline_summary(self,
                             camera_id: str,
                             start_time: datetime,
                             end_time: datetime,
                             bucket_minutes: int = 15) -> Dict:
        """
        Get timeline summary with recording coverage by time buckets.

        Useful for rendering timeline visualization showing gaps.

        Args:
            camera_id: Camera serial number
            start_time: Range start (UTC)
            end_time: Range end (UTC)
            bucket_minutes: Time bucket size in minutes

        Returns:
            Dict with:
            - buckets: List of time buckets with coverage info
            - total_coverage_seconds: Total recorded time
            - total_range_seconds: Total time range
            - coverage_percent: Percentage of time covered
            - gap_count: Number of gaps in coverage
        """
        segments = self.get_timeline_segments(camera_id, start_time, end_time)

        # Create time buckets
        buckets = []
        current = start_time
        bucket_delta = timedelta(minutes=bucket_minutes)

        while current < end_time:
            bucket_end = min(current + bucket_delta, end_time)

            # Find segments overlapping this bucket
            bucket_segments = []
            bucket_coverage = 0

            for seg in segments:
                # Check overlap
                overlap_start = max(current, seg.start_time)
                overlap_end = min(bucket_end, seg.end_time)

                if overlap_start < overlap_end:
                    overlap_seconds = (overlap_end - overlap_start).total_seconds()
                    bucket_coverage += overlap_seconds
                    bucket_segments.append({
                        'recording_id': seg.recording_id,
                        'type': seg.recording_type,
                        'overlap_seconds': int(overlap_seconds)
                    })

            bucket_duration = (bucket_end - current).total_seconds()
            buckets.append({
                'start': current.isoformat(),
                'end': bucket_end.isoformat(),
                'duration_seconds': int(bucket_duration),
                'coverage_seconds': int(bucket_coverage),
                'coverage_percent': round(bucket_coverage / bucket_duration * 100, 1) if bucket_duration > 0 else 0,
                'has_recording': len(bucket_segments) > 0,
                'segment_count': len(bucket_segments),
                'segments': bucket_segments
            })

            current = bucket_end

        # Calculate totals
        total_range = (end_time - start_time).total_seconds()
        total_coverage = sum(b['coverage_seconds'] for b in buckets)
        gap_count = sum(1 for b in buckets if not b['has_recording'])

        return {
            'camera_id': camera_id,
            'start_time': start_time.isoformat(),
            'end_time': end_time.isoformat(),
            'bucket_minutes': bucket_minutes,
            'buckets': buckets,
            'total_range_seconds': int(total_range),
            'total_coverage_seconds': int(total_coverage),
            'coverage_percent': round(total_coverage / total_range * 100, 1) if total_range > 0 else 0,
            'gap_count': gap_count,
            'segment_count': len(segments)
        }

    # =========================================================================
    # Video Export Methods
    # =========================================================================

    def create_export_job(self,
                          camera_id: str,
                          start_time: datetime,
                          end_time: datetime,
                          ios_compatible: bool = False,
                          recording_types: Optional[List[str]] = None) -> ExportJob:
        """
        Create a new video export job.

        Args:
            camera_id: Camera to export
            start_time: Export range start
            end_time: Export range end
            ios_compatible: Convert to iOS-compatible format
            recording_types: Optional filter for recording types

        Returns:
            ExportJob with job_id for tracking
        """
        job_id = str(uuid.uuid4())[:8]

        # Query segments for this range
        segments = self.get_timeline_segments(camera_id, start_time, end_time, recording_types)

        if not segments:
            raise ValueError(f"No recordings found for {camera_id} in specified range")

        # Calculate totals
        total_duration = sum(s.duration_seconds for s in segments)
        total_size = sum(s.file_size_bytes for s in segments)

        job = ExportJob(
            job_id=job_id,
            camera_id=camera_id,
            start_time=start_time,
            end_time=end_time,
            segments=segments,
            ios_compatible=ios_compatible,
            total_duration_seconds=total_duration,
            estimated_size_bytes=total_size
        )

        with self.jobs_lock:
            self.export_jobs[job_id] = job

        logger.info(f"Created export job {job_id}: {len(segments)} segments, {total_duration}s duration")
        return job

    def start_export(self, job_id: str) -> bool:
        """
        Start processing an export job asynchronously.

        Args:
            job_id: Export job ID

        Returns:
            True if started successfully
        """
        with self.jobs_lock:
            job = self.export_jobs.get(job_id)
            if not job:
                raise ValueError(f"Export job not found: {job_id}")

            if job.status != ExportStatus.PENDING:
                raise ValueError(f"Job {job_id} already started (status: {job.status.value})")

            job.status = ExportStatus.PROCESSING

        # Start processing in background thread
        thread = threading.Thread(
            target=self._process_export,
            args=(job_id,),
            daemon=True,
            name=f"export-{job_id}"
        )
        thread.start()

        return True

    def _process_export(self, job_id: str):
        """
        Process export job (runs in background thread).

        Steps:
        1. Create FFmpeg concat list
        2. Merge segments
        3. Optionally convert to iOS format
        4. Move to final location
        """
        job = self.export_jobs.get(job_id)
        if not job:
            return

        try:
            # Create temp directory for processing
            temp_dir = tempfile.mkdtemp(prefix=f"export_{job_id}_")
            concat_list = os.path.join(temp_dir, "concat.txt")
            merged_file = os.path.join(temp_dir, "merged.mp4")

            # Update status
            job.status = ExportStatus.MERGING
            job.progress_percent = 10

            # Create concat list file
            # FFmpeg concat demuxer format: file '/path/to/file.mp4'
            with open(concat_list, 'w') as f:
                for seg in job.segments:
                    # Escape single quotes in path
                    safe_path = seg.file_path.replace("'", "'\\''")
                    f.write(f"file '{safe_path}'\n")

            # Run FFmpeg concat
            # Using concat demuxer for lossless concatenation of same-codec files
            ffmpeg_cmd = [
                'ffmpeg', '-y',
                '-f', 'concat',
                '-safe', '0',
                '-i', concat_list,
                '-c', 'copy',  # Copy streams without re-encoding (fast)
                '-movflags', '+faststart',
                merged_file
            ]

            logger.info(f"[Export {job_id}] Merging {len(job.segments)} segments...")

            result = subprocess.run(
                ffmpeg_cmd,
                capture_output=True,
                text=True,
                timeout=3600  # 1 hour timeout for large exports
            )

            if result.returncode != 0:
                raise RuntimeError(f"FFmpeg merge failed: {result.stderr}")

            job.progress_percent = 50

            # Determine final output path
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_filename = f"{job.camera_id}_{timestamp}"

            if job.ios_compatible:
                # Convert to iOS-compatible format
                job.status = ExportStatus.CONVERTING
                job.progress_percent = 60

                output_filename += "_ios.mp4"
                final_file = os.path.join(self.export_dir, output_filename)

                self._convert_for_ios(merged_file, final_file, job_id)
            else:
                # Just move merged file to export directory
                output_filename += ".mp4"
                final_file = os.path.join(self.export_dir, output_filename)
                shutil.move(merged_file, final_file)

            # Cleanup temp directory
            shutil.rmtree(temp_dir, ignore_errors=True)

            # Update job status
            job.status = ExportStatus.COMPLETED
            job.progress_percent = 100
            job.output_path = final_file
            job.completed_at = datetime.now()

            logger.info(f"[Export {job_id}] Completed: {final_file}")

        except Exception as e:
            logger.error(f"[Export {job_id}] Failed: {e}")
            job.status = ExportStatus.FAILED
            job.error_message = str(e)
            job.completed_at = datetime.now()

            # Cleanup temp directory on failure
            if 'temp_dir' in locals():
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _convert_for_ios(self, input_file: str, output_file: str, job_id: str):
        """
        Convert video to iOS-compatible format.

        Uses H.264 Baseline profile + AAC audio for maximum compatibility
        with iOS Photos app and other Apple devices.

        Args:
            input_file: Source video file
            output_file: Destination file
            job_id: Job ID for progress tracking
        """
        logger.info(f"[Export {job_id}] Converting to iOS format...")

        ffmpeg_cmd = [
            'ffmpeg', '-y',
            '-i', input_file,
            # Video settings
            '-c:v', self.IOS_ENCODING['video_codec'],
            '-profile:v', self.IOS_ENCODING['video_profile'],
            '-level:v', self.IOS_ENCODING['video_level'],
            '-pix_fmt', self.IOS_ENCODING['pixel_format'],
            # Audio settings
            '-c:a', self.IOS_ENCODING['audio_codec'],
            '-b:a', self.IOS_ENCODING['audio_bitrate'],
            # Output optimization
            '-movflags', self.IOS_ENCODING['movflags'],
            # Reasonable quality preset
            '-preset', 'medium',
            '-crf', '23',
            output_file
        ]

        result = subprocess.run(
            ffmpeg_cmd,
            capture_output=True,
            text=True,
            timeout=7200  # 2 hour timeout for conversion
        )

        if result.returncode != 0:
            raise RuntimeError(f"iOS conversion failed: {result.stderr}")

        job = self.export_jobs.get(job_id)
        if job:
            job.progress_percent = 95

    def get_export_job(self, job_id: str) -> Optional[ExportJob]:
        """Get export job by ID."""
        return self.export_jobs.get(job_id)

    def get_export_jobs(self, camera_id: Optional[str] = None) -> List[ExportJob]:
        """
        Get all export jobs, optionally filtered by camera.

        Args:
            camera_id: Optional camera filter

        Returns:
            List of ExportJob objects
        """
        with self.jobs_lock:
            jobs = list(self.export_jobs.values())

            if camera_id:
                jobs = [j for j in jobs if j.camera_id == camera_id]

            return jobs

    def cancel_export(self, job_id: str) -> bool:
        """
        Cancel an export job if still processing.

        Args:
            job_id: Export job ID

        Returns:
            True if cancelled, False if already completed
        """
        with self.jobs_lock:
            job = self.export_jobs.get(job_id)
            if not job:
                return False

            if job.status in [ExportStatus.COMPLETED, ExportStatus.FAILED]:
                return False

            job.status = ExportStatus.CANCELLED
            job.completed_at = datetime.now()
            return True

    def cleanup_old_exports(self, max_age_hours: int = 24) -> int:
        """
        Delete export files older than specified age.

        Args:
            max_age_hours: Maximum age in hours

        Returns:
            Number of files deleted
        """
        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        deleted = 0

        try:
            export_path = Path(self.export_dir)
            for file in export_path.glob("*.mp4"):
                if datetime.fromtimestamp(file.stat().st_mtime) < cutoff:
                    file.unlink()
                    deleted += 1
                    logger.info(f"Deleted old export: {file}")
        except Exception as e:
            logger.error(f"Export cleanup error: {e}")

        # Also cleanup completed/failed jobs from memory
        with self.jobs_lock:
            old_jobs = [
                jid for jid, job in self.export_jobs.items()
                if job.completed_at and job.completed_at < cutoff
            ]
            for jid in old_jobs:
                del self.export_jobs[jid]

        return deleted


# Global singleton instance
timeline_service: Optional[TimelineService] = None


def get_timeline_service() -> TimelineService:
    """Get or create global TimelineService instance."""
    global timeline_service
    if timeline_service is None:
        timeline_service = TimelineService()
    return timeline_service
