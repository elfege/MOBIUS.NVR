"""
routes/ui_event_routes.py — UI interaction audit log endpoints.

Operator decision 2026-05-13: complete traceability of every click and
keystroke for litigation-grade accountability and hacker forensics.
PII risk is explicitly accepted by the operator. Password fields are
masked to "*" CLIENT-SIDE before they ever leave the browser.

This module is the server-side counterpart to:
  - static/js/services/ui-event-tracker.js  (delegated DOM listeners)
  - static/js/services/ui-event-outbox.js   (durable batched flusher)

Three endpoints:

  POST   /api/ui-event/batch        Accept a batch of UI events.
                                    Auth: @login_required + csrf_exempt.
                                    user_id is stamped from current_user;
                                    client_id from the device_token cookie.
                                    host_label is optional from body.
  GET    /api/ui-event/log          Admin-only read for the Logs modal.
                                    Filters: from/to, kind, target_id, q,
                                    limit/offset. Same shape as
                                    /api/audit/log so the frontend can
                                    treat both sources uniformly.
  DELETE /api/ui-event/keystrokes   Admin-only. Wipes ONLY
                                    kind IN ('keystroke','focus','blur')
                                    while preserving the click trail.
                                    Returns {deleted: N}. Irreversible.

Same pattern as routes/audit_routes.py (commits 83d72fc, 7e67e96).
"""

from __future__ import annotations

import json as _json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user

from routes.helpers import csrf_exempt

logger = logging.getLogger(__name__)

ui_event_bp = Blueprint("ui_event", __name__)


# ---------------------------------------------------------------------------
# Limits / configuration
# ---------------------------------------------------------------------------

# Per-batch row cap. ui-event-outbox enforces a smaller cap; defense in depth.
MAX_BATCH_ROWS = 1000

# Per-row payload cap (after JSON encoding of target_attrs + extra).
MAX_ROW_PAYLOAD_BYTES = 16 * 1024

# Read endpoint pagination
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000

# Must match the CHECK constraint in migration 038.
VALID_KINDS = frozenset({
    "click", "keystroke", "focus", "blur", "submit",
    "navigation", "modal_open", "modal_close", "scroll",
})

# The keystroke-class kinds that the "Clear all keystroke entries" button
# is allowed to wipe. Click and submit are NEVER touched.
KEYSTROKE_CLASS_KINDS = ("keystroke", "focus", "blur")


def _db_conn():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "postgres"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "nvr"),
        user=os.getenv("POSTGRES_USER", "nvr_api"),
        password=os.getenv("POSTGRES_PASSWORD", "nvr_internal_db_key"),
        connect_timeout=5,
    )


def _is_admin() -> bool:
    """Mirrors the admin check used by audit_routes._is_admin()."""
    try:
        return current_user.is_authenticated and getattr(current_user, "role", "") == "admin"
    except Exception:
        return False


def _truncate(s: Optional[str], n: int) -> Optional[str]:
    """Defensive server-side truncation (frontend already truncates)."""
    if s is None:
        return None
    s = str(s)
    return s[:n] if len(s) > n else s


# ---------------------------------------------------------------------------
# POST /api/ui-event/batch
# ---------------------------------------------------------------------------

