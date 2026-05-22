"""
routes/camera.py — Flask Blueprint for camera management routes.

Covers:
- Per-camera display settings (video_fit_mode)
- User camera order preferences
- Stream type preferences (per-user, per-camera)
- MediaMTX path status and dynamic path creation
- Camera list, camera detail, rename
- Streaming configuration (WebRTC/ICE settings)
- Bridge control (Eufy bridge start/stop)
- Device refresh and force-sync
- Camera data source status
- FLV stream serving (RTMP)
- Stream start, stop, restart, status
- Camera state (single and batch)
- Camera reboot (Reolink Baichuan, ONVIF)
- Per-camera credentials (GET/PUT/DELETE)
- Service-level (brand) credentials (GET)
- Speaker volume for two-way audio

All service singletons are accessed via routes.shared to avoid circular imports.
"""

import os
import logging
import re
import traceback
import time
from threading import Thread

import psycopg2
import requests
from flask import Blueprint, jsonify, request, Response, session
from flask_login import login_required, current_user

import routes.shared as shared
from routes.helpers import csrf_exempt
from services.camera_state_tracker import camera_state_tracker

logger = logging.getLogger(__name__)

camera_bp = Blueprint('camera', __name__)


# ---------------------------------------------------------------------------
# Valid stream types matching the CHECK constraint in user_camera_preferences
# ---------------------------------------------------------------------------

VALID_STREAM_TYPES = {'MJPEG', 'HLS', 'LL_HLS', 'WEBRTC', 'NEOLINK', 'NEOLINK_LL_HLS', 'GO2RTC'}


# ===== Per-Camera Display Settings =====

@camera_bp.route('/api/camera/<camera_serial>/display', methods=['GET'])
@csrf_exempt
@login_required
def api_get_camera_display(camera_serial):
    """
    Get per-camera display settings (currently: video_fit_mode).
    Returns {'video_fit_mode': 'cover'|'fill'|None} — None means use user default.
    """
    try:
        response = shared._postgrest_session.get(
            f"{shared.POSTGREST_URL}/cameras",
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


@camera_bp.route('/api/camera/<camera_serial>/display', methods=['PUT'])
@csrf_exempt
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
    if fit is not None and fit not in ('cover', 'contain', 'fill'):
        return jsonify({'error': 'video_fit_mode must be "cover", "contain", "fill", or null'}), 400

    try:
        response = shared._postgrest_session.patch(
            f"{shared.POSTGREST_URL}/cameras",
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


# ===== Camera Order Preferences =====

@camera_bp.route('/api/my-camera-order', methods=['PUT'])
@csrf_exempt
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
        # Direct psycopg2 upsert — handles the NOT NULL preferred_stream_type
        # constraint by pulling the camera's default stream_type for new rows.
        # ON CONFLICT preserves existing preferred_stream_type, only updates display_order.
        conn = psycopg2.connect(
            host=os.getenv('POSTGRES_HOST', 'postgres'),
            port=os.getenv('POSTGRES_PORT', '5432'),
            dbname=os.getenv('POSTGRES_DB', 'nvr'),
            user=os.getenv('POSTGRES_USER', 'nvr_api'),
            password=os.getenv('POSTGRES_PASSWORD', 'nvr_internal_db_key'),
            connect_timeout=5
        )
        # Transactional batch (no autocommit) so SET LOCAL audit.* applied
        # by apply_audit_actor is visible to every upsert in the loop. The
        # audit trigger on user_camera_preferences relies on those GUCs.
        cur = conn.cursor()
        from services.audit_actor import apply_audit_actor
        apply_audit_actor(cur)
        for idx, serial in enumerate(order):
            cur.execute("""
                INSERT INTO user_camera_preferences
                    (user_id, camera_serial, preferred_stream_type, display_order)
                SELECT %s, %s, stream_type, %s
                FROM cameras WHERE serial = %s
                ON CONFLICT (user_id, camera_serial)
                DO UPDATE SET display_order = EXCLUDED.display_order,
                              updated_at = NOW()
            """, (current_user.id, serial, idx, serial))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'status': 'saved', 'count': len(order)})
    except Exception as e:
        logger.error(f"Error saving camera order: {e}")
        return jsonify({'error': str(e)}), 500


# ===== Stream Type Preferences =====

