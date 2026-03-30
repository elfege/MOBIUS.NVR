"""
Talkback Blueprint
==================
Flask Blueprint exposing the two-way audio (talkback) WebSocket namespace
``/talkback`` and the HTTP capabilities route.

Routes registered here:
    GET  /api/talkback/<serial>/capabilities   - Query camera talkback support

SocketIO namespace handlers (registered on shared.socketio):
    /talkback  connect         - Client connects for push-to-talk session
    /talkback  disconnect      - Client disconnects, session cleaned up
    /talkback  start_talkback  - Begin audio stream to a camera
    /talkback  audio_frame     - Deliver a PCM audio chunk to the transcoder
    /talkback  stop_talkback   - End audio stream and release the camera

Supported talkback protocols:
    - eufy_p2p : Eufy cameras via P2P tunnel (PCM → AAC transcoding)
    - onvif    : ONVIF backchannel via go2rtc (PCM → G.711 transcoding)

Module-level state:
    _active_talkback_sessions  : dict mapping camera serial → session info dict
    _talkback_transcoder_manager : TalkbackTranscoderManager singleton
"""

from __future__ import annotations

import asyncio
import base64
import logging
import random
import traceback

import requests
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user  # noqa: F401
from flask_socketio import emit

import routes.shared as shared
from routes.helpers import csrf_exempt
from services.talkback_transcoder import TalkbackTranscoderManager
from services.go2rtc_client import get_go2rtc_client

logger = logging.getLogger(__name__)

talkback_bp = Blueprint('talkback', __name__)


# ============================================================================
# Module-level talkback state
# ============================================================================
# Track active talkback sessions with protocol info.
# Format: {camera_serial: {'sid': client_sid,
#                          'protocol': 'eufy_p2p'|'onvif',
#                          'go2rtc_stream': stream_name|None}}
_active_talkback_sessions: dict = {}

# go2rtc client singleton for ONVIF backchannel routing.
_go2rtc_client = None

# Audio frame counter used for periodic (every-10th-frame) log sampling.
_audio_frame_count: int = 0


def _get_go2rtc_client():
    """Get or initialize go2rtc client singleton."""
    global _go2rtc_client
    if _go2rtc_client is None:
        _go2rtc_client = get_go2rtc_client()
    return _go2rtc_client


def _on_transcoded_frame_ready(camera_serial: str, audio_data: bytes):
    """
    Callback called by TalkbackTranscoder when transcoded audio frame is ready.

    Routes audio to the appropriate destination based on the active session's protocol:
    - eufy_p2p: Send AAC ADTS frames to Eufy bridge
    - onvif: Send G.711 PCMU frames to go2rtc backchannel

    Args:
        camera_serial: Camera serial number
        audio_data: Transcoded audio bytes (AAC for Eufy, G.711 for ONVIF)
    """
    global _audio_frame_count

    # Get session info to determine protocol
    session_info = _active_talkback_sessions.get(camera_serial)
    if not session_info:
        # No active session, drop frame
        return

    if isinstance(session_info, dict):
        protocol = session_info.get('protocol', 'eufy_p2p')
        go2rtc_stream = session_info.get('go2rtc_stream')
    else:
        protocol = 'eufy_p2p'
        go2rtc_stream = None

    _audio_frame_count += 1

    if protocol == 'onvif' and go2rtc_stream:
        # ===== ONVIF: Send to go2rtc backchannel =====
        try:
            go2rtc = _get_go2rtc_client()

            # Run async send in new event loop (since we're in a sync callback)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(go2rtc.send_audio(go2rtc_stream, audio_data))
            finally:
                loop.close()

            # Log every 10th frame
            if _audio_frame_count % 10 == 0:
                print(f"[Talkback ONVIF] Sent G.711 frame #{_audio_frame_count} to {go2rtc_stream}, "
                      f"size={len(audio_data)}B, result={result}")
        except Exception as e:
            print(f"[Talkback ONVIF] Error sending to go2rtc: {e}")

    else:
        # ===== EUFY: Send AAC to bridge =====
        if not shared.eufy_bridge or not shared.eufy_bridge.is_running():
            print(f"[Talkback AAC] Bridge not running, dropping frame")
            return

        # Encode AAC bytes to base64 for JSON transmission
        aac_base64 = base64.b64encode(audio_data).decode('ascii')

        try:
            result = shared.eufy_bridge.send_talkback_audio(camera_serial, aac_base64)
            # Log every 10th frame
            if _audio_frame_count % 10 == 0:
                print(f"[Talkback AAC] Sent AAC frame #{_audio_frame_count} to {camera_serial}, "
                      f"size={len(audio_data)}B, result={result}")
        except Exception as e:
            print(f"[Talkback AAC] Error sending to bridge: {e}")


