"""
routes/config.py — Flask Blueprint for core configuration and status routes.

Covers:
- Main UI routes: GET /, GET /streams, GET /reloading
- Health / license / trusted-network settings endpoints
- System status and camera list (/api/cameras GET all) endpoints

Camera detail, force-sync, data-source, and MediaMTX path routes live in
routes/camera.py to avoid duplicate endpoint registration.

All service singletons are accessed via routes.shared to avoid circular imports.
"""

import os
import logging

import requests
from flask import Blueprint, render_template, jsonify, redirect, request
from flask_login import login_required, current_user

import routes.shared as shared
from routes.helpers import (
    csrf_exempt,
    _get_allowed_camera_serials,
    _filter_cameras,
    _ui_health_from_env,
    _is_trusted_network_enabled,
    _get_client_ip,
    _is_same_subnet,
    _trusted_network_cache,
)
from services.db import cursor as db_cursor
from services.license_service import license, validate_license

logger = logging.getLogger(__name__)

config_bp = Blueprint('config', __name__)


def _resolve_fullscreen_request(cameras: dict) -> str | None:
    """
    Read ?fullscreen=<value> from the current request. The value may be
    a camera nickname (cameras.nickname) or a raw serial number. Returns
    the canonical serial if it resolves to a camera the caller has access
    to (i.e. present in the filtered ``cameras`` dict), else None.

    Unknown values silently return None — the page renders normally and
    no fullscreen is triggered. We never error here so a stale bookmark
    or typo doesn't break the page load.
    """
    raw = (request.args.get('fullscreen') or '').strip()
    if not raw:
        return None

    # Fast path: exact serial match.
    if raw in cameras:
        return raw

    # Nickname match — case-insensitive, normalized to lowercase to
    # match the DB CHECK constraint. Scan the dict; with O(20) cameras
    # this is cheaper than a separate DB round-trip.
    needle = raw.lower()
    for serial, info in cameras.items():
        nick = (info.get('nickname') or '').lower()
        if nick and nick == needle:
            return serial
    return None


# ===== Main UI Routes =====

@config_bp.route('/')
@login_required
def index():
    """Redirect to streams page (main interface)"""
    return redirect('/streams')


@config_bp.route('/streams')
@login_required
def streams_page():
    """Multi-stream viewing page — redirects mobile/tablet to /light automatically."""
    # Auto-redirect low-powered devices to /light unless user explicitly opts out
    # via ?full=1 query param (the "Full UI" link in /light uses this)
    if not request.args.get('full'):
        ua = (request.headers.get('User-Agent') or '').lower()
        # Fire tablets (Silk), generic Android tablets/phones, iOS devices
        mobile_indicators = ['silk/', 'android', 'iphone', 'ipad', 'mobile', 'fire']
        if any(token in ua for token in mobile_indicators):
            return redirect('/light')
    try:
        cameras = shared.camera_repo.get_streaming_cameras(include_hidden=True)
        ui_health = _ui_health_from_env()

        # Filter cameras based on user's access permissions
        allowed = _get_allowed_camera_serials(current_user)
        cameras = _filter_cameras(cameras, allowed)

        # Apply user's saved tile display order from DB
        order_map = {}
        try:
            with db_cursor() as cur:
                cur.execute(
                    "SELECT camera_serial, display_order "
                    "FROM user_camera_preferences "
                    "WHERE user_id = %s AND display_order IS NOT NULL",
                    (current_user.id,)
                )
                order_map = {serial: pos for serial, pos in cur.fetchall()}
        except Exception as e:
            logger.warning(f"[streams_page] Could not load display order: {e}")

        # Sort by saved display_order if available, otherwise alphabetical by name
        # so tile positions are always stable across restarts.
        cameras = dict(sorted(
            cameras.items(),
            key=lambda item: (
                order_map.get(item[0], float('inf')),
                (item[1].get('name') or '').lower()
            )
        ))

        fullscreen_serial = _resolve_fullscreen_request(cameras)

        # Eufy Bridge tab visibility flag: the tab only makes sense when the
        # bridge is configured (shared.eufy_bridge is not None) AND at least
        # one camera is type 'eufy'. Surfaced to the client as
        # window.EUFY_BRIDGE_AVAILABLE so the settings modal can gate the tab.
        eufy_bridge_available = bool(
            shared.eufy_bridge
            and any((c.get('type') or '').lower() == 'eufy' for c in cameras.values())
        )

        # Evidence collection feature flag — controls visibility of the
        # "Collect Evidence" tab in the global settings modal. Default OFF
        # (the global master switch shipped in commit b1ec4cee). When off
        # we hide the tab entirely to reduce admin-UI clutter; flipping
        # the master switch back on will surface the tab again on next
        # page load.
        try:
            from services.evidence.gate import evidence_collection_enabled
            evidence_enabled = evidence_collection_enabled(default=False)
        except Exception:
            evidence_enabled = False

        # Pass full camera configs (includes ui_health_monitor per camera)
        return render_template(
            'streams.html',
            cameras=cameras,
            ui_health=ui_health,
            fullscreen_serial=fullscreen_serial,
            eufy_bridge_available=eufy_bridge_available,
            evidence_enabled=evidence_enabled,
        )
    except Exception as e:
        print(f"Error loading streams page: {e}")
        return f"Error loading streams page: {e}", 500


