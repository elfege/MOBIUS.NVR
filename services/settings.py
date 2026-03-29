#!/usr/bin/env python3
"""
Unified Settings Manager — single point of access for all NVR settings.

Consolidates 4 different DB access patterns into one class:
- Global settings (nvr_settings table)
- Per-camera settings (cameras table — direct columns + extra_config JSONB)
- Per-user preferences (user_camera_preferences table)

PostgREST upsert with 409 PATCH fallback handled exactly once in _upsert().
Credentials stay in credential_db_service (encryption is a separate concern).

Usage:
    from services.settings import Settings
    settings = Settings(postgrest_url)
    settings.set_global('streaming_hub_global', 'go2rtc')
    settings.set_camera(serial, 'streaming_hub', 'go2rtc')
    settings.set_user_preference(user_id, serial, 'preferred_stream_type', 'WEBRTC')
"""

import os
import logging
import requests
from typing import Optional, Dict, List, Any

logger = logging.getLogger(__name__)

POSTGREST_URL = os.getenv('NVR_POSTGREST_URL', 'http://postgrest:3001')

# Direct DB columns in the cameras table (vs extra_config JSONB).
# Must match camera_repository.py's db_columns set.
CAMERA_DIRECT_COLUMNS = {
    'serial', 'name', 'type', 'camera_id', 'host', 'mac',
    'packager_path', 'stream_type', 'streaming_hub', 'go2rtc_source',
    'rtsp_alias', 'max_connections', 'onvif_port', 'power_supply',
    'hidden', 'ui_health_monitor', 'reversed_pan', 'reversed_tilt',
    'notes', 'power_supply_device_id', 'true_mjpeg', 'capabilities',
    'll_hls', 'mjpeg_snap', 'neolink', 'player_settings',
    'rtsp_input', 'rtsp_output', 'two_way_audio',
    'power_cycle_on_failure',
}