# Talkback transcoder manager: converts PCM to appropriate format (AAC for Eufy, G.711 for ONVIF)
_talkback_transcoder_manager = TalkbackTranscoderManager(on_aac_frame=_on_transcoded_frame_ready)


# ============================================================================
# Two-Way Audio (Talkback) WebSocket Namespace  — /talkback
# ============================================================================
# Clients connect to /talkback to send microphone audio to cameras.
# Uses push-to-talk model: client emits start_talkback, sends audio_frames,
# then stop_talkback when done.

def handle_talkback_connect():
    """
    Handle WebSocket connection for two-way audio.

    Client connects to /talkback namespace to send microphone audio to cameras.
    This uses a push-to-talk model where the client holds a button to talk.
    """
    from flask import request as flask_request
    sid = flask_request.sid
    logger.info(f"[Talkback] Client {sid[:8]}... connected")
    emit('connected', {'status': 'ok', 'sid': sid})


def handle_talkback_disconnect():
    """
    Handle WebSocket disconnection from talkback namespace.

    Automatically stops any active talkback session for this client.
    """
    from flask import request as flask_request
    sid = flask_request.sid

    # Find and stop any active session for this client
    for camera_serial, session_sid in list(_active_talkback_sessions.items()):
        if session_sid == sid:
            logger.info(f"[Talkback] Client {sid[:8]}... disconnected, stopping session for {camera_serial}")
            try:
                # Stop the transcoder first
                _talkback_transcoder_manager.stop_transcoder(camera_serial)
                # Then stop the Eufy bridge talkback
                if shared.eufy_bridge and shared.eufy_bridge.is_running():
                    shared.eufy_bridge.stop_talkback(camera_serial)
            except Exception as e:
                logger.error(f"[Talkback] Error stopping session on disconnect: {e}")
            del _active_talkback_sessions[camera_serial]

    logger.info(f"[Talkback] Client {sid[:8]}... disconnected")


