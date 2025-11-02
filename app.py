#!/usr/bin/env python3
"""
Unified NVR Flask Application - Complete Merged Version
Combines refactored architecture with all operational routes
"""

import os
import sys
from dotenv import load_dotenv
import logging
import socket
import functools
import requests
import subprocess
import signal
import atexit
import time
import traceback
from threading import Thread

from flask import Flask, render_template, jsonify, request, Response, redirect
from flask_wtf import FlaskForm
from flask_wtf.csrf import CSRFProtect
from wtforms import SelectField, SubmitField
from wtforms.validators import DataRequired

# modular imports
from services.camera_repository import CameraRepository
from services.ptz.ptz_validator import PTZValidator
from streaming.stream_manager import StreamManager

from low_level_handlers.process_reaper import install_sigchld_handler

from eufy_bridge import EufyBridge
from eufy_bridge_watchdog import BridgeWatchdog
from services.unifi_protect_service import UniFiProtectService
from services.unifi_service_resource_monitor import UniFiServiceResourceMonitor
from services.app_restart_handler import AppRestartHandler
from services.unifi_mjpeg_capture_service import unifi_mjpeg_capture_service
from services.amcrest_mjpeg_capture_service import amcrest_mjpeg_capture_service
from services.reolink_mjpeg_capture_service import reolink_mjpeg_capture_service
from services.ptz.amcrest_ptz_handler import amcrest_ptz_handler
from services.onvif.onvif_ptz_handler import ONVIFPTZHandler

from low_level_handlers.cleanup_handler import stop_all_services, kill_all, kill_ffmpeg

# Flask app setup
app = Flask(__name__)
app.config['SECRET_KEY'] = '-secret-key-change-this'
csrf = CSRFProtect(app)

logger = logging.getLogger('werkzeug')
logger.setLevel(logging.WARNING)

load_dotenv()  # loads .env from the current working directory / project root

TRUE_SET = {"1", "true", "yes", "on"}
FALSE_SET = {"0", "false", "no", "off"}

print("=" * 80)
print("🚀 Starting Unified NVR Server - Refactored Architecture")
print("=" * 80)

# ===== Initialize Core Services =====
try:
    print("\n📦 Initializing core services...")

    # Camera repository
    camera_repo = CameraRepository('./config')
    print(
        f"✅ Camera repository loaded: {camera_repo.get_camera_count()} cameras")

    # PTZ validator
    ptz_validator = PTZValidator(camera_repo)
    print("✅ PTZ validator initialized")



    # Stream manager (creates credential providers internally)
    stream_manager = StreamManager(camera_repo=camera_repo)
    # remove any pre-existing stream segments and playlists.
    stream_manager.cleanup_stream_files()
    # ensure streaming dir ownership on init (especially during flask reload during dev)
    stream_manager._ensure_streams_directory_ownership()
    # stream_manager._remove_recreate_stream_dir() # DEPRECATED but keep so we remember this.
    print("✅ Stream manager initialized")
    
    install_sigchld_handler()

    # Eufy bridge for PTZ control
    eufy_bridge = EufyBridge()
    bridge_watchdog = BridgeWatchdog(eufy_bridge)
    print("✅ Eufy bridge initialized")

    print("\n✅ All core services initialized successfully!\n")

except Exception as e:
    print(f"\n❌ Failed to initialize services: {e}")
    print(traceback.print_exc())
    exit(1)

# ===== Initialize UniFi Cameras (MJPEG fallback) =====

unifi_cameras = {}
try:
    print("🔵 Loading UniFi cameras...")
    for camera_id, config in camera_repo.get_cameras_by_type('unifi').items():
        config['id'] = camera_id
        unifi_cameras[camera_id] = UniFiProtectService(config)
        print(f"  ✅ {config['name']}")
except Exception as e:
    print(f"⚠️  UniFi camera initialization warning: {e}")

# ===== Auto-start Bridge and Streams =====


def wait_for_bridge_ready(timeout=5):
    """Wait for bridge to be ready"""
    if os.getenv('USE_EUFY_BRIDGE', False).lower() in ['1', 'true']:
        t0 = time.time()
        while time.time() - t0 < timeout:
            if eufy_bridge.is_running():
                return True
            time.sleep(0.25)
    return False


