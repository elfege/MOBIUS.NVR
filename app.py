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

from flask import Flask, render_template, jsonify, request, Response, redirect, send_file
from flask_wtf import FlaskForm
from flask_wtf.csrf import CSRFProtect
from wtforms import SelectField, SubmitField
from wtforms.validators import DataRequired

# modular imports
from services.camera_repository import CameraRepository
from services.ptz.ptz_validator import PTZValidator
from streaming.stream_manager import StreamManager

from low_level_handlers.process_reaper import install_sigchld_handler

# from services.eufy.eufy_bridge import EufyBridge
# from services.eufy.eufy_bridge_client import submit_captcha_sync, submit_2fa_sync, check_status_sync
from services.eufy.eufy_bridge_watchdog import BridgeWatchdog
from services.unifi_protect_service import UniFiProtectService
from services.unifi_service_resource_monitor import UniFiServiceResourceMonitor
from services.app_restart_handler import AppRestartHandler
from services.unifi_mjpeg_capture_service import unifi_mjpeg_capture_service
from services.amcrest_mjpeg_capture_service import amcrest_mjpeg_capture_service
from services.reolink_mjpeg_capture_service import reolink_mjpeg_capture_service
from services.mediaserver_mjpeg_service import mediaserver_mjpeg_service
from services.ptz.amcrest_ptz_handler import amcrest_ptz_handler
from services.onvif.onvif_ptz_handler import ONVIFPTZHandler
from services.ptz.baichuan_ptz_handler import BaichuanPTZHandler
from services.recording.recording_service import RecordingService
from config.recording_config_loader import RecordingConfig
from services.recording.snapshot_service import SnapshotService
from services.recording.timeline_service import get_timeline_service, TimelineService
from services.onvif.onvif_event_listener import ONVIFEventListener
from services.credentials.reolink_credential_provider import ReolinkCredentialProvider
from services.motion.reolink_motion_service import create_reolink_motion_service
from services.motion.ffmpeg_motion_detector import create_ffmpeg_detector
from services.camera_state_tracker import camera_state_tracker
from services.stream_watchdog import StreamWatchdog
from services.websocket_mjpeg_service import websocket_mjpeg_service

from low_level_handlers.cleanup_handler import stop_all_services, kill_all, kill_ffmpeg

# Flask-SocketIO for WebSocket MJPEG multiplexing
# Uses simple-websocket for Gunicorn compatibility (gthread workers)
from flask_socketio import SocketIO, emit, join_room, leave_room

# Flask app setup
app = Flask(__name__)
app.config['SECRET_KEY'] = '-ratatouillemescouilles'
app.config['TEMPLATES_AUTO_RELOAD'] = True
csrf = CSRFProtect(app)

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

        # Collect cameras that need MJPEG pre-warming
        cameras_to_prewarm = {}
        for camera_serial, camera_config in camera_repo.get_all_cameras().items():
            mjpeg_source = camera_config.get('mjpeg_source', 'mediaserver')
            if mjpeg_source == 'mediaserver':
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

    if os.getenv('USE_EUFY_BRIDGE', False).lower() in ['1', 'true']:
        print("🌉 Initializing Eufy bridge...")
        # Eufy bridge for PTZ control
        # eufy_bridge = EufyBridge() # Eufy Bridge no longer in use for now
        # print("✅ Eufy bridge initialized")
    else:
        eufy_bridge = None
    
    if os.getenv('USE_EUFY_BRIDGE_WATCHDOG', False).lower() in ['1', 'true']:
        # bridge_watchdog = BridgeWatchdog(eufy_bridge) # Eufy Bridge no longer in use for now
        print("✅ Eufy bridge_watchdog initialized")

    print("\n✅ All core services initialized successfully!\n")

except Exception as e:
    print(f"\n❌ Failed to initialize services: {e}")
    print(traceback.print_exc())
    exit(1)


# def wait_for_bridge_ready(timeout=5):
#     """Wait for bridge to be ready"""
#     if os.getenv('USE_EUFY_BRIDGE', False).lower() in ['1', 'true']:
#         t0 = time.time()
#         while time.time() - t0 < timeout:
#             if eufy_bridge.is_running():
#                 return True
#             time.sleep(0.25)
#     return False

# ===== Auto-start Eufy Bridge =====
# try:
#     print("\n🌉 Starting Eufy bridge...")
#     if os.getenv('USE_EUFY_BRIDGE', False).lower() in ['1', 'true']:
#         if not eufy_bridge.is_running():
#             eufy_bridge.start()
#             if wait_for_bridge_ready():
#                 print("✅ Bridge started successfully")