def handle_start_talkback(data):
    """
    Start talkback session with a camera.

    Initiates two-way audio with the specified camera. Only one client can
    talk to a camera at a time (mutex on camera_serial).

    Args:
        data: {'camera_id': 'T8416P0023352DA9'}

    Emits:
        talkback_started: {'camera_id': str} on success
        talkback_error: {'camera_id': str, 'error': str} on failure
    """
    from flask import request as flask_request
    sid = flask_request.sid
    camera_id = data.get('camera_id')

    if not camera_id:
        emit('talkback_error', {'error': 'No camera_id provided'})
        return

    logger.info(f"[Talkback] Client {sid[:8]}... starting talkback for {camera_id}")

    # Check if another client already has an active session
    if camera_id in _active_talkback_sessions:
        session_info = _active_talkback_sessions[camera_id]
        other_sid = session_info.get('sid') if isinstance(session_info, dict) else session_info
        if other_sid != sid:
            logger.warning(f"[Talkback] Camera {camera_id} already in use by {other_sid[:8]}...")
            emit('talkback_error', {
                'camera_id': camera_id,
                'error': 'Camera is in use by another client'
            })
            return

    # Validate camera exists and supports talkback
    camera = shared.camera_repo.get_camera(camera_id)
    if not camera:
        emit('talkback_error', {'camera_id': camera_id, 'error': 'Camera not found'})
        return

    camera_type = camera.get('type', '').lower()

    # Check if camera has two_way_audio enabled in config
    two_way_audio_config = camera.get('two_way_audio', {})
    if not two_way_audio_config.get('enabled', False):
        emit('talkback_error', {
            'camera_id': camera_id,
            'error': 'Two-way audio not enabled for this camera'
        })
        return

    # Get the protocol to use
    protocol = two_way_audio_config.get('protocol', 'eufy_p2p')
    logger.info(f"[Talkback] Camera {camera_id} using protocol: {protocol}")

    # Handle each protocol type
    if protocol == 'eufy_p2p':
        # ===== EUFY P2P PROTOCOL =====
        if not shared.eufy_bridge or not shared.eufy_bridge.is_running():
            emit('talkback_error', {
                'camera_id': camera_id,
                'error': 'Eufy bridge not running'
            })
            return

        try:
            # Start Eufy bridge talkback session
            success = shared.eufy_bridge.start_talkback(camera_id)
            if success:
                # Start FFmpeg transcoder with camera-specific audio settings
                transcoder_started = _talkback_transcoder_manager.start_transcoder(camera_id, camera)
                if not transcoder_started:
                    print(f"[Talkback] Transcoder failed to start for {camera_id}, stopping bridge session")
                    shared.eufy_bridge.stop_talkback(camera_id)
                    emit('talkback_error', {
                        'camera_id': camera_id,
                        'error': 'Failed to start audio transcoder'
                    })
                    return

                _active_talkback_sessions[camera_id] = {
                    'sid': sid,
                    'protocol': 'eufy_p2p',
                    'go2rtc_stream': None
                }
                print(f"[Talkback] Started eufy_p2p for {camera_id}, sid={sid[:8]}...")
                emit('talkback_started', {'camera_id': camera_id})
            else:
                print(f"[Talkback] Failed to start eufy_p2p for {camera_id}")
                emit('talkback_error', {
                    'camera_id': camera_id,
                    'error': 'Failed to start talkback'
                })
        except Exception as e:
            print(f"[Talkback] Exception starting eufy_p2p: {e}")
            emit('talkback_error', {'camera_id': camera_id, 'error': str(e)})

    elif protocol == 'onvif':
        # ===== ONVIF BACKCHANNEL VIA GO2RTC =====
        onvif_config = two_way_audio_config.get('onvif', {})
        go2rtc_stream = onvif_config.get('go2rtc_stream')

        if not go2rtc_stream:
            emit('talkback_error', {
                'camera_id': camera_id,
                'error': 'No go2rtc_stream configured for this camera'
            })
            return

        try:
            # Get go2rtc client and start backchannel
            go2rtc = _get_go2rtc_client()

            # Run async call in event loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                success = loop.run_until_complete(go2rtc.start_backchannel(go2rtc_stream))
            finally:
                loop.close()

            if success:
                # Start FFmpeg transcoder (PCM -> G.711 PCMU for ONVIF)
                transcoder_started = _talkback_transcoder_manager.start_transcoder(camera_id, camera)
                if not transcoder_started:
                    print(f"[Talkback] Transcoder failed for ONVIF {camera_id}")
                    # Stop go2rtc backchannel
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        loop.run_until_complete(go2rtc.stop_backchannel(go2rtc_stream))
                    finally:
                        loop.close()
                    emit('talkback_error', {
                        'camera_id': camera_id,
                        'error': 'Failed to start audio transcoder'
                    })
                    return

                _active_talkback_sessions[camera_id] = {
                    'sid': sid,
                    'protocol': 'onvif',
                    'go2rtc_stream': go2rtc_stream
                }
                print(f"[Talkback] Started onvif for {camera_id} via go2rtc stream {go2rtc_stream}, sid={sid[:8]}...")
                emit('talkback_started', {'camera_id': camera_id})
            else:
                print(f"[Talkback] Failed to start go2rtc backchannel for {go2rtc_stream}")
                emit('talkback_error', {
                    'camera_id': camera_id,
                    'error': 'Failed to connect to camera via ONVIF backchannel'
                })
        except Exception as e:
            print(f"[Talkback] Exception starting onvif: {e}")
            traceback.print_exc()
            emit('talkback_error', {'camera_id': camera_id, 'error': str(e)})

    else:
        # Unsupported protocol
        emit('talkback_error', {
            'camera_id': camera_id,
            'error': f'Talkback protocol "{protocol}" not yet implemented'
        })