@config_bp.route('/light')
@login_required
def streams_light_page():
    """
    Lightweight stream viewer for low-powered devices (Fire tablets, etc.).
    Snapshot-only, 4 cameras per page, no heavy JS/CSS, no health monitor.
    """
    try:
        cameras = shared.camera_repo.get_streaming_cameras(include_hidden=True)

        # Filter cameras based on user's access permissions
        allowed = _get_allowed_camera_serials(current_user)
        cameras = _filter_cameras(cameras, allowed)

        # Remove server-hidden cameras entirely (light mode = no admin debug)
        cameras = {s: c for s, c in cameras.items() if not c.get('hidden', False)}

        # Apply user's saved tile display order from DB
        order_map = {}
        try:
            with db_cursor() as cur:
                cur.execute(
                    "SELECT camera_serial, display_order "
                    "FROM user_camera_preferences "
                    "WHERE user_id = %s AND display_order IS NOT NULL",
                    (current_user.id,)
                )
                order_map = {serial: pos for serial, pos in cur.fetchall()}
        except Exception as e:
            logger.warning(f"[streams_light_page] Could not load display order: {e}")

        cameras = dict(sorted(
            cameras.items(),
            key=lambda item: (
                order_map.get(item[0], float('inf')),
                (item[1].get('name') or '').lower()
            )
        ))

        # Pass an ordered list, not a dict. Flask's tojson filter inherits
        # `DefaultJSONProvider.sort_keys=True`, which alphabetizes dict keys
        # at serialization time — that wipes out the display_order sort we
        # just applied. JSON arrays preserve element order regardless of
        # sort_keys, so a list of {serial, ...info} dicts survives intact.
        # See light-mode-app.js _readCamerasFromDom() for the consumer.
        cameras_ordered = [
            {"serial": serial, **info} for serial, info in cameras.items()
        ]
        fullscreen_serial = _resolve_fullscreen_request(cameras)
        return render_template(
            'streams_light.html',
            cameras=cameras_ordered,
            fullscreen_serial=fullscreen_serial,
        )
    except Exception as e:
        logger.error(f"Error loading light streams page: {e}")
        return f"Error loading light streams page: {e}", 500


@config_bp.route('/reloading')
@login_required
def reloading_page():
    """Reconnection page shown when server is restarting"""
    return render_template('reloading.html')


# ===== Status Routes =====

