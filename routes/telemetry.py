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

from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user

from services import telemetry_settings as ts
from services import telemetry_cleanup

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
