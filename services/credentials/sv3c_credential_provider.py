#!/usr/bin/env python3
"""
SV3C Credential Provider
Retrieves per-camera credentials from environment variables with generic fallback
"""

import os
import logging
from typing import Optional, Tuple
from .credential_provider import CredentialProvider

logger = logging.getLogger(__name__)


class SV3CCredentialProvider(CredentialProvider):
    """
    SV3C uses per-camera credentials with fallback to generic credentials
    
    Expected environment variables:
        Per-camera (priority):
            SV3C_USERNAME
            SV3C_PASSWORD
        
    """
    
    def __init__(self):
        logger.info("Initialized SV3C credential provider")
    
    def get_credentials(self, camera_id: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
        """
        Get credentials for specific SV3C camera
        
        Args:
            camera_id: Camera ID/serial from cameras.json (e.g., "C6F0SgZ0N0PoL2")
                      Will be used to construct env var names like SV3C_USERNAME
            
        Returns:
            (username, password) tuple
        """
        username = None
        password = None
        
        # Try camera-specific credentials first
        if camera_id:
            # For SV3C, use hardcoded name since serial is not meaningful
            # User configured: SV3C_USERNAME, SV3C_PASSWORD
            username_var = "SV3C_USERNAME"
            password_var = "SV3C_PASSWORD"
            
            logger.debug(f"SV3C trying camera-specific credentials: {username_var}, {password_var}")
            
            username = os.getenv(username_var)
            password = os.getenv(password_var)
            
            if username and password:
                logger.debug(f"Using camera-specific credentials: {username_var}, {password_var}")
                return (username, password)
            else:
                logger.debug(f"Camera-specific credentials not found: {username_var}, {password_var}")
        
        # Fallback to generic SV3C credentials
        username = "admin"
        password = "01234567"
        
        if not self.validate_credentials(username, password):
            logger.warning(
                f"Missing SV3C credentials. Tried: "
                f"SV3C_USERNAME/PASSWORD, admin/01234567"
            )
        
        return (username, password)