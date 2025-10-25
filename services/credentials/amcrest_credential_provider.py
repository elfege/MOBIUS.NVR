#!/usr/bin/env python3
"""
Amcrest Credential Provider
Retrieves per-camera credentials from environment variables with generic fallback
"""

import os
import logging
from typing import Optional, Tuple
from .credential_provider import CredentialProvider

logger = logging.getLogger(__name__)


class AmcrestCredentialProvider(CredentialProvider):
    """
    Amcrest uses per-camera credentials with fallback to generic credentials
    
    Expected environment variables:
        Per-camera (priority):
            {CAMERA_ID}_USERNAME  (e.g., AMCREST_LOBBY_USERNAME)
            {CAMERA_ID}_PASSWORD  (e.g., AMCREST_LOBBY_PASSWORD)
        
        Generic fallback:
            AMCREST_USERNAME
            AMCREST_PASSWORD
    """
    
    def __init__(self):
        logger.info("Initialized Amcrest credential provider")
    
    def get_credentials(self, camera_id: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
        """
        Get credentials for specific Amcrest camera
        
        Args:
            camera_id: Camera ID from cameras.json (e.g., "AMCREST_LOBBY")
            
        Returns:
            (username, password) tuple
        """
        username = None
        password = None
        
        # Try camera-specific credentials first
        if camera_id:
            username_var = f"{camera_id}_USERNAME"
            password_var = f"{camera_id}_PASSWORD"
            
            print(f"AMCREST username_var: {username_var}, password_var: {password_var}")
            
            username = os.getenv(username_var)
            password = os.getenv(password_var)
            
            print(f"AMCREST username: {username}, password: {password}")
            
            if username and password:
                logger.debug(f"Using camera-specific credentials: {username_var}, {password_var}")
                return (username, password)
            else:
                logger.debug(f"Camera-specific credentials not found: {username_var}, {password_var}")
        
        # Fallback to generic Amcrest credentials
        username = os.getenv("AMCREST_LOBBY_USERNAME")
        password = os.getenv("AMCREST_LOBBY_PASSWORD")
        
        if not self.validate_credentials(username, password):
            logger.warning(
                f"Missing Amcrest credentials. Tried: "
                f"{camera_id}_USERNAME/PASSWORD (if provided), AMCREST_USERNAME/PASSWORD"
            )
        
        return (username, password)