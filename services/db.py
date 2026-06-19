"""
services/db.py — central psycopg2 connection pool.

Replaces the ~11 hand-rolled `_db_conn()` helpers scattered across the
codebase (catalogued in
`docs/plans/sql_query_audit_scattered_psycopg2_no_connection_pool_inconsistent_postgrest_june_2026.md`).
Each of those helpers opened a fresh TCP connection on every call —
under load that exhausts Postgres `max_connections`. This module owns a
single `ThreadedConnectionPool`, parameterised from the same
`POSTGRES_*` environment variables the old helpers used.

API:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(...)

    with cursor() as cur:                                    # one-shot
        cur.execute(...)

    with cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        ...                                                   # dict rows

    get_conn() / put_conn(conn)                              # explicit
    close_all()                                              # shutdown

Both `connection()` and `cursor()` use psycopg2's connection-context
behaviour: clean exit → COMMIT; exception → ROLLBACK. The connection
itself is then returned to the pool — never closed in the normal path.
On exception the connection is returned with `close=True` so the pool
discards it (defensive: the conn may be in a bad transactional state).

`get_conn()` / `put_conn()` are for callers that can't use a context
manager — currently `services/audit_listener.py` (LISTEN/NOTIFY long-
running connection, no commit semantics).

Pool sizing:
  POSTGRES_POOL_MIN (default 2)
  POSTGRES_POOL_MAX (default 20)
  POSTGRES_CONNECT_TIMEOUT (default 5)

If the pool is exhausted, `pool.getconn()` blocks (no max-blocking-time
control in the threaded pool). If that becomes a problem we'd switch to
SimpleConnectionPool with a semaphore, or move to psycopg3's connection
pool. Today's load doesn't justify it.
"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Optional

import psycopg2
import psycopg2.extras
import psycopg2.pool

logger = logging.getLogger(__name__)


_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None
_pool_lock = threading.Lock()


def _build_pool() -> psycopg2.pool.ThreadedConnectionPool:
    """Construct the pool from the canonical POSTGRES_* env vars.

    Defaults match the legacy `_db_conn()` helpers verbatim — host
    `postgres`, port 5432, db `nvr`, user `nvr_api`, password
    `nvr_internal_db_key` — so a drop-in replacement requires zero env
    changes.
    """
    minconn = int(os.getenv("POSTGRES_POOL_MIN", "2"))
    maxconn = int(os.getenv("POSTGRES_POOL_MAX", "20"))
    connect_timeout = int(os.getenv("POSTGRES_CONNECT_TIMEOUT", "5"))
    logger.info(
        "[db] initializing pool min=%d max=%d connect_timeout=%ds host=%s db=%s user=%s",
        minconn, maxconn, connect_timeout,
        os.getenv("POSTGRES_HOST", "postgres"),
        os.getenv("POSTGRES_DB", "nvr"),
        os.getenv("POSTGRES_USER", "nvr_api"),
    )
    return psycopg2.pool.ThreadedConnectionPool(
        minconn,
        maxconn,
        host=os.getenv("POSTGRES_HOST", "postgres"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "nvr"),
        user=os.getenv("POSTGRES_USER", "nvr_api"),
        password=os.getenv("POSTGRES_PASSWORD", "nvr_internal_db_key"),
        connect_timeout=connect_timeout,
    )


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    """Lazy-init the pool. Double-checked locking so the cost of the
    lock is only paid on first call from each thread, not every call.
    """
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = _build_pool()
    return _pool


@contextmanager
def connection() -> Iterator[Any]:
    """Pooled-connection context manager.

    On clean exit: commits the transaction (via psycopg2's `with conn:`
    behaviour) and returns the connection to the pool.
    On exception: rolls back (psycopg2 again) and returns the connection
    with `close=True` so the pool discards it — defensive against a
    broken transactional state poisoning the next caller.

    The yielded conn is a regular psycopg2 connection — use `.cursor()`
    on it as normal.
    """
    pool = _get_pool()
    conn = pool.getconn()
    closed = False
    try:
        with conn:                          # psycopg2 ctx: commit/rollback
            yield conn
    except Exception:
        # Mark for close-on-return; the `with conn:` block has already
        # rolled back, but the connection may be in a degraded state
        # (e.g., aborted transaction left in pending state by a Python-
        # level exception mid-cursor). Discard rather than reuse.
        closed = True
        raise
    finally:
        pool.putconn(conn, close=closed)


@contextmanager
def cursor(cursor_factory: Optional[type] = None) -> Iterator[Any]:
    """Pooled-connection + cursor in one go.

    Convenience over `connection()` for the dominant call shape:

        with cursor() as cur:
            cur.execute(...)

        with cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            ...

    Cursor is closed on exit. Connection is committed (or rolled back)
    and returned to the pool — same semantics as `connection()`.
    """
    with connection() as conn:
        if cursor_factory is not None:
            cur = conn.cursor(cursor_factory=cursor_factory)
        else:
            cur = conn.cursor()
        try:
            yield cur
        finally:
            cur.close()


def get_conn() -> Any:
    """Direct checkout — caller MUST call put_conn().

    For callers that can't use the context-manager pattern. Today: only
    `services/audit_listener.py` for its long-running LISTEN/NOTIFY
    connection, where transactional commit semantics don't apply.
    """
    return _get_pool().getconn()


def put_conn(conn: Any, *, close: bool = False) -> None:
    """Return a connection to the pool. Pass `close=True` to discard."""
    _get_pool().putconn(conn, close=close)


def close_all() -> None:
    """Close every connection in the pool. Call on graceful shutdown.

    After this call, the next pool operation will re-init the pool
    lazily. Useful for tests + clean app exit.
    """
    global _pool
    if _pool is not None:
        with _pool_lock:
            if _pool is not None:
                _pool.closeall()
                _pool = None
