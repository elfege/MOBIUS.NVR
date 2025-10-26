#!/usr/bin/env python3
"""
PTZ Validator - Business Logic for PTZ Capability Checking
Separated from data access layer
"""

import logging
from typing import Optional
from services.camera_repository import CameraRepository

logger = logging.getLogger(__name__)


class PTZValidator:
    """
    Validates PTZ capabilities and operations
    Pure business logic - no data access
    """
    
    def __init__(self, camera_repo: CameraRepository):
        """
        Initialize validator
        
        Args:
            camera_repo: Camera repository for data access
        """
        self.camera_repo = camera_repo
    
    def is_ptz_capable(self, camera_serial: str) -> bool:
        """
        Check if camera has PTZ capability
        
        Args:
            camera_serial: Camera serial number
            
        Returns:
            True if camera has PTZ capability
        """
        camera = self.camera_repo.get_camera(camera_serial)
        if not camera:
            logger.warning(f"Camera {camera_serial} not found")
            return False
        
        return 'ptz' in camera.get('capabilities', [])
    
    def is_streaming_capable(self, camera_serial: str) -> bool:
        """
        Check if camera has streaming capability
        
        Args:
            camera_serial: Camera serial number
            
        Returns:
            True if camera can stream video
        """
        camera = self.camera_repo.get_camera(camera_serial)
        if not camera:
            logger.warning(f"Camera {camera_serial} not found")
            return False
        
        return 'streaming' in camera.get('capabilities', [])
    
    def validate_ptz_direction(self, direction: str) -> bool:
        """
        Validate PTZ direction command
        
        Args:
            direction: Direction command (left, right, up, down, 360)
            
        Returns:
            True if direction is valid
        """
        valid_directions = ['left', 'right', 'up', 'down', '360']
        return direction.lower() in valid_directions
    
    def should_mirror_direction(self, camera_serial: str) -> bool:
        """
        Check if camera image is mirrored (affects PTZ direction)
        
        Args:
            camera_serial: Camera serial number
            
        Returns:
            True if image is mirrored
        """
        camera = self.camera_repo.get_camera(camera_serial)
        if not camera:
            return False
        
        return camera.get('image_mirrored', False)
    
    def correct_direction_for_mirror(self, direction: str, is_mirrored: bool) -> str:
        """
        Correct PTZ direction if camera image is mirrored
        
        Args:
            direction: Original direction
            is_mirrored: Whether camera is mirrored
            
        Returns:
            Corrected direction
        """
        if not is_mirrored:
            return direction
        
        # Swap left/right for mirrored cameras
        mirror_map = {
            'left': 'right',
            'right': 'left',
            'up': 'up',      # Up/down don't change
            'down': 'down',
            '360': '360'     # 360 doesn't change
        }
        
        return mirror_map.get(direction, direction)