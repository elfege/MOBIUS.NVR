"""
routes/streaming.py — Streaming routes Blueprint for the NVR Flask application.

Covers:
- Stream lifecycle: POST /api/stream/start/<camera_serial>
                    POST /api/stream/stop/<camera_serial>
                    POST /api/stream/restart/<camera_serial>
                    GET  /api/stream/status/<camera_serial>
- Camera state:     GET  /api/camera/state/<camera_id>
                    GET  /api/camera/states
- Stream list:      GET  /api/streams
                    GET  /api/streams/active  (alias)
                    POST /api/streams/stop-all
- HLS serving:      GET  /api/streams/<camera_serial>/playlist.m3u8
                    GET  /api/streams/<camera_serial>/<segment>
- UniFi cameras:    GET  /api/unifi/cameras
                    GET  /api/unifi/<camera_id>/snapshot
                    GET  /api/unifi/<camera_id>/stream/mjpeg
- MJPEG status:     GET  /api/status/mjpeg-captures
                    GET  /api/status/mjpeg-captures/<camera_id>
                    GET  /api/status/unifi-monitor
                    GET  /api/status/unifi-monitor/summary
- Session mgmt:     POST /api/maintenance/recycle-unifi-sessions
- MediaServer MJPEG:GET  /api/mediaserver/<camera_id>/stream/mjpeg
                    GET  /api/status/mediaserver-mjpeg
                    GET  /api/status/mediaserver-mjpeg/<camera_id>
- Snapshot:         GET  /api/snap/<camera_id>
- Reolink MJPEG:    GET  /api/reolink/<camera_id>/stream/mjpeg
- SV3C MJPEG:       GET  /api/sv3c/<camera_id>/stream/mjpeg
- SocketIO /mjpeg namespace:        connect, disconnect, subscribe, unsubscribe
- SocketIO /stream_events namespace: connect, disconnect
"""

import time
import traceback
from threading import Thread

from flask import Blueprint, jsonify, request, Response
from flask_login import login_required, current_user
from flask_socketio import emit

import routes.shared as shared
from routes.helpers import csrf_exempt

# ---------------------------------------------------------------------------
# Blueprint definition
# ---------------------------------------------------------------------------
streaming_bp = Blueprint('streaming', __name__)


########################################################
#                  STREAM LIFECYCLE
########################################################

@streaming_bp.route('/api/stream/start/<camera_serial>', methods=['POST'])
@csrf_exempt
@login_required
def api_stream_start(camera_serial):
    """Start HLS stream for camera"""
    try:
        # Get camera (includes hidden cameras)
        camera = shared.camera_repo.get_camera(camera_serial)

        # Early rejection
        if not camera or camera.get('hidden', False):
            shared.stream_manager  # referenced so lint doesn't prune the import
            import logging
            logging.getLogger(__name__).warning(
                f"API access denied: Camera {camera_serial} not found or hidden")
            return jsonify({
                'success': False,
                'error': 'Camera not found or not accessible'
            }), 404

        camera_name = camera.get('name', camera_serial)
        if not camera_name:
            return jsonify({'success': False, 'error': 'Camera not found'}), 404

        print(f"Attempting to start camera {camera_serial} - {camera_name}")

        # Resolve effective stream type (user preference overrides camera default)
        # This allows per-user stream type switching to actually work end-to-end
        stream_type = shared.camera_repo.get_effective_stream_type(
            camera_serial, user_id=current_user.id if current_user else None)
        if stream_type == 'GO2RTC':
            # GO2RTC streams bypass FFmpeg + MediaMTX entirely.
            # go2rtc reads from Neolink RTSP directly and serves WebRTC to browser.
            # No backend stream start needed — go2rtc handles everything on-demand.
            print(f"[API] {camera_serial} is GO2RTC - no FFmpeg needed (go2rtc reads from Neolink)")
            return jsonify({
                'success': True,
                'camera_serial': camera_serial,
                'camera_name': camera_name,
                'stream_type': 'GO2RTC',
                'protocol': 'go2rtc_webrtc',
                'message': f'GO2RTC stream for {camera_name} (WebRTC via go2rtc, no FFmpeg)'
            })

        if stream_type == 'MJPEG':
            camera_type = camera.get('type', '').lower()
            print(f"[API] {camera_serial} is MJPEG camera - skipping HLS/RTSP start")
            # Return appropriate MJPEG endpoint based on camera type
            if camera_type == 'sv3c':
                mjpeg_url = f"/api/sv3c/{camera_serial}/stream/mjpeg"
            elif camera_type == 'reolink':
                mjpeg_url = f"/api/reolink/{camera_serial}/stream/mjpeg"
            elif camera_type == 'amcrest':
                mjpeg_url = f"/api/amcrest/{camera_serial}/stream/mjpeg"
            elif camera_type == 'unifi':
                mjpeg_url = f"/api/unifi/{camera_serial}/stream/mjpeg"
            else:
                mjpeg_url = f"/api/mediaserver/{camera_serial}/stream/mjpeg"
            return jsonify({
                'success': True,
                'camera_serial': camera_serial,
                'camera_name': camera_name,
                'stream_url': mjpeg_url,
                'stream_type': 'MJPEG',
                'message': f'MJPEG stream for {camera_name} (no HLS needed)'
            })

        # Validate streaming capability
        if not shared.ptz_validator.is_streaming_capable(camera_serial):
            return jsonify({'success': False, 'error': 'Camera does not support streaming'}), 400

        # Extract resolution from request (defaults to 'sub' for grid view)
        # 'sub' = low-res for grid, 'main' = high-res for fullscreen
        data = request.get_json() or {}
        resolution = data.get('type', 'sub')  # 'main' or 'sub'

        print(f"[API] /api/stream/start/{camera_serial} - resolution={resolution}, effective_type={stream_type}")

        # Start the stream with specified resolution.
        # Pass effective stream_type as protocol_override so that cameras whose stored
        # config says MJPEG but whose user preference says WEBRTC/HLS actually start FFmpeg.
        stream_url = shared.stream_manager.start_stream(
            camera_serial, resolution=resolution, protocol_override=stream_type)

        if not stream_url:
            return jsonify({'success': False, 'error': 'Failed to start stream'}), 500

        print(f"[API] Returning stream_url={stream_url} for {camera_name} ({resolution})")

        return jsonify({
            'success': True,
            'camera_serial': camera_serial,
            'camera_name': camera_name,
            'stream_url': stream_url,
            'resolution': resolution,  # Include for frontend debugging
            'message': f'Stream started for {camera_name}'
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e),
            'camera_serial': camera_serial
        }), 500


