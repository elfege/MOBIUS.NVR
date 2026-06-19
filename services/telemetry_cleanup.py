"""
services/telemetry_cleanup.py — bounded-retention cleanup tick for
telemetry_events.

Runs once per hour inside the unified-nvr container. Skips entirely when
admin has telemetry disabled (the dominant case at fresh install). When
enabled, performs two passes in order:

  1. RETENTION DELETE — rows with ts < (now() - retention_days) go.
     This is the hard time-window cap; old data is never useful for
     debugging a fresh incident.

  2. SIZE CAP DELETE — if pg_total_relation_size('telemetry_events') is
     still ≥ 90 % of the admin-set max_size_mb cap after pass 1, delete
     the oldest rows until size drops below 80 % of the cap. Two-band
     hysteresis (trigger at 90, settle at 80) prevents flapping when
     each tick deletes a tiny number of rows.

Single `DELETE WHERE id IN (subselect ORDER BY ts ASC LIMIT n)` keeps each
cleanup pass O(n) with the indexed plan; n is bounded by a per-tick max so
a single huge prune cannot lock the table for more than a few seconds.
"""

import logging
import threading
import time
from typing import Optional

from services import telemetry_settings as ts
from services.db import cursor as db_cursor

logger = logging.getLogger(__name__)

CLEANUP_INTERVAL_SECONDS = 60 * 60   # 1 hour
SIZE_TRIGGER_RATIO       = 0.90      # start size-pruning at 90 % of cap
SIZE_SETTLE_RATIO        = 0.80      # stop size-pruning at 80 % of cap
MAX_DELETE_PER_PASS      = 50_000    # safety: never delete more than 50k rows in a single tick

_TABLE = 'telemetry_events'

_cleanup_thread: Optional[threading.Thread] = None
_cleanup_stop_event = threading.Event()


def table_size_bytes() -> int:
    """Total relation size of telemetry_events, in bytes. 0 on failure."""
    try:
        with db_cursor() as cur:
            cur.execute("SELECT pg_total_relation_size(%s)", (_TABLE,))
            row = cur.fetchone()
            return int(row[0]) if row else 0
    except Exception as e:
        logger.warning(f"[telemetry-cleanup] table_size_bytes() failed: {e}")
        return 0


