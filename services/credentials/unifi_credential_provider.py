#!/usr/bin/env python3
"""
UniFi Protect Credential Provider
Retrieves console-level credentials from database, with env var fallback.
"""

import os
import logging
from typing import Optional, Tuple
from .credential_provider import CredentialProvider
from . import credential_db_service as cred_db

logger = logging.getLogger(__name__)


class UniFiCredentialProvider(CredentialProvider):
    """
    UniFi Protect credential provider.

    Handles two types of credentials:
        1. Console-level credentials (username/password for Protect API access)
           - key='unifi_protect', type='service'
        2. Per-camera RTSP token aliases (pre-authenticated RTSP tokens from Protect)
           - key=camera_id, type='camera', vendor='unifi'
           - Token alias stored in username field; password is placeholder

    Lookup order (both types):
        1. Database (camera_credentials table)
        2. Environment variable fallback
    """

    def __init__(self):
        logger.info("Initialized UniFi Protect credential provider")

    def get_credentials(self, identifier: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
        """
        Get console-level credentials for UniFi Protect.

        Args:
            identifier: Ignored (console-level credentials apply to all cameras)

        Returns:
            (username, password) tuple for Protect console
        """
        # Try database first
        username, password = cred_db.get_credential('unifi_protect', 'service')
        if self.validate_credentials(username, password):
            return (username, password)

        # Fall back to environment variables
        username = os.getenv("NVR_PROTECT_USERNAME")
        password = os.getenv("NVR_PROTECT_SERVER_PASSWORD")

        if not self.validate_credentials(username, password):
            logger.warning(
                "Missing UniFi Protect credentials. "
                "Add via settings UI or set env vars: NVR_PROTECT_USERNAME, NVR_PROTECT_SERVER_PASSWORD"
            )

        return (username, password)

    def get_token_alias(self, camera_id: str) -> Optional[str]:
        """
        Get the RTSP token alias for a specific UniFi camera.

        The Protect console assigns pre-authenticated RTSP tokens to each camera.
        These tokens are used in the RTSP URL path (no username/password needed).

        Lookup order:
            1. Database (camera_credentials table, key=camera_id, type='camera')
            2. Environment variable fallback: NVR_CAMERA_{camera_id}_TOKEN_ALIAS

        Args:
            camera_id: UniFi camera ID (e.g., '68d49398005cf203e400043f')

        Returns:
            Token alias string, or None if not found
        """
        if not camera_id:
            logger.error("get_token_alias called without camera_id")
            return None

        # Try database first (token alias stored in username field)
        token_alias, _ = cred_db.get_credential(camera_id, 'camera')
        if token_alias:
            logger.debug(f"UniFi token alias for {camera_id} loaded from database")
            return token_alias

        # Fall back to environment variable
        env_var = f"NVR_CAMERA_{camera_id}_TOKEN_ALIAS"
        token_alias = os.getenv(env_var)
        if token_alias:
            logger.debug(f"UniFi token alias for {camera_id} loaded from env var {env_var}")
            return token_alias

        logger.warning(
            f"Missing RTSP token alias for UniFi camera {camera_id}. "
            f"Add via camera settings UI or set env var: {env_var}"
        )
        return None