@streaming_bp.route('/api/stream/stop/<camera_serial>', methods=['POST'])
@csrf_exempt
@login_required
def api_stream_stop(camera_serial):
    """Stop HLS stream for camera"""
    try:
        camera_name = shared.camera_repo.get_camera_name(camera_serial)
        success = shared.stream_manager.stop_stream(camera_serial)

        if success:
            return jsonify({
                'success': True,
                'camera_serial': camera_serial,
                'camera_name': camera_name,
                'message': f'Stream stopped for {camera_name}'
            })
        else:
            return jsonify({
                'success': False,
                'camera_serial': camera_serial,
                'error': 'Stream was not running'
            }), 400

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'camera_serial': camera_serial
        }), 500


@streaming_bp.route('/api/stream/restart/<camera_serial>', methods=['POST'])
@csrf_exempt
@login_required
def api_stream_restart(camera_serial):
    """
    Restart stream for camera - stops FFmpeg process and starts fresh.

    This is a 'nuclear' restart that kills the backend FFmpeg and creates
    a new one. Use when stream is stuck, looping, or frozen.

    Unlike the UI refresh which just reconnects HLS.js, this actually
    terminates the FFmpeg publisher and starts a new process.

    Args (JSON body):
        type: 'sub' or 'main' (optional, defaults to 'sub')

    Returns:
        JSON with success status and new stream URL
    """
    import logging
    logger = logging.getLogger(__name__)

    try:
        camera_name = shared.camera_repo.get_camera_name(camera_serial)
        camera = shared.camera_repo.get_camera(camera_serial)

        if not camera:
            return jsonify({
                'success': False,
                'error': f'Camera {camera_serial} not found'
            }), 404

        # Get resolution from request (defaults to 'sub')
        data = request.get_json() or {}
        resolution = data.get('type', 'sub')

        # Resolve effective stream type (user preference overrides camera default)
        stream_type = shared.camera_repo.get_effective_stream_type(
            camera_serial, user_id=current_user.id if current_user else None).upper()
        if stream_type == 'MJPEG':
            return jsonify({
                'success': False,
                'error': 'MJPEG streams do not support restart (stateless)'
            }), 400

        logger.info(f"[RESTART] Restarting stream for {camera_name} ({camera_serial})")

        # Clear watchdog cooldown so it doesn't block this manual restart
        try:
            shared.stream_watchdog.clear_cooldown(camera_serial)
        except Exception as e:
            logger.debug(f"[RESTART] Could not clear watchdog cooldown: {e}")

        # Step 1: Stop the stream (kills FFmpeg) or clear zombie slot
        was_running = shared.stream_manager.is_stream_alive(camera_serial)
        has_slot = camera_serial in shared.stream_manager.active_streams

        if has_slot:
            slot_status = shared.stream_manager.active_streams.get(camera_serial, {}).get('status', 'unknown')
            logger.info(f"[RESTART] Found existing slot for {camera_name} (status: {slot_status}, alive: {was_running})")

            if was_running:
                stop_success = shared.stream_manager.stop_stream(camera_serial)
                if not stop_success:
                    logger.warning(f"[RESTART] Stop returned False for {camera_name}, continuing anyway")
            else:
                # Zombie slot: has entry but process isn't running
                logger.warning(f"[RESTART] Removing zombie slot for {camera_name} (status: {slot_status})")
                shared.stream_manager.active_streams.pop(camera_serial, None)

            # Brief pause to let sockets release
            time.sleep(0.5)

        # Step 2: Start fresh stream
        stream_url = shared.stream_manager.start_stream(camera_serial, resolution=resolution)

        if not stream_url:
            return jsonify({
                'success': False,
                'error': 'Failed to restart stream',
                'camera_serial': camera_serial
            }), 500

        # Step 3: Return immediately — publisher readiness notified via SocketIO
        # Previously this blocked for up to 15 seconds waiting for MediaMTX.
        # Now we spawn a background thread that waits and emits stream_restarted
        # via the existing SocketIO infrastructure when the publisher is ready.
        def _wait_and_notify():
            ready = shared.camera_state_tracker.wait_for_publisher_ready(
                camera_serial, timeout=15
            )
            if ready:
                shared.camera_state_tracker.register_success(camera_serial)
            else:
                logger.warning(
                    f"[RESTART] Publisher not confirmed ready for {camera_name} "
                    f"(FFmpeg may still be connecting to camera)"
                )
            # Broadcast stream_restarted so frontend HLS.js refreshes
            if shared.stream_watchdog:
                shared.stream_watchdog._broadcast_stream_restarted(camera_serial)

        Thread(
            target=_wait_and_notify,
            daemon=True,
            name=f"restart-notify-{camera_serial}"
        ).start()

        logger.info(f"[RESTART] Stream restart initiated for {camera_name}: {stream_url}")

        return jsonify({
            'success': True,
            'camera_serial': camera_serial,
            'camera_name': camera_name,
            'stream_url': stream_url,
            'was_running': was_running,
            'publisher_ready': False,  # Will be notified via SocketIO
            'message': f'Stream restart initiated for {camera_name} — publisher readiness via WebSocket'
        })

    except Exception as e:
        logger.error(f"[RESTART] Failed for {camera_serial}: {e}")
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e),
            'camera_serial': camera_serial
        }), 500


