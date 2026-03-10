#!/usr/bin/env python3
"""
Unified NVR Flask Application - Complete Merged Version
Combines refactored architecture with all operational routes
"""

import os
import sys
import re
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
from datetime import datetime
from threading import Thread
import uuid

from flask import Flask, render_template, jsonify, request, Response, redirect, send_file, session
from flask_wtf import FlaskForm
from flask_wtf.csrf import CSRFProtect
from wtforms import SelectField, SubmitField
from wtforms.validators import DataRequired
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
import bcrypt

# modular imports
from models.user import User
from services.camera_repository import CameraRepository
from services.camera_config_sync import sync_cameras_json_to_db
from services.ptz.ptz_validator import PTZValidator
from streaming.stream_manager import StreamManager

from low_level_handlers.process_reaper import install_sigchld_handler

from services.eufy.eufy_bridge import EufyBridge
from services.eufy.eufy_bridge_client import submit_captcha_sync, submit_2fa_sync, check_status_sync
from services.talkback_transcoder import TalkbackTranscoderManager
from services.eufy.eufy_bridge_watchdog import BridgeWatchdog
from services.unifi_protect_service import UniFiProtectService
from services.unifi_service_resource_monitor import UniFiServiceResourceMonitor
from services.app_restart_handler import AppRestartHandler
from services.unifi_mjpeg_capture_service import unifi_mjpeg_capture_service
from services.amcrest_mjpeg_capture_service import amcrest_mjpeg_capture_service
from services.reolink_mjpeg_capture_service import reolink_mjpeg_capture_service
from services.sv3c_mjpeg_capture_service import sv3c_mjpeg_capture_service
from services.mediaserver_mjpeg_service import mediaserver_mjpeg_service
from services.ptz.amcrest_ptz_handler import amcrest_ptz_handler
from services.onvif.onvif_ptz_handler import ONVIFPTZHandler
from services.ptz.baichuan_ptz_handler import BaichuanPTZHandler
from services.recording.recording_service import RecordingService
from config.recording_config_loader import RecordingConfig
from services.recording.snapshot_service import SnapshotService
from services.recording.timeline_service import get_timeline_service, TimelineService, ExportStatus
from services.onvif.onvif_event_listener import ONVIFEventListener
from services.credentials.reolink_credential_provider import ReolinkCredentialProvider
from services.motion.reolink_motion_service import create_reolink_motion_service
from services.motion.ffmpeg_motion_detector import create_ffmpeg_detector
from services.camera_state_tracker import camera_state_tracker
from services.stream_watchdog import StreamWatchdog
from services.power.hubitat_power_service import HubitatPowerService
from services.power.unifi_poe_service import UnifiPoePowerService
from services.presence.presence_service import PresenceService
from services.websocket_mjpeg_service import websocket_mjpeg_service
from services.cert_routes import cert_bp
from services.external_api_routes import external_api_bp, init_external_api

from low_level_handlers.cleanup_handler import stop_all_services, kill_all, kill_ffmpeg

# Flask-SocketIO for WebSocket MJPEG multiplexing
# Uses simple-websocket for Gunicorn compatibility (gthread workers)
from flask_socketio import SocketIO, emit, join_room, leave_room

# Flask app setup
app = Flask(__name__)
app.config['SECRET_KEY'] = '-ratatouillemescouilles'
app.config['TEMPLATES_AUTO_RELOAD'] = True
csrf = CSRFProtect(app)

# Register Blueprints
app.register_blueprint(cert_bp)
app.register_blueprint(external_api_bp)

# Flask-Login session configuration
# Indefinite sessions until logout (no automatic expiry)
from datetime import timedelta
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = False  # Set to True when using HTTPS
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=365)

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'


@login_manager.unauthorized_handler
def unauthorized_api():
    """
    Return JSON 401 for API requests instead of redirecting to HTML login page.

    Without this handler, Flask-Login redirects ALL unauthorized requests to the
    login page (HTML). When the frontend JS fetches /api/* endpoints and gets
    HTML back, it fails with: Unexpected token '<', "<!DOCTYPE"... is not valid JSON.
    """
    if request.path.startswith('/api/') or request.is_json or request.headers.get('Accept') == 'application/json':
        return jsonify({'error': 'Authentication required', 'login_required': True}), 401
    return redirect('/login')

# Flask-Login user loader
# Called by Flask-Login to reload user object from user ID stored in session
@login_manager.user_loader
def load_user(user_id):
    """
    Load user by ID from database via PostgREST.

    Args:
        user_id (str): User ID from session

    Returns:
        User: User instance if found, None otherwise
    """
    return User.get_by_id(user_id)


@app.before_request
def _auto_login_trusted_device():
    """
    Auto-login users on trusted devices.

    If the user is not authenticated but presents a valid device_token cookie
    that matches a trusted device in the database, automatically log them in
    as the user associated with that device. This means trusted devices never
    see the login page again.

    Skips static files, health checks, and the login route itself.
    """
    from flask_login import current_user as _cu
    # Skip if already authenticated
    if _cu and _cu.is_authenticated:
        return

    # Skip routes that don't need auth
    skip_prefixes = ('/static/', '/api/health', '/login', '/favicon')
    if any(request.path.startswith(p) for p in skip_prefixes):
        return

    device_token = request.cookies.get('device_token')
    if not device_token:
        return

    try:
        resp = _postgrest_session.get(
            f"{POSTGREST_URL}/trusted_devices",
            params={
                'device_token': f'eq.{device_token}',
                'is_trusted': 'eq.true',
                'select': 'user_id'
            },
            timeout=3
        )
        if resp.status_code == 200:
            devices = resp.json()
            if devices and devices[0].get('user_id'):
                user = User.get_by_id(devices[0]['user_id'])
                if user:
                    login_user(user, remember=True)
                    print(f"[TrustedDevice] Auto-login: {user.username} (token: {device_token[:8]}...)")
    except Exception as e:
        # Don't block the request if DB is down — just skip auto-login
        print(f"[TrustedDevice] Auto-login check failed: {e}")


# Initialize Flask-SocketIO
# async_mode='threading' works with Gunicorn gthread workers
# cors_allowed_origins='*' allows connections from any origin (local network)
socketio = SocketIO(
    app,
    async_mode='threading',
    cors_allowed_origins='*',
    ping_timeout=60,
    ping_interval=25,
    logger=False,  # Reduce log noise
    engineio_logger=False
)

# Set SocketIO instance in websocket_mjpeg_service
websocket_mjpeg_service.set_socketio(socketio)

logger = logging.getLogger('werkzeug')
logger.setLevel(logging.WARNING)

# Custom filter to suppress high-frequency API endpoint logs
class SnapAPIFilter(logging.Filter):
    """Filter out /api/snap/ requests from werkzeug access logs.
    iOS polls snapshots every 1s per camera, creating excessive log noise."""
    def filter(self, record):
        # Suppress logs containing /api/snap/ path
        if hasattr(record, 'getMessage'):
            msg = record.getMessage()
            if '/api/snap/' in msg:
                return False
        return True

# Apply filter to werkzeug logger
logger.addFilter(SnapAPIFilter())

load_dotenv()  # loads .env from the current working directory / project root

TRUE_SET = {"1", "true", "yes", "on"}
FALSE_SET = {"0", "false", "no", "off"}

# Application state - thread-safe singleton for shutdown tracking
class AppState:
    """Thread-safe application state"""
    def __init__(self):
        self._shutting_down = False

    @property
    def is_shutting_down(self):
        return self._shutting_down

    def set_shutting_down(self):
        self._shutting_down = True

app_state = AppState()

print("=" * 80)
print("🚀 Starting Unified NVR Server - Refactored Architecture")
print("=" * 80)

