"""
services/telemetry_event_log.py — write API for the per-layer telemetry event log.

Every probe (camera-state hook, publisher-state hook, MediaMTX/go2rtc diff,
RTSP probe, resource snapshot) goes through ``emit()`` below. The helper:

  1. NO-OPS when ``telemetry_settings.is_enabled() == False``. This is the
     dominant case at fresh install and the cheap exit path: we read the
     in-process settings cache (no DB hit), check the flag, and return.
  2. INSERTS a single row when enabled. Failures are logged but do NOT
     propagate — a probe that can't write must not crash its host loop.
  3. DEBOUNCES identical consecutive transitions per (category, camera_id,
     subcategory) inside a configurable window. Default 30s prevents
     flapping-stream write amplification per design doc §8.

Concurrency: psycopg2 connections are not thread-safe across threads, so
each ``emit()`` opens, uses, and closes its own connection. With low
event rates this is fine. If we ever need more, switch to a per-thread
pool — but don't pre-optimize.

Schemas documented per category in the migration's COMMENT statements:

    category            payload contract
    ──────────────────  ─────────────────────────────────────────────────
    camera_state        {from: str, to: str, reason: str, ...}
    publisher           {from: str, to: str, path: str, ...}
    ffmpeg              {pid: int, camera_id: str, cmd: list, rss_bytes: int}
    mediamtx_path       {path: str, from: {...}, to: {...}}
    go2rtc_path         {path: str, from: {...}, to: {...}}
    rtsp_probe          {url: str, error: str | null, latency_ms: int, hub: str}
    resource_snapshot   {ffmpeg_count, mediamtx_path_count, ..., conntrack_count}
    docker_conntrack    {count: int, max: int}

Severity constraint:  info | warning | error  (DB check constraint, do not
violate or the INSERT raises).
"""

import json
import logging
import os
import threading
import time
from typing import Any, Optional

import psycopg2
from psycopg2.extras import Json

from services import telemetry_settings as ts

logger = logging.getLogger(__name__)

ALLOWED_CATEGORIES = {
    'camera_state',
    'publisher',
    'ffmpeg',
    'mediamtx_path',
    'go2rtc_path',
    'rtsp_probe',
    'resource_snapshot',
    'docker_conntrack',
}

ALLOWED_SEVERITIES = {'info', 'warning', 'error'}

DEBOUNCE_DEFAULT_SECONDS = 30.0
DEBOUNCE_MAX_KEYS        = 4096   # bound the debounce dict; LRU-style eviction

# (category, camera_id_or_None, subcategory_or_None, from→to identity) → last_emit_ts
# A degenerate flap on one camera occupies one entry, not 86,400.
_debounce_lock = threading.Lock()
_debounce_state: dict = {}


def _db_conn():
    """Direct psycopg2 connection — same pattern as audit_listener._db_conn()."""
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "postgres"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "nvr"),
        user=os.getenv("POSTGRES_USER", "nvr_api"),
        password=os.getenv("POSTGRES_PASSWORD", "nvr_internal_db_key"),
        connect_timeout=5,
    )


def _make_debounce_key(category: str, camera_id: Optional[str],
                       subcategory: Optional[str], payload: dict) -> tuple:
    """Build the stable key used for debounce checking."""
    # For transitions, fold the from→to pair into the key so distinct
    # transitions remain distinguishable; for snapshots, the key is just
    # (category, subcategory) because every snapshot row is unique by ts.
    from_to = (payload.get('from'), payload.get('to'))
    return (category, camera_id, subcategory, from_to)


def _should_debounce(key: tuple, window_seconds: float) -> bool:
    """Returns True if the same key was emitted within window_seconds. Updates state on emit."""
    now = time.monotonic()
    with _debounce_lock:
        last = _debounce_state.get(key)
        if last is not None and (now - last) < window_seconds:
            return True
        # Evict the oldest entry if we're over budget. This is cheap O(n)
        # at the cap; if we ever need true LRU, switch to OrderedDict.
        if len(_debounce_state) >= DEBOUNCE_MAX_KEYS:
            oldest_key = min(_debounce_state, key=_debounce_state.get)
            _debounce_state.pop(oldest_key, None)
        _debounce_state[key] = now
        return False


def emit(category: str,
         payload: Optional[dict] = None,
         subcategory: Optional[str] = None,
         camera_id: Optional[str] = None,
         severity: str = 'info',
         debounce_seconds: float = DEBOUNCE_DEFAULT_SECONDS) -> bool:
    """
    Write one row to telemetry_events. NO-OP when telemetry is disabled.

    Returns True if a row was actually written, False on no-op / debounce /
    error. The boolean is for tests; callers normally ignore the return.

    debounce_seconds=0 disables debounce for a specific call site (use
    sparingly — useful for resource snapshots which are sampled, not
    transitions).
    """
    if not ts.is_enabled():
        return False

    if category not in ALLOWED_CATEGORIES:
        logger.warning(f"[telemetry] unknown category {category!r} — skipped")
        return False
    if severity not in ALLOWED_SEVERITIES:
        logger.warning(f"[telemetry] unknown severity {severity!r} — defaulting to info")
        severity = 'info'

    payload = payload or {}

    if debounce_seconds > 0:
        key = _make_debounce_key(category, camera_id, subcategory, payload)
        if _should_debounce(key, debounce_seconds):
            return False

    try:
        with _db_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO telemetry_events "
                "(category, subcategory, camera_id, severity, payload) "
                "VALUES (%s, %s, %s, %s, %s)",
                (category, subcategory, camera_id, severity, Json(payload))
            )
        return True
    except Exception as e:
        logger.error(f"[telemetry] INSERT failed cat={category} cam={camera_id} sub={subcategory}: {e}")
        return False


def emit_transition(category: str,
                    camera_id: Optional[str],
                    from_value: Any,
                    to_value: Any,
                    extra: Optional[dict] = None,
                    severity: str = 'info',
                    debounce_seconds: float = DEBOUNCE_DEFAULT_SECONDS) -> bool:
    """
    Convenience wrapper for the common transition pattern. Skips entirely
    when from_value == to_value (a no-op transition that we never want
    to record).
    """
    if from_value == to_value:
        return False
    payload = {'from': from_value, 'to': to_value}
    if extra:
        payload.update(extra)
    return emit(
        category=category,
        subcategory='transition',
        camera_id=camera_id,
        payload=payload,
        severity=severity,
        debounce_seconds=debounce_seconds,
    )


def emit_snapshot(category: str,
                  payload: dict,
                  camera_id: Optional[str] = None,
                  severity: str = 'info') -> bool:
    """
    Convenience wrapper for periodic resource snapshots. Debounce is OFF
    by default — every sampling tick should produce a row (subject to
    the global telemetry-disabled gate).
    """
    return emit(
        category=category,
        subcategory='snapshot',
        camera_id=camera_id,
        payload=payload,
        severity=severity,
        debounce_seconds=0,
    )
