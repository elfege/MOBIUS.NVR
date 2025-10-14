#!/usr/bin/env python3
"""
Abstract Credential Provider Interface
Each vendor implements this interface according to their auth model
"""

from abc import ABC, abstractmethod
from typing import Optional, Tuple


class CredentialProvider(ABC):
    """
    Abstract interface for credential retrieval
    Each vendor implements this based on their specific auth requirements
    """
    
    @abstractmethod
    def get_credentials(self, identifier: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
        """
        Get credentials for streaming
        
        Args:
            identifier: Vendor-specific identifier
                - Eufy: camera serial (required)
                - Reolink: ignored (console-level creds)
                - UniFi: ignored (console-level creds)
        
        Returns:
            Tuple of (username, password) or (None, None) if not found
        """
        pass
    
    def validate_credentials(self, username: Optional[str], password: Optional[str]) -> bool:
        """
        Check if credentials are valid (not None or empty)
        
        Args:
            username: Username to validate
            password: Password to validate
            
        Returns:
            True if both username and password are non-empty
        """
        return bool(username and password)