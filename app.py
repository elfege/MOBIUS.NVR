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
from services.settings import Settings
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
from services.license_service import license, validate_license

from low_level_handlers.cleanup_handler import stop_all_services, kill_all, kill_ffmpeg

# New Blueprint imports
import routes.shared as _shared
from routes.auth import auth_bp
from routes.camera import camera_bp
from routes.config import config_bp
from routes.eufy import eufy_bp
from routes.power import power_bp
from routes.presence import presence_bp
from routes.ptz import ptz_bp
from routes.recording import recording_bp
from routes.storage import storage_bp
from routes.streaming import streaming_bp, init_socketio as _init_streaming_socketio
from routes.settings_routes import settings_bp
from routes.talkback import talkback_bp, init_socketio as _init_talkback_socketio

# Flask-SocketIO for WebSocket MJPEG multiplexing
# Uses simple-websocket for Gunicorn compatibility (gthread workers)
from flask_socketio import SocketIO, emit, join_room, leave_room

# Flask app setup
app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True


def _get_or_create_secret_key():
    """
    Retrieve NVR_SECRET_KEY from DB (nvr_settings table) via direct
    psycopg2 connection (not PostgREST, which may not be ready at startup).
    Falls back to env var, then generates a new one if neither exists.
    The key is persisted to DB so it survives container restarts without
    needing any secrets file on disk.
    """
    import psycopg2

    _pg_host = os.getenv('POSTGRES_HOST', 'postgres')
    _pg_port = os.getenv('POSTGRES_PORT', '5432')
    _pg_db = os.getenv('POSTGRES_DB', 'nvr')
    _pg_user = os.getenv('POSTGRES_USER', 'nvr_api')
    _pg_pass = os.getenv('POSTGRES_PASSWORD', 'nvr_internal_db_key')

    def _db_query(sql, params=None):
        """Run a SQL query via psycopg2 and return the first result value."""
        try:
            conn = psycopg2.connect(
                host=_pg_host, port=_pg_port, dbname=_pg_db,
                user=_pg_user, password=_pg_pass,
                connect_timeout=10
            )
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute(sql, params)
            row = cur.fetchone()
            cur.close()
            conn.close()
            return row[0] if row else ''
        except Exception as e:
            print(f"[SECRET_KEY] DB query failed: {e}")
            return ''

    # 1. Try reading from DB (direct connection — always available before PostgREST)
    db_key = _db_query("SELECT value FROM nvr_settings WHERE key='NVR_SECRET_KEY';")
    if db_key:
        return db_key

    # 2. Try env var (backward compat / migration path)
    env_key = os.environ.get('NVR_SECRET_KEY', '')
    if env_key:
        _db_query("SELECT upsert_setting(%s, %s);", ('NVR_SECRET_KEY', env_key))
        return env_key

    # 3. Generate new key and store in DB
    import secrets as _secrets
    new_key = _secrets.token_hex(32)
    _db_query("SELECT upsert_setting(%s, %s);", ('NVR_SECRET_KEY', new_key))
    return new_key


_secret_key = _get_or_create_secret_key()
app.config['SECRET_KEY'] = _secret_key
# Also set as env var so credential_db_service and other modules can read it
os.environ['NVR_SECRET_KEY'] = _secret_key
csrf = CSRFProtect(app)

# Register Blueprints
app.register_blueprint(cert_bp)
app.register_blueprint(external_api_bp)

# Register new modular blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(camera_bp)
app.register_blueprint(config_bp)
app.register_blueprint(eufy_bp)
app.register_blueprint(power_bp)
app.register_blueprint(presence_bp)
app.register_blueprint(ptz_bp)
app.register_blueprint(recording_bp)
app.register_blueprint(storage_bp)
app.register_blueprint(streaming_bp)
app.register_blueprint(talkback_bp)
app.register_blueprint(settings_bp)

# Exempt all API blueprints from CSRF validation.
# All routes use JSON APIs consumed by frontend JS (not HTML forms).
# The custom @csrf_exempt decorator in helpers.py sets f._csrf_exempt but
# Flask-WTF only checks its internal _exempt_views/_exempt_blueprints sets,
# so we must register exemptions via the CSRFProtect instance directly.
for bp in [auth_bp, camera_bp, config_bp, eufy_bp, power_bp, presence_bp,
           ptz_bp, recording_bp, storage_bp, streaming_bp, talkback_bp]:
    csrf.exempt(bp)

