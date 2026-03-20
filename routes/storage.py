"""
routes/storage.py — Flask Blueprint for storage management and motion detection routes.

Covers:
- Storage statistics (GET /api/storage/stats)
- Storage migration  (POST /api/storage/migrate)
- Storage cleanup    (POST /api/storage/cleanup)
- DB reconciliation  (POST /api/storage/reconcile)
- Full migration     (POST /api/storage/migrate/full)
- File operations log (GET /api/storage/operations)
- Storage settings   (GET/POST /api/storage/settings)
- Cancel operation   (POST /api/storage/cancel)
- Migration status   (GET /api/storage/migration-status)
- Motion detection status (GET /api/motion/status)
- Motion start/stop  (POST /api/motion/<camera_id>/start|stop)

All service singletons are accessed via routes.shared to avoid circular imports.
Module-level migration state (_migration_status, _migration_cancel_event,
MigrationCancelled, update_migration_status, check_migration_cancelled,
get_storage_migration_service) is kept here so the cancel endpoint and status
endpoint share the same in-process state as the long-running operation routes.
"""

import json
import logging
import threading
from datetime import datetime

import requests as req
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user

import routes.shared as shared
from routes.helpers import csrf_exempt

logger = logging.getLogger(__name__)

storage_bp = Blueprint('storage', __name__)

# ---------------------------------------------------------------------------
# Global storage migration service instance — lazy-initialised on first use.
# ---------------------------------------------------------------------------
_storage_migration_service = None


def get_storage_migration_service():
    """
    Get or create the StorageMigrationService singleton.
    Lazy initialization to avoid import issues at startup.
    Starts auto-migration monitor on first initialization.
    """
    global _storage_migration_service
    if _storage_migration_service is None:
        from services.recording.storage_migration import StorageMigrationService
        _storage_migration_service = StorageMigrationService()
        # Start auto-migration background monitor (checks every 5 minutes)
        _storage_migration_service.start_auto_migration_monitor(check_interval_seconds=300)
        logger.info("[STORAGE] Auto-migration monitor started (5 minute interval)")
    return _storage_migration_service


# ---------------------------------------------------------------------------
# Global migration status tracking
# Shared across all routes in this module so the cancel and status endpoints
# can read/write the same dict that the long-running operation routes update.
# ---------------------------------------------------------------------------
_migration_status = {
    'in_progress': False,
    'operation': None,
    'started_at': None,
    'files_processed': 0,
    'files_total': 0,
    'bytes_processed': 0,
    'current_file': None,
    'errors': [],
    'cancel_requested': False
}

# Thread-safe cancellation event for parallel workers
_migration_cancel_event = threading.Event()


class MigrationCancelled(Exception):
    """Raised when migration is cancelled by user."""
    pass


def update_migration_status(in_progress=None, operation=None, files_processed=None,
                            files_total=None, bytes_processed=None, current_file=None,
                            error=None, reset=False, cancel_requested=None):
    """Update global migration status for real-time UI updates."""
    global _migration_status
    if reset:
        _migration_status = {
            'in_progress': False,
            'operation': None,
            'started_at': None,
            'files_processed': 0,
            'files_total': 0,
            'bytes_processed': 0,
            'current_file': None,
            'errors': [],
            'cancel_requested': False
        }
        return

    if in_progress is not None:
        _migration_status['in_progress'] = in_progress
        if in_progress:
            _migration_status['started_at'] = datetime.now().isoformat()
    if operation is not None:
        _migration_status['operation'] = operation
    if files_processed is not None:
        _migration_status['files_processed'] = files_processed
    if files_total is not None:
        _migration_status['files_total'] = files_total
    if bytes_processed is not None:
        _migration_status['bytes_processed'] = bytes_processed
    if current_file is not None:
        _migration_status['current_file'] = current_file
    if error:
        _migration_status['errors'].append(error)
    if cancel_requested is not None:
        _migration_status['cancel_requested'] = cancel_requested


def check_migration_cancelled():
    """Check if migration cancellation was requested. Raises MigrationCancelled if so."""
    if _migration_status.get('cancel_requested'):
        raise MigrationCancelled("Migration cancelled by user")


########################################################
#               STORAGE API ROUTES
########################################################

@storage_bp.route('/api/storage/stats', methods=['GET'])
@login_required
def api_storage_stats():
    """
    Get storage statistics for UI display.

    Returns:
        Disk usage for recent and archive tiers, config settings, warnings
    """
    try:
        migration_service = get_storage_migration_service()
        stats = migration_service.get_storage_stats()

        return jsonify({
            'success': True,
            **stats
        })

    except Exception as e:
        logger.error(f"Storage stats API error: {e}")
        return jsonify({'error': str(e)}), 500