@config_bp.route('/api/health')
def api_health():
    """
    Lightweight health check endpoint
    Returns shutdown status to warn clients before server goes down
    """
    if shared.app_state.is_shutting_down:
        return jsonify({
            'status': 'shutting_down',
            'message': 'Server is shutting down'
        }), 503

    return jsonify({
        'status': 'ok',
        'message': 'Server is healthy'
    }), 200


@config_bp.route('/api/license')
@login_required
def api_license_status():
    """
    Returns the current license state for the frontend.
    Used by the UI to show demo banners, camera limits, and watermark status.
    """
    return jsonify(license.to_dict()), 200


@config_bp.route('/api/settings/trusted-network', methods=['GET'])
@login_required
def api_trusted_network_get():
    """Get the current trusted network setting."""
    return jsonify({
        'enabled': _is_trusted_network_enabled(),
        'client_ip': _get_client_ip(),
        'on_same_subnet': _is_same_subnet(_get_client_ip())
    }), 200


@config_bp.route('/api/settings/trusted-network', methods=['PUT'])
@csrf_exempt
@login_required
def api_trusted_network_put():
    """Enable or disable trusted network auto-login. Admin only."""
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    data = request.get_json()
    enabled = str(data.get('enabled', False)).lower()

    success = shared.settings.set_global('TRUSTED_NETWORK_ENABLED', enabled)
    if success:
        _trusted_network_cache['enabled'] = enabled == 'true'
        _trusted_network_cache['checked_at'] = 0
        return jsonify({'success': True, 'enabled': enabled == 'true'}), 200
    return jsonify({'error': 'Failed to update trusted network setting'}), 500


@config_bp.route('/api/settings/streaming-hubs', methods=['GET'])
@login_required
def api_streaming_hubs_get():
    """Get per-camera streaming hub assignments — always fresh from DB."""
    cameras = shared.camera_repo.get_streaming_cameras(include_hidden=True)
    mediamtx_list = []
    go2rtc_list = []
    for serial, cam in cameras.items():
        entry = {'serial': serial, 'name': cam.get('name', serial)}
        hub = (cam.get('streaming_hub') or 'mediamtx').lower()
        if hub == 'go2rtc':
            go2rtc_list.append(entry)
        else:
            mediamtx_list.append(entry)
    mediamtx_list.sort(key=lambda c: c['name'].lower())
    go2rtc_list.sort(key=lambda c: c['name'].lower())
    return jsonify({'mediamtx': mediamtx_list, 'go2rtc': go2rtc_list}), 200


@config_bp.route('/api/settings/streaming-hubs', methods=['PUT'])
@csrf_exempt
@login_required
def api_streaming_hubs_put():
    """
    Bulk-update streaming hub for multiple cameras.

    Request body: {"cameras": {"SERIAL1": "go2rtc", "SERIAL2": "mediamtx", ...}}
    Each value must be one of the canonical hubs in VALID_STREAMING_HUBS
    (currently: 'mediamtx', 'go2rtc', 'neolink', 'native_mjpeg').

    Pre-2026-06-20 this endpoint hard-coded the allow-list to
    ('go2rtc', 'mediamtx') and silently rejected 'native_mjpeg' — which
    meant operators couldn't switch broken-RTSP cameras (SV3C hi3510) to
    snap-only mode from the General-tab bulk toggle, only from the
    per-camera settings modal. Aligned with VALID_STREAMING_HUBS now.
    """
    from services.streaming_hub import VALID_STREAMING_HUBS

    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    data = request.get_json() or {}
    assignments = data.get('cameras', {})
    if not assignments or not isinstance(assignments, dict):
        return jsonify({'error': 'Body must include "cameras": {"serial": "hub", ...}'}), 400

    updated = []
    errors = []
    for serial, hub in assignments.items():
        if hub not in VALID_STREAMING_HUBS:
            errors.append(f"{serial}: invalid hub '{hub}' (allowed: {', '.join(sorted(VALID_STREAMING_HUBS))})")
            continue
        success = shared.camera_repo.update_camera_setting(serial, 'streaming_hub', hub)
        if success:
            updated.append(serial)
        else:
            errors.append(f"{serial}: update failed")

    logger.info(f"[StreamingHubs] Bulk update by user {current_user.id}: {len(updated)} updated, {len(errors)} errors")
    return jsonify({'updated': updated, 'errors': errors}), 200


