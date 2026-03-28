#!/usr/bin/env python3
"""
Camera Configuration Auto-Sync Module

Runs at application startup to keep cameras.json and the database in sync.
New cameras added to cameras.json are automatically migrated to the database.
Cameras in the database but missing from cameras.json are logged as warnings.

This module does NOT delete cameras from the database - that must be done
explicitly. cameras.json remains the canonical source for new camera discovery
and reset operations.
"""

import json
import os
import logging
import requests
from typing import Dict, Set, Tuple

logger = logging.getLogger(__name__)

POSTGREST_URL = os.getenv('NVR_POSTGREST_URL', 'http://postgrest:3001')


# Fields that map directly from cameras.json device to DB columns
DIRECT_FIELDS = [
    'serial', 'name', 'type', 'camera_id', 'host', 'mac', 'packager_path',
    'stream_type', 'streaming_hub', 'go2rtc_source', 'rtsp_alias',
    'max_connections', 'onvif_port', 'power_supply', 'hidden',
    'ui_health_monitor', 'reversed_pan', 'reversed_tilt', 'notes',
    'power_supply_device_id',
]

# Fields stored as JSONB columns
JSONB_FIELDS = [
    'capabilities', 'll_hls', 'mjpeg_snap', 'neolink', 'player_settings',
    'rtsp_input', 'rtsp_output', 'two_way_audio', 'power_cycle_on_failure',
]

# Boolean fields that may be stored as strings in cameras.json
BOOLEAN_FIELDS = {
    'hidden': False,
    'ui_health_monitor': True,
    'reversed_pan': False,
    'reversed_tilt': False,
    'true_mjpeg': False,
}


