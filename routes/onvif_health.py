"""
routes/onvif_health.py — read-only health endpoint for ONVIF subscription
observability.

GET /api/onvif/health/<serial>
    Admin-only. Returns the current onvif_* health columns for one
    camera. The columns are written by services/onvif/onvif_health.py
    from inside the ONVIF event listener.

GET /api/onvif/health
    Admin-only. Returns the health snapshot for ALL cameras that
    have an ONVIF subscription state (state IS NOT NULL OR
    failure_count > 0). Sorted by failure_count descending so the
    operator's eye lands on the worst offender first.

The write side + auto-disable policy live in separate concerns; this
file is read-only. Adding a `revert` POST endpoint comes later when
the operator's chosen policy (e.g. "5 failures in 5 min auto-disables;
24h cooldown after manual revert") is finalized.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

import psycopg2.extras
from flask import Blueprint, jsonify
from flask_login import current_user, login_required

from services.db import cursor as db_cursor

logger = logging.getLogger(__name__)

onvif_health_bp = Blueprint("onvif_health", __name__)


def _is_admin() -> bool:
    return bool(
        getattr(current_user, "is_authenticated", False)
        and getattr(current_user, "role", None) == "admin"
    )


_FIELDS = (
    "serial, name, "
    "onvif_subscription_state, onvif_failure_count, "
    "onvif_last_failure_ts, onvif_last_success_ts, "
    "onvif_last_error_message"
)


def _row_to_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    # Serialize timestamps to ISO so the frontend can `new Date()` them
    # without server-side timezone gymnastics.
    for key in ("onvif_last_failure_ts", "onvif_last_success_ts"):
        if out.get(key) is not None:
            out[key] = out[key].isoformat()
    return out


@onvif_health_bp.route("/api/onvif/health/<serial>", methods=["GET"])
@login_required
def api_onvif_health_one(serial: str):
    if not _is_admin():
        return jsonify({"error": "admin only"}), 403
    try:
        with db_cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"SELECT {_FIELDS} FROM cameras WHERE serial = %s",
                (serial,),
            )
            row = cur.fetchone()
    except Exception as e:
        logger.exception("onvif_health_one DB error")
        return jsonify({"error": f"db error: {e}"}), 500

    if not row:
        return jsonify({"error": f"camera not found: {serial}"}), 404
    return jsonify(_row_to_dict(row))


@onvif_health_bp.route("/api/onvif/health", methods=["GET"])
@login_required
def api_onvif_health_all():
    if not _is_admin():
        return jsonify({"error": "admin only"}), 403
    try:
        with db_cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT {_FIELDS}
                  FROM cameras
                 WHERE onvif_subscription_state IS NOT NULL
                    OR onvif_failure_count > 0
                 ORDER BY onvif_failure_count DESC NULLS LAST,
                          name ASC
                """
            )
            rows: List[Dict[str, Any]] = cur.fetchall()
    except Exception as e:
        logger.exception("onvif_health_all DB error")
        return jsonify({"error": f"db error: {e}"}), 500

    return jsonify({"cameras": [_row_to_dict(r) for r in rows]})