@config_bp.route('/api/settings/advanced', methods=['GET'])
@login_required
def api_advanced_settings_get():
    """
    Return all nvr_settings rows (excluding secrets) as a JSON array.
    Used by the Advanced tab in the global settings modal.
    Returns: [ { key, value, updated_at }, ... ] ordered by key.
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    # Keys that must never be exposed via this endpoint
    SECRET_KEYS = {'NVR_SECRET_KEY'}

    try:
        with db_cursor() as cur:
            cur.execute("SELECT key, value, updated_at FROM nvr_settings ORDER BY key;")
            rows = cur.fetchall()

        result = [
            {'key': r[0], 'value': r[1], 'updated_at': r[2].isoformat() if r[2] else None}
            for r in rows if r[0] not in SECRET_KEYS
        ]
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"[AdvancedSettings] GET failed: {e}")
        return jsonify({'error': str(e)}), 500


@config_bp.route('/api/settings/advanced/<path:key>', methods=['PATCH'])
@csrf_exempt
@login_required
def api_advanced_settings_patch(key):
    """
    Update a single nvr_settings row by key. Admin only.
    Body: { "value": "<new value>" }
    Rejects attempts to modify secret keys via this endpoint.
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    SECRET_KEYS = {'NVR_SECRET_KEY'}
    if key in SECRET_KEYS:
        return jsonify({'error': 'Cannot modify this key via this endpoint'}), 403

    data = request.get_json() or {}
    value = data.get('value', '')

    try:
        # Pool-backed transactional batch — `with conn:` commits on clean
        # exit so SET LOCAL audit.* applied by apply_audit_actor is in
        # scope for the upsert. The nvr_settings audit trigger relies on
        # the audit.* GUCs to stamp WHO.
        from services.audit_actor import apply_audit_actor
        with db_cursor() as cur:
            apply_audit_actor(cur)
            cur.execute(
                """
                INSERT INTO nvr_settings (key, value, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW();
                """,
                (key, value)
            )

        logger.info(f"[AdvancedSettings] '{key}' set by user {current_user.id}")
        return jsonify({'success': True, 'key': key, 'value': value}), 200
    except Exception as e:
        logger.error(f"[AdvancedSettings] PATCH failed for '{key}': {e}")
        return jsonify({'error': str(e)}), 500


@config_bp.route('/api/status')
@login_required
def api_status():
    """Get system status"""
    eufy_bridge = shared.eufy_bridge
    unifi_cameras = shared.unifi_cameras

    eufy_status = {
        'bridge_configured': eufy_bridge is not None,
        'bridge_running': eufy_bridge.is_running() if eufy_bridge else False,
        'bridge_ready': eufy_bridge.is_ready() if eufy_bridge else False,
        'total_devices': shared.camera_repo.get_camera_count(),
        'ptz_cameras': len(shared.camera_repo.get_ptz_cameras())
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
            'active': shared.stream_manager.get_active_streams(),
            'total_streaming_cameras': len(shared.camera_repo.get_streaming_cameras())
        }
    })


@config_bp.route('/api/cameras')
@login_required
def api_cameras():
    """Get list of available cameras, filtered by user access permissions"""
    allowed = _get_allowed_camera_serials(current_user)
    return jsonify({
        'all': _filter_cameras(shared.camera_repo.get_all_cameras(), allowed),
        'ptz': _filter_cameras(shared.camera_repo.get_ptz_cameras(), allowed),
        'streaming': _filter_cameras(shared.camera_repo.get_streaming_cameras(), allowed)
    })