def _parse_bool(value, default=False):
    """Parse boolean value from cameras.json (handles string 'false'/'true')."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ('true', '1', 'yes')
    return bool(value)


def _build_camera_record(serial: str, config: dict) -> dict:
    """
    Transform a cameras.json device entry into a database record.

    Args:
        serial: Camera serial number
        config: Camera config dict from cameras.json

    Returns:
        dict suitable for PostgREST INSERT
    """
    record = {'serial': serial}

    for field in DIRECT_FIELDS:
        if field in config and field != 'serial':
            record[field] = config[field]

    for field, default in BOOLEAN_FIELDS.items():
        if field in config:
            record[field] = _parse_bool(config[field], default)
        else:
            record[field] = default

    for field in JSONB_FIELDS:
        if field in config:
            record[field] = config[field]

    # Collect unmapped fields into extra_config
    all_known = set(DIRECT_FIELDS + JSONB_FIELDS + list(BOOLEAN_FIELDS.keys()))
    all_known.update(['serial', 'id', 'last_updated', '_max_connections_note'])
    extra = {k: v for k, v in config.items() if k not in all_known}
    if extra:
        record['extra_config'] = extra

    # Defaults for required fields
    record.setdefault('name', serial)
    record.setdefault('type', 'unknown')
    record.setdefault('stream_type', 'LL_HLS')

    return record


def get_db_camera_serials() -> Set[str]:
    """
    Fetch all camera serial numbers currently in the database.

    Returns:
        Set of serial strings, or empty set on error
    """
    try:
        response = requests.get(
            f"{POSTGREST_URL}/cameras",
            params={'select': 'serial'},
            timeout=5
        )
        if response.status_code == 200:
            return {row['serial'] for row in response.json()}
        logger.warning(f"Failed to fetch cameras from DB: HTTP {response.status_code}")
        return set()
    except requests.RequestException as e:
        logger.warning(f"Cannot reach PostgREST for camera sync: {e}")
        return set()


def get_all_db_cameras() -> Dict[str, dict]:
    """
    Fetch all camera records from the database.

    Returns:
        Dict mapping serial -> camera record, or empty dict on error
    """
    try:
        response = requests.get(
            f"{POSTGREST_URL}/cameras",
            timeout=10
        )
        if response.status_code == 200:
            return {row['serial']: row for row in response.json()}
        logger.warning(f"Failed to fetch cameras from DB: HTTP {response.status_code}")
        return {}
    except requests.RequestException as e:
        logger.warning(f"Cannot reach PostgREST for camera fetch: {e}")
        return {}


def _insert_camera(record: dict) -> bool:
    """Insert a single camera record into the database via PostgREST."""
    try:
        response = requests.post(
            f"{POSTGREST_URL}/cameras",
            json=record,
            headers={
                'Prefer': 'resolution=ignore-duplicates,return=representation',
                'Content-Type': 'application/json',
            },
            timeout=10
        )
        return response.status_code in (200, 201, 409)
    except requests.RequestException as e:
        logger.error(f"Failed to insert camera {record.get('serial')}: {e}")
        return False



def sync_cameras_json_to_db(cameras_json_path: str = './config/cameras.json') -> Tuple[int, int, int]:
    """
    Sync cameras.json with the database at app startup.

    - New cameras in JSON but not DB: auto-migrate to DB
    - Cameras in DB but not JSON: log warning (do NOT delete)
    - Cameras in both: no action (DB is source of truth for runtime)

    Args:
        cameras_json_path: Path to cameras.json file

    Returns:
        Tuple of (migrated_count, already_in_db_count, warning_count)
    """
    # Load cameras.json
    if not os.path.exists(cameras_json_path):
        logger.warning(f"cameras.json not found at {cameras_json_path}, skipping sync")
        return (0, 0, 0)

    try:
        with open(cameras_json_path, 'r') as f:
            cameras_data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Failed to read cameras.json: {e}")
        return (0, 0, 0)

    json_devices = cameras_data.get('devices', {})
    if not json_devices:
        logger.warning("No devices found in cameras.json")
        return (0, 0, 0)

    # Fetch current DB state
    db_serials = get_db_camera_serials()
    json_serials = set(json_devices.keys())

    # Find differences
    new_in_json = json_serials - db_serials
    only_in_db = db_serials - json_serials

    migrated = 0
    warnings = 0

    # Auto-migrate new cameras from JSON to DB
    if new_in_json:
        logger.info(f"Found {len(new_in_json)} new camera(s) in cameras.json, migrating to DB...")
        for serial in sorted(new_in_json):
            config = json_devices[serial]
            record = _build_camera_record(serial, config)
            if _insert_camera(record):
                logger.info(f"  Migrated: {serial} ({record.get('name', 'unknown')})")
                migrated += 1
            else:
                logger.error(f"  Failed to migrate: {serial}")

    # Warn about cameras in DB but not in JSON
    if only_in_db:
        logger.warning(
            f"{len(only_in_db)} camera(s) in database but not in cameras.json: "
            f"{', '.join(sorted(only_in_db))}"
        )
        warnings = len(only_in_db)

    # For cameras that exist in both JSON and DB, seed infrastructure fields
    # (go2rtc_source, streaming_hub) from cameras.json ONLY when the DB value
    # is NULL or empty. This ensures:
    #   - First-time setup: cameras.json seeds the DB
    #   - UI changes: preserved across restarts (DB is source of truth)
    #   - cameras.json edits: only take effect if DB field was never set
    INFRA_FIELDS = ['go2rtc_source', 'streaming_hub']
    existing = json_serials & db_serials
    infra_updated = 0

    # Fetch current DB values for infra fields
    try:
        resp = requests.get(
            f"{POSTGREST_URL}/cameras",
            params={'select': ','.join(['serial'] + INFRA_FIELDS)},
            timeout=5
        )
        db_infra = {row['serial']: row for row in resp.json()} if resp.status_code == 200 else {}
    except Exception:
        db_infra = {}

    for serial in existing:
        config = json_devices[serial]
        db_row = db_infra.get(serial, {})
        updates = {}
        for field in INFRA_FIELDS:
            json_val = config.get(field)
            db_val = db_row.get(field)
            # Only seed from JSON if DB is NULL/empty and JSON has a value
            if (db_val is None or db_val == '') and json_val:
                updates[field] = json_val
        if updates:
            try:
                resp = requests.patch(
                    f"{POSTGREST_URL}/cameras",
                    params={'serial': f'eq.{serial}'},
                    json=updates,
                    timeout=5
                )
                if resp.status_code in (200, 204):
                    infra_updated += 1
                    logger.info(f"  Seeded infra fields for {serial}: {list(updates.keys())}")
            except Exception as e:
                logger.warning(f"Failed to seed infra fields for {serial}: {e}")

    already = len(existing)

    if migrated == 0 and warnings == 0 and infra_updated == 0:
        logger.info(f"Camera sync complete: {already} cameras in sync, no changes needed")
    else:
        logger.info(
            f"Camera sync complete: {migrated} migrated, {already} already in DB, "
            f"{infra_updated} infra-field updates, {warnings} warnings"
        )

    return (migrated, already, warnings)


def force_sync_from_json(cameras_json_path: str = './config/cameras.json') -> int:
    """
    Force-update ALL camera records in the database from cameras.json.
    Used for reset operations when cameras.json is the canonical source.

    Args:
        cameras_json_path: Path to cameras.json file

    Returns:
        Number of cameras updated
    """
    if not os.path.exists(cameras_json_path):
        logger.error(f"cameras.json not found at {cameras_json_path}")
        return 0

    try:
        with open(cameras_json_path, 'r') as f:
            cameras_data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Failed to read cameras.json: {e}")
        return 0

    json_devices = cameras_data.get('devices', {})
    updated = 0

    for serial, config in json_devices.items():
        record = _build_camera_record(serial, config)
        try:
            response = requests.post(
                f"{POSTGREST_URL}/cameras",
                json=record,
                headers={
                    'Prefer': 'resolution=merge-duplicates,return=representation',
                    'Content-Type': 'application/json',
                },
                timeout=10
            )
            if response.status_code in (200, 201):
                updated += 1
        except requests.RequestException as e:
            logger.error(f"Failed to force-sync camera {serial}: {e}")

    logger.info(f"Force sync complete: {updated}/{len(json_devices)} cameras updated from JSON")
    return updated
