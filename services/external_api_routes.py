"""
External API Routes for Third-Party LAN Consumers
====================================================
Flask Blueprint providing API endpoints for local network services
(e.g., TILES smart home dashboard) to consume NVR camera data
without requiring Flask session auth.

Why a separate Blueprint?
    The main NVR UI uses Flask-Login session auth + CSRF. External
    consumers like TILES run on different origins (different IPs),
    so cross-origin cookies won't work. These endpoints use Bearer
    token auth (or LAN-only fallback in dev mode).

Security model (layered, following dDMSC pattern):
    1. Bearer token auth (primary): If NVR_API_TOKEN env var is set,
       ALL requests must include "Authorization: Bearer <token>".
       This is the production auth model.
    2. LAN-only fallback (dev mode): If NVR_API_TOKEN is NOT set,
       requests must originate from RFC 1918 private addresses
       (10.x.x.x, 172.16-31.x.x, 192.168.x.x) or localhost.
       A warning is logged on every request in this mode.

Endpoints:
    GET /api/external/docs
        → Dynamically generated API documentation (introspects routes)

    GET /api/external/cameras
        → List of available cameras with basic metadata

    GET /api/external/snap/<camera_id>
        → JPEG snapshot from existing frame buffers (never opens
          new camera connections)

    GET /api/external/stream/<camera_id>/hls
        → JSON with HLS playlist URL for embedding

Integration:
    In app.py, add three lines:
        from services.external_api_routes import external_api_bp, init_external_api
        app.register_blueprint(external_api_bp)
        init_external_api(camera_repo)

    NVR_API_TOKEN is read from os.environ at init time. To set it:
        - Add to .env file: NVR_API_TOKEN=<your-token>
        - Or set via AWS Secrets Manager (pull_nvr_secrets in start.sh)
        - Pass through docker-compose.yml environment section

Requested by: office-tiles (MSG-044, 2026-02-19)
Auth pattern: dDMSC (MSG-069/MSG-070, 2026-02-20)
"""

import io
import ipaddress
import logging
import os
import time
from functools import wraps

import requests as http_requests
from flask import Blueprint, Response, jsonify, request

logger = logging.getLogger(__name__)

external_api_bp = Blueprint('external_api', __name__)

# Module-level reference to camera_repo, set by init_external_api()
# This avoids circular imports (app.py imports us, we can't import app.py)
_camera_repo = None

# API authentication token — read from env at init time.
# When set, ALL external API requests must include "Authorization: Bearer <token>".
# When empty, falls back to LAN-only IP check (dev mode).
# Follows dDMSC pattern: see ~/dDMSC/api/app.py lines 51-128.
_api_token = os.environ.get('NVR_API_TOKEN', '')


def init_external_api(camera_repo):
    """
    Initialize the external API module with a reference to the
    camera repository. Must be called after Blueprint registration.

    Called from app.py:
        from services.external_api_routes import external_api_bp, init_external_api
        app.register_blueprint(external_api_bp)
        init_external_api(camera_repo)
    """
    global _camera_repo
    _camera_repo = camera_repo
    if _api_token:
        logger.info("External API: initialized with camera repository (Bearer token auth ENABLED)")
    else:
        logger.warning(
            "External API: initialized with camera repository — "
            "NVR_API_TOKEN not set, falling back to LAN-only auth (dev mode). "
            "Set NVR_API_TOKEN env var for production."
        )


# ---------------------------------------------------------------------------
# CORS — add Access-Control-Allow-Origin to ALL responses from this Blueprint
# ---------------------------------------------------------------------------
@external_api_bp.after_request
def _add_cors_headers(response):
    """
    Add CORS headers to every response from external API endpoints.
    TILES runs on a different origin (different IP/port),
    so cross-origin requests need explicit permission.
    Authorization header is included for Bearer token auth.
    """
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return response


# ---------------------------------------------------------------------------
# Authentication — Bearer token (primary) with LAN-only fallback (dev mode)
# ---------------------------------------------------------------------------
# Follows dDMSC pattern: ~/dDMSC/api/app.py lines 51-128
#
# Production (NVR_API_TOKEN set):
#   All requests must include "Authorization: Bearer <NVR_API_TOKEN>".
#   LAN check is NOT performed — token is the sole auth mechanism.
#   This allows future non-LAN access if needed.
#
# Dev mode (NVR_API_TOKEN empty):
#   Falls back to LAN-only IP check (RFC 1918 private addresses).
#   A warning is logged on every request — this mode is not for production.
# ---------------------------------------------------------------------------

