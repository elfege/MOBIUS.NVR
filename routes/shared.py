"""
routes/shared.py — Service registry for NVR Flask Blueprints.

All service singletons start as None and are set by app.py after the
initialization block completes.  Blueprints import this module and access
services via `shared.<name>` — **never** import the service instance directly
from app.py, as that would create circular imports.

PostgREST connection objects are module-level singletons so every blueprint
shares a single keep-alive TCP connection pool.
"""

import os
import requests

# ---------------------------------------------------------------------------
# PostgREST connection
# Using requests.Session() keeps TCP connections alive between calls,
# eliminating per-request TCP handshake overhead (~1-3 ms per call on Docker
# network).
# ---------------------------------------------------------------------------
POSTGREST_URL: str = os.getenv('NVR_POSTGREST_URL', 'http://postgrest:3001')
_postgrest_session: requests.Session = requests.Session()
_postgrest_session.headers.update({'Content-Type': 'application/json'})

# ---------------------------------------------------------------------------
# CSRF protect instance — set by app.py immediately after CSRFProtect(app).
# Blueprints that need @csrf.exempt call the standalone csrf_exempt()
# decorator from routes.helpers instead (avoids import-time chicken-and-egg).
# ---------------------------------------------------------------------------
csrf = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Service singletons — all set by app.py during startup via set_services().
# Typed as None here; blueprints must guard against None where appropriate.
# ---------------------------------------------------------------------------
camera_repo = None
ptz_validator = None
stream_manager = None
recording_service = None
snapshot_service = None
timeline_service = None
onvif_listener = None
ffmpeg_motion_detector = None
reolink_motion_service = None
eufy_bridge = None
bridge_watchdog = None
unifi_cameras: dict = {}
unifi_resource_monitor = None
stream_watchdog = None
hubitat_power_service = None
unifi_poe_service = None
presence_service = None
restart_handler = None
app_state = None
socketio = None
amcrest_mjpeg_capture_service = None
reolink_mjpeg_capture_service = None
unifi_mjpeg_capture_service = None
sv3c_mjpeg_capture_service = None
mediaserver_mjpeg_service = None
websocket_mjpeg_service = None
camera_state_tracker = None


def set_services(**kwargs):
    """
    Bulk-set service references from app.py after initialization.

    Called once from app.py as::

        import routes.shared as shared
        shared.set_services(
            camera_repo=camera_repo,
            stream_manager=stream_manager,
            ...
        )

    Each keyword argument must match a name defined above.
    Raises AttributeError for unknown names to catch typos early.
    """
    g = globals()
    for name, value in kwargs.items():
        if name not in g:
            raise AttributeError(f"routes.shared: unknown service name '{name}'")
        g[name] = value
