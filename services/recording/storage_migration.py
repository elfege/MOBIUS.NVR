"""
Storage Migration Service
Handles file migration between recent and archive storage tiers.

Key responsibilities:
- Migrate old recordings from recent to archive (rsync-based)
- Delete old recordings from archive when retention exceeded or capacity low
- Reconcile database with filesystem (remove orphaned entries)
- Track all operations in file_operations_log

Migration triggers:
- Age-based: Files older than age_threshold_days
- Capacity-based: When free space drops below min_free_space_percent
"""

import os
import logging
import subprocess
import requests
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple, Any, Callable
from dataclasses import dataclass, field

import sys
sys.path.append('/app/config')
from recording_config_loader import RecordingConfig

logger = logging.getLogger(__name__)


@dataclass
class MigrationResult:
    """Result of a migration or cleanup operation."""
    success_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    bytes_processed: int = 0
    trigger_reason: str = ""
    details: List[Dict] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class StorageMigrationService:
    """
    Service for managing storage tier migration and cleanup.

    Storage tiers:
    - Recent: /recordings/{type}/ - Fast storage for recent recordings
    - Archive: /recordings/STORAGE/{type}/ - Long-term storage

    Migration flow:
    1. Recent → Archive: When file age > age_threshold_days OR recent capacity < 20%
    2. Archive → Delete: When file age > archive_retention_days OR archive capacity < 20%
    """

    # Recording types that can be migrated
    RECORDING_TYPES = ['motion', 'continuous', 'manual', 'snapshots']

    def __init__(self, config_path: Optional[str] = None,
                 postgrest_url: Optional[str] = None):
        """
        Initialize storage migration service.

        Args:
            config_path: Path to recording_settings.json
            postgrest_url: PostgREST API URL for database operations
                          Defaults to POSTGREST_URL env var or http://nvr-postgrest:3001
        """
        self.config = RecordingConfig(config_path)
        # Use env var, then passed value, then Docker default
        self.postgrest_url = postgrest_url or os.environ.get('POSTGREST_URL', 'http://nvr-postgrest:3001')

        # Get storage paths from config
        storage_paths = self.config.get_storage_paths()
        self.recent_base = Path(storage_paths['recent_base'])
        self.archive_base = Path(storage_paths['archive_base'])

        # Threading lock to prevent concurrent bulk delete operations.
        # Multiple simultaneous DELETEs on the recordings table cause
        # massive lock contention and PostgREST pool exhaustion.
        # Used by both reconciliation and migration paths.
        self._bulk_delete_lock = threading.Lock()

        logger.info(f"StorageMigrationService initialized")
        logger.info(f"  Recent: {self.recent_base}")
        logger.info(f"  Archive: {self.archive_base}")

    # =========================================================================
    # DISK CAPACITY CHECKS
    # =========================================================================

    def get_disk_usage(self, path: Path) -> Dict[str, Any]:
        """
        Get disk usage statistics for a path.

        Args:
            path: Path to check (must exist)

        Returns:
            Dict with total_bytes, used_bytes, free_bytes, free_percent
        """
        try:
            stat = os.statvfs(path)
            total = stat.f_blocks * stat.f_frsize
            free = stat.f_bavail * stat.f_frsize
            used = total - free
            free_percent = round((free / total) * 100, 1) if total > 0 else 0

            return {
                'total_bytes': total,
                'used_bytes': used,
                'free_bytes': free,
                'total_gb': round(total / (1024**3), 2),
                'used_gb': round(used / (1024**3), 2),
                'free_gb': round(free / (1024**3), 2),
                'free_percent': free_percent,
                'used_percent': round(100 - free_percent, 1)
            }
        except Exception as e:
            logger.error(f"Failed to get disk usage for {path}: {e}")
            return {
                'total_bytes': 0, 'used_bytes': 0, 'free_bytes': 0,
                'total_gb': 0, 'used_gb': 0, 'free_gb': 0,
                'free_percent': 100, 'used_percent': 0, 'error': str(e)
            }

    def check_capacity_trigger(self, tier: str) -> Tuple[bool, float]:
        """
        Check if a tier needs capacity-based migration/deletion.

        Args:
            tier: 'recent' or 'archive'

        Returns:
            Tuple of (needs_action, current_free_percent)
        """
        if tier == 'recent':
            path = self.recent_base
        else:
            # For archive, use mounted subdir (archive_base may not be a mount point)
            path = self.archive_base / 'motion'
            if not path.exists():
                path = self.archive_base
        min_free = self.config.get_min_free_space_percent()

        usage = self.get_disk_usage(path)
        free_percent = usage.get('free_percent', 100)

        needs_action = free_percent < min_free
        if needs_action:
            logger.warning(f"{tier.upper()} storage at {100 - free_percent:.1f}% capacity "
                          f"(free: {free_percent:.1f}%, min: {min_free}%)")

        return needs_action, free_percent

    # =========================================================================
    # DATABASE OPERATIONS
    # =========================================================================

    def _query_recordings(self, where_clause: str, order_by: str = "timestamp.asc",
                          limit: Optional[int] = None) -> List[Dict]:
        """
        Query recordings from PostgREST.

        Args:
            where_clause: PostgREST filter (e.g., "storage_tier=eq.recent")
            order_by: Sort order
            limit: Max records to return

        Returns:
            List of recording dicts
        """
        try:
            url = f"{self.postgrest_url}/recordings?{where_clause}&order={order_by}"
            if limit:
                url += f"&limit={limit}"

            response = requests.get(url, timeout=30)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to query recordings: {e}")
            return []

    def _update_recording(self, recording_id: int, updates: Dict) -> bool:
        """
        Update a recording in the database.

        Args:
            recording_id: Recording ID
            updates: Dict of fields to update

        Returns:
            True if successful
        """
        try:
            url = f"{self.postgrest_url}/recordings?id=eq.{recording_id}"
            response = requests.patch(url, json=updates, timeout=30)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Failed to update recording {recording_id}: {e}")
            return False

    def _delete_recording(self, recording_id: int) -> bool:
        """
        Delete a recording from the database.

        Args:
            recording_id: Recording ID

        Returns:
            True if successful
        """
        try:
            url = f"{self.postgrest_url}/recordings?id=eq.{recording_id}"
            response = requests.delete(url, timeout=30)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Failed to delete recording {recording_id}: {e}")
            return False

    def _get_db_connection(self):
        """
        Create a direct psycopg2 connection to PostgreSQL.
        Uses the same env vars as docker-compose provides to the container.
        """
        import psycopg2
        return psycopg2.connect(
            host=os.environ.get('POSTGRES_HOST', 'postgres'),
            port=int(os.environ.get('POSTGRES_PORT', '5432')),
            dbname=os.environ.get('POSTGRES_DB', 'nvr'),
            user=os.environ.get('POSTGRES_USER', 'nvr_api'),
            password=os.environ.get('POSTGRES_PASSWORD', ''),
            connect_timeout=10
        )

    def _batch_delete_recordings(self, recording_ids: List[int], batch_size: int = 500,
                                  progress_callback: Optional[Callable] = None) -> int:
        """
        Delete multiple recordings from the database using direct SQL.
        Bypasses PostgREST entirely to avoid 504 Gateway Timeout issues.

        Thread-safe: Uses _bulk_delete_lock to prevent concurrent bulk deletes
        from both the migration and reconciliation paths, which would cause
        PostgreSQL lock contention and PostgREST connection pool exhaustion.

        Performance note: file_operations_log has an ON DELETE SET NULL FK
        referencing recordings. With ~100M rows in that table, each DELETE
        triggers a massive scan. We pre-clear FK references in bulk and
        disable triggers during the delete to avoid this bottleneck.

        Args:
            recording_ids: List of recording IDs to delete
            batch_size: Number of IDs per DELETE statement (default 500)
            progress_callback: Optional callback(files_processed, files_total, current_file)

        Returns:
            Number of successfully deleted records
        """
        import time

        if not recording_ids:
            return 0

        # Serialize bulk deletes - only one batch delete operation at a time.
        # Both migrate_recent_to_archive and reconcile_db_with_filesystem call
        # this method, and concurrent DELETEs on recordings cause massive
        # lock contention with the 98M-row file_operations_log FK.
        if not self._bulk_delete_lock.acquire(blocking=False):
            logger.warning(f"Bulk delete already in progress - "
                          f"queuing {len(recording_ids)} IDs (waiting for lock)")
        else:
            self._bulk_delete_lock.release()  # Release to re-acquire below as blocking

        # Acquire lock (blocking - will wait for any in-progress delete to finish)
        self._bulk_delete_lock.acquire()
        try:
            return self._do_batch_delete(recording_ids, batch_size, progress_callback)
        finally:
            self._bulk_delete_lock.release()

    def _do_batch_delete(self, recording_ids: List[int], batch_size: int,
                         progress_callback: Optional[Callable] = None) -> int:
        """
        Internal batch delete implementation (called with lock held).

        Strategy: Disable FK triggers via session_replication_role='replica'
        to skip the ON DELETE SET NULL check against file_operations_log (98M+ rows).
        Then per-batch: clear FK references for just that batch, then delete.
        This avoids both the massive single UPDATE and the massive FK trigger scan.

        Dangling recording_ids in file_operations_log are harmless (it's a log table).
        """
        import time

        deleted_count = 0
        total_ids = len(recording_ids)
        total_batches = (total_ids + batch_size - 1) // batch_size

        try:
            conn = self._get_db_connection()
            conn.autocommit = False
            cursor = conn.cursor()

            # Disable triggers and FK checks for this session.
            # This skips the ON DELETE SET NULL trigger on file_operations_log
            # (98M+ rows) which was causing 14+ minute per-batch deletes.
            # Dangling recording_ids in the log table are harmless.
            triggers_disabled = False
            try:
                cursor.execute("SET session_replication_role = 'replica'")
                conn.commit()
                triggers_disabled = True
                logger.info("Disabled triggers/FK checks for bulk delete session")
            except Exception as e:
                conn.rollback()
                logger.warning(f"Could not disable triggers: {e}")
                # If we can't disable triggers, do per-batch FK pre-clear as fallback
                logger.info("Will pre-clear FK references per batch instead")

            # Batch delete recordings
            for batch_num in range(total_batches):
                start = batch_num * batch_size
                end = start + batch_size
                batch_ids = recording_ids[start:end]

                try:
                    # If triggers couldn't be disabled, pre-clear FK refs for this batch
                    if not triggers_disabled:
                        cursor.execute(
                            "UPDATE file_operations_log SET recording_id = NULL "
                            "WHERE recording_id = ANY(%s)",
                            (batch_ids,)
                        )
                        conn.commit()

                    cursor.execute(
                        "DELETE FROM recordings WHERE id = ANY(%s)",
                        (batch_ids,)
                    )
                    rows_deleted = cursor.rowcount
                    conn.commit()
                    deleted_count += rows_deleted
                    logger.info(f"Batch-deleted {rows_deleted} recordings "
                               f"(batch {batch_num + 1}/{total_batches}, "
                               f"total: {deleted_count}/{total_ids})")

                    # Report progress to UI
                    if progress_callback:
                        try:
                            progress_callback(
                                files_processed=deleted_count,
                                files_total=total_ids,
                                current_file=f"Deleting orphans: {deleted_count}/{total_ids} "
                                            f"(batch {batch_num + 1}/{total_batches})"
                            )
                        except Exception:
                            pass  # Don't let callback errors stop the delete

                except Exception as e:
                    conn.rollback()
                    logger.error(f"Batch delete failed for batch {batch_num + 1}: {e}")

                # Brief pause between batches
                if batch_num < total_batches - 1:
                    time.sleep(0.3)

            # Restore normal trigger behavior
            if triggers_disabled:
                try:
                    cursor.execute("SET session_replication_role = 'origin'")
                    conn.commit()
                except Exception:
                    pass

            cursor.close()
            conn.close()

        except Exception as e:
            logger.error(f"Database connection error during batch delete: {e}")
            logger.info("Falling back to individual deletes via direct SQL")
            try:
                conn2 = self._get_db_connection()
                conn2.autocommit = True
                cur2 = conn2.cursor()
                for rid in recording_ids:
                    try:
                        cur2.execute("DELETE FROM recordings WHERE id = %s", (rid,))
                        deleted_count += 1
                    except Exception:
                        pass
                cur2.close()
                conn2.close()
            except Exception as e2:
                logger.error(f"Fallback individual deletes also failed: {e2}")

        return deleted_count

    def _log_operation(self, operation: str, source_path: str,
                       destination_path: Optional[str] = None,
                       file_size_bytes: int = 0,
                       recording_id: Optional[int] = None,
                       camera_id: Optional[str] = None,
                       reason: str = "",
                       trigger_type: str = "manual",
                       success: bool = True,
                       error_message: Optional[str] = None) -> bool:
        """
        Log a file operation to file_operations_log.

        Args:
            operation: Operation type (migrate, delete, reconcile, error)
            source_path: Source file path
            destination_path: Destination path (for migrate)
            file_size_bytes: File size in bytes
            recording_id: Associated recording ID
            camera_id: Camera serial number
            reason: Human-readable reason
            trigger_type: What triggered the operation (age, capacity, manual, scheduled)
            success: Whether operation succeeded
            error_message: Error details if failed

        Returns:
            True if logged successfully
        """
        try:
            payload = {
                'operation': operation,
                'source_path': source_path,
                'destination_path': destination_path,
                'file_size_bytes': file_size_bytes,
                'recording_id': recording_id,
                'camera_id': camera_id,
                'reason': reason,
                'trigger_type': trigger_type,
                'success': success,
                'error_message': error_message
            }

            url = f"{self.postgrest_url}/file_operations_log"
            response = requests.post(url, json=payload, timeout=30)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Failed to log operation: {e}")
            return False

    # =========================================================================
    # FILE OPERATIONS
    # =========================================================================

    def _rsync_file(self, source: Path, dest: Path) -> Tuple[bool, str]:
        """
        Move a file from source to dest, preserving metadata.

        Uses shutil.move (Python built-in) instead of rsync for container compatibility.

        Args:
            source: Source file path
            dest: Destination file path

        Returns:
            Tuple of (success, error_message)
        """
        import shutil

        try:
            # Ensure destination directory exists
            dest.parent.mkdir(parents=True, exist_ok=True)

            # shutil.move handles cross-filesystem moves (copy + delete)
            shutil.move(str(source), str(dest))

            # Verify move succeeded
            if dest.exists() and not source.exists():
                return True, ""
            elif dest.exists() and source.exists():
                # Copy succeeded but source not deleted - try to remove
                source.unlink()
                return True, ""
            else:
                return False, "Move failed - destination not created"

        except Exception as e:
            return False, str(e)

    def _cleanup_empty_dirs(self, base_path: Path) -> int:
        """
        Remove empty directories under base_path.
        Uses: find base_path -type d -empty -delete

        Args:
            base_path: Base directory to clean

        Returns:
            Number of directories removed (approximate)
        """
        try:
            # Count empty dirs before
            result_before = subprocess.run(
                ['find', str(base_path), '-type', 'd', '-empty'],
                capture_output=True, text=True, timeout=60
            )
            count_before = len(result_before.stdout.strip().split('\n')) if result_before.stdout.strip() else 0

            # Delete empty dirs
            subprocess.run(
                ['find', str(base_path), '-type', 'd', '-empty', '-delete'],
                capture_output=True, timeout=60
            )

            logger.debug(f"Cleaned up ~{count_before} empty directories under {base_path}")
            return count_before

        except Exception as e:
            logger.warning(f"Failed to cleanup empty dirs under {base_path}: {e}")
            return 0

    def _get_archive_path(self, source_path: Path, recording_type: str) -> Path:
        """
        Transform a recent path to its archive equivalent.

        Example:
        /recordings/motion/CAMERA/2026/01/15/file.mp4
        → /recordings/STORAGE/motion/CAMERA/2026/01/15/file.mp4

        Args:
            source_path: Path in recent storage
            recording_type: Type (motion, continuous, etc.)

        Returns:
            Equivalent path in archive storage
        """
        recent_type_base = self.recent_base / recording_type
        relative = source_path.relative_to(recent_type_base)
        return self.archive_base / recording_type / relative

    # =========================================================================
    # RECENT → ARCHIVE MIGRATION
    # =========================================================================

    def _migrate_single_file(self, rec: Dict, recording_type: str, trigger_type: str,
                              age_threshold: int, semaphore: threading.Semaphore,
                              cancel_event: threading.Event) -> Dict:
        """
        Migrate a single file from recent to archive tier.
        Thread-safe worker function for parallel migration.

        Args:
            rec: Recording dict from database
            recording_type: Type of recordings being migrated
            trigger_type: What triggered the migration (age/capacity)
            age_threshold: Age threshold in days
            semaphore: Semaphore to limit concurrent operations
            cancel_event: Event to signal cancellation

        Returns:
            Dict with 'status' (success/failed/skipped/cancelled), 'bytes', 'error', 'detail'
        """
        # Check for cancellation before acquiring semaphore
        if cancel_event.is_set():
            return {'status': 'cancelled', 'bytes': 0, 'error': None, 'detail': None}

        with semaphore:
            # Check again after acquiring semaphore
            if cancel_event.is_set():
                return {'status': 'cancelled', 'bytes': 0, 'error': None, 'detail': None}
            source_path = Path(rec.get('file_path', ''))
            recording_id = rec.get('id')
            camera_id = rec.get('camera_id')

            # Detect actual recording type from path
            actual_type = recording_type
            for rtype in self.RECORDING_TYPES:
                if f'/{rtype}/' in str(source_path) or str(source_path).startswith(f'/recordings/{rtype}'):
                    actual_type = rtype
                    break

            # Skip if file doesn't exist - mark as orphan for batch cleanup later
            # (individual DELETEs from parallel workers would overwhelm PostgREST)
            if not source_path.exists():
                logger.debug(f"Source file missing (orphaned DB entry): {source_path}")
                return {
                    'status': 'orphan',
                    'bytes': 0,
                    'error': None,
                    'detail': None,
                    'orphan_id': recording_id
                }

            # Get destination path
            try:
                dest_path = self._get_archive_path(source_path, actual_type)
            except ValueError as e:
                logger.error(f"Failed to compute archive path for {source_path}: {e}")
                return {'status': 'failed', 'bytes': 0, 'error': str(e), 'detail': None}

            # Get file size before migration
            try:
                file_size = source_path.stat().st_size
            except:
                file_size = 0

            # Execute file move
            success, error = self._rsync_file(source_path, dest_path)

            if success:
                # Update database
                db_updated = self._update_recording(recording_id, {
                    'storage_tier': 'archive',
                    'file_path': str(dest_path),
                    'archived_at': datetime.now(timezone.utc).isoformat()
                })

                if db_updated:
                    self._log_operation(
                        operation='migrate',
                        source_path=str(source_path),
                        destination_path=str(dest_path),
                        file_size_bytes=file_size,
                        recording_id=recording_id,
                        camera_id=camera_id,
                        reason=f"file older than {age_threshold} days",
                        trigger_type=trigger_type,
                        success=True
                    )
                    logger.debug(f"Migrated: {source_path.name} → {dest_path}")
                    return {
                        'status': 'success',
                        'bytes': file_size,
                        'error': None,
                        'detail': {
                            'recording_id': recording_id,
                            'camera_id': camera_id,
                            'source': str(source_path),
                            'dest': str(dest_path),
                            'size_bytes': file_size
                        }
                    }
                else:
                    return {'status': 'failed', 'bytes': 0, 'error': f"DB update failed for {recording_id}", 'detail': None}
            else:
                self._log_operation(
                    operation='error',
                    source_path=str(source_path),
                    destination_path=str(dest_path),
                    recording_id=recording_id,
                    camera_id=camera_id,
                    reason='move failed',
                    trigger_type=trigger_type,
                    success=False,
                    error_message=error
                )
                return {'status': 'failed', 'bytes': 0, 'error': f"move failed for {source_path}: {error}", 'detail': None}

    def migrate_recent_to_archive(self, recording_type: str = "motion",
                                   force: bool = False,
                                   progress_callback: Optional[Callable] = None,
                                   cancel_event: Optional[threading.Event] = None) -> MigrationResult:
        """
        Migrate recordings from recent to archive tier using parallel workers.

        Uses ThreadPoolExecutor with semaphore to bound concurrent file operations.
        Number of workers configurable via migration.parallel_workers setting.

        Triggers:
        - file.age > age_threshold_days
        - OR recent_disk.free_percent < min_free_space_percent

        Args:
            recording_type: Type of recordings to migrate
            force: If True, migrate all eligible files regardless of thresholds
            progress_callback: Optional callback(files_processed, files_total, current_file, bytes_processed)
                              for real-time progress updates
            cancel_event: Optional threading.Event for cancellation signal

        Returns:
            MigrationResult with operation details
        """
        # Create cancel_event if not provided
        if cancel_event is None:
            cancel_event = threading.Event()
        result = MigrationResult()

        # Check migration enabled
        if not self.config.is_migration_enabled() and not force:
            logger.info("Migration is disabled in config")
            result.trigger_reason = "disabled"
            return result

        # Determine trigger reason
        capacity_triggered, free_percent = self.check_capacity_trigger('recent')
        age_threshold = self.config.get_migration_age_threshold()
        cutoff_date = datetime.now() - timedelta(days=age_threshold)

        if capacity_triggered:
            result.trigger_reason = f"capacity (free: {free_percent:.1f}%)"
            trigger_type = "capacity"
        else:
            result.trigger_reason = f"age (> {age_threshold} days)"
            trigger_type = "age"

        logger.info(f"Starting migration for {recording_type} - trigger: {result.trigger_reason}")

        # Query eligible recordings
        where_clause = (
            f"storage_tier=eq.recent"
            f"&timestamp=lt.{cutoff_date.isoformat()}"
        )

        # If capacity triggered, get oldest first
        order_by = "timestamp.asc"

        recordings = self._query_recordings(where_clause, order_by)

        if not recordings:
            logger.info(f"No eligible recordings found for migration ({recording_type})")
            return result

        total_recordings = len(recordings)
        max_workers = self.config.get_parallel_workers()
        logger.info(f"Found {total_recordings} recordings to migrate (using {max_workers} parallel workers)")

        # Update progress with total count
        if progress_callback:
            progress_callback(files_processed=0, files_total=total_recordings, current_file=None)

        # Semaphore to limit concurrent I/O operations
        semaphore = threading.Semaphore(max_workers)

        # Thread-safe counters
        files_processed = [0]  # Use list for mutable reference in closure
        counter_lock = threading.Lock()

        # Track cancelled count and orphan IDs for batch cleanup
        cancelled_count = [0]
        orphan_ids = []  # Collect orphaned recording IDs for batch deletion

        # Process recordings in parallel using ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all migration tasks
            futures = {
                executor.submit(
                    self._migrate_single_file,
                    rec, recording_type, trigger_type, age_threshold, semaphore, cancel_event
                ): rec for rec in recordings
            }

            # Collect results as they complete
            for future in as_completed(futures):
                rec = futures[future]
                try:
                    worker_result = future.result()

                    # Update counters thread-safely
                    with counter_lock:
                        if worker_result['status'] == 'success':
                            result.success_count += 1
                            result.bytes_processed += worker_result['bytes']
                            if worker_result['detail']:
                                result.details.append(worker_result['detail'])
                        elif worker_result['status'] == 'failed':
                            result.failed_count += 1
                            if worker_result['error']:
                                result.errors.append(worker_result['error'])
                        elif worker_result['status'] == 'cancelled':
                            cancelled_count[0] += 1
                        elif worker_result['status'] == 'orphan':
                            # Collect orphan ID for batch deletion after all workers finish
                            if worker_result.get('orphan_id'):
                                orphan_ids.append(worker_result['orphan_id'])
                            result.skipped_count += 1
                        else:  # skipped
                            result.skipped_count += 1

                        files_processed[0] += 1

                        # Update progress
                        if progress_callback:
                            progress_callback(
                                files_processed=files_processed[0],
                                files_total=total_recordings,
                                current_file=rec.get('file_path', ''),
                                bytes_processed=result.bytes_processed
                            )

                except Exception as e:
                    logger.error(f"Migration worker exception for {rec.get('file_path')}: {e}")
                    with counter_lock:
                        result.failed_count += 1
                        result.errors.append(str(e))
                        files_processed[0] += 1

        # Batch-delete orphaned DB entries (files that no longer exist on disk)
        # Done AFTER all workers finish to avoid flooding PostgREST during migration
        if orphan_ids:
            logger.info(f"Batch-deleting {len(orphan_ids)} orphaned DB entries...")
            deleted = self._batch_delete_recordings(orphan_ids)
            logger.info(f"Batch-deleted {deleted} orphaned entries")

        # Cleanup empty directories
        self._cleanup_empty_dirs(self.recent_base / recording_type)

        if cancelled_count[0] > 0:
            logger.info(f"Migration cancelled: {result.success_count} migrated, "
                       f"{cancelled_count[0]} cancelled, {result.failed_count} failed")
        else:
            logger.info(f"Migration complete: {result.success_count} migrated, "
                       f"{result.failed_count} failed, {result.skipped_count} skipped")

        return result

    # =========================================================================
    # ARCHIVE CLEANUP (DELETION)
    # =========================================================================

    def cleanup_archive(self, recording_type: str = "motion",
                        force: bool = False) -> MigrationResult:
        """
        Delete old recordings from archive tier.

        Triggers:
        - file.age > archive_retention_days
        - OR archive_disk.free_percent < min_free_space_percent

        Args:
            recording_type: Type of recordings to clean
            force: If True, clean regardless of thresholds

        Returns:
            MigrationResult with operation details
        """
        result = MigrationResult()

        # Determine trigger reason
        capacity_triggered, free_percent = self.check_capacity_trigger('archive')
        retention_days = self.config.get_archive_retention_days()
        cutoff_date = datetime.now() - timedelta(days=retention_days)

        if capacity_triggered:
            result.trigger_reason = f"capacity (free: {free_percent:.1f}%)"
            trigger_type = "capacity"
        else:
            result.trigger_reason = f"retention (> {retention_days} days)"
            trigger_type = "age"

        logger.info(f"Starting archive cleanup for {recording_type} - trigger: {result.trigger_reason}")

        # Query eligible recordings (oldest first for capacity-based deletion)
        where_clause = (
            f"storage_tier=eq.archive"
            f"&timestamp=lt.{cutoff_date.isoformat()}"
        )

        recordings = self._query_recordings(where_clause, "timestamp.asc")

        if not recordings:
            logger.info(f"No eligible recordings found for cleanup ({recording_type})")
            return result

        logger.info(f"Found {len(recordings)} recordings to delete")

        # Process each recording
        for rec in recordings:
            file_path = Path(rec.get('file_path', ''))
            recording_id = rec.get('id')
            camera_id = rec.get('camera_id')

            # Get file size before deletion
            try:
                file_size = file_path.stat().st_size if file_path.exists() else 0
            except:
                file_size = 0

            # Delete file from disk
            file_deleted = False
            if file_path.exists():
                try:
                    file_path.unlink()
                    file_deleted = True
                except Exception as e:
                    logger.error(f"Failed to delete file {file_path}: {e}")
                    result.failed_count += 1
                    result.errors.append(str(e))

                    self._log_operation(
                        operation='error',
                        source_path=str(file_path),
                        recording_id=recording_id,
                        camera_id=camera_id,
                        reason='file deletion failed',
                        trigger_type=trigger_type,
                        success=False,
                        error_message=str(e)
                    )
                    continue
            else:
                # File already gone, just clean DB
                file_deleted = True

            # Delete from database
            if file_deleted:
                db_deleted = self._delete_recording(recording_id)

                if db_deleted:
                    result.success_count += 1
                    result.bytes_processed += file_size
                    result.details.append({
                        'recording_id': recording_id,
                        'camera_id': camera_id,
                        'path': str(file_path),
                        'size_bytes': file_size
                    })

                    self._log_operation(
                        operation='delete',
                        source_path=str(file_path),
                        file_size_bytes=file_size,
                        recording_id=recording_id,
                        camera_id=camera_id,
                        reason=f"older than {retention_days} days",
                        trigger_type=trigger_type,
                        success=True
                    )

                    logger.debug(f"Deleted: {file_path.name}")
                else:
                    result.failed_count += 1
                    result.errors.append(f"DB delete failed for {recording_id}")

        # Cleanup empty directories
        self._cleanup_empty_dirs(self.archive_base / recording_type)

        logger.info(f"Cleanup complete: {result.success_count} deleted, "
                   f"{result.failed_count} failed, freed {result.bytes_processed / (1024**2):.1f} MB")

        return result

    # =========================================================================
    # DATABASE RECONCILIATION
    # =========================================================================

    def reconcile_db_with_filesystem(self, progress_callback: Optional[Callable] = None) -> MigrationResult:
        """
        Remove orphaned database entries where files no longer exist.

        Uses a threading lock to prevent concurrent reconciliation operations
        (which cause PostgreSQL lock contention and PostgREST pool exhaustion).

        This handles:
        - Files manually deleted from disk
        - Disk failures
        - Inconsistent state after crashes

        Args:
            progress_callback: Optional callback(files_processed, files_total, current_file)
                              for real-time progress updates

        Returns:
            MigrationResult with reconciliation details
        """
        # Prevent concurrent reconciliation - multiple DELETE operations on
        # recordings table cause massive lock contention (98M+ rows in
        # file_operations_log FK makes each batch take minutes)
        if not self._bulk_delete_lock.acquire(blocking=False):
            logger.warning("Reconciliation already in progress - skipping duplicate request")
            result = MigrationResult()
            result.trigger_reason = "reconciliation"
            result.errors.append("Reconciliation already in progress")
            return result

        try:
            return self._do_reconcile(progress_callback)
        finally:
            self._bulk_delete_lock.release()

    def _do_reconcile(self, progress_callback: Optional[Callable] = None) -> MigrationResult:
        """
        Internal reconciliation implementation (called with lock held).
        """
        result = MigrationResult()
        result.trigger_reason = "reconciliation"

        logger.info("Starting database reconciliation")

        # Query all recordings
        all_recordings = self._query_recordings("select=id,file_path,camera_id,storage_tier")

        if not all_recordings:
            logger.info("No recordings found in database")
            return result

        total_records = len(all_recordings)
        logger.info(f"Checking {total_records} recordings against filesystem")

        orphaned = []
        checked = 0

        # Phase 1: Find orphaned records (with progress updates)
        for rec in all_recordings:
            file_path = Path(rec.get('file_path', ''))
            checked += 1

            if not file_path.exists():
                orphaned.append(rec)

            # Update progress every 500 records during scan phase
            if progress_callback and checked % 500 == 0:
                progress_callback(
                    files_processed=checked,
                    files_total=total_records,
                    current_file=f"Scanning: {checked}/{total_records} checked, {len(orphaned)} orphans found"
                )

        logger.info(f"Found {len(orphaned)} orphaned database entries")

        # Phase 2: Remove orphaned entries using direct SQL batch DELETE.
        # PostgREST is NOT used for this - it's all psycopg2 direct SQL.
        total_orphans = len(orphaned)
        orphan_ids = [rec.get('id') for rec in orphaned if rec.get('id')]

        if orphan_ids:
            logger.info(f"Batch-deleting {len(orphan_ids)} orphaned DB entries via direct SQL...")
            deleted = self._batch_delete_recordings(
                orphan_ids,
                progress_callback=progress_callback
            )
            result.success_count = deleted
            result.failed_count = len(orphan_ids) - deleted

            # Log a single reconcile operation for the batch
            self._log_operation(
                operation='reconcile',
                source_path=f"batch:{len(orphan_ids)} orphaned entries",
                reason=f'{deleted} orphaned entries removed (batch)',
                trigger_type='reconcile',
                success=deleted > 0
            )

            # Final progress update
            if progress_callback:
                progress_callback(
                    files_processed=total_orphans,
                    files_total=total_orphans,
                    current_file=f"Complete: {deleted} orphaned entries removed"
                )

        logger.info(f"Reconciliation complete: {result.success_count} orphaned entries removed")

        return result

    # =========================================================================
    # FULL MIGRATION RUN
    # =========================================================================

    def run_full_migration(self) -> Dict[str, MigrationResult]:
        """
        Run complete migration cycle for all recording types.

        1. Migrate recent → archive for all types
        2. Cleanup archive for all types
        3. Reconcile database

        Returns:
            Dict mapping operation names to results
        """
        results = {}

        logger.info("=" * 60)
        logger.info("Starting full storage migration cycle")
        logger.info("=" * 60)

        # Phase 1: Migrate recent → archive
        for rec_type in self.RECORDING_TYPES:
            key = f"migrate_{rec_type}"
            results[key] = self.migrate_recent_to_archive(rec_type)

        # Phase 2: Cleanup archive
        for rec_type in self.RECORDING_TYPES:
            key = f"cleanup_{rec_type}"
            results[key] = self.cleanup_archive(rec_type)

        # Phase 3: Reconcile
        results['reconcile'] = self.reconcile_db_with_filesystem()

        # Summary
        total_migrated = sum(r.success_count for k, r in results.items() if k.startswith('migrate_'))
        total_deleted = sum(r.success_count for k, r in results.items() if k.startswith('cleanup_'))
        total_reconciled = results.get('reconcile', MigrationResult()).success_count

        logger.info("=" * 60)
        logger.info(f"Migration cycle complete:")
        logger.info(f"  Migrated: {total_migrated} files")
        logger.info(f"  Deleted: {total_deleted} files")
        logger.info(f"  Reconciled: {total_reconciled} orphaned entries")
        logger.info("=" * 60)

        return results

    # =========================================================================
    # STATISTICS
    # =========================================================================

    def get_storage_stats(self) -> Dict[str, Any]:
        """
        Get comprehensive storage statistics for UI display.

        Returns:
            Dict with disk usage, file counts, warnings
        """
        # For archive, use a mounted subdir (archive_base itself may not be a mount point)
        # In Docker, subdirs like /recordings/STORAGE/motion are mounted, not /recordings/STORAGE
        archive_check_path = self.archive_base / 'motion'
        if not archive_check_path.exists():
            archive_check_path = self.archive_base  # fallback

        stats = {
            'recent': self.get_disk_usage(self.recent_base),
            'archive': self.get_disk_usage(archive_check_path),
            'config': {
                'age_threshold_days': self.config.get_migration_age_threshold(),
                'archive_retention_days': self.config.get_archive_retention_days(),
                'min_free_space_percent': self.config.get_min_free_space_percent(),
                'migration_enabled': self.config.is_migration_enabled()
            },
            'warnings': []
        }

        # Add paths for UI
        storage_paths = self.config.get_storage_paths()
        stats['recent']['host_path'] = storage_paths['recent_host_path']
        stats['archive']['host_path'] = storage_paths['archive_host_path']

        # Check for warnings
        min_free = self.config.get_min_free_space_percent()

        if stats['recent'].get('free_percent', 100) < min_free:
            stats['warnings'].append(
                f"Recent storage critically low ({stats['recent']['free_percent']:.1f}% free)"
            )
        elif stats['recent'].get('free_percent', 100) < min_free + 10:
            stats['warnings'].append(
                f"Recent storage getting low ({stats['recent']['free_percent']:.1f}% free)"
            )

        if stats['archive'].get('free_percent', 100) < min_free:
            stats['warnings'].append(
                f"Archive storage critically low ({stats['archive']['free_percent']:.1f}% free)"
            )
        elif stats['archive'].get('free_percent', 100) < min_free + 10:
            stats['warnings'].append(
                f"Archive storage getting low ({stats['archive']['free_percent']:.1f}% free)"
            )

        # Count recordings per tier
        try:
            recent_count = len(self._query_recordings("storage_tier=eq.recent&select=id"))
            archive_count = len(self._query_recordings("storage_tier=eq.archive&select=id"))
            stats['recent']['recording_count'] = recent_count
            stats['archive']['recording_count'] = archive_count
        except:
            pass

        return stats

    # =========================================================================
    # AUTOMATIC MIGRATION BACKGROUND THREAD
    # =========================================================================

    def start_auto_migration_monitor(self, check_interval_seconds: int = 300):
        """
        Start a background thread that monitors disk usage and triggers
        migration when capacity thresholds are exceeded.

        This runs continuously, checking disk state every check_interval_seconds.
        Migration is triggered when:
        - Recent storage free space < min_free_space_percent

        Args:
            check_interval_seconds: How often to check disk state (default: 5 minutes)
        """
        import threading
        import time

        def monitor_loop():
            logger.info(f"[AUTO-MIGRATE] Background monitor started (interval: {check_interval_seconds}s)")

            while self._monitor_running:
                try:
                    # Check if migration is enabled
                    if not self.config.is_migration_enabled():
                        logger.debug("[AUTO-MIGRATE] Migration disabled in config, skipping check")
                        time.sleep(check_interval_seconds)
                        continue

                    # Check recent storage capacity
                    capacity_triggered, free_percent = self.check_capacity_trigger('recent')

                    # Always run age-based migration (files older than age_threshold_days)
                    age_threshold = self.config.get_migration_age_threshold()
                    migration_needed = capacity_triggered  # Track if any migration happened

                    for rec_type in self.RECORDING_TYPES:
                        try:
                            result = self.migrate_recent_to_archive(rec_type)
                            if result.success_count > 0:
                                migration_needed = True
                                logger.info(f"[AUTO-MIGRATE] {rec_type}: migrated {result.success_count} files, "
                                          f"freed {result.bytes_processed / (1024**3):.2f} GB")
                        except Exception as e:
                            logger.error(f"[AUTO-MIGRATE] Error migrating {rec_type}: {e}")

                    if capacity_triggered:
                        logger.warning(f"[AUTO-MIGRATE] Capacity threshold triggered! "
                                      f"Free: {free_percent:.1f}%")

                    if migration_needed:
                        # Re-check capacity after migration
                        _, new_free_percent = self.check_capacity_trigger('recent')
                        logger.info(f"[AUTO-MIGRATE] Migration complete. Free space: {new_free_percent:.1f}%")
                    else:
                        logger.debug(f"[AUTO-MIGRATE] No files to migrate (age>{age_threshold}d). Free: {free_percent:.1f}%")

                except Exception as e:
                    logger.error(f"[AUTO-MIGRATE] Monitor error: {e}")

                # Sleep until next check
                time.sleep(check_interval_seconds)

            logger.info("[AUTO-MIGRATE] Background monitor stopped")

        # Initialize monitor state
        self._monitor_running = True
        self._monitor_thread = threading.Thread(target=monitor_loop, daemon=True, name="storage-migration-monitor")
        self._monitor_thread.start()
        logger.info("[AUTO-MIGRATE] Monitor thread started")

    def stop_auto_migration_monitor(self):
        """Stop the background migration monitor."""
        if hasattr(self, '_monitor_running'):
            self._monitor_running = False
            logger.info("[AUTO-MIGRATE] Stopping monitor thread...")
            if hasattr(self, '_monitor_thread') and self._monitor_thread.is_alive():
                self._monitor_thread.join(timeout=10)
                logger.info("[AUTO-MIGRATE] Monitor thread stopped")
