"""
routes/ptz.py — Flask Blueprint for PTZ (Pan-Tilt-Zoom) routes.

Covers:
- PTZ movement: POST /api/ptz/<camera_serial>/<direction>
- Preset retrieval: GET /api/ptz/<camera_serial>/presets
- Goto preset: POST /api/ptz/<camera_serial>/preset/<preset_token>
- Save preset: POST /api/ptz/<camera_serial>/preset
- Delete preset: DELETE /api/ptz/<camera_serial>/preset/<preset_token>
- Client latency GET: GET /api/ptz/latency/<client_uuid>/<camera_serial>
- Client latency POST: POST /api/ptz/latency/<client_uuid>/<camera_serial>
- PTZ reversal GET: GET /api/ptz/<camera_serial>/reversal
- PTZ reversal POST: POST /api/ptz/<camera_serial>/reversal
- Camera reboot: POST /api/camera/<camera_serial>/reboot

All service singletons are accessed via routes.shared to avoid circular imports.
PTZ handler classes (BaichuanPTZHandler, ONVIFPTZHandler, amcrest_ptz_handler)
are imported directly — they have no circular-import risk with this blueprint.
"""

import logging

import requests
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user

import routes.shared as shared
from routes.helpers import csrf_exempt
from services.ptz.amcrest_ptz_handler import amcrest_ptz_handler
from services.onvif.onvif_ptz_handler import ONVIFPTZHandler
from services.ptz.baichuan_ptz_handler import BaichuanPTZHandler

logger = logging.getLogger(__name__)

ptz_bp = Blueprint('ptz', __name__)


# ---------------------------------------------------------------------------
# PTZ Movement
# ---------------------------------------------------------------------------