@streaming_bp.route('/api/stream/status/<camera_serial>')
@login_required
def api_stream_status(camera_serial):
    """Get stream status for camera"""
    try:
        is_alive = shared.stream_manager.is_stream_alive(camera_serial)
        stream_url = shared.stream_manager.get_stream_url(
            camera_serial) if is_alive else None

        return jsonify({
            'success': True,
            'camera_serial': camera_serial,
            'is_streaming': is_alive,
            'stream_url': stream_url
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'camera_serial': camera_serial
        }), 500


########################################################
#                  CAMERA STATE
########################################################

@streaming_bp.route('/api/camera/state/<camera_id>')
@login_required
def api_camera_state(camera_id):
    """
    Get detailed camera state from CameraStateTracker

    Returns comprehensive state information including:
    - availability (ONLINE, STARTING, OFFLINE, DEGRADED)
    - publisher_active (MediaMTX has active publisher)
    - ffmpeg_process_alive (FFmpeg publisher process running)
    - failure_count and backoff state
    - next_retry timestamp
    - error_message (last error if any)

    For MJPEG cameras (which don't use MediaMTX), returns hardcoded ONLINE status
    since they stream directly from camera hardware.

    Args:
        camera_id: Camera serial number

    Returns:
        JSON with camera state details or error
    """
    import logging
    logger = logging.getLogger(__name__)

    try:
        # All cameras (LL-HLS and MJPEG) now use CameraStateTracker
        # MJPEG capture services report their state via update_mjpeg_capture_state()
        # LL-HLS cameras get state from MediaMTX API polling
        state = shared.camera_state_tracker.get_camera_state(camera_id)

        # Get camera config for stream_type field
        camera = shared.camera_repo.get_camera(camera_id)
        camera_stream_type = camera.get('stream_type', 'LL_HLS') if camera else 'LL_HLS'
        is_mjpeg = camera_stream_type == 'MJPEG'

        return jsonify({
            'success': True,
            'camera_id': camera_id,
            'stream_type': camera_stream_type,
            'availability': state.availability.value,
            'publisher_active': state.publisher_active,  # For MJPEG: "capture active"
            'ffmpeg_process_alive': state.publisher_active if not is_mjpeg else False,  # Derive from publisher_active for LL-HLS
            'last_seen': state.last_seen.isoformat() if state.last_seen else None,
            'failure_count': state.failure_count,
            'next_retry': state.next_retry.isoformat() if state.next_retry else None,
            'backoff_seconds': state.backoff_seconds,
            'error_message': state.error_message,
            'can_retry': shared.camera_state_tracker.can_retry(camera_id)
        })

    except Exception as e:
        logger.error(f"Error getting camera state for {camera_id}: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e),
            'camera_id': camera_id
        }), 500


