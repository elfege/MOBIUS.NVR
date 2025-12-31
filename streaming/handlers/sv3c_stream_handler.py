#!/usr/bin/env python3
"""
SV3C Stream Handler
Handles SV3C camera streaming via RTSP

SV3C cameras use RTSP paths /11 (main) and /12 (sub)
ONVIF support on port 8000
"""

import logging
import traceback
from pprint import pprint
from typing import Dict, List
from ..stream_handler import StreamHandler
from urllib.parse import quote
from ..ffmpeg_params import (
    build_rtsp_output_params,
    build_rtsp_input_params,
    build_ll_hls_input_publish_params,
    build_ll_hls_output_publish_params
)

logger = logging.getLogger(__name__)


class SV3CStreamHandler(StreamHandler):
    """
    Stream handler for SV3C cameras
    Uses standard RTSP with dual-stream support
    Main stream: /11, Sub stream: /12
    """

    def build_rtsp_url(self, camera_config: Dict, stream_type: str = 'sub') -> str:
        """
        Build RTSP URL for SV3C camera

        Args:
            camera_config: Camera configuration dict
            stream_type: 'main' (fullscreen) or 'sub' (grid view)

        Format: rtsp://username:password@host:554/11 (main) or /12 (sub)
        """
        if not self.validate_camera_config(camera_config):
            raise ValueError(f"Invalid camera configuration for {camera_config.get('name', 'unknown')}")
        
        return self._build_rtsp_url(camera_config, stream_type)

    def _build_rtsp_url(self, camera_config: Dict, stream_type: str) -> str:
        """Build RTSP URL for SV3C camera"""
        rtsp_config = self.vendor_config.get('rtsp', {})
        
        # Get camera-specific credentials using serial/name as identifier
        camera_id = camera_config.get('serial', camera_config.get('name', 'UNKNOWN'))
        username, password = self.credential_provider.get_credentials(camera_id)

        if not username or not password:
            raise ValueError(f"Missing credentials for SV3C camera {camera_id}")

        host = camera_config.get('host')
        port = camera_config.get('port', rtsp_config.get('port', 554))

        # SV3C uses numeric paths: /11 for main stream, /12 for sub stream
        stream_path = '11' if stream_type == 'main' else '12'
        
        # Standard SV3C RTSP format
        rtsp_url = f"rtsp://{username}:{quote(password, safe='')}@{host}:{port}/{stream_path}"
        
        logger.info(f"Built RTSP URL for {camera_config.get('name')} ({stream_type}, path=/{stream_path}): rtsp://{username}:****@{host}:{port}/{stream_path}")
        
        return rtsp_url

    def get_ffmpeg_input_params(self, camera_config: Dict) -> List[str]:
        """FFmpeg input parameters for SV3C cameras"""
        try:
            params = build_rtsp_input_params(camera_config=camera_config)
            logger.debug(f"INPUT PARAMS for {camera_config.get('name', 'unknown camera?')} returns: {params}")
            return params
        except Exception as e:
            logger.error(f"Error building input params for SV3C camera: {e}")
            traceback.print_exc()
            return [
                '-rtsp_transport', 'tcp',
                '-timeout', '5000000',
                '-analyzeduration', '1000000',
                '-probesize', '1000000'
            ]
                
    def get_ffmpeg_output_params(self, stream_type: str = 'sub', camera_config: Dict = None) -> List[str]:
        """
        Delegate to shared FFmpeg parameter module

        Args:
            stream_type: 'main' or 'sub'
            camera_config: Per-camera configuration from cameras.json

        Returns:
            List of FFmpeg output parameters for HLS streaming
        """
        logger.debug("SV3C HANDLER get_ffmpeg_output_params calling build_rtsp_output_params")
        try:
            params = build_rtsp_output_params(
                stream_type=stream_type,
                camera_config=camera_config,
                vendor_prefix='SV3C_'
            )
            logger.debug(f"OUTPUT PARAMS for {camera_config.get('name', 'unknown camera?')} returns: {params}")
            return params
        except Exception as e:
            logger.error(f"Error building output params for SV3C camera: {e}")
            traceback.print_exc()
            return None

    def get_required_config_fields(self) -> List[str]:
        """Required fields for SV3C camera config"""
        return [
            'name',
            'type',
            'host'
        ]

    def validate_camera_config(self, camera_config: Dict) -> bool:
        """Validate SV3C-specific camera configuration"""
        if not super().validate_camera_config(camera_config):
            return False

        # Check host is present
        host = camera_config.get('host')
        if not host:
            logger.error(f"Missing host for SV3C camera")
            return False

        return True