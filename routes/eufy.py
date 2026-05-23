"""
routes/eufy.py — Flask Blueprint for Eufy authentication and Amcrest MJPEG streaming routes.

Covers:
- Eufy auth page render
- Eufy bridge captcha / 2FA submission and status endpoints
- Amcrest MJPEG sub-stream and main-stream endpoints

All service singletons are accessed via routes.shared to avoid circular imports.
"""

import os
import json
import time
import asyncio
import logging

import requests
import websockets
from flask import Blueprint, jsonify, request, Response, render_template, send_file, current_app
from flask_login import login_required, current_user

import routes.shared as shared
from routes.helpers import csrf_exempt
from services.eufy.eufy_bridge_client import (
    submit_captcha_sync, submit_2fa_sync, check_status_sync,
    is_driver_connected_sync, is_station_connected_sync,
)

logger = logging.getLogger(__name__)

eufy_bp = Blueprint('eufy', __name__)

# PostgREST base URL (same convention as the rest of the app). Used to list
# the type=='eufy' cameras for the per-station status table.
POSTGREST_URL = os.getenv('NVR_POSTGREST_URL', 'http://postgrest:3001')

# Canonical path of the eufy-security-ws persistence file inside the
# unified-nvr container. Holds cached cloud tokens and the
# ``cloud_token_expiration`` epoch-MILLISECONDS value the status panel reads.
EUFY_PERSISTENT_JSON = os.getenv('EUFY_PERSISTENT_JSON', '/app/persistent.json')


def _require_admin():
    """Return a (json, status) tuple if the current user is NOT an admin,
    else None. Mirrors the admin gate used elsewhere (e.g. camera settings
    streaming_hub change). Eufy bridge management is destructive/global, so
    every mutating endpoint here is admin-only.
    """
    if not current_user or getattr(current_user, 'role', None) != 'admin':
        return jsonify({'success': False,
                        'message': 'Admin privileges required'}), 403
    return None


def _read_token_expiration():
    """Read ``cloud_token_expiration`` (epoch ms) from persistent.json.

    Returns:
        int | None: the epoch-millisecond expiry, or None if the file is
        missing / unreadable / lacks the key. Never raises — the status
        panel degrades gracefully to "unknown" when this is None.
    """
    try:
        with open(EUFY_PERSISTENT_JSON, 'r') as fh:
            data = json.load(fh)
        exp = data.get('cloud_token_expiration')
        return int(exp) if exp is not None else None
    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError) as e:
        logger.warning(f"[EUFY BRIDGE] Could not read token expiration: {e}")
        return None
    except Exception as e:
        logger.error(f"[EUFY BRIDGE] Unexpected error reading persistent.json: {e}")
        return None


def _list_eufy_cameras():
    """Fetch the type=='eufy' cameras from PostgREST.

    Returns a list of dicts with at least ``serial`` and ``name`` (and
    ``station`` when present). Returns [] on any error so the status
    endpoint still renders the driver/token sections.
    """
    try:
        resp = requests.get(
            f"{POSTGREST_URL}/cameras",
            params={'type': 'eq.eufy', 'select': 'serial,name,station'},
            timeout=5,
        )
        if resp.status_code == 200:
            return resp.json() or []
        logger.warning(f"[EUFY BRIDGE] camera list HTTP {resp.status_code}")
        return []
    except Exception as e:
        logger.error(f"[EUFY BRIDGE] Failed to list eufy cameras: {e}")
        return []


# ============================================================================================
#  ██████╗ ██╗   ██╗███████╗██╗   ██╗     █████╗ ██╗   ██╗████████╗██╗  ██╗
# ██╔════╝ ██║   ██║██╔════╝╚██╗ ██╔╝    ██╔══██╗██║   ██║╚══██╔══╝██║  ██║
# ██║  ███╗██║   ██║█████╗   ╚████╔╝     ███████║██║   ██║   ██║   ███████║
# ██║   ██║██║   ██║██╔══╝    ╚██╔╝      ██╔══██║██║   ██║   ██║   ██╔══██║
# ╚██████╔╝╚██████╔╝██║        ██║       ██║  ██║╚██████╔╝   ██║   ██║  ██║
#  ╚═════╝  ╚═════╝ ╚═╝        ╚═╝       ╚═╝  ╚═╝ ╚═════╝    ╚═╝   ╚═╝  ╚═╝
# ============================================================================================


