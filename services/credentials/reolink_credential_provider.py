#!/usr/bin/env python3
"""
Reolink Credential Provider
Retrieves NVR-level credentials from environment variables
"""

import os
import logging
from typing import Optional, Tuple
from .credential_provider import CredentialProvider

logger = logging.getLogger(__name__)


class ReolinkCredentialProvider(CredentialProvider):
    """
    Reolink uses NVR-level credentials
    All cameras connected to the same NVR share credentials
    
    Expected environment variables:
        REOLINK_USERNAME
        REOLINK_PASSWORD
    """
    
    def __init__(self):
        logger.info("Initialized Reolink credential provider")
    
    def get_credentials(self, identifier: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
        """
        Get NVR-level credentials for Reolink
        
        Args:
            identifier: Ignored (NVR-level credentials apply to all cameras)
            
        Returns:
            (username, password) tuple for Reolink NVR
        """
        username = os.getenv("REOLINK_USERNAME")
        password = os.getenv("REOLINK_PASSWORD")
        
        if not self.validate_credentials(username, password):
            logger.warning(
                "Missing or invalid Reolink credentials. "
                "Expected env vars: REOLINK_USERNAME, REOLINK_PASSWORD"
            )
        
        return (username, password)