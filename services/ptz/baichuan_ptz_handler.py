#!/usr/bin/env python3
"""
Baichuan PTZ Handler - PTZ operations via Reolink's native Baichuan protocol

Uses reolink_aio library (same as motion detection) for PTZ control.
This is an alternative to ONVIF for Reolink cameras, especially useful for:
- Cameras without ONVIF support (E1, Argus series)
- Avoiding RTSP/ONVIF port collision issues
- More reliable connection (Baichuan uses dedicated port 9000)

Architecture:
- Async operations wrapped with asyncio.run() for Flask compatibility
- Connection pooling with Host instances per camera
- Credentials from ReolinkCredentialProvider (same as motion detection)
"""

import asyncio
import logging
from typing import Dict, List, Optional, Tuple
from reolink_aio.api import Host

from services.credentials.reolink_credential_provider import ReolinkCredentialProvider
from services.ptz.preset_cache import PresetCache

logger = logging.getLogger(__name__)


class BaichuanPTZHandler:
    """
    Baichuan PTZ operations handler for Reolink cameras.

    Provides:
    - Continuous move (pan, tilt, zoom)
    - Stop movement
    - Preset management (get, goto)

    Note: Uses async internally but exposes sync interface for Flask routes.
    """

    # Direction mappings to reolink_aio commands
    # reolink_aio uses: 'Up', 'Down', 'Left', 'Right', 'ZoomInc', 'ZoomDec', 'Stop'
    DIRECTION_MAP = {
        'left': 'Left',
        'right': 'Right',
        'up': 'Up',
        'down': 'Down',
        'zoom_in': 'ZoomInc',
        'zoom_out': 'ZoomDec',
        'stop': 'Stop',
    }

    # Default movement speed (1-64 for Reolink)
    DEFAULT_SPEED = 32

    # Credential provider (initialized on first use)
    _credential_provider: Optional[ReolinkCredentialProvider] = None

    # Connection cache: {camera_serial: Host}
    _hosts: Dict[str, Host] = {}

    @classmethod
    def _get_credential_provider(cls) -> ReolinkCredentialProvider:
        """Get or create credential provider instance."""
        if cls._credential_provider is None:
            cls._credential_provider = ReolinkCredentialProvider(use_api_credentials=True)
        return cls._credential_provider

    @classmethod
    async def _get_host(cls, camera_serial: str, camera_config: Dict) -> Optional[Host]:
        """
        Get or create Host connection for camera.

        Args:
            camera_serial: Camera serial number
            camera_config: Camera configuration dict with host

        Returns:
            Connected Host instance or None on failure
        """
        # Check if we have a cached connection
        if camera_serial in cls._hosts:
            host = cls._hosts[camera_serial]
            # Verify connection is still valid
            try:
                # Quick check - if host is logged in, reuse it
                if hasattr(host, '_token') and host._token:
                    return host
            except Exception:
                pass
            # Connection invalid, remove from cache
            del cls._hosts[camera_serial]

        # Create new connection
        camera_ip = camera_config.get('host')
        if not camera_ip:
            logger.error(f"No host configured for camera {camera_serial}")
            return None

        # Get credentials
        provider = cls._get_credential_provider()
        username, password = provider.get_credentials(camera_serial)
        if not username or not password:
            logger.error(f"Missing credentials for camera {camera_serial}")
            return None

        try:
            logger.info(f"Connecting to {camera_serial} via Baichuan ({camera_ip})")
            host = Host(host=camera_ip, username=username, password=password)

            # Get device info to establish connection
            await host.get_host_data()

            # Cache the connection
            cls._hosts[camera_serial] = host

            logger.info(f"Connected to {camera_serial} via Baichuan")
            return host

        except Exception as e:
            logger.error(f"Failed to connect to {camera_serial} via Baichuan: {e}")
            return None

    @classmethod
    async def _disconnect_host(cls, camera_serial: str):
        """Disconnect and remove cached host for camera."""
        if camera_serial in cls._hosts:
            try:
                await cls._hosts[camera_serial].logout()
            except Exception as e:
                logger.warning(f"Error disconnecting from {camera_serial}: {e}")
            del cls._hosts[camera_serial]

    @classmethod
    def _run_async(cls, coro):
        """
        Run async coroutine in sync context.

        Flask routes are sync, but reolink_aio is async. This wrapper
        handles the async/sync bridge safely.
        """
        try:
            # Try to get existing event loop
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're in an async context, create a new loop in a thread
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, coro)
                    return future.result(timeout=30)
            else:
                return loop.run_until_complete(coro)
        except RuntimeError:
            # No event loop, create one
            return asyncio.run(coro)

    @classmethod
    def move_camera(cls,
                   camera_serial: str,
                   direction: str,
                   camera_config: Dict,
                   speed_multiplier: float = 1.0) -> Tuple[bool, str]:
        """
        Execute PTZ movement via Baichuan protocol.

        Args:
            camera_serial: Camera serial number
            direction: Movement direction (left/right/up/down/zoom_in/zoom_out/stop)
            camera_config: Camera configuration dict with host
            speed_multiplier: Speed adjustment (0.1 - 1.0)

        Returns:
            Tuple of (success: bool, message: str)
        """
        return cls._run_async(
            cls._move_camera_async(camera_serial, direction, camera_config, speed_multiplier)
        )

    @classmethod
    async def _move_camera_async(cls,
                                  camera_serial: str,
                                  direction: str,
                                  camera_config: Dict,
                                  speed_multiplier: float = 1.0) -> Tuple[bool, str]:
        """Async implementation of move_camera."""
        try:
            # Validate direction
            if direction not in cls.DIRECTION_MAP:
                return False, f"Invalid direction: {direction}"

            # Get Baichuan command
            command = cls.DIRECTION_MAP[direction]

            # Get host connection
            host = await cls._get_host(camera_serial, camera_config)
            if not host:
                return False, "Failed to connect to camera via Baichuan"

            # Calculate speed (1-64 range for Reolink)
            speed = int(cls.DEFAULT_SPEED * speed_multiplier)
            speed = max(1, min(64, speed))

            # Execute PTZ command
            # Channel 0 is the main channel for most Reolink cameras
            channel = 0

            if direction == 'stop':
                await host.set_ptz_command(channel, command='Stop')
                logger.info(f"Baichuan PTZ stopped for {camera_serial}")
            else:
                await host.set_ptz_command(channel, command=command, speed=speed)
                logger.info(f"Baichuan PTZ {direction} started for {camera_serial} (speed: {speed})")

            return True, "PTZ command executed successfully"

        except Exception as e:
            logger.error(f"Baichuan PTZ move failed for {camera_serial}: {e}")
            # Invalidate connection on error
            await cls._disconnect_host(camera_serial)
            return False, f"PTZ operation failed: {str(e)}"

    @classmethod
    def get_presets(cls, camera_serial: str, camera_config: Dict,
                    force_refresh: bool = False) -> Tuple[bool, List[Dict]]:
        """
        Get list of available PTZ presets with database caching.

        Args:
            camera_serial: Camera serial number
            camera_config: Camera configuration dict
            force_refresh: If True, bypass cache and query camera directly

        Returns:
            Tuple of (success: bool, presets: List[Dict])
            Preset dict format: {'token': str, 'name': str}
        """
        return cls._run_async(
            cls._get_presets_async(camera_serial, camera_config, force_refresh)
        )

    @classmethod
    async def _get_presets_async(cls, camera_serial: str, camera_config: Dict,
                                  force_refresh: bool = False) -> Tuple[bool, List[Dict]]:
        """Async implementation of get_presets."""
        try:
            # Check cache first (unless force refresh requested)
            if not force_refresh:
                cached_presets = PresetCache.get_cached_presets(camera_serial)
                if cached_presets is not None:
                    logger.debug(f"Using cached presets for {camera_serial}: {len(cached_presets)} presets")
                    return True, cached_presets

            # Cache miss or force refresh - query camera
            logger.debug(f"Querying Baichuan for presets: {camera_serial} (force_refresh={force_refresh})")

            # Get host connection
            host = await cls._get_host(camera_serial, camera_config)
            if not host:
                return False, []

            # Get presets from camera
            # Channel 0 is the main channel for most Reolink cameras
            channel = 0

            # reolink_aio stores presets in host.ptz_presets after get_host_data()
            raw_presets = host.ptz_presets(channel) if hasattr(host, 'ptz_presets') else []

            # Parse presets into our format
            presets = []
            if raw_presets:
                for preset in raw_presets:
                    # Reolink preset format: {'id': int, 'name': str}
                    presets.append({
                        'token': str(preset.get('id', '')),
                        'name': preset.get('name', f"Preset {preset.get('id', '')}")
                    })

            logger.info(f"Retrieved {len(presets)} presets for {camera_serial} from Baichuan")

            # Cache the presets (skips if empty)
            PresetCache.cache_presets(camera_serial, presets)

            return True, presets

        except Exception as e:
            logger.error(f"Failed to get presets for {camera_serial} via Baichuan: {e}")
            return False, []

    @classmethod
    def goto_preset(cls,
                   camera_serial: str,
                   preset_token: str,
                   camera_config: Dict,
                   speed_multiplier: float = 1.0) -> Tuple[bool, str]:
        """
        Move camera to preset position.

        Args:
            camera_serial: Camera serial number
            preset_token: Preset token/ID to move to
            camera_config: Camera configuration dict
            speed_multiplier: Movement speed (0.1 - 1.0)

        Returns:
            Tuple of (success: bool, message: str)
        """
        return cls._run_async(
            cls._goto_preset_async(camera_serial, preset_token, camera_config, speed_multiplier)
        )

    @classmethod
    async def _goto_preset_async(cls,
                                  camera_serial: str,
                                  preset_token: str,
                                  camera_config: Dict,
                                  speed_multiplier: float = 1.0) -> Tuple[bool, str]:
        """Async implementation of goto_preset."""
        try:
            # Get host connection
            host = await cls._get_host(camera_serial, camera_config)
            if not host:
                return False, "Failed to connect to camera via Baichuan"

            # Calculate speed
            speed = int(cls.DEFAULT_SPEED * speed_multiplier)
            speed = max(1, min(64, speed))

            # Convert token to preset ID (integer)
            try:
                preset_id = int(preset_token)
            except ValueError:
                return False, f"Invalid preset token: {preset_token}"

            # Execute goto preset
            # Channel 0 is the main channel
            channel = 0
            await host.set_ptz_command(channel, command='ToPos', preset=preset_id, speed=speed)

            logger.info(f"Camera {camera_serial} moving to preset {preset_token} via Baichuan")
            return True, f"Moving to preset {preset_token}"

        except Exception as e:
            logger.error(f"Failed to goto preset for {camera_serial} via Baichuan: {e}")
            await cls._disconnect_host(camera_serial)
            return False, f"Preset operation failed: {str(e)}"

    @classmethod
    def is_baichuan_capable(cls, camera_config: Dict) -> bool:
        """
        Check if camera should use Baichuan for PTZ.

        Args:
            camera_config: Camera configuration dict

        Returns:
            True if camera should use Baichuan PTZ
        """
        # Check for explicit ptz_method setting
        ptz_method = camera_config.get('ptz_method', 'auto')

        if ptz_method == 'baichuan':
            return True

        if ptz_method == 'onvif':
            return False

        # Auto mode: Use Baichuan for NEOLINK streams or cameras without ONVIF port
        stream_type = camera_config.get('stream_type', '')
        if 'NEOLINK' in stream_type:
            return True

        # Use Baichuan if no ONVIF port configured
        if camera_config.get('onvif_port') is None:
            return True

        return False


# Module-level convenience functions
def move_ptz_baichuan(camera_serial: str, direction: str, camera_config: Dict) -> Tuple[bool, str]:
    """Convenience function for Baichuan PTZ movement."""
    return BaichuanPTZHandler.move_camera(camera_serial, direction, camera_config)


def get_presets_baichuan(camera_serial: str, camera_config: Dict) -> Tuple[bool, List[Dict]]:
    """Convenience function to get presets via Baichuan."""
    return BaichuanPTZHandler.get_presets(camera_serial, camera_config)


def goto_preset_baichuan(camera_serial: str, preset_token: str, camera_config: Dict) -> Tuple[bool, str]:
    """Convenience function to go to preset via Baichuan."""
    return BaichuanPTZHandler.goto_preset(camera_serial, preset_token, camera_config)
