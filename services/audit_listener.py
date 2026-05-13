"""
services/audit_listener.py — single-subscription consumer for the
'setting_changed' Postgres NOTIFY channel.

Why this exists
---------------

Phase 2 trigger-based audit (2026-05-13). Every settings table has an
AFTER INSERT/UPDATE trigger (see migration 036) that:

  1. INSERTs a row into setting_audit_log.
  2. pg_notify('setting_changed', <payload>).

This module subscribes to that channel ONCE at app boot, in its own
daemon thread, and fans the event out to:

  - SocketIO broadcast on /stream_events  (event name: 'setting_changed')
    so admin tabs / Logs UI can update in real time.
  - Future plugins: Anamnesis ingest, alerting, etc.

Adding a new consumer means adding ONE entry to the FANOUT list below.
No per-endpoint glue.

Also runs a small background prune that deletes audit rows older than
SETTING_AUDIT_RETENTION_DAYS (default 90, configurable). Hourly tick.
"""

from __future__ import annotations

import json
import logging
import os
import select
import threading
import time
from typing import Callable, List

import psycopg2
import psycopg2.extensions

logger = logging.getLogger(__name__)

# Tunables — operator-set via env if they want to deviate.
SETTING_AUDIT_RETENTION_DAYS = int(os.getenv("SETTING_AUDIT_RETENTION_DAYS", "90"))
_PRUNE_INTERVAL_SECONDS = 3600.0  # once an hour

# Sentinel for the listener thread so init_audit_listener is idempotent.
_started_lock = threading.Lock()
_started = False

# SocketIO instance injected at app boot via init_audit_listener(socketio).
_socketio = None

# Add additional consumers here. Each is called with the parsed JSON
# payload dict (keys: table, pk, op, old, new, ts). Plugins must not
# raise — exceptions are caught and logged by the dispatcher.
FANOUT: List[Callable[[dict], None]] = []


def _db_conn():
    """Direct psycopg2 connection — same pattern as routes/host_state.py:_db_conn()."""
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "postgres"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "nvr"),
        user=os.getenv("POSTGRES_USER", "nvr_api"),
        password=os.getenv("POSTGRES_PASSWORD", "nvr_internal_db_key"),
        connect_timeout=5,
    )


def _broadcast_socketio(payload: dict) -> None:
    """Default fanout — broadcast to /stream_events. No-op if socketio not bound."""
    if _socketio is None:
        return
    try:
        _socketio.emit("setting_changed", payload, namespace="/stream_events")
    except Exception:
        logger.exception("audit_listener: socketio emit failed")


# Register the default consumer.
FANOUT.append(_broadcast_socketio)


def _dispatch(payload: dict) -> None:
    """Call every consumer in FANOUT. Exceptions in one don't break the others."""
    for consumer in FANOUT:
        try:
            consumer(payload)
        except Exception:
            logger.exception(
                "audit_listener: fanout consumer %s raised — continuing",
                getattr(consumer, "__name__", repr(consumer)),
            )


def _listen_loop() -> None:
    """
    Daemon thread: LISTEN setting_changed, dispatch every received NOTIFY.

    Reconnect-on-disconnect: any psycopg2 OperationalError sleeps briefly
    and reopens the connection. Postgres restarts (rare on a kiosk NVR
    but not impossible) shouldn't kill the listener permanently.
    """
    backoff = 1.0
    while True:
        conn = None
        try:
            conn = _db_conn()
            conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
            with conn.cursor() as cur:
                cur.execute("LISTEN setting_changed;")
            logger.info("audit_listener: subscribed to NOTIFY 'setting_changed'")
            backoff = 1.0  # reset after a successful subscribe

            while True:
                # select() blocks up to 30s waiting for the socket to be
                # readable. On timeout we loop back and poll() — keeps the
                # connection warm even when no events fire.
                ready, _, _ = select.select([conn], [], [], 30.0)
                conn.poll()
                while conn.notifies:
                    notify = conn.notifies.pop(0)
                    try:
                        payload = json.loads(notify.payload)
                    except Exception:
                        logger.warning(
                            "audit_listener: payload not JSON, dropping: %r",
                            notify.payload[:200],
                        )
                        continue
                    _dispatch(payload)
        except Exception:
            logger.exception(
                "audit_listener: listen loop crashed — reconnecting in %.0fs",
                backoff,
            )
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


def _prune_loop() -> None:
    """Daemon thread: hourly prune of audit rows older than the retention window."""
    while True:
        try:
            with _db_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM setting_audit_log WHERE ts < NOW() - (%s::text || ' days')::interval",
                    (str(SETTING_AUDIT_RETENTION_DAYS),),
                )
                if cur.rowcount:
                    logger.info(
                        "audit_listener: pruned %d audit row(s) older than %d days",
                        cur.rowcount, SETTING_AUDIT_RETENTION_DAYS,
                    )
        except Exception:
            logger.exception("audit_listener: prune iteration failed")
        time.sleep(_PRUNE_INTERVAL_SECONDS)


def init_audit_listener(socketio_instance) -> None:
    """
    Called from app.py after socketio is constructed. Idempotent.
    Starts the LISTEN thread and the retention-prune thread.
    """
    global _started, _socketio
    with _started_lock:
        if _started:
            return
        _started = True

    _socketio = socketio_instance
    logger.info(
        "audit_listener: starting (retention=%d days, channel='setting_changed')",
        SETTING_AUDIT_RETENTION_DAYS,
    )

    t1 = threading.Thread(target=_listen_loop, name="audit_listener", daemon=True)
    t1.start()
    t2 = threading.Thread(target=_prune_loop, name="audit_prune", daemon=True)
    t2.start()
