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
import json
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


@dataclass
class PreviewJob:
    """
    Tracks a preview merge job (temporary merged file for playback).

    Preview jobs create a temporary merged video that can be:
    - Played directly for preview (shows accurate total duration)
    - Promoted to a permanent export if user downloads

    Attributes:
        job_id: Unique job identifier
        camera_id: Camera being previewed
        segment_ids: List of recording IDs to merge
        status: Current job status
        progress_percent: 0-100 merge progress
        temp_dir: Temporary directory for merge files
        temp_file_path: Path to merged preview file
        error_message: Error details if failed
        created_at: Job creation timestamp
        ffmpeg_process: Popen object for cancellation
        total_duration_seconds: Total duration of merged video
        estimated_size_bytes: Estimated file size
    """
    job_id: str
    camera_id: str
    segment_ids: List[int] = field(default_factory=list)
    status: ExportStatus = ExportStatus.PENDING
    progress_percent: float = 0.0
    temp_dir: Optional[str] = None
    temp_file_path: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    ffmpeg_process: Optional[subprocess.Popen] = None
    total_duration_seconds: int = 0
    estimated_size_bytes: int = 0
    ios_compatible: bool = False

    def to_dict(self) -> Dict:
        """Convert to JSON-serializable dict."""
        return {
            'job_id': self.job_id,
            'camera_id': self.camera_id,
            'segment_count': len(self.segment_ids),
            'status': self.status.value,
            'progress_percent': self.progress_percent,
            'error_message': self.error_message,
            'created_at': self.created_at.isoformat(),
            'total_duration_seconds': self.total_duration_seconds,
            'estimated_size_bytes': self.estimated_size_bytes,
            'ios_compatible': self.ios_compatible
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

    # Config file path
    CONFIG_PATH = '/app/config/recording_settings.json'

    # Default iOS-compatible encoding settings (fallback if config not found)
    DEFAULT_IOS_ENCODING = {
        'video_codec': 'libx264',
        'video_profile': 'baseline',
        'video_level': '3.1',
        'audio_codec': 'aac',
        'audio_bitrate': '128k',
        'pixel_format': 'yuv420p',
        'preset': 'fast',
        'crf': '23',
        'movflags': '+faststart'
    }

    def __init__(self,
                 postgrest_url: str = "http://postgrest:3001",
                 export_dir: Optional[str] = None,
                 config_path: Optional[str] = None):
        """
        Initialize timeline service.

        Args:
            postgrest_url: PostgREST API endpoint for database queries
            export_dir: Directory for export output files
            config_path: Path to recording_settings.json config file
        """
        self.postgrest_url = postgrest_url
        self.export_dir = export_dir or self.EXPORT_DIR
        self.config_path = config_path or self.CONFIG_PATH

        # Active export jobs
        self.export_jobs: Dict[str, ExportJob] = {}
        self.jobs_lock = threading.RLock()

        # Active preview merge jobs
        self.preview_jobs: Dict[str, PreviewJob] = {}
        self.preview_lock = threading.RLock()

        # Load encoding settings from config
        self.ios_encoding = self._load_ios_encoding_settings()

        # Ensure export directory exists
        os.makedirs(self.export_dir, exist_ok=True)

        logger.info(f"TimelineService initialized - PostgREST: {postgrest_url}, Export dir: {self.export_dir}")

    def _load_ios_encoding_settings(self) -> Dict:
        """
        Load iOS encoding settings from recording_settings.json config file.

        Falls back to DEFAULT_IOS_ENCODING if config file not found or invalid.

        Returns:
            Dict with encoding settings (video_codec, video_profile, etc.)
        """
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r') as f:
                    config = json.load(f)

                # Get export_encoding.ios_compatible section
                export_encoding = config.get('export_encoding', {})
                ios_settings = export_encoding.get('ios_compatible', {})

                if ios_settings:
                    # Build encoding dict, excluding comments
                    encoding = {
                        k: v for k, v in ios_settings.items()
                        if not k.startswith('_')
                    }
                    logger.info(f"Loaded iOS encoding settings from config: {encoding}")
                    return encoding

            logger.warning(f"iOS encoding config not found at {self.config_path}, using defaults")
            return self.DEFAULT_IOS_ENCODING.copy()

        except Exception as e:
            logger.error(f"Error loading iOS encoding config: {e}, using defaults")
            return self.DEFAULT_IOS_ENCODING.copy()

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

    def get_segment_by_id(self, recording_id: int) -> Optional[TimelineSegment]:
        """
        Get a single recording segment by its database ID.

        Used for preview playback - fetches recording details and file path.

        Args:
            recording_id: Database recording ID

        Returns:
            TimelineSegment object or None if not found
        """
        logger.info(f"Fetching segment for recording ID: {recording_id}")

        try:
            # Query single recording from database
            response = requests.get(
                f"{self.postgrest_url}/recordings",
                params={
                    'id': f'eq.{recording_id}',
                    'limit': '1'
                },
                timeout=10
            )
            response.raise_for_status()
            recordings = response.json()

            if not recordings:
                logger.warning(f"Recording not found in database: {recording_id}")
                return None

            rec = recordings[0]

            # Parse timestamps
            rec_start = datetime.fromisoformat(rec['timestamp'].replace('Z', '+00:00'))

            # Calculate end time
            if rec.get('end_timestamp'):
                rec_end = datetime.fromisoformat(rec['end_timestamp'].replace('Z', '+00:00'))
            elif rec.get('duration_seconds'):
                rec_end = rec_start + timedelta(seconds=rec['duration_seconds'])
            else:
                rec_end = rec_start + timedelta(seconds=30)

            # Determine recording type
            if rec.get('motion_source') == 'manual':
                recording_type = 'manual'
            elif rec.get('motion_triggered'):
                recording_type = 'motion'
            else:
                recording_type = 'continuous'

            # Get file path
            file_path = rec.get('file_path')
            if not file_path:
                logger.warning(f"Recording {recording_id} has no file_path")
                return None

            segment = TimelineSegment(
                recording_id=rec['id'],
                camera_id=rec.get('camera_id', ''),
                start_time=rec_start,
                end_time=rec_end,
                duration_seconds=int((rec_end - rec_start).total_seconds()),
                file_path=file_path,
                file_size_bytes=rec.get('file_size_bytes', 0),
                recording_type=recording_type,
                has_audio=self._check_audio_track(file_path) if os.path.exists(file_path) else False
            )

            logger.info(f"Found segment: {segment.file_path}")
            return segment

        except requests.exceptions.RequestException as e:
            logger.error(f"Database query failed for recording {recording_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error fetching segment {recording_id}: {e}")
            return None

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
        logger.info(f"[Export {job_id}] Converting to iOS format (config: {self.config_path})...")

        ffmpeg_cmd = [
            'ffmpeg', '-y',
            '-i', input_file,
            # Video settings from config
            '-c:v', self.ios_encoding.get('video_codec', 'libx264'),
            '-profile:v', self.ios_encoding.get('video_profile', 'baseline'),
            '-level:v', self.ios_encoding.get('video_level', '3.1'),
            '-pix_fmt', self.ios_encoding.get('pixel_format', 'yuv420p'),
            # Audio settings from config
            '-c:a', self.ios_encoding.get('audio_codec', 'aac'),
            '-b:a', self.ios_encoding.get('audio_bitrate', '128k'),
            # Output optimization from config
            '-movflags', self.ios_encoding.get('movflags', '+faststart'),
            # Quality preset from config (use 'medium' for export vs 'fast' for preview)
            '-preset', 'medium',
            '-crf', str(self.ios_encoding.get('crf', 23)),
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

    # =========================================================================
    # Preview Merge Methods
    # =========================================================================

    def create_preview_merge(self, camera_id: str, segment_ids: List[int],
                             ios_compatible: bool = False) -> PreviewJob:
        """
        Create and start a preview merge job.

        Creates a temporary merged video file for preview playback.
        The merge runs in a background thread with cancellation support.

        Args:
            camera_id: Camera serial number
            segment_ids: List of recording IDs to merge
            ios_compatible: If True, re-encode to H.264 Baseline for iOS/mobile devices

        Returns:
            PreviewJob with job_id for tracking

        Raises:
            ValueError: If no valid segments found
        """
        job_id = str(uuid.uuid4())[:8]

        # Validate segments exist and calculate totals
        segments = []
        total_duration = 0
        total_size = 0

        for seg_id in segment_ids:
            segment = self.get_segment_by_id(seg_id)
            if segment and os.path.exists(segment.file_path):
                segments.append(segment)
                total_duration += segment.duration_seconds
                total_size += segment.file_size_bytes

        if not segments:
            raise ValueError(f"No valid recordings found for segment IDs: {segment_ids}")

        job = PreviewJob(
            job_id=job_id,
            camera_id=camera_id,
            segment_ids=segment_ids,
            total_duration_seconds=total_duration,
            estimated_size_bytes=total_size,
            ios_compatible=ios_compatible
        )

        with self.preview_lock:
            self.preview_jobs[job_id] = job

        # Start merge in background thread
        thread = threading.Thread(
            target=self._process_preview_merge,
            args=(job_id, segments),
            daemon=True,
            name=f"preview-merge-{job_id}"
        )
        thread.start()

        logger.info(f"Created preview merge job {job_id}: {len(segments)} segments, {total_duration}s duration, ios={ios_compatible}")
        return job

    def _process_preview_merge(self, job_id: str, segments: List[TimelineSegment]):
        """
        Process preview merge job (runs in background thread).

        Merges segments using FFmpeg concat demuxer.
        If ios_compatible is True, re-encodes to H.264 Baseline + AAC for iOS/mobile.
        Stores Popen object for cancellation support.

        Args:
            job_id: Preview job ID
            segments: List of TimelineSegment objects to merge
        """
        job = self.preview_jobs.get(job_id)
        if not job:
            return

        try:
            # Create temp directory for merge
            temp_dir = tempfile.mkdtemp(prefix=f"preview_{job_id}_")
            job.temp_dir = temp_dir
            concat_list = os.path.join(temp_dir, "concat.txt")
            output_file = os.path.join(temp_dir, "preview.mp4")

            # Update status
            job.status = ExportStatus.MERGING
            job.progress_percent = 5

            # Create concat list file
            with open(concat_list, 'w') as f:
                for seg in segments:
                    safe_path = seg.file_path.replace("'", "'\\''")
                    f.write(f"file '{safe_path}'\n")

            # Build FFmpeg command based on iOS compatibility setting
            if job.ios_compatible:
                # Re-encode to H.264 Baseline + AAC for iOS/mobile compatibility
                # This ensures playback works on iOS Safari, Android, and older devices
                # Settings loaded from config/recording_settings.json export_encoding.ios_compatible
                ffmpeg_cmd = [
                    'ffmpeg', '-y',
                    '-f', 'concat',
                    '-safe', '0',
                    '-i', concat_list,
                    # Video: H.264 Baseline profile (most compatible)
                    '-c:v', self.ios_encoding.get('video_codec', 'libx264'),
                    '-profile:v', self.ios_encoding.get('video_profile', 'baseline'),
                    '-level:v', self.ios_encoding.get('video_level', '3.1'),
                    '-pix_fmt', self.ios_encoding.get('pixel_format', 'yuv420p'),
                    # Audio: AAC (universally supported)
                    '-c:a', self.ios_encoding.get('audio_codec', 'aac'),
                    '-b:a', self.ios_encoding.get('audio_bitrate', '128k'),
                    # Encoding quality settings from config
                    '-preset', self.ios_encoding.get('preset', 'fast'),
                    '-crf', str(self.ios_encoding.get('crf', 23)),
                    # Enable fast start for streaming
                    '-movflags', self.ios_encoding.get('movflags', '+faststart'),
                    output_file
                ]
                logger.info(f"[Preview {job_id}] Merging {len(segments)} segments with iOS re-encoding (config: {self.config_path})...")
            else:
                # Fast stream copy (lossless, no re-encoding)
                ffmpeg_cmd = [
                    'ffmpeg', '-y',
                    '-f', 'concat',
                    '-safe', '0',
                    '-i', concat_list,
                    '-c', 'copy',
                    '-movflags', '+faststart',
                    output_file
                ]
                logger.info(f"[Preview {job_id}] Merging {len(segments)} segments (stream copy)...")

            # Use Popen for cancellation support
            job.ffmpeg_process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            # Update progress during merge (simplified - just show it's running)
            job.progress_percent = 50

            # Wait for completion (longer timeout for re-encoding)
            timeout = 3600 if job.ios_compatible else 1800  # 1 hour for iOS, 30 min otherwise
            stdout, stderr = job.ffmpeg_process.communicate(timeout=timeout)

            if job.ffmpeg_process.returncode == 0:
                job.status = ExportStatus.COMPLETED
                job.progress_percent = 100
                job.temp_file_path = output_file
                logger.info(f"[Preview {job_id}] Merge completed: {output_file}")
            else:
                raise RuntimeError(f"FFmpeg merge failed: {stderr.decode()[:500]}")

        except subprocess.TimeoutExpired:
            logger.error(f"[Preview {job_id}] Merge timed out")
            job.status = ExportStatus.FAILED
            job.error_message = "Merge timed out after 30 minutes"
            if job.ffmpeg_process:
                job.ffmpeg_process.kill()
            if job.temp_dir:
                shutil.rmtree(job.temp_dir, ignore_errors=True)

        except Exception as e:
            logger.error(f"[Preview {job_id}] Merge failed: {e}")
            job.status = ExportStatus.FAILED
            job.error_message = str(e)
            if job.temp_dir:
                shutil.rmtree(job.temp_dir, ignore_errors=True)

    def get_preview_job(self, job_id: str) -> Optional[PreviewJob]:
        """Get preview job by ID."""
        return self.preview_jobs.get(job_id)

    def cancel_preview_merge(self, job_id: str) -> bool:
        """
        Cancel a preview merge job.

        Terminates the FFmpeg subprocess and cleans up temp files.

        Args:
            job_id: Preview job ID

        Returns:
            True if cancelled, False if job not found or already done
        """
        job = self.preview_jobs.get(job_id)
        if not job:
            return False

        if job.status in [ExportStatus.COMPLETED, ExportStatus.FAILED, ExportStatus.CANCELLED]:
            return False

        # Terminate FFmpeg process
        if job.ffmpeg_process and job.ffmpeg_process.poll() is None:
            job.ffmpeg_process.terminate()
            try:
                job.ffmpeg_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                job.ffmpeg_process.kill()
                job.ffmpeg_process.wait()

        # Cleanup temp files
        if job.temp_dir and os.path.exists(job.temp_dir):
            shutil.rmtree(job.temp_dir, ignore_errors=True)

        job.status = ExportStatus.CANCELLED
        logger.info(f"[Preview {job_id}] Cancelled")
        return True

    def cleanup_preview(self, job_id: str) -> bool:
        """
        Delete temp preview files and remove job from tracking.

        Should be called when modal closes or after download.

        Args:
            job_id: Preview job ID

        Returns:
            True if cleaned up, False if job not found
        """
        job = self.preview_jobs.get(job_id)
        if not job:
            return False

        # Cancel if still running
        if job.status in [ExportStatus.PENDING, ExportStatus.PROCESSING, ExportStatus.MERGING]:
            self.cancel_preview_merge(job_id)

        # Delete temp directory
        if job.temp_dir and os.path.exists(job.temp_dir):
            shutil.rmtree(job.temp_dir, ignore_errors=True)
            logger.info(f"[Preview {job_id}] Temp files cleaned up")

        # Remove from tracking
        with self.preview_lock:
            if job_id in self.preview_jobs:
                del self.preview_jobs[job_id]

        return True

    def promote_preview_to_export(self, job_id: str, ios_compatible: bool = False) -> Optional[str]:
        """
        Promote a preview merge to a permanent export.

        Moves the temp file to the exports directory. If iOS compatible
        is requested but preview was already iOS-encoded, just moves the file.
        Only re-encodes if iOS is requested but preview wasn't iOS-encoded.

        Args:
            job_id: Preview job ID
            ios_compatible: Whether to convert for iOS

        Returns:
            Path to exported file, or None if failed

        Raises:
            ValueError: If preview not ready
        """
        job = self.preview_jobs.get(job_id)
        if not job:
            raise ValueError(f"Preview job not found: {job_id}")

        if job.status != ExportStatus.COMPLETED or not job.temp_file_path:
            raise ValueError(f"Preview not ready for export (status: {job.status.value})")

        if not os.path.exists(job.temp_file_path):
            raise ValueError("Preview file no longer exists")

        # Generate output filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f"{job.camera_id}_{timestamp}"

        # Check if preview was already iOS-encoded
        # If so, we can skip re-encoding even if ios_compatible is requested
        preview_already_ios = job.ios_compatible

        if ios_compatible and not preview_already_ios:
            # Need to re-encode for iOS (preview was stream-copy, not iOS-encoded)
            output_filename += "_ios.mp4"
            final_file = os.path.join(self.export_dir, output_filename)

            logger.info(f"[Preview {job_id}] Converting to iOS format (preview wasn't iOS-encoded)...")
            ffmpeg_cmd = [
                'ffmpeg', '-y',
                '-i', job.temp_file_path,
                '-c:v', self.ios_encoding.get('video_codec', 'libx264'),
                '-profile:v', self.ios_encoding.get('video_profile', 'baseline'),
                '-level:v', self.ios_encoding.get('video_level', '3.1'),
                '-pix_fmt', self.ios_encoding.get('pixel_format', 'yuv420p'),
                '-c:a', self.ios_encoding.get('audio_codec', 'aac'),
                '-b:a', self.ios_encoding.get('audio_bitrate', '128k'),
                '-movflags', self.ios_encoding.get('movflags', '+faststart'),
                '-preset', 'medium',
                '-crf', str(self.ios_encoding.get('crf', 23)),
                final_file
            ]

            result = subprocess.run(
                ffmpeg_cmd,
                capture_output=True,
                text=True,
                timeout=7200  # 2 hour timeout for conversion
            )

            if result.returncode != 0:
                raise RuntimeError(f"iOS conversion failed: {result.stderr[:500]}")
        else:
            # Either no iOS needed, or preview was already iOS-encoded - just move file
            if ios_compatible and preview_already_ios:
                output_filename += "_ios.mp4"
                logger.info(f"[Preview {job_id}] Preview already iOS-encoded, moving directly")
            else:
                output_filename += ".mp4"
            final_file = os.path.join(self.export_dir, output_filename)

            # Move file to exports
            shutil.move(job.temp_file_path, final_file)

        # Cleanup temp directory (but keep the job in memory for status queries)
        if job.temp_dir and os.path.exists(job.temp_dir):
            shutil.rmtree(job.temp_dir, ignore_errors=True)
        job.temp_dir = None
        job.temp_file_path = None

        logger.info(f"[Preview {job_id}] Promoted to export: {final_file}")
        return final_file


# Global singleton instance
timeline_service: Optional[TimelineService] = None


def get_timeline_service() -> TimelineService:
    """Get or create global TimelineService instance."""
    global timeline_service
    if timeline_service is None:
        timeline_service = TimelineService()
    return timeline_service