@ui_event_bp.route("/api/ui-event/batch", methods=["POST"])
@csrf_exempt
@login_required
def api_ui_event_batch():
    """
    Accept a batch of UI events from the browser ui-event-outbox.

    Body:
        {
          "host_label": "<optional, e.g. 'office-kiosk'>",
          "events": [
            {
              "ts":           "2026-05-13T15:42:11.000Z",  (ISO8601, optional)
              "kind":         "click"|"keystroke"|"focus"|...
              "target_id":    "save-btn"      (optional)
              "target_tag":   "BUTTON"        (optional)
              "target_text":  "Save"          (optional, <=200 chars)
              "target_attrs": {...}           (optional)
              "page_url":     "/streams"      (optional)
              "extra":        {...}           (optional per-kind details)
            },
            ...
          ]
        }

    Auth: @login_required (session). user_id is taken from current_user,
    client_id from the device_token cookie. Neither is trusted from the
    body — defense against a misbehaving client trying to spoof identity.

    Response:
        200 {"accepted": N}
        207 {"accepted": N, "rejected": [...]}  partial — bad rows skipped
        400 {"error": "..."}                    body invalid or empty
    """
    body = request.get_json(silent=True) or {}
    events = body.get("events")
    if not isinstance(events, list) or not events:
        return jsonify({"error": "events must be a non-empty array"}), 400
    if len(events) > MAX_BATCH_ROWS:
        return jsonify({"error": f"batch exceeds {MAX_BATCH_ROWS} rows"}), 400

    # host_label is operator-supplied identifying string for the kiosk
    # machine. It's stored alongside user_id/client_id to disambiguate
    # "which physical screen was this action taken on" — multiple
    # devices may share a user account.
    host_label = _truncate(body.get("host_label"), 128)

    device_token = request.cookies.get("device_token")
    actor_user_id = current_user.id if current_user.is_authenticated else None

    accepted = 0
    rejected: List[Dict[str, Any]] = []

    try:
        with _db_conn() as conn, conn.cursor() as cur:
            for idx, ev in enumerate(events):
                if not isinstance(ev, dict):
                    rejected.append({"idx": idx, "reason": "not an object"})
                    continue

                kind = (ev.get("kind") or "").strip()
                if kind not in VALID_KINDS:
                    rejected.append({"idx": idx, "reason": f"invalid kind: {kind}"})
                    continue

                # Parse ts (optional). Server stamps NOW if absent/invalid.
                ts_raw = ev.get("ts")
                ts_val: Optional[datetime] = None
                if isinstance(ts_raw, str):
                    try:
                        ts_val = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                    except Exception:
                        ts_val = None

                target_id = _truncate(ev.get("target_id"), 256)
                target_tag = _truncate(ev.get("target_tag"), 32)
                target_text = _truncate(ev.get("target_text"), 200)
                page_url = _truncate(ev.get("page_url"), 1024)

                # JSON-serialize attrs + extra with payload guard.
                try:
                    attrs_blob = (
                        _json.dumps(ev.get("target_attrs"))
                        if ev.get("target_attrs") is not None else None
                    )
                    extra_blob = (
                        _json.dumps(ev.get("extra"))
                        if ev.get("extra") is not None else None
                    )
                except Exception:
                    rejected.append({"idx": idx, "reason": "target_attrs or extra not JSON-serializable"})
                    continue

                total_bytes = (len(attrs_blob or "") + len(extra_blob or ""))
                if total_bytes > MAX_ROW_PAYLOAD_BYTES:
                    rejected.append({"idx": idx, "reason": f"payload exceeds {MAX_ROW_PAYLOAD_BYTES} bytes"})
                    continue

                try:
                    cur.execute(
                        """
                        INSERT INTO ui_event_log (
                            ts, user_id, client_id, host_label, kind,
                            target_id, target_tag, target_text,
                            target_attrs, page_url, extra
                        ) VALUES (
                            COALESCE(%s, NOW()), %s, %s::uuid, %s, %s,
                            %s, %s, %s,
                            %s::jsonb, %s, %s::jsonb
                        )
                        """,
                        (
                            ts_val, actor_user_id, device_token, host_label, kind,
                            target_id, target_tag, target_text,
                            attrs_blob, page_url, extra_blob,
                        ),
                    )
                    accepted += 1
                except Exception as e:
                    logger.warning("ui-event/batch: row %d insert failed: %s", idx, e)
                    rejected.append({"idx": idx, "reason": str(e)})
    except Exception as e:
        logger.exception("ui-event/batch: DB error")
        return jsonify({"error": f"db error: {e}"}), 500

    if rejected:
        return jsonify({"accepted": accepted, "rejected": rejected}), 207
    return jsonify({"accepted": accepted}), 200


# ---------------------------------------------------------------------------
# GET /api/ui-event/log
# ---------------------------------------------------------------------------

