#!/usr/bin/env python3
"""
Abstract Stream Handler - Strategy Pattern
Defines interface for vendor-specific streaming logic
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class StreamHandler(ABC):
    """
    Abstract base class for vendor-specific stream handlers
    Each vendor (Eufy, UniFi, Reolink) implements this interface
    """

    def __init__(self, credential_provider, vendor_config: Dict):
        """
        Initialize stream handler

        Args:
            credential_provider: Credential provider instance
            vendor_config: Vendor-specific configuration dict
        """
        self.credential_provider = credential_provider
        self.vendor_config = vendor_config
        self.vendor_name = self.__class__.__name__.replace('StreamHandler', '').lower()

    @abstractmethod
    def build_rtsp_url(self, camera_config: Dict, stream_type: str = 'sub') -> str:
        """
        Build RTSP URL for camera

        Args:
            camera_config: Camera configuration dictionary

        Returns:
            Complete RTSP URL with credentials

        Example:
            rtsp://username:password@192.168.10.84:554/live0
        """
        pass

    @abstractmethod
    def get_ffmpeg_input_params(self, camera_config: Dict,) -> List[str]:
        """
        Get FFmpeg input parameters specific to this vendor

        Returns:
            List of FFmpeg command line arguments for input

        Example:
            ['-rtsp_transport', 'tcp', '-timeout', '5000000']
        """
        pass

    @abstractmethod
    def get_ffmpeg_output_params(self) -> List[str]:
        """
        Get FFmpeg output parameters specific to this vendor

        Returns:
            List of FFmpeg command line arguments for output

        Example:
            ['-c:v', 'copy', '-c:a', 'aac']
        """
        pass

    def validate_camera_config(self, camera_config: Dict) -> bool:
        """
        Validate camera configuration has required fields

        Args:
            camera_config: Camera configuration dictionary

        Returns:
            True if valid, False otherwise
        """
        required_fields = self.get_required_config_fields()

        for field in required_fields:
            if field not in camera_config:
                logger.error(f"Missing required field '{field}' for {self.vendor_name} camera")
                return False

        return True

    @abstractmethod
    def get_required_config_fields(self) -> List[str]:
        """
        Get list of required configuration fields

        Returns:
            List of required field names
        """
        pass

    def get_stream_type(self) -> str:
        """
        Get stream type for this handler

        Returns:
            Stream type identifier ('HLS', 'mjpeg', etc.)
        """
        return 'HLS'  # Default to HLS
