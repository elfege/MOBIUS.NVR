#!/usr/bin/env python3
"""
Eufy Credential Provider
Retrieves per-camera credentials from environment variables
"""

import os
import logging
from typing import Optional, Tuple
from .credential_provider import CredentialProvider

logger = logging.getLogger(__name__)


class EufyCredentialProvider(CredentialProvider):
    """
    Eufy uses per-camera credentials
    Each camera has unique username/password for RTSP access
    
    Expected environment variables:
        EUFY_CAMERA_{SERIAL}_USERNAME
        EUFY_CAMERA_{SERIAL}_PASSWORD
    """
    
    def __init__(self):
        logger.info("Initialized Eufy credential provider")
    
    def get_credentials(self, identifier: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
        """
        Get credentials for specific Eufy camera
        
        Args:
            identifier: Camera serial number (REQUIRED for Eufy)
            
        Returns:
            (username, password) tuple
        """
        if not identifier:
            raise ValueError("Eufy credential provider requires camera serial number")
        
        camera_serial = identifier
        
        # Build environment variable names
        username_var = f"EUFY_CAMERA_{camera_serial}_USERNAME"
        password_var = f"EUFY_CAMERA_{camera_serial}_PASSWORD"
        
        # Retrieve from environment
        username = os.getenv(username_var)
        password = os.getenv(password_var)
        
        if not self.validate_credentials(username, password):
            logger.warning(
                f"Missing or invalid credentials for Eufy camera {camera_serial}. "
                f"Expected env vars: {username_var}, {password_var}"
            )
        
        return (username, password)
    
    def get_bridge_credentials(self) -> Tuple[Optional[str], Optional[str]]:
        """
        Get credentials for Eufy bridge (used for PTZ control)
        
        Returns:
            (username, password) tuple for bridge authentication
        """
        username = os.getenv("EUFY_BRIDGE_USERNAME")
        password = os.getenv("EUFY_BRIDGE_PASSWORD")
        
        if not self.validate_credentials(username, password):
            logger.warning(
                "Missing or invalid Eufy bridge credentials. "
                "Expected env vars: EUFY_BRIDGE_USERNAME, EUFY_BRIDGE_PASSWORD"
            )
        
        return (username, password)