def row_count() -> int:
    """Approximate row count. Uses pg_class.reltuples (cheap, may be stale)."""
    try:
        with db_cursor() as cur:
            cur.execute(
                "SELECT GREATEST(0, reltuples)::BIGINT "
                "FROM pg_class WHERE relname = %s",
                (_TABLE,)
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0
    except Exception as e:
        logger.warning(f"[telemetry-cleanup] row_count() failed: {e}")
        return 0


def _delete_older_than(retention_days: int) -> int:
    """Delete rows older than the retention window. Returns rows deleted."""
    try:
        with db_cursor() as cur:
            cur.execute(
                f"DELETE FROM {_TABLE} "
                f"WHERE id IN ("
                f"  SELECT id FROM {_TABLE} "
                f"  WHERE ts < now() - (%s::int * INTERVAL '1 day') "
                f"  LIMIT %s"
                f")",
                (retention_days, MAX_DELETE_PER_PASS)
            )
            return cur.rowcount or 0
    except Exception as e:
        logger.error(f"[telemetry-cleanup] retention delete failed: {e}")
        return 0


def _delete_oldest_until_below(target_bytes: int) -> int:
    """Delete oldest rows until table size <= target_bytes. Returns rows deleted."""
    deleted_total = 0
    while True:
        size = table_size_bytes()
        if size <= target_bytes:
            return deleted_total
        # Estimate how many rows to delete per iteration based on average row size.
        rows = row_count()
        if rows <= 0:
            return deleted_total
        avg_bytes_per_row = max(1, size // max(1, rows))
        bytes_to_free = size - target_bytes
        # Try to delete a chunk large enough to settle in one or two passes,
        # but never exceed MAX_DELETE_PER_PASS.
        chunk = min(MAX_DELETE_PER_PASS, max(1000, (bytes_to_free // avg_bytes_per_row) + 1))
        try:
            with db_cursor() as cur:
                cur.execute(
                    f"DELETE FROM {_TABLE} "
                    f"WHERE id IN ("
                    f"  SELECT id FROM {_TABLE} "
                    f"  ORDER BY ts ASC "
                    f"  LIMIT %s"
                    f")",
                    (chunk,)
                )
                deleted_this_pass = cur.rowcount or 0
        except Exception as e:
            logger.error(f"[telemetry-cleanup] size-cap delete failed: {e}")
            return deleted_total

        if deleted_this_pass <= 0:
            return deleted_total
        deleted_total += deleted_this_pass

        # Re-check size against the size cap in the next iteration. Force
        # an autovacuum hint after a large delete so size reflects reality.
        if deleted_total >= MAX_DELETE_PER_PASS:
            return deleted_total
    # unreachable


def run_cleanup_once(reason: str = 'scheduled') -> dict:
    """
    Single cleanup pass. Returns a summary dict for logging / API surfacing.

    No-op (returns skipped=True) when telemetry is disabled in nvr_settings.
    Callers may invoke this directly after admin lowers the cap so the
    cleanup happens immediately instead of waiting up to an hour.
    """
    if not ts.is_enabled():
        return {'skipped': True, 'reason_skipped': 'telemetry disabled', 'reason': reason}

    retention = ts.retention_days()
    cap_mb    = ts.max_size_mb()
    cap_bytes = cap_mb * 1024 * 1024

    size_before = table_size_bytes()
    retention_deleted = _delete_older_than(retention)

    size_after_retention = table_size_bytes()
    trigger_bytes = int(cap_bytes * SIZE_TRIGGER_RATIO)
    settle_bytes  = int(cap_bytes * SIZE_SETTLE_RATIO)

    size_deleted = 0
    if size_after_retention >= trigger_bytes:
        size_deleted = _delete_oldest_until_below(settle_bytes)

    size_after = table_size_bytes()

    summary = {
        'skipped': False,
        'reason': reason,
        'retention_days': retention,
        'max_size_mb': cap_mb,
        'size_bytes_before': size_before,
        'size_bytes_after_retention': size_after_retention,
        'size_bytes_after': size_after,
        'rows_deleted_retention': retention_deleted,
        'rows_deleted_size_cap': size_deleted,
    }

    if retention_deleted or size_deleted:
        logger.info(f"[telemetry-cleanup] {summary}")
    else:
        logger.debug(f"[telemetry-cleanup] no-op {summary}")
    return summary


def _cleanup_loop():
    """Hourly tick. Cooperative shutdown via _cleanup_stop_event."""
    logger.info(f"[telemetry-cleanup] loop started (interval={CLEANUP_INTERVAL_SECONDS}s)")
    # Initial tick after ~30s so a misconfigured deployment fails fast on logs
    # rather than waiting an hour.
    if _cleanup_stop_event.wait(30):
        return
    while not _cleanup_stop_event.is_set():
        try:
            run_cleanup_once(reason='loop_tick')
        except Exception:
            logger.exception("[telemetry-cleanup] loop tick raised")
        if _cleanup_stop_event.wait(CLEANUP_INTERVAL_SECONDS):
            break
    logger.info("[telemetry-cleanup] loop stopped")


def start_cleanup_loop():
    """Start the background tick thread. Safe to call multiple times."""
    global _cleanup_thread
    if _cleanup_thread is not None and _cleanup_thread.is_alive():
        return
    _cleanup_stop_event.clear()
    _cleanup_thread = threading.Thread(
        target=_cleanup_loop,
        name='telemetry-cleanup-loop',
        daemon=True
    )
    _cleanup_thread.start()


def stop_cleanup_loop():
    """Stop the background tick (for tests / clean shutdown)."""
    _cleanup_stop_event.set()
