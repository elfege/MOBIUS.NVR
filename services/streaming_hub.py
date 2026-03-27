#!/usr/bin/env python3
"""
Streaming Hub URL Resolver

Centralizes RTSP source URL resolution based on per-camera streaming_hub
configuration. Replaces hardcoded 'rtsp://nvr-packager:8554/' references
throughout the codebase.

Two hubs:
    - 'mediamtx' (default): FFmpeg publishes to MediaMTX, consumers read from it
    - 'go2rtc': go2rtc is the single consumer per camera, consumers read its RTSP re-export
"""

import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Container hostnames for each streaming hub
GO2RTC_RTSP_HOST = "nvr-go2rtc"
GO2RTC_RTSP_PORT = 8555
MEDIAMTX_RTSP_HOST = "nvr-packager"
MEDIAMTX_RTSP_PORT = 8554


def get_streaming_hub(camera_config: Dict) -> str:
    """
    Get the streaming hub for a camera.

    Args:
        camera_config: Camera configuration dict from cameras.json/DB

    Returns:
        'go2rtc' or 'mediamtx'
    """
    return (camera_config.get('streaming_hub') or 'mediamtx').lower()


def get_rtsp_source_url(camera_id: str, camera_config: Dict) -> str:
    """
    Get the RTSP URL where consumers should read this camera's stream.

    For go2rtc cameras: rtsp://nvr-go2rtc:8555/{camera_id}
    For mediamtx cameras: rtsp://nvr-packager:8554/{packager_path}

    Args:
        camera_id: Camera serial number
        camera_config: Camera configuration dict

    Returns:
        RTSP URL string
    """
    hub = get_streaming_hub(camera_config)

    if hub == 'go2rtc':
        url = f"rtsp://{GO2RTC_RTSP_HOST}:{GO2RTC_RTSP_PORT}/{camera_id}"
        logger.debug(f"[StreamingHub] {camera_id} → go2rtc RTSP: {url}")
        return url

    # Default: MediaMTX
    packager_path = camera_config.get('packager_path') or camera_id
    url = f"rtsp://{MEDIAMTX_RTSP_HOST}:{MEDIAMTX_RTSP_PORT}/{packager_path}"
    logger.debug(f"[StreamingHub] {camera_id} → MediaMTX RTSP: {url}")
    return url


def get_rtsp_source_url_main(camera_id: str, camera_config: Dict) -> str:
    """
    Get the RTSP URL for the main (high-res) stream.

    For go2rtc cameras: same as sub (go2rtc passes through native resolution)
    For mediamtx cameras: rtsp://nvr-packager:8554/{packager_path}_main

    Args:
        camera_id: Camera serial number
        camera_config: Camera configuration dict

    Returns:
        RTSP URL string for main stream
    """
    hub = get_streaming_hub(camera_config)

    if hub == 'go2rtc':
        # go2rtc passes through native resolution — no sub/main distinction
        return f"rtsp://{GO2RTC_RTSP_HOST}:{GO2RTC_RTSP_PORT}/{camera_id}"

    # MediaMTX has separate _main path
    packager_path = camera_config.get('packager_path') or camera_id
    return f"rtsp://{MEDIAMTX_RTSP_HOST}:{MEDIAMTX_RTSP_PORT}/{packager_path}_main"


def is_go2rtc_camera(camera_config: Dict) -> bool:
    """Check if this camera uses go2rtc as its streaming hub."""
    return get_streaming_hub(camera_config) == 'go2rtc'