class Settings:
    """
    Unified settings manager for the NVR system.

    Provides get/set methods for three scopes:
    - Global: nvr_settings table (key/value pairs)
    - Camera: cameras table (per-camera config)
    - User:   user_camera_preferences table (per-user per-camera prefs)

    All DB writes go through _upsert() which handles the PostgREST
    merge-duplicates + PATCH-on-409 pattern exactly once.
    """

    def __init__(self, postgrest_url: str = None):
        self._url = postgrest_url or POSTGREST_URL
        self._session = requests.Session()
        self._session.headers.update({
            'Content-Type': 'application/json',
            'Prefer': 'return=representation'
        })

    # =====================================================================
    #  Internal: PostgREST operations with 409 upsert fallback
    # =====================================================================

    def _get(self, table: str, filters: Dict[str, str],
             select: str = '*', single: bool = False) -> Any:
        """
        GET rows from a PostgREST table with filters.

        Args:
            table: Table name (e.g. 'cameras', 'nvr_settings')
            filters: Dict of column=value filters (e.g. {'serial': 'eq.ABC123'})
            select: Column selection (PostgREST select syntax)
            single: If True, return first row or None instead of list

        Returns:
            List of row dicts, or single row dict if single=True, or None
        """
        try:
            params = dict(filters)
            params['select'] = select
            resp = self._session.get(
                f"{self._url}/{table}",
                params=params,
                timeout=5
            )
            if resp.status_code == 200:
                rows = resp.json()
                if single:
                    return rows[0] if rows else None
                return rows
            else:
                logger.warning(f"[Settings] GET {table} returned {resp.status_code}")
                return None if single else []
        except Exception as e:
            logger.error(f"[Settings] GET {table} failed: {e}")
            return None if single else []

    def _patch(self, table: str, filters: Dict[str, str],
               data: Dict) -> bool:
        """
        PATCH (update) rows matching filters.

        Args:
            table: Table name
            filters: PostgREST filter params (e.g. {'serial': 'eq.ABC123'})
            data: Fields to update

        Returns:
            True if successful
        """
        try:
            resp = self._session.patch(
                f"{self._url}/{table}",
                params=filters,
                json=data,
                timeout=5
            )
            if resp.status_code in (200, 204):
                return True
            logger.error(f"[Settings] PATCH {table} returned {resp.status_code}: {resp.text}")
            return False
        except Exception as e:
            logger.error(f"[Settings] PATCH {table} failed: {e}")
            return False

    def _upsert(self, table: str, data: Dict,
                conflict_filters: Dict[str, str] = None) -> bool:
        """
        Insert-or-update a row. Handles PostgREST merge-duplicates header
        AND falls back to PATCH on 409 conflict.

        This is the SINGLE place where the 409 upsert bug is handled.

        Args:
            table: Table name
            data: Full row data for INSERT
            conflict_filters: PostgREST filter params for PATCH fallback
                              (e.g. {'key': 'eq.streaming_hub_global'})

        Returns:
            True if row was inserted or updated
        """
        try:
            # Attempt INSERT with merge-duplicates
            headers = {'Prefer': 'resolution=merge-duplicates,return=representation'}
            resp = self._session.post(
                f"{self._url}/{table}",
                json=data,
                headers=headers,
                timeout=5
            )
            if resp.status_code in (200, 201):
                return True

            # 409 = conflict (row exists) → fall back to PATCH
            if resp.status_code == 409 and conflict_filters:
                return self._patch(table, conflict_filters, data)

            logger.error(f"[Settings] UPSERT {table} returned {resp.status_code}: {resp.text}")
            return False
        except Exception as e:
            logger.error(f"[Settings] UPSERT {table} failed: {e}")
            return False

    # =====================================================================
    #  Global settings (nvr_settings table)
    # =====================================================================

    def get_global(self, key: str, default: str = None) -> Optional[str]:
        """Get a global setting by key from nvr_settings."""
        row = self._get('nvr_settings',
                        filters={'key': f'eq.{key}'},
                        select='value',
                        single=True)
        if row:
            return row.get('value', default)
        return default

    def set_global(self, key: str, value: str) -> bool:
        """Set a global setting (upsert into nvr_settings)."""
        return self._upsert(
            'nvr_settings',
            data={'key': key, 'value': value},
            conflict_filters={'key': f'eq.{key}'}
        )

    def get_all_globals(self, exclude_keys: List[str] = None) -> Dict[str, str]:
        """Get all global settings as a dict. Optionally exclude sensitive keys."""
        rows = self._get('nvr_settings', filters={}, select='key,value')
        result = {}
        for row in (rows or []):
            k = row.get('key', '')
            if exclude_keys and k in exclude_keys:
                continue
            result[k] = row.get('value')
        return result

    # =====================================================================
    #  Per-camera settings (cameras table)
    # =====================================================================

    def get_camera_setting(self, serial: str, key: str) -> Any:
        """Get a single camera setting by key."""
        row = self._get('cameras',
                        filters={'serial': f'eq.{serial}'},
                        select=key,
                        single=True)
        return row.get(key) if row else None

    def set_camera(self, serial: str, key: str, value: Any) -> bool:
        """
        Set a single camera setting. Routes to direct column or extra_config
        based on CAMERA_DIRECT_COLUMNS.
        """
        if key in CAMERA_DIRECT_COLUMNS:
            return self._patch('cameras',
                               filters={'serial': f'eq.{serial}'},
                               data={key: value})
        else:
            # Update extra_config JSONB — need to merge, not overwrite
            current = self._get('cameras',
                                filters={'serial': f'eq.{serial}'},
                                select='extra_config',
                                single=True)
            extra = (current or {}).get('extra_config') or {}
            if not isinstance(extra, dict):
                extra = {}
            extra[key] = value
            return self._patch('cameras',
                               filters={'serial': f'eq.{serial}'},
                               data={'extra_config': extra})

    def set_camera_bulk(self, serial: str, updates: Dict) -> bool:
        """
        Set multiple camera settings at once. Separates direct columns
        from extra_config fields and writes efficiently.
        """
        direct = {k: v for k, v in updates.items() if k in CAMERA_DIRECT_COLUMNS}
        extra_keys = {k: v for k, v in updates.items() if k not in CAMERA_DIRECT_COLUMNS}

        success = True

        if direct:
            if not self._patch('cameras',
                               filters={'serial': f'eq.{serial}'},
                               data=direct):
                success = False

        if extra_keys:
            current = self._get('cameras',
                                filters={'serial': f'eq.{serial}'},
                                select='extra_config',
                                single=True)
            extra = (current or {}).get('extra_config') or {}
            if not isinstance(extra, dict):
                extra = {}
            extra.update(extra_keys)
            if not self._patch('cameras',
                               filters={'serial': f'eq.{serial}'},
                               data={'extra_config': extra}):
                success = False

        return success

    # =====================================================================
    #  Per-user preferences (user_camera_preferences table)
    # =====================================================================

    def get_user_preference(self, user_id: int, serial: str = None,
                            key: str = None) -> Any:
        """
        Get user preference(s).
        - If serial given: return preferences for that camera
        - If key also given: return just that field
        - If neither: return all preferences for user
        """
        filters = {'user_id': f'eq.{user_id}'}
        if serial:
            filters['camera_serial'] = f'eq.{serial}'

        rows = self._get('user_camera_preferences', filters=filters)
        if not rows:
            return None

        if serial and key:
            return rows[0].get(key) if rows else None
        elif serial:
            return rows[0] if rows else None
        else:
            return rows

    def set_user_preference(self, user_id: int, serial: str,
                            key: str, value: Any) -> bool:
        """Set a single user preference for a camera."""
        return self._upsert(
            'user_camera_preferences',
            data={
                'user_id': user_id,
                'camera_serial': serial,
                key: value
            },
            conflict_filters={
                'user_id': f'eq.{user_id}',
                'camera_serial': f'eq.{serial}'
            }
        )

    def set_user_preferences_bulk(self, user_id: int, serial: str,
                                  updates: Dict) -> bool:
        """Set multiple user preferences for a camera."""
        data = {'user_id': user_id, 'camera_serial': serial}
        data.update(updates)
        return self._upsert(
            'user_camera_preferences',
            data=data,
            conflict_filters={
                'user_id': f'eq.{user_id}',
                'camera_serial': f'eq.{serial}'
            }
        )