#     if os.getenv('USE_EUFY_BRIDGE_WATCHDOG', False).lower() in ['1', 'true']:
#         bridge_watchdog.start_monitoring()
#         print("✅ Bridge watchdog started")
        
# except Exception as e:
#     print(f"⚠️  Bridge startup warning: {e}")

# ===== Auto-start Streams =====
# REMOVED: Duplicate auto-start block was here (lines 413-425)
# Stream auto-start is now handled ONLY by auto_start_all_streams() at line ~121
# Having two auto-start blocks caused duplicate MediaMTX publishers and broken pipes

# ===== Start Camera State Tracker =====
try:
    print("\n📡 Starting Camera State Tracker...")
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
            # Eufy uses Reolink credentials (same credential structure)
            username, password = reolink_creds.get_credentials(camera_serial)
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
    restart_handler = AppRestartHandler(stream_manager) #, bridge_watchdog) #, eufy_bridge)

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

@app.route('/reloading')
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
def api_status():
    """Get system status"""
    # eufy_status = {
    #     'bridge_running': eufy_bridge.is_running(),
    #     'bridge_ready': eufy_bridge.is_ready(),
    #     'total_devices': camera_repo.get_camera_count(),
    #     'ptz_cameras': len(camera_repo.get_ptz_cameras())
    # }

    unifi_status = {
        camera_id: {
            'name': camera.name,
            'session_active': camera.session_active,
            'type': 'unifi'
        }
        for camera_id, camera in unifi_cameras.items()
    }

    return jsonify({
        # 'eufy': eufy_status, --- IGNORE ---
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

# ===== Streaming Configuration Routes =====

@app.route('/api/config/streaming')
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
# DEPRECATED OR NOT IN USE FOR NOW # TBD...
@app.route('/api/bridge/start', methods=['POST'])
@csrf.exempt
def api_bridge_start():
    """Start the Eufy bridge"""
    try:
        raise # no longer in use for now. 
        success = eufy_bridge.start()
        return jsonify({
            'success': success,
            'message': 'Bridge started successfully' if success else 'Failed to start bridge'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# DEPRECATED OR NOT IN USE FOR NOW # TBD... 
@app.route('/api/bridge/stop', methods=['POST'])
@csrf.exempt
def api_bridge_stop():
    """Stop the Eufy bridge"""
    try:
        raise # no longer in use for now.
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

        # Extract resolution from request (defaults to 'sub' for grid view)
        # 'sub' = low-res for grid, 'main' = high-res for fullscreen
        data = request.get_json() or {}
        resolution = data.get('type', 'sub')  # 'main' or 'sub'

        print(f"[API] /api/stream/start/{camera_serial} - resolution={resolution}")

        # Start the stream with specified resolution
        stream_url = stream_manager.start_stream(
            camera_serial, resolution=resolution)

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

        # Check stream type - only support non-MJPEG for now
        stream_type = camera.get('stream_type', 'HLS').upper()
        if stream_type == 'MJPEG':
            return jsonify({
                'success': False,
                'error': 'MJPEG streams do not support restart (stateless)'
            }), 400

        logger.info(f"[RESTART] Restarting stream for {camera_name} ({camera_serial})")

        # Step 1: Stop the stream (kills FFmpeg) or clear zombie slot
        # NOTE: We must clear ANY slot (even 'starting' status) to prevent zombie slots
        # from blocking new stream starts. is_stream_alive() returns False for 'starting'
        # status, but those slots still need to be cleared.
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
                # Force remove the slot to allow fresh start
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

        logger.info(f"[RESTART] Stream restarted for {camera_name}: {stream_url}")

        return jsonify({
            'success': True,
            'camera_serial': camera_serial,
            'camera_name': camera_name,
            'stream_url': stream_url,
            'was_running': was_running,
            'message': f'Stream restarted for {camera_name}'
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

########################################################-########################################################
#                                           ⚙️⚙️⚙️⚙️ HLS ⚙️⚙️⚙️⚙️
########################################################-########################################################
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

########################################################-########################################################
#                                           ⚙️⚙️⚙️⚙️ UNIFI ⚙️⚙️⚙️⚙️
########################################################-########################################################
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

########################################################-########################################################
#                                    MEDIASERVER MJPEG (tap MediaMTX)
########################################################-########################################################
@app.route('/api/mediaserver/<camera_id>/stream/mjpeg')
@csrf.exempt
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

        # Verify this camera should use mediaserver MJPEG
        mjpeg_source = camera_config.get('mjpeg_source', 'mediaserver')
        if mjpeg_source != 'mediaserver':
            logger.warning(f"Camera {camera_id} has mjpeg_source='{mjpeg_source}', not 'mediaserver'")
            # Still allow it - frontend may be forcing mediaserver for portable devices

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


@app.route('/api/status/websocket-mjpeg')
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
            # UniFi uses shared frame buffer
            if camera_id in unifi_frame_buffers:
                frame_data = unifi_frame_buffers[camera_id]

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
#                                ⚙️⚙️⚙️⚙️ EUFY BRIDGE AUTHENTICATION ROUTES ⚙️⚙️⚙️⚙️
########################################################-########################################################
@app.route('/eufy-auth')
@csrf.exempt
def eufy_auth_page():
    """
    Serve Eufy authentication page for captcha and 2FA submission
    """
    return render_template('eufy_auth.html')

@app.route('/api/eufy-auth/captcha', methods=['POST'])
@csrf.exempt
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
        raise # eufy bridge no longer in use for now.
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

########################################################-########################################################
#                                          ⚙️⚙️⚙️⚙️PTZ CONTROLS⚙️⚙️⚙️⚙️
########################################################-########################################################
@app.route('/api/ptz/<camera_serial>/<direction>', methods=['POST'])
@csrf.exempt
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
        use_baichuan = camera_type == 'reolink' and BaichuanPTZHandler.is_baichuan_capable(camera)

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

        #######################################
        # Eufy Bridge No longer in use for now.
        #######################################
        # # Eufy uses bridge (no ONVIF support)
        # elif camera_type == 'eufy':
        #     print(f"[APP.PY] Dispatching PTZ to EUFY handler")
        #     success = eufy_bridge.move_camera(camera_serial, direction, camera_repo)
        #     message = f'Camera moved {direction}' if success else 'Movement failed'

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

        # Only Amcrest, Reolink, and SV3C support presets
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
def api_ptz_goto_preset(camera_serial, preset_token):
    """Move camera to preset position"""
    try:
        # Validate camera
        camera = camera_repo.get_camera(camera_serial)
        if not camera:
            return jsonify({'success': False, 'error': 'Camera not found'}), 404

        camera_type = camera.get('type')

        # Only Amcrest, Reolink, and SV3C support presets
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


@app.route('/api/ptz/latency/<client_uuid>/<camera_serial>', methods=['GET'])
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
        import requests
        postgrest_url = os.getenv('POSTGREST_URL', 'http://postgrest:3001')

        # Query PostgREST for this client/camera pair
        response = requests.get(
            f"{postgrest_url}/ptz_client_latency",
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
        import requests
        import json

        data = request.get_json()
        observed_latency = data.get('observed_latency_ms')

        if observed_latency is None:
            return jsonify({'success': False, 'error': 'observed_latency_ms required'}), 400

        postgrest_url = os.getenv('POSTGREST_URL', 'http://postgrest:3001')

        # First, try to get existing record
        get_response = requests.get(
            f"{postgrest_url}/ptz_client_latency",
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
            update_response = requests.patch(
                f"{postgrest_url}/ptz_client_latency",
                params={
                    'client_uuid': f'eq.{client_uuid}',
                    'camera_serial': f'eq.{camera_serial}'
                },
                json={
                    'avg_latency_ms': avg_latency,
                    'samples': samples,
                    'sample_count': len(samples)
                },
                headers={'Content-Type': 'application/json', 'Prefer': 'return=representation'},
                timeout=5
            )
            success = update_response.status_code in [200, 204]
        else:
            # Insert new record
            insert_response = requests.post(
                f"{postgrest_url}/ptz_client_latency",
                json=record_data,
                headers={'Content-Type': 'application/json', 'Prefer': 'return=representation'},
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

########################################################
#           📹 RECORDING API ROUTES 📹
########################################################

@app.route('/api/recording/settings/<camera_id>', methods=['GET', 'POST'])
@csrf.exempt
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
            from datetime import datetime
            start_time = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
            end_time = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
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
            from datetime import datetime
            start_time = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
            end_time = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
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


@app.route('/api/timeline/exports', methods=['GET'])
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
#           📦 STORAGE MIGRATION API ROUTES 📦
########################################################

# Global storage migration service instance
_storage_migration_service = None

def get_storage_migration_service():
    """
    Get or create the StorageMigrationService singleton.
    Lazy initialization to avoid import issues at startup.
    """
    global _storage_migration_service
    if _storage_migration_service is None:
        from services.recording.storage_migration import StorageMigrationService
        _storage_migration_service = StorageMigrationService()
    return _storage_migration_service


@app.route('/api/storage/stats', methods=['GET'])
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
def api_storage_migrate():
    """
    Trigger storage migration from recent to archive tier.

    Request Body (JSON, optional):
        recording_type: Type to migrate (default: "motion")
        force: Bypass threshold checks (default: false)

    Returns:
        Migration result with counts and details
    """
    try:
        data = request.get_json() or {}
        recording_type = data.get('recording_type', 'motion')
        force = data.get('force', False)

        migration_service = get_storage_migration_service()
        result = migration_service.migrate_recent_to_archive(recording_type, force)

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

    except Exception as e:
        logger.error(f"Storage migrate API error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/storage/cleanup', methods=['POST'])
@csrf.exempt
def api_storage_cleanup():
    """
    Trigger archive cleanup (deletion of old files).

    Request Body (JSON, optional):
        recording_type: Type to clean (default: "motion")
        force: Bypass threshold checks (default: false)

    Returns:
        Cleanup result with counts and details
    """
    try:
        data = request.get_json() or {}
        recording_type = data.get('recording_type', 'motion')
        force = data.get('force', False)

        migration_service = get_storage_migration_service()
        result = migration_service.cleanup_archive(recording_type, force)

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
        return jsonify({'error': str(e)}), 500


@app.route('/api/storage/reconcile', methods=['POST'])
@csrf.exempt
def api_storage_reconcile():
    """
    Reconcile database with filesystem.
    Removes orphaned database entries where files no longer exist.

    Returns:
        Reconciliation result with removed entry count
    """
    try:
        migration_service = get_storage_migration_service()
        result = migration_service.reconcile_db_with_filesystem()

        return jsonify({
            'success': True,
            'operation': 'reconcile',
            'orphaned_removed': result.success_count,
            'failed': result.failed_count,
            'errors': result.errors[:10] if result.errors else []
        })

    except Exception as e:
        logger.error(f"Storage reconcile API error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/storage/migrate/full', methods=['POST'])
@csrf.exempt
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


########################################################
#           🏃 MOTION DETECTION API ROUTES 🏃
########################################################

@app.route('/api/motion/status', methods=['GET'])
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
    ui_enabled = _get_bool("UI_HEALTH_ENABLED", default=True)

    # Old ENABLE_WATCHDOG is deprecated - check new STREAM_WATCHDOG_ENABLED
    wd_enabled = _get_bool("STREAM_WATCHDOG_ENABLED", default=False)

    return ui_enabled, wd_enabled

def _ui_health_from_env():
    """
    Build UI health settings dict from environment variables AND cameras.json global settings.
    Priority: cameras.json > .env
    """
    # Start with .env defaults (KEEP THIS - provides fallbacks)
    settings = {
        'uiHealthEnabled': _get_bool("UI_HEALTH_ENABLED", True),
        'sampleIntervalMs': _get_int("UI_HEALTH_SAMPLE_INTERVAL_MS", 2000),
        'staleAfterMs': _get_int("UI_HEALTH_STALE_AFTER_MS", 20000),
        'consecutiveBlankNeeded': _get_int("UI_HEALTH_CONSECUTIVE_BLANK_NEEDED", 10),
        'cooldownMs': _get_int("UI_HEALTH_COOLDOWN_MS", 30000),
        'warmupMs': _get_int("UI_HEALTH_WARMUP_MS", 60000),
        'maxAttempts': _get_int("UI_HEALTH_MAX_ATTEMPTS", 10),  # NEW
        'blankThreshold': {
            'avg': _get_int("UI_HEALTH_BLANK_AVG", 12),
            'std': _get_int("UI_HEALTH_BLANK_STD", 5)
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
                        #   bridge_watchdog, # Eufy Bridge no longer in use for now
                        #   eufy_bridge, # Eufy Bridge no longer in use for now
                          unifi_cameras,
                          unifi_resource_monitor,
                          unifi_mjpeg_capture_service,
                          reolink_mjpeg_capture_service,
                          amcrest_mjpeg_capture_service)
    except Exception as e:
        print(traceback.print_exc())
        print(f"Cleanup error: {e}")
    finally:
        # kill_all(eufy_bridge, stream_manager) # Eufy Bridge no longer in use for now
        kill_all(stream_manager)
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
signal.signal(signal.SIGTSTP, functools.partial(kill_all)) # , eufy_bridge)) # Eufy Bridge no longer in use for now

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

    # NOTE: debug=False and use_reloader=False prevent Flask from spawning 2 processes
    # which caused duplicate auto-start attempts and MediaMTX "closing existing publisher" errors
    app.run(debug=False, host='0.0.0.0', port=5000, use_reloader=False, threaded=True)