@camera_bp.route('/api/user/stream-preferences', methods=['GET'])
@csrf_exempt
@login_required
def api_get_stream_preferences():
    """
    Get current user's per-camera stream type preferences.
    Returns list of {camera_serial, preferred_stream_type} for all cameras
    where the user has set a preference.
    """
    try:
        response = shared._postgrest_session.get(
            f"{shared.POSTGREST_URL}/user_camera_preferences",
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


@camera_bp.route('/api/user/stream-preferences/<camera_serial>', methods=['PUT'])
@csrf_exempt
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

    # Use unified Settings class — handles upsert + 409 fallback
    if shared.settings:
        success = shared.settings.set_user_preference(
            current_user.id, camera_serial,
            'preferred_stream_type', stream_type
        )
        if success:
            return jsonify({'status': 'saved'})
        return jsonify({'error': 'Failed to save stream preference'}), 500

    return jsonify({'error': 'Settings service not initialized'}), 500


# ===== MediaMTX Routes =====

@camera_bp.route('/api/mediamtx/path-status/<camera_serial>', methods=['GET'])
@csrf_exempt
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
        # Resolve streaming hub and path from camera config
        from routes import shared
        from services.streaming_hub import is_go2rtc_camera
        camera_config = shared.camera_repo.get_camera(camera_serial) or {}
        path_name = camera_config.get('packager_path') or camera_serial

        # go2rtc cameras don't use MediaMTX — check go2rtc API instead
        if is_go2rtc_camera(camera_config):
            try:
                resp = requests.get(
                    'http://nvr-go2rtc:1984/api/streams',
                    timeout=3
                )
                if resp.status_code == 200:
                    streams = resp.json()
                    stream_info = streams.get(camera_serial, {})
                    producers = stream_info.get('producers') or []
                    is_ready = len(producers) > 0
                    return jsonify({
                        'ready': is_ready,
                        'path': camera_serial,
                        'streaming_hub': 'go2rtc',
                        'message': 'go2rtc stream active' if is_ready else 'go2rtc stream has no active producer'
                    })
            except requests.RequestException:
                pass
            return jsonify({
                'ready': False,
                'path': camera_serial,
                'streaming_hub': 'go2rtc',
                'message': 'go2rtc API unavailable'
            })

        # MediaMTX cameras: query MediaMTX API for path list
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


@camera_bp.route('/api/mediamtx/create-path/<camera_serial>', methods=['POST'])
@csrf_exempt
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
        camera = shared.camera_repo.get_camera(camera_serial)
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
                elif resp.status_code in (400, 409):
                    # Path already exists — MediaMTX returns 400 with
                    # "path already exists" or 409 depending on version
                    resp_body = resp.text.lower()
                    if 'already exists' in resp_body or resp.status_code == 409:
                        created.append(p)
                        logger.info(f"[MEDIAMTX] Path {p} already exists")
                    else:
                        logger.warning(f"[MEDIAMTX] Failed to create path {p}: "
                                       f"{resp.status_code} {resp.text}")
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
        stream_url = shared.stream_manager.start_stream(
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


# ===== Camera List and Detail =====

@camera_bp.route('/api/cameras/<camera_id>')
@login_required
def api_camera_detail(camera_id):
    """Get single camera configuration — always fresh from DB."""
    try:
        camera = shared.camera_repo.get_camera(camera_id)

        if not camera:
            return jsonify({'error': 'Camera not found'}), 404

        return jsonify(camera)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ===== Camera Rename =====

@camera_bp.route('/api/camera/<camera_serial>/name', methods=['PUT'])
@csrf_exempt
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
        camera = shared.camera_repo.get_camera(camera_serial)
        if not camera:
            return jsonify({'error': f'Camera not found: {camera_serial}'}), 404

        old_name = camera.get('name', camera_serial)

        # Update via CameraRepository (handles DB + JSON + in-memory cache)
        success = shared.camera_repo.update_camera_setting(camera_serial, 'name', new_name)

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


# ===== Camera Nickname =====
#
# Optional short handle for URL-based addressing:
#   GET /streams?fullscreen=lobby   (resolves nickname -> serial server-side)
#   GET /light?fullscreen=lobby
#
# Validation lives both here (clear error messages) and in the DB
# (migration 034 CHECK constraints). Keeping them in sync is intentional
# — the DB is the final guard, this is the friendly one.

_NICKNAME_REGEX = re.compile(r'^[a-z]+[0-9]?$')

# Same blacklist as cameras_nickname_not_brand_chk in migration 034.
# Keep in sync if either side changes.
_NICKNAME_BRAND_BLACKLIST = frozenset({
    'reolink', 'eufy', 'amcrest', 'sv3c', 'unifi',
    'hikvision', 'dahua', 'axis', 'foscam', 'wyze',
    'neolink', 'mediamtx', 'go2rtc', 'baichuan',
})


def _autogen_bases_from_name(name: str) -> list:
    """
    Derive candidate nickname bases from the display name, in priority
    order (per operator request 2026-05-11):

        1. Last word              (split on '_' or ' ', lowercase, strip non-alpha)
        2. Penultimate word       — only if it isn't a brand/vendor name

    The penultimate fallback addresses collisions where two cameras
    share a last word but differ in the word before:

        LAUNDRY ROOM   -> last='room',  penult='laundry'  -> ['room', 'laundry']
        Living Room    -> last='room',  penult='living'   -> ['room', 'living']
        AMCREST LOBBY  -> last='lobby', penult='amcrest' (BRAND -> dropped)
                       -> ['lobby']
        Former CAM STAIRS -> ['stairs', 'cam']
        STAIRS         -> ['stairs']                       (only one word)
        Reolink 1      -> last='1' (digits) -> stripped to '' -> []
    """
    if not name:
        return []
    parts = [p for p in re.split(r'[_ ]+', name.strip()) if p]
    bases = []
    if parts:
        last = re.sub(r'[^a-z]', '', parts[-1].lower())
        if last:
            bases.append(last)
    if len(parts) >= 2:
        penult = re.sub(r'[^a-z]', '', parts[-2].lower())
        if penult and penult not in _NICKNAME_BRAND_BLACKLIST:
            bases.append(penult)
    return bases


def _resolve_free_nickname(bases, taken: set, digit_base: str | None = None) -> str | None:
    """
    Given a priority-ordered list of base candidates, find the first
    free nickname:

        1. Try every bare base in order (last word, then penultimate
           if not a brand).
        2. Fall back to digit-suffixing — using `digit_base` if the
           caller supplied one (so a contested primary that was
           stripped from `bases` can still receive its <primary>N
           cascade), else `bases[0]`.

    Returns None if every option is exhausted.
    """
    # Accept either a single string (legacy callers) or a list.
    if isinstance(bases, str):
        bases = [bases]
    bases = [b for b in (bases or []) if b and b not in _NICKNAME_BRAND_BLACKLIST]
    digit_root = digit_base if (digit_base and digit_base not in _NICKNAME_BRAND_BLACKLIST) \
        else (bases[0] if bases else None)
    if not bases and not digit_root:
        return None

    # 1) Bare bases in priority order. Skip a bare base if numbered
    #    siblings (<base>1..9) already exist in `taken` — otherwise we'd
    #    end up with mixed `living` + `living1` + `living2` assignments,
    #    which contradicts the "all-numbered if contested" rule the
    #    operator set on 2026-05-11. If the bare slot is "alone" it's
    #    still usable.
    def _has_numbered_sibling(b):
        return any(f'{b}{d}' in taken for d in range(1, 10))

    for base in bases:
        if base not in taken and not _has_numbered_sibling(base):
            return base

    # 2) Digit cascade — starts at 1 because the bare base is
    #    conceptually the "first" of its line, so a sibling is
    #    "<base>1" rather than "<base>0". Regex allows any single
    #    digit so 1..9 covers nine duplicates per base.
    if digit_root:
        for d in range(1, 10):
            candidate = f'{digit_root}{d}'
            if candidate in _NICKNAME_BRAND_BLACKLIST:
                continue
            if candidate not in taken:
                return candidate

    return None


# Backward-compat wrapper for callers (e.g. /api/cameras/nicknames?suggest_for=)
# that still pass a single name and expect the same behavior the field
# used to have. Internally just routes through the new bases pipeline.
def _autogen_base_from_last_word(name: str) -> str:
    bases = _autogen_bases_from_name(name)
    return bases[0] if bases else ''


def autogenerate_missing_nicknames() -> dict:
    """
    Walk every camera with nickname IS NULL and try to assign one
    derived from the last word of its display name. Idempotent: only
    touches NULL rows, never overwrites an existing nickname.

    Returns a summary dict:
        {
          "considered": <int>,    # rows examined
          "assigned":   {<serial>: <nickname>, ...},
          "skipped":    {<serial>: "<reason>", ...},
        }

    Designed to run at boot (best-effort — failures logged but never
    raise) and also reachable via POST /api/cameras/nicknames/auto-generate
    for re-runs after a batch of camera renames.
    """
    from collections import Counter

    summary = {"considered": 0, "assigned": {}, "skipped": {}}
    try:
        with _nickname_db_conn() as conn, conn.cursor() as cur:
            # Stamp the SET LOCAL audit.* GUCs for the cameras UPDATE
            # trigger to capture WHO. No-op if not in a request context
            # (admin batch call from a daemon would still write rows,
            # just with NULL actor — that's the graceful-degrade path).
            from services.audit_actor import apply_audit_actor
            apply_audit_actor(cur)
            cur.execute(
                "SELECT serial, name, nickname FROM cameras ORDER BY name ASC"
            )
            rows = cur.fetchall()
            taken = {nick for (_s, _n, nick) in rows if nick}

            # Pre-pass: tally every primary base that would be claimed
            # by a NULL row AND every primary base already occupied by an
            # existing nickname. Treats "lobby" and "lobby1" both as
            # claiming the base "lobby" so contests are detected
            # regardless of the digit suffix already present in the DB.
            #
            # If a primary ends up with more than one claimant we drop
            # the BARE primary from that camera's candidate list — the
            # rule is "either uniquely named bare, or all numbered".
            # Avoids the awkward `living` + `living1` mix the operator
            # flagged on 2026-05-11.
            null_rows = [(s, n) for (s, n, nick) in rows if nick is None]
            bases_per_serial = {}
            primary_count = Counter()
            for serial, name in null_rows:
                # Brand-filter at the pre-pass so primary_count reflects
                # the EFFECTIVE primary each camera would actually claim.
                # Without this filter `Living_REOLINK` (raw bases
                # [reolink, living]) would be counted under 'reolink'
                # while its real claim is on 'living'.
                b = [bb for bb in _autogen_bases_from_name(name or '')
                     if bb not in _NICKNAME_BRAND_BLACKLIST]
                bases_per_serial[serial] = b
                if b:
                    primary_count[b[0]] += 1
            for nick in taken:
                m = re.match(r'^([a-z]+)[0-9]?$', nick)
                if m:
                    primary_count[m.group(1)] += 1

            # Process order: cameras with FEWER viable bases first.
            # A single-option camera has no fallback — its primary is
            # forced and locking it in early lets multi-option cameras
            # adapt around it. Without this ordering, a multi-option
            # camera with a unique penultimate can claim a bare base
            # like 'living' before its single-option siblings arrive,
            # ending up with the forbidden `living` + `living1` mix.
            def _ord_key(row):
                serial, name = row
                viable = [b for b in (bases_per_serial.get(serial) or [])
                          if b and b not in _NICKNAME_BRAND_BLACKLIST]
                return (len(viable) or 99, name or '')
            ordered_rows = sorted(null_rows, key=_ord_key)

            for (serial, name) in ordered_rows:
                summary["considered"] += 1
                bases = bases_per_serial.get(serial) or _autogen_bases_from_name(name or '')
                if not bases:
                    summary["skipped"][serial] = 'no alphabetic last word'
                    continue
                # If the primary is contested, strip it from the bare-base
                # candidate list (penultimate fallback still tried bare;
                # digit-cascade still uses primary).
                primary = bases[0]
                contested = primary_count[primary] > 1
                effective_bases = bases[1:] if contested else bases
                chosen = _resolve_free_nickname(effective_bases, taken, digit_base=primary)
                if not chosen:
                    summary["skipped"][serial] = (
                        f'bases {bases} exhausted or reserved'
                        + (' (contested primary)' if contested else '')
                    )
                    continue
                try:
                    cur.execute(
                        "UPDATE cameras SET nickname = %s WHERE serial = %s",
                        (chosen, serial),
                    )
                    taken.add(chosen)
                    summary["assigned"][serial] = chosen
                except Exception as e:
                    summary["skipped"][serial] = f'db error: {e}'
                    conn.rollback()
                    # Re-open the cursor since rollback may have aborted it.
                    continue
            conn.commit()
    except Exception:
        logger.exception("autogenerate_missing_nicknames: failed")
    if summary["assigned"]:
        logger.info(
            "autogenerated %d nickname(s): %s",
            len(summary["assigned"]),
            ', '.join(f'{k}={v}' for k, v in summary["assigned"].items()),
        )
    return summary


@camera_bp.route('/api/cameras/nicknames/auto-generate', methods=['POST'])
@csrf_exempt
@login_required
def api_cameras_nicknames_autogen():
    """
    Trigger autogenerate_missing_nicknames() — fills in NULL nicknames
    from the last word of each camera's display name. Useful after a
    batch of camera renames.
    """
    return jsonify(autogenerate_missing_nicknames())


def _suggest_nickname(camera_name: str, existing_nicknames: set) -> str | None:
    """
    Derive a candidate nickname from a camera's display name for the
    Settings UI Suggest button. Uses the same priority order as the
    boot autogenerator:
        last word > penultimate word (if not brand) > <last>0..9
    """
    bases = _autogen_bases_from_name(camera_name or '')
    if not bases:
        bases = ['camera']
    return _resolve_free_nickname(bases, set(existing_nicknames or ()))


@camera_bp.route('/api/camera/<camera_serial>/nickname', methods=['PUT'])
@csrf_exempt
@login_required
def api_camera_set_nickname(camera_serial):
    """
    Set or clear a camera's nickname.

    Body: {"nickname": "lobby"}     — set
          {"nickname": null}        — clear
          {"nickname": ""}          — clear

    Returns:
        200 {"serial": "...", "nickname": "..."} on success
        400 {"error": "..."} on regex / brand / type errors
        404 if camera not found
        409 {"error": "...", "owner": "<other_serial>"} on collision
    """
    if not shared.camera_repo.get_camera(camera_serial):
        return jsonify({'error': f'Camera not found: {camera_serial}'}), 404

    body = request.get_json(silent=True) or {}
    raw = body.get('nickname', None)

    # Normalize: empty/whitespace/None -> clear
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        nickname = None
    elif isinstance(raw, str):
        nickname = raw.strip().lower()
    else:
        return jsonify({'error': 'nickname must be a string or null'}), 400

    if nickname is not None:
        if not _NICKNAME_REGEX.match(nickname):
            return jsonify({'error': 'nickname must match ^[a-z]+[0-9]?$'}), 400
        if nickname in _NICKNAME_BRAND_BLACKLIST:
            return jsonify({'error': 'nickname is a reserved brand name'}), 409

        # Collision check before write so we can return the conflicting
        # serial in the error body — the DB UNIQUE index would also catch
        # this but with an opaque IntegrityError.
        try:
            with _nickname_db_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT serial FROM cameras "
                    "WHERE nickname = %s AND serial != %s",
                    (nickname, camera_serial),
                )
                hit = cur.fetchone()
                if hit:
                    return jsonify({
                        'error': 'nickname already used by another camera',
                        'owner': hit[0],
                    }), 409
        except Exception as e:
            logger.exception("nickname collision-check failed")
            return jsonify({'error': f'DB error: {e}'}), 500

    success = shared.camera_repo.update_camera_setting(
        camera_serial, 'nickname', nickname,
    )
    if not success:
        return jsonify({'error': 'failed to write nickname'}), 500

    return jsonify({'serial': camera_serial, 'nickname': nickname})


def _nickname_read_authorized() -> bool:
    """
    Authorize the GET /api/cameras/nicknames endpoint.

    Three accepted paths, in order:
      1. Active Flask-Login session (the normal browser case).
      2. Authorization: Bearer <NVR_API_TOKEN>  (external integrations,
         same token routes/external_api_routes.py and routes/host_state.py
         already accept).
      3. LAN-only fallback when NVR_API_TOKEN is unset (dev convenience,
         matches the pattern in routes/host_state.py:_check_bearer).
    """
    # 1) session
    if getattr(current_user, 'is_authenticated', False):
        return True
    # 2) bearer
    token_env = os.environ.get('NVR_API_TOKEN', '').strip()
    auth = request.headers.get('Authorization', '')
    if auth.startswith('Bearer ') and token_env:
        return auth[7:] == token_env
    # 3) LAN fallback (only when no token configured — dev mode)
    if not token_env:
        ip = (request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
              or request.remote_addr or '')
        return ip.startswith(('10.', '172.', '192.168.', '127.')) or ip == '::1'
    return False


@camera_bp.route('/api/cameras/nicknames', methods=['GET'])
def api_cameras_nicknames():
    """
    List every camera with its nickname (null if unset).

    Auth: session OR Bearer NVR_API_TOKEN OR LAN-fallback (when no token
    configured). Intentionally NOT @login_required so external systems
    (home automation, dashboards) can discover the URL handle for each
    feed without a browser session.

    Response:
        {"cameras": [
            {"serial": "...", "name": "Lobby", "nickname": "lobby"},
            ...
        ],
         "suggestion_for": "<serial>": "<suggested_nickname>"   # optional
        }

    If ?suggest_for=<serial> is passed, attempt an auto-suggestion for
    that camera based on its display name. The suggestion is just a hint
    — the caller still has to PUT /api/camera/<serial>/nickname.
    """
    if not _nickname_read_authorized():
        return jsonify({'error': 'unauthorized'}), 401

    try:
        with _nickname_db_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT serial, name, nickname "
                "FROM cameras "
                "ORDER BY nickname ASC NULLS LAST, name ASC"
            )
            rows = cur.fetchall()
    except Exception as e:
        logger.exception("nickname list failed")
        return jsonify({'error': f'DB error: {e}'}), 500

    cameras = [
        {'serial': s, 'name': n, 'nickname': nick}
        for (s, n, nick) in rows
    ]
    out = {'cameras': cameras}

    suggest_for = (request.args.get('suggest_for') or '').strip()
    if suggest_for:
        target = next((c for c in cameras if c['serial'] == suggest_for), None)
        if target:
            taken = {c['nickname'] for c in cameras if c['nickname']}
            suggestion = _suggest_nickname(target['name'] or '', taken)
            out['suggestion_for'] = suggest_for
            out['suggestion'] = suggestion

    return jsonify(out)