try:
    print("\n🌉 Starting Eufy bridge...")
    if os.getenv('USE_EUFY_BRIDGE', False).lower() in ['1', 'true']:
        if not eufy_bridge.is_running():
            eufy_bridge.start()
            if wait_for_bridge_ready():
                print("✅ Bridge started successfully")

        bridge_watchdog.start_monitoring()
        print("✅ Bridge watchdog started")

    print("\n🎬 Auto-starting camera streams...")
    for serial, camera in camera_repo.get_streaming_cameras().items():
        try:
            Thread(target=stream_manager.start_stream,
                   args=(serial,), daemon=True).start()
            time.sleep(0.4)
            print(f"  ✅ Started: {camera['name']}")
        except Exception as e:
            print(f"  ⚠️  Failed to start {camera['name']}: {e}")

except Exception as e:
    print(f"⚠️  Bridge/streaming startup warning: {e}")

# ===== Initialize Monitoring Services =====

try:
    restart_handler = AppRestartHandler(stream_manager, bridge_watchdog, eufy_bridge)

    if unifi_cameras:
        unifi_resource_monitor = UniFiServiceResourceMonitor(
            unifi_cameras,
            app_restart_callback=restart_handler.restart_app
        )
        unifi_resource_monitor.start_monitoring()
        print("✅ UniFi resource monitoring started")

except Exception as e:
    print(f"⚠️  Monitoring service warning: {e}")

print("\n" + "=" * 80)
print("🎉 Server ready!")
print("=" * 80 + "\n")

# ===== Flask Forms =====

class PTZControlForm(FlaskForm):
    """Form for PTZ camera selection"""
    camera = SelectField('Camera', validators=[DataRequired()])
    submit = SubmitField('Select Camera')

# ===== Main UI Routes =====

@app.route('/')
def index():
    """Redirect to streams page (main interface)"""
    return redirect('/streams')

@app.route('/streams')
def streams_page():
    """Multi-stream viewing page"""
    try:
        cameras = camera_repo.get_streaming_cameras()
        ui_health = _ui_health_from_env()
        
        # Pass full camera configs (includes ui_health_monitor per camera)
        return render_template('streams.html', cameras=cameras, ui_health=ui_health)
    except Exception as e:
        print(f"Error loading streams page: {e}")
        return f"Error loading streams page: {e}", 500

# ===== Status Routes =====

@app.route('/api/status')
def api_status():
    """Get system status"""
    eufy_status = {
        'bridge_running': eufy_bridge.is_running(),
        'bridge_ready': eufy_bridge.is_ready(),
        'total_devices': camera_repo.get_camera_count(),
        'ptz_cameras': len(camera_repo.get_ptz_cameras())
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
            'active': stream_manager.get_active_streams(),
            'total_streaming_cameras': len(camera_repo.get_streaming_cameras())
        }
    })


@app.route('/api/cameras')
def api_cameras():
    """Get list of available cameras"""
    return jsonify({
        'all': camera_repo.get_all_cameras(),
        'ptz': camera_repo.get_ptz_cameras(),
        'streaming': camera_repo.get_streaming_cameras()
    })
    
@app.route('/api/cameras/<camera_id>')
def api_camera_detail(camera_id):
    """Get single camera configuration"""
    try:
        # Get camera from repository
        camera = camera_repo.get_camera(camera_id)
        
        if not camera:
            return jsonify({'error': 'Camera not found'}), 404
        
        return jsonify(camera)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ===== Bridge Control Routes =====