@storage_bp.route('/api/storage/migrate', methods=['POST'])
@csrf_exempt
@login_required
def api_storage_migrate():
    """
    Trigger storage migration from recent to archive tier (admin only).

    Request Body (JSON, optional):
        recording_type: Type to migrate (default: "motion")
        force: Bypass threshold checks (default: false)

    Returns:
        Migration result with counts and details
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403
    try:
        # Prevent concurrent operations
        if _migration_status.get('in_progress'):
            return jsonify({
                'success': False,
                'error': f"Operation '{_migration_status.get('operation')}' already in progress",
                'in_progress': True,
                'current_operation': _migration_status.get('operation'),
                'files_processed': _migration_status.get('files_processed', 0),
                'files_total': _migration_status.get('files_total', 0)
            }), 409  # HTTP 409 Conflict

        global _migration_cancel_event

        data = request.get_json() or {}
        recording_type = data.get('recording_type', 'motion')
        force = data.get('force', False)

        # Reset cancel event and status before starting
        _migration_cancel_event.clear()
        update_migration_status(in_progress=True, operation='migrate', reset=True)
        update_migration_status(in_progress=True, operation='migrate')

        # Progress callback for real-time updates (also checks for cancellation)
        def progress_callback(files_processed=None, files_total=None, current_file=None, bytes_processed=None, **kwargs):
            check_migration_cancelled()  # Raises MigrationCancelled if cancel requested
            update_migration_status(
                files_processed=files_processed,
                files_total=files_total,
                current_file=current_file,
                bytes_processed=bytes_processed
            )

        migration_service = get_storage_migration_service()
        result = migration_service.migrate_recent_to_archive(
            recording_type, force,
            progress_callback=progress_callback,
            cancel_event=_migration_cancel_event
        )

        # Update migration status - complete
        update_migration_status(
            in_progress=False,
            files_processed=result.success_count,
            bytes_processed=result.bytes_processed
        )

        return jsonify({
            'success': True,
            'operation': 'migrate',
            'recording_type': recording_type,
            'trigger_reason': result.trigger_reason,
            'migrated': result.success_count,
            'failed': result.failed_count,
            'skipped': result.skipped_count,
            'bytes_processed': result.bytes_processed,
            'errors': result.errors[:10] if result.errors else []
        })

    except MigrationCancelled:
        logger.info("Migration cancelled by user")
        update_migration_status(in_progress=False)
        return jsonify({
            'success': True,
            'cancelled': True,
            'operation': 'migrate',
            'message': 'Migration cancelled by user',
            'files_processed': _migration_status.get('files_processed', 0)
        })

    except Exception as e:
        logger.error(f"Storage migrate API error: {e}")
        update_migration_status(in_progress=False, error=str(e))
        return jsonify({'error': str(e)}), 500


@storage_bp.route('/api/storage/cleanup', methods=['POST'])
@csrf_exempt
@login_required
def api_storage_cleanup():
    """
    Trigger archive cleanup (deletion of old files) (admin only).

    Request Body (JSON, optional):
        recording_type: Type to clean (default: "motion")
        force: Bypass threshold checks (default: false)

    Returns:
        Cleanup result with counts and details
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403
    try:
        # Prevent concurrent operations
        if _migration_status.get('in_progress'):
            return jsonify({
                'success': False,
                'error': f"Operation '{_migration_status.get('operation')}' already in progress",
                'in_progress': True,
                'current_operation': _migration_status.get('operation'),
                'files_processed': _migration_status.get('files_processed', 0),
                'files_total': _migration_status.get('files_total', 0)
            }), 409

        data = request.get_json() or {}
        recording_type = data.get('recording_type', 'motion')
        force = data.get('force', False)

        # Update migration status - in progress
        update_migration_status(in_progress=True, operation='cleanup', reset=True)
        update_migration_status(in_progress=True, operation='cleanup')

        migration_service = get_storage_migration_service()
        result = migration_service.cleanup_archive(recording_type, force)

        # Update migration status - complete
        update_migration_status(
            in_progress=False,
            files_processed=result.success_count,
            bytes_processed=result.bytes_processed
        )

        return jsonify({
            'success': True,
            'operation': 'cleanup',
            'recording_type': recording_type,
            'trigger_reason': result.trigger_reason,
            'deleted': result.success_count,
            'failed': result.failed_count,
            'bytes_freed': result.bytes_processed,
            'errors': result.errors[:10] if result.errors else []
        })

    except Exception as e:
        logger.error(f"Storage cleanup API error: {e}")
        update_migration_status(in_progress=False, error=str(e))
        return jsonify({'error': str(e)}), 500


