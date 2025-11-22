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
        Main credentials (for RTSP streaming):
            REOLINK_USERNAME
            REOLINK_PASSWORD
        
        API credentials (for Baichuan motion detection):
            REOLINK_API_USER
            REOLINK_API_PASSWORD
    """
    
    def __init__(self, use_api_credentials: bool = False):
        """
        Initialize Reolink credential provider.
        
        Args:
            use_api_credentials: If True, use REOLINK_API_USER/PASSWORD
                                 If False, use REOLINK_USERNAME/PASSWORD
        """
        self.use_api_credentials = use_api_credentials
        logger.info(f"Initialized Reolink credential provider (API creds: {use_api_credentials})")
    
    def get_credentials(self, identifier: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
        """
        Get NVR-level credentials for Reolink
        
        Args:
            identifier: Ignored (NVR-level credentials apply to all cameras)
            
        Returns:
            (username, password) tuple for Reolink NVR
        """
        if self.use_api_credentials:
            username = os.getenv("REOLINK_API_USER")
            password = os.getenv("REOLINK_API_PASSWORD")
            var_names = "REOLINK_API_USER, REOLINK_API_PASSWORD"
        else:
            username = os.getenv("REOLINK_USERNAME")
            password = os.getenv("REOLINK_PASSWORD")
            var_names = "REOLINK_USERNAME, REOLINK_PASSWORD"
        
        if not self.validate_credentials(username, password):
            logger.warning(
                f"Missing or invalid Reolink credentials. "
                f"Expected env vars: {var_names}"
            )
        
        return (username, password)