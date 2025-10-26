#!/usr/bin/env python3
"""
Amcrest PTZ Handler
Handles PTZ control for Amcrest cameras using CGI API with HTTP Digest Auth
"""

import requests
import logging
from typing import Optional
from requests.auth import HTTPDigestAuth
from services.credentials.amcrest_credential_provider import AmcrestCredentialProvider

logger = logging.getLogger(__name__)


class AmcrestPTZHandler:
    """
    PTZ control for Amcrest cameras
    Uses CGI-based API with HTTP Digest authentication
    """
    
    # Amcrest PTZ direction codes (STRING-based, not numeric!)
    DIRECTION_CODES = {
        'up': 'Up',
        'down': 'Down',
        'left': 'Left',
        'right': 'Right'
    }
    
    def __init__(self):
        self.credential_provider = AmcrestCredentialProvider()
        logger.info("Amcrest PTZ Handler initialized")
    
    def move_camera(self, camera_serial: str, direction: str, camera_repo) -> bool:
        """
        Execute PTZ movement command
        
        Args:
            camera_serial: Camera serial/ID
            direction: Movement direction (up, down, left, right, stop)
            camera_repo: Camera repository for config access
            
        Returns:
            True if command successful
        """
        print(f"[AMCREST_PTZ] move_camera() CALLED - serial={camera_serial}, direction={direction}")
        print(f"[AMCREST_PTZ] Starting execution...")
        try:
            # Get camera config
            camera = camera_repo.get_camera(camera_serial)
            if not camera:
                logger.error(f"Camera {camera_serial} not found")
                return False
            
            host = camera.get('host')
            if not host:
                logger.error(f"No host configured for camera {camera_serial}")
                return False
            
            # Get credentials
            username, password = self.credential_provider.get_credentials(camera_serial)
            if not username or not password:
                logger.error(f"Missing credentials for camera {camera_serial}")
                return False
            
            # Execute PTZ command
            if direction == 'stop':
                return self._stop_movement(host, username, password)
            elif direction in self.DIRECTION_CODES:
                return self._start_movement(host, username, password, direction)
            else:
                logger.error(f"Invalid direction: {direction}")
                return False
        
        except Exception as e:
            logger.error(f"PTZ command failed for {camera_serial}: {e}")
            return False
    
    def _start_movement(self, host: str, username: str, password: str, direction: str) -> bool:
        """
        Start PTZ movement in specified direction
        
        Args:
            host: Camera IP address
            username: Camera username
            password: Camera password
            direction: Movement direction
            
        Returns:
            True if successful
        """
        try:
            code = self.DIRECTION_CODES[direction]
            url = f"http://{host}/cgi-bin/ptz.cgi"
            
            params = {
                'action': 'start',
                'channel': 0,
                'code': code,
                'arg1': 0,     # Vertical speed/steps (0 = default, 1-8 for variable)
                'arg2': 5,     # Horizontal speed (1-8 range, 5 = medium speed)
                'arg3': 0      # Reserved/unused (always 0 for basic movement)
            }


            auth = HTTPDigestAuth(username, password)
            
            # Debug logging
            print(f"PTZ Request: {url}")
            print(f"PTZ Params: {params}")
            print(f"PTZ Auth: user={username}")
            
            
            response = requests.get(url, params=params, auth=auth, timeout=5)
            
            # More detailed error logging
            print(f"PTZ Response: {response.status_code}")
            print(f"PTZ Response Text: {response.text}")
            
            if response.status_code == 200:
                print(f"Started PTZ movement: {direction} on {host}")
                return True
            else:
                logger.error(f"PTZ start failed: {response.status_code} - {response.text}")
                return False
        
        except Exception as e:
            logger.error(f"Error starting PTZ movement: {e}")
            return False
    
    def _stop_movement(self, host: str, username: str, password: str) -> bool:
        """
        Stop all PTZ movement
        
        Args:
            host: Camera IP address
            username: Camera username
            password: Camera password
            
        Returns:
            True if successful
        """
        try:
            url = f"http://{host}/cgi-bin/ptz.cgi"
            
            params = {
                'action': 'stop',
                'channel': 0,
                'code': 'Right',   # Arbitrary code for stop
                'arg1': 0,         # Unused
                'arg2': 0,         # Speed = 0 to stop
                'arg3': 0          # Unused
            }
        
            auth = HTTPDigestAuth(username, password)
            response = requests.get(url, params=params, auth=auth, timeout=5)
            
            if response.status_code == 200:
                logger.info(f"Stopped PTZ movement on {host}")
                return True
            else:
                logger.error(f"PTZ stop failed: {response.status_code} - {response.text}")
                return False
        
        except Exception as e:
            logger.error(f"Error stopping PTZ movement: {e}")
            return False


# Global singleton instance
amcrest_ptz_handler = AmcrestPTZHandler()