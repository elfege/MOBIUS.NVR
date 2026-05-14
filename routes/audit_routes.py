"""
routes/audit_routes.py — settings audit log endpoints.

Two endpoints:

  POST /api/audit/batch  — browser-side audit outbox flush. Frontend
                            ES6 module `static/js/services/audit-outbox.js`
                            queues UI-only mutations (localStorage toggles,
                            grid size, fit mode, …) and flushes them here
                            in batches every 60s. The trigger-based audit
                            covers server-side mutations automatically;
                            this endpoint covers the UI-only path.

  GET  /api/audit/log    — read endpoint for the Logs UI tab. Admin only.
                            Supports time range, scope, origin, and
                            free-text search filters with pagination.

Trigger atomicity note: an INSERT into setting_audit_log via this
endpoint ALSO fires `pg_notify('setting_changed', ...)` only if the
table itself has the trigger attached. Migration 036 didn't attach one
to setting_audit_log (no infinite loop risk, just incidental).
The browser-originated audit rows DON'T trigger SocketIO broadcast for
that reason — operator-initiated UI changes don't need server-push
back to other browsers. If desired later, an INSERT trigger on
setting_audit_log can be added.
"""

from __future__ import annotations

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

audit_bp = Blueprint("audit", __name__)


# ---------------------------------------------------------------------------
# Limits / configuration
# ---------------------------------------------------------------------------

# Per-batch row cap. Frontend outbox should never exceed this; defense in depth.
MAX_BATCH_ROWS = 500

# Per-row payload cap (after JSON encoding). Prevents abuse from a misbehaving
# client that tries to stuff a 1MB blob into old_value/new_value.
MAX_ROW_PAYLOAD_BYTES = 32 * 1024

# Read endpoint pagination
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000

# Valid origin values — must match the CHECK constraint in migration 036.
VALID_ORIGINS = frozenset({"ui", "api", "system_auto", "trigger"})


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
    """Mirrors the admin check used by other admin-only endpoints."""
    try:
        return current_user.is_authenticated and getattr(current_user, "role", "") == "admin"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# POST /api/audit/batch
# ---------------------------------------------------------------------------

@audit_bp.route("/api/audit/batch", methods=["POST"])
@csrf_exempt
@login_required
def api_audit_batch():
    """
    Accept a batch of browser-side audit events.

    Body:
        {
          "events": [
            {
              "ts":          "2026-05-13T15:42:11.000Z",   (ISO8601, optional;
                                                            server stamps NOW
                                                            if absent or invalid)
              "scope":       "global" | "camera:<serial>" | "host:<label>" | "user:<id>",
              "setting_key": "nvr_light_grid",
              "old_value":   <any JSON>,
              "new_value":   <any JSON>,
              "origin":      "ui",
              "note":        "..."  (optional)
            },
            ...
          ]
        }

    Auth: @login_required (session). `client_id` is taken from the
    device_token cookie. We do NOT trust client-supplied `client_id` or
    `user_id` for security reasons — the server stamps them from the
    request context.

    Response:
        200 {"accepted": N}
        207 {"accepted": N, "rejected": [...]}  partial — bad rows skipped
        400 {"error": "..."}                    body invalid or empty

    Returns 200 even when most rows are rejected as long as at least one
    succeeded — the outbox can drop the row from its retry queue.
    """
    body = request.get_json(silent=True) or {}
    events = body.get("events")
    if not isinstance(events, list) or not events:
        return jsonify({"error": "events must be a non-empty array"}), 400
    if len(events) > MAX_BATCH_ROWS:
        return jsonify({"error": f"batch exceeds {MAX_BATCH_ROWS} rows"}), 400

    device_token = request.cookies.get("device_token")
    actor_user_id = current_user.id if current_user.is_authenticated else None

    accepted = 0
    rejected: List[Dict[str, Any]] = []

    try:
        with _db_conn() as conn, conn.cursor() as cur:
            for idx, ev in enumerate(events):
                # Validation. Bad rows go into `rejected` so the client
                # can stop retrying them, but a single bad row doesn't
                # abort the whole batch.
                if not isinstance(ev, dict):
                    rejected.append({"idx": idx, "reason": "not an object"})
                    continue
                scope = (ev.get("scope") or "").strip()
                setting_key = (ev.get("setting_key") or "").strip()
                origin = (ev.get("origin") or "ui").strip()
                if not scope or not setting_key:
                    rejected.append({"idx": idx, "reason": "missing scope or setting_key"})
                    continue
                if origin not in VALID_ORIGINS:
                    rejected.append({"idx": idx, "reason": f"invalid origin: {origin}"})
                    continue

                # Use client-supplied ts if it's a parseable ISO string.
                # Otherwise server stamps NOW(). Defends against clock skew
                # but preserves the operator's intended chronological order.
                ts_raw = ev.get("ts")
                ts_val: Optional[datetime] = None
                if isinstance(ts_raw, str):
                    try:
                        ts_val = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                    except Exception:
                        ts_val = None

                # Payload size guard.
                import json as _json
                try:
                    old_blob = _json.dumps(ev.get("old_value")) if ev.get("old_value") is not None else None
                    new_blob = _json.dumps(ev.get("new_value")) if ev.get("new_value") is not None else None
                except Exception:
                    rejected.append({"idx": idx, "reason": "value not JSON-serializable"})
                    continue
                total_bytes = (len(old_blob or "") + len(new_blob or ""))
                if total_bytes > MAX_ROW_PAYLOAD_BYTES:
                    rejected.append({"idx": idx, "reason": f"payload exceeds {MAX_ROW_PAYLOAD_BYTES} bytes"})
                    continue

                # Map scope → table_name + row_pk for consistent querying
                # alongside the trigger-emitted rows. scope='camera:<x>'
                # becomes table_name='cameras', row_pk='<x>'; scope='global'
                # becomes table_name='_local', row_pk=NULL.
                table_name, row_pk = _scope_to_table_pk(scope)

                try:
                    cur.execute(
                        """
                        INSERT INTO setting_audit_log (
                            ts, user_id, client_id, origin, table_name,
                            row_pk, setting_key, old_value, new_value, note
                        ) VALUES (
                            COALESCE(%s, NOW()), %s, %s::uuid, %s, %s,
                            %s, %s, %s::jsonb, %s::jsonb, %s
                        )
                        """,
                        (
                            ts_val, actor_user_id, device_token,
                            origin, table_name, row_pk, setting_key,
                            old_blob, new_blob, ev.get("note"),
                        ),
                    )
                    accepted += 1
                except Exception as e:
                    logger.warning("audit/batch: row %d insert failed: %s", idx, e)
                    rejected.append({"idx": idx, "reason": str(e)})
    except Exception as e:
        logger.exception("audit/batch: DB error")
        return jsonify({"error": f"db error: {e}"}), 500

    if rejected:
        return jsonify({"accepted": accepted, "rejected": rejected}), 207
    return jsonify({"accepted": accepted}), 200