def handle_audio_frame(data):
    """
    Receive PCM audio frame from browser and feed to transcoder.

    Audio data should be base64-encoded PCM audio (16kHz, mono, 16-bit).
    The transcoder converts PCM to AAC and sends it to the Eufy bridge.

    This is called repeatedly while the talkback is active.

    Args:
        data: {'camera_id': 'T8416P...', 'audio_data': 'base64...'}
    """
    from flask import request as flask_request
    sid = flask_request.sid
    camera_id = data.get('camera_id')
    audio_data = data.get('audio_data')

    if not camera_id or not audio_data:
        print(f"[Talkback Audio] Malformed: camera={camera_id}, audio_len={len(audio_data) if audio_data else 0}")
        return  # Silently ignore malformed frames

    # Verify this client owns the session
    session_info = _active_talkback_sessions.get(camera_id)
    if not session_info:
        return  # No active session

    # Handle both old format (just sid) and new format (dict with sid, protocol)
    if isinstance(session_info, dict):
        session_sid = session_info.get('sid')
        protocol = session_info.get('protocol', 'eufy_p2p')
        go2rtc_stream = session_info.get('go2rtc_stream')
    else:
        # Legacy format - just sid
        session_sid = session_info
        protocol = 'eufy_p2p'
        go2rtc_stream = None

    if session_sid != sid:
        print(f"[Talkback Audio] Session mismatch: camera={camera_id}, "
              f"req_sid={sid[:8]}..., session_sid={session_sid[:8] if session_sid else 'None'}...")
        return  # Silently ignore if not the session owner

    # Route audio based on protocol
    try:
        if protocol == 'onvif' and go2rtc_stream:
            # ONVIF: Feed to transcoder, transcoder sends G.711 to go2rtc
            # The transcoder's callback will be modified to route to go2rtc
            result = _talkback_transcoder_manager.feed_pcm_base64(camera_id, audio_data)
        else:
            # Eufy: Feed to transcoder (which converts to AAC and sends to Eufy bridge)
            result = _talkback_transcoder_manager.feed_pcm_base64(camera_id, audio_data)

        # Log every 10th frame to avoid spam
        if random.random() < 0.1:
            print(f"[Talkback Audio] Fed PCM to transcoder for {camera_id} ({protocol}), len={len(audio_data)}, result={result}")
    except Exception as e:
        # Log but don't emit error for every frame
        print(f"[Talkback Audio] Transcoder feed error: {e}")


def handle_stop_talkback(data):
    """
    Stop talkback session.

    Ends the two-way audio stream and releases the camera.

    Args:
        data: {'camera_id': 'T8416P...'}

    Emits:
        talkback_stopped: {'camera_id': str}
    """
    from flask import request as flask_request
    sid = flask_request.sid
    camera_id = data.get('camera_id')

    if not camera_id:
        emit('talkback_error', {'error': 'No camera_id provided'})
        return

    logger.info(f"[Talkback] Client {sid[:8]}... stopping talkback for {camera_id}")

    # Get session info and verify ownership
    session_info = _active_talkback_sessions.get(camera_id)
    if not session_info:
        emit('talkback_error', {
            'camera_id': camera_id,
            'error': 'No active session for this camera'
        })
        return

    # Handle both old format (just sid) and new format (dict)
    if isinstance(session_info, dict):
        session_sid = session_info.get('sid')
        protocol = session_info.get('protocol', 'eufy_p2p')
        go2rtc_stream = session_info.get('go2rtc_stream')
    else:
        session_sid = session_info
        protocol = 'eufy_p2p'
        go2rtc_stream = None

    if session_sid != sid:
        emit('talkback_error', {
            'camera_id': camera_id,
            'error': 'No active session for this camera'
        })
        return

    # Stop the transcoder first (prevents more audio being sent)
    try:
        _talkback_transcoder_manager.stop_transcoder(camera_id)
    except Exception as e:
        logger.error(f"[Talkback] Error stopping transcoder: {e}")

    # Stop based on protocol
    if protocol == 'onvif' and go2rtc_stream:
        # Stop go2rtc backchannel
        try:
            go2rtc = _get_go2rtc_client()
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(go2rtc.stop_backchannel(go2rtc_stream))
            finally:
                loop.close()
            logger.info(f"[Talkback] Stopped go2rtc backchannel for {go2rtc_stream}")
        except Exception as e:
            logger.error(f"[Talkback] Error stopping go2rtc backchannel: {e}")
    else:
        # Stop Eufy bridge talkback
        if shared.eufy_bridge and shared.eufy_bridge.is_running():
            try:
                shared.eufy_bridge.stop_talkback(camera_id)
            except Exception as e:
                logger.error(f"[Talkback] Error stopping eufy talkback: {e}")

    # Release session
    if camera_id in _active_talkback_sessions:
        del _active_talkback_sessions[camera_id]

    emit('talkback_stopped', {'camera_id': camera_id})
    logger.info(f"[Talkback] Stopped {protocol} for {camera_id}")


