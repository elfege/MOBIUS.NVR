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
from functools import wraps

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
    response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
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