# RFC 1918 private networks + loopback (used in dev/fallback mode only)
_ALLOWED_NETWORKS = [
    ipaddress.ip_network('10.0.0.0/8'),
    ipaddress.ip_network('172.16.0.0/12'),
    ipaddress.ip_network('192.168.0.0/16'),
    ipaddress.ip_network('127.0.0.0/8'),
    # IPv6 loopback and link-local
    ipaddress.ip_network('::1/128'),
    ipaddress.ip_network('fe80::/10'),
]


def _is_lan_request():
    """
    Check if the incoming request originates from a private/LAN address.

    Examines X-Forwarded-For (for reverse proxy setups like nginx) first,
    then falls back to request.remote_addr.

    Returns True if the source IP is within RFC 1918 ranges or loopback.
    """
    # X-Forwarded-For may contain a chain: "client, proxy1, proxy2"
    # The leftmost entry is the original client IP
    forwarded_for = request.headers.get('X-Forwarded-For', '')
    if forwarded_for:
        client_ip_str = forwarded_for.split(',')[0].strip()
    else:
        client_ip_str = request.remote_addr

    try:
        client_ip = ipaddress.ip_address(client_ip_str)
        return any(client_ip in network for network in _ALLOWED_NETWORKS)
    except (ValueError, TypeError):
        # If we can't parse the IP, deny access
        logger.warning(f"External API: could not parse client IP '{client_ip_str}' — denying")
        return False


def _check_bearer_token():
    """
    Validate the Bearer token from the Authorization header.

    Returns:
        True if the token is valid, False otherwise.
    """
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        provided_token = auth_header[7:]
        return provided_token == _api_token
    return False


