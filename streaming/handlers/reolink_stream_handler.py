#!/usr/bin/env python3
"""
Reolink Stream Handler
Handles Reolink camera streaming via native dual-stream channels
"""

import os
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


class ReolinkStreamHandler(StreamHandler):
    """
    Stream handler for Reolink cameras
    Uses native dual-stream capability (main + sub channels)
    """

    def build_rtsp_url(self, camera_config: Dict, stream_type: str = 'sub') -> str:
        """
        Build RTSP URL for Reolink camera

        Args:
            camera_config: Camera configuration dict
            stream_type: 'main' (fullscreen) or 'sub' (grid view)

        Format: rtsp://username:password@host:port/h264Preview_01_main
        """
        if not self.validate_camera_config(camera_config):
            raise ValueError(f"Invalid camera configuration for {camera_config.get('name', 'unknown')}")
        
        # Check if camera wants RTMP instead of RTSP
        protocol = camera_config.get('stream_type', 'HLS').upper()
        
        if protocol == 'RTMP':
            return self._build_rtmp_url(camera_config, stream_type)
        elif protocol == 'NEOLINK':
            return self._build_NEOlink_url(camera_config, stream_type)
        else:
            return self._build_rtsp_url(camera_config, stream_type)

    def _build_rtsp_url(self, camera_config: Dict, stream_type: str) -> str:
        """Build RTSP URL for Reolink camera"""
        rtsp_config = self.vendor_config.get('rtsp', {})
        username, password = self.credential_provider.get_credentials()

        if not username or not password:
            raise ValueError(f"Missing credentials for Reolink camera")

        host = camera_config.get('host')
        port = camera_config.get('port', rtsp_config.get('port', 554))

        if stream_type == 'main':
            stream_path = rtsp_config.get('stream_path_main', '/h264Preview_01_main')
        else:
            stream_path = rtsp_config.get('stream_path_sub', '/h264Preview_01_sub')

        rtsp_url = f"rtsp://{username}:{quote(password, safe='')}@{host}:{port}{stream_path}"
        
        logger.info(f"Built RTSP URL for {camera_config.get('name')} ({stream_type}): rtsp://{username}:****@{host}:{port}{stream_path}")
        
        return rtsp_url

    def _build_ll_hls_publish(self, camera_config: Dict, rtsp_url: str) -> Tuple[List[str], str]:
        """
        Build the full ffmpeg argv to publish LL-HLS to MediaMTX packager.

        Uses DUAL OUTPUT mode: single FFmpeg process produces both:
        - Sub stream (transcoded, scaled per cameras.json) → /camera_serial
        - Main stream (passthrough, full resolution) → /camera_serial_main

        Returns: (argv, play_url)
        - argv: ["ffmpeg", <input flags>, "-i", <rtsp_url>, <dual output flags>]
        - play_url: "/hls/<packager_path or serial>/index.m3u8" (sub stream for grid)
        """
        # INPUT side (mirrors RTSP input helper; redundant by design for clarity)
        in_args: List[str] = build_ll_hls_input_publish_params(camera_config=camera_config)

        # OUTPUT side - DUAL outputs: sub (transcoded) + main (passthrough)
        # Single camera connection, two MediaMTX paths
        out_args: List[str] = build_ll_hls_dual_output_publish_params(
            camera_config=camera_config,
            vendor_prefix=camera_config.get("type", "reolink")
        )

        # Assemble final argv
        argv: List[str] = ["ffmpeg", *in_args, "-i", rtsp_url, *out_args]

        # Compute the play URL for the UI/API (edge proxies /hls/ → packager)
        # Returns sub stream URL - frontend will request _main when fullscreen
        path = (
            (camera_config.get("packager_path") or
             camera_config.get("serial") or
             camera_config.get("id"))
        )
        play_url = f"/hls/{path}/index.m3u8"

        return argv, play_url

    def _build_NEOlink_url(self, camera_config: Dict, stream_type: str) -> str:
        """Build RTSP URL via Neolink bridge for Reolink camera

        Uses camera serial as the Neolink path - neolink.toml must use
        the same serial as the 'name' field for consistency.
        """

        neolink_config = camera_config.get('neolink', {})
        port = neolink_config.get('port', 8554)

        # Use serial for the Neolink path (must match [[cameras]] name in neolink.toml)
        serial = camera_config.get('serial', 'UNKNOWN')

        # Use the requested stream_type: 'main' or 'sub'
        # Neolink accepts: main, Main, mainStream, sub, Sub, subStream, etc.
        stream_path = stream_type if stream_type in ('main', 'sub') else 'sub'

        # Neolink bridge runs locally - no credentials needed
        neolink_url = f"rtsp://neolink:{port}/{serial}/{stream_path}"

        logger.info(f"Built Neolink bridge URL for {camera_config.get('name')}: {neolink_url}")

        return neolink_url

    def _build_rtmp_url(self, camera_config: Dict, stream_type: str) -> str:
        """Build RTMP URL for Reolink camera"""
        username, password = self.credential_provider.get_credentials()

        if not username or not password:
            raise ValueError(f"Missing credentials for Reolink camera")

        host = camera_config.get('host')
        port = camera_config.get('rtmp_port', 1935)

        # Determine stream channel based on type
        if stream_type == 'main':
            channel = 'channel0_main.bcs'
            stream_id = 0
        else:
            channel = 'channel0_sub.bcs'
            stream_id = 1

        # Build RTMP URL - NO URL ENCODING for RTMP!
        rtmp_url = f"rtmp://{host}:{port}/bcs/{channel}?channel=0&stream={stream_id}&user={username}&password={password}"
        
        logger.info(f"Built RTMP URL for {camera_config.get('name')} ({stream_type}): rtmp://{host}:{port}/bcs/{channel}?...")
        
        return rtmp_url
    
    def get_ffmpeg_input_params(self, camera_config: Dict) -> List[str]:
        """
        FFmpeg input parameters for Reolink cameras
        Different params for RTSP vs RTMP vs NEOLINK
        """
        protocol = camera_config.get('stream_type', 'HLS')

        if protocol == 'RTMP':
            # RTMP input needs minimal params
            return [
                '-timeout', '5000000',          # 5-second timeout
                '-analyzeduration', '500000',   # Fast analysis for low latency
                '-probesize', '500000'          # Smaller probe for low latency
            ]
        elif protocol == 'NEOLINK':
            # Neolink bridge: use UDP transport to prevent buffer overflow
            # Neolink's GStreamer RTSP server works better with UDP
            return [
                '-rtsp_transport', 'udp',       # UDP prevents buffer stalls
                '-timeout', '5000000',
                '-analyzeduration', '500000',   # Fast analysis
                '-probesize', '500000',
                '-fflags', 'nobuffer',          # Reduce buffering
                '-flags', 'low_delay'           # Minimize latency
            ]
        else:
            # RTSP input needs transport and timeout params
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
        print("REOLINK HANDLER get_ffmpeg_output_params calling build_rtsp_output_params")
        try:
            params = build_rtsp_output_params(
                stream_type=stream_type,
                camera_config=camera_config,
                vendor_prefix='REOLINK_'
            )
            print(f"OUTPUT PARAMS for {camera_config.get('name', 'unknown camera?')} returns: ")
            pprint(params)
            return params
        except Exception as e:
            print(e)
            print(traceback.print_exc())
            return None # ← Add explicit return so it doesn't break iteration

    def get_required_config_fields(self) -> List[str]:
        """Required fields for Reolink camera config"""
        return [
            'name',
            'type',
            'host'
        ]

    def validate_camera_config(self, camera_config: Dict) -> bool:
        """Validate Reolink-specific camera configuration"""
        if not super().validate_camera_config(camera_config):
            return False

        # Check host is present
        host = camera_config.get('host')
        if not host:
            logger.error(f"Missing host for Reolink camera")
            return False

        return True
