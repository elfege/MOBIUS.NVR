"""
routes/config.py — Flask Blueprint for core configuration and status routes.

Covers:
- Main UI routes: GET /, GET /streams, GET /reloading
- Health / license / trusted-network settings endpoints
- System status and camera list (/api/cameras GET all) endpoints

Camera detail, force-sync, data-source, and MediaMTX path routes live in
routes/camera.py to avoid duplicate endpoint registration.

All service singletons are accessed via routes.shared to avoid circular imports.
"""

import os
import logging

import psycopg2
import requests
from flask import Blueprint, render_template, jsonify, redirect, request
from flask_login import login_required, current_user

import routes.shared as shared
from routes.helpers import (
    csrf_exempt,
    _get_allowed_camera_serials,
    _filter_cameras,
    _ui_health_from_env,
    _is_trusted_network_enabled,
    _get_client_ip,
    _is_same_subnet,
    _trusted_network_cache,
)
from services.license_service import license, validate_license

logger = logging.getLogger(__name__)

config_bp = Blueprint('config', __name__)


# ===== Main UI Routes =====

@config_bp.route('/')
@login_required
def index():
    """Redirect to streams page (main interface)"""
    return redirect('/streams')


@config_bp.route('/streams')
@login_required
def streams_page():
    """Multi-stream viewing page"""
    try:
        cameras = shared.camera_repo.get_streaming_cameras(include_hidden=True)
        ui_health = _ui_health_from_env()

        # Filter cameras based on user's access permissions
        allowed = _get_allowed_camera_serials(current_user)
        cameras = _filter_cameras(cameras, allowed)

        # Apply user's saved tile display order from DB
        order_map = {}
        try:
            conn = psycopg2.connect(
                host=os.getenv('POSTGRES_HOST', 'postgres'),
                port=os.getenv('POSTGRES_PORT', '5432'),
                dbname=os.getenv('POSTGRES_DB', 'nvr'),
                user=os.getenv('POSTGRES_USER', 'nvr_api'),
                password=os.getenv('POSTGRES_PASSWORD', 'nvr_internal_db_key'),
                connect_timeout=3
            )
            cur = conn.cursor()
            cur.execute(
                "SELECT camera_serial, display_order "
                "FROM user_camera_preferences "
                "WHERE user_id = %s AND display_order IS NOT NULL",
                (current_user.id,)
            )
            order_map = {serial: pos for serial, pos in cur.fetchall()}
            cur.close()
            conn.close()
        except Exception as e:
            logger.warning(f"[streams_page] Could not load display order: {e}")

        # Sort by saved display_order if available, otherwise alphabetical by name
        # so tile positions are always stable across restarts.
        cameras = dict(sorted(
            cameras.items(),
            key=lambda item: (
                order_map.get(item[0], float('inf')),
                (item[1].get('name') or '').lower()
            )
        ))

        # Pass full camera configs (includes ui_health_monitor per camera)
        return render_template('streams.html', cameras=cameras, ui_health=ui_health)
    except Exception as e:
        print(f"Error loading streams page: {e}")
        return f"Error loading streams page: {e}", 500


@config_bp.route('/light')
@login_required
def streams_light_page():
    """
    Lightweight stream viewer for low-powered devices (Fire tablets, etc.).
    Snapshot-only, 4 cameras per page, no heavy JS/CSS, no health monitor.
    """
    try:
        cameras = shared.camera_repo.get_streaming_cameras(include_hidden=True)

        # Filter cameras based on user's access permissions
        allowed = _get_allowed_camera_serials(current_user)
        cameras = _filter_cameras(cameras, allowed)

        # Remove server-hidden cameras entirely (light mode = no admin debug)
        cameras = {s: c for s, c in cameras.items() if not c.get('hidden', False)}

        # Apply user's saved tile display order from DB
        order_map = {}
        try:
            conn = psycopg2.connect(
                host=os.getenv('POSTGRES_HOST', 'postgres'),
                port=os.getenv('POSTGRES_PORT', '5432'),
                dbname=os.getenv('POSTGRES_DB', 'nvr'),
                user=os.getenv('POSTGRES_USER', 'nvr_api'),
                password=os.getenv('POSTGRES_PASSWORD', 'nvr_internal_db_key'),
                connect_timeout=3
            )
            cur = conn.cursor()
            cur.execute(
                "SELECT camera_serial, display_order "
                "FROM user_camera_preferences "
                "WHERE user_id = %s AND display_order IS NOT NULL",
                (current_user.id,)
            )
            order_map = {serial: pos for serial, pos in cur.fetchall()}
            cur.close()
            conn.close()
        except Exception as e:
            logger.warning(f"[streams_light_page] Could not load display order: {e}")

        cameras = dict(sorted(
            cameras.items(),
            key=lambda item: (
                order_map.get(item[0], float('inf')),
                (item[1].get('name') or '').lower()
            )
        ))

        return render_template('streams_light.html', cameras=cameras)
    except Exception as e:
        logger.error(f"Error loading light streams page: {e}")
        return f"Error loading light streams page: {e}", 500