# ===== Remote fullscreen switch =====
#
# Allows an external integration (home automation, dashboard, voice
# assistant) to push "show this camera fullscreen on the kiosk(s)"
# without a browser session.
#
# Auth: Bearer NVR_API_TOKEN only. No session, no LAN fallback — this is
# a mutation that affects what every viewer sees, so we require an
# explicit credential. Use the same NVR_API_TOKEN that gates
# /api/host/state and /api/external/*.
#
# Real-time path: emits a `fullscreen_request` event on the
# /stream_events SocketIO namespace. Subscribers on /streams (stream.js)
# and /light (light-mode-app.js) listen for this and call their
# respective openFullscreen() — which writes localStorage exactly as a
# native click would, so the chosen camera persists across reloads.
#
# Targeting: optional host_label scopes the event to one kiosk; absent
# means "all viewers". Each browser reads its own host_label from
# localStorage.mobius_host_label (same key the throttle controller uses)
# and filters server events accordingly.

_socketio = None  # injected by app.py via init_camera_socketio()


def init_camera_socketio(socketio_instance) -> None:
    """Called from app.py after socketio is constructed."""
    global _socketio
    _socketio = socketio_instance


def _bearer_only_auth() -> bool:
    """Bearer NVR_API_TOKEN required. No session, no LAN fallback."""
    token_env = os.environ.get('NVR_API_TOKEN', '').strip()
    if not token_env:
        return False
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return False
    return auth[7:] == token_env


