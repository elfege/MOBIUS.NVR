"""
routes/recording.py — Flask Blueprint for recording, timeline, file browser,
and recordings download API routes.

Covers:
- Recording settings, start/stop, active recordings
- Timeline segments, summary, export (create/status/start/cancel/download/stream)
- Timeline preview (by recording ID)
- Preview-merge (create/status/cancel/stream/cleanup/promote)
- Export download/stream by filename
- Alternate-storage file browser (browse/stream/download)
- Main recordings storage download

All service singletons are accessed via routes.shared to avoid circular imports.
The timeline service is retrieved via get_timeline_service() (a factory/singleton
accessor from services.recording.timeline_service) rather than the shared registry,
because it manages its own internal state and may not yet be registered at import time.
"""

import logging
import os
import re

import requests
from flask import Blueprint, Response, jsonify, request, send_file
from flask_login import current_user, login_required

import routes.shared as shared
from routes.helpers import csrf_exempt
from services.recording.timeline_service import ExportStatus, get_timeline_service

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Blueprint definition
# ---------------------------------------------------------------------------

recording_bp = Blueprint('recording', __name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Base path for alternate recordings — mounted in docker-compose.yml
ALTERNATE_RECORDING_BASE = '/recordings/ALTERNATE'

# Base path for main recordings storage
RECORDINGS_BASE = '/recordings'


########################################################
#           RECORDING API ROUTES
########################################################

@recording_bp.route('/api/recording/settings/<camera_id>', methods=['GET', 'POST'])
@csrf_exempt
@login_required
def api_recording_settings(camera_id):
    """Get or update recording settings for a camera."""
    if not shared.recording_service:
        return jsonify({'error': 'Recording service not available'}), 503

    try:
        if request.method == 'GET':
            camera = shared.camera_repo.get_camera(camera_id)
            if not camera:
                return jsonify({'error': 'Camera not found'}), 404

            settings = shared.recording_service.config.get_camera_settings(camera_id)

            return jsonify({
                'camera_id': camera_id,
                'camera_name': camera.get('name', camera_id),
                'settings': settings
            })

        else:  # POST
            data = request.get_json()
            if not data:
                return jsonify({'error': 'No data provided'}), 400

            shared.recording_service.config.update_camera_settings(camera_id, data)
            shared.recording_service.config.reload()

            return jsonify({
                'success': True,
                'camera_id': camera_id,
                'message': 'Settings updated successfully'
            })

    except Exception as e:
        logger.error(f"Recording settings API error for {camera_id}: {e}")
        return jsonify({'error': str(e)}), 500


@recording_bp.route('/api/recording/<camera_id>/start', methods=['POST'])
@csrf_exempt
@login_required
def api_recording_start(camera_id):
    """Start manual recording for a camera."""
    if not shared.recording_service:
        return jsonify({'error': 'Recording service not available'}), 503

    try:
        camera = shared.camera_repo.get_camera(camera_id)
        if not camera:
            return jsonify({'error': 'Camera not found'}), 404

        data = request.get_json() or {}
        duration = data.get('duration', 30)  # Default 30 seconds if not specified

        # Use start_motion_recording for manual recordings too
        recording_id = shared.recording_service.start_manual_recording(camera_id, duration=duration)

        if not recording_id:
            return jsonify({
                'success': False,
                'error': 'Failed to start recording'
            }), 500

        return jsonify({
            'success': True,
            'recording_id': recording_id,
            'camera_id': camera_id,
            'duration': duration,
            'message': 'Recording started'
        })

    except Exception as e:
        logger.error(f"Start recording API error for {camera_id}: {e}")
        return jsonify({'error': str(e)}), 500


@recording_bp.route('/api/recording/<camera_id>/stop', methods=['POST'])
@csrf_exempt
@login_required
def api_recording_stop(camera_id):
    """Stop an active recording by camera ID (recording_id passed as camera_id parameter)."""
    if not shared.recording_service:
        return jsonify({'error': 'Recording service not available'}), 503

    try:
        success = shared.recording_service.stop_recording(camera_id)

        if not success:
            return jsonify({
                'success': False,
                'error': 'Failed to stop recording or recording not found'
            }), 404

        return jsonify({
            'success': True,
            'recording_id': camera_id,
            'message': 'Recording stopped'
        })

    except Exception as e:
        logger.error(f"Stop recording API error for {camera_id}: {e}")
        return jsonify({'error': str(e)}), 500


@recording_bp.route('/api/recording/active', methods=['GET'])
@login_required
def api_recording_active():
    """Get list of all currently active recordings."""
    if not shared.recording_service:
        return jsonify({'error': 'Recording service not available'}), 503

    try:
        active_recordings = shared.recording_service.get_active_recordings()

        return jsonify({
            'success': True,
            'count': len(active_recordings),
            'recordings': active_recordings
        })

    except Exception as e:
        logger.error(f"Get active recordings API error: {e}")
        return jsonify({'error': str(e)}), 500


########################################################
#           TIMELINE PLAYBACK API ROUTES
########################################################

@recording_bp.route('/api/timeline/segments/<camera_id>', methods=['GET'])
@login_required
def api_timeline_segments(camera_id: str):
    """
    Get timeline segments for a camera within a time range.

    Query Parameters:
        start: ISO timestamp (required) - Range start
        end: ISO timestamp (required) - Range end
        types: Comma-separated recording types (optional) - motion,continuous,manual

    Returns:
        List of recording segments with file paths and metadata
    """
    try:
        # Parse time range from query params
        start_str = request.args.get('start')
        end_str = request.args.get('end')

        if not start_str or not end_str:
            return jsonify({'error': 'start and end parameters required'}), 400

        try:
            from datetime import datetime, timezone
            import pytz

            # Parse timestamps - if no timezone provided, assume local time (EST)
            local_tz = pytz.timezone('America/New_York')

            start_time = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
            end_time = datetime.fromisoformat(end_str.replace('Z', '+00:00'))

            # If naive (no timezone), assume local time and convert to UTC
            if start_time.tzinfo is None:
                start_time = local_tz.localize(start_time).astimezone(timezone.utc)
            if end_time.tzinfo is None:
                end_time = local_tz.localize(end_time).astimezone(timezone.utc)

            logger.debug(f"Timeline segments query: {start_str} -> {start_time.isoformat()} (UTC)")
        except ValueError as e:
            return jsonify({'error': f'Invalid timestamp format: {e}'}), 400

        # Optional recording type filter
        types_str = request.args.get('types')
        recording_types = types_str.split(',') if types_str else None

        # Get timeline service
        timeline_service = get_timeline_service()

        # Query segments
        segments = timeline_service.get_timeline_segments(
            camera_id, start_time, end_time, recording_types
        )

        return jsonify({
            'success': True,
            'camera_id': camera_id,
            'start': start_time.isoformat(),
            'end': end_time.isoformat(),
            'segment_count': len(segments),
            'segments': [
                {
                    'recording_id': seg.recording_id,
                    'start_time': seg.start_time.isoformat(),
                    'end_time': seg.end_time.isoformat(),
                    'duration_seconds': seg.duration_seconds,
                    'file_path': seg.file_path,
                    'file_size_bytes': seg.file_size_bytes,
                    'recording_type': seg.recording_type,
                    'has_audio': seg.has_audio
                }
                for seg in segments
            ]
        })

    except Exception as e:
        logger.error(f"Timeline segments API error for {camera_id}: {e}")
        return jsonify({'error': str(e)}), 500


@recording_bp.route('/api/timeline/summary/<camera_id>', methods=['GET'])
@login_required
def api_timeline_summary(camera_id: str):
    """
    Get timeline summary with recording coverage by time buckets.

    Query Parameters:
        start: ISO timestamp (required) - Range start
        end: ISO timestamp (required) - Range end
        bucket_minutes: Bucket size in minutes (optional, default: 15)

    Returns:
        Summary with time buckets showing recording coverage and gaps
    """
    try:
        # Parse parameters
        start_str = request.args.get('start')
        end_str = request.args.get('end')
        bucket_minutes = int(request.args.get('bucket_minutes', 15))

        if not start_str or not end_str:
            return jsonify({'error': 'start and end parameters required'}), 400

        try:
            from datetime import datetime, timezone
            import pytz

            # Parse timestamps - if no timezone provided, assume local time (EST)
            local_tz = pytz.timezone('America/New_York')

            start_time = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
            end_time = datetime.fromisoformat(end_str.replace('Z', '+00:00'))

            # If naive (no timezone), assume local time and convert to UTC
            if start_time.tzinfo is None:
                start_time = local_tz.localize(start_time).astimezone(timezone.utc)
            if end_time.tzinfo is None:
                end_time = local_tz.localize(end_time).astimezone(timezone.utc)

            logger.debug(f"Timeline summary query: {start_str} -> {start_time.isoformat()} (UTC)")
        except ValueError as e:
            return jsonify({'error': f'Invalid timestamp format: {e}'}), 400

        timeline_service = get_timeline_service()

        summary = timeline_service.get_timeline_summary(
            camera_id, start_time, end_time, bucket_minutes
        )

        return jsonify({
            'success': True,
            **summary
        })

    except Exception as e:
        logger.error(f"Timeline summary API error for {camera_id}: {e}")
        return jsonify({'error': str(e)}), 500


@recording_bp.route('/api/timeline/export', methods=['POST'])
@csrf_exempt
@login_required
def api_timeline_export_create():
    """
    Create a video export job for a time range.

    Request Body (JSON):
        camera_id: Camera serial number (required)
        start: ISO timestamp (required) - Export range start
        end: ISO timestamp (required) - Export range end
        ios_compatible: Boolean (optional) - Convert to iOS format
        types: List of recording types (optional) - ['motion', 'continuous', 'manual']
        auto_start: Boolean (optional, default: true) - Start processing immediately

    Returns:
        Export job details with job_id for tracking
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'JSON body required'}), 400

        camera_id = data.get('camera_id')
        start_str = data.get('start')
        end_str = data.get('end')

        if not camera_id or not start_str or not end_str:
            return jsonify({'error': 'camera_id, start, and end are required'}), 400

        try:
            from datetime import datetime
            start_time = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
            end_time = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
        except ValueError as e:
            return jsonify({'error': f'Invalid timestamp format: {e}'}), 400

        ios_compatible = data.get('ios_compatible', False)
        recording_types = data.get('types')
        auto_start = data.get('auto_start', True)

        timeline_service = get_timeline_service()

        # Create export job
        job = timeline_service.create_export_job(
            camera_id=camera_id,
            start_time=start_time,
            end_time=end_time,
            ios_compatible=ios_compatible,
            recording_types=recording_types
        )

        # Optionally start processing immediately
        if auto_start:
            timeline_service.start_export(job.job_id)

        return jsonify({
            'success': True,
            'job': job.to_dict()
        })

    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Timeline export create API error: {e}")
        return jsonify({'error': str(e)}), 500


@recording_bp.route('/api/timeline/export/<export_id>', methods=['GET'])
@login_required
def api_timeline_export_status(export_id: str):
    """
    Get export job status.

    Returns:
        Export job details including progress and output path when complete
    """
    try:
        timeline_service = get_timeline_service()
        job = timeline_service.get_export_job(export_id)

        if not job:
            return jsonify({'error': f'Export job not found: {export_id}'}), 404

        return jsonify({
            'success': True,
            'job': job.to_dict()
        })

    except Exception as e:
        logger.error(f"Timeline export status API error for {export_id}: {e}")
        return jsonify({'error': str(e)}), 500


@recording_bp.route('/api/timeline/export/<export_id>/start', methods=['POST'])
@csrf_exempt
@login_required
def api_timeline_export_start(export_id: str):
    """
    Start processing a pending export job.

    Use this if auto_start was false when creating the job.
    """
    try:
        timeline_service = get_timeline_service()
        timeline_service.start_export(export_id)

        job = timeline_service.get_export_job(export_id)
        return jsonify({
            'success': True,
            'job': job.to_dict()
        })

    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Timeline export start API error for {export_id}: {e}")
        return jsonify({'error': str(e)}), 500


@recording_bp.route('/api/timeline/export/<export_id>/cancel', methods=['POST'])
@csrf_exempt
@login_required
def api_timeline_export_cancel(export_id: str):
    """Cancel a pending or processing export job."""
    try:
        timeline_service = get_timeline_service()
        cancelled = timeline_service.cancel_export(export_id)

        if not cancelled:
            job = timeline_service.get_export_job(export_id)
            if not job:
                return jsonify({'error': f'Export job not found: {export_id}'}), 404
            return jsonify({'error': f'Cannot cancel job in status: {job.status.value}'}), 400

        return jsonify({
            'success': True,
            'message': f'Export job {export_id} cancelled'
        })

    except Exception as e:
        logger.error(f"Timeline export cancel API error for {export_id}: {e}")
        return jsonify({'error': str(e)}), 500


@recording_bp.route('/api/timeline/export/<export_id>/download', methods=['GET'])
@login_required
def api_timeline_export_download(export_id: str):
    """
    Download completed export file.

    Returns:
        Video file as attachment for download
    """
    try:
        timeline_service = get_timeline_service()
        job = timeline_service.get_export_job(export_id)

        if not job:
            return jsonify({'error': f'Export job not found: {export_id}'}), 404

        if job.status.value != 'completed':
            return jsonify({'error': f'Export not ready (status: {job.status.value})'}), 400

        if not job.output_path or not os.path.exists(job.output_path):
            return jsonify({'error': 'Export file not found'}), 404

        # Return file for download
        return send_file(
            job.output_path,
            mimetype='video/mp4',
            as_attachment=True,
            download_name=os.path.basename(job.output_path)
        )

    except Exception as e:
        logger.error(f"Timeline export download API error for {export_id}: {e}")
        return jsonify({'error': str(e)}), 500


@recording_bp.route('/api/timeline/export/<export_id>/stream', methods=['GET'])
@login_required
def api_timeline_export_stream(export_id: str):
    """
    Stream export file for inline playback (iOS save workaround).

    Unlike /download, this streams for playback (not as attachment),
    allowing iOS users to long-press the video to save it.

    Supports HTTP Range requests for seeking.

    Returns:
        Video stream with appropriate headers for inline playback
    """
    try:
        timeline_service = get_timeline_service()
        job = timeline_service.get_export_job(export_id)

        if not job:
            return jsonify({'error': f'Export job not found: {export_id}'}), 404

        if job.status.value != 'completed':
            return jsonify({'error': f'Export not ready (status: {job.status.value})'}), 400

        file_path = job.output_path
        if not file_path or not os.path.exists(file_path):
            return jsonify({'error': 'Export file not found'}), 404

        file_size = os.path.getsize(file_path)

        # Handle Range requests for video seeking
        range_header = request.headers.get('Range')
        if range_header:
            # Parse range header (e.g., "bytes=0-1023")
            byte_start = 0
            byte_end = file_size - 1

            match = re.match(r'bytes=(\d*)-(\d*)', range_header)
            if match:
                if match.group(1):
                    byte_start = int(match.group(1))
                if match.group(2):
                    byte_end = int(match.group(2))

            # Clamp to file size
            byte_end = min(byte_end, file_size - 1)
            content_length = byte_end - byte_start + 1

            def generate_range():
                with open(file_path, 'rb') as f:
                    f.seek(byte_start)
                    remaining = content_length
                    while remaining > 0:
                        chunk_size = min(8192, remaining)
                        data = f.read(chunk_size)
                        if not data:
                            break
                        remaining -= len(data)
                        yield data

            response = Response(
                generate_range(),
                status=206,
                mimetype='video/mp4',
                direct_passthrough=True
            )
            response.headers['Content-Range'] = f'bytes {byte_start}-{byte_end}/{file_size}'
            response.headers['Content-Length'] = content_length
            response.headers['Accept-Ranges'] = 'bytes'
            return response

        # Full file request (no Range header)
        return send_file(
            file_path,
            mimetype='video/mp4',
            as_attachment=False  # Inline playback, not download
        )

    except Exception as e:
        logger.error(f"Timeline export stream API error for {export_id}: {e}")
        return jsonify({'error': str(e)}), 500


@recording_bp.route('/api/timeline/preview/<int:recording_id>', methods=['GET'])
@login_required
def api_timeline_preview(recording_id: int):
    """
    Stream a recording file for in-browser preview playback.

    Supports HTTP Range requests for seeking in video players.

    Args:
        camera_id: Database recording ID (passed as camera_id parameter per route spec)

    Returns:
        Video file stream with appropriate headers for playback
    """
    try:
        timeline_service = get_timeline_service()

        # Get recording details from database
        segment = timeline_service.get_segment_by_id(recording_id)

        if not segment:
            return jsonify({'error': 'Recording not found'}), 404

        file_path = segment.file_path

        if not os.path.exists(file_path):
            logger.error(f"Preview file not found: {file_path}")
            return jsonify({'error': 'Recording file not found on disk'}), 404

        # Get file size for range request support
        file_size = os.path.getsize(file_path)

        # Handle Range requests for video seeking
        range_header = request.headers.get('Range')

        if range_header:
            # Parse range header (e.g., "bytes=0-1024")
            byte_start = 0
            byte_end = file_size - 1

            match = range_header.replace('bytes=', '').split('-')
            if match[0]:
                byte_start = int(match[0])
            if match[1]:
                byte_end = int(match[1])

            # Ensure end doesn't exceed file size
            byte_end = min(byte_end, file_size - 1)
            content_length = byte_end - byte_start + 1

            def generate_range():
                with open(file_path, 'rb') as f:
                    f.seek(byte_start)
                    remaining = content_length
                    chunk_size = 8192
                    while remaining > 0:
                        chunk = f.read(min(chunk_size, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk

            response = Response(
                generate_range(),
                status=206,
                mimetype='video/mp4',
                direct_passthrough=True
            )
            response.headers['Content-Range'] = f'bytes {byte_start}-{byte_end}/{file_size}'
            response.headers['Accept-Ranges'] = 'bytes'
            response.headers['Content-Length'] = content_length
            return response

        # Full file response (no range requested)
        return send_file(
            file_path,
            mimetype='video/mp4',
            as_attachment=False
        )

    except Exception as e:
        logger.error(f"Timeline preview API error for recording {recording_id}: {e}")
        return jsonify({'error': str(e)}), 500


@recording_bp.route('/api/timeline/exports', methods=['GET'])
@login_required
def api_timeline_export_list():
    """
    List all export jobs, optionally filtered by camera.

    Query Parameters:
        camera_id: Optional camera filter
    """
    try:
        camera_id = request.args.get('camera_id')
        timeline_service = get_timeline_service()

        jobs = timeline_service.get_export_jobs(camera_id)

        return jsonify({
            'success': True,
            'count': len(jobs),
            'jobs': [job.to_dict() for job in jobs]
        })

    except Exception as e:
        logger.error(f"Timeline export list API error: {e}")
        return jsonify({'error': str(e)}), 500


########################################################
#           PREVIEW MERGE API ROUTES
########################################################

@recording_bp.route('/api/timeline/preview-merge', methods=['POST'])
@csrf_exempt
@login_required
def api_timeline_preview_merge_create():
    """
    Create a merged preview from selected segment IDs.

    Merges multiple recording segments into a single temporary MP4 file
    for preview playback. The merge runs asynchronously.

    Request Body:
        camera_id: Camera serial number
        segment_ids: List of recording IDs to merge
        ios_compatible: (optional) If true, re-encode to H.264 Baseline for iOS/mobile

    Returns:
        job_id for tracking merge progress
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No JSON data provided'}), 400

        camera_id = data.get('camera_id')
        segment_ids = data.get('segment_ids', [])
        ios_compatible = data.get('ios_compatible', False)

        if not camera_id:
            return jsonify({'error': 'camera_id is required'}), 400
        if not segment_ids or not isinstance(segment_ids, list):
            return jsonify({'error': 'segment_ids must be a non-empty list'}), 400

        timeline_service = get_timeline_service()
        job = timeline_service.create_preview_merge(camera_id, segment_ids, ios_compatible)

        return jsonify({
            'success': True,
            'job_id': job.job_id,
            'job': job.to_dict()
        })

    except ValueError as e:
        logger.warning(f"Preview merge validation error: {e}")
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Preview merge create error: {e}")
        return jsonify({'error': str(e)}), 500


@recording_bp.route('/api/timeline/preview-merge/<merge_id>', methods=['GET'])
@login_required
def api_timeline_preview_merge_status(merge_id: str):
    """
    Get preview merge job status and progress.

    Args:
        merge_id: Preview job ID

    Returns:
        Job status including progress_percent, status, error_message
    """
    try:
        timeline_service = get_timeline_service()
        job = timeline_service.get_preview_job(merge_id)

        if not job:
            return jsonify({'error': 'Preview job not found'}), 404

        return jsonify({
            'success': True,
            'job': job.to_dict()
        })

    except Exception as e:
        logger.error(f"Preview merge status error for {merge_id}: {e}")
        return jsonify({'error': str(e)}), 500


@recording_bp.route('/api/timeline/preview-merge/<merge_id>/cancel', methods=['POST'])
@csrf_exempt
@login_required
def api_timeline_preview_merge_cancel(merge_id: str):
    """
    Cancel a preview merge job.

    Terminates the FFmpeg process and cleans up temp files.

    Args:
        merge_id: Preview job ID
    """
    try:
        timeline_service = get_timeline_service()
        cancelled = timeline_service.cancel_preview_merge(merge_id)

        if not cancelled:
            return jsonify({
                'success': False,
                'error': 'Job not found or already completed'
            }), 404

        return jsonify({'success': True})

    except Exception as e:
        logger.error(f"Preview merge cancel error for {merge_id}: {e}")
        return jsonify({'error': str(e)}), 500


@recording_bp.route('/api/timeline/preview-merge/<merge_id>/stream', methods=['GET'])
@login_required
def api_timeline_preview_merge_stream(merge_id: str):
    """
    Stream the merged preview file for playback.

    Supports HTTP Range requests for video seeking.

    Args:
        merge_id: Preview job ID

    Returns:
        Video file stream with appropriate headers
    """
    try:
        timeline_service = get_timeline_service()
        job = timeline_service.get_preview_job(merge_id)

        if not job:
            return jsonify({'error': 'Preview job not found'}), 404

        if job.status != ExportStatus.COMPLETED:
            return jsonify({'error': f'Preview not ready (status: {job.status.value})'}), 400

        if not job.temp_file_path or not os.path.exists(job.temp_file_path):
            return jsonify({'error': 'Preview file not found'}), 404

        file_path = job.temp_file_path
        file_size = os.path.getsize(file_path)

        # Handle Range requests for video seeking
        range_header = request.headers.get('Range')

        if range_header:
            byte_start = 0
            byte_end = file_size - 1

            match = range_header.replace('bytes=', '').split('-')
            if match[0]:
                byte_start = int(match[0])
            if match[1]:
                byte_end = int(match[1])

            byte_end = min(byte_end, file_size - 1)
            content_length = byte_end - byte_start + 1

            def generate_range():
                with open(file_path, 'rb') as f:
                    f.seek(byte_start)
                    remaining = content_length
                    chunk_size = 8192
                    while remaining > 0:
                        chunk = f.read(min(chunk_size, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk

            response = Response(
                generate_range(),
                status=206,
                mimetype='video/mp4',
                direct_passthrough=True
            )
            response.headers['Content-Range'] = f'bytes {byte_start}-{byte_end}/{file_size}'
            response.headers['Accept-Ranges'] = 'bytes'
            response.headers['Content-Length'] = content_length
            return response

        # Full file response
        return send_file(
            file_path,
            mimetype='video/mp4',
            as_attachment=False
        )

    except Exception as e:
        logger.error(f"Preview merge stream error for {merge_id}: {e}")
        return jsonify({'error': str(e)}), 500


@recording_bp.route('/api/timeline/preview-merge/<merge_id>/cleanup', methods=['DELETE'])
@csrf_exempt
@login_required
def api_timeline_preview_merge_cleanup(merge_id: str):
    """
    Delete temp preview files and cleanup resources.

    Should be called when modal closes or after download.

    Args:
        merge_id: Preview job ID
    """
    try:
        timeline_service = get_timeline_service()
        cleaned = timeline_service.cleanup_preview(merge_id)

        if not cleaned:
            return jsonify({
                'success': False,
                'error': 'Job not found'
            }), 404

        return jsonify({'success': True})

    except Exception as e:
        logger.error(f"Preview merge cleanup error for {merge_id}: {e}")
        return jsonify({'error': str(e)}), 500


@recording_bp.route('/api/timeline/preview-merge/<merge_id>/promote', methods=['POST'])
@csrf_exempt
@login_required
def api_timeline_preview_merge_promote(merge_id: str):
    """
    Promote a preview merge to a permanent export.

    Moves the temp file to exports directory. Optionally converts for iOS.

    Args:
        merge_id: Preview job ID

    Request Body:
        ios_compatible: bool - Whether to convert for iOS (optional, default false)

    Returns:
        download_url for the exported file
    """
    try:
        data = request.get_json() or {}
        ios_compatible = data.get('ios_compatible', False)

        timeline_service = get_timeline_service()
        export_path = timeline_service.promote_preview_to_export(merge_id, ios_compatible)

        if not export_path:
            return jsonify({'error': 'Promotion failed'}), 500

        # Build download URL
        filename = os.path.basename(export_path)
        download_url = f'/api/recording/export/download/{filename}'

        return jsonify({
            'success': True,
            'export_path': export_path,
            'download_url': download_url,
            'filename': filename
        })

    except ValueError as e:
        logger.warning(f"Preview promote validation error for {merge_id}: {e}")
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Preview promote error for {merge_id}: {e}")
        return jsonify({'error': str(e)}), 500


@recording_bp.route('/api/timeline/export/download/<filename>', methods=['GET'])
@login_required
def api_timeline_export_download_by_filename(filename: str):
    """
    Download an exported file by filename.

    Args:
        filename: Export filename (e.g., 'T8416P0023352DA9_20260120_170000.mp4')

    Returns:
        File download
    """
    try:
        timeline_service = get_timeline_service()
        file_path = os.path.join(timeline_service.export_dir, filename)

        # Validate filename (prevent directory traversal)
        if '..' in filename or '/' in filename:
            return jsonify({'error': 'Invalid filename'}), 400

        if not os.path.exists(file_path):
            return jsonify({'error': 'Export file not found'}), 404

        return send_file(
            file_path,
            mimetype='video/mp4',
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        logger.error(f"Export download error for {filename}: {e}")
        return jsonify({'error': str(e)}), 500


@recording_bp.route('/api/timeline/export/stream/<filename>', methods=['GET'])
@login_required
def api_timeline_export_stream_by_filename(filename: str):
    """
    Stream an exported file by filename for inline playback.
    Used for iOS save workaround where user needs to long-press video.

    Args:
        filename: Export filename (e.g., 'T8416P0023352DA9_20260120_170000.mp4')

    Returns:
        Video stream with Range support for seeking
    """
    try:
        timeline_service = get_timeline_service()
        file_path = os.path.join(timeline_service.export_dir, filename)

        # Validate filename (prevent directory traversal)
        if '..' in filename or '/' in filename:
            return jsonify({'error': 'Invalid filename'}), 400

        if not os.path.exists(file_path):
            return jsonify({'error': 'Export file not found'}), 404

        # Get file size for Range header support
        file_size = os.path.getsize(file_path)

        # Handle Range requests for video seeking
        range_header = request.headers.get('Range')
        if range_header:
            # Parse range header: "bytes=start-end"
            match = re.match(r'bytes=(\d+)-(\d*)', range_header)
            if match:
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else file_size - 1
                length = end - start + 1

                def generate_range():
                    with open(file_path, 'rb') as f:
                        f.seek(start)
                        remaining = length
                        while remaining > 0:
                            chunk_size = min(8192, remaining)
                            data = f.read(chunk_size)
                            if not data:
                                break
                            remaining -= len(data)
                            yield data

                response = Response(
                    generate_range(),
                    status=206,
                    mimetype='video/mp4',
                    direct_passthrough=True
                )
                response.headers['Content-Range'] = f'bytes {start}-{end}/{file_size}'
                response.headers['Accept-Ranges'] = 'bytes'
                response.headers['Content-Length'] = length
                return response

        # No range - send full file for inline viewing (not as attachment)
        return send_file(
            file_path,
            mimetype='video/mp4',
            as_attachment=False  # Inline viewing for iOS long-press save
        )

    except Exception as e:
        logger.error(f"Export stream error for {filename}: {e}")
        return jsonify({'error': str(e)}), 500


########################################################
#           FILE BROWSER API ROUTES
########################################################
# Used for browsing alternate recording sources (FTP uploads, etc.)

@recording_bp.route('/api/files/browse', methods=['GET'])
@login_required
def api_browse_files():
    """
    Browse files in a directory within the allowed paths.

    Query params:
        path: Relative path from ALTERNATE_RECORDING_BASE (default: /)

    Returns:
        JSON with directories and files list
    """
    try:
        # Get relative path from query string
        relative_path = request.args.get('path', '/')

        # Security: Normalize and validate path to prevent directory traversal
        # Remove leading slash if present
        if relative_path.startswith('/'):
            relative_path = relative_path[1:]

        # Construct full path
        full_path = os.path.normpath(os.path.join(ALTERNATE_RECORDING_BASE, relative_path))

        # Security check: Ensure path is within allowed base
        if not full_path.startswith(ALTERNATE_RECORDING_BASE):
            logger.warning(f"[FILE_BROWSER] Directory traversal attempt blocked: {relative_path}")
            return jsonify({'error': 'Invalid path'}), 403

        # Check if path exists
        if not os.path.exists(full_path):
            return jsonify({
                'success': True,
                'path': '/' + relative_path if relative_path else '/',
                'directories': [],
                'files': [],
                'message': 'Directory does not exist'
            })

        # Check if it's a directory
        if not os.path.isdir(full_path):
            return jsonify({'error': 'Path is not a directory'}), 400

        directories = []
        files = []

        try:
            entries = os.listdir(full_path)
        except PermissionError:
            return jsonify({'error': 'Permission denied'}), 403

        for entry in sorted(entries):
            entry_path = os.path.join(full_path, entry)

            try:
                stat_info = os.stat(entry_path)

                if os.path.isdir(entry_path):
                    directories.append({
                        'name': entry,
                        'type': 'directory',
                        'modified': stat_info.st_mtime
                    })
                else:
                    # Only include video files
                    ext = os.path.splitext(entry)[1].lower()
                    if ext in ['.mp4', '.avi', '.mkv', '.mov', '.m4v', '.webm']:
                        files.append({
                            'name': entry,
                            'type': 'video',
                            'size': stat_info.st_size,
                            'modified': stat_info.st_mtime
                        })
            except (OSError, PermissionError):
                # Skip files we can't access
                continue

        # Return current path relative to base
        display_path = '/' + relative_path if relative_path else '/'

        return jsonify({
            'success': True,
            'path': display_path,
            'directories': directories,
            'files': files,
            'total_items': len(directories) + len(files)
        })

    except Exception as e:
        logger.error(f"[FILE_BROWSER] Error browsing files: {e}")
        return jsonify({'error': str(e)}), 500


@recording_bp.route('/api/files/stream/<path:filepath>', methods=['GET'])
@login_required
def api_stream_file(filepath):
    """
    Stream a video file from the alternate recording storage.
    Supports HTTP range requests for seeking.

    Args:
        filepath: Relative path to the file from ALTERNATE_RECORDING_BASE

    Returns:
        Video stream with proper headers for range requests
    """
    try:

        # Security: Normalize and validate path
        full_path = os.path.normpath(os.path.join(ALTERNATE_RECORDING_BASE, filepath))

        # Security check: Ensure path is within allowed base
        if not full_path.startswith(ALTERNATE_RECORDING_BASE):
            logger.warning(f"[FILE_BROWSER] Stream traversal attempt blocked: {filepath}")
            return jsonify({'error': 'Invalid path'}), 403

        # Check if file exists
        if not os.path.exists(full_path):
            return jsonify({'error': 'File not found'}), 404

        if not os.path.isfile(full_path):
            return jsonify({'error': 'Not a file'}), 400

        # Get file info
        file_size = os.path.getsize(full_path)

        # Determine mime type
        ext = os.path.splitext(full_path)[1].lower()
        mime_types = {
            '.mp4': 'video/mp4',
            '.avi': 'video/x-msvideo',
            '.mkv': 'video/x-matroska',
            '.mov': 'video/quicktime',
            '.m4v': 'video/x-m4v',
            '.webm': 'video/webm'
        }
        mime_type = mime_types.get(ext, 'video/mp4')

        # Handle range request for seeking
        range_header = request.headers.get('Range')
        if range_header:
            match = re.match(r'bytes=(\d+)-(\d*)', range_header)
            if match:
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else file_size - 1

                # Clamp values
                start = max(0, min(start, file_size - 1))
                end = max(start, min(end, file_size - 1))
                length = end - start + 1

                def generate_range():
                    with open(full_path, 'rb') as f:
                        f.seek(start)
                        remaining = length
                        while remaining > 0:
                            chunk_size = min(8192, remaining)
                            data = f.read(chunk_size)
                            if not data:
                                break
                            remaining -= len(data)
                            yield data

                response = Response(
                    generate_range(),
                    status=206,
                    mimetype=mime_type,
                    direct_passthrough=True
                )
                response.headers['Content-Range'] = f'bytes {start}-{end}/{file_size}'
                response.headers['Accept-Ranges'] = 'bytes'
                response.headers['Content-Length'] = length
                return response

        # No range - send full file for inline viewing
        return send_file(
            full_path,
            mimetype=mime_type,
            as_attachment=False
        )

    except Exception as e:
        logger.error(f"[FILE_BROWSER] Error streaming file {filepath}: {e}")
        return jsonify({'error': str(e)}), 500


@recording_bp.route('/api/files/download/<path:filepath>', methods=['GET'])
@login_required
def api_download_file(filepath):
    """
    Download a video file from the alternate recording storage.
    Returns file as attachment (triggers browser download).

    Args:
        filepath: Relative path to the file from ALTERNATE_RECORDING_BASE

    Returns:
        File download response
    """
    try:

        # Security: Normalize and validate path
        full_path = os.path.normpath(os.path.join(ALTERNATE_RECORDING_BASE, filepath))

        # Security check: Ensure path is within allowed base
        if not full_path.startswith(ALTERNATE_RECORDING_BASE):
            logger.warning(f"[FILE_BROWSER] Download traversal attempt blocked: {filepath}")
            return jsonify({'error': 'Invalid path'}), 403

        # Check if file exists
        if not os.path.exists(full_path):
            return jsonify({'error': 'File not found'}), 404

        if not os.path.isfile(full_path):
            return jsonify({'error': 'Not a file'}), 400

        # Get filename for download
        filename = os.path.basename(full_path)

        # Determine mime type
        ext = os.path.splitext(full_path)[1].lower()
        mime_types = {
            '.mp4': 'video/mp4',
            '.avi': 'video/x-msvideo',
            '.mkv': 'video/x-matroska',
            '.mov': 'video/quicktime',
            '.m4v': 'video/x-m4v',
            '.webm': 'video/webm'
        }
        mime_type = mime_types.get(ext, 'application/octet-stream')

        logger.info(f"[FILE_BROWSER] Download: {filename}")

        return send_file(
            full_path,
            mimetype=mime_type,
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        logger.error(f"[FILE_BROWSER] Error downloading file {filepath}: {e}")
        return jsonify({'error': str(e)}), 500


########################################################
#           RECORDINGS DOWNLOAD API ROUTE
########################################################

@recording_bp.route('/api/recordings/download/<path:filepath>', methods=['GET'])
@login_required
def api_download_recording(filepath):
    """
    Download a recording file from the main recordings storage.
    Used by timeline playback modal to download selected segments.

    Args:
        filepath: Relative path to the file from /recordings/
                  e.g., motion/SERIAL/filename.mp4

    Returns:
        File download response
    """
    try:

        # Security: Normalize and validate path
        full_path = os.path.normpath(os.path.join(RECORDINGS_BASE, filepath))

        # Security check: Ensure path is within allowed base
        if not full_path.startswith(RECORDINGS_BASE):
            logger.warning(f"[RECORDINGS] Download traversal attempt blocked: {filepath}")
            return jsonify({'error': 'Invalid path'}), 403

        # Check if file exists
        if not os.path.exists(full_path):
            return jsonify({'error': 'File not found'}), 404

        if not os.path.isfile(full_path):
            return jsonify({'error': 'Not a file'}), 400

        # Get filename for download
        filename = os.path.basename(full_path)

        # Determine mime type
        ext = os.path.splitext(full_path)[1].lower()
        mime_types = {
            '.mp4': 'video/mp4',
            '.avi': 'video/x-msvideo',
            '.mkv': 'video/x-matroska',
            '.mov': 'video/quicktime',
            '.m4v': 'video/x-m4v',
            '.webm': 'video/webm'
        }
        mime_type = mime_types.get(ext, 'application/octet-stream')

        logger.info(f"[RECORDINGS] Download: {filename}")

        return send_file(
            full_path,
            mimetype=mime_type,
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        logger.error(f"[RECORDINGS] Error downloading file {filepath}: {e}")
        return jsonify({'error': str(e)}), 500
