#!/usr/bin/env python3
"""
Eufy Credential Provider
Retrieves per-camera credentials from database, with env var fallback.
"""

import os
import logging
from typing import Optional, Tuple
from .credential_provider import CredentialProvider
from . import credential_db_service as cred_db

logger = logging.getLogger(__name__)


class EufyCredentialProvider(CredentialProvider):
    """
    Eufy uses per-camera credentials.
    Each camera has unique username/password for RTSP access.

    Lookup order:
        1. Database (camera_credentials table, keyed by serial)
        2. Environment variable fallback: NVR_EUFY_CAMERA_{SERIAL}_USERNAME/PASSWORD
    """

    def __init__(self):
        logger.info("Initialized Eufy credential provider")

    def get_credentials(self, identifier: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
        """
        Get credentials for specific Eufy camera.

        Args:
            identifier: Camera serial number (REQUIRED for Eufy)

        Returns:
            (username, password) tuple
        """
        if not identifier:
            raise ValueError("Eufy credential provider requires camera serial number")

        camera_serial = identifier

        # Try database first
        username, password = cred_db.get_credential(camera_serial, 'camera')
        if self.validate_credentials(username, password):
            return (username, password)

        # Fall back to environment variables (legacy support / migration period)
        username_var = f"NVR_EUFY_CAMERA_{camera_serial}_USERNAME"
        password_var = f"NVR_EUFY_CAMERA_{camera_serial}_PASSWORD"
        username = os.getenv(username_var)
        password = os.getenv(password_var)

        if self.validate_credentials(username, password):
            logger.debug(
                f"Eufy camera {camera_serial}: loaded from env vars (not yet in DB)"
            )
            return (username, password)

        logger.warning(
            f"Missing credentials for Eufy camera {camera_serial}. "
            f"Add via camera settings UI or set env vars: {username_var}, {password_var}"
        )
        return (None, None)

    def get_bridge_credentials(self) -> Tuple[Optional[str], Optional[str]]:
        """
        Get credentials for Eufy bridge (used for PTZ control).

        Returns:
            (username, password) tuple for bridge authentication
        """
        # Try database first
        username, password = cred_db.get_credential('eufy_bridge', 'service')
        if self.validate_credentials(username, password):
            return (username, password)

        # Fall back to environment variables
        username = os.getenv("NVR_EUFY_BRIDGE_USERNAME")
        password = os.getenv("NVR_EUFY_BRIDGE_PASSWORD")

        if not self.validate_credentials(username, password):
            logger.warning(
                "Missing Eufy bridge credentials. "
                "Add via settings UI or set env vars: NVR_EUFY_BRIDGE_USERNAME, NVR_EUFY_BRIDGE_PASSWORD"
            )

        return (username, password)