@camera_bp.route('/api/fullscreen/switch', methods=['POST'])
@csrf_exempt
def api_fullscreen_switch():
    """
    Push a fullscreen target to the kiosk(s) in real time.

    Body:
        {
          "target":     "lobby" | "T8416P0023352DA9",   (required)
          "host_label": "rog"                            (optional;
              if present, only browsers bound to this host_label react)
        }

    Returns:
        200 {"serial": "...", "nickname": "...|null", "host_label": "...|null"}
        400 {"error": "missing target"}
        401 {"error": "unauthorized"}    (token missing/invalid)
        404 {"error": "unknown target"}  (nickname or serial not found)
        503 {"error": "socketio not ready"}
    """
    if not _bearer_only_auth():
        return jsonify({'error': 'unauthorized'}), 401

    body = request.get_json(silent=True) or {}
    target = (body.get('target') or '').strip()
    host_label = (body.get('host_label') or '').strip() or None
    if not target:
        return jsonify({'error': 'missing target'}), 400

    # Resolve target -> canonical serial. Accept either an exact serial
    # match or a nickname (case-insensitive).
    cameras = shared.camera_repo.get_streaming_cameras(include_hidden=True)
    resolved_serial = None
    resolved_nick = None
    if target in cameras:
        resolved_serial = target
        resolved_nick = cameras[target].get('nickname')
    else:
        needle = target.lower()
        for serial, info in cameras.items():
            nick = (info.get('nickname') or '').lower()
            if nick and nick == needle:
                resolved_serial = serial
                resolved_nick = info.get('nickname')
                break

    if not resolved_serial:
        return jsonify({'error': 'unknown target', 'target': target}), 404

    if _socketio is None:
        return jsonify({'error': 'socketio not ready'}), 503

    payload = {
        'serial':     resolved_serial,
        'nickname':   resolved_nick,
        'host_label': host_label,
        'ts':         time.time(),
    }
    try:
        _socketio.emit('fullscreen_request', payload, namespace='/stream_events')
    except Exception as e:
        logger.exception('fullscreen_switch: emit failed')
        return jsonify({'error': f'emit failed: {e}'}), 500

    logger.info(
        'fullscreen_switch: target=%r -> serial=%s (host_label=%s)',
        target, resolved_serial, host_label or '<any>',
    )
    return jsonify(payload)


