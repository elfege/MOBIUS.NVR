"""
Presence API Blueprint
======================
Flask Blueprint exposing all /api/presence/* endpoints.

Routes registered here:
    GET  /api/presence                          - Get presence status for all tracked people
    GET  /api/presence/<person_name>            - Get presence status for one person
    POST /api/presence/<person_name>/toggle     - Toggle presence status
    POST /api/presence/<person_name>/set        - Set presence status explicitly
    GET  /api/presence/devices                  - List Hubitat PresenceSensor devices
    POST /api/presence/<person_name>/device     - Associate a Hubitat device with a person

All routes require login (@login_required) and are CSRF-exempt because they are
called from JavaScript fetch() with JSON bodies rather than HTML forms.
"""

from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user  # noqa: F401  (current_user available for future use)
import routes.shared as shared
from routes.helpers import csrf_exempt


presence_bp = Blueprint('presence', __name__)


########################################################
#           PRESENCE API ROUTES
########################################################

@presence_bp.route('/api/presence', methods=['GET'])
@csrf_exempt
@login_required
def api_get_all_presence():
    """
    Get presence status for all tracked people.

    Returns:
        JSON array of presence objects with person_name, is_present,
        hubitat_device_id, last_changed_at, last_changed_by
    """
    if not shared.presence_service:
        return jsonify({
            'success': False,
            'error': 'Presence Service not available'
        }), 503

    statuses = shared.presence_service.get_all_presence()
    return jsonify([s.to_dict() for s in statuses])


@presence_bp.route('/api/presence/<person_name>', methods=['GET'])
@csrf_exempt
@login_required
def api_get_presence(person_name):
    """
    Get presence status for a specific person.

    Args:
        person_name: Name of the person

    Returns:
        JSON object with presence status or 404 if not found
    """
    if not shared.presence_service:
        return jsonify({
            'success': False,
            'error': 'Presence Service not available'
        }), 503

    status = shared.presence_service.get_presence(person_name)
    if status is None:
        return jsonify({
            'success': False,
            'error': f'Person not found: {person_name}'
        }), 404

    return jsonify(status.to_dict())


@presence_bp.route('/api/presence/<person_name>/toggle', methods=['POST'])
@csrf_exempt
@login_required
def api_toggle_presence(person_name):
    """
    Toggle presence status for a person.

    Args:
        person_name: Name of the person

    Returns:
        JSON object with new status or error
    """
    if not shared.presence_service:
        return jsonify({
            'success': False,
            'error': 'Presence Service not available'
        }), 503

    new_status = shared.presence_service.toggle_presence(person_name)
    if new_status is None:
        return jsonify({
            'success': False,
            'error': f'Failed to toggle presence for {person_name}'
        }), 500

    return jsonify({
        'success': True,
        'person_name': person_name,
        'is_present': new_status
    })


@presence_bp.route('/api/presence/<person_name>/set', methods=['POST'])
@csrf_exempt
@login_required
def api_set_presence(person_name):
    """
    Set presence status for a person.

    Args:
        person_name: Name of the person

    JSON Body:
        is_present: boolean - New presence status

    Returns:
        JSON object with result
    """
    if not shared.presence_service:
        return jsonify({
            'success': False,
            'error': 'Presence Service not available'
        }), 503

    data = request.get_json() or {}
    is_present = data.get('is_present')

    if is_present is None:
        return jsonify({
            'success': False,
            'error': 'is_present field required'
        }), 400

    success = shared.presence_service.set_presence(person_name, bool(is_present), source='api')
    if not success:
        return jsonify({
            'success': False,
            'error': f'Failed to set presence for {person_name}'
        }), 500

    return jsonify({
        'success': True,
        'person_name': person_name,
        'is_present': is_present
    })


@presence_bp.route('/api/presence/devices', methods=['GET'])
@csrf_exempt
@login_required
def api_get_presence_devices():
    """
    Get all Hubitat devices with PresenceSensor capability.

    Returns:
        JSON array of device objects with id, label, capabilities
    """
    if not shared.presence_service:
        return jsonify({
            'success': False,
            'error': 'Presence Service not available'
        }), 503

    devices = shared.presence_service.get_presence_devices()
    return jsonify(devices)


@presence_bp.route('/api/presence/<person_name>/device', methods=['POST'])
@csrf_exempt
@login_required
def api_set_presence_device(person_name):
    """
    Associate a Hubitat presence sensor with a person.

    Args:
        person_name: Name of the person

    JSON Body:
        device_id: string|null - Hubitat device ID (or null to remove)

    Returns:
        JSON object with result
    """
    if not shared.presence_service:
        return jsonify({
            'success': False,
            'error': 'Presence Service not available'
        }), 503

    data = request.get_json() or {}
    device_id = data.get('device_id')

    success = shared.presence_service.set_hubitat_device(person_name, device_id)
    if not success:
        return jsonify({
            'success': False,
            'error': f'Failed to set device for {person_name}'
        }), 500

    return jsonify({
        'success': True,
        'person_name': person_name,
        'device_id': device_id
    })