@app.route('/api/bridge/start', methods=['POST'])
@csrf.exempt
def api_bridge_start():
    """Start the Eufy bridge"""
    try:
        success = eufy_bridge.start()
        return jsonify({
            'success': success,
            'message': 'Bridge started successfully' if success else 'Failed to start bridge'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/bridge/stop', methods=['POST'])
@csrf.exempt
def api_bridge_stop():
    """Stop the Eufy bridge"""
    try:
        eufy_bridge.stop()
        return jsonify({'success': True, 'message': 'Bridge stopped'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ===== Device Management Routes =====


@app.route('/api/devices/refresh', methods=['POST'])
@csrf.exempt
def api_refresh_devices():
    """Refresh device list from bridge (Eufy only for now)"""
    try:
        # TODO: Implement device discovery with new architecture
        # For now, just reload configs
        camera_repo.reload()

        return jsonify({
            'success': True,
            'total_devices': camera_repo.get_camera_count(),
            'ptz_cameras': len(camera_repo.get_ptz_cameras())
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ===== Streaming Routes =====

# RTMP
@app.route('/api/camera/<camera_serial>/flv')
@csrf.exempt
def serve_camera_flv(camera_serial):
    """Serve FLV stream from already-running RTMP process"""
    
    # Get process from StreamManager WITH LOCK
    with stream_manager._streams_lock:
        stream_info = stream_manager.active_streams.get(camera_serial)
        
        if not stream_info:
            return jsonify({'error': 'Stream not started'}), 404
        
        if stream_info.get('protocol') != 'rtmp':
            return jsonify({'error': 'Not an RTMP stream'}), 400
        
        process = stream_info.get('process')
        
        if not process or process.poll() is not None:
            return jsonify({'error': 'Stream process not running'}), 500
        
        # Get reference to process INSIDE the lock
        # (the process object itself is thread-safe once we have it)
    
    # Now stream OUTSIDE the lock (don't hold lock during streaming!)
    def generate():
        try:
            while True:
                chunk = process.stdout.read(8192)
                if not chunk:
                    break
                yield chunk
        except Exception as e:
            logger.error(f"FLV streaming error for {camera_serial}: {e}")
    
    return Response(
        generate(),
        mimetype='video/x-flv',
        headers={
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Connection': 'keep-alive'
        }
    )

@app.route('/api/stream/start/<camera_serial>', methods=['POST'])
@csrf.exempt
def api_stream_start(camera_serial):
    """Start HLS stream for camera"""
    try:
        # Get camera (includes hidden cameras)
        camera = camera_repo.get_camera(camera_serial)

        # Early rejection
        if not camera or camera.get('hidden', False):
            logger.warning(f"API access denied: Camera {camera_serial} not found or hidden")
            return jsonify({
                'success': False,
                'error': 'Camera not found or not accessible'
            }), 404

        camera_name = camera.get('name', camera_serial)
        if not camera_name:
            return jsonify({'success': False, 'error': 'Camera not found'}), 404

        print(f"Attempting to start camera {camera_serial} - {camera_name}")

        # Validate streaming capability
        if not ptz_validator.is_streaming_capable(camera_serial):
            return jsonify({'success': False, 'error': 'Camera does not support streaming'}), 400

        # Extract stream type from request (defaults to 'sub' for grid view)
        data = request.get_json() or {}
        stream_type = data.get('type', 'sub')  # 'main' or 'sub'

        # Start the stream with specified type
        stream_url = stream_manager.start_stream(
            camera_serial, stream_type=stream_type)

        if not stream_url:
            return jsonify({'success': False, 'error': 'Failed to start stream'}), 500

        return jsonify({
            'success': True,
            'camera_serial': camera_serial,
            'camera_name': camera_name,
            'stream_url': stream_url,
            'message': f'Stream started for {camera_name}'
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e),
            'camera_serial': camera_serial
        }), 500


@app.route('/api/stream/stop/<camera_serial>', methods=['POST'])
@csrf.exempt
def api_stream_stop(camera_serial):
    """Stop HLS stream for camera"""
    try:
        camera_name = camera_repo.get_camera_name(camera_serial)
        success = stream_manager.stop_stream(camera_serial)

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


@app.route('/api/stream/status/<camera_serial>')
def api_stream_status(camera_serial):
    """Get stream status for camera"""
    try:
        is_alive = stream_manager.is_stream_alive(camera_serial)
        stream_url = stream_manager.get_stream_url(
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


@app.route('/api/streams')
def api_streams():
    """Get all active streams"""
    try:
        active_streams = stream_manager.get_active_streams()
        return jsonify({
            'success': True,
            'active_streams': active_streams,
            'count': len(active_streams)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/streams/active')
def api_active_streams():
    """Get all active streams (alias)"""
    return api_streams()


@app.route('/api/streams/stop-all', methods=['POST'])
@csrf.exempt
def api_streams_stop_all():
    """Stop all active streams"""
    try:
        stream_manager.stop_all_streams()
        return jsonify({
            'success': True,
            'message': 'All streams stopped'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ===== HLS Playlist and Segment Serving =====


@app.route('/api/streams/<camera_serial>/playlist.m3u8')
def serve_playlist(camera_serial):
    """Serve HLS playlist for camera"""
    try:
        if camera_serial not in stream_manager.active_streams:
            return "Stream not found", 404

        playlist_path = stream_manager.active_streams[camera_serial]['playlist_path']

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


@app.route('/api/streams/<camera_serial>/<segment>')
def serve_segment(camera_serial, segment):
    """Serve HLS segment for camera - supports both MPEG-TS (.ts) and fMP4 (.m4s, init.mp4)"""
    try:
        # Accept .ts (MPEG-TS), .m4s (fMP4 segments), and .mp4 (fMP4 init)
        valid_extensions = ('.ts', '.m4s', '.mp4')
        if not segment.endswith(valid_extensions):
            return "Invalid segment format", 400

        if camera_serial not in stream_manager.active_streams:
            return "Stream not found", 404

        stream_dir = stream_manager.active_streams[camera_serial]['stream_dir']
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
# ===== UniFi Camera Routes =====


@app.route('/api/unifi/cameras')
def api_unifi_cameras():
    """Get list of UniFi cameras"""
    try:
        cameras = {}
        for camera_id, camera in unifi_cameras.items():
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


@app.route('/api/unifi/<camera_id>/snapshot')
def api_unifi_snapshot(camera_id):
    """Get UniFi camera snapshot"""
    try:
        if camera_id not in unifi_cameras:
            return "UniFi camera not found", 404

        camera = unifi_cameras[camera_id]
        snapshot = camera.get_snapshot()

        if snapshot:
            return Response(snapshot, mimetype='image/jpeg')
        else:
            return "Snapshot failed", 503

    except Exception as e:
        return f"Snapshot error: {e}", 500


@app.route('/api/unifi/<camera_id>/stream/mjpeg')
def api_unifi_stream_mjpeg(camera_id):
    """
    MJPEG stream for UniFi cameras - Single capture, multiple clients
    Prevents resource multiplication across multiple browser connections
    """
    try:
        if camera_id not in unifi_cameras:
            return "UniFi camera not found", 404

        camera = unifi_cameras[camera_id]

        # Add this client to the capture service
        if not unifi_mjpeg_capture_service.add_client(camera_id, camera):
            return "Failed to start capture service", 503

        def generate():
            """Generator serves frames from shared buffer instead of direct camera calls"""
            try:
                while True:
                    # Get frame from shared buffer instead of direct camera.get_snapshot()
                    frame_data = unifi_mjpeg_capture_service.get_latest_frame(
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
                unifi_mjpeg_capture_service.remove_client(camera_id)
                logger.info(
                    f"Client disconnected from MJPEG stream {camera_id}")
            except Exception as e:
                logger.error(f"MJPEG stream error for {camera_id}: {e}")
                unifi_mjpeg_capture_service.remove_client(camera_id)

        return Response(generate(),
                        mimetype='multipart/x-mixed-replace; boundary=jpgboundary')

    except Exception as e:
        unifi_mjpeg_capture_service.remove_client(camera_id)
        return f"Stream error: {e}", 500

# ===== UNIFI MJPEG Capture Service Status Routes =====

@app.route('/api/status/mjpeg-captures')
def api_mjpeg_capture_status():
    """Get status of all MJPEG capture processes"""
    try:
        status = unifi_mjpeg_capture_service.get_all_status()
        return jsonify({
            'success': True,
            'captures': status,
            'active_count': len(status)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/status/mjpeg-captures/<camera_id>')
def api_mjpeg_capture_status_single(camera_id):
    """Get status of specific MJPEG capture process"""
    try:
        status = unifi_mjpeg_capture_service.get_status(camera_id)
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

@app.route('/api/status/unifi-monitor')
def api_unifi_monitor_status():
    """Get UniFi resource monitor status"""
    try:
        return jsonify({
            'success': True,
            'status': unifi_resource_monitor.get_status()
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/status/unifi-monitor/summary')
def api_unifi_monitor_summary():
    """Get brief UniFi resource monitor summary"""
    try:
        return jsonify({
            'success': True,
            'summary': unifi_resource_monitor.get_summary()
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/maintenance/recycle-unifi-sessions', methods=['POST'])
@csrf.exempt
def api_recycle_unifi_sessions():
    """Manually trigger UniFi session recycling"""
    try:
        recycled = []
        for camera_id, camera in unifi_cameras.items():
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


# ===== REOLINK MJPEG Capture Service Status Routes =====
@app.route('/api/reolink/<camera_id>/stream/mjpeg')
@csrf.exempt
def api_reolink_stream_mjpeg_sub(camera_id):
    """
    MJPEG stream for Reolink cameras via Snap API polling
    Uses capture service for single-source, multi-client architecture
    """
    logger.info(f"Client requesting Reolink MJPEG stream for {camera_id}")
    
    try:
        # Get camera configuration
        camera = camera_repo.get_camera(camera_id)
        if not camera:
            logger.error(f"Camera {camera_id} not found")
            return "Camera not found", 404
        
        if camera.get('type') != 'reolink':
            logger.error(f"Camera {camera_id} is not a Reolink camera")
            return "Not a Reolink camera", 400
        
        # # Get MJPEG snap configuration
        # mjpeg_config = camera.get('mjpeg_snap', {})
        # if not mjpeg_config.get('enabled', True):
        #     logger.warning(f"MJPEG snap not enabled for camera {camera_id}")
        #     return "MJPEG snap not enabled for this camera", 400
        
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
        if not reolink_mjpeg_capture_service.add_client(camera_id, camera_with_sub, camera_repo):
            logger.error(f"Failed to add client for Reolink MJPEG stream {camera_id}")
            return "Failed to start capture", 500
        
        def generate():
            """Generator reads from shared frame buffer"""
            logger.info(f"[Reolink MJPEG] Client connected to {camera_id}")
            
            try:
                last_frame_number = -1
                
                while True:
                    # Get latest frame from shared buffer
                    frame_data = reolink_mjpeg_capture_service.get_latest_frame(camera_id)
                    
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
                reolink_mjpeg_capture_service.remove_client(camera_id)
            except Exception as e:
                logger.error(f"[Reolink MJPEG] Stream error for {camera_id}: {e}")
                reolink_mjpeg_capture_service.remove_client(camera_id)
        
        return Response(generate(),
                        mimetype='multipart/x-mixed-replace; boundary=jpgboundary')
    
    except Exception as e:
        logger.error(f"Failed to start Reolink MJPEG stream for {camera_id}: {e}")
        return f"Stream error: {e}", 500
    
# ===== U.I. based health monitor env parameters =====
@app.route('/api/reolink/<camera_id>/stream/mjpeg/main')
def api_reolink_stream_mjpeg_main(camera_id):
    """
    MJPEG main stream for Reolink cameras (fullscreen mode)
    Uses higher resolution but more bandwidth
    """
    logger.info(f"Client requesting Reolink MJPEG MAIN stream for {camera_id}")
    
    # Same implementation as sub stream but pass stream_type='main'
    try:
        camera = camera_repo.get_camera(camera_id)
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
        
        if not reolink_mjpeg_capture_service.add_client(camera_id_main, camera_main, camera_repo):
            return "Failed to start capture", 500
        
        def generate():
            try:
                last_frame_number = -1
                while True:
                    frame_data = reolink_mjpeg_capture_service.get_latest_frame(camera_id_main)
                    if frame_data and frame_data['frame_number'] != last_frame_number:
                        snapshot = frame_data['data']
                        last_frame_number = frame_data['frame_number']
                        yield (b'--jpgboundary\r\n'
                               b'Content-Type: image/jpeg\r\n' +
                               f'Content-Length: {len(snapshot)}\r\n\r\n'.encode() +
                               snapshot + b'\r\n')
                    time.sleep(0.033)
            except GeneratorExit:
                reolink_mjpeg_capture_service.remove_client(camera_id_main)
            except Exception as e:
                logger.error(f"[Reolink MJPEG MAIN] Error {camera_id}: {e}")
                reolink_mjpeg_capture_service.remove_client(camera_id_main)
        
        return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=jpgboundary')
    except Exception as e:
        return f"Stream error: {e}", 500


########################################################-########################################################
#                                          ⚙️⚙️⚙️⚙️AMCREST ⚙️⚙️⚙️⚙️
########################################################-########################################################

# ===== AMCREST MJPEG Service Routes =====
@app.route('/api/amcrest/<camera_id>/stream/mjpeg')
def api_amcrest_stream_mjpeg(camera_id):
    """MJPEG sub stream for Amcrest cameras (grid mode)"""
    logger.info(f"Client requesting Amcrest MJPEG stream for {camera_id}")
    
    try:
        camera = camera_repo.get_camera(camera_id)
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

        if not amcrest_mjpeg_capture_service.add_client(camera_id, camera_with_sub, camera_repo):
            return "Failed to start capture", 500
        
        def generate():
            try:
                last_frame_number = -1
                while True:
                    frame_data = amcrest_mjpeg_capture_service.get_latest_frame(camera_id)
                    if frame_data and frame_data['frame_number'] != last_frame_number:
                        snapshot = frame_data['data']
                        last_frame_number = frame_data['frame_number']
                        yield (b'--jpgboundary\r\n'
                               b'Content-Type: image/jpeg\r\n' +
                               f'Content-Length: {len(snapshot)}\r\n\r\n'.encode() +
                               snapshot + b'\r\n')
                    time.sleep(0.033)
            except GeneratorExit:
                amcrest_mjpeg_capture_service.remove_client(camera_id)
            except Exception as e:
                logger.error(f"[Amcrest MJPEG] Error {camera_id}: {e}")
                amcrest_mjpeg_capture_service.remove_client(camera_id)
        
        return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=jpgboundary')
    except Exception as e:
        return f"Stream error: {e}", 500
    
@app.route('/api/amcrest/<camera_id>/stream/mjpeg/main')
def api_amcrest_stream_mjpeg_main(camera_id):
    """MJPEG main stream for Amcrest cameras (fullscreen mode)"""
    logger.info(f"Client requesting Amcrest MJPEG MAIN stream for {camera_id}")
    
    try:
        camera = camera_repo.get_camera(camera_id)
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
        
        if not amcrest_mjpeg_capture_service.add_client(camera_id_main, camera_main, camera_repo):
            return "Failed to start capture", 500
        
        def generate():
            try:
                last_frame_number = -1
                while True:
                    frame_data = amcrest_mjpeg_capture_service.get_latest_frame(camera_id_main)
                    if frame_data and frame_data['frame_number'] != last_frame_number:
                        snapshot = frame_data['data']
                        last_frame_number = frame_data['frame_number']
                        yield (b'--jpgboundary\r\n'
                               b'Content-Type: image/jpeg\r\n' +
                               f'Content-Length: {len(snapshot)}\r\n\r\n'.encode() +
                               snapshot + b'\r\n')
                    time.sleep(0.033)
            except GeneratorExit:
                amcrest_mjpeg_capture_service.remove_client(camera_id_main)
            except Exception as e:
                logger.error(f"[Amcrest MJPEG MAIN] Error {camera_id}: {e}")
                amcrest_mjpeg_capture_service.remove_client(camera_id_main)
        
        return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=jpgboundary')
    except Exception as e:
        return f"Stream error: {e}", 500

##########################################################-########################################################
#                                          ⚙️⚙️⚙️⚙️PTZ CONTROLS⚙️⚙️⚙️⚙️
##########################################################-########################################################
@app.route('/api/ptz/<camera_serial>/<direction>', methods=['POST'])
@csrf.exempt
def api_ptz_move(camera_serial, direction):
    """Execute PTZ movement with ONVIF priority"""
    try:
        # Validate camera
        if not ptz_validator.is_ptz_capable(camera_serial):
            return jsonify({'success': False, 'error': 'Invalid camera or no PTZ capability'}), 400

        # Get camera config
        camera = camera_repo.get_camera(camera_serial)
        if not camera:
            return jsonify({'success': False, 'error': 'Camera not found'}), 404
        
        camera_type = camera.get('type')
        print(f"[APP.PY] PTZ request for camera: {camera_serial}, type: {camera_type}, direction: {direction}")

        success = False
        message = ""
        
        # Try ONVIF first for Amcrest and Reolink (priority)
        if camera_type in ['amcrest', 'reolink']:
            print(f"[APP.PY] Attempting ONVIF PTZ for {camera_type} camera")
            success, message = ONVIFPTZHandler.move_camera(
                camera_serial=camera_serial,
                direction=direction,
                camera_config=camera
            )
            
            # If ONVIF fails, fall back to brand-specific handler
            if not success and camera_type == 'amcrest':
                print(f"[APP.PY] ONVIF failed, falling back to Amcrest CGI handler")
                success = amcrest_ptz_handler.move_camera(camera_serial, direction, camera_repo)
                message = f'Camera moved {direction} via CGI' if success else 'Movement failed'
        
        # Eufy uses bridge (no ONVIF support)
        elif camera_type == 'eufy':
            print(f"[APP.PY] Dispatching PTZ to EUFY handler")
            success = eufy_bridge.move_camera(camera_serial, direction, camera_repo)
            message = f'Camera moved {direction}' if success else 'Movement failed'
        
        else:
            return jsonify({'success': False, 'error': f'PTZ not supported for camera type: {camera_type}'}), 400

        return jsonify({
            'success': success,
            'camera': camera_serial,
            'direction': direction,
            'message': message
        })

    except Exception as e:
        logger.error(f"PTZ API error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/ptz/<camera_serial>/presets', methods=['GET'])
def api_ptz_get_presets(camera_serial):
    """Get list of PTZ presets for camera"""
    try:
        # Validate camera
        camera = camera_repo.get_camera(camera_serial)
        if not camera:
            return jsonify({'success': False, 'error': 'Camera not found'}), 404
        
        camera_type = camera.get('type')
        
        # Only Amcrest and Reolink support ONVIF presets
        if camera_type not in ['amcrest', 'reolink']:
            return jsonify({'success': False, 'error': 'Presets not supported for this camera type'}), 400
        
        # Get presets via ONVIF
        success, presets = ONVIFPTZHandler.get_presets(camera_serial, camera)
        
        if not success:
            return jsonify({'success': False, 'error': 'Failed to retrieve presets', 'presets': []}), 500
        
        return jsonify({
            'success': True,
            'camera': camera_serial,
            'presets': presets
        })
        
    except Exception as e:
        logger.error(f"Get presets API error: {e}")
        return jsonify({'success': False, 'error': str(e), 'presets': []}), 500


@app.route('/api/ptz/<camera_serial>/preset/<preset_token>', methods=['POST'])
@csrf.exempt
def api_ptz_goto_preset(camera_serial, preset_token):
    """Move camera to preset position"""
    try:
        # Validate camera
        camera = camera_repo.get_camera(camera_serial)
        if not camera:
            return jsonify({'success': False, 'error': 'Camera not found'}), 404
        
        camera_type = camera.get('type')
        
        # Only Amcrest and Reolink support ONVIF presets
        if camera_type not in ['amcrest', 'reolink']:
            return jsonify({'success': False, 'error': 'Presets not supported for this camera type'}), 400
        
        # Execute goto preset
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


@app.route('/api/ptz/<camera_serial>/preset', methods=['POST'])
@csrf.exempt
def api_ptz_set_preset(camera_serial):
    """Save current position as preset"""
    try:
        # Get preset name from request
        data = request.get_json()
        preset_name = data.get('name')
        
        if not preset_name:
            return jsonify({'success': False, 'error': 'Preset name required'}), 400
        
        # Validate camera
        camera = camera_repo.get_camera(camera_serial)
        if not camera:
            return jsonify({'success': False, 'error': 'Camera not found'}), 404
        
        camera_type = camera.get('type')
        
        # Only Amcrest and Reolink support ONVIF presets
        if camera_type not in ['amcrest', 'reolink']:
            return jsonify({'success': False, 'error': 'Presets not supported for this camera type'}), 400
        
        # Set preset
        success, message = ONVIFPTZHandler.set_preset(camera_serial, preset_name, camera)
        
        return jsonify({
            'success': success,
            'camera': camera_serial,
            'preset_name': preset_name,
            'message': message
        })
        
    except Exception as e:
        logger.error(f"Set preset API error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    
########################################################-########################################################
#                                   ⚙️⚙️⚙️⚙️ENVIRONMENT VARIABLE HELPERS⚙️⚙️⚙️⚙️
########################################################-########################################################

def _get_bool(name: str, default: bool | None = None) -> bool | None:
    """
    Return True/False if set, or None if unset and default is None.
    Accepts 1/0, true/false, yes/no, on/off (case-insensitive).
    """
    val = os.getenv(name)
    if val is None:
        return default
    s = str(val).strip().lower()
    if s in TRUE_SET:
        return True
    if s in FALSE_SET:
        return False
    # Fallback: treat any non-empty string as True
    return default if default is not None else None


def _get_int(name: str, default: int) -> int:
    val = os.getenv(name)
    if val is None or val == "":
        return default
    try:
        return int(val)
    except Exception:
        return default


def _resolve_ui_vs_watchdog():
    """
    Mutual exclusion policy:
      - If UI_HEALTH_ENABLED is explicitly set, honor it.
      - Else, if ENABLE_WATCHDOG is true, disable UI health.
      - Else, enable UI health.
      - If both explicitly true, prefer UI health (disable watchdog) and warn.
    Returns (ui_health_enabled, watchdog_enabled)
    """
    wd = _get_bool("ENABLE_WATCHDOG", default=False)
    ui = _get_bool("UI_HEALTH_ENABLED", default=None)  # tri-state

    if ui is None:
        # UI not explicitly set → infer from watchdog
        ui_enabled = not wd
        wd_enabled = wd
    else:
        ui_enabled = ui
        wd_enabled = _get_bool("ENABLE_WATCHDOG", default=False)
        if ui_enabled and wd_enabled:
            # conflict: prefer UI health to avoid dueling restarts
            print(
                "WARN: UI_HEALTH_ENABLED and ENABLE_WATCHDOG both true; disabling watchdog.")
            wd_enabled = False

    return ui_enabled, wd_enabled


def _ui_health_from_env():
    """
    Build UI health settings dict from environment variables AND cameras.json global settings.
    Priority: cameras.json > .env
    """
    # Start with .env defaults
    settings = {
        'uiHealthEnabled': _get_bool("UI_HEALTH_ENABLED", True),  # ← CHANGED
        'sampleIntervalMs': _get_int("UI_HEALTH_SAMPLE_INTERVAL_MS", 2000),
        'staleAfterMs': _get_int("UI_HEALTH_STALE_AFTER_MS", 20000),
        'consecutiveBlankNeeded': _get_int("UI_HEALTH_CONSECUTIVE_BLANK_NEEDED", 10),
        'cooldownMs': _get_int("UI_HEALTH_COOLDOWN_MS", 30000),
        'warmupMs': _get_int("UI_HEALTH_WARMUP_MS", 60000),
        'blankThreshold': {
            'avg': _get_int("UI_HEALTH_BLANK_AVG", 12),
            'std': _get_int("UI_HEALTH_BLANK_STD", 5)
        }
    }
    
    # Override with cameras.json global settings if they exist
    try:
        global_settings = camera_repo.cameras_data.get('ui_health_global_settings', {})
        if global_settings:
            # Map cameras.json keys (uppercase) to settings keys (camelCase)
            key_mapping = {
                'UI_HEALTH_ENABLED': 'uiHealthEnabled',
                'UI_HEALTH_SAMPLE_INTERVAL_MS': 'sampleIntervalMs',
                'UI_HEALTH_STALE_AFTER_MS': 'staleAfterMs',
                'UI_HEALTH_CONSECUTIVE_BLANK_NEEDED': 'consecutiveBlankNeeded',
                'UI_HEALTH_COOLDOWN_MS': 'cooldownMs',
                'UI_HEALTH_WARMUP_MS': 'warmupMs',
                'UI_HEALTH_BLANK_AVG': 'blankAvg',
                'UI_HEALTH_BLANK_STD': 'blankStd'
            }
            
            for json_key, settings_key in key_mapping.items():
                if json_key in global_settings:
                    settings[settings_key] = global_settings[json_key]
                    
    except Exception as e:
        print(f"Warning: Could not load global UI health settings from cameras.json: {e}")
    
    return settings

# ===== Cleanup Handlers =====

######################### -#########################
#        🧹🧼🧽🪣🫧CLEANUP🧹🧼🧽🪣🫧
######################### -#########################

# ===== Zombie Process Reaper =====
def reap_zombies(signum, frame):
    """
    Signal handler to automatically reap zombie child processes.
    Called whenever a child process dies (SIGCHLD signal).
    """
    while True:
        print("zombies?")
        try:
            # WNOHANG = don't block if no zombies available
            pid, status = os.waitpid(-1, os.WNOHANG)
            if pid == 0:
                # No more zombies to reap
                break
            # Log the reaped zombie (optional - can remove if too noisy)
            # print(f"[Reaper] Reaped zombie process PID {pid} with status {status}")
        except ChildProcessError:
            # No child processes exist
            break
        except Exception as e:
            # Unexpected error - log but don't crash
            print(f"[Reaper] Error reaping zombie: {e}")
            break

def cleanup_handler(signum=None, frame=None):
    """Handle cleanup on shutdown signals"""

    print("\n🛑 Shutting down... cleaning up streams and resources")
    try:
        stop_all_services(stream_manager,
                          bridge_watchdog,
                          eufy_bridge,
                          unifi_cameras,
                          unifi_resource_monitor,
                          unifi_mjpeg_capture_service,
                          reolink_mjpeg_capture_service,
                          amcrest_mjpeg_capture_service)
    except Exception as e:
        print(traceback.print_exc())
        print(f"Cleanup error: {e}")
    finally:
        kill_all(eufy_bridge, stream_manager)
        print("✅ Cleanup completed")

    if signum:
        exit(0)


# Register zombie reaper BEFORE other signal handlers
signal.signal(signal.SIGCHLD, reap_zombies)
print("✅ Zombie process reaper installed")

# Register cleanup handlers
# Run cleanup_handler automatically when the program exits normally
atexit.register(cleanup_handler)

# Triggered by Ctrl+C in the terminal (KeyboardInterrupt -> SIGINT)
signal.signal(signal.SIGINT, cleanup_handler)

# Triggered by `kill <pid>` (default), or system shutdown (SIGTERM)
signal.signal(signal.SIGTERM, cleanup_handler)

# Triggered by Ctrl+Z in the terminal (SIGTSTP → normally suspends, but here we repurpose it to nuke everything at once)
signal.signal(signal.SIGTSTP, functools.partial(kill_all, eufy_bridge))


# ===== Run Server =====

if __name__ == '__main__':
    # Get server IP
    local_ip = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    local_ip.connect(('8.8.8.8', 81))
    server_ip = local_ip.getsockname()[0]
    local_ip.close()

    print(f"🚀 Starting Unified NVR API...")
    print(f"📱 Web interface: http://{server_ip}:5000")
    print(f"🔧 API endpoints: http://{server_ip}:5000/api/")

    app.run(debug=True, host='0.0.0.0', port=5000)