# ===== Initialize Core Services =====
try:
    print("\n📦 Initializing core services...")

    # Camera repository (loads from DB first, falls back to JSON)
    camera_repo = CameraRepository('./config')
    print(
        f"✅ Camera repository loaded: {camera_repo.get_camera_count()} cameras "
        f"(source: {camera_repo.get_data_source()})")

    # Initialize external API with camera_repo reference (for TILES integration)
    init_external_api(camera_repo)

    # Auto-sync: migrate new cameras from cameras.json to database
    try:
        migrated, existing, warnings = sync_cameras_json_to_db('./config/cameras.json')
        if migrated > 0:
            # Reload from DB to pick up newly migrated cameras
            camera_repo.reload()
            print(f"✅ Camera sync: {migrated} new cameras migrated to database")
        else:
            print(f"✅ Camera sync: {existing} cameras in sync, no migration needed")
    except Exception as e:
        print(f"⚠️ Camera sync failed (non-fatal): {e}")

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

    # Auto-start HLS streams for all cameras at container startup
    # This ensures streams are ready before any client connects, providing:
    # 1. Instant page loads (no wait for HLS startup)
    # 2. Buffer overfill defense (streams always running)
    # 3. Better MJPEG experience on iOS (mediaserver can tap immediately)
    def auto_start_all_streams():
        """Start HLS streams for all cameras in background threads"""
        all_cameras = camera_repo.get_all_cameras(include_hidden=False)
        print(f"🎬 Auto-starting HLS streams for {len(all_cameras)} cameras...")

        started = 0
        for serial, config in all_cameras.items():
            try:
                # Start in background thread to not block app startup
                def start_camera_stream(cam_serial, cam_config):
                    try:
                        stream_manager.start_stream(cam_serial, resolution='sub')
                        print(f"   ✓ Started stream: {cam_config.get('name', cam_serial)}")
                    except Exception as e:
                        print(f"   ✗ Failed to start {cam_config.get('name', cam_serial)}: {e}")

                Thread(
                    target=start_camera_stream,
                    args=(serial, config),
                    daemon=True
                ).start()
                started += 1

                # Small stagger to avoid overwhelming cameras/network
                time.sleep(0.5)

            except Exception as e:
                print(f"   ✗ Error starting {config.get('name', serial)}: {e}")

        print(f"🎬 Initiated {started} stream starts (running in background)")

    # Start streams in a separate thread so app startup isn't blocked
    Thread(target=auto_start_all_streams, daemon=True).start()

    # ===== Pre-warm MediaServer MJPEG captures =====
    # MJPEG FFmpeg processes tap MediaMTX sub streams and produce JPEG frames.
    # Pre-warming ensures instant loading when iOS/portable clients connect.
    def auto_start_mediaserver_mjpeg():
        """
        Pre-warm MediaServer MJPEG captures for instant loading.

        Polls MediaMTX API until streams are actually publishing before starting
        MJPEG captures. This prevents 404 errors from FFmpeg trying to connect
        to non-existent streams.

        Starts FFmpeg capture processes for all cameras that use mediaserver MJPEG.
        These processes run continuously, buffering frames for instant client access.
        """
        import requests

        # Collect cameras that need MJPEG pre-warming (MediaMTX tap)
        #
        # Only cameras that publish to MediaMTX can be tapped for MJPEG:
        # - LL_HLS, HLS, WEBRTC, NEOLINK → publish to MediaMTX → can tap
        # - MJPEG, NEOLINK → don't publish to MediaMTX → use vendor-specific capture
        #
        # Vendor-specific MJPEG services:
        # - Reolink MJPEG: snapshot polling via reolink_mjpeg_capture_service
        # - Amcrest: true MJPEG stream via /cgi-bin/mjpg/video.cgi
        cameras_to_prewarm = {}
        for camera_serial, camera_config in camera_repo.get_all_cameras().items():
            stream_type = camera_config.get('stream_type', 'HLS').upper()

            # Only pre-warm cameras that publish to MediaMTX
            if stream_type in ('MJPEG', 'NEOLINK'):
                # These don't publish to MediaMTX - skip
                continue

            cameras_to_prewarm[camera_serial] = camera_config

        if not cameras_to_prewarm:
            print("📭 No mediaserver MJPEG cameras to pre-warm")
            return

        print(f"⏳ Waiting for {len(cameras_to_prewarm)} HLS streams to publish before MJPEG pre-warming...")

        # Poll MediaMTX until all required streams are publishing
        # Max wait: 120 seconds (FFmpeg startup can be slow)
        max_wait = 120
        poll_interval = 5
        waited = 0

        while waited < max_wait:
            try:
                # Query MediaMTX paths API
                resp = requests.get('http://nvr-packager:9997/v3/paths/list', timeout=5)
                if resp.status_code == 200:
                    paths_data = resp.json()
                    publishing_paths = set()

                    # Extract publishing path names
                    for item in paths_data.get('items', []):
                        path_name = item.get('name', '')
                        # A path is "publishing" if it has readers OR a source
                        if item.get('source') or item.get('readers'):
                            publishing_paths.add(path_name)

                    # Check how many of our cameras are publishing
                    ready_cameras = [s for s in cameras_to_prewarm if s in publishing_paths]

                    if len(ready_cameras) == len(cameras_to_prewarm):
                        print(f"✅ All {len(cameras_to_prewarm)} HLS streams publishing - proceeding with MJPEG pre-warming")
                        break
                    else:
                        pending = len(cameras_to_prewarm) - len(ready_cameras)
                        print(f"  ⏳ {len(ready_cameras)}/{len(cameras_to_prewarm)} streams ready, waiting for {pending} more...")

            except Exception as e:
                print(f"  ⚠️ MediaMTX poll error: {e}")

            time.sleep(poll_interval)
            waited += poll_interval

        if waited >= max_wait:
            print(f"⚠️ Timeout waiting for HLS streams ({max_wait}s) - proceeding with MJPEG pre-warming anyway")

        print("🎬 Pre-warming MediaServer MJPEG captures...")
        started = 0
        failed = 0

        for camera_serial, camera_config in cameras_to_prewarm.items():
            try:
                mediaserver_mjpeg_service.start_capture(camera_serial, camera_config)
                print(f"  ✓ {camera_config.get('name', camera_serial)}: MJPEG pre-warmed")
                started += 1
                time.sleep(0.3)  # Brief delay between starts
            except Exception as e:
                print(f"  ✗ {camera_config.get('name', camera_serial)}: {e}")
                failed += 1

        print(f"✅ MediaServer MJPEG pre-warming complete: {started} started, {failed} failed")

    # Start MJPEG pre-warming in background (after HLS streams)
    Thread(target=auto_start_mediaserver_mjpeg, daemon=True).start()

    # Recording service
    try:
        recording_service = RecordingService(
            camera_repo,
            config_path='./config/recording_settings.json'
        )
        print("✅ Recording service initialized")
    except Exception as e:
        print(f"⚠️  Recording service initialization failed: {e}")
        recording_service = None
        
        
    # Reolink motion detection service (Baichuan protocol)
    reolink_motion_service = None
    if recording_service:
        try:
            reolink_creds = ReolinkCredentialProvider(use_api_credentials=True)
            recording_config = RecordingConfig(config_path='./config/recording_settings.json')
            
            reolink_motion_service = create_reolink_motion_service(
                camera_repo,
                recording_service,
                recording_config,
                reolink_creds
            )
            print("✅ Reolink motion service initialized")
        except Exception as e:
            print(f"⚠️  Reolink motion service initialization failed: {e}")
            reolink_motion_service = None
    
    # Snapshot service
    try:
        snapshot_service = SnapshotService(
            camera_repo,
            recording_service.storage,  # Reuse storage manager
            recording_service.config     # Reuse recording config
        )
        print("✅ Snapshot service initialized")
    except Exception as e:
        print(f"⚠️  Snapshot service initialization failed: {e}")
        snapshot_service = None
        
    # ONVIF event listener for motion detection
    onvif_listener = None
    if recording_service:
        try:
            onvif_listener = ONVIFEventListener(camera_repo, recording_service)
            print("✅ ONVIF event listener initialized")
        except Exception as e:
            print(f"⚠️  ONVIF event listener initialization failed: {e}")
            onvif_listener = None

    # FFmpeg motion detector for cameras without ONVIF or Baichuan support
    # Uses CameraStateTracker for health checks instead of ffprobe (avoids extra RTSP connections)
    ffmpeg_motion_detector = None
    if recording_service:
        try:
            ffmpeg_motion_detector = create_ffmpeg_detector(
                camera_repo,
                recording_service,
                recording_config,
                camera_state_tracker  # Pass state tracker for health checks
            )
            print("✅ FFmpeg motion detector initialized (with CameraStateTracker)")
        except Exception as e:
            print(f"⚠️  FFmpeg motion detector initialization failed: {e}")
            ffmpeg_motion_detector = None

    # Kill any orphaned FFmpeg recording processes from previous runs
    if recording_service:
        print("🧹 Cleaning up orphaned recording processes...")
        try:
            import subprocess
            
            # Find FFmpeg processes writing to /recordings/continuous/
            result = subprocess.run(
                ['pgrep', '-f', 'ffmpeg.*recordings/continuous'],
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0 and result.stdout.strip():
                pids = result.stdout.strip().split('\n')
                for pid in pids:
                    try:
                        subprocess.run(['kill', '-9', pid], check=False)
                        print(f"  ✅ Killed orphaned FFmpeg process: {pid}")
                    except Exception as e:
                        print(f"  ⚠️  Could not kill process {pid}: {e}")
            else:
                print("  ✅ No orphaned processes found")
                
        except Exception as e:
            print(f"  ⚠️  Error cleaning orphaned processes: {e}")
        
    # Auto-start continuous recordings
    if recording_service:
        print("🎬 Auto-starting enabled recordings...")
        
        for camera_id in camera_repo.get_all_cameras().keys():
            try:
                camera = camera_repo.get_camera(camera_id)
                camera_name = camera.get('name', camera_id)
                
                # Start continuous recording if enabled
                if recording_service.config.is_recording_enabled(camera_id, 'continuous'):
                    if recording_service.start_continuous_recording(camera_id):
                        print(f"  ✅ Continuous: {camera_name}")
                    else:
                        print(f"  ❌ Failed continuous: {camera_name}")
                
                # Start motion detection if enabled (ONVIF, FFmpeg, or Baichuan)
                if recording_service.config.is_recording_enabled(camera_id, 'motion'):
                    camera_cfg = recording_service.config.get_camera_config(camera_id)
                    detection_method = camera_cfg.get('motion_recording', {}).get('detection_method', 'onvif')
                    camera_type = camera.get('type', '').lower()

                    # Skip Reolink cameras - they use Baichuan motion service
                    if camera_type == 'reolink':
                        pass  # Handled by reolink_motion_service
                    elif detection_method == 'onvif':
                        # Check if camera has ONVIF capability
                        if onvif_listener and 'ONVIF' in camera.get('capabilities', []):
                            if onvif_listener.start_listener(camera_id):
                                print(f"  ✅ ONVIF Motion: {camera_name}")
                            else:
                                print(f"  ❌ Failed ONVIF Motion: {camera_name}")
                        else:
                            print(f"  ⚠️  ONVIF not available for {camera_name}, configure detection_method='ffmpeg' instead")
                    elif detection_method == 'ffmpeg':
                        # Use FFmpeg scene detection
                        if ffmpeg_motion_detector:
                            sensitivity = camera_cfg.get('motion_recording', {}).get('ffmpeg_sensitivity', 0.3)
                            if ffmpeg_motion_detector.start_detector(camera_id, sensitivity):
                                print(f"  ✅ FFmpeg Motion: {camera_name}")
                            else:
                                print(f"  ❌ Failed FFmpeg Motion: {camera_name}")
                        else:
                            print(f"  ⚠️  FFmpeg detector not available for {camera_name}")
                            
                # Start snapshots if enabled
                if snapshot_service and snapshot_service.config.is_recording_enabled(camera_id, 'snapshots'):
                    if snapshot_service.start_snapshots(camera_id):
                        print(f"  ✅ Snapshots: {camera_name}")
                    else:
                        print(f"  ❌ Failed snapshots: {camera_name}")
                
            except Exception as e:
                print(f"  ❌ Error starting services for {camera_id}: {e}")

    # Start pre-buffer segment recording for cameras with it enabled
    if recording_service and recording_service.segment_buffer_manager:
        print("\n📼 Starting pre-buffer segment recording...")
        for camera_id in camera_repo.get_all_cameras():
            camera = camera_repo.get_camera(camera_id)
            if not camera:
                continue
            camera_name = camera.get('name', camera_id)

            # Skip cameras without streaming capability (e.g., doorbells)
            if 'streaming' not in camera.get('capabilities', []):
                continue

            if recording_service.config.is_pre_buffer_enabled(camera_id):
                try:
                    # Get stream URL from recording service
                    source_url, _ = recording_service._get_recording_source_url(camera_id)
                    if source_url and recording_service.segment_buffer_manager.start_buffer(camera_id, source_url):
                        print(f"  ✅ Pre-buffer: {camera_name}")
                    else:
                        print(f"  ⚠️  Skipped pre-buffer: {camera_name} (no source URL)")
                except NotImplementedError:
                    print(f"  ⏭️  Skipped pre-buffer: {camera_name} (source not implemented)")
                except Exception as e:
                    print(f"  ❌ Failed pre-buffer: {camera_name} ({e})")

    # Background thread to monitor recording completions and auto-restart
    if recording_service:
        def recording_monitor_loop():
            """Background thread to cleanup finished recordings and auto-restart continuous"""
            import time

            buffer_cleanup_counter = 0

            while True:
                try:
                    time.sleep(10)  # Check every 10 seconds

                    if recording_service:
                        cleaned = recording_service.cleanup_finished_recordings()
                        if cleaned > 0:
                            logger.debug(f"Recording monitor cleaned {cleaned} finished recordings")

                        # Periodic buffer cleanup every 5 minutes (30 iterations * 10s)
                        buffer_cleanup_counter += 1
                        if buffer_cleanup_counter >= 30:
                            buffer_cleanup_counter = 0
                            if recording_service.storage:
                                recording_service.storage.cleanup_buffer_directory(max_age_minutes=5)

                except Exception as e:
                    logger.error(f"Recording monitor error: {e}")
                    time.sleep(30)  # Back off on error
        
        monitor_thread = Thread(target=recording_monitor_loop, daemon=True, name="RecordingMonitor")
        monitor_thread.start()
        print("✅ Recording monitor thread started")
    

    install_sigchld_handler()

    if os.getenv('NVR_USE_EUFY_BRIDGE', '0').lower() in ['1', 'true']:
        print("🌉 Initializing Eufy bridge...")
        eufy_bridge = EufyBridge()
        print("✅ Eufy bridge initialized")
    else:
        eufy_bridge = None

    if os.getenv('NVR_USE_EUFY_BRIDGE_WATCHDOG', '0').lower() in ['1', 'true'] and eufy_bridge:
        bridge_watchdog = BridgeWatchdog(eufy_bridge)
        print("✅ Eufy bridge_watchdog initialized")
    else:
        bridge_watchdog = None

    print("\n✅ All core services initialized successfully!\n")

except Exception as e:
    print(f"\n❌ Failed to initialize services: {e}")
    print(traceback.print_exc())
    exit(1)


def wait_for_bridge_ready(timeout=5):
    """Wait for bridge to be ready"""
    if eufy_bridge and os.getenv('NVR_USE_EUFY_BRIDGE', '0').lower() in ['1', 'true']:
        t0 = time.time()
        while time.time() - t0 < timeout:
            if eufy_bridge.is_running():
                return True
            time.sleep(0.25)
    return False

# ===== Auto-start Eufy Bridge =====
try:
    if eufy_bridge:
        print("\n🌉 Starting Eufy bridge...")
        if not eufy_bridge.is_running():
            eufy_bridge.start()
            if wait_for_bridge_ready():
                print("✅ Bridge started successfully")
            else:
                print("⚠️  Bridge did not reach ready state in time")

    if bridge_watchdog:
        bridge_watchdog.start_monitoring()
        print("✅ Bridge watchdog started")

except Exception as e:
    print(f"⚠️  Bridge startup warning: {e}")

# ===== Auto-start Streams =====
# REMOVED: Duplicate auto-start block was here (lines 413-425)
# Stream auto-start is now handled ONLY by auto_start_all_streams() at line ~121
# Having two auto-start blocks caused duplicate MediaMTX publishers and broken pipes

# ===== Start Camera State Tracker =====
try:
    print("\n📡 Starting Camera State Tracker...")
    camera_state_tracker.set_socketio(socketio)
    camera_state_tracker.start()
    print("✅ Camera State Tracker started (polling MediaMTX API every 5s)")
except Exception as e:
    print(f"⚠️  Camera State Tracker startup warning: {e}")

# ===== Start Stream Watchdog =====
# Initialize StreamWatchdog for unified stream health monitoring
stream_watchdog = None
try:
    print("\n🔄 Initializing Stream Watchdog...")
    # Configure MJPEG services for watchdog to use
    mjpeg_services = {
        'reolink': reolink_mjpeg_capture_service,
        'amcrest': amcrest_mjpeg_capture_service,
        'unifi': unifi_mjpeg_capture_service,
    }
    stream_watchdog = StreamWatchdog(
        stream_manager=stream_manager,
        camera_state_tracker=camera_state_tracker,
        mjpeg_services=mjpeg_services
    )
    # Set SocketIO instance so watchdog can broadcast stream_restarted events
    stream_watchdog.set_socketio(socketio)
    stream_watchdog.start()
    print("✅ Stream Watchdog started (polling every 10s, uses CameraStateTracker)")
except Exception as e:
    print(f"⚠️  Stream Watchdog startup warning: {e}")

# ===== Start Hubitat Power Service =====
# Provides power cycling for cameras with power_supply='hubitat' via smart plugs
hubitat_power_service = None
try:
    print("\n🔌 Initializing Hubitat Power Service...")
    hubitat_power_service = HubitatPowerService(
        camera_repo=camera_repo,
        camera_state_tracker=camera_state_tracker
    )
    # Set stream_manager for automatic stream restart after power cycle
    hubitat_power_service.set_stream_manager(stream_manager)
    hubitat_power_service.start()
    if hubitat_power_service.is_enabled():
        hubitat_cameras = hubitat_power_service.get_hubitat_cameras()
        print(f"✅ Hubitat Power Service started ({len(hubitat_cameras)} cameras)")
    else:
        print("⚠️  Hubitat Power Service disabled (credentials not configured)")
except Exception as e:
    print(f"⚠️  Hubitat Power Service startup warning: {e}")

# ===== Start UniFi POE Power Service =====
# Provides power cycling for cameras with power_supply='poe' via UniFi switches
unifi_poe_service = None
try:
    print("\n🔌 Initializing UniFi POE Power Service...")
    unifi_poe_service = UnifiPoePowerService(
        camera_repo=camera_repo,
        camera_state_tracker=camera_state_tracker
    )
    unifi_poe_service.start()
    if unifi_poe_service.is_enabled():
        poe_cameras = unifi_poe_service.get_poe_cameras()
        print(f"✅ UniFi POE Power Service started ({len(poe_cameras)} cameras)")
    else:
        print("⚠️  UniFi POE Power Service disabled (credentials not configured)")
except Exception as e:
    print(f"⚠️  UniFi POE Power Service startup warning: {e}")

# ===== Start Presence Service =====
# Provides household presence tracking with Hubitat integration
presence_service = None
try:
    print("\n👥 Initializing Presence Service...")
    presence_service = PresenceService()
    presence_service.start()
    print("✅ Presence Service started")
except Exception as e:
    print(f"⚠️  Presence Service startup warning: {e}")

# ===== Auto-start Reolink Motion Detection =====
if reolink_motion_service:
    try:
        print("\n🏃 Starting Reolink motion detection service...")
        reolink_motion_service.start()
        print("✅ Reolink motion detection started")
    except Exception as e:
        print(f"⚠️  Reolink motion detection startup warning: {e}")

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


# ===== Pre-warm ONVIF Connections for PTZ Cameras =====
# Eliminates first-command latency by populating service caches at startup
def prewarm_onvif_connections():
    """
    Pre-warm ONVIF connections for all PTZ-capable cameras with ONVIF ports.

    Populates caches for:
    - ONVIFClient._connections (camera connections)
    - ONVIFClient._ptz_services (PTZ service instances)
    - ONVIFClient._media_services (Media service instances)
    - ONVIFClient._profile_tokens (profile tokens)

    This ensures first PTZ command is fast (~200ms) instead of slow (~10s).
    """
    from services.onvif.onvif_client import ONVIFClient
    from services.credentials.reolink_credential_provider import ReolinkCredentialProvider
    from services.credentials.amcrest_credential_provider import AmcrestCredentialProvider
    from services.credentials.sv3c_credential_provider import SV3CCredentialProvider

    # Credential providers (reuse if already initialized)
    reolink_creds = ReolinkCredentialProvider()
    amcrest_creds = AmcrestCredentialProvider()
    sv3c_creds = SV3CCredentialProvider()

    warmed_count = 0
    failed_count = 0

    for camera_serial, camera_config in camera_repo.get_all_cameras().items():
        # Skip cameras without PTZ capability
        capabilities = camera_config.get('capabilities', [])
        if 'ptz' not in capabilities:
            continue

        # Skip cameras without ONVIF port (use Baichuan instead)
        onvif_port = camera_config.get('onvif_port')
        if onvif_port is None:
            print(f"  ⏭️  {camera_config.get('name', camera_serial)}: No ONVIF port (uses Baichuan)")
            continue

        camera_type = camera_config.get('type', 'unknown')
        host = camera_config.get('host')

        if not host:
            print(f"  ⚠️  {camera_config.get('name', camera_serial)}: No host configured")
            failed_count += 1
            continue

        # Get credentials based on camera type
        username, password = None, None
        if camera_type == 'reolink':
            username, password = reolink_creds.get_credentials(camera_serial)
        elif camera_type == 'amcrest':
            username, password = amcrest_creds.get_credentials(camera_serial)
        elif camera_type == 'sv3c':
            username, password = sv3c_creds.get_credentials(camera_serial)
        elif camera_type == 'eufy':
            # Eufy uses bridge for PTZ, not ONVIF - skip
            print(f"  ⏭️  {camera_config.get('name', camera_serial)}: Eufy uses bridge (no ONVIF)")
            continue
        else:
            print(f"  ⏭️  {camera_config.get('name', camera_serial)}: Unknown type '{camera_type}'")
            continue

        if not username or not password:
            print(f"  ⚠️  {camera_config.get('name', camera_serial)}: Missing credentials")
            failed_count += 1
            continue

        try:
            # Connect to camera (populates _connections cache)
            camera = ONVIFClient.get_camera(
                host=host,
                port=onvif_port,
                username=username,
                password=password,
                camera_serial=camera_serial
            )

            if not camera:
                print(f"  ⚠️  {camera_config.get('name', camera_serial)}: Connection failed")
                failed_count += 1
                continue

            # Pre-warm PTZ service (populates _ptz_services cache)
            ptz_service = ONVIFClient.get_ptz_service(camera, camera_serial=camera_serial)
            if not ptz_service:
                print(f"  ⚠️  {camera_config.get('name', camera_serial)}: No PTZ service")
                failed_count += 1
                continue

            # Pre-warm profile token (populates _profile_tokens cache)
            profile_token = ONVIFClient.get_profile_token(camera, camera_serial=camera_serial)
            if not profile_token:
                print(f"  ⚠️  {camera_config.get('name', camera_serial)}: No profile token")
                failed_count += 1
                continue

            # Send stop command to ensure camera isn't spinning from connection
            # Some cameras resume last PTZ state on ONVIF connect
            try:
                stop_request = ptz_service.create_type('Stop')
                stop_request.ProfileToken = profile_token
                stop_request.PanTilt = True
                stop_request.Zoom = True
                ptz_service.Stop(stop_request)
            except Exception as stop_err:
                # Non-fatal - just log and continue
                print(f"  ⚠️  {camera_config.get('name', camera_serial)}: Stop command failed: {stop_err}")

            print(f"  ✅ {camera_config.get('name', camera_serial)}: ONVIF pre-warmed")
            warmed_count += 1

        except Exception as e:
            print(f"  ⚠️  {camera_config.get('name', camera_serial)}: {e}")
            failed_count += 1

    return warmed_count, failed_count

try:
    print("\n🔥 Pre-warming ONVIF connections for PTZ cameras...")
    warmed, failed = prewarm_onvif_connections()
    if warmed > 0:
        print(f"✅ ONVIF pre-warmed: {warmed} cameras ready, {failed} failed")
    elif failed > 0:
        print(f"⚠️  ONVIF pre-warming: 0 cameras ready, {failed} failed")
    else:
        print("ℹ️  No PTZ cameras with ONVIF ports found")
except Exception as e:
    print(f"⚠️  ONVIF pre-warming warning: {e}")

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

# ===== Authentication Helper Functions =====

# PostgREST connection URL and persistent session (connection pooling)
# Using requests.Session() keeps TCP connections alive between calls,
# eliminating per-request TCP handshake overhead (~1-3ms per call on Docker network)
POSTGREST_URL = os.getenv('NVR_POSTGREST_URL', 'http://postgrest:3001')
_postgrest_session = requests.Session()
_postgrest_session.headers.update({'Content-Type': 'application/json'})

def _create_user_session(user_id, ip_address, user_agent):
    """
    Create session record in database via PostgREST.

    Args:
        user_id (int): User ID
        ip_address (str): Client IP address
        user_agent (str): Client User-Agent string
    """
    try:
        _postgrest_session.post(
            f"{POSTGREST_URL}/user_sessions",
            json={
                'user_id': user_id,
                'ip_address': ip_address,
                'user_agent': user_agent,
                'is_active': True
            },
            timeout=5
        )
    except requests.RequestException as e:
        print(f"Error creating user session: {e}")

def _deactivate_user_session(user_id):
    """
    Mark all user sessions as inactive in database.

    Args:
        user_id (int): User ID
    """
    try:
        _postgrest_session.patch(
            f"{POSTGREST_URL}/user_sessions",
            params={'user_id': f'eq.{user_id}', 'is_active': 'eq.true'},
            json={'is_active': False},
            headers={'Prefer': 'return=minimal'},
            timeout=5
        )
    except requests.RequestException as e:
        print(f"Error deactivating user session: {e}")

# ===== Authentication Routes =====

@app.route('/login', methods=['GET', 'POST'])
def login():
    """
    User login page and authentication handler.

    GET: Display login form
    POST: Authenticate user credentials and create session
    """
    if request.method == 'GET':
        return render_template('login.html')

    # POST - handle login
    username = request.form.get('username')
    password = request.form.get('password')

    if not username or not password:
        return render_template('login.html', error='Username and password required')

    # Load user and password hash from database
    user, password_hash = User.get_by_username(username)

    if not user:
        return render_template('login.html', error='Invalid username or password')

    # Verify password with bcrypt
    if not bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8')):
        return render_template('login.html', error='Invalid username or password')

    # Log user in (even if password change required - they authenticated successfully)
    login_user(user, remember=True)

    # Create session record in database
    _create_user_session(user.id, request.remote_addr, request.user_agent.string)

    # Register device token — reuse existing cookie or generate new one
    device_token = request.cookies.get('device_token') or str(uuid.uuid4())
    _register_or_update_device(
        device_token=device_token,
        user_id=user.id,
        ip_address=request.remote_addr,
        user_agent=request.user_agent.string
    )

    # Check if password change required AFTER login
    # This allows change-password route to use current_user with proper auth context
    if user.must_change_password:
        resp = redirect('/change-password')
    else:
        resp = redirect('/streams')

    # Set device_token cookie on the redirect response
    resp.set_cookie(
        'device_token',
        device_token,
        max_age=365 * 24 * 3600,
        httponly=True,
        samesite='Lax',
        secure=False
    )
    return resp

@app.route('/logout', methods=['POST'])
@csrf.exempt
@login_required
def logout():
    """
    User logout handler.

    Deactivates session in database and clears Flask-Login session.
    """
    # Mark session as inactive in database
    _deactivate_user_session(current_user.id)

    # Clear Flask-Login session
    logout_user()

    return redirect('/login')

@app.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    """
    Password change page for forced password updates.

    Used when must_change_password flag is set (e.g., default admin account).
    User must be logged in to access this page.
    """
    # Security check: only allow if password change is actually required
    if not current_user.must_change_password:
        return redirect('/streams')

    if request.method == 'GET':
        return render_template('change_password.html')

    # POST - handle password change
    new_password = request.form.get('new_password')
    confirm_password = request.form.get('confirm_password')

    if not new_password or new_password != confirm_password:
        return render_template('change_password.html', error='Passwords do not match')

    if len(new_password) < 8:
        return render_template('change_password.html', error='Password must be at least 8 characters')

    # Hash new password with bcrypt
    password_hash = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    # Update database using authenticated user context
    try:
        response = _postgrest_session.patch(
            f"{POSTGREST_URL}/users",
            params={'id': f'eq.{current_user.id}'},
            json={
                'password_hash': password_hash,
                'must_change_password': False
            },
            headers={
                'Prefer': 'return=minimal'
                # TODO: Set RLS context headers here when implementing stricter policies
            },
            timeout=5
        )

        if response.status_code == 204:
            # Log user out so they can verify new password works
            logout_user()
            return redirect('/login')

        return render_template('change_password.html', error='Failed to update password')
    except requests.RequestException as e:
        print(f"Error updating password: {e}")
        return render_template('change_password.html', error='Database error')

# ===== User Management API (Admin Only) =====

@app.route('/api/users', methods=['GET'])
@login_required
def api_get_users():
    """
    Get list of all users (admin only).

    Returns list of users with id, username, role (password_hash excluded).
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    try:
        response = _postgrest_session.get(
            f"{POSTGREST_URL}/users",
            params={'select': 'id,username,role,created_at'},
            timeout=5
        )

        if response.status_code == 200:
            return jsonify(response.json())

        return jsonify({'error': 'Failed to fetch users'}), 500
    except requests.RequestException as e:
        print(f"Error fetching users: {e}")
        return jsonify({'error': 'Database error'}), 500

@app.route('/api/users', methods=['POST'])
@csrf.exempt
@login_required
def api_create_user():
    """
    Create new user (admin only).

    Expects JSON: {username, password, role, must_change_password}
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    try:
        data = request.get_json()
        username = data.get('username')
        password = data.get('password')
        role = data.get('role', 'user')
        must_change_password = data.get('must_change_password', True)  # Default to requiring password change

        if not username or not password:
            return jsonify({'error': 'Username and password required'}), 400

        if len(password) < 8:
            return jsonify({'error': 'Password must be at least 8 characters'}), 400

        # Hash password with bcrypt
        password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        # Create user in database
        response = _postgrest_session.post(
            f"{POSTGREST_URL}/users",
            json={
                'username': username,
                'password_hash': password_hash,
                'role': role,
                'must_change_password': must_change_password
            },
            headers={'Prefer': 'return=representation'},
            timeout=5
        )

        if response.status_code == 201:
            user = response.json()[0]
            return jsonify({
                'success': True,
                'user': {
                    'id': user['id'],
                    'username': user['username'],
                    'role': user['role']
                }
            })

        if response.status_code == 409:
            return jsonify({'error': 'Username already exists'}), 409

        return jsonify({'error': 'Failed to create user'}), 500
    except requests.RequestException as e:
        print(f"Error creating user: {e}")
        return jsonify({'error': 'Database error'}), 500

@app.route('/api/users/<int:user_id>', methods=['PATCH'])
@csrf.exempt
@login_required
def api_update_user(user_id):
    """
    Update user (admin only).

    Expects JSON: {username?, password?, role?}
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    try:
        data = request.get_json()
        update_data = {}

        # Update username if provided
        if 'username' in data:
            update_data['username'] = data['username']

        # Update password if provided
        if 'password' in data and data['password']:
            if len(data['password']) < 8:
                return jsonify({'error': 'Password must be at least 8 characters'}), 400
            password_hash = bcrypt.hashpw(data['password'].encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            update_data['password_hash'] = password_hash

        # Update role if provided
        if 'role' in data:
            update_data['role'] = data['role']

        if not update_data:
            return jsonify({'error': 'No fields to update'}), 400

        # Update user in database
        response = _postgrest_session.patch(
            f"{POSTGREST_URL}/users",
            params={'id': f'eq.{user_id}'},
            json=update_data,
            headers={'Prefer': 'return=representation'},
            timeout=5
        )

        if response.status_code == 200:
            user = response.json()[0]
            return jsonify({
                'success': True,
                'user': {
                    'id': user['id'],
                    'username': user['username'],
                    'role': user['role']
                }
            })

        return jsonify({'error': 'Failed to update user'}), 500
    except requests.RequestException as e:
        print(f"Error updating user: {e}")
        return jsonify({'error': 'Database error'}), 500

@app.route('/api/users/<int:user_id>', methods=['DELETE'])
@csrf.exempt
@login_required
def api_delete_user(user_id):
    """
    Delete user (admin only).

    Cannot delete yourself or the default admin account.
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    # Prevent deleting yourself
    if user_id == current_user.id:
        return jsonify({'error': 'Cannot delete your own account'}), 400

    try:
        # Delete user from database
        response = _postgrest_session.delete(
            f"{POSTGREST_URL}/users",
            params={'id': f'eq.{user_id}'},
            headers={'Prefer': 'return=minimal'},
            timeout=5
        )

        if response.status_code == 204:
            return jsonify({'success': True})

        return jsonify({'error': 'Failed to delete user'}), 500
    except requests.RequestException as e:
        print(f"Error deleting user: {e}")
        return jsonify({'error': 'Database error'}), 500

@app.route('/api/users/<int:user_id>/reset-password', methods=['POST'])
@csrf.exempt
@login_required
def api_reset_user_password(user_id):
    """
    Reset user password (admin only).

    Sets a new temporary password and forces user to change it on next login.
    Validates that new password is different from current password.

    Expects JSON: {new_password}
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    try:
        data = request.get_json()
        new_password = data.get('new_password')

        if not new_password:
            return jsonify({'error': 'New password required'}), 400

        if len(new_password) < 8:
            return jsonify({'error': 'Password must be at least 8 characters'}), 400

        # Get current password hash from database
        user_response = _postgrest_session.get(
            f"{POSTGREST_URL}/users",
            params={'id': f'eq.{user_id}', 'select': 'password_hash'},
            timeout=5
        )

        if user_response.status_code != 200 or not user_response.json():
            return jsonify({'error': 'User not found'}), 404

        current_password_hash = user_response.json()[0]['password_hash']

        # Validate new password is different from current password
        if bcrypt.checkpw(new_password.encode('utf-8'), current_password_hash.encode('utf-8')):
            return jsonify({'error': 'New password must be different from current password'}), 400

        # Hash new password
        new_password_hash = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        # Update password and set must_change_password flag
        response = _postgrest_session.patch(
            f"{POSTGREST_URL}/users",
            params={'id': f'eq.{user_id}'},
            json={
                'password_hash': new_password_hash,
                'must_change_password': True
            },
            headers={'Prefer': 'return=minimal'},
            timeout=5
        )

        if response.status_code == 204:
            return jsonify({'success': True})

        return jsonify({'error': 'Failed to reset password'}), 500
    except requests.RequestException as e:
        print(f"Error resetting password: {e}")
        return jsonify({'error': 'Database error'}), 500

# ===== Device Management API =====

def _register_or_update_device(device_token, user_id, ip_address, user_agent):
    """
    Register a new device or update last_seen for an existing one.

    Uses PostgREST upsert (ON CONFLICT) to atomically create or update.
    Returns the device record.
    """
    try:
        # Try to find existing device
        resp = _postgrest_session.get(
            f"{POSTGREST_URL}/trusted_devices",
            params={
                'device_token': f'eq.{device_token}',
                'select': 'id,device_token,user_id,device_name,ip_address,user_agent,is_trusted,first_seen,last_seen'
            },
            timeout=5
        )
        if resp.status_code == 200 and resp.json():
            # Device exists — update last_seen, ip, user_agent, and user_id
            device = resp.json()[0]
            update_data = {
                'last_seen': datetime.utcnow().isoformat(),
                'ip_address': ip_address,
                'user_agent': user_agent
            }
            if user_id:
                update_data['user_id'] = user_id
            _postgrest_session.patch(
                f"{POSTGREST_URL}/trusted_devices",
                params={'device_token': f'eq.{device_token}'},
                json=update_data,
                headers={'Prefer': 'return=minimal'},
                timeout=5
            )
            device.update(update_data)
            return device

        # New device — insert
        new_device = {
            'device_token': device_token,
            'user_id': user_id,
            'ip_address': ip_address,
            'user_agent': user_agent
        }
        resp = _postgrest_session.post(
            f"{POSTGREST_URL}/trusted_devices",
            json=new_device,
            headers={'Prefer': 'return=representation'},
            timeout=5
        )
        if resp.status_code == 201:
            return resp.json()[0]
        return None
    except requests.RequestException as e:
        print(f"[DeviceManager] Error registering device: {e}")
        return None


@app.route('/api/device/register', methods=['POST'])
@csrf.exempt
@login_required
def api_device_register():
    """
    Register a device token for the current user.

    Called by the frontend on page load. If the client already has a device_token
    in localStorage, it sends it here. Otherwise, the server generates a new one.

    Returns:
        JSON with device_token (to be stored in localStorage by client)
    """
    data = request.get_json() or {}
    client_token = data.get('device_token')

    # Use the client's existing token or generate a new one
    device_token = client_token or str(uuid.uuid4())

    device = _register_or_update_device(
        device_token=device_token,
        user_id=current_user.id,
        ip_address=request.remote_addr,
        user_agent=request.user_agent.string
    )

    if not device:
        return jsonify({'error': 'Failed to register device'}), 500

    # Set the device_token as an httpOnly cookie (backup for localStorage)
    resp = jsonify({
        'device_token': device_token,
        'is_trusted': device.get('is_trusted', False)
    })
    resp.set_cookie(
        'device_token',
        device_token,
        max_age=365 * 24 * 3600,  # 1 year
        httponly=True,
        samesite='Lax',
        secure=False  # Set True when HTTPS enabled
    )
    return resp


@app.route('/api/device/heartbeat', methods=['POST'])
@csrf.exempt
@login_required
def api_device_heartbeat():
    """
    Update last_seen for the current device.

    Called periodically by the connection monitor alongside health checks.
    Updates IP, user_agent, and last_seen timestamp.

    Returns:
        JSON with is_trusted status
    """
    data = request.get_json() or {}
    device_token = data.get('device_token') or request.cookies.get('device_token')

    if not device_token:
        return jsonify({'error': 'No device token'}), 400

    device = _register_or_update_device(
        device_token=device_token,
        user_id=current_user.id,
        ip_address=request.remote_addr,
        user_agent=request.user_agent.string
    )

    if not device:
        return jsonify({'error': 'Failed to update device'}), 500

    return jsonify({'is_trusted': device.get('is_trusted', False)})


@app.route('/api/admin/devices', methods=['GET'])
@login_required
def api_admin_get_devices():
    """
    Get all registered devices (admin only).

    Returns list of devices with user info, IP, last_seen, trust status.
    Devices seen in the last 5 minutes are considered "online".
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    try:
        resp = _postgrest_session.get(
            f"{POSTGREST_URL}/trusted_devices",
            params={
                'select': 'id,device_token,user_id,device_name,ip_address,user_agent,is_trusted,first_seen,last_seen',
                'order': 'last_seen.desc'
            },
            timeout=5
        )
        if resp.status_code != 200:
            return jsonify({'error': 'Failed to fetch devices'}), 500

        devices = resp.json()

        # Enrich with username lookup
        user_cache = {}
        for device in devices:
            uid = device.get('user_id')
            if uid and uid not in user_cache:
                user = User.get_by_id(uid)
                user_cache[uid] = user.username if user else 'unknown'
            device['username'] = user_cache.get(uid, 'unlinked')

        return jsonify(devices)
    except requests.RequestException as e:
        print(f"[DeviceManager] Error fetching devices: {e}")
        return jsonify({'error': 'Database error'}), 500


@app.route('/api/admin/devices/<int:device_id>/trust', methods=['PATCH'])
@csrf.exempt
@login_required
def api_admin_toggle_trust(device_id):
    """
    Toggle trusted status for a device (admin only).

    Expects JSON: {is_trusted: true/false}
    When trusted, the device will auto-login without requiring credentials.
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    data = request.get_json()
    if data is None or 'is_trusted' not in data:
        return jsonify({'error': 'is_trusted field required'}), 400

    try:
        resp = _postgrest_session.patch(
            f"{POSTGREST_URL}/trusted_devices",
            params={'id': f'eq.{device_id}'},
            json={'is_trusted': bool(data['is_trusted'])},
            headers={'Prefer': 'return=representation'},
            timeout=5
        )
        if resp.status_code == 200 and resp.json():
            device = resp.json()[0]
            action = 'trusted' if device['is_trusted'] else 'untrusted'
            print(f"[DeviceManager] Device {device_id} marked as {action} by {current_user.username}")
            return jsonify(device)

        return jsonify({'error': 'Device not found'}), 404
    except requests.RequestException as e:
        print(f"[DeviceManager] Error toggling trust: {e}")
        return jsonify({'error': 'Database error'}), 500


@app.route('/api/admin/devices/<int:device_id>/name', methods=['PATCH'])
@csrf.exempt
@login_required
def api_admin_rename_device(device_id):
    """
    Set a friendly name for a device (admin only).

    Expects JSON: {device_name: "Living Room iPad"}
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    data = request.get_json()
    if data is None or 'device_name' not in data:
        return jsonify({'error': 'device_name field required'}), 400

    try:
        resp = _postgrest_session.patch(
            f"{POSTGREST_URL}/trusted_devices",
            params={'id': f'eq.{device_id}'},
            json={'device_name': str(data['device_name'])[:100]},
            headers={'Prefer': 'return=representation'},
            timeout=5
        )
        if resp.status_code == 200 and resp.json():
            return jsonify(resp.json()[0])

        return jsonify({'error': 'Device not found'}), 404
    except requests.RequestException as e:
        print(f"[DeviceManager] Error renaming device: {e}")
        return jsonify({'error': 'Database error'}), 500


@app.route('/api/admin/devices/<int:device_id>', methods=['DELETE'])
@csrf.exempt
@login_required
def api_admin_delete_device(device_id):
    """
    Delete a registered device (admin only).

    Removes the device from the database entirely.
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    try:
        resp = _postgrest_session.delete(
            f"{POSTGREST_URL}/trusted_devices",
            params={'id': f'eq.{device_id}'},
            headers={'Prefer': 'return=minimal'},
            timeout=5
        )
        if resp.status_code == 204:
            print(f"[DeviceManager] Device {device_id} deleted by {current_user.username}")
            return jsonify({'success': True})

        return jsonify({'error': 'Device not found'}), 404
    except requests.RequestException as e:
        print(f"[DeviceManager] Error deleting device: {e}")
        return jsonify({'error': 'Database error'}), 500


# ===== User Camera Access Control =====

@app.route('/api/users/<int:user_id>/camera-access', methods=['GET'])
@csrf.exempt
@login_required
def api_get_user_camera_access(user_id):
    """
    Get camera access list for a user (admin only).

    Returns list of camera serials the user is allowed to see.
    Empty list means user can see ALL cameras (default).
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    try:
        response = _postgrest_session.get(
            f"{POSTGREST_URL}/user_camera_access",
            params={'user_id': f'eq.{user_id}', 'select': 'camera_serial,allowed'},
            timeout=5
        )

        if response.status_code == 200:
            return jsonify(response.json())

        return jsonify([])
    except requests.RequestException as e:
        print(f"Error fetching camera access: {e}")
        return jsonify({'error': 'Database error'}), 500


@app.route('/api/users/<int:user_id>/camera-access', methods=['PUT'])
@csrf.exempt
@login_required
def api_set_user_camera_access(user_id):
    """
    Set camera access for a user (admin only).

    Expects JSON: {cameras: [{camera_serial, allowed}, ...]}
    Replaces all existing access rules for the user.
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    try:
        data = request.get_json()
        cameras = data.get('cameras', [])

        # Delete existing access rules for this user
        _postgrest_session.delete(
            f"{POSTGREST_URL}/user_camera_access",
            params={'user_id': f'eq.{user_id}'},
            headers={'Prefer': 'return=minimal'},
            timeout=5
        )

        # Insert new access rules (only for cameras that are allowed)
        allowed_cameras = [c for c in cameras if c.get('allowed', False)]
        if allowed_cameras:
            rows = [
                {
                    'user_id': user_id,
                    'camera_serial': c['camera_serial'],
                    'allowed': True
                }
                for c in allowed_cameras
            ]
            response = _postgrest_session.post(
                f"{POSTGREST_URL}/user_camera_access",
                json=rows,
                headers={'Prefer': 'return=minimal'},
                timeout=5
            )

            if response.status_code not in [200, 201]:
                return jsonify({'error': 'Failed to save camera access'}), 500

        return jsonify({'success': True})
    except requests.RequestException as e:
        print(f"Error saving camera access: {e}")
        return jsonify({'error': 'Database error'}), 500


@app.route('/api/my-camera-access', methods=['GET'])
@csrf.exempt
@login_required
def api_get_my_camera_access():
    """
    Get current user's camera access list.

    Admins always get all cameras.
    For regular users: if no access rules exist, they see all cameras.
    If access rules exist, they only see allowed cameras.
    """
    # Admins always see everything
    if current_user.role == 'admin':
        return jsonify({'all_access': True, 'cameras': []})

    try:
        response = _postgrest_session.get(
            f"{POSTGREST_URL}/user_camera_access",
            params={'user_id': f'eq.{current_user.id}', 'select': 'camera_serial,allowed'},
            timeout=5
        )

        if response.status_code == 200:
            access_list = response.json()
            if not access_list:
                # No restrictions set - user sees all cameras
                return jsonify({'all_access': True, 'cameras': []})
            else:
                # Return only allowed camera serials
                allowed = [a['camera_serial'] for a in access_list if a.get('allowed', False)]
                return jsonify({'all_access': False, 'cameras': allowed})

        return jsonify({'all_access': True, 'cameras': []})
    except requests.RequestException as e:
        print(f"Error fetching user camera access: {e}")
        return jsonify({'all_access': True, 'cameras': []})


@app.route('/api/my-preferences', methods=['GET'])
@csrf.exempt
@login_required
def api_get_my_preferences():
    """
    Get current user's display preferences (hidden cameras, HD cameras).
    Returns defaults if no preferences saved yet.
    """
    try:
        response = _postgrest_session.get(
            f"{POSTGREST_URL}/user_preferences",
            params={
                'user_id': f'eq.{current_user.id}',
                'select': 'hidden_cameras,hd_cameras,default_video_fit,pinned_camera,pinned_windows'
            },
            timeout=5
        )

        if response.status_code == 200:
            rows = response.json()
            if rows:
                return jsonify(rows[0])

        # No preferences saved yet - return defaults
        return jsonify({'hidden_cameras': [], 'hd_cameras': [], 'default_video_fit': 'cover', 'pinned_camera': None, 'pinned_windows': {}})
    except requests.RequestException as e:
        logger.error(f"Error fetching user preferences: {e}")
        return jsonify({'hidden_cameras': [], 'hd_cameras': [], 'default_video_fit': 'cover', 'pinned_camera': None, 'pinned_windows': {}})


@app.route('/api/my-preferences', methods=['PUT'])
@csrf.exempt
@login_required
def api_put_my_preferences():
    """
    Save current user's display preferences (hidden cameras, HD cameras).
    Uses upsert: creates row if none exists, updates if it does.
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    payload = {'user_id': current_user.id}
    if 'hidden_cameras' in data:
        payload['hidden_cameras'] = data['hidden_cameras']
    if 'hd_cameras' in data:
        payload['hd_cameras'] = data['hd_cameras']
    if 'default_video_fit' in data:
        if data['default_video_fit'] not in ('cover', 'fill'):
            return jsonify({'error': 'default_video_fit must be "cover" or "fill"'}), 400
        payload['default_video_fit'] = data['default_video_fit']
    if 'pinned_camera' in data:
        # Accept string serial or null to clear the pin
        val = data['pinned_camera']
        payload['pinned_camera'] = val if isinstance(val, str) else None
    if 'pinned_windows' in data:
        # Accept dict mapping serial → {x, y, w, h} window position/size
        val = data['pinned_windows']
        payload['pinned_windows'] = val if isinstance(val, dict) else {}

    try:
        # Upsert: use Prefer: resolution=merge-duplicates with the unique constraint on user_id
        response = _postgrest_session.post(
            f"{POSTGREST_URL}/user_preferences",
            json=payload,
            headers={
                'Prefer': 'resolution=merge-duplicates,return=representation',
            },
            timeout=5
        )

        if response.status_code in (200, 201):
            rows = response.json()
            if rows:
                return jsonify(rows[0])
            return jsonify({'status': 'saved'})
        else:
            logger.error(f"Failed to save preferences: {response.status_code} {response.text}")
            return jsonify({'error': 'Failed to save preferences'}), 500
    except requests.RequestException as e:
        logger.error(f"Error saving user preferences: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/camera/<camera_serial>/display', methods=['GET'])
@csrf.exempt
@login_required
def api_get_camera_display(camera_serial):
    """
    Get per-camera display settings (currently: video_fit_mode).
    Returns {'video_fit_mode': 'cover'|'fill'|None} — None means use user default.
    """
    try:
        response = _postgrest_session.get(
            f"{POSTGREST_URL}/cameras",
            params={
                'serial': f'eq.{camera_serial}',
                'select': 'serial,video_fit_mode'
            },
            timeout=5
        )
        if response.status_code == 200:
            rows = response.json()
            if rows:
                return jsonify({'video_fit_mode': rows[0].get('video_fit_mode')})
        return jsonify({'video_fit_mode': None})
    except requests.RequestException as e:
        logger.error(f"Error fetching camera display settings for {camera_serial}: {e}")
        return jsonify({'video_fit_mode': None})


@app.route('/api/camera/<camera_serial>/display', methods=['PUT'])
@csrf.exempt
@login_required
def api_put_camera_display(camera_serial):
    """
    Set per-camera video fit mode.
    Body: { "video_fit_mode": "cover" | "fill" | null }
    null clears the override — camera falls back to user default.
    """
    data = request.get_json()
    if data is None:
        return jsonify({'error': 'No data provided'}), 400

    fit = data.get('video_fit_mode')
    if fit is not None and fit not in ('cover', 'fill'):
        return jsonify({'error': 'video_fit_mode must be "cover", "fill", or null'}), 400

    try:
        response = _postgrest_session.patch(
            f"{POSTGREST_URL}/cameras",
            params={'serial': f'eq.{camera_serial}'},
            json={'video_fit_mode': fit},
            timeout=5
        )
        if response.status_code in (200, 204):
            return jsonify({'status': 'saved', 'video_fit_mode': fit})
        logger.error(f"Failed to save camera display setting: {response.status_code} {response.text}")
        return jsonify({'error': 'Failed to save'}), 500
    except requests.RequestException as e:
        logger.error(f"Error saving camera display setting for {camera_serial}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/my-camera-order', methods=['PUT'])
@csrf.exempt
@login_required
def api_put_camera_order():
    """
    Save the user's preferred camera tile order.
    Body: { "order": ["serial1", "serial2", ...] }
    Upserts display_order values into user_camera_preferences for each serial.
    """
    data = request.get_json()
    if not data or 'order' not in data:
        return jsonify({'error': 'order array is required'}), 400

    order = data['order']
    if not isinstance(order, list):
        return jsonify({'error': 'order must be an array of camera serials'}), 400

    try:
        # Upsert one row per camera with its new display_order index
        rows = [
            {
                'user_id': current_user.id,
                'camera_serial': serial,
                'display_order': idx
            }
            for idx, serial in enumerate(order)
        ]
        response = _postgrest_session.post(
            f"{POSTGREST_URL}/user_camera_preferences",
            json=rows,
            headers={'Prefer': 'resolution=merge-duplicates'},
            timeout=5
        )
        if response.status_code in (200, 201):
            return jsonify({'status': 'saved', 'count': len(rows)})
        logger.error(f"Failed to save camera order: {response.status_code} {response.text}")
        return jsonify({'error': 'Failed to save order'}), 500
    except requests.RequestException as e:
        logger.error(f"Error saving camera order: {e}")
        return jsonify({'error': str(e)}), 500


# Valid stream types matching the CHECK constraint in user_camera_preferences table
VALID_STREAM_TYPES = {'MJPEG', 'HLS', 'LL_HLS', 'WEBRTC', 'NEOLINK', 'NEOLINK_LL_HLS', 'GO2RTC'}


@app.route('/api/user/stream-preferences', methods=['GET'])
@csrf.exempt
@login_required
def api_get_stream_preferences():
    """
    Get current user's per-camera stream type preferences.
    Returns list of {camera_serial, preferred_stream_type} for all cameras
    where the user has set a preference.
    """
    try:
        response = _postgrest_session.get(
            f"{POSTGREST_URL}/user_camera_preferences",
            params={
                'user_id': f'eq.{current_user.id}',
                'select': 'camera_serial,preferred_stream_type'
            },
            timeout=5
        )

        if response.status_code == 200:
            return jsonify(response.json())

        return jsonify([])
    except requests.RequestException as e:
        logger.error(f"Error fetching stream preferences: {e}")
        return jsonify([])


@app.route('/api/user/stream-preferences/<camera_serial>', methods=['PUT'])
@csrf.exempt
@login_required
def api_put_stream_preference(camera_serial):
    """
    Save or update stream type preference for a specific camera.
    Uses upsert on the (user_id, camera_serial) unique constraint.

    Body: { "preferred_stream_type": "WEBRTC" }
    """
    data = request.get_json()
    if not data or 'preferred_stream_type' not in data:
        return jsonify({'error': 'preferred_stream_type is required'}), 400

    stream_type = data['preferred_stream_type']
    if stream_type not in VALID_STREAM_TYPES:
        return jsonify({'error': f'Invalid stream type. Must be one of: {", ".join(sorted(VALID_STREAM_TYPES))}'}), 400

    try:
        response = _postgrest_session.post(
            f"{POSTGREST_URL}/user_camera_preferences",
            json={
                'user_id': current_user.id,
                'camera_serial': camera_serial,
                'preferred_stream_type': stream_type
            },
            headers={
                'Prefer': 'resolution=merge-duplicates,return=representation',
            },
            timeout=5
        )

        if response.status_code in (200, 201):
            rows = response.json()
            if rows:
                return jsonify(rows[0])
            return jsonify({'status': 'saved'})
        else:
            logger.error(f"Failed to save stream preference: {response.status_code} {response.text}")
            return jsonify({'error': 'Failed to save stream preference'}), 500
    except requests.RequestException as e:
        logger.error(f"Error saving stream preference: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/mediamtx/path-status/<camera_serial>', methods=['GET'])
@csrf.exempt
@login_required
def api_mediamtx_path_status(camera_serial):
    """
    Check if a MediaMTX path exists and has an active publisher for a camera.
    Used by the frontend to validate before switching to MediaMTX-based stream types
    (WebRTC, HLS, LL_HLS). MJPEG doesn't need MediaMTX.

    Returns:
        {ready: bool, path: str, message: str}
    """
    try:
        # Resolve MediaMTX path name from camera config
        from services.camera_repository import CameraRepository
        camera_config = CameraRepository.get_camera(camera_serial) or {}
        path_name = camera_config.get('packager_path') or camera_serial

        # Query MediaMTX API for path list
        resp = requests.get(
            'http://nvr-packager:9997/v3/paths/list',
            auth=('nvr-api', ''),
            timeout=3
        )

        if resp.status_code != 200:
            return jsonify({
                'ready': False,
                'path': path_name,
                'message': 'MediaMTX API unavailable'
            })

        paths_data = resp.json()
        for item in paths_data.get('items', []):
            if item.get('name') == path_name:
                is_ready = bool(item.get('ready', False))
                return jsonify({
                    'ready': is_ready,
                    'path': path_name,
                    'message': 'Stream source active' if is_ready else 'Path exists but no active publisher'
                })

        # Path not found at all
        return jsonify({
            'ready': False,
            'path': path_name,
            'message': f'No MediaMTX path "{path_name}" found. The server may need a full restart (start.sh) to configure streaming paths.'
        })

    except requests.RequestException as e:
        logger.error(f"Error checking MediaMTX path for {camera_serial}: {e}")
        return jsonify({
            'ready': False,
            'path': camera_serial,
            'message': 'Could not reach MediaMTX service'
        })


@app.route('/api/mediamtx/create-path/<camera_serial>', methods=['POST'])
@csrf.exempt
@login_required
def api_mediamtx_create_path(camera_serial):
    """Dynamically create MediaMTX paths for a camera and start the FFmpeg publisher.

    Used when switching stream type from MJPEG to a MediaMTX-based type
    (WebRTC, HLS, LL_HLS). MJPEG cameras don't have MediaMTX paths by default
    because they connect directly to camera endpoints. This endpoint creates both
    sub and main paths on demand and starts FFmpeg to publish into them.

    Returns:
        {success: bool, paths_created: list, stream_url: str, message: str}
    """
    try:
        camera = camera_repo.get_camera(camera_serial)
        if not camera:
            return jsonify({'success': False, 'error': 'Camera not found'}), 404

        path_name = camera.get('packager_path') or camera_serial
        paths_to_create = [path_name, f"{path_name}_main"]
        created = []

        for p in paths_to_create:
            try:
                resp = requests.post(
                    f'http://nvr-packager:9997/v3/config/paths/add/{p}',
                    json={'source': 'publisher'},
                    auth=('nvr-api', ''),
                    timeout=5
                )
                if resp.status_code in (200, 201):
                    created.append(p)
                    logger.info(f"[MEDIAMTX] Created path: {p}")
                elif resp.status_code == 409:
                    # Path already exists — treat as success
                    created.append(p)
                    logger.info(f"[MEDIAMTX] Path {p} already exists")
                else:
                    logger.warning(f"[MEDIAMTX] Failed to create path {p}: "
                                   f"{resp.status_code} {resp.text}")
            except requests.RequestException as e:
                logger.error(f"[MEDIAMTX] Error creating path {p}: {e}")

        if len(created) < 2:
            return jsonify({
                'success': False,
                'paths_created': created,
                'error': 'Failed to create all required MediaMTX paths'
            }), 500

        # Determine target protocol for FFmpeg startup.
        # When switching from MJPEG, the camera's stored stream_type is still MJPEG,
        # so we must override it to actually start FFmpeg for the target protocol.
        data = request.get_json() or {}
        target_type = data.get('target_type', 'LL_HLS').upper()
        if target_type == 'MJPEG':
            # Nonsensical: creating MediaMTX path for MJPEG. Default to LL_HLS.
            target_type = 'LL_HLS'

        # Start FFmpeg publisher with protocol override (sub stream — dual-output publishes both)
        stream_url = stream_manager.start_stream(
            camera_serial, resolution='sub', protocol_override=target_type)

        return jsonify({
            'success': True,
            'paths_created': created,
            'stream_url': stream_url,
            'target_type': target_type,
            'message': f'MediaMTX paths created and FFmpeg publisher started for {path_name}'
        })

    except Exception as e:
        logger.error(f"[MEDIAMTX] Error creating paths for {camera_serial}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


def _get_allowed_camera_serials(user):
    """
    Get set of allowed camera serials for a user.
    Returns None if user has unrestricted access (admin or no rules set).
    Returns a set of serial strings if restricted.
    """
    if user.role == 'admin':
        return None  # No restriction

    try:
        response = _postgrest_session.get(
            f"{POSTGREST_URL}/user_camera_access",
            params={'user_id': f'eq.{user.id}', 'select': 'camera_serial,allowed'},
            timeout=5
        )
        if response.status_code == 200:
            access_list = response.json()
            if not access_list:
                return None  # No restrictions
            return set(a['camera_serial'] for a in access_list if a.get('allowed', False))
    except requests.RequestException:
        pass

    return None  # Default: no restriction on error


def _filter_cameras(cameras, allowed_serials):
    """
    Filter camera dict by allowed serials.
    If allowed_serials is None, returns all cameras (no restriction).
    """
    if allowed_serials is None:
        return cameras
    return {
        serial: info for serial, info in cameras.items()
        if serial in allowed_serials
    }


# ===== Main UI Routes =====
@app.route('/')
@login_required
def index():
    """Redirect to streams page (main interface)"""
    return redirect('/streams')

@app.route('/streams')
@login_required
def streams_page():
    """Multi-stream viewing page"""
    try:
        cameras = camera_repo.get_streaming_cameras()
        ui_health = _ui_health_from_env()

        # Filter cameras based on user's access permissions
        allowed = _get_allowed_camera_serials(current_user)
        cameras = _filter_cameras(cameras, allowed)

        # Pass full camera configs (includes ui_health_monitor per camera)
        return render_template('streams.html', cameras=cameras, ui_health=ui_health)
    except Exception as e:
        print(f"Error loading streams page: {e}")
        return f"Error loading streams page: {e}", 500

@app.route('/reloading')
@login_required
def reloading_page():
    """Reconnection page shown when server is restarting"""
    return render_template('reloading.html')

# ===== Status Routes =====
@app.route('/api/health')
def api_health():
    """
    Lightweight health check endpoint
    Returns shutdown status to warn clients before server goes down
    """
    if app_state.is_shutting_down:
        return jsonify({
            'status': 'shutting_down',
            'message': 'Server is shutting down'
        }), 503

    return jsonify({
        'status': 'ok',
        'message': 'Server is healthy'
    }), 200

@app.route('/api/status')
@login_required
def api_status():
    """Get system status"""
    eufy_status = {
        'bridge_configured': eufy_bridge is not None,
        'bridge_running': eufy_bridge.is_running() if eufy_bridge else False,
        'bridge_ready': eufy_bridge.is_ready() if eufy_bridge else False,
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
@login_required
def api_cameras():
    """Get list of available cameras, filtered by user access permissions"""
    allowed = _get_allowed_camera_serials(current_user)
    return jsonify({
        'all': _filter_cameras(camera_repo.get_all_cameras(), allowed),
        'ptz': _filter_cameras(camera_repo.get_ptz_cameras(), allowed),
        'streaming': _filter_cameras(camera_repo.get_streaming_cameras(), allowed)
    })

@app.route('/api/cameras/<camera_id>')
@login_required
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


@app.route('/api/camera/<camera_serial>/name', methods=['PUT'])
@csrf.exempt
@login_required
def api_camera_rename(camera_serial):
    """
    Rename a camera. Updates the name in the database and cameras.json.

    Request body: {"name": "New Camera Name"}

    Returns:
        200: {"success": true, "serial": "...", "name": "..."}
        400: Missing or invalid name
        404: Camera not found
        500: Update failed
    """
    try:
        data = request.get_json()
        if not data or 'name' not in data:
            return jsonify({'error': 'Missing "name" field in request body'}), 400

        new_name = str(data['name']).strip()
        if not new_name:
            return jsonify({'error': 'Camera name cannot be empty'}), 400

        if len(new_name) > 255:
            return jsonify({'error': 'Camera name must be 255 characters or fewer'}), 400

        # Verify camera exists
        camera = camera_repo.get_camera(camera_serial)
        if not camera:
            return jsonify({'error': f'Camera not found: {camera_serial}'}), 404

        old_name = camera.get('name', camera_serial)

        # Update via CameraRepository (handles DB + JSON + in-memory cache)
        success = camera_repo.update_camera_setting(camera_serial, 'name', new_name)

        if not success:
            return jsonify({'error': 'Failed to update camera name'}), 500

        logger.info(f"Camera renamed: {camera_serial} '{old_name}' -> '{new_name}'")

        return jsonify({
            'success': True,
            'serial': camera_serial,
            'name': new_name,
            'previous_name': old_name
        })

    except Exception as e:
        logger.error(f"Error renaming camera {camera_serial}: {e}")
        return jsonify({'error': str(e)}), 500


# ===== Streaming Configuration Routes =====

@app.route('/api/config/streaming')
@login_required
def api_streaming_config():
    """
    Get streaming configuration for frontend.

    Returns WebRTC settings so frontend can determine:
    - Whether iOS can use WebRTC (requires DTLS)
    - ICE server configuration for NAT traversal

    Used by stream.js to decide streaming method per device.
    """
    try:
        # Access cameras_data which contains the full cameras.json config
        webrtc_settings = camera_repo.cameras_data.get('webrtc_global_settings', {})
        return jsonify({
            'webrtc': {
                'encryption_enabled': webrtc_settings.get('enable_dtls', False),
                'ice_servers': webrtc_settings.get('ice_servers', [])
            }
        })
    except Exception as e:
        print(f"[Config] Error getting streaming config: {e}")
        return jsonify({
            'webrtc': {
                'encryption_enabled': False,
                'ice_servers': []
            }
        })

# ===== Bridge Control Routes =====
@app.route('/api/bridge/start', methods=['POST'])
@csrf.exempt
@login_required
def api_bridge_start():
    """Start the Eufy bridge"""
    try:
        if not eufy_bridge:
            return jsonify({'success': False, 'error': 'Eufy bridge not configured (USE_EUFY_BRIDGE=0)'}), 503
        success = eufy_bridge.start()
        return jsonify({
            'success': success,
            'message': 'Bridge started successfully' if success else 'Failed to start bridge'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/bridge/stop', methods=['POST'])
@csrf.exempt
@login_required
def api_bridge_stop():
    """Stop the Eufy bridge"""
    try:
        if not eufy_bridge:
            return jsonify({'success': False, 'error': 'Eufy bridge not configured'}), 503
        eufy_bridge.stop()
        return jsonify({'success': True, 'message': 'Bridge stopped'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ===== Device Management Routes =====
@app.route('/api/devices/refresh', methods=['POST'])
@csrf.exempt
@login_required
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


@app.route('/api/cameras/force-sync', methods=['POST'])
@csrf.exempt
@login_required
def api_force_sync_cameras():
    """
    Force-sync all camera configurations from cameras.json to database.
    Used for reset operations when cameras.json is the canonical source.
    Admin-only operation.
    """
    if not current_user or current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    try:
        from services.camera_config_sync import force_sync_from_json
        updated = force_sync_from_json('./config/cameras.json')

        # Reload repository from DB to pick up changes
        camera_repo.reload()

        return jsonify({
            'success': True,
            'cameras_updated': updated,
            'total_devices': camera_repo.get_camera_count(),
            'source': camera_repo.get_data_source(),
            'message': f'Force-synced {updated} cameras from cameras.json to database'
        })
    except Exception as e:
        logger.error(f"Force sync failed: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cameras/data-source', methods=['GET'])
@csrf.exempt
@login_required
def api_camera_data_source():
    """Get the current camera data source (database or json)."""
    return jsonify({
        'source': camera_repo.get_data_source(),
        'total_devices': camera_repo.get_camera_count(include_hidden=True),
        'visible_devices': camera_repo.get_camera_count(include_hidden=False),
        'last_updated': camera_repo.get_last_updated()
    })


# ===== Streaming Routes =====
# RTMP
@app.route('/api/camera/<camera_serial>/flv')
@csrf.exempt
@login_required
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
@login_required
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

        # Resolve effective stream type (user preference overrides camera default)
        # This allows per-user stream type switching to actually work end-to-end
        stream_type = camera_repo.get_effective_stream_type(
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
        if not ptz_validator.is_streaming_capable(camera_serial):
            return jsonify({'success': False, 'error': 'Camera does not support streaming'}), 400

        # Extract resolution from request (defaults to 'sub' for grid view)
        # 'sub' = low-res for grid, 'main' = high-res for fullscreen
        data = request.get_json() or {}
        resolution = data.get('type', 'sub')  # 'main' or 'sub'

        print(f"[API] /api/stream/start/{camera_serial} - resolution={resolution}, effective_type={stream_type}")

        # Start the stream with specified resolution.
        # Pass effective stream_type as protocol_override so that cameras whose stored
        # config says MJPEG but whose user preference says WEBRTC/HLS actually start FFmpeg.
        stream_url = stream_manager.start_stream(
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

@app.route('/api/stream/stop/<camera_serial>', methods=['POST'])
@csrf.exempt
@login_required
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

@app.route('/api/stream/restart/<camera_serial>', methods=['POST'])
@csrf.exempt
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
    try:
        camera_name = camera_repo.get_camera_name(camera_serial)
        camera = camera_repo.get_camera(camera_serial)

        if not camera:
            return jsonify({
                'success': False,
                'error': f'Camera {camera_serial} not found'
            }), 404

        # Get resolution from request (defaults to 'sub')
        data = request.get_json() or {}
        resolution = data.get('type', 'sub')

        # Resolve effective stream type (user preference overrides camera default)
        stream_type = camera_repo.get_effective_stream_type(
            camera_serial, user_id=current_user.id if current_user else None).upper()
        if stream_type == 'MJPEG':
            return jsonify({
                'success': False,
                'error': 'MJPEG streams do not support restart (stateless)'
            }), 400

        logger.info(f"[RESTART] Restarting stream for {camera_name} ({camera_serial})")

        # Clear watchdog cooldown so it doesn't block this manual restart
        try:
            stream_watchdog.clear_cooldown(camera_serial)
        except Exception as e:
            logger.debug(f"[RESTART] Could not clear watchdog cooldown: {e}")

        # Step 1: Stop the stream (kills FFmpeg) or clear zombie slot
        was_running = stream_manager.is_stream_alive(camera_serial)
        has_slot = camera_serial in stream_manager.active_streams

        if has_slot:
            slot_status = stream_manager.active_streams.get(camera_serial, {}).get('status', 'unknown')
            logger.info(f"[RESTART] Found existing slot for {camera_name} (status: {slot_status}, alive: {was_running})")

            if was_running:
                stop_success = stream_manager.stop_stream(camera_serial)
                if not stop_success:
                    logger.warning(f"[RESTART] Stop returned False for {camera_name}, continuing anyway")
            else:
                # Zombie slot: has entry but process isn't running
                logger.warning(f"[RESTART] Removing zombie slot for {camera_name} (status: {slot_status})")
                stream_manager.active_streams.pop(camera_serial, None)

            # Brief pause to let sockets release
            import time
            time.sleep(0.5)

        # Step 2: Start fresh stream
        stream_url = stream_manager.start_stream(camera_serial, resolution=resolution)

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
            ready = camera_state_tracker.wait_for_publisher_ready(
                camera_serial, timeout=15
            )
            if ready:
                camera_state_tracker.register_success(camera_serial)
            else:
                logger.warning(
                    f"[RESTART] Publisher not confirmed ready for {camera_name} "
                    f"(FFmpeg may still be connecting to camera)"
                )
            # Broadcast stream_restarted so frontend HLS.js refreshes
            if stream_watchdog:
                stream_watchdog._broadcast_stream_restarted(camera_serial)

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


@app.route('/api/stream/status/<camera_serial>')
@login_required
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

@app.route('/api/camera/state/<camera_id>')
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
    try:
        # All cameras (LL-HLS and MJPEG) now use CameraStateTracker
        # MJPEG capture services report their state via update_mjpeg_capture_state()
        # LL-HLS cameras get state from MediaMTX API polling
        state = camera_state_tracker.get_camera_state(camera_id)

        # Get camera config for stream_type field
        camera = camera_repo.get_camera(camera_id)
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
            'can_retry': camera_state_tracker.can_retry(camera_id)
        })

    except Exception as e:
        logger.error(f"Error getting camera state for {camera_id}: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e),
            'camera_id': camera_id
        }), 500

@app.route('/api/camera/states')
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
    try:
        states = {}
        with camera_state_tracker._lock:
            for camera_id, state in camera_state_tracker._states.items():
                camera = camera_repo.get_camera(camera_id)
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
                    'can_retry': camera_state_tracker.can_retry(camera_id),
                }

        return jsonify({'success': True, 'states': states})

    except Exception as e:
        logger.error(f"Error getting batch camera states: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/streams')
@login_required
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
@login_required
def api_active_streams():
    """Get all active streams (alias)"""
    return api_streams()

@app.route('/api/streams/stop-all', methods=['POST'])
@csrf.exempt
@login_required
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

########################################################-########################################################
#                                           ⚙️⚙️⚙️⚙️ HLS ⚙️⚙️⚙️⚙️
########################################################-########################################################
# ===== HLS Playlist and Segment Serving =====
@app.route('/api/streams/<camera_serial>/playlist.m3u8')
@login_required
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
@login_required
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

########################################################-########################################################
#                                           ⚙️⚙️⚙️⚙️ UNIFI ⚙️⚙️⚙️⚙️
########################################################-########################################################
# ===== UniFi Camera Routes =====
@app.route('/api/unifi/cameras')
@login_required
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
@login_required
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
@login_required
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
@login_required
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
@login_required
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
@login_required
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
@login_required
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
@login_required
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

########################################################-########################################################
#                                    MEDIASERVER MJPEG (tap MediaMTX)
########################################################-########################################################
@app.route('/api/mediaserver/<camera_id>/stream/mjpeg')
@csrf.exempt
@login_required
def api_mediaserver_stream_mjpeg(camera_id):
    """
    MJPEG stream for cameras with mjpeg_source: "mediaserver"

    Taps the existing MediaMTX RTSP output (from dual-output FFmpeg) and
    extracts JPEG frames. Used for single-connection cameras (Eufy, SV3C,
    Neolink) that can't open a second connection for native MJPEG.

    This enables MJPEG grid view on iOS/portable devices for ALL camera types.
    """
    logger.info(f"Client requesting MediaServer MJPEG stream for {camera_id}")

    try:
        # Get camera configuration
        camera_config = camera_repo.get_camera(camera_id)
        if not camera_config:
            return jsonify({'error': 'Camera not found'}), 404

        # Note: This endpoint taps MediaMTX RTSP for MJPEG frames.
        # Only works for cameras that publish to MediaMTX (LL_HLS, HLS, WEBRTC).
        # Cameras with stream_type: MJPEG use vendor-specific endpoints instead.

        # Add client to capture service
        if not mediaserver_mjpeg_service.add_client(camera_id, camera_config):
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
                    frame_data = mediaserver_mjpeg_service.get_latest_frame(camera_id)

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
                mediaserver_mjpeg_service.remove_client(camera_id)
                logger.debug(f"MediaServer MJPEG {camera_id}: Generator cleanup complete (served {frame_count} frames)")

        response = Response(generate(),
                            mimetype='multipart/x-mixed-replace; boundary=jpgboundary')
        # Disable buffering at all layers for streaming
        response.headers['X-Accel-Buffering'] = 'no'
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
        return response

    except Exception as e:
        mediaserver_mjpeg_service.remove_client(camera_id)
        return jsonify({'error': f'Stream error: {e}'}), 500


@app.route('/api/status/mediaserver-mjpeg')
@login_required
def api_mediaserver_mjpeg_status():
    """Get status of all MediaServer MJPEG capture processes"""
    try:
        status = mediaserver_mjpeg_service.get_all_status()
        return jsonify({
            'success': True,
            'captures': status,
            'active_count': len(status)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/status/mediaserver-mjpeg/<camera_id>')
@login_required
def api_mediaserver_mjpeg_status_single(camera_id):
    """Get status of specific MediaServer MJPEG capture process"""
    try:
        status = mediaserver_mjpeg_service.get_status(camera_id)
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


########################################################-########################################################
#                                    🔌 WEBSOCKET MJPEG (MULTIPLEXED) 🔌
########################################################-########################################################

@socketio.on('connect', namespace='/mjpeg')
def ws_mjpeg_connect():
    """
    Handle WebSocket connection for MJPEG streaming.

    Client connects to /mjpeg namespace, then emits 'subscribe' with camera list.
    This bypasses browser's ~6 HTTP connection limit by multiplexing all camera
    streams over a single WebSocket connection.
    """
    from flask import request as flask_request
    sid = flask_request.sid
    logger.info(f"WebSocket MJPEG: Client {sid[:8]}... connected")
    emit('connected', {'status': 'ok', 'sid': sid})


@socketio.on('disconnect', namespace='/mjpeg')
def ws_mjpeg_disconnect():
    """Handle WebSocket disconnection"""
    from flask import request as flask_request
    sid = flask_request.sid
    websocket_mjpeg_service.remove_client(sid)
    logger.info(f"WebSocket MJPEG: Client {sid[:8]}... disconnected")


@socketio.on('subscribe', namespace='/mjpeg')
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

    camera_ids = data.get('cameras', [])
    if not camera_ids:
        emit('error', {'message': 'No cameras specified'})
        return

    # Validate cameras exist
    valid_cameras = []
    for camera_id in camera_ids:
        if camera_repo.get_camera(camera_id):
            valid_cameras.append(camera_id)
        else:
            logger.warning(f"WebSocket MJPEG: Unknown camera {camera_id}")

    if not valid_cameras:
        emit('error', {'message': 'No valid cameras specified'})
        return

    # Register client subscription
    websocket_mjpeg_service.add_client(sid, valid_cameras)

    emit('subscribed', {
        'cameras': valid_cameras,
        'count': len(valid_cameras)
    })


@socketio.on('unsubscribe', namespace='/mjpeg')
def ws_mjpeg_unsubscribe(data=None):
    """Unsubscribe client from all camera streams"""
    from flask import request as flask_request
    sid = flask_request.sid
    websocket_mjpeg_service.remove_client(sid)
    emit('unsubscribed', {'status': 'ok'})


# ============================================================================
# Stream Events WebSocket (notify frontend of backend stream restarts)
# ============================================================================
# Frontend connects to /stream_events namespace to receive real-time
# notifications when StreamWatchdog restarts a stream. This allows instant
# HLS refresh instead of waiting for the 10-second polling cycle.

@socketio.on('connect', namespace='/stream_events')
def handle_stream_events_connect():
    """
    Handle WebSocket connection for stream event notifications.

    Frontend connects to /stream_events namespace to receive real-time
    notifications when StreamWatchdog restarts a stream.
    """
    from flask import request as flask_request
    sid = flask_request.sid
    logger.info(f"StreamEvents: Client {sid[:8]}... connected")
    emit('connected', {'status': 'ok', 'sid': sid})


@socketio.on('disconnect', namespace='/stream_events')
def handle_stream_events_disconnect():
    """Handle WebSocket disconnection from stream events namespace"""
    from flask import request as flask_request
    sid = flask_request.sid
    logger.info(f"StreamEvents: Client {sid[:8]}... disconnected")


# ============================================================================
# Two-Way Audio (Talkback) WebSocket Namespace
# ============================================================================
# Clients connect to /talkback to send microphone audio to cameras.
# Uses push-to-talk model: client emits start_talkback, sends audio_frames,
# then stop_talkback when done.
#
# Supported protocols:
# - eufy_p2p: Eufy cameras via P2P tunnel (PCM -> AAC transcoding)
# - onvif: ONVIF backchannel via go2rtc (PCM -> G.711 transcoding by go2rtc)

# Track active talkback sessions with protocol info
# Format: {camera_serial: {'sid': client_sid, 'protocol': 'eufy_p2p'|'onvif', 'go2rtc_stream': stream_name|None}}
_active_talkback_sessions = {}

# go2rtc client for ONVIF backchannel
from services.go2rtc_client import get_go2rtc_client
_go2rtc_client = None

def _get_go2rtc_client():
    """Get or initialize go2rtc client singleton."""
    global _go2rtc_client
    if _go2rtc_client is None:
        _go2rtc_client = get_go2rtc_client()
    return _go2rtc_client


_audio_frame_count = 0  # Track frames for logging

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
    import base64
    import asyncio

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

            # Run async send in new event loop (since we're in sync callback)
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
        if not eufy_bridge or not eufy_bridge.is_running():
            print(f"[Talkback AAC] Bridge not running, dropping frame")
            return

        # Encode AAC bytes to base64 for JSON transmission
        aac_base64 = base64.b64encode(audio_data).decode('ascii')

        try:
            result = eufy_bridge.send_talkback_audio(camera_serial, aac_base64)
            # Log every 10th frame
            if _audio_frame_count % 10 == 0:
                print(f"[Talkback AAC] Sent AAC frame #{_audio_frame_count} to {camera_serial}, "
                      f"size={len(audio_data)}B, result={result}")
        except Exception as e:
            print(f"[Talkback AAC] Error sending to bridge: {e}")


# Talkback transcoder manager: converts PCM to appropriate format (AAC for Eufy, G.711 for ONVIF)
_talkback_transcoder_manager = TalkbackTranscoderManager(on_aac_frame=_on_transcoded_frame_ready)


@socketio.on('connect', namespace='/talkback')
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


@socketio.on('disconnect', namespace='/talkback')
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
                if eufy_bridge and eufy_bridge.is_running():
                    eufy_bridge.stop_talkback(camera_serial)
            except Exception as e:
                logger.error(f"[Talkback] Error stopping session on disconnect: {e}")
            del _active_talkback_sessions[camera_serial]

    logger.info(f"[Talkback] Client {sid[:8]}... disconnected")


@socketio.on('start_talkback', namespace='/talkback')
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
    camera = camera_repo.get_camera(camera_id)
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
        if not eufy_bridge or not eufy_bridge.is_running():
            emit('talkback_error', {
                'camera_id': camera_id,
                'error': 'Eufy bridge not running'
            })
            return

        try:
            # Start Eufy bridge talkback session
            success = eufy_bridge.start_talkback(camera_id)
            if success:
                # Start FFmpeg transcoder with camera-specific audio settings
                transcoder_started = _talkback_transcoder_manager.start_transcoder(camera_id, camera)
                if not transcoder_started:
                    print(f"[Talkback] Transcoder failed to start for {camera_id}, stopping bridge session")
                    eufy_bridge.stop_talkback(camera_id)
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
            import asyncio
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
            import traceback
            traceback.print_exc()
            emit('talkback_error', {'camera_id': camera_id, 'error': str(e)})

    else:
        # Unsupported protocol
        emit('talkback_error', {
            'camera_id': camera_id,
            'error': f'Talkback protocol "{protocol}" not yet implemented'
        })


@socketio.on('audio_frame', namespace='/talkback')
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
        import random
        if random.random() < 0.1:
            print(f"[Talkback Audio] Fed PCM to transcoder for {camera_id} ({protocol}), len={len(audio_data)}, result={result}")
    except Exception as e:
        # Log but don't emit error for every frame
        print(f"[Talkback Audio] Transcoder feed error: {e}")


@socketio.on('stop_talkback', namespace='/talkback')
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
            import asyncio
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
        if eufy_bridge and eufy_bridge.is_running():
            try:
                eufy_bridge.stop_talkback(camera_id)
            except Exception as e:
                logger.error(f"[Talkback] Error stopping eufy talkback: {e}")

    # Release session
    if camera_id in _active_talkback_sessions:
        del _active_talkback_sessions[camera_id]

    emit('talkback_stopped', {'camera_id': camera_id})
    logger.info(f"[Talkback] Stopped {protocol} for {camera_id}")


@app.route('/api/talkback/<camera_serial>/capabilities')
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
        camera = camera_repo.get_camera(camera_serial)
        if not camera:
            return jsonify({'success': False, 'error': 'Camera not found'}), 404

        camera_type = camera.get('type', '').lower()

        # Capability matrix
        capabilities = {
            'eufy': {
                'supported': True,
                'method': 'p2p',
                'ready': eufy_bridge is not None and eufy_bridge.is_running()
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


@app.route('/api/status/websocket-mjpeg')
@login_required
def api_websocket_mjpeg_status():
    """Get status of WebSocket MJPEG service"""
    try:
        status = websocket_mjpeg_service.get_status()
        return jsonify({
            'success': True,
            **status
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


########################################################-########################################################
#                                          ⚙️⚙️⚙️⚙️ SNAPSHOT API ⚙️⚙️⚙️⚙️
########################################################-########################################################
@app.route('/api/snap/<camera_id>')
@csrf.exempt
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
    from services.reolink_mjpeg_capture_service import reolink_mjpeg_capture_service
    from services.mediaserver_mjpeg_service import mediaserver_mjpeg_service

    try:
        camera = camera_repo.get_camera(camera_id)
        if not camera:
            return "Camera not found", 404

        camera_type = camera.get('type', '').lower()
        frame_data = None

        # Try camera-specific service first
        if camera_type == 'reolink':
            frame_data = reolink_mjpeg_capture_service.get_latest_frame(camera_id)
        elif camera_type == 'unifi':
            # UniFi uses MJPEG capture service
            frame_data = unifi_mjpeg_capture_service.get_latest_frame(camera_id)
        elif camera_type == 'sv3c':
            # SV3C uses direct HTTP snapshots (/tmpfs/auto.jpg)
            frame_data = sv3c_mjpeg_capture_service.get_latest_frame(camera_id)

        # Fallback to mediaserver (works for any camera with HLS running)
        if not frame_data:
            frame_data = mediaserver_mjpeg_service.get_latest_frame(camera_id)

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


########################################################-########################################################
#                                          ⚙️⚙️⚙️⚙️ REOLINK ⚙️⚙️⚙️⚙️
########################################################-########################################################
@app.route('/api/reolink/<camera_id>/stream/mjpeg')
@csrf.exempt
@login_required
def api_reolink_stream_mjpeg_default(camera_id):
    stream = request.args.get('stream', 'sub')
    if stream == 'sub':
        return api_reolink_stream_mjpeg_sub(camera_id)
    else:
        return api_reolink_stream_mjpeg_main(camera_id)

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
#                                📷 SV3C MJPEG STREAM ROUTES (hi3510 chipset)
########################################################-########################################################
@app.route('/api/sv3c/<camera_id>/stream/mjpeg')
@csrf.exempt
@login_required
def api_sv3c_stream_mjpeg_default(camera_id):
    """
    MJPEG stream for SV3C cameras via snapshot polling.
    SV3C uses hi3510 chipset with CGI snapshot endpoints.
    Bypasses unstable RTSP by polling snapshots directly.
    """
    stream = request.args.get('stream', 'sub')
    if stream == 'sub':
        return api_sv3c_stream_mjpeg_sub(camera_id)
    else:
        return api_sv3c_stream_mjpeg_main(camera_id)


def api_sv3c_stream_mjpeg_sub(camera_id):
    """
    MJPEG sub stream for SV3C cameras via snapshot polling.
    Uses single-source, multi-client architecture.
    """
    logger.info(f"Client requesting SV3C MJPEG stream for {camera_id}")

    try:
        camera = camera_repo.get_camera(camera_id)
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
        if not sv3c_mjpeg_capture_service.add_client(camera_id, camera_with_sub, camera_repo):
            logger.error(f"Failed to add client for SV3C MJPEG stream {camera_id}")
            return "Failed to start capture", 500

        def generate():
            """Generator reads from shared frame buffer"""
            logger.info(f"[SV3C MJPEG] Client connected to {camera_id}")

            try:
                last_frame_number = -1

                while True:
                    frame_data = sv3c_mjpeg_capture_service.get_latest_frame(camera_id)

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
                sv3c_mjpeg_capture_service.remove_client(camera_id)
            except Exception as e:
                logger.error(f"[SV3C MJPEG] Stream error for {camera_id}: {e}")
                sv3c_mjpeg_capture_service.remove_client(camera_id)

        return Response(generate(),
                        mimetype='multipart/x-mixed-replace; boundary=jpgboundary')

    except Exception as e:
        logger.error(f"Failed to start SV3C MJPEG stream for {camera_id}: {e}")
        return f"Stream error: {e}", 500


def api_sv3c_stream_mjpeg_main(camera_id):
    """
    MJPEG main stream for SV3C cameras (fullscreen mode).
    Uses higher resolution but more bandwidth.
    """
    logger.info(f"Client requesting SV3C MJPEG MAIN stream for {camera_id}")

    try:
        camera = camera_repo.get_camera(camera_id)
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

        if not sv3c_mjpeg_capture_service.add_client(camera_id_main, camera_main, camera_repo):
            return "Failed to start capture", 500

        def generate():
            try:
                last_frame_number = -1
                while True:
                    frame_data = sv3c_mjpeg_capture_service.get_latest_frame(camera_id_main)
                    if frame_data and frame_data['frame_number'] != last_frame_number:
                        snapshot = frame_data['data']
                        last_frame_number = frame_data['frame_number']
                        yield (b'--jpgboundary\r\n'
                               b'Content-Type: image/jpeg\r\n' +
                               f'Content-Length: {len(snapshot)}\r\n\r\n'.encode() +
                               snapshot + b'\r\n')
                    time.sleep(0.033)
            except GeneratorExit:
                sv3c_mjpeg_capture_service.remove_client(camera_id_main)
            except Exception as e:
                logger.error(f"[SV3C MJPEG MAIN] Error {camera_id}: {e}")
                sv3c_mjpeg_capture_service.remove_client(camera_id_main)

        return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=jpgboundary')
    except Exception as e:
        return f"Stream error: {e}", 500


########################################################-########################################################
#                                ⚙️⚙️⚙️⚙️ EUFY BRIDGE AUTHENTICATION ROUTES ⚙️⚙️⚙️⚙️
########################################################-########################################################
@app.route('/eufy-auth')
@csrf.exempt
@login_required
def eufy_auth_page():
    """
    Serve Eufy authentication page for captcha and 2FA submission
    """
    return render_template('eufy_auth.html')

@app.route('/api/eufy-auth/captcha', methods=['POST'])
@csrf.exempt
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

@app.route('/api/eufy-auth/2fa', methods=['POST'])
@csrf.exempt
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

@app.route('/api/eufy-auth/status')
@csrf.exempt
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
        if not eufy_bridge:
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

@app.route('/api/eufy-auth/captcha-image')
@csrf.exempt
@login_required
def eufy_captcha_image():
    """
    Serve the current captcha image
    
    Returns: PNG image or 404 if not available
    """
    captcha_path = os.path.join(app.static_folder, 'eufy_captcha.png')
    
    if os.path.exists(captcha_path):
        return send_file(captcha_path, mimetype='image/png')
    else:
        return jsonify({
            'error': 'No captcha image available'
        }), 404

@app.route('/api/eufy-auth/refresh-captcha', methods=['POST'])
@csrf.exempt
@login_required
def refresh_eufy_captcha():
    """Request a new captcha from the bridge"""
    try:
        import time
        import asyncio
        import websockets
        import json
        
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
    
########################################################-########################################################
#                                          ⚙️⚙️⚙️⚙️AMCREST ⚙️⚙️⚙️⚙️
########################################################-########################################################

# ===== AMCREST MJPEG Service Routes =====
@app.route('/api/amcrest/<camera_id>/stream/mjpeg')
@login_required
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
@login_required
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

########################################################-########################################################
#                                          ⚙️⚙️⚙️⚙️PTZ CONTROLS⚙️⚙️⚙️⚙️
########################################################-########################################################
@app.route('/api/ptz/<camera_serial>/<direction>', methods=['POST'])
@csrf.exempt
@login_required
def api_ptz_move(camera_serial, direction):
    """Execute PTZ movement with ONVIF priority"""
    import time as _time
    _ptz_start = _time.time()
    try:
        # Validate camera
        if not ptz_validator.is_ptz_capable(camera_serial):
            return jsonify({'success': False, 'error': 'Invalid camera or no PTZ capability'}), 400

        # Get camera config
        camera = camera_repo.get_camera(camera_serial)
        if not camera:
            return jsonify({'success': False, 'error': 'Camera not found'}), 404

        camera_type = camera.get('type')
        _ptz_setup_time = _time.time() - _ptz_start
        print(f"[APP.PY] PTZ request for camera: {camera_serial}, type: {camera_type}, direction: {direction} (setup: {_ptz_setup_time*1000:.0f}ms)")

        success = False
        message = ""

        # Check if camera should use Baichuan protocol (Reolink without ONVIF or NEOLINK streams)
        # Exception: 'recalibrate' requires ONVIF GotoHomePosition, Baichuan doesn't support it
        use_baichuan = camera_type == 'reolink' and BaichuanPTZHandler.is_baichuan_capable(camera) and direction != 'recalibrate'

        if use_baichuan:
            # Use Baichuan for Reolink cameras without ONVIF or configured for Baichuan
            print(f"[APP.PY] Using Baichuan PTZ for {camera_type} camera (NEOLINK/no-ONVIF)")
            success, message = BaichuanPTZHandler.move_camera(
                camera_serial=camera_serial,
                direction=direction,
                camera_config=camera
            )
        elif camera_type in ['amcrest', 'reolink', 'sv3c']:
            # Try ONVIF for Amcrest, Reolink (with ONVIF), and SV3C cameras
            _onvif_start = _time.time()
            print(f"[APP.PY] Attempting ONVIF PTZ for {camera_type} camera")
            success, message = ONVIFPTZHandler.move_camera(
                camera_serial=camera_serial,
                direction=direction,
                camera_config=camera
            )
            _onvif_time = _time.time() - _onvif_start
            print(f"[APP.PY] ONVIF PTZ completed in {_onvif_time*1000:.0f}ms (success={success})")

            # If ONVIF fails for Reolink, try Baichuan as fallback
            if not success and camera_type == 'reolink':
                print(f"[APP.PY] ONVIF failed, falling back to Baichuan PTZ handler")
                success, message = BaichuanPTZHandler.move_camera(
                    camera_serial=camera_serial,
                    direction=direction,
                    camera_config=camera
                )

            # If ONVIF fails for Amcrest, fall back to CGI handler
            if not success and camera_type == 'amcrest':
                print(f"[APP.PY] ONVIF failed, falling back to Amcrest CGI handler")
                success = amcrest_ptz_handler.move_camera(camera_serial, direction, camera_repo)
                message = f'Camera moved {direction} via CGI' if success else 'Movement failed'

        # Eufy uses bridge (no ONVIF support)
        # move_camera() returns (success, message) and handles auto-restart internally
        elif camera_type == 'eufy':
            print(f"[EUFY PTZ] Request: camera={camera_serial}, direction={direction}")
            if not eufy_bridge:
                return jsonify({'success': False, 'error': 'Eufy bridge not initialized'}), 503

            # move_camera handles is_running check, auto-restart, and retry internally
            bridge_status = eufy_bridge.get_status()
            print(f"[EUFY PTZ] Bridge status: {bridge_status}")
            success, message = eufy_bridge.move_camera(camera_serial, direction, camera_repo)
            print(f"[EUFY PTZ] Result: success={success}, message={message}")

            if not success:
                # Return 503 with the detailed error from the bridge
                _total_time = _time.time() - _ptz_start
                print(f"[APP.PY] PTZ request TOTAL: {_total_time*1000:.0f}ms")
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
        print(f"[APP.PY] PTZ request TOTAL: {_total_time*1000:.0f}ms")
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
        camera = camera_repo.get_camera(camera_serial)
        if not camera:
            return jsonify({'success': False, 'error': 'Camera not found'}), 404

        camera_type = camera.get('type')

        # Handle Eufy cameras via bridge
        if camera_type == 'eufy':
            if not eufy_bridge or not eufy_bridge.is_running():
                return jsonify({'success': False, 'error': 'Eufy bridge not running', 'presets': []}), 503

            # Eufy has 4 fixed preset slots
            presets = eufy_bridge.get_presets(camera_serial)
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

@app.route('/api/ptz/<camera_serial>/preset/<preset_token>', methods=['POST'])
@csrf.exempt
@login_required
def api_ptz_goto_preset(camera_serial, preset_token):
    """Move camera to preset position"""
    try:
        # Validate camera
        camera = camera_repo.get_camera(camera_serial)
        if not camera:
            return jsonify({'success': False, 'error': 'Camera not found'}), 404

        camera_type = camera.get('type')

        # Handle Eufy cameras via bridge (handles auto-restart internally)
        if camera_type == 'eufy':
            if not eufy_bridge:
                return jsonify({'success': False, 'error': 'Eufy bridge not initialized'}), 503

            try:
                preset_index = int(preset_token)
            except ValueError:
                return jsonify({'success': False, 'error': 'Invalid preset token for Eufy'}), 400

            success, message = eufy_bridge.goto_preset(camera_serial, preset_index)
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

@app.route('/api/ptz/<camera_serial>/preset', methods=['POST'])
@csrf.exempt
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
        camera = camera_repo.get_camera(camera_serial)
        if not camera:
            return jsonify({'success': False, 'error': 'Camera not found'}), 404

        camera_type = camera.get('type')

        # Handle Eufy cameras via bridge (handles auto-restart internally)
        if camera_type == 'eufy':
            if not eufy_bridge:
                return jsonify({'success': False, 'error': 'Eufy bridge not initialized'}), 503

            if preset_index is None:
                return jsonify({'success': False, 'error': 'Preset index required for Eufy (0-3)'}), 400

            try:
                preset_index = int(preset_index)
            except ValueError:
                return jsonify({'success': False, 'error': 'Invalid preset index'}), 400

            success, message = eufy_bridge.save_preset(camera_serial, preset_index)
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
                response['bridge_status'] = eufy_bridge.get_status()
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


@app.route('/api/ptz/<camera_serial>/preset/<preset_token>', methods=['DELETE'])
@csrf.exempt
@login_required
def api_ptz_delete_preset(camera_serial, preset_token):
    """
    Delete a PTZ preset.

    Currently only supported on Eufy cameras.
    """
    try:
        # Validate camera
        camera = camera_repo.get_camera(camera_serial)
        if not camera:
            return jsonify({'success': False, 'error': 'Camera not found'}), 404

        camera_type = camera.get('type')

        # Handle Eufy cameras via bridge (handles auto-restart internally)
        if camera_type == 'eufy':
            if not eufy_bridge:
                return jsonify({'success': False, 'error': 'Eufy bridge not initialized'}), 503

            try:
                preset_index = int(preset_token)
            except ValueError:
                return jsonify({'success': False, 'error': 'Invalid preset token for Eufy'}), 400

            success, message = eufy_bridge.delete_preset(camera_serial, preset_index)
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


@app.route('/api/ptz/latency/<client_uuid>/<camera_serial>', methods=['GET'])
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
        response = _postgrest_session.get(
            f"{POSTGREST_URL}/ptz_client_latency",
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


@app.route('/api/ptz/latency/<client_uuid>/<camera_serial>', methods=['POST'])
@csrf.exempt
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
        get_response = _postgrest_session.get(
            f"{POSTGREST_URL}/ptz_client_latency",
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
            update_response = _postgrest_session.patch(
                f"{POSTGREST_URL}/ptz_client_latency",
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
            insert_response = _postgrest_session.post(
                f"{POSTGREST_URL}/ptz_client_latency",
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


@app.route('/api/ptz/<camera_serial>/reversal', methods=['GET'])
@csrf.exempt
@login_required
def api_ptz_get_reversal(camera_serial):
    """
    Get PTZ reversal settings for a camera.

    Returns:
        JSON with reversed_pan and reversed_tilt booleans
    """
    try:
        reversal = camera_repo.get_camera_ptz_reversal(camera_serial)
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


@app.route('/api/ptz/<camera_serial>/reversal', methods=['POST'])
@csrf.exempt
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

        success = camera_repo.update_camera_ptz_reversal(
            camera_serial,
            reversed_pan=reversed_pan,
            reversed_tilt=reversed_tilt
        )

        if success:
            reversal = camera_repo.get_camera_ptz_reversal(camera_serial)
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


@app.route('/api/camera/<camera_serial>/reboot', methods=['POST'])
@csrf.exempt
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

        camera = camera_repo.get_camera(camera_serial)
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


########################################################
#           🔌 HUBITAT POWER API ROUTES 🔌
########################################################

@app.route('/api/hubitat/devices/switch', methods=['GET'])
@csrf.exempt
@login_required
def api_hubitat_switch_devices():
    """
    Get all Hubitat devices with Switch capability.

    Used by device picker UI to show available smart plugs for camera power control.

    Returns:
        JSON array of device objects with id, label, capabilities
    """
    if not hubitat_power_service or not hubitat_power_service.is_enabled():
        return jsonify({
            'success': False,
            'error': 'Hubitat Power Service not configured'
        }), 503

    devices = hubitat_power_service.get_switch_devices()
    return jsonify(devices)


@app.route('/api/cameras/<camera_serial>/power_supply', methods=['GET', 'POST'])
@csrf.exempt
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
    camera = camera_repo.get_camera(camera_serial)
    if not camera:
        return jsonify({'success': False, 'error': 'Camera not found'}), 404

    if request.method == 'GET':
        return jsonify({
            'camera_serial': camera_serial,
            'power_supply': camera.get('power_supply'),
            'power_supply_device_id': camera.get('power_supply_device_id'),
            'power_supply_types': hubitat_power_service.get_power_supply_types() if hubitat_power_service else ['hubitat', 'poe', 'none'],
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
        if hubitat_power_service:
            success = hubitat_power_service.set_camera_power_supply(camera_serial, power_supply)
            if not success:
                return jsonify({
                    'success': False,
                    'error': f'Invalid power_supply type: {power_supply}'
                }), 400
        else:
            # No service, update directly
            camera_repo.update_camera_setting(camera_serial, 'power_supply', power_supply)

    # Update device_id if provided
    device_id = data.get('device_id')
    if device_id is not None:
        if hubitat_power_service:
            success = hubitat_power_service.set_camera_device(camera_serial, str(device_id))
            if not success:
                return jsonify({
                    'success': False,
                    'error': 'Failed to update device ID'
                }), 500
        else:
            camera_repo.update_camera_setting(camera_serial, 'power_supply_device_id', int(device_id))

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
        camera_repo.update_camera_setting(camera_serial, 'power_cycle_on_failure', updated_config)

    # Return updated settings
    camera = camera_repo.get_camera(camera_serial)
    return jsonify({
        'success': True,
        'power_supply': camera.get('power_supply'),
        'power_supply_device_id': camera.get('power_supply_device_id'),
        'power_cycle_on_failure': camera.get('power_cycle_on_failure')
    })


@app.route('/api/cameras/<camera_serial>/speaker_volume', methods=['GET', 'POST'])
@csrf.exempt
@login_required
def api_camera_speaker_volume(camera_serial):
    """
    Get or set speaker volume for a camera's two-way audio.

    GET: Returns current speaker_volume setting (0-150, default 100)
    POST: Updates speaker_volume from JSON body: {speaker_volume: 80}
    """
    camera = camera_repo.get_camera(camera_serial)
    if not camera:
        return jsonify({'success': False, 'error': 'Camera not found'}), 404

    two_way_audio = camera.get('two_way_audio', {})

    if request.method == 'GET':
        return jsonify({
            'camera_serial': camera_serial,
            'speaker_volume': two_way_audio.get('speaker_volume', 100)
        })

    # POST - update speaker volume
    data = request.get_json() or {}
    volume = data.get('speaker_volume')

    if volume is None:
        return jsonify({'success': False, 'error': 'speaker_volume is required'}), 400

    # Validate range (0-150, allowing boost up to 150%)
    try:
        volume = int(volume)
        if volume < 0 or volume > 150:
            return jsonify({'success': False, 'error': 'speaker_volume must be 0-150'}), 400
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'speaker_volume must be an integer'}), 400

    # Update the two_way_audio.speaker_volume setting
    two_way_audio['speaker_volume'] = volume
    camera_repo.update_camera_setting(camera_serial, 'two_way_audio', two_way_audio)

    app.logger.info(f"[TalkbackVolume] Updated speaker_volume for {camera_serial[:8]}... to {volume}%")

    return jsonify({
        'success': True,
        'camera_serial': camera_serial,
        'speaker_volume': volume
    })


@app.route('/api/power/<camera_serial>/cycle', methods=['POST'])
@csrf.exempt
@login_required
def api_power_cycle(camera_serial):
    """
    Trigger power cycle for a camera via its Hubitat smart plug.

    Turns off the smart plug, waits 10 seconds, then turns it back on.
    Requires camera to have power_supply='hubitat' and hubitat_device_id set.
    """
    if not hubitat_power_service:
        return jsonify({
            'success': False,
            'error': 'Hubitat Power Service not available'
        }), 503

    result = hubitat_power_service.power_cycle(camera_serial)

    if result.get('success'):
        return jsonify(result)
    else:
        return jsonify(result), 400


@app.route('/api/power/<camera_serial>/status', methods=['GET'])
@csrf.exempt
@login_required
def api_power_status(camera_serial):
    """
    Get power cycle status for a camera.

    Returns current state (idle, powering_off, powering_on, complete, failed)
    and related timestamps.
    """
    if not hubitat_power_service:
        return jsonify({
            'success': False,
            'error': 'Hubitat Power Service not available'
        }), 503

    status = hubitat_power_service.get_power_status(camera_serial)
    return jsonify(status)


@app.route('/api/hubitat/cameras', methods=['GET'])
@csrf.exempt
@login_required
def api_hubitat_cameras():
    """
    Get all cameras with power_supply='hubitat'.

    Returns list of camera configurations for cameras that can be
    power cycled via Hubitat smart plugs.
    """
    if not hubitat_power_service:
        return jsonify({
            'success': False,
            'error': 'Hubitat Power Service not available'
        }), 503

    cameras = hubitat_power_service.get_hubitat_cameras()
    return jsonify(cameras)


########################################################
#           👥 PRESENCE API ROUTES 👥
########################################################

@app.route('/api/presence', methods=['GET'])
@csrf.exempt
@login_required
def api_get_all_presence():
    """
    Get presence status for all tracked people.

    Returns:
        JSON array of presence objects with person_name, is_present,
        hubitat_device_id, last_changed_at, last_changed_by
    """
    if not presence_service:
        return jsonify({
            'success': False,
            'error': 'Presence Service not available'
        }), 503

    statuses = presence_service.get_all_presence()
    return jsonify([s.to_dict() for s in statuses])


@app.route('/api/presence/<person_name>', methods=['GET'])
@csrf.exempt
@login_required
def api_get_presence(person_name):
    """
    Get presence status for a specific person.

    Args:
        person_name: Name of the person

    Returns:
        JSON object with presence status or 404 if not found
    """
    if not presence_service:
        return jsonify({
            'success': False,
            'error': 'Presence Service not available'
        }), 503

    status = presence_service.get_presence(person_name)
    if status is None:
        return jsonify({
            'success': False,
            'error': f'Person not found: {person_name}'
        }), 404

    return jsonify(status.to_dict())


@app.route('/api/presence/<person_name>/toggle', methods=['POST'])
@csrf.exempt
@login_required
def api_toggle_presence(person_name):
    """
    Toggle presence status for a person.

    Args:
        person_name: Name of the person

    Returns:
        JSON object with new status or error
    """
    if not presence_service:
        return jsonify({
            'success': False,
            'error': 'Presence Service not available'
        }), 503

    new_status = presence_service.toggle_presence(person_name)
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


@app.route('/api/presence/<person_name>/set', methods=['POST'])
@csrf.exempt
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
    if not presence_service:
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

    success = presence_service.set_presence(person_name, bool(is_present), source='api')
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


@app.route('/api/presence/devices', methods=['GET'])
@csrf.exempt
@login_required
def api_get_presence_devices():
    """
    Get all Hubitat devices with PresenceSensor capability.

    Returns:
        JSON array of device objects with id, label, capabilities
    """
    if not presence_service:
        return jsonify({
            'success': False,
            'error': 'Presence Service not available'
        }), 503

    devices = presence_service.get_presence_devices()
    return jsonify(devices)


@app.route('/api/presence/<person_name>/device', methods=['POST'])
@csrf.exempt
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
    if not presence_service:
        return jsonify({
            'success': False,
            'error': 'Presence Service not available'
        }), 503

    data = request.get_json() or {}
    device_id = data.get('device_id')

    success = presence_service.set_hubitat_device(person_name, device_id)
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


########################################################
#           🔌 UNIFI POE POWER API ROUTES 🔌
########################################################

@app.route('/api/unifi-poe/switches', methods=['GET'])
@csrf.exempt
@login_required
def api_unifi_poe_switches():
    """
    Get all UniFi switches from the controller.

    Returns list of switches with MAC address, name, model, and port count.
    Used for switch/port configuration UI.
    """
    if not unifi_poe_service or not unifi_poe_service.is_enabled():
        return jsonify({
            'success': False,
            'error': 'UniFi POE Service not available or not configured'
        }), 503

    switches = unifi_poe_service.get_switches()
    return jsonify(switches)


@app.route('/api/unifi-poe/switches/<switch_mac>/ports', methods=['GET'])
@csrf.exempt
@login_required
def api_unifi_poe_switch_ports(switch_mac):
    """
    Get all ports on a specific switch with POE status.

    Returns list of ports with port_idx, name, poe_mode, poe_power.
    Used for selecting which port a camera is connected to.
    """
    if not unifi_poe_service or not unifi_poe_service.is_enabled():
        return jsonify({
            'success': False,
            'error': 'UniFi POE Service not available or not configured'
        }), 503

    ports = unifi_poe_service.get_switch_ports(switch_mac)
    return jsonify(ports)


@app.route('/api/cameras/<camera_serial>/poe_config', methods=['GET', 'POST'])
@csrf.exempt
@login_required
def api_camera_poe_config(camera_serial):
    """
    Get or set POE configuration for a camera.

    GET: Returns current poe_switch_mac and poe_port
    POST: Set poe_switch_mac and poe_port
          Body: {switch_mac: "aa:bb:cc:dd:ee:ff", port: 12}
    """
    camera = camera_repo.get_camera(camera_serial)
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

    if unifi_poe_service:
        success = unifi_poe_service.set_camera_poe_config(
            camera_serial, switch_mac, int(port)
        )
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Failed to save config'}), 500
    else:
        # Fallback: directly update camera settings
        success1 = camera_repo.update_camera_setting(
            camera_serial, 'poe_switch_mac', switch_mac
        )
        success2 = camera_repo.update_camera_setting(
            camera_serial, 'poe_port', int(port)
        )
        if success1 and success2:
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Failed to save config'}), 500


@app.route('/api/poe/<camera_serial>/cycle', methods=['POST'])
@csrf.exempt
@login_required
def api_poe_power_cycle(camera_serial):
    """
    Manually trigger POE power cycle for a camera.

    Requires camera to have power_supply='poe' and poe_switch_mac/poe_port set.
    """
    if not unifi_poe_service:
        return jsonify({
            'success': False,
            'error': 'UniFi POE Service not available'
        }), 503

    result = unifi_poe_service.power_cycle(camera_serial)
    if result.get('success'):
        return jsonify(result)
    else:
        return jsonify(result), 400


@app.route('/api/poe/<camera_serial>/status', methods=['GET'])
@csrf.exempt
@login_required
def api_poe_power_status(camera_serial):
    """
    Get POE power cycle status for a camera.

    Returns current state (idle, cycling, complete, failed)
    and related timestamps.
    """
    if not unifi_poe_service:
        return jsonify({
            'success': False,
            'error': 'UniFi POE Service not available'
        }), 503

    status = unifi_poe_service.get_power_status(camera_serial)
    return jsonify(status)


@app.route('/api/unifi-poe/cameras', methods=['GET'])
@csrf.exempt
@login_required
def api_poe_cameras():
    """
    Get all cameras with power_supply='poe'.

    Returns list of camera configurations for cameras that can be
    power cycled via UniFi POE switches.
    """
    if not unifi_poe_service:
        return jsonify({
            'success': False,
            'error': 'UniFi POE Service not available'
        }), 503

    cameras = unifi_poe_service.get_poe_cameras()
    return jsonify(cameras)


########################################################
#           📹 RECORDING API ROUTES 📹
########################################################

@app.route('/api/recording/settings/<camera_id>', methods=['GET', 'POST'])
@csrf.exempt
@login_required
def api_recording_settings(camera_id):
    """Get or update recording settings for a camera"""
    if not recording_service:
        return jsonify({'error': 'Recording service not available'}), 503
    
    try:
        if request.method == 'GET':
            camera = camera_repo.get_camera(camera_id)
            if not camera:
                return jsonify({'error': 'Camera not found'}), 404
            
            settings = recording_service.config.get_camera_settings(camera_id)
            
            return jsonify({
                'camera_id': camera_id,
                'camera_name': camera.get('name', camera_id),
                'settings': settings
            })
        
        else:  # POST
            data = request.get_json()
            if not data:
                return jsonify({'error': 'No data provided'}), 400
            
            recording_service.config.update_camera_settings(camera_id, data)
            recording_service.config.reload()
            
            return jsonify({
                'success': True,
                'camera_id': camera_id,
                'message': 'Settings updated successfully'
            })
    
    except Exception as e:
        logger.error(f"Recording settings API error for {camera_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/recording/start/<camera_id>', methods=['POST'])
@csrf.exempt
@login_required
def api_recording_start(camera_id):
    """Start manual recording for a camera"""
    if not recording_service:
        return jsonify({'error': 'Recording service not available'}), 503
    
    try:
        camera = camera_repo.get_camera(camera_id)
        if not camera:
            return jsonify({'error': 'Camera not found'}), 404
        
        data = request.get_json() or {}
        duration = data.get('duration', 30)  # Default 30 seconds if not specified
        
        # Use start_motion_recording for manual recordings too
        recording_id = recording_service.start_manual_recording(camera_id, duration=duration)
        
        if not recording_id:
            return jsonify({
                'success': False,
                'error': 'Failed to start recording'
            }), 500
        
        return jsonify({
            'success': True,
            'recording_id': recording_id,
            'camera_id': camera_id,
            'duration': duration,
            'message': 'Recording started'
        })
    
    except Exception as e:
        logger.error(f"Start recording API error for {camera_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/recording/stop/<recording_id>', methods=['POST'])
@csrf.exempt
@login_required
def api_recording_stop(recording_id):
    """Stop an active recording by recording ID"""
    if not recording_service:
        return jsonify({'error': 'Recording service not available'}), 503
    
    try:
        success = recording_service.stop_recording(recording_id)
        
        if not success:
            return jsonify({
                'success': False,
                'error': 'Failed to stop recording or recording not found'
            }), 404
        
        return jsonify({
            'success': True,
            'recording_id': recording_id,
            'message': 'Recording stopped'
        })
    
    except Exception as e:
        logger.error(f"Stop recording API error for {recording_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/recording/active', methods=['GET'])
@login_required
def api_recording_active():
    """Get list of all currently active recordings"""
    if not recording_service:
        return jsonify({'error': 'Recording service not available'}), 503
    
    try:
        active_recordings = recording_service.get_active_recordings()
        
        return jsonify({
            'success': True,
            'count': len(active_recordings),
            'recordings': active_recordings
        })
    
    except Exception as e:
        logger.error(f"Get active recordings API error: {e}")
        return jsonify({'error': str(e)}), 500


########################################################
#           📼 TIMELINE PLAYBACK API ROUTES 📼
########################################################

@app.route('/api/timeline/segments/<camera_id>', methods=['GET'])
@login_required
def api_timeline_segments(camera_id: str):
    """
    Get timeline segments for a camera within a time range.

    Query Parameters:
        start: ISO timestamp (required) - Range start
        end: ISO timestamp (required) - Range end
        types: Comma-separated recording types (optional) - motion,continuous,manual

    Returns:
        List of recording segments with file paths and metadata
    """
    try:
        # Parse time range from query params
        start_str = request.args.get('start')
        end_str = request.args.get('end')

        if not start_str or not end_str:
            return jsonify({'error': 'start and end parameters required'}), 400

        try:
            from datetime import datetime, timezone
            import pytz

            # Parse timestamps - if no timezone provided, assume local time (EST)
            local_tz = pytz.timezone('America/New_York')

            start_time = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
            end_time = datetime.fromisoformat(end_str.replace('Z', '+00:00'))

            # If naive (no timezone), assume local time and convert to UTC
            if start_time.tzinfo is None:
                start_time = local_tz.localize(start_time).astimezone(timezone.utc)
            if end_time.tzinfo is None:
                end_time = local_tz.localize(end_time).astimezone(timezone.utc)

            logger.debug(f"Timeline segments query: {start_str} -> {start_time.isoformat()} (UTC)")
        except ValueError as e:
            return jsonify({'error': f'Invalid timestamp format: {e}'}), 400

        # Optional recording type filter
        types_str = request.args.get('types')
        recording_types = types_str.split(',') if types_str else None

        # Get timeline service
        timeline_service = get_timeline_service()

        # Query segments
        segments = timeline_service.get_timeline_segments(
            camera_id, start_time, end_time, recording_types
        )

        return jsonify({
            'success': True,
            'camera_id': camera_id,
            'start': start_time.isoformat(),
            'end': end_time.isoformat(),
            'segment_count': len(segments),
            'segments': [
                {
                    'recording_id': seg.recording_id,
                    'start_time': seg.start_time.isoformat(),
                    'end_time': seg.end_time.isoformat(),
                    'duration_seconds': seg.duration_seconds,
                    'file_path': seg.file_path,
                    'file_size_bytes': seg.file_size_bytes,
                    'recording_type': seg.recording_type,
                    'has_audio': seg.has_audio
                }
                for seg in segments
            ]
        })

    except Exception as e:
        logger.error(f"Timeline segments API error for {camera_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/timeline/summary/<camera_id>', methods=['GET'])
@login_required
def api_timeline_summary(camera_id: str):
    """
    Get timeline summary with recording coverage by time buckets.

    Query Parameters:
        start: ISO timestamp (required) - Range start
        end: ISO timestamp (required) - Range end
        bucket_minutes: Bucket size in minutes (optional, default: 15)

    Returns:
        Summary with time buckets showing recording coverage and gaps
    """
    try:
        # Parse parameters
        start_str = request.args.get('start')
        end_str = request.args.get('end')
        bucket_minutes = int(request.args.get('bucket_minutes', 15))

        if not start_str or not end_str:
            return jsonify({'error': 'start and end parameters required'}), 400

        try:
            from datetime import datetime, timezone
            import pytz

            # Parse timestamps - if no timezone provided, assume local time (EST)
            local_tz = pytz.timezone('America/New_York')

            start_time = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
            end_time = datetime.fromisoformat(end_str.replace('Z', '+00:00'))

            # If naive (no timezone), assume local time and convert to UTC
            if start_time.tzinfo is None:
                start_time = local_tz.localize(start_time).astimezone(timezone.utc)
            if end_time.tzinfo is None:
                end_time = local_tz.localize(end_time).astimezone(timezone.utc)

            logger.debug(f"Timeline summary query: {start_str} -> {start_time.isoformat()} (UTC)")
        except ValueError as e:
            return jsonify({'error': f'Invalid timestamp format: {e}'}), 400

        timeline_service = get_timeline_service()

        summary = timeline_service.get_timeline_summary(
            camera_id, start_time, end_time, bucket_minutes
        )

        return jsonify({
            'success': True,
            **summary
        })

    except Exception as e:
        logger.error(f"Timeline summary API error for {camera_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/timeline/export', methods=['POST'])
@csrf.exempt
@login_required
def api_timeline_export_create():
    """
    Create a video export job for a time range.

    Request Body (JSON):
        camera_id: Camera serial number (required)
        start: ISO timestamp (required) - Export range start
        end: ISO timestamp (required) - Export range end
        ios_compatible: Boolean (optional) - Convert to iOS format
        types: List of recording types (optional) - ['motion', 'continuous', 'manual']
        auto_start: Boolean (optional, default: true) - Start processing immediately

    Returns:
        Export job details with job_id for tracking
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'JSON body required'}), 400

        camera_id = data.get('camera_id')
        start_str = data.get('start')
        end_str = data.get('end')

        if not camera_id or not start_str or not end_str:
            return jsonify({'error': 'camera_id, start, and end are required'}), 400

        try:
            from datetime import datetime
            start_time = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
            end_time = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
        except ValueError as e:
            return jsonify({'error': f'Invalid timestamp format: {e}'}), 400

        ios_compatible = data.get('ios_compatible', False)
        recording_types = data.get('types')
        auto_start = data.get('auto_start', True)

        timeline_service = get_timeline_service()

        # Create export job
        job = timeline_service.create_export_job(
            camera_id=camera_id,
            start_time=start_time,
            end_time=end_time,
            ios_compatible=ios_compatible,
            recording_types=recording_types
        )

        # Optionally start processing immediately
        if auto_start:
            timeline_service.start_export(job.job_id)

        return jsonify({
            'success': True,
            'job': job.to_dict()
        })

    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Timeline export create API error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/timeline/export/<job_id>', methods=['GET'])
@login_required
def api_timeline_export_status(job_id: str):
    """
    Get export job status.

    Returns:
        Export job details including progress and output path when complete
    """
    try:
        timeline_service = get_timeline_service()
        job = timeline_service.get_export_job(job_id)

        if not job:
            return jsonify({'error': f'Export job not found: {job_id}'}), 404

        return jsonify({
            'success': True,
            'job': job.to_dict()
        })

    except Exception as e:
        logger.error(f"Timeline export status API error for {job_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/timeline/export/<job_id>/start', methods=['POST'])
@csrf.exempt
@login_required
def api_timeline_export_start(job_id: str):
    """
    Start processing a pending export job.

    Use this if auto_start was false when creating the job.
    """
    try:
        timeline_service = get_timeline_service()
        timeline_service.start_export(job_id)

        job = timeline_service.get_export_job(job_id)
        return jsonify({
            'success': True,
            'job': job.to_dict()
        })

    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Timeline export start API error for {job_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/timeline/export/<job_id>/cancel', methods=['POST'])
@csrf.exempt
@login_required
def api_timeline_export_cancel(job_id: str):
    """Cancel a pending or processing export job."""
    try:
        timeline_service = get_timeline_service()
        cancelled = timeline_service.cancel_export(job_id)

        if not cancelled:
            job = timeline_service.get_export_job(job_id)
            if not job:
                return jsonify({'error': f'Export job not found: {job_id}'}), 404
            return jsonify({'error': f'Cannot cancel job in status: {job.status.value}'}), 400

        return jsonify({
            'success': True,
            'message': f'Export job {job_id} cancelled'
        })

    except Exception as e:
        logger.error(f"Timeline export cancel API error for {job_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/timeline/export/<job_id>/download', methods=['GET'])
@login_required
def api_timeline_export_download(job_id: str):
    """
    Download completed export file.

    Returns:
        Video file as attachment for download
    """
    try:
        timeline_service = get_timeline_service()
        job = timeline_service.get_export_job(job_id)

        if not job:
            return jsonify({'error': f'Export job not found: {job_id}'}), 404

        if job.status.value != 'completed':
            return jsonify({'error': f'Export not ready (status: {job.status.value})'}), 400

        if not job.output_path or not os.path.exists(job.output_path):
            return jsonify({'error': 'Export file not found'}), 404

        # Return file for download
        return send_file(
            job.output_path,
            mimetype='video/mp4',
            as_attachment=True,
            download_name=os.path.basename(job.output_path)
        )

    except Exception as e:
        logger.error(f"Timeline export download API error for {job_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/timeline/export/<job_id>/stream', methods=['GET'])
@login_required
def api_timeline_export_stream(job_id: str):
    """
    Stream export file for inline playback (iOS save workaround).

    Unlike /download, this streams for playback (not as attachment),
    allowing iOS users to long-press the video to save it.

    Supports HTTP Range requests for seeking.

    Returns:
        Video stream with appropriate headers for inline playback
    """
    try:
        timeline_service = get_timeline_service()
        job = timeline_service.get_export_job(job_id)

        if not job:
            return jsonify({'error': f'Export job not found: {job_id}'}), 404

        if job.status.value != 'completed':
            return jsonify({'error': f'Export not ready (status: {job.status.value})'}), 400

        file_path = job.output_path
        if not file_path or not os.path.exists(file_path):
            return jsonify({'error': 'Export file not found'}), 404

        file_size = os.path.getsize(file_path)

        # Handle Range requests for video seeking
        range_header = request.headers.get('Range')
        if range_header:
            # Parse range header (e.g., "bytes=0-1023")
            byte_start = 0
            byte_end = file_size - 1

            match = re.match(r'bytes=(\d*)-(\d*)', range_header)
            if match:
                if match.group(1):
                    byte_start = int(match.group(1))
                if match.group(2):
                    byte_end = int(match.group(2))

            # Clamp to file size
            byte_end = min(byte_end, file_size - 1)
            content_length = byte_end - byte_start + 1

            def generate_range():
                with open(file_path, 'rb') as f:
                    f.seek(byte_start)
                    remaining = content_length
                    while remaining > 0:
                        chunk_size = min(8192, remaining)
                        data = f.read(chunk_size)
                        if not data:
                            break
                        remaining -= len(data)
                        yield data

            response = Response(
                generate_range(),
                status=206,
                mimetype='video/mp4',
                direct_passthrough=True
            )
            response.headers['Content-Range'] = f'bytes {byte_start}-{byte_end}/{file_size}'
            response.headers['Content-Length'] = content_length
            response.headers['Accept-Ranges'] = 'bytes'
            return response

        # Full file request (no Range header)
        return send_file(
            file_path,
            mimetype='video/mp4',
            as_attachment=False  # Inline playback, not download
        )

    except Exception as e:
        logger.error(f"Timeline export stream API error for {job_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/timeline/preview/<int:recording_id>', methods=['GET'])
@login_required
def api_timeline_preview(recording_id: int):
    """
    Stream a recording file for in-browser preview playback.

    Supports HTTP Range requests for seeking in video players.

    Args:
        recording_id: Database recording ID

    Returns:
        Video file stream with appropriate headers for playback
    """
    try:
        timeline_service = get_timeline_service()

        # Get recording details from database
        segment = timeline_service.get_segment_by_id(recording_id)

        if not segment:
            return jsonify({'error': 'Recording not found'}), 404

        file_path = segment.file_path

        if not os.path.exists(file_path):
            logger.error(f"Preview file not found: {file_path}")
            return jsonify({'error': 'Recording file not found on disk'}), 404

        # Get file size for range request support
        file_size = os.path.getsize(file_path)

        # Handle Range requests for video seeking
        range_header = request.headers.get('Range')

        if range_header:
            # Parse range header (e.g., "bytes=0-1024")
            byte_start = 0
            byte_end = file_size - 1

            match = range_header.replace('bytes=', '').split('-')
            if match[0]:
                byte_start = int(match[0])
            if match[1]:
                byte_end = int(match[1])

            # Ensure end doesn't exceed file size
            byte_end = min(byte_end, file_size - 1)
            content_length = byte_end - byte_start + 1

            def generate_range():
                with open(file_path, 'rb') as f:
                    f.seek(byte_start)
                    remaining = content_length
                    chunk_size = 8192
                    while remaining > 0:
                        chunk = f.read(min(chunk_size, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk

            response = Response(
                generate_range(),
                status=206,
                mimetype='video/mp4',
                direct_passthrough=True
            )
            response.headers['Content-Range'] = f'bytes {byte_start}-{byte_end}/{file_size}'
            response.headers['Accept-Ranges'] = 'bytes'
            response.headers['Content-Length'] = content_length
            return response

        # Full file response (no range requested)
        return send_file(
            file_path,
            mimetype='video/mp4',
            as_attachment=False
        )

    except Exception as e:
        logger.error(f"Timeline preview API error for recording {recording_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/timeline/exports', methods=['GET'])
@login_required
def api_timeline_export_list():
    """
    List all export jobs, optionally filtered by camera.

    Query Parameters:
        camera_id: Optional camera filter
    """
    try:
        camera_id = request.args.get('camera_id')
        timeline_service = get_timeline_service()

        jobs = timeline_service.get_export_jobs(camera_id)

        return jsonify({
            'success': True,
            'count': len(jobs),
            'jobs': [job.to_dict() for job in jobs]
        })

    except Exception as e:
        logger.error(f"Timeline export list API error: {e}")
        return jsonify({'error': str(e)}), 500


########################################################
#           🎬 PREVIEW MERGE API ROUTES 🎬
########################################################

@app.route('/api/timeline/preview-merge', methods=['POST'])
@csrf.exempt
@login_required
def api_timeline_preview_merge_create():
    """
    Create a merged preview from selected segment IDs.

    Merges multiple recording segments into a single temporary MP4 file
    for preview playback. The merge runs asynchronously.

    Request Body:
        camera_id: Camera serial number
        segment_ids: List of recording IDs to merge
        ios_compatible: (optional) If true, re-encode to H.264 Baseline for iOS/mobile

    Returns:
        job_id for tracking merge progress
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No JSON data provided'}), 400

        camera_id = data.get('camera_id')
        segment_ids = data.get('segment_ids', [])
        ios_compatible = data.get('ios_compatible', False)

        if not camera_id:
            return jsonify({'error': 'camera_id is required'}), 400
        if not segment_ids or not isinstance(segment_ids, list):
            return jsonify({'error': 'segment_ids must be a non-empty list'}), 400

        timeline_service = get_timeline_service()
        job = timeline_service.create_preview_merge(camera_id, segment_ids, ios_compatible)

        return jsonify({
            'success': True,
            'job_id': job.job_id,
            'job': job.to_dict()
        })

    except ValueError as e:
        logger.warning(f"Preview merge validation error: {e}")
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Preview merge create error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/timeline/preview-merge/<job_id>', methods=['GET'])
@login_required
def api_timeline_preview_merge_status(job_id: str):
    """
    Get preview merge job status and progress.

    Args:
        job_id: Preview job ID

    Returns:
        Job status including progress_percent, status, error_message
    """
    try:
        timeline_service = get_timeline_service()
        job = timeline_service.get_preview_job(job_id)

        if not job:
            return jsonify({'error': 'Preview job not found'}), 404

        return jsonify({
            'success': True,
            'job': job.to_dict()
        })

    except Exception as e:
        logger.error(f"Preview merge status error for {job_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/timeline/preview-merge/<job_id>/cancel', methods=['POST'])
@csrf.exempt
@login_required
def api_timeline_preview_merge_cancel(job_id: str):
    """
    Cancel a preview merge job.

    Terminates the FFmpeg process and cleans up temp files.

    Args:
        job_id: Preview job ID
    """
    try:
        timeline_service = get_timeline_service()
        cancelled = timeline_service.cancel_preview_merge(job_id)

        if not cancelled:
            return jsonify({
                'success': False,
                'error': 'Job not found or already completed'
            }), 404

        return jsonify({'success': True})

    except Exception as e:
        logger.error(f"Preview merge cancel error for {job_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/timeline/preview-merge/<job_id>/stream', methods=['GET'])
@login_required
def api_timeline_preview_merge_stream(job_id: str):
    """
    Stream the merged preview file for playback.

    Supports HTTP Range requests for video seeking.

    Args:
        job_id: Preview job ID

    Returns:
        Video file stream with appropriate headers
    """
    try:
        timeline_service = get_timeline_service()
        job = timeline_service.get_preview_job(job_id)

        if not job:
            return jsonify({'error': 'Preview job not found'}), 404

        if job.status != ExportStatus.COMPLETED:
            return jsonify({'error': f'Preview not ready (status: {job.status.value})'}), 400

        if not job.temp_file_path or not os.path.exists(job.temp_file_path):
            return jsonify({'error': 'Preview file not found'}), 404

        file_path = job.temp_file_path
        file_size = os.path.getsize(file_path)

        # Handle Range requests for video seeking
        range_header = request.headers.get('Range')

        if range_header:
            byte_start = 0
            byte_end = file_size - 1

            match = range_header.replace('bytes=', '').split('-')
            if match[0]:
                byte_start = int(match[0])
            if match[1]:
                byte_end = int(match[1])

            byte_end = min(byte_end, file_size - 1)
            content_length = byte_end - byte_start + 1

            def generate_range():
                with open(file_path, 'rb') as f:
                    f.seek(byte_start)
                    remaining = content_length
                    chunk_size = 8192
                    while remaining > 0:
                        chunk = f.read(min(chunk_size, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk

            response = Response(
                generate_range(),
                status=206,
                mimetype='video/mp4',
                direct_passthrough=True
            )
            response.headers['Content-Range'] = f'bytes {byte_start}-{byte_end}/{file_size}'
            response.headers['Accept-Ranges'] = 'bytes'
            response.headers['Content-Length'] = content_length
            return response

        # Full file response
        return send_file(
            file_path,
            mimetype='video/mp4',
            as_attachment=False
        )

    except Exception as e:
        logger.error(f"Preview merge stream error for {job_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/timeline/preview-merge/<job_id>/cleanup', methods=['DELETE'])
@csrf.exempt
@login_required
def api_timeline_preview_merge_cleanup(job_id: str):
    """
    Delete temp preview files and cleanup resources.

    Should be called when modal closes or after download.

    Args:
        job_id: Preview job ID
    """
    try:
        timeline_service = get_timeline_service()
        cleaned = timeline_service.cleanup_preview(job_id)

        if not cleaned:
            return jsonify({
                'success': False,
                'error': 'Job not found'
            }), 404

        return jsonify({'success': True})

    except Exception as e:
        logger.error(f"Preview merge cleanup error for {job_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/timeline/preview-merge/<job_id>/promote', methods=['POST'])
@csrf.exempt
@login_required
def api_timeline_preview_merge_promote(job_id: str):
    """
    Promote a preview merge to a permanent export.

    Moves the temp file to exports directory. Optionally converts for iOS.

    Args:
        job_id: Preview job ID

    Request Body:
        ios_compatible: bool - Whether to convert for iOS (optional, default false)

    Returns:
        download_url for the exported file
    """
    try:
        data = request.get_json() or {}
        ios_compatible = data.get('ios_compatible', False)

        timeline_service = get_timeline_service()
        export_path = timeline_service.promote_preview_to_export(job_id, ios_compatible)

        if not export_path:
            return jsonify({'error': 'Promotion failed'}), 500

        # Build download URL
        filename = os.path.basename(export_path)
        download_url = f'/api/timeline/export/download/{filename}'

        return jsonify({
            'success': True,
            'export_path': export_path,
            'download_url': download_url,
            'filename': filename
        })

    except ValueError as e:
        logger.warning(f"Preview promote validation error for {job_id}: {e}")
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Preview promote error for {job_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/timeline/export/download/<filename>', methods=['GET'])
@login_required
def api_timeline_export_download_by_filename(filename: str):
    """
    Download an exported file by filename.

    Args:
        filename: Export filename (e.g., 'T8416P0023352DA9_20260120_170000.mp4')

    Returns:
        File download
    """
    try:
        timeline_service = get_timeline_service()
        file_path = os.path.join(timeline_service.export_dir, filename)

        # Validate filename (prevent directory traversal)
        if '..' in filename or '/' in filename:
            return jsonify({'error': 'Invalid filename'}), 400

        if not os.path.exists(file_path):
            return jsonify({'error': 'Export file not found'}), 404

        return send_file(
            file_path,
            mimetype='video/mp4',
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        logger.error(f"Export download error for {filename}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/timeline/export/stream/<filename>', methods=['GET'])
@login_required
def api_timeline_export_stream_by_filename(filename: str):
    """
    Stream an exported file by filename for inline playback.
    Used for iOS save workaround where user needs to long-press video.

    Args:
        filename: Export filename (e.g., 'T8416P0023352DA9_20260120_170000.mp4')

    Returns:
        Video stream with Range support for seeking
    """
    try:
        timeline_service = get_timeline_service()
        file_path = os.path.join(timeline_service.export_dir, filename)

        # Validate filename (prevent directory traversal)
        if '..' in filename or '/' in filename:
            return jsonify({'error': 'Invalid filename'}), 400

        if not os.path.exists(file_path):
            return jsonify({'error': 'Export file not found'}), 404

        # Get file size for Range header support
        file_size = os.path.getsize(file_path)

        # Handle Range requests for video seeking
        range_header = request.headers.get('Range')
        if range_header:
            # Parse range header: "bytes=start-end"
            match = re.match(r'bytes=(\d+)-(\d*)', range_header)
            if match:
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else file_size - 1
                length = end - start + 1

                def generate_range():
                    with open(file_path, 'rb') as f:
                        f.seek(start)
                        remaining = length
                        while remaining > 0:
                            chunk_size = min(8192, remaining)
                            data = f.read(chunk_size)
                            if not data:
                                break
                            remaining -= len(data)
                            yield data

                response = Response(
                    generate_range(),
                    status=206,
                    mimetype='video/mp4',
                    direct_passthrough=True
                )
                response.headers['Content-Range'] = f'bytes {start}-{end}/{file_size}'
                response.headers['Accept-Ranges'] = 'bytes'
                response.headers['Content-Length'] = length
                return response

        # No range - send full file for inline viewing (not as attachment)
        return send_file(
            file_path,
            mimetype='video/mp4',
            as_attachment=False  # Inline viewing for iOS long-press save
        )

    except Exception as e:
        logger.error(f"Export stream error for {filename}: {e}")
        return jsonify({'error': str(e)}), 500


########################################################
#           📂 FILE BROWSER API ROUTES 📂
########################################################
# Used for browsing alternate recording sources (FTP uploads, etc.)

# Base path for alternate recordings - mounted in docker-compose.yml
ALTERNATE_RECORDING_BASE = '/recordings/ALTERNATE'


@app.route('/api/files/browse', methods=['GET'])
@login_required
def api_browse_files():
    """
    Browse files in a directory within the allowed paths.

    Query params:
        path: Relative path from ALTERNATE_RECORDING_BASE (default: /)

    Returns:
        JSON with directories and files list
    """
    try:
        # Get relative path from query string
        relative_path = request.args.get('path', '/')

        # Security: Normalize and validate path to prevent directory traversal
        # Remove leading slash if present
        if relative_path.startswith('/'):
            relative_path = relative_path[1:]

        # Construct full path
        full_path = os.path.normpath(os.path.join(ALTERNATE_RECORDING_BASE, relative_path))

        # Security check: Ensure path is within allowed base
        if not full_path.startswith(ALTERNATE_RECORDING_BASE):
            logger.warning(f"[FILE_BROWSER] Directory traversal attempt blocked: {relative_path}")
            return jsonify({'error': 'Invalid path'}), 403

        # Check if path exists
        if not os.path.exists(full_path):
            return jsonify({
                'success': True,
                'path': '/' + relative_path if relative_path else '/',
                'directories': [],
                'files': [],
                'message': 'Directory does not exist'
            })

        # Check if it's a directory
        if not os.path.isdir(full_path):
            return jsonify({'error': 'Path is not a directory'}), 400

        directories = []
        files = []

        try:
            entries = os.listdir(full_path)
        except PermissionError:
            return jsonify({'error': 'Permission denied'}), 403

        for entry in sorted(entries):
            entry_path = os.path.join(full_path, entry)

            try:
                stat_info = os.stat(entry_path)

                if os.path.isdir(entry_path):
                    directories.append({
                        'name': entry,
                        'type': 'directory',
                        'modified': stat_info.st_mtime
                    })
                else:
                    # Only include video files
                    ext = os.path.splitext(entry)[1].lower()
                    if ext in ['.mp4', '.avi', '.mkv', '.mov', '.m4v', '.webm']:
                        files.append({
                            'name': entry,
                            'type': 'video',
                            'size': stat_info.st_size,
                            'modified': stat_info.st_mtime
                        })
            except (OSError, PermissionError):
                # Skip files we can't access
                continue

        # Return current path relative to base
        display_path = '/' + relative_path if relative_path else '/'

        return jsonify({
            'success': True,
            'path': display_path,
            'directories': directories,
            'files': files,
            'total_items': len(directories) + len(files)
        })

    except Exception as e:
        logger.error(f"[FILE_BROWSER] Error browsing files: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/files/stream/<path:filepath>', methods=['GET'])
@login_required
def api_stream_file(filepath):
    """
    Stream a video file from the alternate recording storage.
    Supports HTTP range requests for seeking.

    Args:
        filepath: Relative path to the file from ALTERNATE_RECORDING_BASE

    Returns:
        Video stream with proper headers for range requests
    """
    try:
        # Security: Normalize and validate path
        full_path = os.path.normpath(os.path.join(ALTERNATE_RECORDING_BASE, filepath))

        # Security check: Ensure path is within allowed base
        if not full_path.startswith(ALTERNATE_RECORDING_BASE):
            logger.warning(f"[FILE_BROWSER] Stream traversal attempt blocked: {filepath}")
            return jsonify({'error': 'Invalid path'}), 403

        # Check if file exists
        if not os.path.exists(full_path):
            return jsonify({'error': 'File not found'}), 404

        if not os.path.isfile(full_path):
            return jsonify({'error': 'Not a file'}), 400

        # Get file info
        file_size = os.path.getsize(full_path)

        # Determine mime type
        ext = os.path.splitext(full_path)[1].lower()
        mime_types = {
            '.mp4': 'video/mp4',
            '.avi': 'video/x-msvideo',
            '.mkv': 'video/x-matroska',
            '.mov': 'video/quicktime',
            '.m4v': 'video/x-m4v',
            '.webm': 'video/webm'
        }
        mime_type = mime_types.get(ext, 'video/mp4')

        # Handle range request for seeking
        range_header = request.headers.get('Range')
        if range_header:
            match = re.match(r'bytes=(\d+)-(\d*)', range_header)
            if match:
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else file_size - 1

                # Clamp values
                start = max(0, min(start, file_size - 1))
                end = max(start, min(end, file_size - 1))
                length = end - start + 1

                def generate_range():
                    with open(full_path, 'rb') as f:
                        f.seek(start)
                        remaining = length
                        while remaining > 0:
                            chunk_size = min(8192, remaining)
                            data = f.read(chunk_size)
                            if not data:
                                break
                            remaining -= len(data)
                            yield data

                response = Response(
                    generate_range(),
                    status=206,
                    mimetype=mime_type,
                    direct_passthrough=True
                )
                response.headers['Content-Range'] = f'bytes {start}-{end}/{file_size}'
                response.headers['Accept-Ranges'] = 'bytes'
                response.headers['Content-Length'] = length
                return response

        # No range - send full file for inline viewing
        return send_file(
            full_path,
            mimetype=mime_type,
            as_attachment=False
        )

    except Exception as e:
        logger.error(f"[FILE_BROWSER] Error streaming file {filepath}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/files/download/<path:filepath>', methods=['GET'])
@login_required
def api_download_file(filepath):
    """
    Download a video file from the alternate recording storage.
    Returns file as attachment (triggers browser download).

    Args:
        filepath: Relative path to the file from ALTERNATE_RECORDING_BASE

    Returns:
        File download response
    """
    try:
        # Security: Normalize and validate path
        full_path = os.path.normpath(os.path.join(ALTERNATE_RECORDING_BASE, filepath))

        # Security check: Ensure path is within allowed base
        if not full_path.startswith(ALTERNATE_RECORDING_BASE):
            logger.warning(f"[FILE_BROWSER] Download traversal attempt blocked: {filepath}")
            return jsonify({'error': 'Invalid path'}), 403

        # Check if file exists
        if not os.path.exists(full_path):
            return jsonify({'error': 'File not found'}), 404

        if not os.path.isfile(full_path):
            return jsonify({'error': 'Not a file'}), 400

        # Get filename for download
        filename = os.path.basename(full_path)

        # Determine mime type
        ext = os.path.splitext(full_path)[1].lower()
        mime_types = {
            '.mp4': 'video/mp4',
            '.avi': 'video/x-msvideo',
            '.mkv': 'video/x-matroska',
            '.mov': 'video/quicktime',
            '.m4v': 'video/x-m4v',
            '.webm': 'video/webm'
        }
        mime_type = mime_types.get(ext, 'application/octet-stream')

        logger.info(f"[FILE_BROWSER] Download: {filename}")

        return send_file(
            full_path,
            mimetype=mime_type,
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        logger.error(f"[FILE_BROWSER] Error downloading file {filepath}: {e}")
        return jsonify({'error': str(e)}), 500


########################################################
#           📥 RECORDINGS DOWNLOAD API ROUTE 📥
########################################################

RECORDINGS_BASE = '/recordings'

@app.route('/api/recordings/download/<path:filepath>', methods=['GET'])
@login_required
def api_download_recording(filepath):
    """
    Download a recording file from the main recordings storage.
    Used by timeline playback modal to download selected segments.

    Args:
        filepath: Relative path to the file from /recordings/
                  e.g., motion/SERIAL/filename.mp4

    Returns:
        File download response
    """
    try:
        # Security: Normalize and validate path
        full_path = os.path.normpath(os.path.join(RECORDINGS_BASE, filepath))

        # Security check: Ensure path is within allowed base
        if not full_path.startswith(RECORDINGS_BASE):
            logger.warning(f"[RECORDINGS] Download traversal attempt blocked: {filepath}")
            return jsonify({'error': 'Invalid path'}), 403

        # Check if file exists
        if not os.path.exists(full_path):
            return jsonify({'error': 'File not found'}), 404

        if not os.path.isfile(full_path):
            return jsonify({'error': 'Not a file'}), 400

        # Get filename for download
        filename = os.path.basename(full_path)

        # Determine mime type
        ext = os.path.splitext(full_path)[1].lower()
        mime_types = {
            '.mp4': 'video/mp4',
            '.avi': 'video/x-msvideo',
            '.mkv': 'video/x-matroska',
            '.mov': 'video/quicktime',
            '.m4v': 'video/x-m4v',
            '.webm': 'video/webm'
        }
        mime_type = mime_types.get(ext, 'application/octet-stream')

        logger.info(f"[RECORDINGS] Download: {filename}")

        return send_file(
            full_path,
            mimetype=mime_type,
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        logger.error(f"[RECORDINGS] Error downloading file {filepath}: {e}")
        return jsonify({'error': str(e)}), 500


########################################################
#           📦 STORAGE MIGRATION API ROUTES 📦
########################################################

# Global storage migration service instance
_storage_migration_service = None

def get_storage_migration_service():
    """
    Get or create the StorageMigrationService singleton.
    Lazy initialization to avoid import issues at startup.
    Starts auto-migration monitor on first initialization.
    """
    global _storage_migration_service
    if _storage_migration_service is None:
        from services.recording.storage_migration import StorageMigrationService
        _storage_migration_service = StorageMigrationService()
        # Start auto-migration background monitor (checks every 5 minutes)
        _storage_migration_service.start_auto_migration_monitor(check_interval_seconds=300)
        logger.info("[STORAGE] Auto-migration monitor started (5 minute interval)")
    return _storage_migration_service


@app.route('/api/storage/stats', methods=['GET'])
@login_required
def api_storage_stats():
    """
    Get storage statistics for UI display.

    Returns:
        Disk usage for recent and archive tiers, config settings, warnings
    """
    try:
        migration_service = get_storage_migration_service()
        stats = migration_service.get_storage_stats()

        return jsonify({
            'success': True,
            **stats
        })

    except Exception as e:
        logger.error(f"Storage stats API error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/storage/migrate', methods=['POST'])
@csrf.exempt
@login_required
def api_storage_migrate():
    """
    Trigger storage migration from recent to archive tier (admin only).

    Request Body (JSON, optional):
        recording_type: Type to migrate (default: "motion")
        force: Bypass threshold checks (default: false)

    Returns:
        Migration result with counts and details
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403
    try:
        # Prevent concurrent operations
        if _migration_status.get('in_progress'):
            return jsonify({
                'success': False,
                'error': f"Operation '{_migration_status.get('operation')}' already in progress",
                'in_progress': True,
                'current_operation': _migration_status.get('operation'),
                'files_processed': _migration_status.get('files_processed', 0),
                'files_total': _migration_status.get('files_total', 0)
            }), 409  # HTTP 409 Conflict

        global _migration_cancel_event

        data = request.get_json() or {}
        recording_type = data.get('recording_type', 'motion')
        force = data.get('force', False)

        # Reset cancel event and status before starting
        _migration_cancel_event.clear()
        update_migration_status(in_progress=True, operation='migrate', reset=True)
        update_migration_status(in_progress=True, operation='migrate')

        # Progress callback for real-time updates (also checks for cancellation)
        def progress_callback(files_processed=None, files_total=None, current_file=None, bytes_processed=None, **kwargs):
            check_migration_cancelled()  # Raises MigrationCancelled if cancel requested
            update_migration_status(
                files_processed=files_processed,
                files_total=files_total,
                current_file=current_file,
                bytes_processed=bytes_processed
            )

        migration_service = get_storage_migration_service()
        result = migration_service.migrate_recent_to_archive(
            recording_type, force,
            progress_callback=progress_callback,
            cancel_event=_migration_cancel_event
        )

        # Update migration status - complete
        update_migration_status(
            in_progress=False,
            files_processed=result.success_count,
            bytes_processed=result.bytes_processed
        )

        return jsonify({
            'success': True,
            'operation': 'migrate',
            'recording_type': recording_type,
            'trigger_reason': result.trigger_reason,
            'migrated': result.success_count,
            'failed': result.failed_count,
            'skipped': result.skipped_count,
            'bytes_processed': result.bytes_processed,
            'errors': result.errors[:10] if result.errors else []
        })

    except MigrationCancelled:
        logger.info("Migration cancelled by user")
        update_migration_status(in_progress=False)
        return jsonify({
            'success': True,
            'cancelled': True,
            'operation': 'migrate',
            'message': 'Migration cancelled by user',
            'files_processed': _migration_status.get('files_processed', 0)
        })

    except Exception as e:
        logger.error(f"Storage migrate API error: {e}")
        update_migration_status(in_progress=False, error=str(e))
        return jsonify({'error': str(e)}), 500


@app.route('/api/storage/cleanup', methods=['POST'])
@csrf.exempt
@login_required
def api_storage_cleanup():
    """
    Trigger archive cleanup (deletion of old files) (admin only).

    Request Body (JSON, optional):
        recording_type: Type to clean (default: "motion")
        force: Bypass threshold checks (default: false)

    Returns:
        Cleanup result with counts and details
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403
    try:
        # Prevent concurrent operations
        if _migration_status.get('in_progress'):
            return jsonify({
                'success': False,
                'error': f"Operation '{_migration_status.get('operation')}' already in progress",
                'in_progress': True,
                'current_operation': _migration_status.get('operation'),
                'files_processed': _migration_status.get('files_processed', 0),
                'files_total': _migration_status.get('files_total', 0)
            }), 409

        data = request.get_json() or {}
        recording_type = data.get('recording_type', 'motion')
        force = data.get('force', False)

        # Update migration status - in progress
        update_migration_status(in_progress=True, operation='cleanup', reset=True)
        update_migration_status(in_progress=True, operation='cleanup')

        migration_service = get_storage_migration_service()
        result = migration_service.cleanup_archive(recording_type, force)

        # Update migration status - complete
        update_migration_status(
            in_progress=False,
            files_processed=result.success_count,
            bytes_processed=result.bytes_processed
        )

        return jsonify({
            'success': True,
            'operation': 'cleanup',
            'recording_type': recording_type,
            'trigger_reason': result.trigger_reason,
            'deleted': result.success_count,
            'failed': result.failed_count,
            'bytes_freed': result.bytes_processed,
            'errors': result.errors[:10] if result.errors else []
        })

    except Exception as e:
        logger.error(f"Storage cleanup API error: {e}")
        update_migration_status(in_progress=False, error=str(e))
        return jsonify({'error': str(e)}), 500


@app.route('/api/storage/reconcile', methods=['POST'])
@csrf.exempt
@login_required
def api_storage_reconcile():
    """
    Reconcile database with filesystem (admin only).
    Removes orphaned database entries where files no longer exist.

    Returns:
        Reconciliation result with removed entry count
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403
    try:
        # Prevent concurrent operations
        if _migration_status.get('in_progress'):
            return jsonify({
                'success': False,
                'error': f"Operation '{_migration_status.get('operation')}' already in progress",
                'in_progress': True,
                'current_operation': _migration_status.get('operation'),
                'files_processed': _migration_status.get('files_processed', 0),
                'files_total': _migration_status.get('files_total', 0)
            }), 409

        # Update migration status - in progress
        update_migration_status(in_progress=True, operation='reconcile', reset=True)
        update_migration_status(in_progress=True, operation='reconcile')

        # Progress callback for real-time updates (also checks for cancellation)
        def progress_callback(files_processed=None, files_total=None, current_file=None, **kwargs):
            check_migration_cancelled()  # Raises MigrationCancelled if cancel requested
            update_migration_status(
                files_processed=files_processed,
                files_total=files_total,
                current_file=current_file
            )

        migration_service = get_storage_migration_service()
        result = migration_service.reconcile_db_with_filesystem(progress_callback=progress_callback)

        # Check if the service-level lock prevented execution
        if result.errors and "already in progress" in str(result.errors):
            update_migration_status(in_progress=False)
            return jsonify({
                'success': False,
                'error': 'Reconciliation already running (service lock)',
                'in_progress': True
            }), 409

        # Update migration status - complete
        update_migration_status(
            in_progress=False,
            files_processed=result.success_count
        )

        return jsonify({
            'success': True,
            'operation': 'reconcile',
            'orphaned_removed': result.success_count,
            'failed': result.failed_count,
            'errors': result.errors[:10] if result.errors else []
        })

    except MigrationCancelled:
        logger.info("Reconcile cancelled by user")
        update_migration_status(in_progress=False)
        return jsonify({
            'success': True,
            'cancelled': True,
            'operation': 'reconcile',
            'message': 'Reconcile cancelled by user',
            'files_processed': _migration_status.get('files_processed', 0)
        })

    except Exception as e:
        logger.error(f"Storage reconcile API error: {e}")
        update_migration_status(in_progress=False, error=str(e))
        return jsonify({'error': str(e)}), 500


@app.route('/api/storage/migrate/full', methods=['POST'])
@csrf.exempt
@login_required
def api_storage_full_migration():
    """
    Run complete migration cycle for all recording types.

    Steps:
    1. Migrate recent → archive for all types
    2. Cleanup archive for all types
    3. Reconcile database

    Returns:
        Summary of all operations
    """
    try:
        migration_service = get_storage_migration_service()
        results = migration_service.run_full_migration()

        # Summarize results
        summary = {
            'success': True,
            'operation': 'full_migration',
            'migrate': {},
            'cleanup': {},
            'reconcile': {}
        }

        for key, result in results.items():
            if key.startswith('migrate_'):
                rec_type = key.replace('migrate_', '')
                summary['migrate'][rec_type] = {
                    'migrated': result.success_count,
                    'failed': result.failed_count
                }
            elif key.startswith('cleanup_'):
                rec_type = key.replace('cleanup_', '')
                summary['cleanup'][rec_type] = {
                    'deleted': result.success_count,
                    'failed': result.failed_count
                }
            elif key == 'reconcile':
                summary['reconcile'] = {
                    'orphaned_removed': result.success_count,
                    'failed': result.failed_count
                }

        return jsonify(summary)

    except Exception as e:
        logger.error(f"Storage full migration API error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/storage/operations', methods=['GET'])
@login_required
def api_storage_operations():
    """
    Query file operations log.

    Query Parameters:
        operation: Filter by operation type (migrate, delete, reconcile, error)
        camera_id: Filter by camera
        limit: Max records (default: 50)
        offset: Pagination offset (default: 0)

    Returns:
        List of file operation log entries
    """
    try:
        # Build PostgREST query
        operation = request.args.get('operation')
        camera_id = request.args.get('camera_id')
        limit = int(request.args.get('limit', 50))
        offset = int(request.args.get('offset', 0))

        # Query PostgREST directly
        url = f"http://localhost:3000/file_operations_log?order=created_at.desc&limit={limit}&offset={offset}"

        if operation:
            url += f"&operation=eq.{operation}"
        if camera_id:
            url += f"&camera_id=eq.{camera_id}"

        import requests as req
        response = req.get(url, timeout=30)
        response.raise_for_status()
        operations = response.json()

        return jsonify({
            'success': True,
            'count': len(operations),
            'operations': operations
        })

    except Exception as e:
        logger.error(f"Storage operations API error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/storage/settings', methods=['GET', 'POST'])
@csrf.exempt
@login_required
def api_storage_settings():
    """
    Get or update storage migration settings (admin only).

    GET: Returns current migration settings from recording_settings.json
    POST: Updates migration settings (persisted to recording_settings.json)

    Settings:
        - age_threshold_days: Days before migrating to archive
        - archive_retention_days: Days to keep files in archive before deletion
        - min_free_space_percent: Migrate when free space drops below this %
        - max_recent_storage_mb: Max size for recent storage (0 = unlimited)
        - max_archive_storage_mb: Max size for archive storage (0 = unlimited)
    """
    import json

    # Admin-only for all storage settings operations
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    config_path = '/app/config/recording_settings.json'

    try:
        if request.method == 'GET':
            # Read current settings
            with open(config_path, 'r') as f:
                config = json.load(f)

            migration = config.get('migration', {})
            storage_limits = config.get('storage_limits', {})

            return jsonify({
                'success': True,
                'settings': {
                    'age_threshold_days': migration.get('age_threshold_days', 3),
                    'archive_retention_days': migration.get('archive_retention_days', 90),
                    'min_free_space_percent': migration.get('min_free_space_percent', 20),
                    'max_recent_storage_mb': migration.get('max_recent_storage_mb', 0),
                    'max_archive_storage_mb': migration.get('max_archive_storage_mb', 0),
                    'enabled': migration.get('enabled', True)
                }
            })

        else:  # POST - update settings
            data = request.get_json() or {}

            # Read current config
            with open(config_path, 'r') as f:
                config = json.load(f)

            # Ensure migration section exists
            if 'migration' not in config:
                config['migration'] = {}

            # Update only provided fields
            migration = config['migration']
            if 'age_threshold_days' in data:
                migration['age_threshold_days'] = int(data['age_threshold_days'])
            if 'archive_retention_days' in data:
                migration['archive_retention_days'] = int(data['archive_retention_days'])
            if 'min_free_space_percent' in data:
                migration['min_free_space_percent'] = int(data['min_free_space_percent'])
            if 'max_recent_storage_mb' in data:
                migration['max_recent_storage_mb'] = int(data['max_recent_storage_mb'])
            if 'max_archive_storage_mb' in data:
                migration['max_archive_storage_mb'] = int(data['max_archive_storage_mb'])
            if 'enabled' in data:
                migration['enabled'] = bool(data['enabled'])

            # Write updated config
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=2)

            # Reload config in migration service if available
            migration_service = get_storage_migration_service()
            if migration_service:
                migration_service.config.reload()

            logger.info(f"Storage settings updated: {migration}")

            return jsonify({
                'success': True,
                'message': 'Settings updated successfully',
                'settings': migration
            })

    except Exception as e:
        logger.error(f"Storage settings API error: {e}")
        return jsonify({'error': str(e)}), 500


# Global migration status tracking
_migration_status = {
    'in_progress': False,
    'operation': None,
    'started_at': None,
    'files_processed': 0,
    'files_total': 0,
    'bytes_processed': 0,
    'current_file': None,
    'errors': [],
    'cancel_requested': False
}

# Thread-safe cancellation event for parallel workers
import threading
_migration_cancel_event = threading.Event()


class MigrationCancelled(Exception):
    """Raised when migration is cancelled by user."""
    pass


def update_migration_status(in_progress=None, operation=None, files_processed=None,
                           files_total=None, bytes_processed=None, current_file=None,
                           error=None, reset=False, cancel_requested=None):
    """Update global migration status for real-time UI updates."""
    global _migration_status
    if reset:
        _migration_status = {
            'in_progress': False,
            'operation': None,
            'started_at': None,
            'files_processed': 0,
            'files_total': 0,
            'bytes_processed': 0,
            'current_file': None,
            'errors': [],
            'cancel_requested': False
        }
        return

    if in_progress is not None:
        _migration_status['in_progress'] = in_progress
        if in_progress:
            _migration_status['started_at'] = datetime.now().isoformat()
    if operation is not None:
        _migration_status['operation'] = operation
    if files_processed is not None:
        _migration_status['files_processed'] = files_processed
    if files_total is not None:
        _migration_status['files_total'] = files_total
    if bytes_processed is not None:
        _migration_status['bytes_processed'] = bytes_processed
    if current_file is not None:
        _migration_status['current_file'] = current_file
    if error:
        _migration_status['errors'].append(error)
    if cancel_requested is not None:
        _migration_status['cancel_requested'] = cancel_requested


def check_migration_cancelled():
    """Check if migration cancellation was requested. Raises MigrationCancelled if so."""
    if _migration_status.get('cancel_requested'):
        raise MigrationCancelled("Migration cancelled by user")


@app.route('/api/storage/cancel', methods=['POST'])
@csrf.exempt
@login_required
def api_storage_cancel():
    """
    Cancel the current storage operation.
    Sets both the status flag and the threading.Event for parallel workers.
    """
    global _migration_cancel_event

    if not _migration_status.get('in_progress'):
        return jsonify({
            'success': False,
            'error': 'No operation in progress to cancel'
        }), 400

    operation = _migration_status.get('operation')
    update_migration_status(cancel_requested=True)
    _migration_cancel_event.set()  # Signal parallel workers to stop
    logger.info(f"Storage operation '{operation}' cancellation requested")

    return jsonify({
        'success': True,
        'message': f'Cancellation requested for {operation}',
        'operation': operation
    })


@app.route('/api/storage/migration-status', methods=['GET'])
@login_required
def api_migration_status():
    """
    Get current migration operation status for real-time UI updates.

    Returns:
        - in_progress: Whether migration is currently running
        - operation: Current operation type (migrate, cleanup, reconcile)
        - files_processed: Number of files processed so far
        - files_total: Total files to process (if known)
        - bytes_processed: Bytes processed so far
        - current_file: Currently processing file path
        - errors: List of errors encountered
    """
    return jsonify({
        'success': True,
        **_migration_status
    })


########################################################
#           🏃 MOTION DETECTION API ROUTES 🏃
########################################################

@app.route('/api/motion/status', methods=['GET'])
@login_required
def api_motion_status():
    """Get status of all motion detection services"""
    try:
        status = {
            'onvif': {},
            'ffmpeg': {},
            'reolink': {}
        }

        # ONVIF listeners
        if onvif_listener:
            for camera_id, is_active in onvif_listener.active_listeners.items():
                camera = camera_repo.get_camera(camera_id)
                status['onvif'][camera_id] = {
                    'camera_name': camera.get('name', camera_id) if camera else camera_id,
                    'active': is_active,
                    'method': 'onvif'
                }

        # FFmpeg detectors
        if ffmpeg_motion_detector:
            status['ffmpeg'] = ffmpeg_motion_detector.get_status()

        # Reolink Baichuan service
        if reolink_motion_service:
            status['reolink'] = reolink_motion_service.get_status()

        return jsonify({
            'success': True,
            'motion_detectors': status
        })

    except Exception as e:
        logger.error(f"Motion status API error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/motion/start/<camera_id>', methods=['POST'])
@csrf.exempt
@login_required
def api_motion_start(camera_id):
    """Start motion detection for a specific camera"""
    if not recording_service:
        return jsonify({'error': 'Recording service not available'}), 503

    try:
        camera = camera_repo.get_camera(camera_id)
        if not camera:
            return jsonify({'error': 'Camera not found'}), 404

        camera_name = camera.get('name', camera_id)
        camera_type = camera.get('type', '').lower()

        data = request.get_json() or {}
        method = data.get('method', 'auto')  # auto, onvif, ffmpeg

        # Auto-detect best method
        if method == 'auto':
            if camera_type == 'reolink':
                return jsonify({
                    'success': False,
                    'error': 'Reolink cameras use Baichuan service - start via /api/reolink/motion'
                }), 400
            elif 'ONVIF' in camera.get('capabilities', []):
                method = 'onvif'
            else:
                method = 'ffmpeg'

        success = False
        if method == 'onvif':
            if onvif_listener:
                success = onvif_listener.start_listener(camera_id)
            else:
                return jsonify({'error': 'ONVIF listener not available'}), 503
        elif method == 'ffmpeg':
            if ffmpeg_motion_detector:
                sensitivity = data.get('sensitivity', 0.3)
                success = ffmpeg_motion_detector.start_detector(camera_id, sensitivity)
            else:
                return jsonify({'error': 'FFmpeg detector not available'}), 503
        else:
            return jsonify({'error': f'Unknown method: {method}'}), 400

        return jsonify({
            'success': success,
            'camera_id': camera_id,
            'camera_name': camera_name,
            'method': method,
            'message': f'Motion detection started ({method})' if success else 'Failed to start'
        })

    except Exception as e:
        logger.error(f"Motion start API error for {camera_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/motion/stop/<camera_id>', methods=['POST'])
@csrf.exempt
@login_required
def api_motion_stop(camera_id):
    """Stop motion detection for a specific camera"""
    try:
        stopped = []

        # Stop ONVIF listener
        if onvif_listener and camera_id in onvif_listener.active_listeners:
            onvif_listener.stop_listener(camera_id)
            stopped.append('onvif')

        # Stop FFmpeg detector
        if ffmpeg_motion_detector and camera_id in ffmpeg_motion_detector.active_detectors:
            ffmpeg_motion_detector.stop_detector(camera_id)
            stopped.append('ffmpeg')

        if stopped:
            return jsonify({
                'success': True,
                'camera_id': camera_id,
                'stopped_methods': stopped,
                'message': f'Stopped motion detection: {", ".join(stopped)}'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'No active motion detection found for this camera'
            }), 404

    except Exception as e:
        logger.error(f"Motion stop API error for {camera_id}: {e}")
        return jsonify({'error': str(e)}), 500


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
    DEPRECATED: Old mutual exclusion policy for UI Health vs per-stream watchdog.

    As of Jan 4, 2026:
    - Old per-stream watchdog (ENABLE_WATCHDOG) removed from StreamManager
    - New StreamWatchdog service COEXISTS with UI Health
    - UI Health: detects browser/network issues (frontend)
    - StreamWatchdog: detects server/camera issues (backend)

    This function is kept for backward compatibility but no longer enforces
    mutual exclusion. Use STREAM_WATCHDOG_ENABLED for new watchdog.

    Returns (ui_health_enabled, watchdog_enabled)
    """
    # UI Health always available now (no conflict with new watchdog)
    ui_enabled = _get_bool("NVR_UI_HEALTH_ENABLED", default=True)

    # Old ENABLE_WATCHDOG is deprecated - check new STREAM_WATCHDOG_ENABLED
    wd_enabled = _get_bool("NVR_STREAM_WATCHDOG_ENABLED", default=False)

    return ui_enabled, wd_enabled

def _ui_health_from_env():
    """
    Build UI health settings dict from environment variables AND cameras.json global settings.
    Priority: cameras.json > .env
    """
    # Start with .env defaults (KEEP THIS - provides fallbacks)
    settings = {
        'uiHealthEnabled': _get_bool("NVR_UI_HEALTH_ENABLED", True),
        'sampleIntervalMs': _get_int("NVR_UI_HEALTH_SAMPLE_INTERVAL_MS", 2000),
        'staleAfterMs': _get_int("NVR_UI_HEALTH_STALE_AFTER_MS", 20000),
        'consecutiveBlankNeeded': _get_int("NVR_UI_HEALTH_CONSECUTIVE_BLANK_NEEDED", 10),
        'cooldownMs': _get_int("NVR_UI_HEALTH_COOLDOWN_MS", 30000),
        'warmupMs': _get_int("NVR_UI_HEALTH_WARMUP_MS", 60000),
        'maxAttempts': _get_int("NVR_UI_HEALTH_MAX_ATTEMPTS", 10),  # NEW
        'blankThreshold': {
            'avg': _get_int("NVR_UI_HEALTH_BLANK_AVG", 12),
            'std': _get_int("NVR_UI_HEALTH_BLANK_STD", 5)
        }
    }
    
    # Override with cameras.json (this flattens blankThreshold)
    try:
        global_settings = camera_repo.cameras_data.get('ui_health_global_settings', {})
        if global_settings:
            key_mapping = {
                'UI_HEALTH_ENABLED': 'uiHealthEnabled',
                'UI_HEALTH_SAMPLE_INTERVAL_MS': 'sampleIntervalMs',
                'UI_HEALTH_STALE_AFTER_MS': 'staleAfterMs',
                'UI_HEALTH_CONSECUTIVE_BLANK_NEEDED': 'consecutiveBlankNeeded',
                'UI_HEALTH_COOLDOWN_MS': 'cooldownMs',
                'UI_HEALTH_WARMUP_MS': 'warmupMs',
                'UI_HEALTH_BLANK_AVG': 'blankAvg',  # Flattens from nested
                'UI_HEALTH_BLANK_STD': 'blankStd',  # Flattens from nested
                'UI_HEALTH_MAX_ATTEMPTS': 'maxAttempts'  # NEW
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
    app_state.set_shutting_down()

    print("\n🛑 Shutting down... cleaning up streams and resources")
    try:
        # Stop stream watchdog first (prevents restart attempts during cleanup)
        if stream_watchdog:
            print("  Stopping Stream Watchdog...")
            stream_watchdog.stop()

        # Stop motion detection services
        if onvif_listener:
            print("  Stopping ONVIF listeners...")
            onvif_listener.stop_all()
        if ffmpeg_motion_detector:
            print("  Stopping FFmpeg motion detectors...")
            ffmpeg_motion_detector.stop_all()
        if reolink_motion_service:
            print("  Stopping Reolink motion service...")
            reolink_motion_service.stop()

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

    # Initialize storage migration service with auto-migration monitor
    # This starts a background thread that checks disk capacity every 5 minutes
    # and automatically migrates old recordings when space is low
    try:
        get_storage_migration_service()
        print(f"📦 Storage auto-migration monitor started (5 min interval)")
    except Exception as e:
        print(f"⚠️ Storage migration service failed to start: {e}")

    # NOTE: debug=False and use_reloader=False prevent Flask from spawning 2 processes
    # which caused duplicate auto-start attempts and MediaMTX "closing existing publisher" errors
    app.run(debug=False, host='0.0.0.0', port=5000, use_reloader=False, threaded=True)