@config_bp.route('/api/cameras/scan-lan', methods=['POST'])
@login_required
def api_cameras_scan_lan():
    """Basic LAN camera discovery (admin only) — listing/detection, no add.

    Self-configuring: the cameras already known to the NVR live on the LAN, so
    we derive the /24 subnet(s) from their host IPs and TCP-probe every host on
    those subnets for camera-defining ports (RTSP 554/8554, ONVIF 8000; HTTP 80
    is noted as supplementary but is NOT sufficient on its own — too noisy, every
    router/printer answers on 80). Discovery only: nothing is written to the DB.

    Why derive the subnet from camera IPs instead of the host's own interface:
    this service runs in a container whose own address is a Docker-bridge IP
    (172.x), not the camera LAN. The known cameras' IPs are the reliable anchor
    for "which network are the cameras on".

    Returns JSON: {
        subnet:  "<LAN_IP>/24" | null,
        devices: [{ ip, ports: [..], http: bool, already_known: bool }],
        scanned: <hosts probed>
    }
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    import ipaddress
    import socket
    from concurrent.futures import ThreadPoolExecutor

    CAMERA_PORTS = (554, 8554, 8000)   # RTSP, RTSP-alt, ONVIF — camera-defining
    HTTP_PORT = 80                      # supplementary signal only
    CONNECT_TIMEOUT = 0.4              # seconds per TCP connect attempt
    MAX_HOSTS = 1024                   # safety ceiling across all derived subnets

    # Derive the subnets to scan + the set of IPs already configured.
    known_ips = set()
    subnets = set()
    # get_all_cameras() returns Dict[serial -> config]; iterate the configs.
    for cam in shared.camera_repo.get_all_cameras().values():
        host = str(cam.get('host') or '').strip()
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            continue  # hostname (not an IP) — can't derive a /24 from it
        known_ips.add(str(ip))
        subnets.add(ipaddress.ip_network(f"{ip}/24", strict=False))

    if not subnets:
        return jsonify({
            'subnet': None, 'devices': [], 'scanned': 0,
            'note': 'No camera IP addresses are known, so no subnet could be derived to scan.'
        })

    # Bounded target list across all derived subnets.
    targets = []
    for net in sorted(subnets, key=str):
        for ip in net.hosts():
            targets.append(str(ip))
            if len(targets) >= MAX_HOSTS:
                break
        if len(targets) >= MAX_HOSTS:
            break

    def _port_open(ip, port):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(CONNECT_TIMEOUT)
        try:
            return s.connect_ex((ip, port)) == 0
        except OSError:
            return False
        finally:
            s.close()

    def _probe(ip):
        open_cam = [p for p in CAMERA_PORTS if _port_open(ip, p)]
        if not open_cam:
            return None  # not camera-like; HTTP-only hosts are skipped (noise)
        return {
            'ip': ip,
            'ports': open_cam,
            'http': _port_open(ip, HTTP_PORT),
            'already_known': ip in known_ips,
        }

    devices = []
    with ThreadPoolExecutor(max_workers=64) as pool:
        for result in pool.map(_probe, targets):
            if result:
                devices.append(result)

    # New devices first, then ascending numeric IP.
    devices.sort(key=lambda d: (d['already_known'], tuple(int(o) for o in d['ip'].split('.'))))
    subnet_label = ', '.join(str(s) for s in sorted(subnets, key=str))
    logger.info(f"LAN camera scan: {len(devices)} camera-like device(s) on {subnet_label} "
                f"({len(targets)} hosts probed)")
    return jsonify({
        'subnet': subnet_label,
        'devices': devices,
        'scanned': len(targets),
    })


# NOTE: /api/cameras/<camera_id>, /api/cameras/force-sync, /api/cameras/data-source,
# /api/mediamtx/path-status, and /api/mediamtx/create-path are registered in
# routes/camera.py to avoid duplicate endpoint errors.