@eufy_bp.route('/eufy-auth')
@csrf_exempt
@login_required
def eufy_auth_page():
    """
    Serve Eufy authentication page for captcha and 2FA submission
    """
    return render_template('eufy_auth.html')


@eufy_bp.route('/api/eufy-auth/captcha', methods=['POST'])
@csrf_exempt
@login_required
def submit_eufy_captcha():
    """
    Submit captcha code to Eufy bridge

    Expected JSON body:
    {
        "captcha_code": "1234"
    }

    Returns:
    {
        "success": true/false,
        "message": "...",
        "next_step": "2fa" (if successful)
    }
    """
    try:
        data = request.get_json()
        captcha_code = data.get('captcha_code', '').strip()

        if not captcha_code:
            return jsonify({
                'success': False,
                'message': 'Captcha code is required'
            }), 400

        # Validate format - alphanumeric, 4 characters
        if len(captcha_code) != 4 or not captcha_code.isalnum():
            return jsonify({
                'success': False,
                'message': 'Captcha code must be exactly 4 alphanumeric characters'
            }), 400

        # Submit to bridge
        logger.info(f"Submitting captcha code: {captcha_code}")
        success = submit_captcha_sync(captcha_code)

        if success:
            return jsonify({
                'success': True,
                'message': 'Captcha verified! Check your email for 2FA code.',
                'next_step': '2fa'
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Invalid captcha code. Please try again.'
            }), 400

    except ValueError as e:
        logger.error(f"Captcha validation error: {e}")
        return jsonify({
            'success': False,
            'message': str(e)
        }), 400

    except Exception as e:
        logger.error(f"Error submitting captcha: {e}")
        return jsonify({
            'success': False,
            'message': 'Failed to submit captcha. Bridge may not be running.'
        }), 500


@eufy_bp.route('/api/eufy-auth/2fa', methods=['POST'])
@csrf_exempt
@login_required
def submit_eufy_2fa():
    """
    Submit 2FA verification code to Eufy bridge

    Expected JSON body:
    {
        "verify_code": "123456"
    }

    Returns:
    {
        "success": true/false,
        "message": "...",
        "authenticated": true (if successful)
    }
    """
    try:
        data = request.get_json()
        verify_code = data.get('verify_code', '').strip()

        if not verify_code:
            return jsonify({
                'success': False,
                'message': '2FA code is required'
            }), 400

        # Validate format
        if len(verify_code) != 6 or not verify_code.isdigit():
            return jsonify({
                'success': False,
                'message': '2FA code must be exactly 6 digits'
            }), 400

        # Submit to bridge
        logger.info(f"Submitting 2FA code: {verify_code}")
        success = submit_2fa_sync(verify_code)

        if success:
            return jsonify({
                'success': True,
                'message': 'Authentication successful! Bridge is now connected.',
                'authenticated': True
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Invalid 2FA code. Please check your email and try again.'
            }), 400

    except ValueError as e:
        logger.error(f"2FA validation error: {e}")
        return jsonify({
            'success': False,
            'message': str(e)
        }), 400

    except Exception as e:
        logger.error(f"Error submitting 2FA: {e}")
        return jsonify({
            'success': False,
            'message': 'Failed to submit 2FA code. Bridge may not be running.'
        }), 500


@eufy_bp.route('/api/eufy-auth/status')
@csrf_exempt
@login_required
def eufy_auth_status():
    """
    Check Eufy bridge authentication status

    Returns:
    {
        "connected": true/false,
        "status": "connected" | "disconnected" | "error",
        "message": "..."
    }
    """
    try:
        if not shared.eufy_bridge:
            return jsonify({
                'connected': False,
                'status': 'disabled',
                'message': 'Eufy bridge not configured (USE_EUFY_BRIDGE=0)'
            })
        status = check_status_sync()

        if status.get('connected'):
            message = 'Bridge is connected and authenticated'
        elif status.get('status') == 'error':
            message = f"Error checking status: {status.get('error', 'Unknown error')}"
        else:
            message = 'Bridge is not connected. Authentication required.'

        return jsonify({
            'connected': status.get('connected', False),
            'status': status.get('status', 'unknown'),
            'message': message
        })

    except Exception as e:
        logger.error(f"Error checking auth status: {e}")
        return jsonify({
            'connected': False,
            'status': 'error',
            'message': f'Failed to check status: {str(e)}'
        }), 500


