"""
routes/power.py — Flask Blueprint for camera power-management routes.

Covers two distinct power-control subsystems:

    1. Hubitat smart-plug power control
       - List switch devices available on the Hubitat hub
       - Get / set the power-supply configuration for any camera
       - Trigger and poll a Hubitat-backed power cycle

    2. UniFi PoE switch power control
       - List UniFi switches and their ports
       - Get / set the PoE port assignment for any camera
       - Trigger and poll a PoE-backed power cycle

All service singletons are accessed via routes.shared to avoid circular
imports with app.py.  CSRF exemption is applied through the standalone
csrf_exempt() helper from routes.helpers rather than the CSRFProtect
instance directly.
"""

import logging

from flask import Blueprint, jsonify, request
from flask_login import login_required

import routes.shared as shared
from routes.helpers import csrf_exempt

logger = logging.getLogger(__name__)

power_bp = Blueprint('power', __name__)


########################################################
#           HUBITAT POWER API ROUTES
########################################################

@power_bp.route('/api/hubitat/devices/switch', methods=['GET'])
@csrf_exempt
@login_required
def api_hubitat_switch_devices():
    """
    Get all Hubitat devices with Switch capability.

    Used by device picker UI to show available smart plugs for camera power control.

    Returns:
        JSON array of device objects with id, label, capabilities
    """
    if not shared.hubitat_power_service or not shared.hubitat_power_service.is_enabled():
        return jsonify({
            'success': False,
            'error': 'Hubitat Power Service not configured'
        }), 503

    devices = shared.hubitat_power_service.get_switch_devices()
    return jsonify(devices)


@power_bp.route('/api/cameras/<camera_serial>/power_supply', methods=['GET', 'POST'])
@csrf_exempt
@login_required
def api_camera_power_supply(camera_serial):
    """
    Get or set power supply settings for a camera.

    GET: Returns current power_supply, power_supply_device_id, and power_cycle_on_failure settings
    POST: Updates power_supply, power_supply_device_id, and/or power_cycle_on_failure from JSON body:
          {
            power_supply: "hubitat",
            device_id: 123,
            power_cycle_on_failure: {enabled: true, cooldown_hours: 24}
          }
    """
    camera = shared.camera_repo.get_camera(camera_serial)
    if not camera:
        return jsonify({'success': False, 'error': 'Camera not found'}), 404

    if request.method == 'GET':
        return jsonify({
            'camera_serial': camera_serial,
            'power_supply': camera.get('power_supply'),
            'power_supply_device_id': camera.get('power_supply_device_id'),
            'power_supply_types': shared.hubitat_power_service.get_power_supply_types() if shared.hubitat_power_service else ['hubitat', 'poe', 'none'],
            'power_cycle_on_failure': camera.get('power_cycle_on_failure', {
                'enabled': False,
                'cooldown_hours': 24,
                '_note': 'If true, camera will be auto power-cycled when OFFLINE. Max once per cooldown_hours.'
            })
        })

    # POST - update settings
    data = request.get_json() or {}

    # Update power_supply type if provided
    power_supply = data.get('power_supply')
    if power_supply is not None:
        if shared.hubitat_power_service:
            success = shared.hubitat_power_service.set_camera_power_supply(camera_serial, power_supply)
            if not success:
                return jsonify({
                    'success': False,
                    'error': f'Invalid power_supply type: {power_supply}'
                }), 400
        else:
            # No service, update directly
            shared.camera_repo.update_camera_setting(camera_serial, 'power_supply', power_supply)

    # Update device_id if provided
    device_id = data.get('device_id')
    if device_id is not None:
        if shared.hubitat_power_service:
            success = shared.hubitat_power_service.set_camera_device(camera_serial, str(device_id))
            if not success:
                return jsonify({
                    'success': False,
                    'error': 'Failed to update device ID'
                }), 500
        else:
            shared.camera_repo.update_camera_setting(camera_serial, 'power_supply_device_id', int(device_id))

    # Update power_cycle_on_failure settings if provided
    power_cycle_config = data.get('power_cycle_on_failure')
    if power_cycle_config is not None:
        # Merge with existing settings to preserve _note
        existing_config = camera.get('power_cycle_on_failure', {})
        updated_config = {
            'enabled': power_cycle_config.get('enabled', existing_config.get('enabled', False)),
            'cooldown_hours': power_cycle_config.get('cooldown_hours', existing_config.get('cooldown_hours', 24)),
            '_note': existing_config.get('_note', 'If true, camera will be auto power-cycled when OFFLINE. Max once per cooldown_hours.')
        }
        shared.camera_repo.update_camera_setting(camera_serial, 'power_cycle_on_failure', updated_config)

    # Return updated settings
    camera = shared.camera_repo.get_camera(camera_serial)
    return jsonify({
        'success': True,
        'power_supply': camera.get('power_supply'),
        'power_supply_device_id': camera.get('power_supply_device_id'),
        'power_cycle_on_failure': camera.get('power_cycle_on_failure')
    })


@power_bp.route('/api/power/<camera_serial>/cycle', methods=['POST'])
@csrf_exempt
@login_required
def api_power_cycle(camera_serial):
    """
    Trigger power cycle for a camera via its Hubitat smart plug.

    Turns off the smart plug, waits 10 seconds, then turns it back on.
    Requires camera to have power_supply='hubitat' and hubitat_device_id set.
    """
    if not shared.hubitat_power_service:
        return jsonify({
            'success': False,
            'error': 'Hubitat Power Service not available'
        }), 503

    result = shared.hubitat_power_service.power_cycle(camera_serial)

    if result.get('success'):
        return jsonify(result)
    else:
        return jsonify(result), 400


