#!/usr/bin/env python3
"""
Streaming Hub URL Resolver

Centralizes RTSP source URL resolution based on streaming hub configuration.
Replaces hardcoded 'rtsp://nvr-packager:8554/' references throughout the codebase.

Two hubs:
    - 'mediamtx' (default): FFmpeg publishes to MediaMTX, consumers read from it
    - 'go2rtc': go2rtc is the single consumer per camera, consumers read its RTSP re-export

Hub resolution order:
    1. nvr_settings.streaming_hub_global (if set) → overrides everything
    2. cameras.streaming_hub (per-camera setting)
    3. 'mediamtx' (hardcoded default)

The global setting is cached for GLOBAL_HUB_CACHE_TTL seconds to avoid a DB
round-trip on every stream request. The UI writes to nvr_settings via PostgREST;
the cache expires and picks up the change within TTL seconds.
"""

import logging
import os
import time
import requests
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Container hostnames for each streaming hub
GO2RTC_RTSP_HOST = "nvr-go2rtc"
GO2RTC_RTSP_PORT = 8555
MEDIAMTX_RTSP_HOST = "nvr-packager"
MEDIAMTX_RTSP_PORT = 8554

# PostgREST URL (same env var used everywhere else in the app)
POSTGREST_URL = os.getenv('NVR_POSTGREST_URL', 'http://postgrest:3001')

# How long to cache the global hub setting before re-querying DB (seconds)
GLOBAL_HUB_CACHE_TTL = 30

# Module-level cache: (value_or_none, expiry_timestamp)
_global_hub_cache: tuple = (None, 0.0)


def _fetch_global_hub() -> Optional[str]:
    """
    Query nvr_settings for streaming_hub_global.

    Returns:
        'go2rtc', 'mediamtx', or None (no global override — use per-camera).
    """
    try:
        response = requests.get(
            f"{POSTGREST_URL}/nvr_settings",
            params={'key': 'eq.streaming_hub_global', 'select': 'value'},
            timeout=2
        )
        if response.status_code == 200:
            rows = response.json()
            if rows:
                value = rows[0].get('value')
                # None or empty string → no override
                if value in (None, ''):
                    return None
                return value.lower()
    except Exception as e:
        logger.debug(f"[StreamingHub] Could not fetch global hub setting: {e}")
    return None


def get_global_hub() -> Optional[str]:
    """
    Get the global streaming hub override, with TTL caching.

    Returns:
        'go2rtc', 'mediamtx', or None (per-camera setting applies).
    """
    global _global_hub_cache
    value, expiry = _global_hub_cache
    if time.monotonic() < expiry:
        return value

    # Cache miss — re-query DB
    value = _fetch_global_hub()
    _global_hub_cache = (value, time.monotonic() + GLOBAL_HUB_CACHE_TTL)
    if value:
        logger.debug(f"[StreamingHub] Global hub override: {value}")
    return value


def invalidate_global_hub_cache():
    """Force the global hub cache to expire on the next call. Call after UI updates the setting."""
    global _global_hub_cache
    _global_hub_cache = (None, 0.0)


def get_streaming_hub(camera_config: Dict) -> str:
    """
    Resolve the effective streaming hub for a camera.

    Resolution order:
        1. nvr_settings.streaming_hub_global (global UI toggle)
        2. cameras.streaming_hub (per-camera DB field)
        3. 'mediamtx' (hardcoded default)

    Args:
        camera_config: Camera configuration dict from DB (via camera_repository)

    Returns:
        'go2rtc' or 'mediamtx'
    """
    global_hub = get_global_hub()
    if global_hub:
        return global_hub

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
    """Check if this camera's effective streaming hub is go2rtc."""
    return get_streaming_hub(camera_config) == 'go2rtc'