def _scope_to_table_pk(scope: str) -> tuple:
    """
    Map a frontend scope string to (table_name, row_pk) for the audit row.

    Examples:
      'global'         -> ('_local', None)
      'camera:T8416…'  -> ('cameras', 'T8416…')
      'host:rog'       -> ('host_settings', 'rog')
      'user:7'         -> ('users', '7')

    Falls back to ('_local', None) for unknown scopes so the row still
    lands and is queryable.
    """
    if scope == "global" or not scope:
        return ("_local", None)
    if ":" not in scope:
        return ("_local", scope)
    kind, _, ident = scope.partition(":")
    kind = kind.strip().lower()
    ident = ident.strip()
    if kind == "camera":
        return ("cameras", ident or None)
    if kind == "host":
        return ("host_settings", ident or None)
    if kind == "user":
        return ("users", ident or None)
    if kind == "device":
        return ("trusted_devices", ident or None)
    return ("_local", ident or None)


# ---------------------------------------------------------------------------
# GET /api/audit/log
# ---------------------------------------------------------------------------

@audit_bp.route("/api/audit/log", methods=["GET"])
@login_required
def api_audit_log():
    """
    Read the audit log. Admin-only.

    Query params (all optional):
        from         ISO timestamp, default NOW() - 24h
        to           ISO timestamp, default NOW()
        scope        substring match on table_name OR exact match on
                     "table:pk" (where "table" is the actual table name)
        origin       one of VALID_ORIGINS
        client_id    UUID
        user_id      integer
        q            free-text search on setting_key + note + JSON values
        limit        default 100, max 1000
        offset       default 0

    Response:
        {
          "rows": [{id, ts, user_id, client_id, origin, table_name,
                    row_pk, setting_key, old_value, new_value, note}, ...],
          "total": <int>,     -- count matching the filter, ignoring limit/offset
          "limit": <int>,
          "offset": <int>
        }
    """
    if not _is_admin():
        return jsonify({"error": "admin only"}), 403

    # Defaults
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

    scope = (request.args.get("scope") or "").strip()
    origin = (request.args.get("origin") or "").strip()
    client_id = (request.args.get("client_id") or "").strip()
    try:
        user_id = int(request.args.get("user_id")) if request.args.get("user_id") else None
    except (ValueError, TypeError):
        user_id = None
    q = (request.args.get("q") or "").strip()

    where = ["ts BETWEEN %s AND %s"]
    params: List[Any] = [from_ts, to_ts]

    if scope:
        if ":" in scope:
            tbl, _, pk = scope.partition(":")
            where.append("table_name = %s AND row_pk = %s")
            params.extend([tbl, pk])
        else:
            where.append("table_name = %s")
            params.append(scope)
    if origin:
        if origin not in VALID_ORIGINS:
            return jsonify({"error": f"invalid origin filter: {origin}"}), 400
        where.append("origin = %s")
        params.append(origin)
    if client_id:
        where.append("client_id = %s::uuid")
        params.append(client_id)
    if user_id is not None:
        where.append("user_id = %s")
        params.append(user_id)
    if q:
        where.append(
            "(setting_key ILIKE %s OR note ILIKE %s "
            "OR new_value::text ILIKE %s OR old_value::text ILIKE %s)"
        )
        pat = f"%{q}%"
        params.extend([pat, pat, pat, pat])

    where_sql = " AND ".join(where)

    try:
        with _db_conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(f"SELECT COUNT(*) AS n FROM setting_audit_log WHERE {where_sql}", params)
            total = cur.fetchone()["n"]

            cur.execute(
                f"""
                SELECT id, ts, user_id, client_id, origin, table_name,
                       row_pk, setting_key, old_value, new_value, note
                  FROM setting_audit_log
                 WHERE {where_sql}
                 ORDER BY ts DESC, id DESC
                 LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = cur.fetchall()
    except Exception as e:
        logger.exception("audit/log: query failed")
        return jsonify({"error": f"db error: {e}"}), 500

    # JSONify: convert datetimes / UUIDs to strings
    for r in rows:
        if r.get("ts"):
            r["ts"] = r["ts"].isoformat()
        if r.get("client_id"):
            r["client_id"] = str(r["client_id"])

    return jsonify({"rows": rows, "total": total, "limit": limit, "offset": offset})