@camera_bp.route('/api/fullscreen/exit', methods=['POST'])
@csrf_exempt
def api_fullscreen_exit():
    """
    Tell the kiosk(s) to exit fullscreen and return to the grid view.
    Also clears the local persistence (localStorage.fullscreenCameraSerial
    on /streams, localStorage.nvr_light_fs_cam on /light) so the next
    page reload doesn't restore the prior fullscreen.

    Body:
        {"host_label": "rog"}    optional — scope to one kiosk; absent
                                 means every viewer.

    Auth: Bearer NVR_API_TOKEN only. Same authority as the switch
    endpoint — both are remote-control mutations.
    """
    if not _bearer_only_auth():
        return jsonify({'error': 'unauthorized'}), 401

    body = request.get_json(silent=True) or {}
    host_label = (body.get('host_label') or '').strip() or None

    if _socketio is None:
        return jsonify({'error': 'socketio not ready'}), 503

    payload = {'host_label': host_label, 'ts': time.time()}
    try:
        _socketio.emit('fullscreen_exit', payload, namespace='/stream_events')
    except Exception as e:
        logger.exception('fullscreen_exit: emit failed')
        return jsonify({'error': f'emit failed: {e}'}), 500

    logger.info('fullscreen_exit: host_label=%s', host_label or '<any>')
    return jsonify(payload)


def _nickname_db_conn():
    """Minimal direct connection — same pattern as routes/host_state.py."""
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST', 'postgres'),
        port=os.getenv('POSTGRES_PORT', '5432'),
        dbname=os.getenv('POSTGRES_DB', 'nvr'),
        user=os.getenv('POSTGRES_USER', 'nvr_api'),
        password=os.getenv('POSTGRES_PASSWORD', 'nvr_internal_db_key'),
        connect_timeout=3,
    )


# ===== Camera Settings (Generic) =====

@camera_bp.route('/api/camera/<camera_serial>/settings', methods=['PUT'])
@csrf_exempt
@login_required
def api_camera_settings_update(camera_serial):
    """
    Update one or more top-level settings for a camera.

    Request body: {"key": "value", ...}
    Only whitelisted keys are accepted to prevent accidental corruption.
    Nested objects (ll_hls, mjpeg_snap, etc.) are accepted as full replacements.

    Returns:
        200: {"success": true, "updated": ["key1", "key2"]}
        400: Invalid key or value
        404: Camera not found
    """
    # Keys that the UI is allowed to modify via this endpoint.
    # 'serial' and 'camera_id' are immutable identifiers.
    EDITABLE_KEYS = {
        'name', 'type', 'host', 'mac', 'packager_path', 'stream_type',
        'rtsp_alias', 'max_connections', 'onvif_port', 'power_supply',
        'hidden', 'ui_health_monitor', 'ui_health_refresh_delay_ms',
        'reversed_pan', 'reversed_tilt',
        'notes', 'power_supply_device_id', 'true_mjpeg', 'capabilities',
        'll_hls', 'mjpeg_snap', 'neolink', 'player_settings',
        'rtsp_input', 'rtsp_output', 'two_way_audio',
        'power_cycle_on_failure', 'rtsp', 'model', 'station',
        'image_mirrored', 'streaming_hub',
        # Throttler controls (migration 039). throttle_priority is INT,
        # throttle_never is BOOL — the frontend coerces appropriately
        # before sending; the Settings class round-trips both fine.
        'throttle_priority', 'throttle_never',
    }
    IMMUTABLE_KEYS = {'serial', 'camera_id', 'id'}

    try:
        data = request.get_json()
        if not data or not isinstance(data, dict):
            return jsonify({'error': 'Request body must be a JSON object'}), 400

        camera = shared.camera_repo.get_camera(camera_serial)
        if not camera:
            return jsonify({'error': f'Camera not found: {camera_serial}'}), 404

        # Reject immutable keys
        blocked = set(data.keys()) & IMMUTABLE_KEYS
        if blocked:
            return jsonify({'error': f'Cannot modify immutable keys: {", ".join(blocked)}'}), 400

        # ── Layer-1 (source / streaming_hub): admin-only + validated ─────────
        # Changing the hub changes the single feed every client derives from,
        # and native_mjpeg excludes the camera from RTSP ingest entirely. So:
        #   (a) validate the value against the known hubs (reject typos),
        #   (b) gate the change to admins,
        #   (c) flag requires_restart — mediamtx/go2rtc configs are NOT
        #       hot-reloaded; a hub change needs generate_streaming_configs.py
        #       + a container restart. The UI prompts the operator on this flag.
        requires_restart = False
        if 'streaming_hub' in data:
            from services.streaming_hub import VALID_STREAMING_HUBS, get_streaming_hub
            new_hub = (data.get('streaming_hub') or 'mediamtx').lower()
            if new_hub not in VALID_STREAMING_HUBS:
                return jsonify({'error': f"Invalid streaming_hub '{data.get('streaming_hub')}'. "
                                         f"Allowed: {', '.join(sorted(VALID_STREAMING_HUBS))}"}), 400
            if not current_user or getattr(current_user, 'role', None) != 'admin':
                return jsonify({'error': "Only an admin can change a camera's streaming source"}), 403
            data['streaming_hub'] = new_hub  # normalize casing before write
            old_hub = get_streaming_hub(camera)
            if new_hub != old_hub:
                requires_restart = True
                logger.info(f"[Settings PUT] {camera_serial}: streaming_hub {old_hub} -> {new_hub} "
                            f"(config regeneration + restart required)")

        # Use Settings class for DB writes (handles direct columns vs extra_config)
        logger.info(f"[Settings PUT] {camera_serial}: shared.settings={shared.settings is not None}, data={data}")
        if shared.settings:
            success = shared.settings.set_camera_bulk(camera_serial, data)
            logger.info(f"[Settings PUT] {camera_serial}: set_camera_bulk returned {success}")
            if success:
                updated = list(data.keys())
            else:
                updated = []
        else:
            # Fallback: direct PostgREST PATCH (no Settings class available)
            logger.warning(f"[Settings PUT] {camera_serial}: shared.settings is None, using direct PATCH")
            import os, requests as http_req
            postgrest_url = os.getenv('NVR_POSTGREST_URL', 'http://postgrest:3001')
            resp = http_req.patch(
                f"{postgrest_url}/cameras",
                params={'serial': f'eq.{camera_serial}'},
                json=data,
                timeout=5
            )
            if resp.status_code in (200, 204):
                updated = list(data.keys())
                logger.info(f"[Settings PUT] {camera_serial}: direct PATCH success")
            else:
                updated = []
                logger.error(f"[Settings PUT] {camera_serial}: direct PATCH failed {resp.status_code}: {resp.text}")

        if not updated:
            return jsonify({'error': 'No settings were updated'}), 500

        logger.info(f"Camera settings updated for {camera_serial}: {updated}")
        return jsonify({'success': True, 'updated': updated, 'requires_restart': requires_restart})

    except Exception as e:
        logger.error(f"Error updating camera settings {camera_serial}: {e}")
        return jsonify({'error': str(e)}), 500


