#!/usr/bin/env python3
"""
SV3C Credential Provider
Retrieves credentials from database, with env var fallback.
"""

import os
import logging
from typing import Optional, Tuple
from .credential_provider import CredentialProvider
from . import credential_db_service as cred_db

logger = logging.getLogger(__name__)


class SV3CCredentialProvider(CredentialProvider):
    """
    SV3C uses brand-level credentials (all SV3C cameras share the same login).

    Lookup order:
        1. Database (camera_credentials table, key='sv3c', type='service')
        2. Environment variable: NVR_SV3C_USERNAME, NVR_SV3C_PASSWORD
    """

    def __init__(self):
        logger.info("Initialized SV3C credential provider")

    def get_credentials(self, camera_id: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
        """
        Get credentials for SV3C camera.

        Args:
            camera_id: Camera ID/serial from cameras.json (currently ignored;
                       all SV3C cameras share credentials)

        Returns:
            (username, password) tuple
        """
        # Try database first
        username, password = cred_db.get_credential('sv3c', 'service')
        if self.validate_credentials(username, password):
            return (username, password)

        # Fall back to environment variables
        username = os.getenv("NVR_SV3C_USERNAME")
        password = os.getenv("NVR_SV3C_PASSWORD")

        if self.validate_credentials(username, password):
            return (username, password)

        # No credentials found anywhere
        logger.warning(
            "Missing SV3C credentials. "
            "Add via camera settings UI or set env vars: NVR_SV3C_USERNAME, NVR_SV3C_PASSWORD"
        )
        return (None, None)
