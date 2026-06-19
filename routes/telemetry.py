"""
routes/telemetry.py — admin-only HTTP API for the per-layer telemetry event
log feature.

Endpoints:

    GET   /api/telemetry/settings  →  current config (enabled / cap / retention)
    POST  /api/telemetry/settings  →  persist config (admin only)
    GET   /api/telemetry/usage     →  table size + row count for the UI widget

All three require role='admin'. The pattern (`if current_user.role != 'admin':
return abort(403)`) mirrors routes/audit_routes.py:77, routes/storage.py:174,
routes/ui_event_routes.py:84.

Per the design doc in
docs/plans/per_layer_telemetry_event_log_for_localizing_long_uptime_streaming_entropy_with_bounded_postgres_retention.md
the feature is disabled by default — the GET endpoint will return
{enabled: false, ...} for every fresh install until an admin toggles it on
via the Data tab in the global settings modal.
"""

import logging

import psycopg2.extras
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user

from routes.helpers import csrf_exempt
from services import telemetry_settings as ts
from services import telemetry_cleanup
from services.db import cursor as db_cursor

logger = logging.getLogger(__name__)

telemetry_bp = Blueprint('telemetry', __name__)


def _require_admin():
    """Returns (ok, response_or_none). Same pattern as other admin endpoints."""
    if not current_user.is_authenticated:
        return False, (jsonify({'error': 'Authentication required'}), 401)
    if getattr(current_user, 'role', '') != 'admin':
        return False, (jsonify({'error': 'Admin access required'}), 403)
    return True, None


@telemetry_bp.route('/api/telemetry/settings', methods=['GET'])
@login_required
def api_telemetry_settings_get():
    """Read current telemetry config. Admin-only."""
    ok, err = _require_admin()
    if not ok:
        return err
    return jsonify({'success': True, 'settings': ts.snapshot()})


@telemetry_bp.route('/api/telemetry/settings', methods=['POST'])
@csrf_exempt
@login_required
def api_telemetry_settings_set():
    """
    Persist telemetry config. Admin-only.

    Body (any subset):
        {
          "enabled":         true|false,
          "max_size_mb":     int (10 .. 2048),
          "retention_days":  1 | 7 | 30
        }

    If max_size_mb is REDUCED, the cleanup tick runs immediately so the
    user sees the table shrink without waiting up to an hour.
    """
    ok, err = _require_admin()
    if not ok:
        return err

    data = request.get_json(silent=True) or {}
    prev_max = ts.max_size_mb()
    changes = {}

    if 'enabled' in data:
        new_enabled = bool(data['enabled'])
        if ts.set_enabled(new_enabled):
            changes['enabled'] = new_enabled
        else:
            return jsonify({'error': 'Failed to persist enabled flag'}), 500

    if 'max_size_mb' in data:
        try:
            new_max = int(data['max_size_mb'])
        except (TypeError, ValueError):
            return jsonify({'error': 'max_size_mb must be an integer'}), 400
        if new_max < ts.MIN_MAX_SIZE_MB or new_max > ts.MAX_MAX_SIZE_MB:
            return jsonify({
                'error': f'max_size_mb must be between {ts.MIN_MAX_SIZE_MB} and {ts.MAX_MAX_SIZE_MB}'
            }), 400
        if ts.set_max_size_mb(new_max):
            changes['max_size_mb'] = new_max
        else:
            return jsonify({'error': 'Failed to persist max_size_mb'}), 500

    if 'retention_days' in data:
        try:
            new_ret = int(data['retention_days'])
        except (TypeError, ValueError):
            return jsonify({'error': 'retention_days must be an integer'}), 400
        if new_ret not in ts.ALLOWED_RETENTION_DAYS:
            return jsonify({
                'error': f'retention_days must be one of {list(ts.ALLOWED_RETENTION_DAYS)}'
            }), 400
        if ts.set_retention_days(new_ret):
            changes['retention_days'] = new_ret
        else:
            return jsonify({'error': 'Failed to persist retention_days'}), 500

    # If the cap was reduced, run cleanup immediately so the operator sees
    # the table shrink. This is best-effort; failures here are logged but
    # don't fail the API call.
    if 'max_size_mb' in changes and changes['max_size_mb'] < prev_max:
        try:
            telemetry_cleanup.run_cleanup_once(reason='cap_reduced')
        except Exception:
            logger.exception("[telemetry] cleanup-on-cap-reduce raised")

    logger.info(f"[telemetry] settings updated by admin user={current_user.id}: {changes}")
    return jsonify({'success': True, 'changes': changes, 'settings': ts.snapshot()})


@telemetry_bp.route('/api/telemetry/usage', methods=['GET'])
@login_required
def api_telemetry_usage():
    """Current table-size and row-count snapshot for the Data tab widget."""
    ok, err = _require_admin()
    if not ok:
        return err

    size_bytes = telemetry_cleanup.table_size_bytes()
    cap_mb     = ts.max_size_mb()
    cap_bytes  = cap_mb * 1024 * 1024

    return jsonify({
        'success': True,
        'usage': {
            'size_bytes':   size_bytes,
            'size_mb':      round(size_bytes / (1024 * 1024), 2),
            'cap_mb':       cap_mb,
            'cap_bytes':    cap_bytes,
            'percent_used': round((size_bytes / cap_bytes) * 100, 1) if cap_bytes > 0 else 0,
            'row_count':    telemetry_cleanup.row_count(),
            'enabled':      ts.is_enabled(),
        }
    })


@telemetry_bp.route('/api/telemetry/recent', methods=['GET'])
@login_required
def api_telemetry_recent():
    """
    Paginated reader for the telemetry_events table. Admin-only.

    Query params (all optional):
        category       — filter to one category (e.g. 'rtsp_probe')
        camera_id      — filter to one camera
        severity       — info | warning | error
        since_minutes  — only events newer than N minutes (default 60)
        limit          — max rows returned (default 100, hard cap 1000)

    Returns events in DESC ts order. Payload is JSONB → returned verbatim.
    """
    ok, err = _require_admin()
    if not ok:
        return err

    category      = request.args.get('category')
    camera_id     = request.args.get('camera_id')
    severity      = request.args.get('severity')
    since_minutes = int(request.args.get('since_minutes', 60))
    limit         = min(int(request.args.get('limit', 100)), 1000)

    where  = ["ts > now() - (%s * INTERVAL '1 minute')"]
    params = [since_minutes]
    if category:
        where.append("category = %s")
        params.append(category)
    if camera_id:
        where.append("camera_id = %s")
        params.append(camera_id)
    if severity:
        where.append("severity = %s")
        params.append(severity)

    sql = (
        "SELECT id, ts, category, subcategory, camera_id, severity, payload "
        "FROM telemetry_events "
        "WHERE " + " AND ".join(where) + " "
        "ORDER BY ts DESC "
        "LIMIT %s"
    )
    params.append(limit)

    try:
        with db_cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        # Convert datetimes to isoformat for JSON.
        for row in rows:
            if row.get('ts'):
                row['ts'] = row['ts'].isoformat()
        return jsonify({'success': True, 'count': len(rows), 'events': rows})
    except Exception as e:
        logger.error(f"[telemetry] /recent query failed: {e}")
        return jsonify({'error': str(e)}), 500