@eufy_bp.route('/api/eufy-auth/captcha-image')
@csrf_exempt
@login_required
def eufy_captcha_image():
    """
    Serve the current captcha image

    Returns: PNG image or 404 if not available
    """
    captcha_path = os.path.join(current_app.static_folder, 'eufy_captcha.png')

    if os.path.exists(captcha_path):
        return send_file(captcha_path, mimetype='image/png')
    else:
        return jsonify({
            'error': 'No captcha image available'
        }), 404


@eufy_bp.route('/api/eufy-auth/refresh-captcha', methods=['POST'])
@csrf_exempt
@login_required
def refresh_eufy_captcha():
    """Request a new captcha from the bridge"""
    try:
        async def request_captcha():
            """Send invalid captcha to trigger new one"""
            try:
                async with websockets.connect('ws://127.0.0.1:3000', open_timeout=5) as ws:
                    # Set API schema
                    await ws.send(json.dumps({
                        "messageId": "schema",
                        "command": "set_api_schema",
                        "schemaVersion": 21
                    }))
                    await ws.recv()

                    # Send invalid captcha to trigger new one
                    await ws.send(json.dumps({
                        "messageId": "refresh_captcha",
                        "command": "driver.set_captcha",
                        "captchaCode": "0000"
                    }))

                    await asyncio.sleep(0.5)
                return True
            except Exception as e:
                logger.error(f"Failed to request new captcha: {e}")
                return False

        success = asyncio.run(request_captcha())
        time.sleep(1)

        return jsonify({
            'success': success,
            'message': 'New captcha requested' if success else 'Failed to request captcha',
            'timestamp': int(time.time() * 1000)
        })

    except Exception as e:
        logger.error(f"Error refreshing captcha: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


# ============================================================================================
#  EUFY BRIDGE MANAGEMENT (status panel + relogin)
# ============================================================================================
#
# These power the "Eufy Bridge" tab in the global settings modal. They are
# distinct from the /api/eufy-auth/* endpoints above (which the inline
# captcha/2FA flow reuses verbatim): the endpoints here report live bridge
# health and trigger a destructive force-re-login.


@eufy_bp.route('/api/eufy-bridge/status')
@csrf_exempt
@login_required
def eufy_bridge_status():
    """Live Eufy bridge status for the settings panel.

    Returns a JSON object with three sections:

      * ``driver``  — cloud driver connection state (driver.is_connected).
      * ``token``   — cloud token expiry (epoch ms + computed days left),
                      read from persistent.json on the container.
      * ``stations``— per-camera P2P connection state. For each type=='eufy'
                      camera we query ``station.is_connected`` using the
                      camera's station serial (falls back to its own serial
                      for standalone cams). The 3-valued ``state`` field is
                      'connected' | 'timeout' | 'unknown' — 'unknown' is used
                      for HomeBase serial mismatches / transient errors and
                      must NOT be rendered as a hard failure.

    @login_required (read-only; not admin-gated so non-admins who can see
    the tab — they can't, it's admin-gated client-side — would still only
    read). Returns 200 even when the bridge is down, with the down-state
    encoded in the body, so the panel can render a meaningful message.
    """
    # Bridge disabled entirely (USE_EUFY_BRIDGE=0) → report disabled.
    if not shared.eufy_bridge:
        return jsonify({
            'available': False,
            'driver': {'connected': False, 'reachable': False},
            'token': {'expiration_ms': None, 'days_left': None},
            'stations': [],
            'message': 'Eufy bridge not configured (USE_EUFY_BRIDGE=0)',
        })

    # ── Driver connection ────────────────────────────────────────────────
    # If the bridge port is dead, skip the WS round-trips entirely.
    bridge_up = False
    try:
        bridge_up = shared.eufy_bridge.is_running()
    except Exception as e:
        logger.warning(f"[EUFY BRIDGE] is_running() raised: {e}")

    driver_connected = False
    driver_reachable = bridge_up
    if bridge_up:
        drv = is_driver_connected_sync()
        driver_connected = bool(drv.get('connected'))
        if 'error' in drv:
            driver_reachable = False

    # ── Token expiry ─────────────────────────────────────────────────────
    exp_ms = _read_token_expiration()
    days_left = None
    if exp_ms is not None:
        # cloud_token_expiration is epoch MILLISECONDS.
        days_left = round((exp_ms / 1000.0 - time.time()) / 86400.0, 1)

    # ── Per-station P2P state ────────────────────────────────────────────
    stations = []
    cameras = _list_eufy_cameras()
    for cam in cameras:
        serial = cam.get('serial')
        # Standalone cams: station serial == camera serial. HomeBase-linked
        # cams: a distinct 'station' value; prefer it when present.
        station_serial = cam.get('station') or serial
        if bridge_up and station_serial:
            st = is_station_connected_sync(station_serial)
        else:
            st = {'state': 'unknown', 'connected': None,
                  'error': 'bridge down' if not bridge_up else 'no serial'}
        stations.append({
            'serial': serial,
            'name': cam.get('name') or serial,
            'station_serial': station_serial,
            'state': st.get('state', 'unknown'),
            'connected': st.get('connected'),
        })

    return jsonify({
        'available': True,
        'driver': {'connected': driver_connected, 'reachable': driver_reachable},
        'token': {'expiration_ms': exp_ms, 'days_left': days_left},
        'stations': stations,
    })


@eufy_bp.route('/api/eufy-bridge/relogin', methods=['POST'])
@csrf_exempt
@login_required
def eufy_bridge_relogin():
    """Force a fresh Eufy cloud login (admin-only, destructive).

    Steps:
      1. Back up persistent.json to persistent.json.bak.<unix_ts>.
      2. Remove the original (this drops the cached cloud token, forcing the
         bridge to re-authenticate from scratch on next start).
      3. Restart the bridge via EufyBridge.restart().

    After this returns, the existing captcha flow takes over: the bridge,
    lacking a valid token, asks Eufy cloud to log in again and emits a
    captcha image. The settings panel then polls /api/eufy-auth/status and
    surfaces the captcha + 2FA inputs (reusing /api/eufy-auth/captcha and
    /api/eufy-auth/2fa).

    NOTE: We deliberately do NOT block on the bridge becoming "ready" —
    restart() may return False precisely because re-auth is now required,
    which is the expected post-relogin state. The panel drives the rest.
    """
    not_admin = _require_admin()
    if not_admin:
        return not_admin

    if not shared.eufy_bridge:
        return jsonify({'success': False,
                        'message': 'Eufy bridge not configured (USE_EUFY_BRIDGE=0)'}), 400

    try:
        backup_path = None
        # ── 1. Back up + 2. remove persistent.json ───────────────────────
        if os.path.exists(EUFY_PERSISTENT_JSON):
            ts = int(time.time())
            backup_path = f"{EUFY_PERSISTENT_JSON}.bak.{ts}"
            import shutil
            shutil.copy2(EUFY_PERSISTENT_JSON, backup_path)
            os.remove(EUFY_PERSISTENT_JSON)
            logger.info(f"[EUFY BRIDGE] persistent.json backed up to {backup_path} and removed")
        else:
            logger.info(f"[EUFY BRIDGE] No persistent.json to back up at {EUFY_PERSISTENT_JSON}")

        # ── 3. Restart the bridge ────────────────────────────────────────
        # restart() may legitimately return False here because the token is
        # now gone and re-authentication (captcha/2FA) is required — that's
        # the whole point. We surface a flag rather than treating it as an
        # error; the captcha flow continues client-side.
        restarted_ready = shared.eufy_bridge.restart()

        return jsonify({
            'success': True,
            'message': ('Bridge restarted; complete the captcha/2FA below to '
                        'finish re-login.'),
            'backup_path': backup_path,
            'bridge_ready': bool(restarted_ready),
        })

    except Exception as e:
        logger.error(f"[EUFY BRIDGE] relogin failed: {e}")
        return jsonify({'success': False,
                        'message': f'Relogin failed: {e}'}), 500


# ============================================================================================
#  █████╗ ███╗   ███╗ ██████╗██████╗ ███████╗███████╗████████╗
# ██╔══██╗████╗ ████║██╔════╝██╔══██╗██╔════╝██╔════╝╚══██╔══╝
# ███████║██╔████╔██║██║     ██████╔╝█████╗  ███████╗   ██║
# ██╔══██║██║╚██╔╝██║██║     ██╔══██╗██╔══╝  ╚════██║   ██║
# ██║  ██║██║ ╚═╝ ██║╚██████╗██║  ██║███████╗███████║   ██║
# ╚═╝  ╚═╝╚═╝     ╚═╝ ╚═════╝╚═╝  ╚═╝╚══════╝╚══════╝   ╚═╝
#  ███╗   ███╗     ██╗██████╗ ███████╗ ██████╗
#  ████╗ ████║     ██║██╔══██╗██╔════╝██╔════╝
#  ██╔████╔██║     ██║██████╔╝█████╗  ██║  ███╗
#  ██║╚██╔╝██║██   ██║██╔═══╝ ██╔══╝  ██║   ██║
#  ██║ ╚═╝ ██║╚█████╔╝██║     ███████╗╚██████╔╝
#  ╚═╝     ╚═╝ ╚════╝ ╚═╝     ╚══════╝ ╚═════╝
# ============================================================================================

# ===== AMCREST MJPEG Service Routes =====

@eufy_bp.route('/api/amcrest/<camera_id>/stream/mjpeg')
@login_required
def api_amcrest_stream_mjpeg(camera_id):
    """MJPEG sub stream for Amcrest cameras (grid mode)"""
    logger.info(f"Client requesting Amcrest MJPEG stream for {camera_id}")

    try:
        camera = shared.camera_repo.get_camera(camera_id)
        if not camera:
            return "Camera not found", 404

        if camera.get('type') != 'amcrest':
            return "Not an Amcrest camera", 400

        mjpeg_config = camera.get('mjpeg_snap', {})
        sub_config = mjpeg_config.get('sub', mjpeg_config)
        if not sub_config.get('enabled', True):
            return "MJPEG not enabled", 400

        camera_with_sub = camera.copy()
        camera_with_sub['mjpeg_snap'] = sub_config
        camera_with_sub['mjpeg_snap']['snap_type'] = 'sub'

        if not shared.amcrest_mjpeg_capture_service.add_client(camera_id, camera_with_sub, shared.camera_repo):
            return "Failed to start capture", 500

        def generate():
            try:
                last_frame_number = -1
                while True:
                    frame_data = shared.amcrest_mjpeg_capture_service.get_latest_frame(camera_id)
                    if frame_data and frame_data['frame_number'] != last_frame_number:
                        snapshot = frame_data['data']
                        last_frame_number = frame_data['frame_number']
                        yield (b'--jpgboundary\r\n'
                               b'Content-Type: image/jpeg\r\n' +
                               f'Content-Length: {len(snapshot)}\r\n\r\n'.encode() +
                               snapshot + b'\r\n')
                    time.sleep(0.033)
            except GeneratorExit:
                shared.amcrest_mjpeg_capture_service.remove_client(camera_id)
            except Exception as e:
                logger.error(f"[Amcrest MJPEG] Error {camera_id}: {e}")
                shared.amcrest_mjpeg_capture_service.remove_client(camera_id)

        return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=jpgboundary')
    except Exception as e:
        return f"Stream error: {e}", 500


@eufy_bp.route('/api/amcrest/<camera_id>/stream/mjpeg/main')
@login_required
def api_amcrest_stream_mjpeg_main(camera_id):
    """MJPEG main stream for Amcrest cameras (fullscreen mode)"""
    logger.info(f"Client requesting Amcrest MJPEG MAIN stream for {camera_id}")

    try:
        camera = shared.camera_repo.get_camera(camera_id)
        if not camera:
            return "Camera not found", 404

        if camera.get('type') != 'amcrest':
            return "Not an Amcrest camera", 400

        mjpeg_snap = camera.get('mjpeg_snap', {})
        main_config = mjpeg_snap.get('main', mjpeg_snap.get('sub', mjpeg_snap))
        if not main_config.get('enabled', True):
            return "MJPEG not enabled", 400

        camera_main = camera.copy()
        camera_main['mjpeg_snap'] = main_config.copy()
        camera_main['mjpeg_snap']['snap_type'] = 'main'

        camera_id_main = f"{camera_id}_main"

        if not shared.amcrest_mjpeg_capture_service.add_client(camera_id_main, camera_main, shared.camera_repo):
            return "Failed to start capture", 500

        def generate():
            try:
                last_frame_number = -1
                while True:
                    frame_data = shared.amcrest_mjpeg_capture_service.get_latest_frame(camera_id_main)
                    if frame_data and frame_data['frame_number'] != last_frame_number:
                        snapshot = frame_data['data']
                        last_frame_number = frame_data['frame_number']
                        yield (b'--jpgboundary\r\n'
                               b'Content-Type: image/jpeg\r\n' +
                               f'Content-Length: {len(snapshot)}\r\n\r\n'.encode() +
                               snapshot + b'\r\n')
                    time.sleep(0.033)
            except GeneratorExit:
                shared.amcrest_mjpeg_capture_service.remove_client(camera_id_main)
            except Exception as e:
                logger.error(f"[Amcrest MJPEG MAIN] Error {camera_id}: {e}")
                shared.amcrest_mjpeg_capture_service.remove_client(camera_id_main)

        return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=jpgboundary')
    except Exception as e:
        return f"Stream error: {e}", 500
