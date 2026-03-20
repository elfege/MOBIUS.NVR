"""
routes/helpers.py — Shared helpers for NVR Flask Blueprints.

Contains:
- csrf_exempt: standalone decorator (avoids import-time circular dependency
  with the CSRFProtect instance living in app.py)
- Session / device helpers (_create_user_session, _deactivate_user_session,
  _register_or_update_device)
- Camera access helpers (_get_allowed_camera_serials, _filter_cameras)
- Trusted-network helpers (_get_client_ip, _is_same_subnet,
  _is_trusted_network_enabled)
- Environment helpers (_get_bool, _get_int, _resolve_ui_vs_watchdog,
  _ui_health_from_env)
"""

from __future__ import annotations

import os
import time as _time
from datetime import datetime
from typing import Any

import requests
from flask import request

# Import the shared service registry — helpers use shared.POSTGREST_URL etc.
import routes.shared as shared

# ---------------------------------------------------------------------------
# CSRF helpers
# ---------------------------------------------------------------------------

TRUE_SET = {"1", "true", "yes", "on"}
FALSE_SET = {"0", "false", "no", "off"}


def csrf_exempt(f):
    """
    Mark a view function as exempt from CSRF validation.

    Replicates Flask-WTF's CSRFProtect.exempt() logic without needing the
    actual CSRFProtect instance at import time.  Flask-WTF checks the
    ``_csrf_exempt`` attribute on the view function during request dispatch.
    """
    f._csrf_exempt = True
    return f


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def _create_user_session(user_id: int, ip_address: str, user_agent: str) -> None:
    """
    Create a session record in the database via PostgREST.

    Args:
        user_id: User ID.
        ip_address: Client IP address.
        user_agent: Client User-Agent string.
    """
    try:
        shared._postgrest_session.post(
            f"{shared.POSTGREST_URL}/user_sessions",
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


def _deactivate_user_session(user_id: int) -> None:
    """
    Mark all active sessions for a user as inactive.

    Args:
        user_id: User ID.
    """
    try:
        shared._postgrest_session.patch(
            f"{shared.POSTGREST_URL}/user_sessions",
            params={'user_id': f'eq.{user_id}', 'is_active': 'eq.true'},
            json={'is_active': False},
            headers={'Prefer': 'return=minimal'},
            timeout=5
        )
    except requests.RequestException as e:
        print(f"Error deactivating user session: {e}")


# ---------------------------------------------------------------------------
# Device helpers
# ---------------------------------------------------------------------------

def _register_or_update_device(
        device_token: str,
        user_id: int | None,
        ip_address: str,
        user_agent: str) -> dict | None:
    """
    Register a new device or update last_seen for an existing one.

    Uses PostgREST upsert (ON CONFLICT) to atomically create or update.
    Returns the device record dict on success, or None on error.
    """
    try:
        # Try to find existing device
        resp = shared._postgrest_session.get(
            f"{shared.POSTGREST_URL}/trusted_devices",
            params={
                'device_token': f'eq.{device_token}',
                'select': 'id,device_token,user_id,device_name,ip_address,user_agent,is_trusted,first_seen,last_seen'
            },
            timeout=5
        )
        if resp.status_code == 200 and resp.json():
            # Device exists — update last_seen, ip, user_agent, and user_id
            device = resp.json()[0]
            update_data: dict[str, Any] = {
                'last_seen': datetime.utcnow().isoformat(),
                'ip_address': ip_address,
                'user_agent': user_agent
            }
            if user_id:
                update_data['user_id'] = user_id
            shared._postgrest_session.patch(
                f"{shared.POSTGREST_URL}/trusted_devices",
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
        resp = shared._postgrest_session.post(
            f"{shared.POSTGREST_URL}/trusted_devices",
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


# ---------------------------------------------------------------------------
# Camera access helpers
# ---------------------------------------------------------------------------

def _get_allowed_camera_serials(user) -> set[str] | None:
    """
    Return the set of camera serials a user is allowed to access.

    Returns None if the user has unrestricted access (admin or no access
    rules configured in the DB).  Returns a set of serial strings if the
    user has a restricted access list.
    """
    if user.role == 'admin':
        return None  # No restriction

    try:
        response = shared._postgrest_session.get(
            f"{shared.POSTGREST_URL}/user_camera_access",
            params={'user_id': f'eq.{user.id}', 'select': 'camera_serial,allowed'},
            timeout=5
        )
        if response.status_code == 200:
            access_list = response.json()
            if not access_list:
                return None  # No restriction rules at all
            return set(a['camera_serial'] for a in access_list if a.get('allowed', False))
    except requests.RequestException:
        pass

    return None  # Default: unrestricted on error


def _filter_cameras(cameras: dict, allowed_serials: set[str] | None) -> dict:
    """
    Filter a camera dict to only include allowed serials.

    If allowed_serials is None, all cameras are returned (no restriction).
    """
    if allowed_serials is None:
        return cameras
    return {
        serial: info for serial, info in cameras.items()
        if serial in allowed_serials
    }


# ---------------------------------------------------------------------------
# Trusted-network helpers
# ---------------------------------------------------------------------------

def _get_client_ip() -> str:
    """
    Return the real client IP, accounting for the nginx reverse proxy.

    Nginx sets X-Forwarded-For to the actual client IP.  Falls back to
    request.remote_addr (which is the nginx container IP behind the proxy).
    """
    forwarded = request.headers.get('X-Forwarded-For', '')
    if forwarded:
        # X-Forwarded-For can be comma-separated; first entry is the real client
        return forwarded.split(',')[0].strip()
    return request.remote_addr


def _is_same_subnet(client_ip: str) -> bool:
    """
    Return True if client_ip is on the same /24 as the NVR host.

    Only considers private IP ranges (RFC 1918).  The host IP is read from
    the NVR_LOCAL_HOST_IP environment variable set by start.sh.
    """
    import ipaddress
    try:
        client = ipaddress.ip_address(client_ip)
        if not client.is_private:
            return False
        host_ip = os.environ.get('NVR_LOCAL_HOST_IP', '')
        if not host_ip:
            return False
        client_net = ipaddress.ip_network(f"{client_ip}/24", strict=False)
        host_net = ipaddress.ip_network(f"{host_ip}/24", strict=False)
        return client_net == host_net
    except (ValueError, TypeError):
        return False


# Cache for trusted network setting (avoid DB query on every request)
_trusted_network_cache: dict = {'enabled': None, 'checked_at': 0}


def _is_trusted_network_enabled() -> bool:
    """
    Check whether the admin has enabled the 'Trust this network' setting.

    Result is cached for 30 seconds to avoid hammering the DB on every
    request.  Falls back to False on any DB error.
    """
    import psycopg2

    now = _time.time()
    if (_trusted_network_cache['enabled'] is not None
            and (now - _trusted_network_cache['checked_at']) < 30):
        return _trusted_network_cache['enabled']

    try:
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


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------

def _get_bool(name: str, default: bool | None = None) -> bool | None:
    """
    Return True/False for a boolean env var, or default if unset.

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
    return default if default is not None else None


def _get_int(name: str, default: int) -> int:
    """Return int value of env var or default."""
    val = os.getenv(name)
    if val is None or val == "":
        return default
    try:
        return int(val)
    except Exception:
        return default


def _resolve_ui_vs_watchdog() -> tuple[bool | None, bool | None]:
    """
    DEPRECATED: Old mutual-exclusion policy for UI Health vs per-stream watchdog.

    As of Jan 4, 2026:
    - Old per-stream watchdog (ENABLE_WATCHDOG) removed from StreamManager
    - New StreamWatchdog service COEXISTS with UI Health
    - UI Health: detects browser/network issues (frontend)
    - StreamWatchdog: detects server/camera issues (backend)

    Kept for backward compatibility.  Returns (ui_health_enabled, watchdog_enabled).
    """
    ui_enabled = _get_bool("NVR_UI_HEALTH_ENABLED", default=True)
    wd_enabled = _get_bool("NVR_STREAM_WATCHDOG_ENABLED", default=False)
    return ui_enabled, wd_enabled


def _ui_health_from_env() -> dict:
    """
    Build UI health settings dict from environment variables and cameras.json.

    Priority: cameras.json global_settings > .env variables.
    """
    settings: dict = {
        'uiHealthEnabled': _get_bool("NVR_UI_HEALTH_ENABLED", True),
        'sampleIntervalMs': _get_int("NVR_UI_HEALTH_SAMPLE_INTERVAL_MS", 2000),
        'staleAfterMs': _get_int("NVR_UI_HEALTH_STALE_AFTER_MS", 20000),
        'consecutiveBlankNeeded': _get_int("NVR_UI_HEALTH_CONSECUTIVE_BLANK_NEEDED", 10),
        'cooldownMs': _get_int("NVR_UI_HEALTH_COOLDOWN_MS", 30000),
        'warmupMs': _get_int("NVR_UI_HEALTH_WARMUP_MS", 60000),
        'maxAttempts': _get_int("NVR_UI_HEALTH_MAX_ATTEMPTS", 10),
        'blankThreshold': {
            'avg': _get_int("NVR_UI_HEALTH_BLANK_AVG", 12),
            'std': _get_int("NVR_UI_HEALTH_BLANK_STD", 5)
        }
    }

    # Override with cameras.json global settings (flattens blankThreshold)
    try:
        global_settings = shared.camera_repo.cameras_data.get('ui_health_global_settings', {})
        if global_settings:
            key_mapping = {
                'UI_HEALTH_ENABLED': 'uiHealthEnabled',
                'UI_HEALTH_SAMPLE_INTERVAL_MS': 'sampleIntervalMs',
                'UI_HEALTH_STALE_AFTER_MS': 'staleAfterMs',
                'UI_HEALTH_CONSECUTIVE_BLANK_NEEDED': 'consecutiveBlankNeeded',
                'UI_HEALTH_COOLDOWN_MS': 'cooldownMs',
                'UI_HEALTH_WARMUP_MS': 'warmupMs',
                'UI_HEALTH_BLANK_AVG': 'blankAvg',
                'UI_HEALTH_BLANK_STD': 'blankStd',
                'UI_HEALTH_MAX_ATTEMPTS': 'maxAttempts'
            }
            for json_key, settings_key in key_mapping.items():
                if json_key in global_settings:
                    settings[settings_key] = global_settings[json_key]
    except Exception as e:
        print(f"Warning: Could not load global UI health settings from cameras.json: {e}")

    return settings
