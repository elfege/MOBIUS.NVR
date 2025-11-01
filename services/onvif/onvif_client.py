#!/usr/bin/env python3
"""
ONVIF Client - Base connection and authentication wrapper
Handles ONVIF camera connections with connection pooling and error handling
"""

import logging
from typing import Optional, Dict
from onvif import ONVIFCamera
from onvif.exceptions import ONVIFError
import zeep.exceptions

logger = logging.getLogger(__name__)


class ONVIFClient:
    """
    ONVIF connection manager with connection pooling
    
    Handles authentication, connection lifecycle, and provides
    access to ONVIF services (PTZ, Media, Device, etc.)
    """
    
    # Connection pool: {camera_serial: ONVIFCamera instance}
    _connections: Dict[str, ONVIFCamera] = {}
    
    # ONVIF default ports
    DEFAULT_PORT = 80
    WSDL_DIR = '/etc/onvif/wsdl/'  # Default WSDL directory for onvif-zeep
    
    @classmethod
    def get_camera(cls, 
                   host: str, 
                   port: int,
                   username: str, 
                   password: str,
                   camera_serial: str,
                   wsdl_dir: Optional[str] = None) -> Optional[ONVIFCamera]:
        """
        Get or create ONVIF camera connection (with connection pooling)
        
        Args:
            host: Camera IP address
            port: ONVIF port (usually 80)
            username: ONVIF username
            password: ONVIF password
            camera_serial: Unique camera identifier for connection pool
            wsdl_dir: Custom WSDL directory (optional)
            
        Returns:
            ONVIFCamera instance or None if connection fails
        """
        # Return cached connection if exists
        if camera_serial in cls._connections:
            logger.debug(f"Reusing ONVIF connection for {camera_serial}")
            return cls._connections[camera_serial]
        
        # Create new connection
        try:
            logger.info(f"Creating new ONVIF connection to {host}:{port}")
            
            wsdl = wsdl_dir or cls.WSDL_DIR
            
            camera = ONVIFCamera(
                host=host,
                port=port,
                user=username,
                passwd=password,
                wsdl_dir=wsdl
            )
            
            # Test connection by getting device information
            device_service = camera.create_devicemgmt_service()
            device_info = device_service.GetDeviceInformation()
            
            logger.info(
                f"ONVIF connection established: {device_info.Manufacturer} "
                f"{device_info.Model} (FW: {device_info.FirmwareVersion})"
            )
            
            # Cache connection
            cls._connections[camera_serial] = camera
            
            return camera
            
        except ONVIFError as e:
            logger.error(f"ONVIF connection failed for {host}:{port} - {e}")
            return None
        except zeep.exceptions.Fault as e:
            logger.error(f"ONVIF SOAP fault for {host}:{port} - {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error connecting to {host}:{port} - {e}")
            return None
    
    @classmethod
    def close_camera(cls, camera_serial: str) -> bool:
        """
        Close and remove camera connection from pool
        
        Args:
            camera_serial: Camera identifier
            
        Returns:
            True if connection was closed, False if not found
        """
        if camera_serial in cls._connections:
            logger.info(f"Closing ONVIF connection for {camera_serial}")
            del cls._connections[camera_serial]
            return True
        return False
    
    @classmethod
    def close_all(cls) -> int:
        """
        Close all cached ONVIF connections
        
        Returns:
            Number of connections closed
        """
        count = len(cls._connections)
        cls._connections.clear()
        logger.info(f"Closed {count} ONVIF connections")
        return count
    
    @classmethod
    def get_ptz_service(cls, camera: ONVIFCamera):
        """
        Get PTZ service from camera
        
        Args:
            camera: ONVIFCamera instance
            
        Returns:
            PTZ service or None if not available
        """
        try:
            return camera.create_ptz_service()
        except Exception as e:
            logger.error(f"Failed to create PTZ service: {e}")
            return None
    
    @classmethod
    def get_media_service(cls, camera: ONVIFCamera):
        """
        Get Media service from camera (for profiles, stream URIs, etc.)
        
        Args:
            camera: ONVIFCamera instance
            
        Returns:
            Media service or None if not available
        """
        try:
            return camera.create_media_service()
        except Exception as e:
            logger.error(f"Failed to create Media service: {e}")
            return None
    
    @classmethod
    def get_device_service(cls, camera: ONVIFCamera):
        """
        Get Device service from camera (for device info, capabilities, etc.)
        
        Args:
            camera: ONVIFCamera instance
            
        Returns:
            Device service or None if not available
        """
        try:
            return camera.create_devicemgmt_service()
        except Exception as e:
            logger.error(f"Failed to create Device service: {e}")
            return None
    
    @classmethod
    def get_profile_token(cls, camera: ONVIFCamera, profile_index: int = 0) -> Optional[str]:
        """
        Get media profile token (required for most ONVIF operations)
        
        Args:
            camera: ONVIFCamera instance
            profile_index: Profile index (default: 0 = first profile)
            
        Returns:
            Profile token string or None if not available
        """
        try:
            media_service = cls.get_media_service(camera)
            if not media_service:
                return None
            
            profiles = media_service.GetProfiles()
            
            if not profiles or len(profiles) <= profile_index:
                logger.warning(f"Profile index {profile_index} not available")
                return None
            
            token = profiles[profile_index].token
            logger.debug(f"Got profile token: {token}")
            return token
            
        except Exception as e:
            logger.error(f"Failed to get profile token: {e}")
            return None


# Module-level convenience function
def create_onvif_camera(host: str, 
                       port: int, 
                       username: str, 
                       password: str,
                       camera_serial: str) -> Optional[ONVIFCamera]:
    """
    Convenience function to create ONVIF camera connection
    
    Args:
        host: Camera IP address
        port: ONVIF port
        username: ONVIF username
        password: ONVIF password
        camera_serial: Unique camera identifier
        
    Returns:
        ONVIFCamera instance or None
    """
    return ONVIFClient.get_camera(host, port, username, password, camera_serial)