@streaming_bp.route('/api/camera/states')
@login_required
def api_camera_states_batch():
    """
    Batch camera state for all tracked cameras — replaces N+1 per-camera polling.

    Returns all camera states in a single response, eliminating the need for
    the frontend to poll /api/camera/state/<id> individually per camera.
    With 20 cameras, this reduces state polling from 20 requests/cycle to 1.

    Returns:
        JSON with states dict keyed by camera_id
    """
    import logging
    logger = logging.getLogger(__name__)

    try:
        states = {}
        with shared.camera_state_tracker._lock:
            for camera_id, state in shared.camera_state_tracker._states.items():
                camera = shared.camera_repo.get_camera(camera_id)
                camera_stream_type = camera.get('stream_type', 'LL_HLS') if camera else 'LL_HLS'
                is_mjpeg = camera_stream_type == 'MJPEG'

                states[camera_id] = {
                    'camera_id': camera_id,
                    'stream_type': camera_stream_type,
                    'availability': state.availability.value,
                    'publisher_active': state.publisher_active,
                    'ffmpeg_process_alive': state.publisher_active if not is_mjpeg else False,
                    'last_seen': state.last_seen.isoformat() if state.last_seen else None,
                    'failure_count': state.failure_count,
                    'next_retry': state.next_retry.isoformat() if state.next_retry else None,
                    'backoff_seconds': state.backoff_seconds,
                    'error_message': state.error_message,
                    'can_retry': shared.camera_state_tracker.can_retry(camera_id),
                }

        return jsonify({'success': True, 'states': states})

    except Exception as e:
        logger.error(f"Error getting batch camera states: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


########################################################
#                  STREAM LIST
########################################################

@streaming_bp.route('/api/streams')
@login_required
def api_streams():
    """Get all active streams"""
    try:
        active_streams = shared.stream_manager.get_active_streams()
        return jsonify({
            'success': True,
            'active_streams': active_streams,
            'count': len(active_streams)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@streaming_bp.route('/api/streams/active')
@login_required
def api_active_streams():
    """Get all active streams (alias)"""
    return api_streams()


@streaming_bp.route('/api/streams/stop-all', methods=['POST'])
@csrf_exempt
@login_required
def api_streams_stop_all():
    """Stop all active streams"""
    try:
        shared.stream_manager.stop_all_streams()
        return jsonify({
            'success': True,
            'message': 'All streams stopped'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


########################################################
#                  HLS PLAYLIST AND SEGMENT SERVING
########################################################

@streaming_bp.route('/api/streams/<camera_serial>/playlist.m3u8')
@login_required
def serve_playlist(camera_serial):
    """Serve HLS playlist for camera"""
    try:
        if camera_serial not in shared.stream_manager.active_streams:
            return "Stream not found", 404

        playlist_path = shared.stream_manager.active_streams[camera_serial]['playlist_path']

        if not playlist_path.exists():
            return "Playlist not found", 404

        with open(playlist_path, 'r') as f:
            content = f.read()

        return Response(
            content,
            mimetype='application/vnd.apple.mpegurl',
            headers={
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Pragma': 'no-cache',
                'Expires': '0'
            }
        )
    except Exception as e:
        return f"Error serving playlist: {e}", 500


@streaming_bp.route('/api/streams/<camera_serial>/<segment>')
@login_required
def serve_segment(camera_serial, segment):
    """Serve HLS segment for camera - supports both MPEG-TS (.ts) and fMP4 (.m4s, init.mp4)"""
    try:
        # Accept .ts (MPEG-TS), .m4s (fMP4 segments), and .mp4 (fMP4 init)
        valid_extensions = ('.ts', '.m4s', '.mp4')
        if not segment.endswith(valid_extensions):
            return "Invalid segment format", 400

        if camera_serial not in shared.stream_manager.active_streams:
            return "Stream not found", 404

        stream_dir = shared.stream_manager.active_streams[camera_serial]['stream_dir']
        segment_path = stream_dir / segment

        if not segment_path.exists():
            return "Segment not found", 404

        with open(segment_path, 'rb') as f:
            content = f.read()

        # Determine MIME type based on extension
        if segment.endswith('.ts'):
            mimetype = 'video/mp2t'
        elif segment.endswith('.m4s'):
            mimetype = 'video/iso.segment'  # Standard MIME for fMP4 segments
        elif segment.endswith('.mp4'):
            mimetype = 'video/mp4'  # For init.mp4
        else:
            mimetype = 'application/octet-stream'

        # Segments: do NOT cache (or keep it very short)
        return Response(
            content,
            mimetype=mimetype,
            headers={
                'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
                'Pragma': 'no-cache',
                'Expires': '0'
            }
        )
    except Exception as e:
        return f"Error serving segment: {e}", 500


########################################################
#                  UNIFI CAMERA ROUTES
########################################################

@streaming_bp.route('/api/unifi/cameras')
@login_required
def api_unifi_cameras():
    """Get list of UniFi cameras"""
    try:
        cameras = {}
        for camera_id, camera in shared.unifi_cameras.items():
            cameras[camera_id] = {
                'id': camera_id,
                'name': camera.name,
                'type': 'unifi',
                'session_active': camera.session_active,
                'capabilities': camera.config.get('capabilities', [])
            }
        return jsonify({'success': True, 'cameras': cameras})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@streaming_bp.route('/api/unifi/<camera_id>/snapshot')
@login_required
def api_unifi_snapshot(camera_id):
    """Get UniFi camera snapshot"""
    try:
        if camera_id not in shared.unifi_cameras:
            return "UniFi camera not found", 404

        camera = shared.unifi_cameras[camera_id]
        snapshot = camera.get_snapshot()

        if snapshot:
            return Response(snapshot, mimetype='image/jpeg')
        else:
            return "Snapshot failed", 503

    except Exception as e:
        return f"Snapshot error: {e}", 500


@streaming_bp.route('/api/unifi/<camera_id>/stream/mjpeg')
@login_required
def api_unifi_stream_mjpeg(camera_id):
    """
    MJPEG stream for UniFi cameras - Single capture, multiple clients
    Prevents resource multiplication across multiple browser connections
    """
    import logging
    logger = logging.getLogger(__name__)

    try:
        if camera_id not in shared.unifi_cameras:
            return "UniFi camera not found", 404

        camera = shared.unifi_cameras[camera_id]

        # Add this client to the capture service
        if not shared.unifi_mjpeg_capture_service.add_client(camera_id, camera):
            return "Failed to start capture service", 503

        def generate():
            """Generator serves frames from shared buffer instead of direct camera calls"""
            try:
                while True:
                    # Get frame from shared buffer instead of direct camera.get_snapshot()
                    frame_data = shared.unifi_mjpeg_capture_service.get_latest_frame(
                        camera_id)

                    if frame_data and frame_data['data']:
                        snapshot = frame_data['data']
                        yield (b'--jpgboundary\r\n'
                               b'Content-Type: image/jpeg\r\n' +
                               f'Content-Length: {len(snapshot)}\r\n\r\n'.encode() +
                               snapshot + b'\r\n')
                    else:
                        # Send small placeholder if no frame available
                        placeholder = b'No frame available'
                        yield (b'--jpgboundary\r\n'
                               b'Content-Type: text/plain\r\n' +
                               f'Content-Length: {len(placeholder)}\r\n\r\n'.encode() +
                               placeholder + b'\r\n')

                    time.sleep(0.5)  # 2 FPS

            except GeneratorExit:
                # Client disconnected
                shared.unifi_mjpeg_capture_service.remove_client(camera_id)
                logger.info(
                    f"Client disconnected from MJPEG stream {camera_id}")
            except Exception as e:
                logger.error(f"MJPEG stream error for {camera_id}: {e}")
                shared.unifi_mjpeg_capture_service.remove_client(camera_id)

        return Response(generate(),
                        mimetype='multipart/x-mixed-replace; boundary=jpgboundary')

    except Exception as e:
        shared.unifi_mjpeg_capture_service.remove_client(camera_id)
        return f"Stream error: {e}", 500


# ===== UNIFI MJPEG Capture Service Status Routes =====

@streaming_bp.route('/api/status/mjpeg-captures')
@login_required
def api_mjpeg_capture_status():
    """Get status of all MJPEG capture processes"""
    try:
        status = shared.unifi_mjpeg_capture_service.get_all_status()
        return jsonify({
            'success': True,
            'captures': status,
            'active_count': len(status)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@streaming_bp.route('/api/status/mjpeg-captures/<camera_id>')
@login_required
def api_mjpeg_capture_status_single(camera_id):
    """Get status of specific MJPEG capture process"""
    try:
        status = shared.unifi_mjpeg_capture_service.get_status(camera_id)
        if status:
            return jsonify({
                'success': True,
                'status': status
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Capture not found'
            }), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ===== UniFi Resource Monitor Routes =====

@streaming_bp.route('/api/status/unifi-monitor')
@login_required
def api_unifi_monitor_status():
    """Get UniFi resource monitor status"""
    try:
        return jsonify({
            'success': True,
            'status': shared.unifi_resource_monitor.get_status()
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@streaming_bp.route('/api/status/unifi-monitor/summary')
@login_required
def api_unifi_monitor_summary():
    """Get brief UniFi resource monitor summary"""
    try:
        return jsonify({
            'success': True,
            'summary': shared.unifi_resource_monitor.get_summary()
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@streaming_bp.route('/api/maintenance/recycle-unifi-sessions', methods=['POST'])
@csrf_exempt
@login_required
def api_recycle_unifi_sessions():
    """Manually trigger UniFi session recycling"""
    try:
        recycled = []
        for camera_id, camera in shared.unifi_cameras.items():
            try:
                camera._force_session_recycle()
                recycled.append(camera_id)
            except Exception as e:
                print(f"Error recycling {camera_id}: {e}")

        return jsonify({
            'success': True,
            'recycled_cameras': recycled,
            'count': len(recycled)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


########################################################
#         MEDIASERVER MJPEG (tap MediaMTX)
########################################################

@streaming_bp.route('/api/mediaserver/<camera_id>/stream/mjpeg')
@csrf_exempt
@login_required
def api_mediaserver_stream_mjpeg(camera_id):
    """
    MJPEG stream for cameras with mjpeg_source: "mediaserver"

    Taps the existing MediaMTX RTSP output (from dual-output FFmpeg) and
    extracts JPEG frames. Used for single-connection cameras (Eufy, SV3C,
    Neolink) that can't open a second connection for native MJPEG.

    This enables MJPEG grid view on iOS/portable devices for ALL camera types.
    """
    import logging
    logger = logging.getLogger(__name__)

    logger.info(f"Client requesting MediaServer MJPEG stream for {camera_id}")

    try:
        # Get camera configuration
        camera_config = shared.camera_repo.get_camera(camera_id)
        if not camera_config:
            return jsonify({'error': 'Camera not found'}), 404

        # Note: This endpoint taps MediaMTX RTSP for MJPEG frames.
        # Only works for cameras that publish to MediaMTX (LL_HLS, HLS, WEBRTC).
        # Cameras with stream_type: MJPEG use vendor-specific endpoints instead.

        # Add client to capture service
        if not shared.mediaserver_mjpeg_service.add_client(camera_id, camera_config):
            return jsonify({'error': 'Failed to start mediaserver MJPEG capture'}), 503

        def generate():
            """Generator serves frames from shared buffer.

            IMPORTANT: Uses try/finally to GUARANTEE remove_client() is called.
            GeneratorExit is not always raised on browser disconnect (network issues,
            tab close vs clean close). The finally block ensures cleanup even when
            generator is garbage collected without GeneratorExit.
            """
            frame_count = 0
            last_frame_number = -1
            no_frame_retries = 0
            max_no_frame_retries = 20  # 10 seconds max wait for first frame
            try:
                # Log when generator starts
                logger.info(f"MediaServer MJPEG {camera_id}: Generator started, waiting for frames...")

                while True:
                    frame_data = shared.mediaserver_mjpeg_service.get_latest_frame(camera_id)

                    if frame_data and frame_data['data']:
                        # Reset retry counter on success
                        no_frame_retries = 0

                        # Skip if same frame (avoid duplicate sends)
                        current_frame_number = frame_data.get('frame_number', 0)
                        if current_frame_number == last_frame_number and frame_count > 0:
                            time.sleep(0.1)  # Short sleep when waiting for new frame
                            continue

                        last_frame_number = current_frame_number

                        # Frame data is always JPEG (either real or error frame with text)
                        snapshot = frame_data['data']
                        frame_count += 1
                        is_error = frame_data.get('is_error', False)

                        # Log first 5 frames for debugging iOS issues
                        if frame_count <= 5:
                            logger.info(f"MediaServer MJPEG {camera_id}: Sending frame #{frame_count} ({len(snapshot)} bytes, error={is_error})")

                        yield (b'--jpgboundary\r\n'
                               b'Content-Type: image/jpeg\r\n' +
                               f'Content-Length: {len(snapshot)}\r\n\r\n'.encode() +
                               snapshot + b'\r\n')
                    else:
                        no_frame_retries += 1
                        if frame_count == 0:
                            if no_frame_retries <= 3:
                                logger.info(f"MediaServer MJPEG {camera_id}: Waiting for first frame (attempt {no_frame_retries})...")
                            elif no_frame_retries == max_no_frame_retries:
                                logger.warning(f"MediaServer MJPEG {camera_id}: Gave up waiting for frames after {max_no_frame_retries} attempts")
                                # Create and send a "No Signal" frame before giving up
                                from services.mediaserver_mjpeg_service import _create_error_frame
                                no_signal_frame = _create_error_frame(f"No signal from {camera_id}")
                                yield (b'--jpgboundary\r\n'
                                       b'Content-Type: image/jpeg\r\n' +
                                       f'Content-Length: {len(no_signal_frame)}\r\n\r\n'.encode() +
                                       no_signal_frame + b'\r\n')
                                return  # End generator - finally block will clean up

                    time.sleep(0.5)  # 2 FPS

            except GeneratorExit:
                logger.info(f"Client disconnected from MediaServer MJPEG stream {camera_id} after {frame_count} frames (GeneratorExit)")
            except Exception as e:
                logger.error(f"MediaServer MJPEG stream error for {camera_id}: {e}")
            finally:
                # ALWAYS remove client - handles all exit paths:
                # - Normal GeneratorExit on browser disconnect
                # - Early return when no frames received
                # - Exceptions during frame serving
                # - Generator garbage collection (browser closed without clean disconnect)
                shared.mediaserver_mjpeg_service.remove_client(camera_id)
                logger.debug(f"MediaServer MJPEG {camera_id}: Generator cleanup complete (served {frame_count} frames)")

        response = Response(generate(),
                            mimetype='multipart/x-mixed-replace; boundary=jpgboundary')
        # Disable buffering at all layers for streaming
        response.headers['X-Accel-Buffering'] = 'no'
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
        return response

    except Exception as e:
        shared.mediaserver_mjpeg_service.remove_client(camera_id)
        return jsonify({'error': f'Stream error: {e}'}), 500


@streaming_bp.route('/api/status/mediaserver-mjpeg')
@login_required
def api_mediaserver_mjpeg_status():
    """Get status of all MediaServer MJPEG capture processes"""
    try:
        status = shared.mediaserver_mjpeg_service.get_all_status()
        return jsonify({
            'success': True,
            'captures': status,
            'active_count': len(status)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@streaming_bp.route('/api/status/mediaserver-mjpeg/<camera_id>')
@login_required
def api_mediaserver_mjpeg_status_single(camera_id):
    """Get status of specific MediaServer MJPEG capture process"""
    try:
        status = shared.mediaserver_mjpeg_service.get_status(camera_id)
        if status:
            return jsonify({
                'success': True,
                'status': status
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Capture not found'
            }), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


########################################################
#                  SNAPSHOT ROUTE
########################################################

@streaming_bp.route('/api/snap/<camera_id>')
@csrf_exempt
@login_required
def api_snap_camera(camera_id):
    """
    Get a single JPEG snapshot from any camera.
    Used for iOS grid view (polling snapshots instead of MJPEG streams).

    Checks frame buffers in order:
    1. reolink_mjpeg_capture_service (for Reolink cameras)
    2. mediaserver_mjpeg_service (for eufy, sv3c, neolink via MediaMTX)
    3. unifi_mjpeg_service (for UniFi cameras)

    Returns latest cached frame if available, or 503 if no frame ready.
    """
    import logging
    logger = logging.getLogger(__name__)

    try:
        camera = shared.camera_repo.get_camera(camera_id)
        if not camera:
            return "Camera not found", 404

        camera_type = camera.get('type', '').lower()
        frame_data = None

        # Try camera-specific service first
        if camera_type == 'reolink':
            frame_data = shared.reolink_mjpeg_capture_service.get_latest_frame(camera_id)
        elif camera_type == 'unifi':
            # UniFi uses MJPEG capture service
            frame_data = shared.unifi_mjpeg_capture_service.get_latest_frame(camera_id)
        elif camera_type == 'sv3c':
            # SV3C uses direct HTTP snapshots (/tmpfs/auto.jpg)
            frame_data = shared.sv3c_mjpeg_capture_service.get_latest_frame(camera_id)

        # Fallback to mediaserver (works for any camera with HLS running)
        if not frame_data:
            frame_data = shared.mediaserver_mjpeg_service.get_latest_frame(camera_id)

        if frame_data and frame_data.get('data'):
            return Response(
                frame_data['data'],
                mimetype='image/jpeg',
                headers={
                    'Cache-Control': 'no-cache, no-store, must-revalidate',
                    'Pragma': 'no-cache',
                    'Expires': '0'
                }
            )
        else:
            # No frame available - return 503 so client knows to retry
            return "No frame available", 503

    except Exception as e:
        logger.error(f"Snapshot error for {camera_id}: {e}")
        return f"Snapshot error: {e}", 500


########################################################
#                  REOLINK MJPEG ROUTES
########################################################

@streaming_bp.route('/api/reolink/<camera_id>/stream/mjpeg')
@csrf_exempt
@login_required
def api_reolink_stream_mjpeg_default(camera_id):
    """Route Reolink MJPEG stream to sub or main based on 'stream' query param."""
    stream = request.args.get('stream', 'sub')
    if stream == 'sub':
        return _api_reolink_stream_mjpeg_sub(camera_id)
    else:
        return _api_reolink_stream_mjpeg_main(camera_id)


def _api_reolink_stream_mjpeg_sub(camera_id):
    """
    MJPEG stream for Reolink cameras via Snap API polling
    Uses capture service for single-source, multi-client architecture
    """
    import logging
    logger = logging.getLogger(__name__)

    logger.info(f"Client requesting Reolink MJPEG stream for {camera_id}")

    try:
        # Get camera configuration
        camera = shared.camera_repo.get_camera(camera_id)
        if not camera:
            logger.error(f"Camera {camera_id} not found")
            return "Camera not found", 404

        if camera.get('type') != 'reolink':
            logger.error(f"Camera {camera_id} is not a Reolink camera")
            return "Not a Reolink camera", 400

        # Get MJPEG snap configuration
        mjpeg_config = camera.get('mjpeg_snap', {})
        sub_config = mjpeg_config.get('sub', mjpeg_config)  # Fall back to old format
        if not sub_config.get('enabled', True):
            logger.warning(f"MJPEG snap not enabled for camera {camera_id}")
            return "MJPEG snap not enabled for this camera", 400

        # CREATE THIS PART:
        camera_with_sub = camera.copy()
        camera_with_sub['mjpeg_snap'] = sub_config
        camera_with_sub['mjpeg_snap']['snap_type'] = 'sub'

        # Add client to capture service (starts capture if first client)
        if not shared.reolink_mjpeg_capture_service.add_client(camera_id, camera_with_sub, shared.camera_repo):
            logger.error(f"Failed to add client for Reolink MJPEG stream {camera_id}")
            return "Failed to start capture", 500

        def generate():
            """Generator reads from shared frame buffer"""
            logger.info(f"[Reolink MJPEG] Client connected to {camera_id}")

            try:
                last_frame_number = -1

                while True:
                    # Get latest frame from shared buffer
                    frame_data = shared.reolink_mjpeg_capture_service.get_latest_frame(camera_id)

                    if frame_data:
                        # Only yield if we have a new frame
                        if frame_data['frame_number'] != last_frame_number:
                            snapshot = frame_data['data']
                            last_frame_number = frame_data['frame_number']

                            # Yield MJPEG frame
                            yield (b'--jpgboundary\r\n'
                                   b'Content-Type: image/jpeg\r\n' +
                                   f'Content-Length: {len(snapshot)}\r\n\r\n'.encode() +
                                   snapshot + b'\r\n')

                    # Small sleep to prevent tight loop
                    time.sleep(0.033)  # ~30 FPS check rate

            except GeneratorExit:
                # Client disconnected - remove from service
                logger.info(f"[Reolink MJPEG] Client disconnected from {camera_id}")
                shared.reolink_mjpeg_capture_service.remove_client(camera_id)
            except Exception as e:
                logger.error(f"[Reolink MJPEG] Stream error for {camera_id}: {e}")
                shared.reolink_mjpeg_capture_service.remove_client(camera_id)

        return Response(generate(),
                        mimetype='multipart/x-mixed-replace; boundary=jpgboundary')

    except Exception as e:
        logger.error(f"Failed to start Reolink MJPEG stream for {camera_id}: {e}")
        return f"Stream error: {e}", 500


def _api_reolink_stream_mjpeg_main(camera_id):
    """
    MJPEG main stream for Reolink cameras (fullscreen mode)
    Uses higher resolution but more bandwidth
    """
    import logging
    logger = logging.getLogger(__name__)

    logger.info(f"Client requesting Reolink MJPEG MAIN stream for {camera_id}")

    # Same implementation as sub stream but pass stream_type='main'
    try:
        camera = shared.camera_repo.get_camera(camera_id)
        if not camera:
            return "Camera not found", 404

        if camera.get('type') != 'reolink':
            return "Not a Reolink camera", 400

        mjpeg_snap = camera.get('mjpeg_snap', {})
        main_config = mjpeg_snap.get('main', mjpeg_snap.get('sub', mjpeg_snap))

        if not main_config.get('enabled', True):
            return "MJPEG snap not enabled for this camera", 400

        camera_main = camera.copy()
        camera_main['mjpeg_snap'] = main_config.copy()
        camera_main['mjpeg_snap']['snap_type'] = 'main'

        camera_id_main = f"{camera_id}_main"

        if not shared.reolink_mjpeg_capture_service.add_client(camera_id_main, camera_main, shared.camera_repo):
            return "Failed to start capture", 500

        def generate():
            try:
                last_frame_number = -1
                while True:
                    frame_data = shared.reolink_mjpeg_capture_service.get_latest_frame(camera_id_main)
                    if frame_data and frame_data['frame_number'] != last_frame_number:
                        snapshot = frame_data['data']
                        last_frame_number = frame_data['frame_number']
                        yield (b'--jpgboundary\r\n'
                               b'Content-Type: image/jpeg\r\n' +
                               f'Content-Length: {len(snapshot)}\r\n\r\n'.encode() +
                               snapshot + b'\r\n')
                    time.sleep(0.033)
            except GeneratorExit:
                shared.reolink_mjpeg_capture_service.remove_client(camera_id_main)
            except Exception as e:
                logger.error(f"[Reolink MJPEG MAIN] Error {camera_id}: {e}")
                shared.reolink_mjpeg_capture_service.remove_client(camera_id_main)

        return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=jpgboundary')
    except Exception as e:
        return f"Stream error: {e}", 500


########################################################
#         SV3C MJPEG STREAM ROUTES (hi3510 chipset)
########################################################

@streaming_bp.route('/api/sv3c/<camera_id>/stream/mjpeg')
@csrf_exempt
@login_required
def api_sv3c_stream_mjpeg_default(camera_id):
    """
    MJPEG stream for SV3C cameras via snapshot polling.
    SV3C uses hi3510 chipset with CGI snapshot endpoints.
    Bypasses unstable RTSP by polling snapshots directly.
    """
    stream = request.args.get('stream', 'sub')
    if stream == 'sub':
        return _api_sv3c_stream_mjpeg_sub(camera_id)
    else:
        return _api_sv3c_stream_mjpeg_main(camera_id)


def _api_sv3c_stream_mjpeg_sub(camera_id):
    """
    MJPEG sub stream for SV3C cameras via snapshot polling.
    Uses single-source, multi-client architecture.
    """
    import logging
    logger = logging.getLogger(__name__)

    logger.info(f"Client requesting SV3C MJPEG stream for {camera_id}")

    try:
        camera = shared.camera_repo.get_camera(camera_id)
        if not camera:
            logger.error(f"Camera {camera_id} not found")
            return "Camera not found", 404

        if camera.get('type') != 'sv3c':
            logger.error(f"Camera {camera_id} is not a SV3C camera (type={camera.get('type')})")
            return "Not a SV3C camera", 400

        # Get MJPEG snap configuration
        mjpeg_config = camera.get('mjpeg_snap', {})
        sub_config = mjpeg_config.get('sub', mjpeg_config)
        if not sub_config.get('enabled', True):
            logger.warning(f"MJPEG snap not enabled for camera {camera_id}")
            return "MJPEG snap not enabled for this camera", 400

        # Build camera config with sub stream settings
        camera_with_sub = camera.copy()
        camera_with_sub['mjpeg_snap'] = sub_config
        camera_with_sub['mjpeg_snap']['snap_type'] = 'sub'

        # Add client to capture service
        if not shared.sv3c_mjpeg_capture_service.add_client(camera_id, camera_with_sub, shared.camera_repo):
            logger.error(f"Failed to add client for SV3C MJPEG stream {camera_id}")
            return "Failed to start capture", 500

        def generate():
            """Generator reads from shared frame buffer"""
            logger.info(f"[SV3C MJPEG] Client connected to {camera_id}")

            try:
                last_frame_number = -1

                while True:
                    frame_data = shared.sv3c_mjpeg_capture_service.get_latest_frame(camera_id)

                    if frame_data:
                        if frame_data['frame_number'] != last_frame_number:
                            snapshot = frame_data['data']
                            last_frame_number = frame_data['frame_number']

                            yield (b'--jpgboundary\r\n'
                                   b'Content-Type: image/jpeg\r\n' +
                                   f'Content-Length: {len(snapshot)}\r\n\r\n'.encode() +
                                   snapshot + b'\r\n')

                    time.sleep(0.033)  # ~30 FPS check rate

            except GeneratorExit:
                logger.info(f"[SV3C MJPEG] Client disconnected from {camera_id}")
                shared.sv3c_mjpeg_capture_service.remove_client(camera_id)
            except Exception as e:
                logger.error(f"[SV3C MJPEG] Stream error for {camera_id}: {e}")
                shared.sv3c_mjpeg_capture_service.remove_client(camera_id)

        return Response(generate(),
                        mimetype='multipart/x-mixed-replace; boundary=jpgboundary')

    except Exception as e:
        logger.error(f"Failed to start SV3C MJPEG stream for {camera_id}: {e}")
        return f"Stream error: {e}", 500


def _api_sv3c_stream_mjpeg_main(camera_id):
    """
    MJPEG main stream for SV3C cameras (fullscreen mode).
    Uses higher resolution but more bandwidth.
    """
    import logging
    logger = logging.getLogger(__name__)

    logger.info(f"Client requesting SV3C MJPEG MAIN stream for {camera_id}")

    try:
        camera = shared.camera_repo.get_camera(camera_id)
        if not camera:
            return "Camera not found", 404

        if camera.get('type') != 'sv3c':
            return "Not a SV3C camera", 400

        mjpeg_snap = camera.get('mjpeg_snap', {})
        main_config = mjpeg_snap.get('main', mjpeg_snap.get('sub', mjpeg_snap))

        if not main_config.get('enabled', True):
            return "MJPEG snap not enabled for this camera", 400

        camera_main = camera.copy()
        camera_main['mjpeg_snap'] = main_config.copy()
        camera_main['mjpeg_snap']['snap_type'] = 'main'

        camera_id_main = f"{camera_id}_main"

        if not shared.sv3c_mjpeg_capture_service.add_client(camera_id_main, camera_main, shared.camera_repo):
            return "Failed to start capture", 500

        def generate():
            try:
                last_frame_number = -1
                while True:
                    frame_data = shared.sv3c_mjpeg_capture_service.get_latest_frame(camera_id_main)
                    if frame_data and frame_data['frame_number'] != last_frame_number:
                        snapshot = frame_data['data']
                        last_frame_number = frame_data['frame_number']
                        yield (b'--jpgboundary\r\n'
                               b'Content-Type: image/jpeg\r\n' +
                               f'Content-Length: {len(snapshot)}\r\n\r\n'.encode() +
                               snapshot + b'\r\n')
                    time.sleep(0.033)
            except GeneratorExit:
                shared.sv3c_mjpeg_capture_service.remove_client(camera_id_main)
            except Exception as e:
                logger.error(f"[SV3C MJPEG MAIN] Error {camera_id}: {e}")
                shared.sv3c_mjpeg_capture_service.remove_client(camera_id_main)

        return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=jpgboundary')
    except Exception as e:
        return f"Stream error: {e}", 500


########################################################
#         WEBSOCKET MJPEG (MULTIPLEXED) — /mjpeg
########################################################

@shared.socketio.on('connect', namespace='/mjpeg')
def ws_mjpeg_connect():
    """
    Handle WebSocket connection for MJPEG streaming.

    Client connects to /mjpeg namespace, then emits 'subscribe' with camera list.
    This bypasses browser's ~6 HTTP connection limit by multiplexing all camera
    streams over a single WebSocket connection.
    """
    from flask import request as flask_request
    sid = flask_request.sid
    import logging
    logging.getLogger(__name__).info(f"WebSocket MJPEG: Client {sid[:8]}... connected")
    emit('connected', {'status': 'ok', 'sid': sid})


@shared.socketio.on('disconnect', namespace='/mjpeg')
def ws_mjpeg_disconnect():
    """Handle WebSocket disconnection"""
    from flask import request as flask_request
    sid = flask_request.sid
    shared.websocket_mjpeg_service.remove_client(sid)
    import logging
    logging.getLogger(__name__).info(f"WebSocket MJPEG: Client {sid[:8]}... disconnected")


@shared.socketio.on('subscribe', namespace='/mjpeg')
def ws_mjpeg_subscribe(data):
    """
    Subscribe client to camera streams.

    Args:
        data: {'cameras': ['serial1', 'serial2', ...]}

    The server will begin sending mjpeg_frames events containing:
    {'frames': [{'camera_id': 'serial1', 'frame': 'base64...', 'frame_num': 1}, ...]}
    """
    from flask import request as flask_request
    sid = flask_request.sid

    import logging
    logger = logging.getLogger(__name__)

    camera_ids = data.get('cameras', [])
    if not camera_ids:
        emit('error', {'message': 'No cameras specified'})
        return

    # Validate cameras exist
    valid_cameras = []
    for camera_id in camera_ids:
        if shared.camera_repo.get_camera(camera_id):
            valid_cameras.append(camera_id)
        else:
            logger.warning(f"WebSocket MJPEG: Unknown camera {camera_id}")

    if not valid_cameras:
        emit('error', {'message': 'No valid cameras specified'})
        return

    # Register client subscription
    shared.websocket_mjpeg_service.add_client(sid, valid_cameras)

    emit('subscribed', {
        'cameras': valid_cameras,
        'count': len(valid_cameras)
    })


@shared.socketio.on('unsubscribe', namespace='/mjpeg')
def ws_mjpeg_unsubscribe(data=None):
    """Unsubscribe client from all camera streams"""
    from flask import request as flask_request
    sid = flask_request.sid
    shared.websocket_mjpeg_service.remove_client(sid)
    emit('unsubscribed', {'status': 'ok'})


########################################################
#  STREAM EVENTS WEBSOCKET — /stream_events
#  Notify frontend of backend stream restarts in real-time.
#  Frontend connects here to receive stream_restarted events
#  from StreamWatchdog instead of waiting for 10s polling.
########################################################

@shared.socketio.on('connect', namespace='/stream_events')
def handle_stream_events_connect():
    """
    Handle WebSocket connection for stream event notifications.

    Frontend connects to /stream_events namespace to receive real-time
    notifications when StreamWatchdog restarts a stream.
    """
    from flask import request as flask_request
    sid = flask_request.sid
    import logging
    logging.getLogger(__name__).info(f"StreamEvents: Client {sid[:8]}... connected")
    emit('connected', {'status': 'ok', 'sid': sid})


@shared.socketio.on('disconnect', namespace='/stream_events')
def handle_stream_events_disconnect():
    """Handle WebSocket disconnection from stream events namespace"""
    from flask import request as flask_request
    sid = flask_request.sid
    import logging
    logging.getLogger(__name__).info(f"StreamEvents: Client {sid[:8]}... disconnected")