def require_auth(f):
    """
    Decorator implementing layered auth for external API endpoints.

    When NVR_API_TOKEN is set (production):
        Requires valid "Authorization: Bearer <token>" header.
        Returns 401 Unauthorized if missing or invalid.

    When NVR_API_TOKEN is NOT set (dev mode):
        Falls back to LAN-only IP check (RFC 1918).
        Returns 403 Forbidden for non-LAN requests.
        Logs a warning on every request — set NVR_API_TOKEN for production.

    Replaces the previous @lan_only decorator.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # CORS preflight (OPTIONS) must pass without auth — browsers never
        # send Authorization headers on preflight requests.
        if request.method == 'OPTIONS':
            return f(*args, **kwargs)

        if _api_token:
            # Production mode: Bearer token required
            if _check_bearer_token():
                return f(*args, **kwargs)
            # Token configured but not provided or invalid
            logger.warning(
                f"External API: unauthorized request to {request.path} "
                f"from {request.remote_addr} — invalid or missing Bearer token"
            )
            return jsonify({'error': 'Unauthorized — Bearer token required'}), 401
        else:
            # Dev mode: LAN-only fallback (NVR_API_TOKEN not set)
            if _is_lan_request():
                return f(*args, **kwargs)
            logger.warning(
                f"External API: blocked non-LAN request from {request.remote_addr} "
                f"to {request.path} (no NVR_API_TOKEN set, LAN-only fallback active)"
            )
            return jsonify({'error': 'Forbidden — LAN access only (set NVR_API_TOKEN for token auth)'}), 403
    return decorated_function


# Keep lan_only as an alias for backward compatibility (other code may import it)
lan_only = require_auth


# ---------------------------------------------------------------------------
# Helper: frame buffer access
# ---------------------------------------------------------------------------
# MJPEG capture services are imported lazily inside handlers because they
# are singletons initialized in app.py after Blueprint registration.
# Importing at module level would access uninitialized objects.


def _get_latest_frame(camera_id, camera_type):
    """
    Retrieve the latest JPEG frame from existing capture service buffers.
    Same logic as /api/snap/<camera_id> in app.py, but extracted here
    to avoid duplicating the buffer-checking chain.

    IMPORTANT: This never opens new camera connections. It only reads
    from already-running capture services.

    Args:
        camera_id: Camera serial number
        camera_type: Camera type string ('reolink', 'unifi', 'sv3c', etc.)

    Returns:
        bytes (JPEG data) or None if no frame available
    """
    from services.reolink_mjpeg_capture_service import reolink_mjpeg_capture_service
    from services.mediaserver_mjpeg_service import mediaserver_mjpeg_service
    from services.unifi_mjpeg_capture_service import unifi_mjpeg_capture_service
    from services.sv3c_mjpeg_capture_service import sv3c_mjpeg_capture_service

    frame_data = None

    # Try camera-specific service first (matches app.py logic)
    if camera_type == 'reolink':
        frame_data = reolink_mjpeg_capture_service.get_latest_frame(camera_id)
    elif camera_type == 'unifi':
        frame_data = unifi_mjpeg_capture_service.get_latest_frame(camera_id)
    elif camera_type == 'sv3c':
        frame_data = sv3c_mjpeg_capture_service.get_latest_frame(camera_id)

    # Fallback to mediaserver (works for any camera with HLS running)
    if not frame_data:
        frame_data = mediaserver_mjpeg_service.get_latest_frame(camera_id)

    if frame_data and frame_data.get('data'):
        return frame_data['data']
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@external_api_bp.route('/api/external/docs')
@require_auth
def external_api_docs():
    """
    Dynamically generated API documentation.
    Introspects all routes registered on this Blueprint at runtime,
    extracting paths, methods, path parameters, and docstrings.
    If a new endpoint is added to the Blueprint, it appears here
    automatically — no manual doc maintenance needed.
    """
    from flask import current_app
    import re

    endpoints_list = []

    # Iterate over all URL rules registered on the Flask app
    # and filter to only those belonging to this Blueprint
    for rule in current_app.url_map.iter_rules():
        # Blueprint endpoints are prefixed with "external_api."
        if rule.endpoint and rule.endpoint.startswith('external_api.'):
            # Get the view function for this endpoint
            view_func = current_app.view_functions.get(rule.endpoint)
            if not view_func:
                continue

            # Extract HTTP methods (exclude HEAD and OPTIONS — Flask adds those)
            methods = sorted([m for m in rule.methods if m not in ('HEAD', 'OPTIONS')])

            # Extract path parameters from the URL rule
            # Flask rule format: /api/external/snap/<camera_id>
            path_params = []
            for param_name in rule.arguments:
                path_params.append({
                    'name': param_name,
                    'in': 'path',
                    'required': True,
                    'type': 'string'
                })

            # Parse the docstring for description and structured metadata
            # First paragraph = description, subsequent lines may contain
            # special markers like "Query params:" or "Response JSON:"
            raw_docstring = (view_func.__doc__ or '').strip()
            description, query_params, response_info = _parse_docstring(raw_docstring)

            endpoint_doc = {
                'path': rule.rule,
                'methods': methods,
                'function': rule.endpoint.replace('external_api.', ''),
                'description': description,
                'parameters': {
                    'path': path_params,
                    'query': query_params
                },
                'response': response_info
            }

            endpoints_list.append(endpoint_doc)

    # Sort endpoints alphabetically by path for consistent output
    endpoints_list.sort(key=lambda e: e['path'])

    docs = {
        'api': 'NVR External API',
        'version': '1.0',
        'generated_at': __import__('datetime').datetime.now().isoformat(),
        'base_url': request.host_url.rstrip('/'),
        'description': (
            'API endpoints for third-party consumers (e.g., TILES smart home '
            'dashboard). When NVR_API_TOKEN is set, Bearer token auth is required. '
            'When not set, falls back to LAN-only (RFC 1918 IP check).'
        ),
        'security': {
            'model': 'Bearer token (production) / LAN-only fallback (dev)',
            'auth': (
                'Bearer token required when NVR_API_TOKEN is set. '
                'Falls back to LAN-only (RFC 1918 IP check) when token is not configured.'
            ),
            'token_configured': bool(_api_token),
            'cors': 'Access-Control-Allow-Origin: * on all responses',
            'headers': 'Authorization: Bearer <NVR_API_TOKEN>'
        },
        'endpoint_count': len(endpoints_list),
        'endpoints': endpoints_list
    }

    return jsonify(docs)


def _parse_docstring(docstring):
    """
    Parse a route handler's docstring into structured documentation.

    Extracts:
        - description: first paragraph (up to first blank line)
        - query_params: lines under "Query params" section
        - response_info: lines under "Response JSON:" or content type hints

    Returns:
        (description, query_params_list, response_info_dict)
    """
    import re

    if not docstring:
        return ('No description available', [], {})

    lines = docstring.split('\n')
    # Strip common leading whitespace (dedent)
    stripped_lines = [line.strip() for line in lines]

    # First paragraph = description (up to first blank line)
    description_lines = []
    remaining_lines = []
    past_first_paragraph = False
    for line in stripped_lines:
        if not past_first_paragraph:
            if line == '':
                if description_lines:
                    past_first_paragraph = True
                continue
            description_lines.append(line)
        else:
            remaining_lines.append(line)

    description = ' '.join(description_lines) if description_lines else 'No description available'
    remaining_text = '\n'.join(remaining_lines)

    # Extract query parameters from "Query params" section
    # Pattern: "param_name — description" or "param_name: description"
    query_params = []
    query_section_match = re.search(
        r'Query params[^:]*:(.*?)(?=\n\s*\n|\n\s*[A-Z]|\Z)',
        remaining_text,
        re.DOTALL | re.IGNORECASE
    )
    if query_section_match:
        param_text = query_section_match.group(1)
        # Match lines like "width  — desired width in pixels"
        for match in re.finditer(r'(\w+)\s*[—\-:]+\s*(.+)', param_text):
            param_name = match.group(1).strip()
            param_desc = match.group(2).strip()
            # Infer type from description keywords
            param_type = 'integer' if any(w in param_desc.lower() for w in ['pixel', 'width', 'height', 'size', 'int']) else 'string'
            query_params.append({
                'name': param_name,
                'in': 'query',
                'required': False,
                'type': param_type,
                'description': param_desc
            })

    # Extract response info from "Response JSON:" or "Returns" section
    response_info = {}
    if 'image/jpeg' in remaining_text.lower() or 'jpeg' in description.lower():
        response_info['content_type'] = 'image/jpeg'
    elif 'Response JSON:' in remaining_text or 'json' in description.lower():
        response_info['content_type'] = 'application/json'
    else:
        response_info['content_type'] = 'application/json'

    # Extract JSON example if present (between { } after "Response JSON:")
    json_example_match = re.search(
        r'Response JSON:\s*(\{.*?\}|\[.*?\])',
        remaining_text,
        re.DOTALL
    )
    if json_example_match:
        response_info['example_shape'] = json_example_match.group(1).strip()

    # Extract error codes from "returns XXX" patterns
    error_codes = {}
    for match in re.finditer(r'(?:returns?|Returns?)\s+(\d{3})\s*(.*?)(?:\.|$)', remaining_text):
        error_codes[match.group(1)] = match.group(2).strip()
    if error_codes:
        response_info['error_codes'] = error_codes

    # Check for IMPORTANT or NOTE markers
    important_notes = []
    for match in re.finditer(r'(?:IMPORTANT|NOTE|CRITICAL):\s*(.+?)(?:\n|$)', remaining_text, re.IGNORECASE):
        important_notes.append(match.group(1).strip())
    if important_notes:
        response_info['notes'] = important_notes

    return (description, query_params, response_info)


@external_api_bp.route('/api/external/cameras')
@require_auth
def external_cameras_list():
    """
    List all visible cameras with basic metadata.

    Response JSON:
    [
        {
            "id": "48D8884ABC12",
            "name": "Backyard",
            "type": "reolink",
            "has_stream": true,
            "has_audio": true,
            "thumbnail_available": true
        },
        ...
    ]

    Used by TILES to populate a camera selection dropdown.
    Only returns cameras with streaming capability that are not hidden.
    """
    if _camera_repo is None:
        return jsonify({'error': 'Camera repository not initialized — init_external_api() not called'}), 503

    try:
        # get_streaming_cameras returns {serial: config} for cameras with
        # the 'streaming' capability, excluding hidden ones
        streaming_cameras = _camera_repo.get_streaming_cameras(include_hidden=False)

        cameras_list = []
        for serial, config in streaming_cameras.items():
            camera_name = config.get('name', serial)
            camera_type = config.get('type', 'unknown').lower()

            # Check if a frame buffer has data for this camera
            # (indicates the capture service is running and has frames)
            # Wrapped in try/except so one broken camera doesn't fail the whole list
            try:
                has_frame = _get_latest_frame(serial, camera_type) is not None
            except Exception:
                has_frame = False

            # Check audio config — cameras.json has audio.enabled per camera
            audio_config = config.get('audio', {})
            has_audio = audio_config.get('enabled', False) if isinstance(audio_config, dict) else False

            cameras_list.append({
                'id': serial,
                'name': camera_name,
                'type': camera_type,
                'has_stream': 'streaming' in config.get('capabilities', []),
                'has_audio': has_audio,
                'thumbnail_available': has_frame
            })

        return jsonify(cameras_list)

    except Exception as e:
        logger.error(f"External API: error listing cameras: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@external_api_bp.route('/api/external/snap/<camera_id>')
@require_auth
def external_snap(camera_id):
    """
    Get a JPEG snapshot from an existing frame buffer.

    Query params (optional):
        width  — desired width in pixels (downscale only)
        height — desired height in pixels (downscale only)

    If width/height are provided, the frame is downscaled using Pillow.
    If only one dimension is given, aspect ratio is preserved.
    If neither is given, the full-resolution buffer frame is returned.

    IMPORTANT: This endpoint NEVER opens new camera connections.
    It reads from already-running MJPEG capture service buffers.
    If no frame is available, returns 503.
    """
    if _camera_repo is None:
        return jsonify({'error': 'Camera repository not initialized — init_external_api() not called'}), 503

    camera = _camera_repo.get_camera(camera_id)
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404

    camera_type = camera.get('type', '').lower()

    try:
        frame_bytes = _get_latest_frame(camera_id, camera_type)
        if not frame_bytes:
            return jsonify({'error': 'No frame available — capture service may not be running'}), 503

        # Check if resize was requested
        requested_width = request.args.get('width', type=int)
        requested_height = request.args.get('height', type=int)

        if requested_width or requested_height:
            frame_bytes = _resize_jpeg(frame_bytes, requested_width, requested_height)

        return Response(
            frame_bytes,
            mimetype='image/jpeg',
            headers={
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Pragma': 'no-cache',
                'Expires': '0'
                # CORS handled by Blueprint after_request hook
            }
        )

    except Exception as e:
        logger.error(f"External API: snapshot error for {camera_id}: {e}")
        return jsonify({'error': f'Snapshot error: {e}'}), 500


@external_api_bp.route('/api/external/stream/<camera_id>/hls')
@require_auth
def external_stream_hls(camera_id):
    """
    Return the HLS playlist URL for a camera.

    Response JSON:
    {
        "camera_id": "48D8884ABC12",
        "url": "/hls/48D8884ABC12/index.m3u8",
        "type": "ll-hls"
    }

    The URL is relative to the NVR host. TILES should construct the
    full URL using the NVR's IP/hostname (e.g., https://192.168.10.200).

    This endpoint does NOT start a stream — it only returns the URL
    where the HLS playlist would be if the stream is already running
    via MediaMTX.
    """
    if _camera_repo is None:
        return jsonify({'error': 'Camera repository not initialized — init_external_api() not called'}), 503

    camera = _camera_repo.get_camera(camera_id)
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404

    # MediaMTX serves HLS at /hls/<path_name>/index.m3u8
    # The path_name matches the camera serial by convention
    hls_url = f"/hls/{camera_id}/index.m3u8"

    return jsonify({
        'camera_id': camera_id,
        'url': hls_url,
        'type': 'll-hls'
    })
    # CORS handled by Blueprint after_request hook


# ---------------------------------------------------------------------------
# Stream discovery — unified endpoint for third-party consumers
# ---------------------------------------------------------------------------

@external_api_bp.route('/api/external/stream/<camera_id>')
@require_auth
def external_stream_info(camera_id):
    """
    Discover the active stream protocol for a camera and get access URLs.

    Returns JSON describing which protocols are available and the URLs
    to consume them. Third-party apps use this to decide how to connect.

    Response JSON:
    {
        "camera_id": "T8416P0023352DA9",
        "name": "Backyard",
        "active_protocol": "webrtc",
        "streaming_hub": "mediamtx",
        "endpoints": {
            "mjpeg": "/api/external/stream/T8416P0023352DA9/mjpeg",
            "hls": "/api/external/stream/T8416P0023352DA9/hls",
            "whep": "/api/external/stream/T8416P0023352DA9/whep"
        }
    }

    The active_protocol field reflects the camera's configured stream type:
    - "webrtc" — use the whep endpoint (MediaMTX WHEP)
    - "go2rtc" — use the go2rtc endpoint (go2rtc WebRTC)
    - "ll_hls" / "hls" — use the hls endpoint
    - "mjpeg" — use the mjpeg endpoint

    MJPEG is always available as a fallback (taps existing frame buffers).
    """
    if _camera_repo is None:
        return jsonify({'error': 'Camera repository not initialized'}), 503

    camera = _camera_repo.get_camera(camera_id)
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404

    # Resolve effective stream type (no user context for external API — use camera default)
    stream_type = (camera.get('stream_type') or 'LL_HLS').upper()

    # Check streaming hub
    from services.streaming_hub import get_streaming_hub
    hub = get_streaming_hub(camera)

    # Determine active protocol based on hub + stream type
    if hub == 'go2rtc':
        active_protocol = 'go2rtc'
    elif stream_type in ('WEBRTC', 'WEBRTC_MEDIAMTX'):
        active_protocol = 'webrtc'
    elif stream_type == 'MJPEG':
        active_protocol = 'mjpeg'
    else:
        active_protocol = 'll_hls'

    # Build available endpoints
    base = f"/api/external/stream/{camera_id}"
    endpoints = {
        'mjpeg': f"{base}/mjpeg",
        'hls': f"{base}/hls",
    }

    # Add WebRTC endpoints based on hub
    if hub == 'go2rtc':
        endpoints['go2rtc'] = f"{base}/go2rtc"
    else:
        endpoints['whep'] = f"{base}/whep"

    return jsonify({
        'camera_id': camera_id,
        'name': camera.get('name', camera_id),
        'active_protocol': active_protocol,
        'streaming_hub': hub,
        'endpoints': endpoints
    })


# ---------------------------------------------------------------------------
# MJPEG stream — multipart/x-mixed-replace for third-party consumers
# ---------------------------------------------------------------------------

@external_api_bp.route('/api/external/stream/<camera_id>/mjpeg')
@require_auth
def external_stream_mjpeg(camera_id):
    """
    Live MJPEG stream (multipart/x-mixed-replace) for third-party consumers.

    Taps into existing NVR capture service frame buffers — never opens new
    camera connections. Works for any camera type that has an active capture
    service (Reolink, UniFi, SV3C, Amcrest, or MediaServer/MediaMTX tap).

    Query params (optional):
        fps — target frame rate, 1-10 (default: 2)

    Content-Type: multipart/x-mixed-replace; boundary=jpgboundary

    IMPORTANT: This is a long-lived streaming response. The connection
    stays open until the client disconnects.

    Returns 503 if no frames are available (camera not streaming).
    """
    if _camera_repo is None:
        return jsonify({'error': 'Camera repository not initialized'}), 503

    camera = _camera_repo.get_camera(camera_id)
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404

    camera_type = camera.get('type', '').lower()

    # Target FPS from query param, clamped to 1-10
    target_fps = request.args.get('fps', 2, type=int)
    target_fps = max(1, min(10, target_fps))
    frame_interval = 1.0 / target_fps

    def generate():
        """
        Generator that yields MJPEG frames from existing capture service buffers.
        Dispatches to the correct capture service based on camera type.
        Uses try/finally to guarantee cleanup on disconnect.
        """
        frame_count = 0
        no_frame_retries = 0
        max_no_frame_retries = 20  # 10 seconds max wait for first frame
        last_frame_time = 0

        try:
            while True:
                # Rate limit to target FPS
                now = time.monotonic()
                elapsed = now - last_frame_time
                if elapsed < frame_interval:
                    time.sleep(frame_interval - elapsed)

                frame_bytes = _get_latest_frame(camera_id, camera_type)

                if frame_bytes:
                    no_frame_retries = 0
                    frame_count += 1
                    last_frame_time = time.monotonic()

                    yield (b'--jpgboundary\r\n'
                           b'Content-Type: image/jpeg\r\n'
                           + f'Content-Length: {len(frame_bytes)}\r\n\r\n'.encode()
                           + frame_bytes + b'\r\n')
                else:
                    no_frame_retries += 1
                    if no_frame_retries >= max_no_frame_retries and frame_count == 0:
                        # No frames after 10 seconds — give up
                        logger.warning(
                            f"External API MJPEG {camera_id}: no frames after "
                            f"{max_no_frame_retries} attempts, ending stream"
                        )
                        return
                    time.sleep(0.5)

        except GeneratorExit:
            logger.info(
                f"External API MJPEG {camera_id}: client disconnected "
                f"after {frame_count} frames"
            )
        except Exception as e:
            logger.error(f"External API MJPEG {camera_id}: error: {e}")

    # Check if at least one frame is available before committing to the stream
    test_frame = _get_latest_frame(camera_id, camera_type)
    if not test_frame:
        # Try mediaserver fallback — it may have frames from MediaMTX tap
        from services.mediaserver_mjpeg_service import mediaserver_mjpeg_service
        ms_frame = mediaserver_mjpeg_service.get_latest_frame(camera_id)
        if not ms_frame or not ms_frame.get('data'):
            return jsonify({
                'error': 'No frames available — camera may not be streaming'
            }), 503

    response = Response(generate(), mimetype='multipart/x-mixed-replace; boundary=jpgboundary')
    response.headers['X-Accel-Buffering'] = 'no'
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response


# ---------------------------------------------------------------------------
# WebRTC WHEP proxy — relays SDP signaling to MediaMTX with Bearer auth
# ---------------------------------------------------------------------------

@external_api_bp.route('/api/external/stream/<camera_id>/whep', methods=['POST', 'OPTIONS'])
@require_auth
def external_stream_whep(camera_id):
    """
    WebRTC WHEP signaling proxy for third-party consumers.

    Proxies the WHEP (WebRTC-HTTP Egress Protocol) exchange to MediaMTX,
    adding Bearer token authentication. The client sends an SDP offer,
    this endpoint relays it to MediaMTX and returns the SDP answer.

    Request:
        POST with Content-Type: application/sdp
        Body: SDP offer string

    Response:
        Content-Type: application/sdp
        Body: SDP answer string

    After receiving the SDP answer, the client establishes a direct
    WebRTC media connection to MediaMTX (ICE candidates resolved in SDP).

    NOTE: WebRTC media flows directly between client and MediaMTX after
    signaling. LAN connectivity is required for the media plane unless
    a TURN server is configured.

    Query params (optional):
        stream — 'sub' (default) or 'main' for resolution selection

    Returns 404 if camera not found.
    Returns 502 if MediaMTX WHEP endpoint is unreachable.
    """
    if _camera_repo is None:
        return jsonify({'error': 'Camera repository not initialized'}), 503

    camera = _camera_repo.get_camera(camera_id)
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404

    # Handle CORS preflight
    if request.method == 'OPTIONS':
        resp = Response('', status=204)
        resp.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        return resp

    # Resolve MediaMTX path
    stream = request.args.get('stream', 'sub')
    stream_path = f"{camera_id}_main" if stream == 'main' else camera_id

    # MediaMTX WHEP endpoint (internal docker network)
    # MediaMTX listens on HTTPS 8889 when webrtcEncryption is enabled
    mediamtx_whep_url = f"https://nvr-packager:8889/{stream_path}/whep"

    try:
        # Relay the SDP offer to MediaMTX
        resp = http_requests.post(
            mediamtx_whep_url,
            data=request.get_data(),
            headers={'Content-Type': 'application/sdp'},
            verify=False,  # MediaMTX uses self-signed cert
            timeout=10
        )

        if resp.status_code != 201 and resp.status_code != 200:
            logger.warning(
                f"External API WHEP {camera_id}: MediaMTX returned {resp.status_code}: "
                f"{resp.text[:200]}"
            )
            return Response(
                resp.text,
                status=resp.status_code,
                content_type=resp.headers.get('Content-Type', 'text/plain')
            )

        # Return the SDP answer to the client
        answer_resp = Response(
            resp.content,
            status=resp.status_code,
            content_type=resp.headers.get('Content-Type', 'application/sdp')
        )
        # Forward Location header (WHEP resource URL for PATCH/DELETE)
        if 'Location' in resp.headers:
            answer_resp.headers['Location'] = resp.headers['Location']
        return answer_resp

    except http_requests.exceptions.ConnectionError:
        logger.error(f"External API WHEP {camera_id}: cannot reach MediaMTX at {mediamtx_whep_url}")
        return jsonify({'error': 'MediaMTX unreachable — stream may not be running'}), 502
    except http_requests.exceptions.Timeout:
        logger.error(f"External API WHEP {camera_id}: MediaMTX timeout")
        return jsonify({'error': 'MediaMTX timeout'}), 504
    except Exception as e:
        logger.error(f"External API WHEP {camera_id}: error: {e}")
        return jsonify({'error': f'WHEP proxy error: {e}'}), 500


# ---------------------------------------------------------------------------
# go2rtc WebRTC proxy — relays SDP signaling to go2rtc with Bearer auth
# ---------------------------------------------------------------------------

@external_api_bp.route('/api/external/stream/<camera_id>/go2rtc', methods=['POST', 'OPTIONS'])
@require_auth
def external_stream_go2rtc(camera_id):
    """
    WebRTC signaling proxy for go2rtc cameras.

    go2rtc serves WebRTC for cameras that use it as their streaming hub
    (typically Neolink/Baichuan cameras). This endpoint proxies the
    SDP offer/answer exchange, adding Bearer token authentication.

    Request:
        POST with Content-Type: application/json
        Body: {"type": "offer", "sdp": "v=0\\r\\n..."}

    Response:
        Content-Type: application/json
        Body: {"type": "answer", "sdp": "v=0\\r\\n..."}

    NOTE: go2rtc uses JSON format for SDP exchange, not raw SDP like WHEP.

    Returns 404 if camera not found.
    Returns 502 if go2rtc is unreachable.
    """
    if _camera_repo is None:
        return jsonify({'error': 'Camera repository not initialized'}), 503

    camera = _camera_repo.get_camera(camera_id)
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404

    # Verify this camera uses go2rtc
    from services.streaming_hub import is_go2rtc_camera
    if not is_go2rtc_camera(camera):
        return jsonify({
            'error': f'Camera {camera_id} does not use go2rtc — use /whep endpoint instead'
        }), 400

    # Handle CORS preflight
    if request.method == 'OPTIONS':
        resp = Response('', status=204)
        resp.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        return resp

    # go2rtc WebRTC API endpoint (internal docker network)
    go2rtc_url = f"http://nvr-go2rtc:1984/api/webrtc?src={camera_id}"

    try:
        # Relay the SDP offer to go2rtc
        resp = http_requests.post(
            go2rtc_url,
            json=request.get_json(),
            headers={'Content-Type': 'application/json'},
            timeout=10
        )

        if resp.status_code != 200:
            logger.warning(
                f"External API go2rtc {camera_id}: go2rtc returned {resp.status_code}: "
                f"{resp.text[:200]}"
            )
            return Response(
                resp.text,
                status=resp.status_code,
                content_type=resp.headers.get('Content-Type', 'text/plain')
            )

        # Return the SDP answer
        return Response(
            resp.content,
            status=200,
            content_type='application/json'
        )

    except http_requests.exceptions.ConnectionError:
        logger.error(f"External API go2rtc {camera_id}: cannot reach go2rtc at {go2rtc_url}")
        return jsonify({'error': 'go2rtc unreachable — service may not be running'}), 502
    except http_requests.exceptions.Timeout:
        logger.error(f"External API go2rtc {camera_id}: timeout")
        return jsonify({'error': 'go2rtc timeout'}), 504
    except Exception as e:
        logger.error(f"External API go2rtc {camera_id}: error: {e}")
        return jsonify({'error': f'go2rtc proxy error: {e}'}), 500


# ---------------------------------------------------------------------------
# Image resize helper
# ---------------------------------------------------------------------------

def _resize_jpeg(jpeg_bytes, target_width=None, target_height=None):
    """
    Downscale a JPEG image while preserving aspect ratio.
    Uses Pillow (PIL) which is already a dependency for the NVR project.

    Only downscales — if the requested dimensions are larger than the
    original, the original is returned unchanged.

    Args:
        jpeg_bytes: Raw JPEG bytes
        target_width: Desired width (optional)
        target_height: Desired height (optional)

    Returns:
        Resized JPEG bytes, or original bytes if no resize needed
    """
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(jpeg_bytes))
        original_width, original_height = img.size

        # Calculate target dimensions preserving aspect ratio
        if target_width and target_height:
            # Both specified — use as-is (may distort aspect ratio)
            new_width, new_height = target_width, target_height
        elif target_width:
            # Width specified, calculate height from aspect ratio
            aspect_ratio = original_height / original_width
            new_width = target_width
            new_height = int(target_width * aspect_ratio)
        elif target_height:
            # Height specified, calculate width from aspect ratio
            aspect_ratio = original_width / original_height
            new_width = int(target_height * aspect_ratio)
            new_height = target_height
        else:
            # Neither specified — return original
            return jpeg_bytes

        # Only downscale, never upscale
        if new_width >= original_width and new_height >= original_height:
            return jpeg_bytes

        # Resize using LANCZOS (high quality downscale)
        img_resized = img.resize((new_width, new_height), Image.LANCZOS)

        # Encode back to JPEG
        output_buffer = io.BytesIO()
        img_resized.save(output_buffer, format='JPEG', quality=85)
        return output_buffer.getvalue()

    except ImportError:
        # Pillow not installed — return original frame
        logger.warning("External API: Pillow not installed, returning full-resolution frame")
        return jpeg_bytes
    except Exception as e:
        logger.warning(f"External API: resize failed ({e}), returning full-resolution frame")
        return jpeg_bytes