# ===== Streaming Configuration Routes =====

@camera_bp.route('/api/config/streaming')
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
        webrtc_settings = shared.camera_repo.cameras_data.get('webrtc_global_settings', {})
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

@camera_bp.route('/api/bridge/start', methods=['POST'])
@csrf_exempt
@login_required
def api_bridge_start():
    """Start the Eufy bridge"""
    try:
        if not shared.eufy_bridge:
            return jsonify({'success': False, 'error': 'Eufy bridge not configured (USE_EUFY_BRIDGE=0)'}), 503
        success = shared.eufy_bridge.start()
        return jsonify({
            'success': success,
            'message': 'Bridge started successfully' if success else 'Failed to start bridge'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@camera_bp.route('/api/bridge/stop', methods=['POST'])
@csrf_exempt
@login_required
def api_bridge_stop():
    """Stop the Eufy bridge"""
    try:
        if not shared.eufy_bridge:
            return jsonify({'success': False, 'error': 'Eufy bridge not configured'}), 503
        shared.eufy_bridge.stop()
        return jsonify({'success': True, 'message': 'Bridge stopped'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ===== Device Management Routes =====

@camera_bp.route('/api/devices/refresh', methods=['POST'])
@csrf_exempt
@login_required
def api_refresh_devices():
    """Refresh device list from bridge (Eufy only for now)"""
    try:
        # TODO: Implement device discovery with new architecture
        # For now, just reload configs
        shared.camera_repo.reload()

        return jsonify({
            'success': True,
            'total_devices': shared.camera_repo.get_camera_count(),
            'ptz_cameras': len(shared.camera_repo.get_ptz_cameras())
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@camera_bp.route('/api/cameras/force-sync', methods=['POST'])
@csrf_exempt
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
        shared.camera_repo.reload()

        return jsonify({
            'success': True,
            'cameras_updated': updated,
            'total_devices': shared.camera_repo.get_camera_count(),
            'source': shared.camera_repo.get_data_source(),
            'message': f'Force-synced {updated} cameras from cameras.json to database'
        })
    except Exception as e:
        logger.error(f"Force sync failed: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@camera_bp.route('/api/cameras/data-source', methods=['GET'])
@csrf_exempt
@login_required
def api_camera_data_source():
    """Get the current camera data source (database or json)."""
    return jsonify({
        'source': shared.camera_repo.get_data_source(),
        'total_devices': shared.camera_repo.get_camera_count(include_hidden=True),
        'visible_devices': shared.camera_repo.get_camera_count(include_hidden=False),
        'last_updated': shared.camera_repo.get_last_updated()
    })


# ===== Streaming Routes =====

# RTMP / FLV
@camera_bp.route('/api/camera/<camera_serial>/flv')
@csrf_exempt
@login_required
def serve_camera_flv(camera_serial):
    """Serve FLV stream from already-running RTMP process"""

    # Get process from StreamManager WITH LOCK
    with shared.stream_manager._streams_lock:
        stream_info = shared.stream_manager.active_streams.get(camera_serial)

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


@camera_bp.route('/api/stream/start/<camera_serial>', methods=['POST'])
@csrf_exempt
@login_required
def api_stream_start(camera_serial):
    """Start HLS stream for camera"""
    try:
        # Get camera (includes hidden cameras)
        camera = shared.camera_repo.get_camera(camera_serial)

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


@camera_bp.route('/api/stream/stop/<camera_serial>', methods=['POST'])
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


@camera_bp.route('/api/stream/restart/<camera_serial>', methods=['POST'])
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

        # go2rtc cameras: no FFmpeg to restart — go2rtc manages its own connections.
        # Just return success so the frontend reconnects via go2rtc WebRTC API.
        from services.streaming_hub import get_streaming_hub
        hub = get_streaming_hub(camera)
        if hub == 'go2rtc':
            logger.info(f"[RESTART] {camera_name} uses go2rtc hub — no FFmpeg restart needed")
            return jsonify({
                'success': True,
                'camera_serial': camera_serial,
                'camera_name': camera_name,
                'message': f'go2rtc manages {camera_name} — reconnecting player',
                'streaming_hub': 'go2rtc',
                'stream_url': f'/api/streams/{camera_serial}/playlist.m3u8'
            })

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


@camera_bp.route('/api/stream/status/<camera_serial>')
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


@camera_bp.route('/api/camera/state/<camera_id>')
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
            'can_retry': camera_state_tracker.can_retry(camera_id)
        })

    except Exception as e:
        logger.error(f"Error getting camera state for {camera_id}: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e),
            'camera_id': camera_id
        }), 500


@camera_bp.route('/api/camera/states')
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
                    'can_retry': camera_state_tracker.can_retry(camera_id)
                }

        return jsonify({'success': True, 'states': states})

    except Exception as e:
        logger.error(f"Error getting batch camera states: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


# ===== Camera Reboot =====

@camera_bp.route('/api/camera/<camera_serial>/reboot', methods=['POST'])
@csrf_exempt
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

        camera = shared.camera_repo.get_camera(camera_serial)
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


# ===== Camera Credentials =====

@camera_bp.route('/api/camera/<camera_serial>/credentials', methods=['GET'])
@login_required
def api_camera_credentials_get(camera_serial):
    """
    Check if credentials exist for a camera (does NOT return actual passwords).

    Returns main camera credentials (per-camera or brand-level fallback) plus
    go2rtc-specific credentials as a separate field.

    Response shape:
        {
            has_credentials: bool,
            username: str|null,
            source: "db"|"none",
            scope: "camera"|"brand",          # only present when has_credentials=true
            go2rtc_credentials: {
                has_credentials: bool,
                username: str|null
            }
        }
    """
    from services.credentials import credential_db_service as cred_db

    # ── Main camera credentials (per-camera first, brand-level fallback) ─────
    main_result = {'has_credentials': False, 'username': None, 'source': 'none'}

    username, password = cred_db.get_credential(camera_serial, 'camera')
    if username and password:
        main_result = {'has_credentials': True, 'username': username, 'password': password, 'source': 'db', 'scope': 'camera'}
    else:
        camera = shared.camera_repo.get_camera(camera_serial)
        if camera:
            cam_type = camera.get('type', '').lower()
            service_key_map = {
                'reolink': 'reolink_api',
                'amcrest': 'amcrest',
                'sv3c': 'sv3c',
                'unifi': 'unifi_protect',
                'eufy': None  # Eufy uses per-camera only
            }
            service_key = service_key_map.get(cam_type)
            if service_key:
                username, password = cred_db.get_credential(service_key, 'service')
                if username and password:
                    main_result = {'has_credentials': True, 'username': username, 'password': password, 'source': 'db', 'scope': 'brand'}

    # ── go2rtc credentials (per-camera, credential_type='go2rtc') ────────────
    go2rtc_user, go2rtc_pass = cred_db.get_credential(camera_serial, 'go2rtc')

    go2rtc_result = {
        'has_credentials': bool(go2rtc_user and go2rtc_pass),
        'username': go2rtc_user if go2rtc_user else None,
        'password': go2rtc_pass if go2rtc_pass else None,
    }

    return jsonify({**main_result, 'go2rtc_credentials': go2rtc_result})


@camera_bp.route('/api/camera/<camera_serial>/credentials', methods=['PUT'])
@csrf_exempt
@login_required
def api_camera_credentials_put(camera_serial):
    """
    Store or update credentials for a camera.
    Body: {username: "...", password: "...", scope: "camera"|"brand"}

    scope="camera" stores per-camera credentials (keyed by serial).
    scope="brand" stores brand-level credentials (keyed by vendor type).
    """
    from services.credentials import credential_db_service as cred_db

    data = request.get_json()
    if not data or not data.get('username') or not data.get('password'):
        return jsonify({'success': False, 'error': 'username and password required'}), 400

    camera = shared.camera_repo.get_camera(camera_serial)
    if not camera:
        return jsonify({'success': False, 'error': 'Camera not found'}), 404

    cam_type = camera.get('type', '').lower()
    scope = data.get('scope', 'camera')
    vendor = cam_type if cam_type in ('eufy', 'reolink', 'unifi', 'amcrest', 'sv3c') else 'system'

    if scope == 'go2rtc':
        # Per-camera go2rtc credentials — used by generate_go2rtc_config.py to resolve
        # ${go2rtc_username} / ${go2rtc_password} placeholders in go2rtc_source URLs.
        cred_key  = camera_serial
        cred_type = 'go2rtc'
        label     = f'{camera.get("name", camera_serial)} go2rtc credentials'
    elif scope == 'go2rtc_brand':
        # Brand-level go2rtc credentials — shared by all cameras of this type.
        # generate_go2rtc_config.py falls back to these when no per-camera go2rtc cred exists.
        go2rtc_vendor_key_map = {
            'reolink': 'reolink_go2rtc',
            'amcrest': 'amcrest_go2rtc',
            'sv3c': 'sv3c_go2rtc',
            'eufy': 'eufy_go2rtc',
        }
        cred_key  = go2rtc_vendor_key_map.get(cam_type, f'{cam_type}_go2rtc')
        cred_type = 'service'
        label     = f'{cam_type.title()} go2rtc brand credentials'
    elif scope == 'brand':
        # Store as brand-level service credential
        vendor_key_map = {
            'reolink': 'reolink_api',
            'amcrest': 'amcrest',
            'sv3c': 'sv3c',
            'unifi': 'unifi_protect',
            'eufy': 'eufy_bridge'
        }
        cred_key  = vendor_key_map.get(cam_type, cam_type)
        cred_type = 'service'
        label     = f'{cam_type.title()} brand credentials'
    else:
        cred_key  = camera_serial
        cred_type = 'camera'
        label     = f'{camera.get("name", camera_serial)} credentials'

    success = cred_db.store_credential(
        credential_key=cred_key,
        username=data['username'],
        password=data['password'],
        vendor=vendor,
        credential_type=cred_type,
        label=label
    )

    return jsonify({'success': success})


@camera_bp.route('/api/camera/<camera_serial>/credentials', methods=['DELETE'])
@csrf_exempt
@login_required
def api_camera_credentials_delete(camera_serial):
    """Delete per-camera credentials (does not delete brand-level credentials)."""
    from services.credentials import credential_db_service as cred_db

    success = cred_db.delete_credential(camera_serial, 'camera')
    return jsonify({'success': success})


@camera_bp.route('/api/credentials/copy-to-go2rtc/<vendor>', methods=['POST'])
@csrf_exempt
@login_required
def api_copy_camera_creds_to_go2rtc(vendor):
    """
    Copy each camera's main credentials into its go2rtc credential slot
    for all cameras of the given vendor type.

    For each camera of that vendor:
      1. Read main credential (per-camera first, brand-level fallback)
      2. Store as (serial, 'go2rtc') credential

    Returns: { copied: int, skipped: int, errors: [...] }
    """
    if current_user.role != 'admin':
        return jsonify({'error': 'Admin access required'}), 403

    from services.credentials import credential_db_service as cred_db

    # Get all cameras of this vendor type
    all_cameras = shared.camera_repo.get_all_cameras(include_hidden=True)
    vendor_cameras = {
        serial: cam for serial, cam in all_cameras.items()
        if (cam.get('type') or '').lower() == vendor.lower()
    }

    if not vendor_cameras:
        return jsonify({'error': f'No cameras found for vendor "{vendor}"'}), 404

    copied = 0
    skipped = 0
    errors = []

    for serial, cam in vendor_cameras.items():
        # Read main camera credential (per-camera first, brand fallback)
        username, password = cred_db.get_credential(serial, 'camera')
        if not username or not password:
            # Try brand-level fallback
            service_key_map = {
                'reolink': 'reolink_api', 'amcrest': 'amcrest',
                'sv3c': 'sv3c', 'unifi': 'unifi_protect', 'eufy': None,
            }
            service_key = service_key_map.get(vendor.lower())
            if service_key:
                username, password = cred_db.get_credential(service_key, 'service')

        if not username or not password:
            skipped += 1
            continue

        cam_name = cam.get('name', serial)
        success = cred_db.store_credential(
            credential_key=serial,
            username=username,
            password=password,
            vendor=vendor.lower(),
            credential_type='go2rtc',
            label=f'{cam_name} go2rtc credentials'
        )
        if success:
            copied += 1
        else:
            errors.append(serial)

    logger.info(f"[CopyToGo2rtc] vendor={vendor}: copied={copied}, skipped={skipped}, errors={len(errors)}")
    return jsonify({'copied': copied, 'skipped': skipped, 'errors': errors})


# ===== Service-Level (Brand) Credentials =====

@camera_bp.route('/api/credentials/service', methods=['GET'])
@login_required
def api_service_credentials_list():
    """
    List all service-level credentials (brand-level).
    Returns credential keys and usernames only (no passwords).
    """
    from services.credentials import credential_db_service as cred_db

    try:
        resp = cred_db._postgrest_session().get(
            f"{cred_db.POSTGREST_URL}/camera_credentials",
            params={
                'credential_type': 'eq.service',
                'select': 'credential_key,vendor,label,updated_at'
            }
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify([])
    except Exception:
        return jsonify([])


# ===== Speaker Volume (Two-Way Audio) =====

@camera_bp.route('/api/cameras/<camera_serial>/speaker_volume', methods=['GET', 'POST'])
@csrf_exempt
@login_required
def api_camera_speaker_volume(camera_serial):
    """
    Get or set speaker volume for a camera's two-way audio.

    GET: Returns current speaker_volume setting (0-150, default 100)
    POST: Updates speaker_volume from JSON body: {speaker_volume: 80}
    """
    camera = shared.camera_repo.get_camera(camera_serial)
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
    shared.camera_repo.update_camera_setting(camera_serial, 'two_way_audio', two_way_audio)

    logger.info(f"[TalkbackVolume] Updated speaker_volume for {camera_serial[:8]}... to {volume}%")

    return jsonify({
        'success': True,
        'camera_serial': camera_serial,
        'speaker_volume': volume
    })


# ===== Admin: Application Restart =====

@camera_bp.route('/api/admin/restart', methods=['POST'])
@csrf_exempt
@login_required
def api_admin_restart():
    """
    Trigger graceful application restart.

    Used by the UI after streaming infrastructure changes (go2rtc_source,
    streaming_hub) that require go2rtc.yaml regeneration and process restart.

    The restart runs in a daemon thread: streams are stopped, bridges cleaned up,
    then os._exit(1) lets Docker's restart policy restart the container.
    """
    data = request.get_json() or {}
    reason = data.get('reason', 'UI requested restart')

    if not shared.restart_handler:
        return jsonify({'success': False, 'error': 'Restart handler not initialized'}), 500

    # mode: 'full' = SSH to host, run start.sh (regenerates configs)
    #        'container' = os._exit(1), Docker restarts container only
    mode = data.get('mode', 'full')
    logger.info(f"[Admin] Restart requested (mode={mode}): {reason}")

    if mode == 'full':
        success = shared.restart_handler.restart_full(reason)
        if not success:
            return jsonify({'success': False, 'error': 'Restart already in progress'}), 409
        return jsonify({'success': True, 'message': 'Full restart initiated (start.sh on host)'})
    else:
        shared.restart_handler.restart_app(reason)
        return jsonify({'success': True, 'message': 'Container restart initiated'})


# ===== Admin: Restart-log live tail (SSE) =====

@camera_bp.route('/api/admin/restart-log/stream', methods=['GET'])
@login_required
def api_admin_restart_log_stream():
    """
    Server-Sent Events stream of ``~/0_MOBIUS.NVR/restart_from_app.log``.

    Why this exists:
      The /reloading page used to display a static "tail -f" command for the
      user to copy into a separate terminal. That worked but required the
      user to context-switch, and most browser sessions are remote so they
      may not even have shell access to the host. This endpoint streams the
      log directly into the page.

    File visibility:
      The host writes to ``~/0_MOBIUS.NVR/restart_from_app.log``. The
      container sees the project root bind-mounted at ``/app`` (see
      docker-compose.yml: ``./:/app``). So the log file is at
      ``/app/restart_from_app.log`` from the container's view.

    Stream framing:
      One SSE ``data:`` line per appended log line. Heartbeat comment
      every 15 s so intermediate proxies (nginx, browsers) don't time the
      connection out. Stops when the client disconnects.

    Auth:
      ``@login_required`` only — same gate as the restart endpoint itself.
      Admin-role check intentionally omitted to match the existing
      /api/admin/restart endpoint, which is also login-required-but-not-
      admin-only (audit point: tighten both at once if you tighten one).
    """
    import os
    import time
    from flask import Response, stream_with_context

    LOG_PATH = '/app/restart_from_app.log'
    POLL_INTERVAL = 0.5         # seconds between read attempts when at EOF
    HEARTBEAT_INTERVAL = 15.0   # seconds between SSE keep-alive comments
    MAX_INITIAL_TAIL_BYTES = 64 * 1024  # don't replay more than 64 KB on connect

    @stream_with_context
    def generate():
        # Initial header — confirms the stream is open before any log appears
        yield f": connected to /api/admin/restart-log/stream at {time.time():.0f}\n\n"

        last_heartbeat = time.time()
        f = None
        last_inode = None

        try:
            while True:
                # (Re)open the file if missing or rotated.
                try:
                    st = os.stat(LOG_PATH)
                except FileNotFoundError:
                    if f is not None:
                        try: f.close()
                        except Exception: pass
                        f = None
                        last_inode = None
                    yield f"data: (waiting for {LOG_PATH} to be created…)\n\n"
                    # Poll for the file to appear
                    for _ in range(int(2.0 / POLL_INTERVAL)):
                        time.sleep(POLL_INTERVAL)
                        if os.path.exists(LOG_PATH):
                            break
                    continue

                # Detect rotation / truncation by inode change
                if f is None or last_inode != st.st_ino:
                    if f is not None:
                        try: f.close()
                        except Exception: pass
                    f = open(LOG_PATH, 'r', encoding='utf-8', errors='replace')
                    last_inode = st.st_ino
                    # Seek to the last MAX_INITIAL_TAIL_BYTES so we don't dump
                    # an entire ancient log into the page on connect.
                    if st.st_size > MAX_INITIAL_TAIL_BYTES:
                        f.seek(st.st_size - MAX_INITIAL_TAIL_BYTES)
                        # Drop the partial line we likely landed in the middle of
                        f.readline()
                    yield f"data: --- attached to {LOG_PATH} (inode {last_inode}) ---\n\n"

                # Stream any new lines
                line = f.readline()
                if line:
                    # Strip trailing newline; SSE framing expects \n\n separators
                    yield f"data: {line.rstrip(chr(10))}\n\n"
                    continue

                # No new data — heartbeat if it's been a while, then poll
                now = time.time()
                if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                    yield f": heartbeat {int(now)}\n\n"
                    last_heartbeat = now
                time.sleep(POLL_INTERVAL)
        except GeneratorExit:
            # Client disconnected — clean up file handle
            if f is not None:
                try: f.close()
                except Exception: pass
            return
        except Exception as e:
            logger.warning(f"[restart-log SSE] stream error: {e}")
            yield f"data: --- stream error: {e} ---\n\n"
            if f is not None:
                try: f.close()
                except Exception: pass

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',  # disable nginx buffering for SSE
            'Connection': 'keep-alive',
        },
    )