# ===== License Validation =====
# Validates on startup. Sets global license state (demo/valid/expired).
# Demo mode: 7 days, max 2 cameras, no recording, watermark on streams.
logger = logging.getLogger(__name__)
try:
    validate_license()
    if license.is_demo:
        logger.warning(f"LICENSE: {license.message}")
    else:
        logger.info(f"LICENSE: Valid, expires {license.expires}")
except Exception as e:
    logger.error(f"LICENSE: Validation failed: {e} — running in demo mode")
    license.set_demo()

# Flask-Login session configuration
# Indefinite sessions until logout (no automatic expiry)
from datetime import timedelta
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_ENV') != 'development'
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=365)

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'
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


def _get_client_ip():
    """
    Get the real client IP address, accounting for nginx reverse proxy.
    Nginx sets X-Forwarded-For to the actual client IP.
    Falls back to request.remote_addr (which is nginx's container IP behind proxy).
    """
    forwarded = request.headers.get('X-Forwarded-For', '')
    if forwarded:
        # X-Forwarded-For can be a comma-separated list; first is the real client
        return forwarded.split(',')[0].strip()
    return request.remote_addr


def _is_same_subnet(client_ip):
    """
    Check if client IP is on the same private subnet as the NVR host.
    Compares the first 3 octets (assumes /24 subnet for home networks).
    Only considers private IP ranges (192.168.x.x, 10.x.x.x, 172.16-31.x.x).
    """
    import ipaddress
    try:
        client = ipaddress.ip_address(client_ip)
        if not client.is_private:
            return False
        # Get host IP from environment (set by start.sh)
        host_ip = os.environ.get('NVR_LOCAL_HOST_IP', '')
        if not host_ip:
            return False
        host = ipaddress.ip_address(host_ip)
        # Compare /24 subnet (first 3 octets)
        client_net = ipaddress.ip_network(f"{client_ip}/24", strict=False)
        host_net = ipaddress.ip_network(f"{host_ip}/24", strict=False)
        return client_net == host_net
    except (ValueError, TypeError):
        return False


# Cache for trusted network setting (avoid DB query on every request)
_trusted_network_cache = {'enabled': None, 'checked_at': 0}


def _is_trusted_network_enabled():
    """
    Check if the admin has enabled 'Trust this network' setting.
    Cached for 30 seconds to avoid hammering the DB on every request.
    """
    import time as _time
    now = _time.time()
    if _trusted_network_cache['enabled'] is not None and (now - _trusted_network_cache['checked_at']) < 30:
        return _trusted_network_cache['enabled']

    try:
        import psycopg2
        conn = psycopg2.connect(
            host=os.getenv('POSTGRES_HOST', 'postgres'),
            port=os.getenv('POSTGRES_PORT', '5432'),
            dbname=os.getenv('POSTGRES_DB', 'nvr'),
            user=os.getenv('POSTGRES_USER', 'nvr_api'),
            password=os.getenv('POSTGRES_PASSWORD', 'nvr_internal_db_key'),
            connect_timeout=3
        )
        cur = conn.cursor()
        cur.execute("SELECT value FROM nvr_settings WHERE key='TRUSTED_NETWORK_ENABLED';")
        row = cur.fetchone()
        cur.close()
        conn.close()
        enabled = row[0].lower() == 'true' if row else False
    except Exception:
        enabled = False

    _trusted_network_cache['enabled'] = enabled
    _trusted_network_cache['checked_at'] = now
    return enabled