def init_socketio(sio):
    """
    Register SocketIO event handlers for the /talkback namespace.

    Must be called from app.py AFTER socketio is initialized and set_services() has run.
    Using a factory function avoids the import-time crash that occurs when
    @shared.socketio.on(...) decorators fire before shared.socketio is set.
    """
    sio.on('connect', namespace='/talkback')(handle_talkback_connect)
    sio.on('disconnect', namespace='/talkback')(handle_talkback_disconnect)
    sio.on('start_talkback', namespace='/talkback')(handle_start_talkback)
    sio.on('audio_frame', namespace='/talkback')(handle_audio_frame)
    sio.on('stop_talkback', namespace='/talkback')(handle_stop_talkback)
    logger.info("[Talkback] SocketIO handlers registered on /talkback namespace")


# ============================================================================
# HTTP API Routes
# ============================================================================

@talkback_bp.route('/api/talkback/<camera_serial>/capabilities')
@login_required
def api_talkback_capabilities(camera_serial):
    """
    Check if camera supports two-way audio (talkback).

    Returns camera's talkback capability based on camera type.
    Currently only Eufy cameras are fully supported.

    Args:
        camera_serial: Camera serial number

    Returns:
        JSON with supported flag and optional details
    """
    try:
        camera = shared.camera_repo.get_camera(camera_serial)
        if not camera:
            return jsonify({'success': False, 'error': 'Camera not found'}), 404

        camera_type = camera.get('type', '').lower()

        # Check Eufy cloud connectivity for P2P
        eufy_cloud_ok = False
        eufy_bridge_alive = shared.eufy_bridge is not None and shared.eufy_bridge.is_running()
        if camera_type == 'eufy':
            try:
                import requests as http_req
                resp = http_req.get('https://mysecurity.eufylife.com', timeout=3)
                eufy_cloud_ok = resp.status_code == 200
            except Exception:
                eufy_cloud_ok = False

        # Capability matrix
        capabilities = {
            'eufy': {
                'supported': True,
                'method': 'p2p',
                'ready': eufy_bridge_alive,
                'cloud_reachable': eufy_cloud_ok,
                'cloud_required': True,
                'cloud_info': 'Eufy P2P backchannel requires cloud access for session key exchange. '
                              'Cameras must have WAN access (check firewall rules).'
            },
            'reolink': {
                'supported': False,  # Not yet implemented
                'method': 'baichuan',
                'ready': False
            },
            'amcrest': {
                'supported': False,  # Not yet implemented
                'method': 'onvif',
                'ready': False
            },
            'unifi': {
                'supported': False,  # Not yet implemented
                'method': 'onvif',
                'ready': False
            }
        }

        cap = capabilities.get(camera_type, {
            'supported': False,
            'method': 'unknown',
            'ready': False
        })

        return jsonify({
            'success': True,
            'camera': camera_serial,
            'camera_type': camera_type,
            **cap
        })

    except Exception as e:
        logger.error(f"[Talkback] Capabilities error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@talkback_bp.route('/api/eufy/cloud-status')
@login_required
def api_eufy_cloud_status():
    """Check Eufy cloud connectivity + bridge status.
    Used by talkback modal and PTZ controls to show cloud dependency status."""
    bridge_alive = shared.eufy_bridge is not None and shared.eufy_bridge.is_running()

    cloud_reachable = False
    try:
        import requests as http_req
        resp = http_req.get('https://mysecurity.eufylife.com', timeout=3)
        cloud_reachable = resp.status_code == 200
    except Exception:
        cloud_reachable = False

    return jsonify({
        'bridge_running': bridge_alive,
        'cloud_reachable': cloud_reachable,
        'p2p_available': bridge_alive and cloud_reachable,
        'message': (
            'Eufy cloud and bridge ready' if bridge_alive and cloud_reachable
            else 'Bridge not running' if not bridge_alive
            else 'Eufy cloud unreachable — cameras need WAN access for P2P'
        )
    })
