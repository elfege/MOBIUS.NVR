#!/usr/bin/env python3
"""
Eufy Stream Handler
Handles Eufy camera RTSP streaming with direct camera access
"""
import os
import logging
import traceback
from pprint import pprint
from typing import Dict, List
from ..stream_handler import StreamHandler
from ..ffmpeg_params import build_rtsp_output_params, build_rtsp_input_params


logger = logging.getLogger(__name__)


class EufyStreamHandler(StreamHandler):
    """
    Stream handler for Eufy cameras
    Uses direct RTSP with embedded credentials
    """

    def build_rtsp_url(self, camera_config: Dict, stream_type: str = 'sub') -> str:

        """
        Build RTSP URL for Eufy camera

        Format: rtsp://username:password@host:port/path
        """
        if not self.validate_camera_config(camera_config):
            raise ValueError(
                f"Invalid camera configuration for {camera_config.get('name', 'unknown')}")

        # Get camera serial and RTSP config
        serial = camera_config.get('serial')
        rtsp = camera_config.get('rtsp', {})

        # Get credentials from provider (pass camera serial)
        username, password = self.credential_provider.get_credentials(serial)

        if not username or not password:
            raise ValueError(f"Missing credentials for Eufy camera {serial}")

        # Build RTSP URL
        host = rtsp['host']
        port = rtsp.get('port', 554)
        path = rtsp.get('path', '/live0')

        rtsp_url = f"rtsp://{username}:{password}@{host}:{port}{path}"

        logger.info(
            f"Built RTSP URL for {camera_config.get('name')}: rtsp://{username}:****@{host}:{port}{path}")

        return rtsp_url

    def get_ffmpeg_input_params(self, camera_config: Dict) -> List[str]:
        try:
            params = build_rtsp_input_params(
                camera_config=camera_config
            )
            print(f"INPUT PARAMS for {camera_config.get('name', 'unknown camera?')} returns: ")
            pprint(params)
            return params
        except Exception as e:
            print(e)
            traceback.print_exc()
            return [
                '-rtsp_transport', 'tcp',
                '-timeout', '30000000',
                '-analyzeduration', '1000000',
                '-probesize', '1000000',
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
        print("UNIFI HANDLER get_ffmpeg_output_params calling build_rtsp_output_params")
        try:
            params = build_rtsp_output_params(
                stream_type=stream_type,
                camera_config=camera_config,
                vendor_prefix='EUFY_'
            )
            print(f"OUTPUT PARAMS for {camera_config.get('name', 'unknown camera?')} returns: ")
            pprint(params)
            return params
        except Exception as e:
            print(e)
            print(traceback.print_exc())
            return None # ← Add explicit return so it doesn't break iteration


    def get_required_config_fields(self) -> List[str]:
        """Required fields for Eufy camera config"""
        return [
            'serial',
            'name',
            'type',
            'rtsp'
        ]

    def validate_camera_config(self, camera_config: Dict) -> bool:
        """Validate Eufy-specific camera configuration"""
        if not super().validate_camera_config(camera_config):
            return False

        # Check RTSP sub-structure
        rtsp = camera_config.get('rtsp', {})
        if not rtsp:
            logger.error(f"Missing RTSP configuration for Eufy camera")
            return False

        required_rtsp_fields = ['host', 'port', 'path']
        for field in required_rtsp_fields:
            if field not in rtsp:
                logger.error(f"Missing RTSP field '{field}' for Eufy camera")
                return False

        return True
