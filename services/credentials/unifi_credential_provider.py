#!/usr/bin/env python3
"""
UniFi Protect Credential Provider
Retrieves console-level credentials from environment variables
"""

import os
import logging
from typing import Optional, Tuple
from .credential_provider import CredentialProvider

logger = logging.getLogger(__name__)


class UniFiCredentialProvider(CredentialProvider):
    """
    UniFi Protect uses console-level credentials
    All cameras share the same Protect console authentication
    
    Note: These credentials are NOT used in RTSP URLs
    They're only used for API access (snapshots, metadata, etc.)
    
    Expected environment variables:
        NVR_PROTECT_USERNAME
        NVR_PROTECT_SERVER_PASSWORD
    """
    
    def __init__(self):
        logger.info("Initialized UniFi Protect credential provider")
    
    def get_credentials(self, identifier: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
        """
        Get console-level credentials for UniFi Protect
        
        Args:
            identifier: Ignored (console-level credentials apply to all cameras)
            
        Returns:
            (username, password) tuple for Protect console
        """
        username = os.getenv("NVR_PROTECT_USERNAME")
        password = os.getenv("NVR_PROTECT_SERVER_PASSWORD")

        if not self.validate_credentials(username, password):
            logger.warning(
                "Missing or invalid UniFi Protect credentials. "
                "Expected env vars: NVR_PROTECT_USERNAME, NVR_PROTECT_SERVER_PASSWORD"
            )
        
        return (username, password)