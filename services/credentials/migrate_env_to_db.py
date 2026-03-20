#!/usr/bin/env python3
"""
One-time migration: Environment variables -> Database credentials

Reads camera credentials from environment variables (the legacy approach)
and stores them in the camera_credentials database table. This script is
idempotent — running it multiple times will upsert (not duplicate).

Called automatically by app.py at startup if credentials exist in env vars
but not yet in the database.

Credential mapping:
    Eufy cameras:     NVR_EUFY_CAMERA_{SERIAL}_USERNAME/PASSWORD -> per-camera
    Eufy bridge:      NVR_EUFY_BRIDGE_USERNAME/PASSWORD          -> service 'eufy_bridge'
    Reolink API:      NVR_REOLINK_API_USER/PASSWORD              -> service 'reolink_api'
    Reolink RTSP:     NVR_REOLINK_USERNAME/PASSWORD              -> service 'reolink_rtsp'
    UniFi Protect:    NVR_PROTECT_USERNAME/SERVER_PASSWORD        -> service 'unifi_protect'
    Amcrest:          NVR_AMCREST_LOBBY_USERNAME/PASSWORD         -> service 'amcrest'
    SV3C:             NVR_SV3C_USERNAME/PASSWORD                  -> service 'sv3c'
    UniFi Controller: NVR_UNIFI_CONTROLLER_USERNAME/PASSWORD      -> service 'unifi_controller'
"""

import os
import re
import logging

from . import credential_db_service as cred_db

logger = logging.getLogger(__name__)

# Pattern to match Eufy per-camera env vars: NVR_EUFY_CAMERA_{SERIAL}_USERNAME
_EUFY_CAMERA_PATTERN = re.compile(r'^NVR_EUFY_CAMERA_([A-Z0-9]+)_USERNAME$')


def _store_if_present(
    env_user: str,
    env_pass: str,
    credential_key: str,
    vendor: str,
    credential_type: str = 'service',
    label: str = ''
) -> bool:
    """
    Check if env vars are set; if so, store in DB.
    Returns True if credentials were migrated.
    """
    username = os.getenv(env_user)
    password = os.getenv(env_pass)
    if username and password:
        # Check if already in DB to avoid unnecessary writes
        existing_user, existing_pass = cred_db.get_credential(credential_key, credential_type)
        if existing_user and existing_pass:
            logger.debug(f"Credentials already in DB for {credential_key}, skipping migration")
            return False

        success = cred_db.store_credential(
            credential_key=credential_key,
            username=username,
            password=password,
            vendor=vendor,
            credential_type=credential_type,
            label=label or credential_key
        )
        if success:
            logger.info(f"Migrated {credential_key} ({vendor}/{credential_type}) from env to DB")
        return success
    return False


def migrate_all() -> int:
    """
    Migrate all known credential env vars to the database.

    Returns:
        Number of credentials migrated
    """
    migrated = 0

    # --- Service-level credentials ---

    # Reolink API (Baichuan motion detection)
    if _store_if_present(
        'NVR_REOLINK_API_USER', 'NVR_REOLINK_API_PASSWORD',
        'reolink_api', 'reolink', label='Reolink API credentials'
    ):
        migrated += 1

    # Reolink RTSP (streaming)
    if _store_if_present(
        'NVR_REOLINK_USERNAME', 'NVR_REOLINK_PASSWORD',
        'reolink_rtsp', 'reolink', label='Reolink RTSP credentials'
    ):
        migrated += 1

    # UniFi Protect console
    if _store_if_present(
        'NVR_PROTECT_USERNAME', 'NVR_PROTECT_SERVER_PASSWORD',
        'unifi_protect', 'unifi', label='UniFi Protect console'
    ):
        migrated += 1

    # UniFi Controller (POE power control)
    if _store_if_present(
        'NVR_UNIFI_CONTROLLER_USERNAME', 'NVR_UNIFI_CONTROLLER_PASSWORD',
        'unifi_controller', 'unifi', label='UniFi controller (POE)'
    ):
        migrated += 1

    # Amcrest (generic/lobby)
    if _store_if_present(
        'NVR_AMCREST_LOBBY_USERNAME', 'NVR_AMCREST_LOBBY_PASSWORD',
        'amcrest', 'amcrest', label='Amcrest default credentials'
    ):
        migrated += 1

    # SV3C
    if _store_if_present(
        'NVR_SV3C_USERNAME', 'NVR_SV3C_PASSWORD',
        'sv3c', 'sv3c', label='SV3C default credentials'
    ):
        migrated += 1

    # Eufy bridge (PTZ control)
    if _store_if_present(
        'NVR_EUFY_BRIDGE_USERNAME', 'NVR_EUFY_BRIDGE_PASSWORD',
        'eufy_bridge', 'eufy', label='Eufy bridge (PTZ)'
    ):
        migrated += 1

    # --- Per-camera Eufy credentials ---
    # Scan environment for NVR_EUFY_CAMERA_{SERIAL}_USERNAME pattern
    for env_key in sorted(os.environ.keys()):
        match = _EUFY_CAMERA_PATTERN.match(env_key)
        if match:
            serial = match.group(1)
            username = os.getenv(env_key)
            password = os.getenv(f"NVR_EUFY_CAMERA_{serial}_PASSWORD")
            if username and password:
                # Check if already in DB
                existing_user, _ = cred_db.get_credential(serial, 'camera')
                if existing_user:
                    continue

                success = cred_db.store_credential(
                    credential_key=serial,
                    username=username,
                    password=password,
                    vendor='eufy',
                    credential_type='camera',
                    label=f'Eufy camera {serial}'
                )
                if success:
                    migrated += 1

    if migrated > 0:
        logger.info(f"Credential migration complete: {migrated} credentials moved to database")
    else:
        logger.debug("No new credentials to migrate from env vars")

    return migrated
