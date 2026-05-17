"""
services/onvif/onvif_health.py — write ONVIF subscription health observations
to the `cameras` table.

Each camera's listener calls one of these on a state change:

    record_subscribe_success(camera_id)   — Subscribe Creation succeeded.
                                           Zeroes failure_count, stamps
                                           last_success_ts, sets state='healthy'.
    record_subscribe_failure(camera_id, error_msg) — listener-level
                                           failure (CreatePullPointSubscription
                                           threw, credential lookup blew up,
                                           connection timed out, etc.).
                                           Increments failure_count, stamps
                                           last_failure_ts + last_error_message,
                                           sets state='failing'.

Both are best-effort: if the DB is down, the listener keeps running and
the operator just doesn't get the health signal for that interval.
Never raises; logs and returns.

This module is the WRITE side. The READ side is exposed via a GET route
(see routes/onvif_health.py). Auto-disable + revert logic lives in a
future branch — by design, so the operator can shape that policy after
observing real failure rates via the read endpoint.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import psycopg2

logger = logging.getLogger(__name__)


def _db_conn():
    """Same pattern as the rest of the direct-psycopg2 callsites in this repo."""
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "postgres"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "nvr"),
        user=os.getenv("POSTGRES_USER", "nvr_api"),
        password=os.getenv("POSTGRES_PASSWORD", "nvr_internal_db_key"),
        connect_timeout=3,
    )


# Truncate stored error messages to a reasonable size. Real listener errors
# fit comfortably inside this — anything longer is almost always a stack
# trace dump we don't want in the DB.
_MAX_ERROR_LEN = 500


def record_subscribe_success(camera_id: str) -> None:
    """Reset failure_count, stamp last_success_ts, set state=healthy."""
    try:
        with _db_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE cameras
                   SET onvif_failure_count       = 0,
                       onvif_last_success_ts     = NOW(),
                       onvif_subscription_state  = 'healthy'
                 WHERE serial = %s
                """,
                (camera_id,),
            )
    except Exception:
        # Never let an observability write break the listener.
        logger.debug("record_subscribe_success failed for %s", camera_id, exc_info=True)


def record_subscribe_failure(camera_id: str, error_msg: Optional[str]) -> None:
    """Increment failure_count, stamp last_failure_ts + error, set state=failing.

    The increment is done in the UPDATE statement itself so concurrent
    listener loops (one per camera) don't collide via read-modify-write.
    """
    msg = (error_msg or "")[:_MAX_ERROR_LEN]
    try:
        with _db_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE cameras
                   SET onvif_failure_count       = onvif_failure_count + 1,
                       onvif_last_failure_ts     = NOW(),
                       onvif_last_error_message  = %s,
                       onvif_subscription_state  = 'failing'
                 WHERE serial = %s
                """,
                (msg, camera_id),
            )
    except Exception:
        logger.debug("record_subscribe_failure failed for %s", camera_id, exc_info=True)