@config_bp.route('/reloading')
@login_required
def reloading_page():
    """Reconnection page shown when server is restarting"""
    return render_template('reloading.html')


# ===== Status Routes =====

@config_bp.route('/api/health')
def api_health():
    """
    Lightweight health check endpoint
    Returns shutdown status to warn clients before server goes down
    """
    if shared.app_state.is_shutting_down:
        return jsonify({
            'status': 'shutting_down',
            'message': 'Server is shutting down'
        }), 503

    return jsonify({
        'status': 'ok',
        'message': 'Server is healthy'
    }), 200


@config_bp.route('/api/license')
@login_required
def api_license_status():
    """
    Returns the current license state for the frontend.
    Used by the UI to show demo banners, camera limits, and watermark status.
    """
    return jsonify(license.to_dict()), 200


@config_bp.route('/api/settings/trusted-network', methods=['GET'])
@login_required
def api_trusted_network_get():
    """Get the current trusted network setting."""
    return jsonify({
        'enabled': _is_trusted_network_enabled(),
        'client_ip': _get_client_ip(),
        'on_same_subnet': _is_same_subnet(_get_client_ip())
    }), 200


@config_bp.route('/api/settings/trusted-network', methods=['PUT'])
@csrf_exempt
@login_required
def api_trusted_network_put():
    """Enable or disable trusted network auto-login. Admin only."""
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    data = request.get_json()
    enabled = str(data.get('enabled', False)).lower()

    success = shared.settings.set_global('TRUSTED_NETWORK_ENABLED', enabled)
    if success:
        _trusted_network_cache['enabled'] = enabled == 'true'
        _trusted_network_cache['checked_at'] = 0
        return jsonify({'success': True, 'enabled': enabled == 'true'}), 200
    return jsonify({'error': 'Failed to update trusted network setting'}), 500


@config_bp.route('/api/settings/streaming-hubs', methods=['GET'])
@login_required
def api_streaming_hubs_get():
    """Get per-camera streaming hub assignments — always fresh from DB."""
    cameras = shared.camera_repo.get_streaming_cameras(include_hidden=True)
    mediamtx_list = []
    go2rtc_list = []
    for serial, cam in cameras.items():
        entry = {'serial': serial, 'name': cam.get('name', serial)}
        hub = (cam.get('streaming_hub') or 'mediamtx').lower()
        if hub == 'go2rtc':
            go2rtc_list.append(entry)
        else:
            mediamtx_list.append(entry)
    mediamtx_list.sort(key=lambda c: c['name'].lower())
    go2rtc_list.sort(key=lambda c: c['name'].lower())
    return jsonify({'mediamtx': mediamtx_list, 'go2rtc': go2rtc_list}), 200


@config_bp.route('/api/settings/streaming-hubs', methods=['PUT'])
@csrf_exempt
@login_required
def api_streaming_hubs_put():
    """
    Bulk-update streaming hub for multiple cameras.

    Request body: {"cameras": {"SERIAL1": "go2rtc", "SERIAL2": "mediamtx", ...}}
    Each value must be 'go2rtc' or 'mediamtx'.
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    data = request.get_json() or {}
    assignments = data.get('cameras', {})
    if not assignments or not isinstance(assignments, dict):
        return jsonify({'error': 'Body must include "cameras": {"serial": "hub", ...}'}), 400

    updated = []
    errors = []
    for serial, hub in assignments.items():
        if hub not in ('go2rtc', 'mediamtx'):
            errors.append(f"{serial}: invalid hub '{hub}'")
            continue
        success = shared.camera_repo.update_camera_setting(serial, 'streaming_hub', hub)
        if success:
            updated.append(serial)
        else:
            errors.append(f"{serial}: update failed")

    logger.info(f"[StreamingHubs] Bulk update by user {current_user.id}: {len(updated)} updated, {len(errors)} errors")
    return jsonify({'updated': updated, 'errors': errors}), 200


@config_bp.route('/api/settings/advanced', methods=['GET'])
@login_required
def api_advanced_settings_get():
    """
    Return all nvr_settings rows (excluding secrets) as a JSON array.
    Used by the Advanced tab in the global settings modal.
    Returns: [ { key, value, updated_at }, ... ] ordered by key.
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    # Keys that must never be exposed via this endpoint
    SECRET_KEYS = {'NVR_SECRET_KEY'}

    try:
        conn = psycopg2.connect(
            host=os.getenv('POSTGRES_HOST', 'postgres'),
            port=os.getenv('POSTGRES_PORT', '5432'),
            dbname=os.getenv('POSTGRES_DB', 'nvr'),
            user=os.getenv('POSTGRES_USER', 'nvr_api'),
            password=os.getenv('POSTGRES_PASSWORD', 'nvr_internal_db_key'),
            connect_timeout=5
        )
        cur = conn.cursor()
        cur.execute("SELECT key, value, updated_at FROM nvr_settings ORDER BY key;")
        rows = cur.fetchall()
        cur.close()
        conn.close()

        result = [
            {'key': r[0], 'value': r[1], 'updated_at': r[2].isoformat() if r[2] else None}
            for r in rows if r[0] not in SECRET_KEYS
        ]
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"[AdvancedSettings] GET failed: {e}")
        return jsonify({'error': str(e)}), 500


