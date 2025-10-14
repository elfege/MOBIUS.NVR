#!/usr/bin/env python3
"""
UniFi Protect Stream Handler
Handles UniFi camera streaming via Protect console
"""
import os
import logging
import traceback
from pprint import pprint
from typing import Dict, List
from ..stream_handler import StreamHandler
from ..ffmpeg_params import build_rtsp_output_params, build_rtsp_input_params


logger = logging.getLogger(__name__)


class UniFiStreamHandler(StreamHandler):
    """
    Stream handler for UniFi Protect cameras
    Uses Protect console RTSP proxy (no camera credentials needed)
    """

    def build_rtsp_url(self, camera_config: Dict, stream_type: str = 'sub') -> str:
        """
        Build RTSP URL for UniFi Protect camera

        Format: rtsp://protect_host:port/rtsp_alias
        No credentials needed in URL (console handles auth)
        """
        if not self.validate_camera_config(camera_config):
            raise ValueError(
                f"Invalid camera configuration for {camera_config.get('name', 'unknown')}")

        # Get Protect console config
        console_config = self.vendor_config.get('console', {})
        protect_host = console_config.get('host', '192.168.10.3')
        protect_port = console_config.get('port', 7447)

        # Get camera-specific config
        camera_id = camera_config.get("camera_id")  # ✅ Use camera_id instead of name
        camera_name = camera_config.get("name", "UNKNOWN")

        # Construct env var name from camera_id (no spaces)
        rtsp_alias = os.getenv(f"CAMERA_{camera_id}_TOKEN_ALIAS", None)

        print("═══════════════════════════════════════════════════════════════════════════")
        print(f"UNIFI protect_host: {protect_host}")
        print(f"UNIFI protect_port: {protect_port}")
        print(f"UNIFI camera_name: {camera_name}")
        if rtsp_alias:
            print(f"UNIFI rtsp_alias: *********{rtsp_alias[-3:]}")
        else:
            print(f"UNIFI rtsp_alias: None (env var not found)")
        print("═══════════════════════════════════════════════════════════════════════════")

        if not rtsp_alias:
            raise ValueError(
                f"Missing rtsp_alias for UniFi camera {camera_config.get('name')}")

        # Build RTSP URL (no credentials in URL for Protect)
        rtsp_url = f"rtsp://{protect_host}:{protect_port}/{rtsp_alias}"
        print(f"UNIFI rtsp_url: {rtsp_url}")

        logger.info(
            f"Built RTSP URL for {camera_config.get('name')}: {rtsp_url}")

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
            traceback.print_exc()
            return [
                '-rtsp_transport', 'tcp',
                '-timeout', '30000000',          # 30 s (µs)
                '-fflags', 'nobuffer',
                '-flags', 'low_delay',
                '-analyzeduration', '1000000',
                '-probesize', '1000000',
                '-use_wallclock_as_timestamps', '1'
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
        try:
            # DEBUG: Print what we're receiving
            print("=" * 80)
            print(f"UniFi get_ffmpeg_output_params called")
            print(f"stream_type: {stream_type}")
            print(f"camera_config: {camera_config}")
            print("=" * 80)

            return build_rtsp_output_params(
                stream_type=stream_type,
                camera_config=camera_config,
                vendor_prefix='UNIFI_'
            )
        except Exception as e:
            print(traceback.print_exc())
            print(e)
            return None  # ← Add explicit return so it doesn't break iteration


    def get_required_config_fields(self) -> List[str]:
        """Required fields for UniFi camera config"""
        return [
            'camera_id',
            'name',
            'type',
            'rtsp_alias'
        ]

    def validate_camera_config(self, camera_config: Dict) -> bool:
        """Validate UniFi-specific camera configuration"""
        if not super().validate_camera_config(camera_config):
            return False

        # Check rtsp_alias exists
        if not camera_config.get('rtsp_alias'):
            logger.error(f"Missing rtsp_alias for UniFi camera")
            return False

        return True
