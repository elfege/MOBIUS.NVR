#!/usr/bin/env python3
"""
Reolink Credential Provider
Retrieves NVR-level credentials from database, with env var fallback.
"""

import os
import logging
from typing import Optional, Tuple
from .credential_provider import CredentialProvider
from . import credential_db_service as cred_db

logger = logging.getLogger(__name__)


class ReolinkCredentialProvider(CredentialProvider):
    """
    Reolink uses NVR-level credentials.
    All cameras connected to the same NVR share credentials.

    Lookup order:
        1. Database (camera_credentials table, service-level)
        2. Environment variable fallback

    Service keys:
        'reolink_api'  — API credentials (Baichuan motion detection)
        'reolink_rtsp' — RTSP streaming credentials
    """

    def __init__(self, use_api_credentials: bool = True):
        """
        Initialize Reolink credential provider.

        Args:
            use_api_credentials: If True, use API credentials (reolink_api)
                                 If False, use RTSP credentials (reolink_rtsp)
        """
        self.use_api_credentials = use_api_credentials
        self._db_key = 'reolink_api' if use_api_credentials else 'reolink_rtsp'
        logger.info(f"Initialized Reolink credential provider (API creds: {use_api_credentials})")

    def get_credentials(self, identifier: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
        """
        Get NVR-level credentials for Reolink.

        Args:
            identifier: Ignored (NVR-level credentials apply to all cameras)

        Returns:
            (username, password) tuple for Reolink NVR
        """
        # Try database first
        username, password = cred_db.get_credential(self._db_key, 'service')
        if self.validate_credentials(username, password):
            return (username, password)

        # Fall back to environment variables
        if self.use_api_credentials:
            username = os.getenv("NVR_REOLINK_API_USER")
            password = os.getenv("NVR_REOLINK_API_PASSWORD")
            var_names = "NVR_REOLINK_API_USER, NVR_REOLINK_API_PASSWORD"
        else:
            username = os.getenv("NVR_REOLINK_USERNAME")
            password = os.getenv("NVR_REOLINK_PASSWORD")
            var_names = "NVR_REOLINK_USERNAME, NVR_REOLINK_PASSWORD"

        if not self.validate_credentials(username, password):
            logger.warning(
                f"Missing Reolink credentials ({self._db_key}). "
                f"Add via settings UI or set env vars: {var_names}"
            )

        return (username, password)