@storage_bp.route('/api/storage/reconcile', methods=['POST'])
@csrf_exempt
@login_required
def api_storage_reconcile():
    """
    Reconcile database with filesystem (admin only).
    Removes orphaned database entries where files no longer exist.

    Returns:
        Reconciliation result with removed entry count
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403
    try:
        # Prevent concurrent operations
        if _migration_status.get('in_progress'):
            return jsonify({
                'success': False,
                'error': f"Operation '{_migration_status.get('operation')}' already in progress",
                'in_progress': True,
                'current_operation': _migration_status.get('operation'),
                'files_processed': _migration_status.get('files_processed', 0),
                'files_total': _migration_status.get('files_total', 0)
            }), 409

        # Update migration status - in progress
        update_migration_status(in_progress=True, operation='reconcile', reset=True)
        update_migration_status(in_progress=True, operation='reconcile')

        # Progress callback for real-time updates (also checks for cancellation)
        def progress_callback(files_processed=None, files_total=None, current_file=None, **kwargs):
            check_migration_cancelled()  # Raises MigrationCancelled if cancel requested
            update_migration_status(
                files_processed=files_processed,
                files_total=files_total,
                current_file=current_file
            )

        migration_service = get_storage_migration_service()
        result = migration_service.reconcile_db_with_filesystem(progress_callback=progress_callback)

        # Check if the service-level lock prevented execution
        if result.errors and "already in progress" in str(result.errors):
            update_migration_status(in_progress=False)
            return jsonify({
                'success': False,
                'error': 'Reconciliation already running (service lock)',
                'in_progress': True
            }), 409

        # Update migration status - complete
        update_migration_status(
            in_progress=False,
            files_processed=result.success_count
        )

        return jsonify({
            'success': True,
            'operation': 'reconcile',
            'orphaned_removed': result.success_count,
            'failed': result.failed_count,
            'errors': result.errors[:10] if result.errors else []
        })

    except MigrationCancelled:
        logger.info("Reconcile cancelled by user")
        update_migration_status(in_progress=False)
        return jsonify({
            'success': True,
            'cancelled': True,
            'operation': 'reconcile',
            'message': 'Reconcile cancelled by user',
            'files_processed': _migration_status.get('files_processed', 0)
        })

    except Exception as e:
        logger.error(f"Storage reconcile API error: {e}")
        update_migration_status(in_progress=False, error=str(e))
        return jsonify({'error': str(e)}), 500


@storage_bp.route('/api/storage/migrate/full', methods=['POST'])
@csrf_exempt
@login_required
def api_storage_full_migration():
    """
    Run complete migration cycle for all recording types.

    Steps:
    1. Migrate recent → archive for all types
    2. Cleanup archive for all types
    3. Reconcile database

    Returns:
        Summary of all operations
    """
    try:
        migration_service = get_storage_migration_service()
        results = migration_service.run_full_migration()

        # Summarize results
        summary = {
            'success': True,
            'operation': 'full_migration',
            'migrate': {},
            'cleanup': {},
            'reconcile': {}
        }

        for key, result in results.items():
            if key.startswith('migrate_'):
                rec_type = key.replace('migrate_', '')
                summary['migrate'][rec_type] = {
                    'migrated': result.success_count,
                    'failed': result.failed_count
                }
            elif key.startswith('cleanup_'):
                rec_type = key.replace('cleanup_', '')
                summary['cleanup'][rec_type] = {
                    'deleted': result.success_count,
                    'failed': result.failed_count
                }
            elif key == 'reconcile':
                summary['reconcile'] = {
                    'orphaned_removed': result.success_count,
                    'failed': result.failed_count
                }

        return jsonify(summary)

    except Exception as e:
        logger.error(f"Storage full migration API error: {e}")
        return jsonify({'error': str(e)}), 500


@storage_bp.route('/api/storage/operations', methods=['GET'])
@login_required
def api_storage_operations():
    """
    Query file operations log.

    Query Parameters:
        operation: Filter by operation type (migrate, delete, reconcile, error)
        camera_id: Filter by camera
        limit: Max records (default: 50)
        offset: Pagination offset (default: 0)

    Returns:
        List of file operation log entries
    """
    try:
        # Build PostgREST query
        operation = request.args.get('operation')
        camera_id = request.args.get('camera_id')
        limit = int(request.args.get('limit', 50))
        offset = int(request.args.get('offset', 0))

        # Query PostgREST directly
        url = f"http://localhost:3000/file_operations_log?order=created_at.desc&limit={limit}&offset={offset}"

        if operation:
            url += f"&operation=eq.{operation}"
        if camera_id:
            url += f"&camera_id=eq.{camera_id}"

        response = req.get(url, timeout=30)
        response.raise_for_status()
        operations = response.json()

        return jsonify({
            'success': True,
            'count': len(operations),
            'operations': operations
        })

    except Exception as e:
        logger.error(f"Storage operations API error: {e}")
        return jsonify({'error': str(e)}), 500


@storage_bp.route('/api/storage/settings', methods=['GET', 'POST'])
@csrf_exempt
@login_required
def api_storage_settings():
    """
    Get or update storage migration settings (admin only).

    GET: Returns current migration settings from recording_settings.json
    POST: Updates migration settings (persisted to recording_settings.json)

    Settings:
        - age_threshold_days: Days before migrating to archive
        - archive_retention_days: Days to keep files in archive before deletion
        - min_free_space_percent: Migrate when free space drops below this %
        - max_recent_storage_mb: Max size for recent storage (0 = unlimited)
        - max_archive_storage_mb: Max size for archive storage (0 = unlimited)
    """
    # Admin-only for all storage settings operations
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    config_path = '/app/config/recording_settings.json'

    try:
        if request.method == 'GET':
            # Read current settings
            with open(config_path, 'r') as f:
                config = json.load(f)

            migration = config.get('migration', {})

            return jsonify({
                'success': True,
                'settings': {
                    'age_threshold_days': migration.get('age_threshold_days', 3),
                    'archive_retention_days': migration.get('archive_retention_days', 90),
                    'min_free_space_percent': migration.get('min_free_space_percent', 20),
                    'max_recent_storage_mb': migration.get('max_recent_storage_mb', 0),
                    'max_archive_storage_mb': migration.get('max_archive_storage_mb', 0),
                    'enabled': migration.get('enabled', True)
                }
            })

        else:  # POST - update settings
            data = request.get_json() or {}

            # Read current config
            with open(config_path, 'r') as f:
                config = json.load(f)

            # Ensure migration section exists
            if 'migration' not in config:
                config['migration'] = {}

            # Update only provided fields
            migration = config['migration']
            if 'age_threshold_days' in data:
                migration['age_threshold_days'] = int(data['age_threshold_days'])
            if 'archive_retention_days' in data:
                migration['archive_retention_days'] = int(data['archive_retention_days'])
            if 'min_free_space_percent' in data:
                migration['min_free_space_percent'] = int(data['min_free_space_percent'])
            if 'max_recent_storage_mb' in data:
                migration['max_recent_storage_mb'] = int(data['max_recent_storage_mb'])
            if 'max_archive_storage_mb' in data:
                migration['max_archive_storage_mb'] = int(data['max_archive_storage_mb'])
            if 'enabled' in data:
                migration['enabled'] = bool(data['enabled'])

            # Write updated config
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=2)

            # Reload config in migration service if available
            migration_service = get_storage_migration_service()
            if migration_service:
                migration_service.config.reload()

            logger.info(f"Storage settings updated: {migration}")

            return jsonify({
                'success': True,
                'message': 'Settings updated successfully',
                'settings': migration
            })

    except Exception as e:
        logger.error(f"Storage settings API error: {e}")
        return jsonify({'error': str(e)}), 500


@storage_bp.route('/api/storage/cancel', methods=['POST'])
@csrf_exempt
@login_required
def api_storage_cancel():
    """
    Cancel the current storage operation.
    Sets both the status flag and the threading.Event for parallel workers.
    """
    global _migration_cancel_event

    if not _migration_status.get('in_progress'):
        return jsonify({
            'success': False,
            'error': 'No operation in progress to cancel'
        }), 400

    operation = _migration_status.get('operation')
    update_migration_status(cancel_requested=True)
    _migration_cancel_event.set()  # Signal parallel workers to stop
    logger.info(f"Storage operation '{operation}' cancellation requested")

    return jsonify({
        'success': True,
        'message': f'Cancellation requested for {operation}',
        'operation': operation
    })


@storage_bp.route('/api/storage/migration-status', methods=['GET'])
@login_required
def api_migration_status():
    """
    Get current migration operation status for real-time UI updates.

    Returns:
        - in_progress: Whether migration is currently running
        - operation: Current operation type (migrate, cleanup, reconcile)
        - files_processed: Number of files processed so far
        - files_total: Total files to process (if known)
        - bytes_processed: Bytes processed so far
        - current_file: Currently processing file path
        - errors: List of errors encountered
    """
    return jsonify({
        'success': True,
        **_migration_status
    })


########################################################
#           MOTION DETECTION API ROUTES
########################################################

@storage_bp.route('/api/motion/status', methods=['GET'])
@login_required
def api_motion_status():
    """Get status of all motion detection services"""
    try:
        status = {
            'onvif': {},
            'ffmpeg': {},
            'reolink': {}
        }

        # ONVIF listeners
        if shared.onvif_listener:
            for camera_id, is_active in shared.onvif_listener.active_listeners.items():
                camera = shared.camera_repo.get_camera(camera_id)
                status['onvif'][camera_id] = {
                    'camera_name': camera.get('name', camera_id) if camera else camera_id,
                    'active': is_active,
                    'method': 'onvif'
                }

        # FFmpeg detectors
        if shared.ffmpeg_motion_detector:
            status['ffmpeg'] = shared.ffmpeg_motion_detector.get_status()

        # Reolink Baichuan service
        if shared.reolink_motion_service:
            status['reolink'] = shared.reolink_motion_service.get_status()

        return jsonify({
            'success': True,
            'motion_detectors': status
        })

    except Exception as e:
        logger.error(f"Motion status API error: {e}")
        return jsonify({'error': str(e)}), 500


@storage_bp.route('/api/motion/start/<camera_id>', methods=['POST'])
@csrf_exempt
@login_required
def api_motion_start(camera_id):
    """Start motion detection for a specific camera"""
    if not shared.recording_service:
        return jsonify({'error': 'Recording service not available'}), 503

    try:
        camera = shared.camera_repo.get_camera(camera_id)
        if not camera:
            return jsonify({'error': 'Camera not found'}), 404

        camera_name = camera.get('name', camera_id)
        camera_type = camera.get('type', '').lower()

        data = request.get_json() or {}
        method = data.get('method', 'auto')  # auto, onvif, ffmpeg

        # Auto-detect best method
        if method == 'auto':
            if camera_type == 'reolink':
                return jsonify({
                    'success': False,
                    'error': 'Reolink cameras use Baichuan service - start via /api/reolink/motion'
                }), 400
            elif 'ONVIF' in camera.get('capabilities', []):
                method = 'onvif'
            else:
                method = 'ffmpeg'

        success = False
        if method == 'onvif':
            if shared.onvif_listener:
                success = shared.onvif_listener.start_listener(camera_id)
            else:
                return jsonify({'error': 'ONVIF listener not available'}), 503
        elif method == 'ffmpeg':
            if shared.ffmpeg_motion_detector:
                sensitivity = data.get('sensitivity', 0.3)
                success = shared.ffmpeg_motion_detector.start_detector(camera_id, sensitivity)
            else:
                return jsonify({'error': 'FFmpeg detector not available'}), 503
        else:
            return jsonify({'error': f'Unknown method: {method}'}), 400

        return jsonify({
            'success': success,
            'camera_id': camera_id,
            'camera_name': camera_name,
            'method': method,
            'message': f'Motion detection started ({method})' if success else 'Failed to start'
        })

    except Exception as e:
        logger.error(f"Motion start API error for {camera_id}: {e}")
        return jsonify({'error': str(e)}), 500


@storage_bp.route('/api/motion/stop/<camera_id>', methods=['POST'])
@csrf_exempt
@login_required
def api_motion_stop(camera_id):
    """Stop motion detection for a specific camera"""
    try:
        stopped = []

        # Stop ONVIF listener
        if shared.onvif_listener and camera_id in shared.onvif_listener.active_listeners:
            shared.onvif_listener.stop_listener(camera_id)
            stopped.append('onvif')

        # Stop FFmpeg detector
        if shared.ffmpeg_motion_detector and camera_id in shared.ffmpeg_motion_detector.active_detectors:
            shared.ffmpeg_motion_detector.stop_detector(camera_id)
            stopped.append('ffmpeg')

        if stopped:
            return jsonify({
                'success': True,
                'camera_id': camera_id,
                'stopped_methods': stopped,
                'message': f'Stopped motion detection: {", ".join(stopped)}'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'No active motion detection found for this camera'
            }), 404

    except Exception as e:
        logger.error(f"Motion stop API error for {camera_id}: {e}")
        return jsonify({'error': str(e)}), 500
