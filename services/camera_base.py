#!/usr/bin/env python3
"""
Abstract camera service interface for unified camera system
"""

from abc import ABC, abstractmethod

class CameraService(ABC):
    """Abstract base class for all camera services"""
    
    def __init__(self, camera_config):
        self.config = camera_config
        self.name = camera_config['name']
        self.camera_id = camera_config.get('id', 'unknown')
        self.session_active = False
        
    @abstractmethod
    def authenticate(self) -> bool:
        """Authenticate with camera"""
        pass
        
    @abstractmethod
    def get_snapshot(self) -> bytes:
        """Get single JPEG snapshot"""
        pass
        
    @abstractmethod
    def get_stream_url(self) -> str:
        """Get streaming URL for this camera"""
        pass
        
    def has_ptz(self) -> bool:
        """Check if camera supports PTZ"""
        return 'ptz' in self.config.get('capabilities', [])
        
    def ptz_move(self, direction: str) -> bool:
        """Move PTZ camera (override in subclasses that support PTZ)"""
        return False
        
    def get_status(self) -> dict:
        """Get camera status"""
        return {
            'name': self.name,
            'id': self.camera_id,
            'type': self.config.get('type'),
            'session_active': self.session_active,
            'has_ptz': self.has_ptz(),
            'capabilities': self.config.get('capabilities', [])
        }