@app.before_request
def _auto_login_trusted():
    """
    Auto-login users via trusted device OR trusted network.

    Priority:
    1. Already authenticated → skip
    2. Trusted device cookie → auto-login as device's user
    3. Trusted network enabled + same subnet → auto-login as admin
    4. None → normal login required
    """
    from flask_login import current_user as _cu
    # Skip if already authenticated
    if _cu and _cu.is_authenticated:
        return

    # Skip routes that don't need auth
    skip_prefixes = ('/static/', '/api/health', '/login', '/favicon',
                     '/install-cert', '/api/cert/')
    if any(request.path.startswith(p) for p in skip_prefixes):
        return

    # --- Trusted device (existing logic) ---
    device_token = request.cookies.get('device_token')
    if device_token:
        try:
            resp = _shared._postgrest_session.get(
                f"{_shared.POSTGREST_URL}/trusted_devices",
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
                        session['auth_method'] = 'trusted_device'
                        print(f"[TrustedDevice] Auto-login: {user.username} (token: {device_token[:8]}...)")
                        return
        except Exception as e:
            print(f"[TrustedDevice] Auto-login check failed: {e}")

    # --- Trusted network (new logic) ---
    if _is_trusted_network_enabled():
        client_ip = _get_client_ip()
        if _is_same_subnet(client_ip):
            # Auto-login as admin (default user for trusted network)
            admin_user, _ = User.get_by_username("admin")
            if admin_user:
                login_user(admin_user, remember=True)
                session['auth_method'] = 'trusted_network'
                print(f"[TrustedNetwork] Auto-login: admin (client: {client_ip})")
                return


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

    # cameras.json is NO LONGER used at runtime. The database is the sole source
    # of truth. cameras.json is retained as a brand schema template for the
    # "Add Camera" UI form. New cameras are added via the UI, not by editing JSON.
    #
    # To import cameras from cameras.json (first boot / migration), use:
    #   python3 -c "from services.camera_config_sync import sync_cameras_json_to_db; sync_cameras_json_to_db()"
    print("✅ Camera data: database is sole source of truth (cameras.json not used at runtime)")

    # Migrate credentials from env vars to database (one-time, idempotent)
    try:
        from services.credentials.migrate_env_to_db import migrate_all as migrate_credentials
        cred_count = migrate_credentials()
        if cred_count > 0:
            print(f"✅ Credential migration: {cred_count} credentials moved from env vars to database")
        else:
            print("✅ Credentials: all in database (no env var migration needed)")
    except Exception as e:
        print(f"⚠️ Credential migration failed (non-fatal, env vars still work): {e}")

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

    # Timeline service (global singleton)
    timeline_service = get_timeline_service()
    print("✅ Timeline service initialized")

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
# ── Unified Settings Manager ──────────────────────────────────────────────
settings = Settings()
print("✅ Settings manager initialized")

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

# ===== Set service references on shared module (for blueprints) =====
_shared.set_services(
    camera_repo=camera_repo,
    ptz_validator=ptz_validator,
    stream_manager=stream_manager,
    recording_service=recording_service,
    snapshot_service=snapshot_service,
    timeline_service=timeline_service,
    onvif_listener=onvif_listener,
    ffmpeg_motion_detector=ffmpeg_motion_detector,
    reolink_motion_service=reolink_motion_service,
    eufy_bridge=eufy_bridge,
    bridge_watchdog=bridge_watchdog,
    unifi_cameras=unifi_cameras,
    unifi_resource_monitor=unifi_resource_monitor,
    stream_watchdog=stream_watchdog,
    hubitat_power_service=hubitat_power_service,
    unifi_poe_service=unifi_poe_service,
    presence_service=presence_service,
    restart_handler=restart_handler,
    settings=settings,
    app_state=app_state,
    socketio=socketio,
    reolink_mjpeg_capture_service=reolink_mjpeg_capture_service,
    amcrest_mjpeg_capture_service=amcrest_mjpeg_capture_service,
    unifi_mjpeg_capture_service=unifi_mjpeg_capture_service,
    sv3c_mjpeg_capture_service=sv3c_mjpeg_capture_service,
    mediaserver_mjpeg_service=mediaserver_mjpeg_service,
    websocket_mjpeg_service=websocket_mjpeg_service,
    camera_state_tracker=camera_state_tracker,
)
print("✅ Shared service registry populated for blueprints")

# ===== Register SocketIO event handlers =====
# Must run AFTER set_services() so socketio is no longer None.
# Handlers are defined as plain functions in streaming.py and talkback.py;
# init_socketio(sio) registers them via sio.on(...)(fn) to avoid the
# import-time AttributeError that occurs when @shared.socketio.on(...)
# decorators fire before shared.socketio is set.
_init_streaming_socketio(socketio)
_init_talkback_socketio(socketio)
print("✅ SocketIO handlers registered (/mjpeg, /stream_events, /talkback)")

# ===== Flask Forms =====
class PTZControlForm(FlaskForm):
    """Form for PTZ camera selection"""
    camera = SelectField('Camera', validators=[DataRequired()])
    submit = SubmitField('Select Camera')

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
