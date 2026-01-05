#!/usr/bin/env python3
"""
ONVIF Client - Base connection and authentication wrapper
Handles ONVIF camera connections with connection pooling and error handling

Note: Some cameras (especially Reolink) share ONVIF port with RTSP, causing
intermittent BadStatusLine errors when RTSP interleaved data corrupts HTTP
responses. This module includes retry logic to handle such cases.
"""

import logging
import time
from typing import Optional, Dict
from http.client import BadStatusLine
from onvif import ONVIFCamera
from onvif.exceptions import ONVIFError
import zeep.exceptions

logger = logging.getLogger(__name__)

# Retry configuration for RTSP/ONVIF port collision issues
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 0.5


class ONVIFClient:
    """
    ONVIF connection manager with connection pooling

    Handles authentication, connection lifecycle, and provides
    access to ONVIF services (PTZ, Media, Device, etc.)

    Caches:
    - _connections: ONVIFCamera instances per camera
    - _ptz_services: PTZ service instances per camera
    - _profile_tokens: Media profile tokens per camera
    """

    # Connection pool: {camera_serial: ONVIFCamera instance}
    _connections: Dict[str, ONVIFCamera] = {}

    # Service cache: {camera_serial: service instance}
    _ptz_services: Dict[str, object] = {}
    _media_services: Dict[str, object] = {}

    # Profile token cache: {camera_serial: token string}
    _profile_tokens: Dict[str, str] = {}
    
    # ONVIF default ports
    DEFAULT_PORT = 80
    # WSDL_DIR = '/etc/onvif/wsdl/'  # Default WSDL directory for onvif-zeep
    WSDL_DIR = '/usr/local/lib/python3.11/site-packages/wsdl/'  # Default WSDL directory for onvif-zeep

    
    @classmethod
    def get_camera(cls, 
                host: str, 
                username: str, 
                password: str,
                camera_serial: str,
                port: int = DEFAULT_PORT,
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
                wsdl_dir=wsdl,
                no_cache=True  # Disable caching to avoid write permission issues
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
        Close and remove camera connection from pool (also clears cached services)

        Args:
            camera_serial: Camera identifier

        Returns:
            True if connection was closed, False if not found
        """
        found = False
        if camera_serial in cls._connections:
            logger.info(f"Closing ONVIF connection for {camera_serial}")
            del cls._connections[camera_serial]
            found = True
        # Also clear cached services for this camera
        if camera_serial in cls._ptz_services:
            del cls._ptz_services[camera_serial]
        if camera_serial in cls._media_services:
            del cls._media_services[camera_serial]
        # Clear profile tokens (may have multiple keys like serial_0, serial_1)
        keys_to_remove = [k for k in cls._profile_tokens if k.startswith(f"{camera_serial}_")]
        for key in keys_to_remove:
            del cls._profile_tokens[key]
        return found

    @classmethod
    def close_all(cls) -> int:
        """
        Close all cached ONVIF connections and clear all service caches

        Returns:
            Number of connections closed
        """
        count = len(cls._connections)
        cls._connections.clear()
        cls._ptz_services.clear()
        cls._media_services.clear()
        cls._profile_tokens.clear()
        logger.info(f"Closed {count} ONVIF connections and cleared service caches")
        return count
    
    @classmethod
    def get_ptz_service(cls, camera: ONVIFCamera, camera_serial: Optional[str] = None):
        """
        Get PTZ service from camera (cached per camera_serial)

        Args:
            camera: ONVIFCamera instance
            camera_serial: Camera serial for caching (optional but recommended)

        Returns:
            PTZ service or None if not available
        """
        # Check cache first
        if camera_serial and camera_serial in cls._ptz_services:
            logger.debug(f"Reusing cached PTZ service for {camera_serial}")
            return cls._ptz_services[camera_serial]

        try:
            ptz_service = camera.create_ptz_service()
            # Cache if we have a serial
            if camera_serial and ptz_service:
                cls._ptz_services[camera_serial] = ptz_service
                logger.debug(f"Cached PTZ service for {camera_serial}")
            return ptz_service
        except Exception as e:
            logger.error(f"Failed to create PTZ service: {e}")
            return None
    
    @classmethod
    def get_media_service(cls, camera: ONVIFCamera, camera_serial: Optional[str] = None):
        """
        Get Media service from camera (cached per camera_serial)

        Args:
            camera: ONVIFCamera instance
            camera_serial: Camera serial for caching (optional but recommended)

        Returns:
            Media service or None if not available
        """
        # Check cache first
        if camera_serial and camera_serial in cls._media_services:
            logger.debug(f"Reusing cached Media service for {camera_serial}")
            return cls._media_services[camera_serial]

        try:
            media_service = camera.create_media_service()
            # Cache if we have a serial
            if camera_serial and media_service:
                cls._media_services[camera_serial] = media_service
                logger.debug(f"Cached Media service for {camera_serial}")
            return media_service
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
    def _is_rtsp_collision_error(cls, error: Exception) -> bool:
        """
        Check if error is caused by RTSP/ONVIF port collision

        Some cameras share the same port for ONVIF and RTSP. When streaming is active,
        RTSP interleaved binary data can corrupt HTTP responses, causing BadStatusLine
        errors with binary data (indicated by '$' prefix in error message).

        Args:
            error: Exception to check

        Returns:
            True if error appears to be RTSP collision, False otherwise
        """
        error_str = str(error)
        # RTSP interleaved data starts with '$' (0x24) followed by channel byte
        # BadStatusLine errors containing binary data or '$' indicate collision
        if 'BadStatusLine' in error_str:
            return True
        if 'Connection aborted' in error_str and '$' in error_str:
            return True
        return False

    @classmethod
    def get_profile_token(cls, camera: ONVIFCamera, profile_index: int = 0,
                          camera_serial: Optional[str] = None) -> Optional[str]:
        """
        Get media profile token (required for most ONVIF operations)

        Profile tokens are cached per camera_serial to avoid repeated SOAP calls.
        Includes retry logic for RTSP/ONVIF port collision errors.

        Args:
            camera: ONVIFCamera instance
            profile_index: Profile index (default: 0 = first profile)
            camera_serial: Camera serial for caching and connection invalidation

        Returns:
            Profile token string or None if not available
        """
        # Check cache first (use composite key for profile_index support)
        cache_key = f"{camera_serial}_{profile_index}" if camera_serial else None
        if cache_key and cache_key in cls._profile_tokens:
            logger.debug(f"Reusing cached profile token for {camera_serial}")
            return cls._profile_tokens[cache_key]

        last_error = None

        for attempt in range(MAX_RETRIES):
            try:
                media_service = cls.get_media_service(camera, camera_serial)
                if not media_service:
                    return None

                profiles = media_service.GetProfiles()

                if not profiles or len(profiles) <= profile_index:
                    logger.warning(f"Profile index {profile_index} not available")
                    return None

                token = profiles[profile_index].token
                # Cache the token
                if cache_key:
                    cls._profile_tokens[cache_key] = token
                    logger.debug(f"Cached profile token for {camera_serial}: {token}")
                return token

            except Exception as e:
                last_error = e

                # Check if this is an RTSP collision error (retry-able)
                if cls._is_rtsp_collision_error(e) and attempt < MAX_RETRIES - 1:
                    logger.warning(
                        f"RTSP/ONVIF collision detected (attempt {attempt + 1}/{MAX_RETRIES}), "
                        f"retrying after {RETRY_DELAY_SECONDS}s..."
                    )
                    # Invalidate cached connection if we have the serial
                    if camera_serial and camera_serial in cls._connections:
                        logger.debug(f"Invalidating cached connection for {camera_serial}")
                        del cls._connections[camera_serial]
                    time.sleep(RETRY_DELAY_SECONDS)
                    continue

                # Non-retryable error or final attempt
                logger.error(f"Failed to get profile token: {e}")
                return None

        logger.error(f"Failed to get profile token after {MAX_RETRIES} attempts: {last_error}")
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