@power_bp.route('/api/power/<camera_serial>/status', methods=['GET'])
@csrf_exempt
@login_required
def api_power_status(camera_serial):
    """
    Get power cycle status for a camera.

    Returns current state (idle, powering_off, powering_on, complete, failed)
    and related timestamps.
    """
    if not shared.hubitat_power_service:
        return jsonify({
            'success': False,
            'error': 'Hubitat Power Service not available'
        }), 503

    status = shared.hubitat_power_service.get_power_status(camera_serial)
    return jsonify(status)


@power_bp.route('/api/hubitat/cameras', methods=['GET'])
@csrf_exempt
@login_required
def api_hubitat_cameras():
    """
    Get all cameras with power_supply='hubitat'.

    Returns list of camera configurations for cameras that can be
    power cycled via Hubitat smart plugs.
    """
    if not shared.hubitat_power_service:
        return jsonify({
            'success': False,
            'error': 'Hubitat Power Service not available'
        }), 503

    cameras = shared.hubitat_power_service.get_hubitat_cameras()
    return jsonify(cameras)


########################################################
#           UNIFI POE API ROUTES
########################################################

@power_bp.route('/api/unifi-poe/switches', methods=['GET'])
@csrf_exempt
@login_required
def api_unifi_poe_switches():
    """
    Get all UniFi switches from the controller.

    Returns list of switches with MAC address, name, model, and port count.
    Used for switch/port configuration UI.
    """
    if not shared.unifi_poe_service or not shared.unifi_poe_service.is_enabled():
        return jsonify({
            'success': False,
            'error': 'UniFi POE Service not available or not configured'
        }), 503

    switches = shared.unifi_poe_service.get_switches()
    return jsonify(switches)


@power_bp.route('/api/unifi-poe/switches/<switch_mac>/ports', methods=['GET'])
@csrf_exempt
@login_required
def api_unifi_poe_switch_ports(switch_mac):
    """
    Get all ports on a specific switch with POE status.

    Returns list of ports with port_idx, name, poe_mode, poe_power.
    Used for selecting which port a camera is connected to.
    """
    if not shared.unifi_poe_service or not shared.unifi_poe_service.is_enabled():
        return jsonify({
            'success': False,
            'error': 'UniFi POE Service not available or not configured'
        }), 503

    ports = shared.unifi_poe_service.get_switch_ports(switch_mac)
    return jsonify(ports)


@power_bp.route('/api/cameras/<camera_serial>/poe_config', methods=['GET', 'POST'])
@csrf_exempt
@login_required
def api_camera_poe_config(camera_serial):
    """
    Get or set POE configuration for a camera.

    GET: Returns current poe_switch_mac and poe_port
    POST: Set poe_switch_mac and poe_port
          Body: {switch_mac: "aa:bb:cc:dd:ee:ff", port: 12}
    """
    camera = shared.camera_repo.get_camera(camera_serial)
    if not camera:
        return jsonify({'error': f'Camera not found: {camera_serial}'}), 404

    if request.method == 'GET':
        return jsonify({
            'camera_serial': camera_serial,
            'power_supply': camera.get('power_supply'),
            'poe_switch_mac': camera.get('poe_switch_mac'),
            'poe_port': camera.get('poe_port')
        })

    # POST - set POE config
    data = request.json
    switch_mac = data.get('switch_mac')
    port = data.get('port')

    if not switch_mac or port is None:
        return jsonify({
            'error': 'Missing switch_mac or port in request body'
        }), 400

    if shared.unifi_poe_service:
        success = shared.unifi_poe_service.set_camera_poe_config(
            camera_serial, switch_mac, int(port)
        )
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Failed to save config'}), 500
    else:
        # Fallback: directly update camera settings
        success1 = shared.camera_repo.update_camera_setting(
            camera_serial, 'poe_switch_mac', switch_mac
        )
        success2 = shared.camera_repo.update_camera_setting(
            camera_serial, 'poe_port', int(port)
        )
        if success1 and success2:
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Failed to save config'}), 500


@power_bp.route('/api/poe/<camera_serial>/cycle', methods=['POST'])
@csrf_exempt
@login_required
def api_poe_power_cycle(camera_serial):
    """
    Manually trigger POE power cycle for a camera.

    Requires camera to have power_supply='poe' and poe_switch_mac/poe_port set.
    """
    if not shared.unifi_poe_service:
        return jsonify({
            'success': False,
            'error': 'UniFi POE Service not available'
        }), 503

    result = shared.unifi_poe_service.power_cycle(camera_serial)
    if result.get('success'):
        return jsonify(result)
    else:
        return jsonify(result), 400


@power_bp.route('/api/poe/<camera_serial>/status', methods=['GET'])
@csrf_exempt
@login_required
def api_poe_power_status(camera_serial):
    """
    Get POE power cycle status for a camera.

    Returns current state (idle, cycling, complete, failed)
    and related timestamps.
    """
    if not shared.unifi_poe_service:
        return jsonify({
            'success': False,
            'error': 'UniFi POE Service not available'
        }), 503

    status = shared.unifi_poe_service.get_power_status(camera_serial)
    return jsonify(status)


@power_bp.route('/api/unifi-poe/cameras', methods=['GET'])
@csrf_exempt
@login_required
def api_poe_cameras():
    """
    Get all cameras with power_supply='poe'.

    Returns list of camera configurations for cameras that can be
    power cycled via UniFi POE switches.
    """
    if not shared.unifi_poe_service:
        return jsonify({
            'success': False,
            'error': 'UniFi POE Service not available'
        }), 503

    cameras = shared.unifi_poe_service.get_poe_cameras()
    return jsonify(cameras)
