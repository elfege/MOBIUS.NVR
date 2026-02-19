#!/usr/bin/env python3
"""
Camera Configuration Migration: cameras.json → PostgreSQL Database

One-time migration script that reads config/cameras.json and populates the
cameras table via PostgREST. Idempotent: uses upsert so running multiple
times is safe.

Usage:
    # From inside the container:
    python scripts/migrate_cameras_to_db.py

    # From host (if port-forwarded):
    POSTGREST_URL=http://localhost:3001 python scripts/migrate_cameras_to_db.py

    # Force update existing records:
    python scripts/migrate_cameras_to_db.py --force
"""

import json
import os
import sys
import logging
import argparse
import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

POSTGREST_URL = os.getenv('POSTGREST_URL', 'http://postgrest:3001')
CAMERAS_JSON_PATH = os.getenv('CAMERAS_JSON_PATH', './config/cameras.json')

# Fields that map directly from cameras.json device to DB columns
DIRECT_FIELDS = [
    'serial', 'name', 'type', 'camera_id', 'host', 'mac', 'packager_path',
    'stream_type', 'rtsp_alias', 'max_connections', 'onvif_port',
    'power_supply', 'hidden', 'ui_health_monitor', 'reversed_pan',
    'reversed_tilt', 'notes', 'power_supply_device_id',
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


def parse_bool(value, default=False):
    """
    Parse boolean value from cameras.json.
    Handles strings like "false", "true", actual booleans, and None.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ('true', '1', 'yes')
    return bool(value)


def build_camera_record(serial, config):
    """
    Transform a cameras.json device entry into a database record.

    Args:
        serial: Camera serial number (dict key from cameras.json devices)
        config: Camera config dict from cameras.json

    Returns:
        dict: Record suitable for PostgREST INSERT/UPSERT
    """
    record = {'serial': serial}

    # Direct fields
    for field in DIRECT_FIELDS:
        if field in config and field != 'serial':
            record[field] = config[field]

    # Boolean fields: parse strings to actual booleans
    for field, default in BOOLEAN_FIELDS.items():
        if field in config:
            record[field] = parse_bool(config[field], default)
        else:
            record[field] = default

    # JSONB fields: copy as-is (PostgREST handles serialization)
    for field in JSONB_FIELDS:
        if field in config:
            record[field] = config[field]

    # Collect unmapped fields into extra_config
    all_known_fields = set(DIRECT_FIELDS + JSONB_FIELDS + list(BOOLEAN_FIELDS.keys()))
    all_known_fields.add('serial')
    all_known_fields.add('id')  # Some cameras have 'id' field (alias for camera_id)
    all_known_fields.add('last_updated')  # Per-camera timestamp
    all_known_fields.add('_max_connections_note')  # Documentation field

    extra = {}
    for key, value in config.items():
        if key not in all_known_fields:
            extra[key] = value

    if extra:
        record['extra_config'] = extra

    # Ensure required fields have defaults
    if 'name' not in record:
        record['name'] = serial
    if 'type' not in record:
        record['type'] = 'unknown'
    if 'stream_type' not in record:
        record['stream_type'] = 'LL_HLS'

    return record


def migrate_camera(record, force=False):
    """
    Insert or upsert a single camera record to the database via PostgREST.

    Args:
        record: Camera record dict
        force: If True, update existing records. If False, skip existing.

    Returns:
        tuple: (success: bool, action: str) where action is 'inserted', 'updated', or 'skipped'
    """
    serial = record['serial']

    if force:
        # Upsert: insert or update on conflict
        headers = {
            'Prefer': 'resolution=merge-duplicates,return=representation',
            'Content-Type': 'application/json',
        }
    else:
        # Insert only: skip on conflict
        headers = {
            'Prefer': 'resolution=ignore-duplicates,return=representation',
            'Content-Type': 'application/json',
        }

    try:
        response = requests.post(
            f"{POSTGREST_URL}/cameras",
            json=record,
            headers=headers,
            timeout=10
        )

        if response.status_code in (200, 201):
            rows = response.json()
            if rows:
                action = 'updated' if force else 'inserted'
                logger.info(f"  {action}: {serial} ({record.get('name', 'unknown')})")
                return True, action
            else:
                logger.info(f"  skipped (already exists): {serial}")
                return True, 'skipped'
        elif response.status_code == 409:
            logger.info(f"  skipped (conflict): {serial}")
            return True, 'skipped'
        else:
            logger.error(
                f"  FAILED: {serial} - HTTP {response.status_code}: {response.text}")
            return False, 'error'

    except requests.RequestException as e:
        logger.error(f"  FAILED: {serial} - {e}")
        return False, 'error'


def main():
    """Main migration entry point."""
    parser = argparse.ArgumentParser(description='Migrate cameras.json to database')
    parser.add_argument('--force', action='store_true',
                        help='Update existing records (default: skip existing)')
    parser.add_argument('--cameras-json', default=CAMERAS_JSON_PATH,
                        help=f'Path to cameras.json (default: {CAMERAS_JSON_PATH})')
    parser.add_argument('--postgrest-url', default=POSTGREST_URL,
                        help=f'PostgREST URL (default: {POSTGREST_URL})')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print records without inserting')
    args = parser.parse_args()

    postgrest_url = args.postgrest_url

    # Load cameras.json
    cameras_json_path = args.cameras_json
    if not os.path.exists(cameras_json_path):
        logger.error(f"cameras.json not found at: {cameras_json_path}")
        sys.exit(1)

    with open(cameras_json_path, 'r') as f:
        cameras_data = json.load(f)

    devices = cameras_data.get('devices', {})
    if not devices:
        logger.error("No devices found in cameras.json")
        sys.exit(1)

    logger.info(f"Found {len(devices)} cameras in {cameras_json_path}")
    logger.info(f"PostgREST URL: {postgrest_url}")
    logger.info(f"Mode: {'force update' if args.force else 'insert only (skip existing)'}")

    # Override module-level POSTGREST_URL for migrate_camera()
    global POSTGREST_URL
    POSTGREST_URL = postgrest_url

    # Build and migrate records
    stats = {'inserted': 0, 'updated': 0, 'skipped': 0, 'error': 0}

    for serial, config in devices.items():
        record = build_camera_record(serial, config)

        if args.dry_run:
            logger.info(f"  [DRY RUN] Would insert: {serial} ({record.get('name', 'unknown')})")
            logger.info(f"    Fields: {list(record.keys())}")
            stats['skipped'] += 1
            continue

        success, action = migrate_camera(record, force=args.force)
        stats[action] += 1

    # Summary
    logger.info("=" * 60)
    logger.info("Migration Summary:")
    logger.info(f"  Inserted: {stats['inserted']}")
    logger.info(f"  Updated:  {stats['updated']}")
    logger.info(f"  Skipped:  {stats['skipped']}")
    logger.info(f"  Errors:   {stats['error']}")
    logger.info("=" * 60)

    if stats['error'] > 0:
        sys.exit(1)


if __name__ == '__main__':
    main()
