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
from services.eufy.eufy_bridge_client import submit_captcha_sync, submit_2fa_sync, check_status_sync

logger = logging.getLogger(__name__)

eufy_bp = Blueprint('eufy', __name__)


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