@ui_event_bp.route("/api/ui-event/log", methods=["GET"])
@login_required
def api_ui_event_log():
    """
    Read the UI event log. Admin-only.

    Query params (all optional):
        from         ISO timestamp, default NOW() - 24h
        to           ISO timestamp, default NOW()
        kind         one of VALID_KINDS
        target_id    exact match
        client_id    UUID
        user_id      integer
        host_label   substring match
        q            free-text search on target_text, target_id, page_url,
                     target_attrs::text, extra::text
        limit        default 100, max 1000
        offset       default 0

    Response shape mirrors /api/audit/log so the frontend can render
    both with a single normalizing function.
    """
    if not _is_admin():
        return jsonify({"error": "admin only"}), 403

    end_default = datetime.now(timezone.utc)
    start_default = end_default - timedelta(hours=24)

    def _parse_ts(arg: str, default: datetime) -> datetime:
        s = request.args.get(arg)
        if not s:
            return default
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return default

    from_ts = _parse_ts("from", start_default)
    to_ts = _parse_ts("to", end_default)

    try:
        limit = max(1, min(MAX_LIMIT, int(request.args.get("limit", DEFAULT_LIMIT))))
    except (ValueError, TypeError):
        limit = DEFAULT_LIMIT
    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except (ValueError, TypeError):
        offset = 0

    kind = (request.args.get("kind") or "").strip()
    target_id = (request.args.get("target_id") or "").strip()
    client_id = (request.args.get("client_id") or "").strip()
    host_label = (request.args.get("host_label") or "").strip()
    try:
        user_id = int(request.args.get("user_id")) if request.args.get("user_id") else None
    except (ValueError, TypeError):
        user_id = None
    q = (request.args.get("q") or "").strip()

    where = ["ts BETWEEN %s AND %s"]
    params: List[Any] = [from_ts, to_ts]

    if kind:
        if kind not in VALID_KINDS:
            return jsonify({"error": f"invalid kind filter: {kind}"}), 400
        where.append("kind = %s")
        params.append(kind)
    if target_id:
        where.append("target_id = %s")
        params.append(target_id)
    if client_id:
        where.append("client_id = %s::uuid")
        params.append(client_id)
    if user_id is not None:
        where.append("user_id = %s")
        params.append(user_id)
    if host_label:
        where.append("host_label ILIKE %s")
        params.append(f"%{host_label}%")
    if q:
        where.append(
            "(target_text ILIKE %s OR target_id ILIKE %s "
            "OR page_url ILIKE %s OR target_attrs::text ILIKE %s "
            "OR extra::text ILIKE %s)"
        )
        pat = f"%{q}%"
        params.extend([pat, pat, pat, pat, pat])

    where_sql = " AND ".join(where)

    try:
        with _db_conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(f"SELECT COUNT(*) AS n FROM ui_event_log WHERE {where_sql}", params)
            total = cur.fetchone()["n"]

            cur.execute(
                f"""
                SELECT id, ts, user_id, client_id, host_label, kind,
                       target_id, target_tag, target_text, target_attrs,
                       page_url, extra
                  FROM ui_event_log
                 WHERE {where_sql}
                 ORDER BY ts DESC, id DESC
                 LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = cur.fetchall()
    except Exception as e:
        logger.exception("ui-event/log: query failed")
        return jsonify({"error": f"db error: {e}"}), 500

    # JSONify datetimes / UUIDs.
    for r in rows:
        if r.get("ts"):
            r["ts"] = r["ts"].isoformat()
        if r.get("client_id"):
            r["client_id"] = str(r["client_id"])

    return jsonify({"rows": rows, "total": total, "limit": limit, "offset": offset})


# ---------------------------------------------------------------------------
# DELETE /api/ui-event/keystrokes
# ---------------------------------------------------------------------------

@ui_event_bp.route("/api/ui-event/keystrokes", methods=["DELETE"])
@csrf_exempt
@login_required
def api_ui_event_delete_keystrokes():
    """
    Bulk-delete keystroke-class rows (kind IN keystroke, focus, blur).
    Admin-only. The click / submit / navigation / modal_* trail is NOT
    affected — that's the explicit operator design intent: erase typed
    content (a privacy mitigation) while preserving the click-trail
    record for accountability.

    Response: {"deleted": N}
    """
    if not _is_admin():
        return jsonify({"error": "admin only"}), 403

    try:
        with _db_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM ui_event_log WHERE kind = ANY(%s)",
                (list(KEYSTROKE_CLASS_KINDS),),
            )
            deleted = cur.rowcount
    except Exception as e:
        logger.exception("ui-event/keystrokes: delete failed")
        return jsonify({"error": f"db error: {e}"}), 500

    logger.info(
        "ui-event/keystrokes: %d rows deleted by user_id=%s",
        deleted, getattr(current_user, "id", None),
    )
    return jsonify({"deleted": deleted}), 200