@config_bp.route('/api/settings/advanced/<path:key>', methods=['PATCH'])
@csrf_exempt
@login_required
def api_advanced_settings_patch(key):
    """
    Update a single nvr_settings row by key. Admin only.
    Body: { "value": "<new value>" }
    Rejects attempts to modify secret keys via this endpoint.
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    SECRET_KEYS = {'NVR_SECRET_KEY'}
    if key in SECRET_KEYS:
        return jsonify({'error': 'Cannot modify this key via this endpoint'}), 403

    data = request.get_json() or {}
    value = data.get('value', '')

    try:
        conn = psycopg2.connect(
            host=os.getenv('POSTGRES_HOST', 'postgres'),
            port=os.getenv('POSTGRES_PORT', '5432'),
            dbname=os.getenv('POSTGRES_DB', 'nvr'),
            user=os.getenv('POSTGRES_USER', 'nvr_api'),
            password=os.getenv('POSTGRES_PASSWORD', 'nvr_internal_db_key'),
            connect_timeout=5
        )
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO nvr_settings (key, value, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW();
            """,
            (key, value)
        )
        cur.close()
        conn.close()

        logger.info(f"[AdvancedSettings] '{key}' set by user {current_user.id}")
        return jsonify({'success': True, 'key': key, 'value': value}), 200
    except Exception as e:
        logger.error(f"[AdvancedSettings] PATCH failed for '{key}': {e}")
        return jsonify({'error': str(e)}), 500


@config_bp.route('/api/status')
@login_required
def api_status():
    """Get system status"""
    eufy_bridge = shared.eufy_bridge
    unifi_cameras = shared.unifi_cameras

    eufy_status = {
        'bridge_configured': eufy_bridge is not None,
        'bridge_running': eufy_bridge.is_running() if eufy_bridge else False,
        'bridge_ready': eufy_bridge.is_ready() if eufy_bridge else False,
        'total_devices': shared.camera_repo.get_camera_count(),
        'ptz_cameras': len(shared.camera_repo.get_ptz_cameras())
    }

    unifi_status = {
        camera_id: {
            'name': camera.name,
            'session_active': camera.session_active,
            'type': 'unifi'
        }
        for camera_id, camera in unifi_cameras.items()
    }

    return jsonify({
        'eufy': eufy_status,
        'unifi': unifi_status,
        'streams': {
            'active': shared.stream_manager.get_active_streams(),
            'total_streaming_cameras': len(shared.camera_repo.get_streaming_cameras())
        }
    })


@config_bp.route('/api/cameras')
@login_required
def api_cameras():
    """Get list of available cameras, filtered by user access permissions"""
    allowed = _get_allowed_camera_serials(current_user)
    return jsonify({
        'all': _filter_cameras(shared.camera_repo.get_all_cameras(), allowed),
        'ptz': _filter_cameras(shared.camera_repo.get_ptz_cameras(), allowed),
        'streaming': _filter_cameras(shared.camera_repo.get_streaming_cameras(), allowed)
    })


# NOTE: /api/cameras/<camera_id>, /api/cameras/force-sync, /api/cameras/data-source,
# /api/mediamtx/path-status, and /api/mediamtx/create-path are registered in
# routes/camera.py to avoid duplicate endpoint errors.