@ptz_bp.route('/api/ptz/<camera_serial>/<direction>', methods=['POST'])
@csrf_exempt
@login_required
def api_ptz_move(camera_serial, direction):
    """Execute PTZ movement with ONVIF priority"""
    import time as _time
    _ptz_start = _time.time()
    try:
        # Validate camera
        if not shared.ptz_validator.is_ptz_capable(camera_serial):
            return jsonify({'success': False, 'error': 'Invalid camera or no PTZ capability'}), 400

        # Get camera config
        camera = shared.camera_repo.get_camera(camera_serial)
        if not camera:
            return jsonify({'success': False, 'error': 'Camera not found'}), 404

        camera_type = camera.get('type')
        _ptz_setup_time = _time.time() - _ptz_start
        print(f"[PTZ] PTZ request for camera: {camera_serial}, type: {camera_type}, direction: {direction} (setup: {_ptz_setup_time*1000:.0f}ms)")

        success = False
        message = ""

        # Check if camera should use Baichuan protocol (Reolink without ONVIF or NEOLINK streams)
        # Exception: 'recalibrate' requires ONVIF GotoHomePosition, Baichuan doesn't support it
        use_baichuan = camera_type == 'reolink' and BaichuanPTZHandler.is_baichuan_capable(camera) and direction != 'recalibrate'

        if use_baichuan:
            # Use Baichuan for Reolink cameras without ONVIF or configured for Baichuan
            print(f"[PTZ] Using Baichuan PTZ for {camera_type} camera (NEOLINK/no-ONVIF)")
            success, message = BaichuanPTZHandler.move_camera(
                camera_serial=camera_serial,
                direction=direction,
                camera_config=camera
            )
        elif camera_type in ['amcrest', 'reolink', 'sv3c']:
            # Try ONVIF for Amcrest, Reolink (with ONVIF), and SV3C cameras
            _onvif_start = _time.time()
            print(f"[PTZ] Attempting ONVIF PTZ for {camera_type} camera")
            success, message = ONVIFPTZHandler.move_camera(
                camera_serial=camera_serial,
                direction=direction,
                camera_config=camera
            )
            _onvif_time = _time.time() - _onvif_start
            print(f"[PTZ] ONVIF PTZ completed in {_onvif_time*1000:.0f}ms (success={success})")

            # If ONVIF fails for Reolink, try Baichuan as fallback
            if not success and camera_type == 'reolink':
                print(f"[PTZ] ONVIF failed, falling back to Baichuan PTZ handler")
                success, message = BaichuanPTZHandler.move_camera(
                    camera_serial=camera_serial,
                    direction=direction,
                    camera_config=camera
                )

            # If ONVIF fails for Amcrest, fall back to CGI handler
            if not success and camera_type == 'amcrest':
                print(f"[PTZ] ONVIF failed, falling back to Amcrest CGI handler")
                success = amcrest_ptz_handler.move_camera(camera_serial, direction, shared.camera_repo)
                message = f'Camera moved {direction} via CGI' if success else 'Movement failed'

        # Eufy uses bridge (no ONVIF support)
        # move_camera() returns (success, message) and handles auto-restart internally
        elif camera_type == 'eufy':
            print(f"[EUFY PTZ] Request: camera={camera_serial}, direction={direction}")
            if not shared.eufy_bridge:
                return jsonify({'success': False, 'error': 'Eufy bridge not initialized'}), 503

            # move_camera handles is_running check, auto-restart, and retry internally
            bridge_status = shared.eufy_bridge.get_status()
            print(f"[EUFY PTZ] Bridge status: {bridge_status}")
            success, message = shared.eufy_bridge.move_camera(camera_serial, direction, shared.camera_repo)
            print(f"[EUFY PTZ] Result: success={success}, message={message}")

            if not success:
                # Return 503 with the detailed error from the bridge
                _total_time = _time.time() - _ptz_start
                print(f"[PTZ] PTZ request TOTAL: {_total_time*1000:.0f}ms")
                return jsonify({
                    'success': False,
                    'camera': camera_serial,
                    'direction': direction,
                    'error': message,
                    'bridge_status': bridge_status
                }), 503

        else:
            return jsonify({'success': False, 'error': f'PTZ not supported for camera type: {camera_type}'}), 400

        _total_time = _time.time() - _ptz_start
        print(f"[PTZ] PTZ request TOTAL: {_total_time*1000:.0f}ms")
        return jsonify({
            'success': success,
            'camera': camera_serial,
            'direction': direction,
            'message': message
        })

    except Exception as e:
        logger.error(f"PTZ API error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# PTZ Presets
# ---------------------------------------------------------------------------

@ptz_bp.route('/api/ptz/<camera_serial>/presets', methods=['GET'])
@login_required
def api_ptz_get_presets(camera_serial):
    """
    Get list of PTZ presets for camera.

    Presets are cached in PostgreSQL with 6-day TTL. Use ?refresh=true to
    bypass cache and fetch fresh data from ONVIF.

    Query params:
        refresh: Set to 'true' to bypass cache and query ONVIF directly
    """
    try:
        # Check for force refresh parameter
        force_refresh = request.args.get('refresh', 'false').lower() == 'true'

        # Validate camera
        camera = shared.camera_repo.get_camera(camera_serial)
        if not camera:
            return jsonify({'success': False, 'error': 'Camera not found'}), 404

        camera_type = camera.get('type')

        # Handle Eufy cameras via bridge
        if camera_type == 'eufy':
            if not shared.eufy_bridge or not shared.eufy_bridge.is_running():
                return jsonify({'success': False, 'error': 'Eufy bridge not running', 'presets': []}), 503

            # Eufy has 4 fixed preset slots
            presets = shared.eufy_bridge.get_presets(camera_serial)
            return jsonify({
                'success': True,
                'camera': camera_serial,
                'presets': presets,
                'cached': False,
                'method': 'eufy'
            })

        # Only Amcrest, Reolink, and SV3C support ONVIF/Baichuan presets
        if camera_type not in ['amcrest', 'reolink', 'sv3c']:
            return jsonify({'success': False, 'error': 'Presets not supported for this camera type'}), 400

        # Check if camera should use Baichuan protocol
        use_baichuan = camera_type == 'reolink' and BaichuanPTZHandler.is_baichuan_capable(camera)

        if use_baichuan:
            # Get presets via Baichuan (with caching)
            success, presets = BaichuanPTZHandler.get_presets(camera_serial, camera, force_refresh=force_refresh)
        else:
            # Get presets via ONVIF (with caching)
            success, presets = ONVIFPTZHandler.get_presets(camera_serial, camera, force_refresh=force_refresh)

        if not success:
            return jsonify({'success': False, 'error': 'Failed to retrieve presets', 'presets': []}), 500

        return jsonify({
            'success': True,
            'camera': camera_serial,
            'presets': presets,
            'cached': not force_refresh,  # Indicate if result may be from cache
            'method': 'baichuan' if use_baichuan else 'onvif'
        })

    except Exception as e:
        logger.error(f"Get presets API error: {e}")
        return jsonify({'success': False, 'error': str(e), 'presets': []}), 500


@ptz_bp.route('/api/ptz/<camera_serial>/preset/<preset_token>', methods=['POST'])
@csrf_exempt
@login_required
def api_ptz_goto_preset(camera_serial, preset_token):
    """Move camera to preset position"""
    try:
        # Validate camera
        camera = shared.camera_repo.get_camera(camera_serial)
        if not camera:
            return jsonify({'success': False, 'error': 'Camera not found'}), 404

        camera_type = camera.get('type')

        # Handle Eufy cameras via bridge (handles auto-restart internally)
        if camera_type == 'eufy':
            if not shared.eufy_bridge:
                return jsonify({'success': False, 'error': 'Eufy bridge not initialized'}), 503

            try:
                preset_index = int(preset_token)
            except ValueError:
                return jsonify({'success': False, 'error': 'Invalid preset token for Eufy'}), 400

            success, message = shared.eufy_bridge.goto_preset(camera_serial, preset_index)
            status_code = 200 if success else 503
            return jsonify({
                'success': success,
                'camera': camera_serial,
                'preset': preset_token,
                'message': message,
                'error': message if not success else None
            }), status_code

        # Only Amcrest, Reolink, and SV3C support ONVIF/Baichuan presets
        if camera_type not in ['amcrest', 'reolink', 'sv3c']:
            return jsonify({'success': False, 'error': 'Presets not supported for this camera type'}), 400

        # Check if camera should use Baichuan protocol
        use_baichuan = camera_type == 'reolink' and BaichuanPTZHandler.is_baichuan_capable(camera)

        if use_baichuan:
            # Execute goto preset via Baichuan
            success, message = BaichuanPTZHandler.goto_preset(camera_serial, preset_token, camera)
        else:
            # Execute goto preset via ONVIF
            success, message = ONVIFPTZHandler.goto_preset(camera_serial, preset_token, camera)

        return jsonify({
            'success': success,
            'camera': camera_serial,
            'preset': preset_token,
            'message': message
        })

    except Exception as e:
        logger.error(f"Goto preset API error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@ptz_bp.route('/api/ptz/<camera_serial>/preset', methods=['POST'])
@csrf_exempt
@login_required
def api_ptz_set_preset(camera_serial):
    """Save current position as preset

    Request body:
        name: Preset name (required for ONVIF)
        index: Preset index 0-3 (required for Eufy)
        token: Preset token to overwrite (optional, ONVIF only)
    """
    try:
        # Get preset info from request
        data = request.get_json()
        preset_name = data.get('name')
        preset_index = data.get('index')  # For Eufy: slot index 0-3
        preset_token = data.get('token')  # For ONVIF: token to overwrite existing preset

        # Validate camera
        camera = shared.camera_repo.get_camera(camera_serial)
        if not camera:
            return jsonify({'success': False, 'error': 'Camera not found'}), 404

        camera_type = camera.get('type')

        # Handle Eufy cameras via bridge (handles auto-restart internally)
        if camera_type == 'eufy':
            if not shared.eufy_bridge:
                return jsonify({'success': False, 'error': 'Eufy bridge not initialized'}), 503

            if preset_index is None:
                return jsonify({'success': False, 'error': 'Preset index required for Eufy (0-3)'}), 400

            try:
                preset_index = int(preset_index)
            except ValueError:
                return jsonify({'success': False, 'error': 'Invalid preset index'}), 400

            success, message = shared.eufy_bridge.save_preset(camera_serial, preset_index)
            status_code = 200 if success else 503
            response = {
                'success': success,
                'camera': camera_serial,
                'preset_index': preset_index,
                'message': message,
                'error': message if not success else None
            }
            if not success:
                # Include diagnostic info so frontend can offer retry
                response['retry_available'] = True
                response['bridge_status'] = shared.eufy_bridge.get_status()
            return jsonify(response), status_code

        # Preset name is required for all non-Eufy cameras
        if not preset_name:
            return jsonify({'success': False, 'error': 'Preset name required'}), 400

        # Check if camera uses Baichuan for PTZ (E1, NEOLINK cameras, no ONVIF port)
        use_baichuan = camera_type == 'reolink' and BaichuanPTZHandler.is_baichuan_capable(camera)

        if use_baichuan:
            # Save preset via Baichuan protocol (raw XML cmd_id 19, setPos command)
            preset_id_override = int(preset_token) if preset_token else None
            success, message = BaichuanPTZHandler.save_preset(
                camera_serial, preset_name, camera, preset_id=preset_id_override
            )
            return jsonify({
                'success': success,
                'camera': camera_serial,
                'preset_name': preset_name,
                'message': message
            })

        # Only Amcrest and Reolink support ONVIF presets
        if camera_type not in ['amcrest', 'reolink']:
            return jsonify({'success': False, 'error': 'Presets not supported for this camera type'}), 400

        # Set preset via ONVIF (preset_token allows overwriting an existing preset)
        success, message = ONVIFPTZHandler.set_preset(
            camera_serial, preset_name, camera, preset_token=preset_token
        )

        return jsonify({
            'success': success,
            'camera': camera_serial,
            'preset_name': preset_name,
            'preset_token': preset_token,
            'message': message
        })

    except Exception as e:
        logger.error(f"Set preset API error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@ptz_bp.route('/api/ptz/<camera_serial>/preset/<preset_token>', methods=['DELETE'])
@csrf_exempt
@login_required
def api_ptz_delete_preset(camera_serial, preset_token):
    """
    Delete a PTZ preset.

    Currently only supported on Eufy cameras.
    """
    try:
        # Validate camera
        camera = shared.camera_repo.get_camera(camera_serial)
        if not camera:
            return jsonify({'success': False, 'error': 'Camera not found'}), 404

        camera_type = camera.get('type')

        # Handle Eufy cameras via bridge (handles auto-restart internally)
        if camera_type == 'eufy':
            if not shared.eufy_bridge:
                return jsonify({'success': False, 'error': 'Eufy bridge not initialized'}), 503

            try:
                preset_index = int(preset_token)
            except ValueError:
                return jsonify({'success': False, 'error': 'Invalid preset token for Eufy'}), 400

            success, message = shared.eufy_bridge.delete_preset(camera_serial, preset_index)
            status_code = 200 if success else 503
            return jsonify({
                'success': success,
                'camera': camera_serial,
                'preset': preset_token,
                'message': message,
                'error': message if not success else None
            }), status_code

        # Other camera types don't support delete via this endpoint
        return jsonify({'success': False, 'error': 'Delete preset not supported for this camera type'}), 400

    except Exception as e:
        logger.error(f"Delete preset API error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# PTZ Client Latency
# ---------------------------------------------------------------------------

@ptz_bp.route('/api/ptz/latency/<client_uuid>/<camera_serial>', methods=['GET'])
@login_required
def api_ptz_get_latency(client_uuid, camera_serial):
    """
    Get learned PTZ latency for a client/camera pair.

    Returns stored latency data from PostgreSQL via PostgREST.
    If no data exists, returns default values.

    Args:
        client_uuid: Browser-generated UUID identifying the client
        camera_serial: Camera serial number

    Returns:
        JSON with avg_latency_ms and sample_count
    """
    try:
        # Query PostgREST for this client/camera pair
        response = shared._postgrest_session.get(
            f"{shared.POSTGREST_URL}/ptz_client_latency",
            params={
                'client_uuid': f'eq.{client_uuid}',
                'camera_serial': f'eq.{camera_serial}'
            },
            timeout=5
        )

        if response.status_code == 200:
            data = response.json()
            if data and len(data) > 0:
                record = data[0]
                return jsonify({
                    'success': True,
                    'avg_latency_ms': record.get('avg_latency_ms', 1000),
                    'sample_count': record.get('sample_count', 0),
                    'samples': record.get('samples', [])
                })

        # No data found - return defaults
        return jsonify({
            'success': True,
            'avg_latency_ms': 1000,
            'sample_count': 0,
            'samples': []
        })

    except Exception as e:
        logger.error(f"Get PTZ latency error: {e}")
        return jsonify({
            'success': False,
            'error': str(e),
            'avg_latency_ms': 1000,
            'sample_count': 0
        }), 500


@ptz_bp.route('/api/ptz/latency/<client_uuid>/<camera_serial>', methods=['POST'])
@csrf_exempt
@login_required
def api_ptz_update_latency(client_uuid, camera_serial):
    """
    Update learned PTZ latency for a client/camera pair.

    Stores observed latency in PostgreSQL via PostgREST.
    Maintains a rolling average of the last 10 samples.

    Args:
        client_uuid: Browser-generated UUID identifying the client
        camera_serial: Camera serial number

    Request body:
        observed_latency_ms: The observed latency in milliseconds

    Returns:
        JSON with updated avg_latency_ms and sample_count
    """
    try:
        import json

        data = request.get_json()
        observed_latency = data.get('observed_latency_ms')

        if observed_latency is None:
            return jsonify({'success': False, 'error': 'observed_latency_ms required'}), 400

        # First, try to get existing record
        get_response = shared._postgrest_session.get(
            f"{shared.POSTGREST_URL}/ptz_client_latency",
            params={
                'client_uuid': f'eq.{client_uuid}',
                'camera_serial': f'eq.{camera_serial}'
            },
            timeout=5
        )

        existing = None
        if get_response.status_code == 200:
            records = get_response.json()
            if records and len(records) > 0:
                existing = records[0]

        # Calculate new rolling average
        samples = existing.get('samples', []) if existing else []
        # PostgREST may return JSONB as string or as list depending on config
        if isinstance(samples, str):
            samples = json.loads(samples) if samples else []
        samples.append(observed_latency)

        # Keep only last 10 samples
        if len(samples) > 10:
            samples = samples[-10:]

        avg_latency = round(sum(samples) / len(samples))

        record_data = {
            'client_uuid': client_uuid,
            'camera_serial': camera_serial,
            'avg_latency_ms': avg_latency,
            'samples': json.dumps(samples),
            'sample_count': len(samples)
        }

        if existing:
            # Update existing record
            update_response = shared._postgrest_session.patch(
                f"{shared.POSTGREST_URL}/ptz_client_latency",
                params={
                    'client_uuid': f'eq.{client_uuid}',
                    'camera_serial': f'eq.{camera_serial}'
                },
                json={
                    'avg_latency_ms': avg_latency,
                    'samples': samples,
                    'sample_count': len(samples)
                },
                headers={'Prefer': 'return=representation'},
                timeout=5
            )
            success = update_response.status_code in [200, 204]
        else:
            # Insert new record
            insert_response = shared._postgrest_session.post(
                f"{shared.POSTGREST_URL}/ptz_client_latency",
                json=record_data,
                headers={'Prefer': 'return=representation'},
                timeout=5
            )
            success = insert_response.status_code in [200, 201]

        if success:
            logger.info(f"[PTZ Latency] Updated {client_uuid[:8]}.../{camera_serial}: {avg_latency}ms (samples: {len(samples)})")
            return jsonify({
                'success': True,
                'avg_latency_ms': avg_latency,
                'sample_count': len(samples),
                'samples': samples
            })
        else:
            error_msg = f"PostgREST error: {update_response.status_code if existing else insert_response.status_code}"
            logger.error(f"[PTZ Latency] {error_msg}")
            return jsonify({'success': False, 'error': error_msg}), 500

    except Exception as e:
        logger.error(f"Update PTZ latency error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# PTZ Reversal
# ---------------------------------------------------------------------------

@ptz_bp.route('/api/ptz/<camera_serial>/reversal', methods=['GET'])
@csrf_exempt
@login_required
def api_ptz_get_reversal(camera_serial):
    """
    Get PTZ reversal settings for a camera.

    Returns:
        JSON with reversed_pan and reversed_tilt booleans
    """
    try:
        reversal = shared.camera_repo.get_camera_ptz_reversal(camera_serial)
        return jsonify({
            'success': True,
            'camera_serial': camera_serial,
            **reversal
        })
    except Exception as e:
        logger.error(f"Get PTZ reversal error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@ptz_bp.route('/api/ptz/<camera_serial>/reversal', methods=['POST'])
@csrf_exempt
@login_required
def api_ptz_update_reversal(camera_serial):
    """
    Update PTZ reversal settings for a camera.

    Request body:
        reversed_pan: boolean (optional)
        reversed_tilt: boolean (optional)

    Returns:
        JSON with success status and updated values
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400

        reversed_pan = data.get('reversed_pan')
        reversed_tilt = data.get('reversed_tilt')

        # Validate input
        if reversed_pan is not None and not isinstance(reversed_pan, bool):
            return jsonify({'success': False, 'error': 'reversed_pan must be a boolean'}), 400
        if reversed_tilt is not None and not isinstance(reversed_tilt, bool):
            return jsonify({'success': False, 'error': 'reversed_tilt must be a boolean'}), 400

        if reversed_pan is None and reversed_tilt is None:
            return jsonify({'success': False, 'error': 'At least one of reversed_pan or reversed_tilt required'}), 400

        success = shared.camera_repo.update_camera_ptz_reversal(
            camera_serial,
            reversed_pan=reversed_pan,
            reversed_tilt=reversed_tilt
        )

        if success:
            reversal = shared.camera_repo.get_camera_ptz_reversal(camera_serial)
            logger.info(f"[PTZ Reversal] Updated {camera_serial}: pan={reversal['reversed_pan']}, tilt={reversal['reversed_tilt']}")
            return jsonify({
                'success': True,
                'camera_serial': camera_serial,
                **reversal
            })
        else:
            return jsonify({'success': False, 'error': 'Failed to update camera settings'}), 500

    except Exception as e:
        logger.error(f"Update PTZ reversal error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Camera Reboot
# ---------------------------------------------------------------------------

@ptz_bp.route('/api/camera/<camera_serial>/reboot', methods=['POST'])
@csrf_exempt
@login_required
def api_camera_reboot(camera_serial):
    """
    Reboot a camera.

    Requires JSON body with confirm='REBOOT' to prevent accidental reboots.
    Supports Reolink (Baichuan), Amcrest (ONVIF), and other ONVIF cameras.
    """
    try:
        # Require confirmation to prevent accidental reboots
        data = request.get_json() or {}
        if data.get('confirm') != 'REBOOT':
            return jsonify({
                'success': False,
                'error': 'Confirmation required. Send {"confirm": "REBOOT"} to proceed.'
            }), 400

        camera = shared.camera_repo.get_camera(camera_serial)
        if not camera:
            return jsonify({'success': False, 'error': 'Camera not found'}), 404

        # Check if camera supports reboot
        capabilities = camera.get('capabilities', [])
        if 'reboot' not in capabilities:
            return jsonify({
                'success': False,
                'error': 'Camera does not support reboot capability'
            }), 400

        camera_type = camera.get('type', '').lower()
        logger.info(f"[Reboot] Initiating reboot for {camera_serial} (type: {camera_type})")

        # Route to appropriate handler based on camera type
        if camera_type == 'reolink':
            from services.ptz.baichuan_ptz_handler import reboot_camera_baichuan
            success, message = reboot_camera_baichuan(camera_serial, camera)
        elif camera_type in ('amcrest', 'sv3c'):
            from services.onvif.onvif_ptz_handler import reboot_camera
            success, message = reboot_camera(camera_serial, camera)
        else:
            return jsonify({
                'success': False,
                'error': f'Reboot not implemented for camera type: {camera_type}'
            }), 400

        if success:
            logger.info(f"[Reboot] {camera_serial}: {message}")
            return jsonify({'success': True, 'message': message})
        else:
            logger.error(f"[Reboot] {camera_serial}: {message}")
            return jsonify({'success': False, 'error': message}), 500

    except Exception as e:
        logger.error(f"Camera reboot error for {camera_serial}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
