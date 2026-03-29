#!/usr/bin/env python3
"""
routes/settings_routes.py — Consolidated settings blueprint.

All settings read/write goes through the unified Settings class.
Replaces scattered PostgREST and psycopg2 calls in camera.py and config.py.

Endpoint structure:
    GET/PUT  /api/settings/global/<key>           — nvr_settings table
    GET      /api/settings/global                  — all global settings
    GET/PUT  /api/settings/camera/<serial>         — per-camera settings
    PUT      /api/settings/camera/<serial>/bulk    — multi-field update
    GET/PUT  /api/settings/user/<serial>           — per-user camera preferences
"""

import logging
from flask import Blueprint, jsonify, request, session
from flask_login import login_required, current_user
from routes import shared

logger = logging.getLogger(__name__)

settings_bp = Blueprint('settings', __name__)


# =====================================================================
#  Global settings (nvr_settings table)
# =====================================================================

@settings_bp.route('/api/settings/global', methods=['GET'])
@login_required
def api_settings_global_all():
    """Get all global settings. Excludes sensitive keys."""
    settings = shared.settings
    if not settings:
        return jsonify({'error': 'Settings service not initialized'}), 500

    result = settings.get_all_globals(exclude_keys=['NVR_SECRET_KEY'])
    return jsonify(result)


@settings_bp.route('/api/settings/global/<key>', methods=['GET'])
@login_required
def api_settings_global_get(key):
    """Get a single global setting by key."""
    settings = shared.settings
    if not settings:
        return jsonify({'error': 'Settings service not initialized'}), 500

    value = settings.get_global(key)
    if value is None:
        return jsonify({'key': key, 'value': None, 'exists': False})
    return jsonify({'key': key, 'value': value, 'exists': True})


@settings_bp.route('/api/settings/global/<key>', methods=['PUT'])
@login_required
def api_settings_global_set(key):
    """Set a global setting. Body: {"value": "..."}"""
    settings = shared.settings
    if not settings:
        return jsonify({'error': 'Settings service not initialized'}), 500

    data = request.get_json()
    if not data or 'value' not in data:
        return jsonify({'error': 'Missing "value" in request body'}), 400

    value = data['value']

    # Handle null/empty as delete (set to empty string)
    if value is None:
        value = ''

    success = settings.set_global(key, str(value))

    # Special handling: invalidate streaming hub cache when global hub changes
    if key == 'streaming_hub_global' and success:
        try:
            from services.streaming_hub import invalidate_global_hub_cache
            invalidate_global_hub_cache()
        except ImportError:
            pass

    if success:
        logger.info(f"[Settings] Global setting updated: {key}")
        return jsonify({'success': True, 'key': key, 'value': value})
    else:
        return jsonify({'error': f'Failed to update setting: {key}'}), 500


# =====================================================================
#  Per-camera settings (cameras table)
# =====================================================================

@settings_bp.route('/api/settings/camera/<serial>', methods=['GET'])
@login_required
def api_settings_camera_get(serial):
    """Get all settings for a camera."""
    camera = shared.camera_repo.get_camera(serial)
    if not camera:
        return jsonify({'error': f'Camera not found: {serial}'}), 404
    return jsonify(camera)


@settings_bp.route('/api/settings/camera/<serial>/<key>', methods=['GET'])
@login_required
def api_settings_camera_key_get(serial, key):
    """Get a single camera setting."""
    settings = shared.settings
    if not settings:
        return jsonify({'error': 'Settings service not initialized'}), 500

    value = settings.get_camera_setting(serial, key)
    return jsonify({'serial': serial, 'key': key, 'value': value})


@settings_bp.route('/api/settings/camera/<serial>/<key>', methods=['PUT'])
@login_required
def api_settings_camera_key_set(serial, key):
    """Set a single camera setting. Body: {"value": ...}"""
    settings = shared.settings
    if not settings:
        return jsonify({'error': 'Settings service not initialized'}), 500

    data = request.get_json()
    if not data or 'value' not in data:
        return jsonify({'error': 'Missing "value" in request body'}), 400

    # Reject immutable keys
    if key in ('serial', 'camera_id', 'id'):
        return jsonify({'error': f'Cannot modify immutable key: {key}'}), 400

    success = settings.set_camera(serial, key, data['value'])

    # Update in-memory cache
    if success:
        camera = shared.camera_repo.get_camera(serial)
        if camera:
            camera[key] = data['value']

    if success:
        return jsonify({'success': True, 'serial': serial, 'key': key})
    else:
        return jsonify({'error': f'Failed to update {serial}.{key}'}), 500


@settings_bp.route('/api/settings/camera/<serial>/bulk', methods=['PUT'])
@login_required
def api_settings_camera_bulk(serial):
    """
    Update multiple camera settings at once.
    Body: {"key1": value1, "key2": value2, ...}
    """
    settings = shared.settings
    if not settings:
        return jsonify({'error': 'Settings service not initialized'}), 500

    data = request.get_json()
    if not data or not isinstance(data, dict):
        return jsonify({'error': 'Request body must be a JSON object'}), 400

    # Reject immutable keys
    immutable = {'serial', 'camera_id', 'id'}
    blocked = set(data.keys()) & immutable
    if blocked:
        return jsonify({'error': f'Cannot modify immutable keys: {", ".join(blocked)}'}), 400

    success = settings.set_camera_bulk(serial, data)

    # Update in-memory cache
    if success:
        camera = shared.camera_repo.get_camera(serial)
        if camera:
            for k, v in data.items():
                camera[k] = v

    if success:
        return jsonify({'success': True, 'serial': serial, 'updated': list(data.keys())})
    else:
        return jsonify({'error': 'Failed to update camera settings'}), 500


# =====================================================================
#  Per-user preferences (user_camera_preferences table)
# =====================================================================

@settings_bp.route('/api/settings/user/preferences', methods=['GET'])
@login_required
def api_settings_user_all():
    """Get all preferences for the current user."""
    settings = shared.settings
    if not settings:
        return jsonify({'error': 'Settings service not initialized'}), 500

    prefs = settings.get_user_preference(current_user.id)
    return jsonify(prefs or [])


@settings_bp.route('/api/settings/user/<serial>/<key>', methods=['GET'])
@login_required
def api_settings_user_get(serial, key):
    """Get a specific user preference for a camera."""
    settings = shared.settings
    if not settings:
        return jsonify({'error': 'Settings service not initialized'}), 500

    value = settings.get_user_preference(current_user.id, serial, key)
    return jsonify({'serial': serial, 'key': key, 'value': value})


@settings_bp.route('/api/settings/user/<serial>/<key>', methods=['PUT'])
@login_required
def api_settings_user_set(serial, key):
    """Set a user preference for a camera. Body: {"value": ...}"""
    settings = shared.settings
    if not settings:
        return jsonify({'error': 'Settings service not initialized'}), 500

    data = request.get_json()
    if not data or 'value' not in data:
        return jsonify({'error': 'Missing "value" in request body'}), 400

    success = settings.set_user_preference(
        current_user.id, serial, key, data['value']
    )

    if success:
        return jsonify({'success': True, 'serial': serial, 'key': key})
    else:
        return jsonify({'error': f'Failed to update preference'}), 500
