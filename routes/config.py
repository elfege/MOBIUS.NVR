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
        cameras = shared.camera_repo.get_streaming_cameras()
        ui_health = _ui_health_from_env()

        # Filter cameras based on user's access permissions
        allowed = _get_allowed_camera_serials(current_user)
        cameras = _filter_cameras(cameras, allowed)

        # Pass full camera configs (includes ui_health_monitor per camera)
        return render_template('streams.html', cameras=cameras, ui_health=ui_health)
    except Exception as e:
        print(f"Error loading streams page: {e}")
        return f"Error loading streams page: {e}", 500


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
        cur.execute("SELECT upsert_setting(%s, %s);", ('TRUSTED_NETWORK_ENABLED', enabled))
        cur.close()
        conn.close()

        # Invalidate cache
        _trusted_network_cache['enabled'] = enabled == 'true'
        _trusted_network_cache['checked_at'] = 0

        return jsonify({'success': True, 'enabled': enabled == 'true'}), 200
    except Exception as e:
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

