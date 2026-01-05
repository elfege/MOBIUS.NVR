#!/usr/bin/env python3
"""
Amcrest Stream Handler
Handles Amcrest camera streaming via RTSP

UNTESTED! 
"""

import logging
import traceback
from pprint import pprint
from typing import Dict, List, Tuple
from ..stream_handler import StreamHandler
from urllib.parse import quote
from ..ffmpeg_params import (
    build_rtsp_output_params,
    build_rtsp_input_params,
    build_ll_hls_input_publish_params,
    build_ll_hls_output_publish_params,
    build_ll_hls_dual_output_publish_params
)

logger = logging.getLogger(__name__)


class AmcrestStreamHandler(StreamHandler):
    """
    Stream handler for Amcrest cameras
    Uses standard RTSP with dual-stream support
    """

    def build_rtsp_url(self, camera_config: Dict, stream_type: str = 'sub') -> str:
        """
        Build RTSP URL for Amcrest camera

        Args:
            camera_config: Camera configuration dict
            stream_type: 'main' (fullscreen) or 'sub' (grid view)

        Format: rtsp://username:password@host:554/cam/realmonitor?channel=1&subtype=0
        """
        if not self.validate_camera_config(camera_config):
            raise ValueError(f"Invalid camera configuration for {camera_config.get('name', 'unknown')}")
        
        return self._build_rtsp_url(camera_config, stream_type)

    def _build_rtsp_url(self, camera_config: Dict, stream_type: str) -> str:
        """Build RTSP URL for Amcrest camera"""
        rtsp_config = self.vendor_config.get('rtsp', {})
        username, password = self.credential_provider.get_credentials()

        if not username or not password:
            raise ValueError(f"Missing credentials for Amcrest camera")

        host = camera_config.get('host')
        port = camera_config.get('port', rtsp_config.get('port', 554))

        # Amcrest uses subtype parameter: 0=main, 1=sub
        subtype = 0 if stream_type == 'main' else 1
        
        # Standard Amcrest RTSP format
        rtsp_url = f"rtsp://{username}:{quote(password, safe='')}@{host}:{port}/cam/realmonitor?channel=1&subtype={subtype}"
        
        logger.info(f"Built RTSP URL for {camera_config.get('name')} ({stream_type}, subtype={subtype}): rtsp://{username}:****@{host}:{port}/cam/realmonitor?...")
        
        return rtsp_url

    def get_ffmpeg_input_params(self, camera_config: Dict) -> List[str]:
        """FFmpeg input parameters for Amcrest cameras"""
        try:
            params = build_rtsp_input_params(camera_config=camera_config)
            print(f"INPUT PARAMS for {camera_config.get('name', 'unknown camera?')} returns: ")
            pprint(params)
            return params
        except Exception as e:
            traceback.print_exc()
            print(e)
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
        print("AMCREST HANDLER get_ffmpeg_output_params calling build_rtsp_output_params")
        try:
            params = build_rtsp_output_params(
                stream_type=stream_type,
                camera_config=camera_config,
                vendor_prefix='AMCREST_'
            )
            print(f"OUTPUT PARAMS for {camera_config.get('name', 'unknown camera?')} returns: ")
            pprint(params)
            return params
        except Exception as e:
            print(e)
            print(traceback.print_exc())
            return None

    def get_required_config_fields(self) -> List[str]:
        """Required fields for Amcrest camera config"""
        return [
            'name',
            'type',
            'host'
        ]

    def validate_camera_config(self, camera_config: Dict) -> bool:
        """Validate Amcrest-specific camera configuration"""
        if not super().validate_camera_config(camera_config):
            return False

        # Check host is present
        host = camera_config.get('host')
        if not host:
            logger.error(f"Missing host for Amcrest camera")
            return False

        return True

    def _build_ll_hls_publish(self, camera_config: Dict, rtsp_url: str) -> Tuple[List[str], str]:
        """
        Build the full ffmpeg argv to publish LL-HLS to MediaMTX packager.

        Uses DUAL OUTPUT mode: single FFmpeg process produces both:
        - Sub stream (transcoded, scaled per cameras.json) → /camera_serial
        - Main stream (passthrough, full resolution) → /camera_serial_main

        Args:
            camera_config: Camera configuration dict
            rtsp_url: RTSP URL for the camera (already built)

        Returns: (argv, play_url)
        - argv: ["ffmpeg", <input flags>, "-i", <rtsp_url>, <dual output flags>]
        - play_url: "/hls/<packager_path or serial>/index.m3u8" (sub stream for grid)
        """
        # INPUT side - use shared helper for consistent input params
        in_args: List[str] = build_ll_hls_input_publish_params(camera_config=camera_config)

        # OUTPUT side - DUAL outputs: sub (transcoded) + main (passthrough)
        # Single camera connection, two MediaMTX paths
        out_args: List[str] = build_ll_hls_dual_output_publish_params(
            camera_config=camera_config,
            vendor_prefix='AMCREST_'
        )

        # Assemble final argv
        argv: List[str] = ["ffmpeg", *in_args, "-i", rtsp_url, *out_args]

        # Compute the play URL for the UI/API (edge proxies /hls/ → packager)
        # Returns sub stream URL - frontend will request _main when fullscreen
        path = (
            camera_config.get("packager_path") or
            camera_config.get("serial") or
            camera_config.get("id")
        )
        play_url = f"/hls/{path}/index.m3u8"

        logger.info(f"Built LL-HLS publish command for {camera_config.get('name')}")

        return argv, play_url