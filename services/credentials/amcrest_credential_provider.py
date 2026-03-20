#!/usr/bin/env python3
"""
Amcrest Credential Provider
Retrieves per-camera credentials from database, with env var fallback.
"""

import os
import logging
from typing import Optional, Tuple
from .credential_provider import CredentialProvider
from . import credential_db_service as cred_db

logger = logging.getLogger(__name__)


class AmcrestCredentialProvider(CredentialProvider):
    """
    Amcrest uses per-camera credentials with fallback to brand-level defaults.

    Lookup order:
        1. Database: per-camera (keyed by camera_id, type='camera')
        2. Database: brand service (key='amcrest', type='service')
        3. Environment variable: NVR_{CAMERA_ID}_USERNAME/PASSWORD
        4. Environment variable: NVR_AMCREST_LOBBY_USERNAME/PASSWORD (generic fallback)
    """

    def __init__(self):
        logger.info("Initialized Amcrest credential provider")

    def get_credentials(self, camera_id: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
        """
        Get credentials for specific Amcrest camera.

        Args:
            camera_id: Camera ID from cameras.json (e.g., "AMCREST_LOBBY")

        Returns:
            (username, password) tuple
        """
        # Try database: per-camera lookup
        if camera_id:
            username, password = cred_db.get_credential(camera_id, 'camera')
            if self.validate_credentials(username, password):
                return (username, password)

        # Try database: brand-level service credential
        username, password = cred_db.get_credential('amcrest', 'service')
        if self.validate_credentials(username, password):
            return (username, password)

        # Fall back to environment variables: per-camera
        if camera_id:
            username_var = f"NVR_{camera_id}_USERNAME"
            password_var = f"NVR_{camera_id}_PASSWORD"
            username = os.getenv(username_var)
            password = os.getenv(password_var)
            if self.validate_credentials(username, password):
                logger.debug(f"Using camera-specific env vars: {username_var}, {password_var}")
                return (username, password)

        # Fall back to environment variables: generic
        username = os.getenv("NVR_AMCREST_LOBBY_USERNAME")
        password = os.getenv("NVR_AMCREST_LOBBY_PASSWORD")

        if not self.validate_credentials(username, password):
            logger.warning(
                f"Missing Amcrest credentials for {camera_id or 'unknown'}. "
                f"Add via camera settings UI or set env vars."
            )

        return (username, password)
