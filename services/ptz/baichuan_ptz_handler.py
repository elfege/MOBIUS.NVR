#!/usr/bin/env python3
"""
Baichuan PTZ Handler - PTZ operations via Reolink's native Baichuan protocol

Uses reolink_aio library (same as motion detection) for PTZ control.
This is an alternative to ONVIF for Reolink cameras, especially useful for:
- Cameras without ONVIF support (E1, Argus series)
- Avoiding RTSP/ONVIF port collision issues
- More reliable connection (Baichuan uses dedicated port 9000)

Architecture:
- A single dedicated event loop runs in a background daemon thread.
  All async PTZ operations are dispatched to this loop via run_coroutine_threadsafe().
  This avoids the cross-event-loop socket bug that occurred when each Flask request
  called asyncio.run() (new loop per call), invalidating cached Host connections.
- Host connections are cached per-camera within this single loop — sockets remain
  valid because the loop never changes.
- Stale connections are detected and refreshed automatically.
- A per-camera threading.Lock prevents rapid button presses from flooding the
  camera with simultaneous connections.
- Credentials from ReolinkCredentialProvider (same as motion detection)
"""

import asyncio
import logging
import threading
import time
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
    - Preset management (get, goto, save)

    Uses a dedicated background event loop for all async operations so that
    cached Host connections remain valid (same loop = same sockets).
    """

    # Direction mappings to reolink_aio commands
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

    # How long (seconds) a cached connection is considered fresh before re-login
    CONNECTION_TTL = 300  # 5 minutes

    # Credential provider (initialized on first use)
    _credential_provider: Optional[ReolinkCredentialProvider] = None

    # Connection cache: {camera_serial: (Host, last_used_timestamp)}
    _hosts: Dict[str, Tuple[Host, float]] = {}

    # Dedicated background event loop — all async PTZ ops run here.
    # This loop lives for the entire process lifetime, so cached Host
    # objects (and their sockets) stay valid across Flask requests.
    _loop: Optional[asyncio.AbstractEventLoop] = None
    _loop_thread: Optional[threading.Thread] = None
    _loop_lock = threading.Lock()

    # Per-camera locks to prevent flooding the camera with simultaneous connections
    _cam_locks: Dict[str, threading.Lock] = {}
    _cam_locks_lock = threading.Lock()

    @classmethod
    def _ensure_loop(cls) -> asyncio.AbstractEventLoop:
        """
        Get or create the dedicated background event loop.

        The loop runs in a daemon thread so it doesn't block process exit.
        All reolink_aio async operations are dispatched here via
        run_coroutine_threadsafe(), ensuring cached Host sockets stay valid.
        """
        if cls._loop is not None and cls._loop.is_running():
            return cls._loop
        with cls._loop_lock:
            # Double-check after acquiring lock
            if cls._loop is not None and cls._loop.is_running():
                return cls._loop
            cls._loop = asyncio.new_event_loop()

            def _run_loop():
                asyncio.set_event_loop(cls._loop)
                cls._loop.run_forever()

            cls._loop_thread = threading.Thread(target=_run_loop, daemon=True, name="baichuan-ptz-loop")
            cls._loop_thread.start()
            logger.info("Started dedicated Baichuan PTZ event loop")
            return cls._loop

    @classmethod
    def _run_async(cls, coro, timeout: float = 15.0):
        """
        Run async coroutine on the dedicated PTZ event loop.

        Dispatches the coroutine to the background loop and blocks the calling
        Flask thread until the result is ready (or timeout).

        Args:
            coro: The coroutine to run
            timeout: Max seconds to wait for result (default 15s)
        """
        loop = cls._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=timeout)

    @classmethod
    def _get_cam_lock(cls, camera_serial: str) -> threading.Lock:
        """Get or create a per-camera lock."""
        with cls._cam_locks_lock:
            if camera_serial not in cls._cam_locks:
                cls._cam_locks[camera_serial] = threading.Lock()
            return cls._cam_locks[camera_serial]

    @classmethod
    def _get_credential_provider(cls) -> ReolinkCredentialProvider:
        """Get or create credential provider instance."""
        if cls._credential_provider is None:
            cls._credential_provider = ReolinkCredentialProvider(use_api_credentials=True)
        return cls._credential_provider

    @classmethod
    async def _get_host(cls, camera_serial: str, camera_config: Dict) -> Optional[Host]:
        """
        Get a cached or fresh Baichuan Host connection.

        Connections are cached per-camera and reused within the TTL window.
        Since all calls run on the same event loop, cached sockets stay valid.

        Args:
            camera_serial: Camera serial number
            camera_config: Camera configuration dict with host

        Returns:
            Connected Host instance or None on failure
        """
        now = time.time()

        # Check cache
        if camera_serial in cls._hosts:
            host, last_used = cls._hosts[camera_serial]
            age = now - last_used
            if age < cls.CONNECTION_TTL:
                try:
                    # bc_only connections have no HTTP token (_token is always None).
                    # Use session_active (True when Baichuan TCP session is alive)
                    # or baichuan._logged_in as the validity check.
                    is_valid = getattr(host, 'session_active', False) or \
                               getattr(host.baichuan, '_logged_in', False)
                    if is_valid:
                        cls._hosts[camera_serial] = (host, now)
                        logger.debug(f"Reusing cached connection to {camera_serial} (age: {age:.0f}s)")
                        return host
                except Exception:
                    pass
            # Stale or invalid — disconnect and reconnect
            logger.debug(f"Cached connection to {camera_serial} is stale (age: {age:.0f}s), reconnecting")
            try:
                await host.logout()
            except Exception:
                pass
            del cls._hosts[camera_serial]

        # Create new connection
        camera_ip = camera_config.get('host')
        if not camera_ip:
            logger.error(f"No host configured for camera {camera_serial}")
            return None

        provider = cls._get_credential_provider()
        username, password = provider.get_credentials(camera_serial)
        if not username or not password:
            logger.error(f"Missing credentials for camera {camera_serial}")
            return None

        try:
            logger.debug(f"Connecting to {camera_serial} via Baichuan ({camera_ip})")
            # bc_only=True: use only the Baichuan protocol on port 9000.
            # Without this, reolink_aio 0.19+ attempts HTTPS on port 443 first,
            # which fails on E1/budget cameras that don't expose a web API.
            host = Host(host=camera_ip, username=username, password=password, bc_only=True)
            await host.get_host_data()
            cls._hosts[camera_serial] = (host, now)
            logger.info(f"Connected to {camera_serial} via Baichuan")
            return host

        except Exception as e:
            logger.error(f"Failed to connect to {camera_serial} via Baichuan: {e}")
            return None

    @classmethod
    async def _invalidate_host(cls, camera_serial: str):
        """Disconnect and remove cached host for camera (on error)."""
        if camera_serial in cls._hosts:
            host, _ = cls._hosts[camera_serial]
            try:
                await host.logout()
            except Exception as e:
                logger.debug(f"Disconnect warning for {camera_serial}: {e}")
            del cls._hosts[camera_serial]

    @classmethod
    def move_camera(cls,
                   camera_serial: str,
                   direction: str,
                   camera_config: Dict,
                   speed_multiplier: float = 1.0) -> Tuple[bool, str]:
        """
        Execute PTZ movement via Baichuan protocol.

        Lock strategy:
        - Stop commands BLOCK-WAIT (up to 5s) — stop must never be dropped,
          otherwise the camera keeps moving indefinitely after the user releases
          the button.
        - Move commands are NON-BLOCKING — if the lock is held (previous command
          still executing), skip this move. The user will just press again.

        Args:
            camera_serial: Camera serial number
            direction: Movement direction (left/right/up/down/zoom_in/zoom_out/stop)
            camera_config: Camera configuration dict with host
            speed_multiplier: Speed adjustment (0.1 - 1.0)

        Returns:
            Tuple of (success: bool, message: str)
        """
        lock = cls._get_cam_lock(camera_serial)

        if direction == 'stop':
            # Stop commands must always execute — block-wait up to 5s
            acquired = lock.acquire(blocking=True, timeout=5.0)
            if not acquired:
                logger.warning(f"PTZ stop timed out waiting for lock on {camera_serial}")
                return False, "Stop command timed out waiting for previous command"
        else:
            # Move commands are non-blocking — skip if busy
            if not lock.acquire(blocking=False):
                logger.info(f"PTZ {direction} skipped for {camera_serial} — previous command still executing")
                return True, "Previous PTZ command still executing"

        try:
            return cls._run_async(
                cls._move_camera_async(camera_serial, direction, camera_config, speed_multiplier)
            )
        finally:
            lock.release()

    @classmethod
    async def _move_camera_async(cls,
                                  camera_serial: str,
                                  direction: str,
                                  camera_config: Dict,
                                  speed_multiplier: float = 1.0) -> Tuple[bool, str]:
        """Async implementation of move_camera."""
        if direction not in cls.DIRECTION_MAP:
            return False, f"Invalid direction: {direction}"

        command = cls.DIRECTION_MAP[direction]
        host = await cls._get_host(camera_serial, camera_config)
        if not host:
            return False, "Failed to connect to camera via Baichuan"

        try:
            channel = 0
            supports_speed = host.supported(channel, "ptz_speed")

            if direction == 'stop':
                await host.set_ptz_command(channel, command='Stop')
                logger.info(f"Baichuan PTZ stopped for {camera_serial}")
            elif supports_speed:
                speed = int(cls.DEFAULT_SPEED * speed_multiplier)
                speed = max(1, min(64, speed))
                await host.set_ptz_command(channel, command=command, speed=speed)
                logger.info(f"Baichuan PTZ {direction} started for {camera_serial} (speed: {speed})")
            else:
                await host.set_ptz_command(channel, command=command)
                logger.info(f"Baichuan PTZ {direction} started for {camera_serial} (no speed control)")

            return True, "PTZ command executed successfully"

        except Exception as e:
            logger.error(f"Baichuan PTZ move failed for {camera_serial}: {e}")
            await cls._invalidate_host(camera_serial)
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
            # Check DB cache first (unless force refresh requested)
            if not force_refresh:
                cached_presets = PresetCache.get_cached_presets(camera_serial)
                if cached_presets is not None:
                    logger.debug(f"Using cached presets for {camera_serial}: {len(cached_presets)} presets")
                    return True, cached_presets

            # Cache miss or force refresh — query camera
            logger.debug(f"Querying Baichuan for presets: {camera_serial} (force_refresh={force_refresh})")

            host = await cls._get_host(camera_serial, camera_config)
            if not host:
                return False, []

            channel = 0

            # reolink_aio populates host.ptz_presets() during get_host_data().
            # Returns dict: {name: id} for Baichuan cameras.
            raw_presets = host.ptz_presets(channel) if hasattr(host, 'ptz_presets') else {}

            presets = []
            if raw_presets:
                if isinstance(raw_presets, dict):
                    # Baichuan returns {name: id}
                    for name, preset_id in raw_presets.items():
                        presets.append({
                            'token': str(preset_id),
                            'name': name
                        })
                else:
                    # Fallback for list format
                    for preset in raw_presets:
                        presets.append({
                            'token': str(preset.get('id', '')),
                            'name': preset.get('name', f"Preset {preset.get('id', '')}")
                        })

            logger.info(f"Retrieved {len(presets)} presets for {camera_serial} from Baichuan")

            # Cache the presets
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
        lock = cls._get_cam_lock(camera_serial)
        if not lock.acquire(blocking=False):
            return True, "Previous PTZ command still executing"
        try:
            return cls._run_async(
                cls._goto_preset_async(camera_serial, preset_token, camera_config, speed_multiplier)
            )
        finally:
            lock.release()

    @classmethod
    async def _goto_preset_async(cls,
                                  camera_serial: str,
                                  preset_token: str,
                                  camera_config: Dict,
                                  speed_multiplier: float = 1.0) -> Tuple[bool, str]:
        """Async implementation of goto_preset."""
        try:
            preset_id = int(preset_token)
        except ValueError:
            return False, f"Invalid preset token: {preset_token}"

        host = await cls._get_host(camera_serial, camera_config)
        if not host:
            return False, "Failed to connect to camera via Baichuan"

        try:
            speed = int(cls.DEFAULT_SPEED * speed_multiplier)
            speed = max(1, min(64, speed))
            channel = 0

            supports_speed = host.supported(channel, "ptz_speed")

            if supports_speed:
                await host.set_ptz_command(channel, command='ToPos', preset=preset_id, speed=speed)
                logger.info(f"Camera {camera_serial} moving to preset {preset_token} via Baichuan (speed: {speed})")
            else:
                await host.set_ptz_command(channel, command='ToPos', preset=preset_id)
                logger.info(f"Camera {camera_serial} moving to preset {preset_token} via Baichuan (no speed control)")
            return True, f"Moving to preset {preset_token}"

        except Exception as e:
            logger.error(f"Failed to goto preset for {camera_serial} via Baichuan: {e}")
            await cls._invalidate_host(camera_serial)
            return False, f"Preset operation failed: {str(e)}"

    @classmethod
    def save_preset(cls,
                    camera_serial: str,
                    preset_name: str,
                    camera_config: Dict,
                    preset_id: Optional[int] = None) -> Tuple[bool, str]:
        """
        Save current camera position as a preset via Baichuan protocol.

        Uses raw Baichuan XML with cmd_id 19 and <command>setPos</command>.
        This is the same command the Reolink native app uses to save presets.

        Args:
            camera_serial: Camera serial number
            preset_name: Name for the preset
            camera_config: Camera configuration dict
            preset_id: Optional preset ID to overwrite (auto-assigned if None)

        Returns:
            Tuple of (success: bool, message: str)
        """
        lock = cls._get_cam_lock(camera_serial)
        if not lock.acquire(blocking=False):
            return False, "Previous PTZ command still executing"
        try:
            return cls._run_async(
                cls._save_preset_async(camera_serial, preset_name, camera_config, preset_id)
            )
        finally:
            lock.release()

    @classmethod
    async def _save_preset_async(cls,
                                  camera_serial: str,
                                  preset_name: str,
                                  camera_config: Dict,
                                  preset_id: Optional[int] = None) -> Tuple[bool, str]:
        """
        Async implementation of save_preset.

        Sends raw Baichuan XML to cmd_id 19 with <command>setPos</command>.
        The camera saves its current position under the given name/ID.
        """
        host = await cls._get_host(camera_serial, camera_config)
        if not host:
            return False, "Failed to connect to camera via Baichuan"

        try:
            channel = 0

            # Determine preset ID: use provided, or find next available
            if preset_id is None:
                existing = host.ptz_presets(channel) if hasattr(host, 'ptz_presets') else {}
                if isinstance(existing, dict):
                    used_ids = set(existing.values())
                else:
                    used_ids = set()
                # Find first unused ID (0-63 range for Reolink)
                for candidate in range(64):
                    if candidate not in used_ids:
                        preset_id = candidate
                        break
                else:
                    return False, "All 64 preset slots are full"

            # Raw Baichuan XML for saving preset at current position.
            # cmd_id 19 is the preset command; <command>setPos</command> saves,
            # vs <command>toPos</command> which navigates to an existing preset.
            xml = (
                '<?xml version="1.0" encoding="UTF-8" ?>\n'
                '<body>\n'
                '<PtzPreset version="1.1">\n'
                f'<channelId>{channel}</channelId>\n'
                '<presetList>\n'
                '<preset>\n'
                f'<id>{preset_id}</id>\n'
                f'<name>{preset_name}</name>\n'
                '<command>setPos</command>\n'
                '</preset>\n'
                '</presetList>\n'
                '</PtzPreset>\n'
                '</body>'
            )

            await host.baichuan.send(cmd_id=19, channel=channel, body=xml)
            logger.info(f"Saved preset '{preset_name}' (id={preset_id}) for {camera_serial} via Baichuan")

            # Refresh preset list from camera to confirm it was saved
            await host.baichuan.get_ptz_preset(channel)
            updated_presets = host.ptz_presets(channel) if hasattr(host, 'ptz_presets') else {}
            logger.info(f"Updated presets for {camera_serial}: {updated_presets}")

            # Invalidate DB preset cache so next get_presets() fetches fresh data
            PresetCache.cache_presets(camera_serial, [])

            return True, f"Preset '{preset_name}' saved at position (id={preset_id})"

        except Exception as e:
            logger.error(f"Failed to save preset for {camera_serial} via Baichuan: {e}")
            await cls._invalidate_host(camera_serial)
            return False, f"Save preset failed: {str(e)}"

    @classmethod
    def is_baichuan_capable(cls, camera_config: Dict) -> bool:
        """
        Check if camera should use Baichuan for PTZ.

        This function determines PTZ routing only - not streaming capability.
        NEOLINK streaming works independently of this check.

        Args:
            camera_config: Camera configuration dict

        Returns:
            True if camera should use Baichuan PTZ
        """
        capabilities = camera_config.get('capabilities', [])
        if 'ptz' not in capabilities:
            return False

        ptz_method = camera_config.get('ptz_method', 'auto')

        if ptz_method == 'baichuan':
            return True
        if ptz_method == 'onvif':
            return False

        # Auto mode: Use Baichuan for NEOLINK streams or cameras without ONVIF port
        stream_type = camera_config.get('stream_type', '')
        if 'NEOLINK' in stream_type:
            return True

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


def save_preset_baichuan(camera_serial: str, preset_name: str, camera_config: Dict,
                          preset_id: Optional[int] = None) -> Tuple[bool, str]:
    """Convenience function to save preset via Baichuan."""
    return BaichuanPTZHandler.save_preset(camera_serial, preset_name, camera_config, preset_id)


def reboot_camera_baichuan(camera_serial: str, camera_config: Dict) -> Tuple[bool, str]:
    """
    Reboot a Reolink camera via Baichuan protocol.

    Args:
        camera_serial: Camera serial number
        camera_config: Camera configuration dict with host, credentials, etc.

    Returns:
        Tuple of (success: bool, message: str)
    """
    async def _do_reboot():
        host_ip = camera_config.get('host')
        if not host_ip:
            return False, "Camera host not configured"

        credential_provider = ReolinkCredentialProvider()
        username, password = credential_provider.get_credentials()

        logger.info(f"Initiating Baichuan reboot for camera {camera_serial} at {host_ip}")

        api = Host(host_ip, username, password, bc_only=True)

        try:
            await api.get_host_data()
            logger.info(f"Sending reboot command to {camera_serial}")
            await api.reboot()
            logger.info(f"Reboot command sent successfully to {camera_serial}")
            return True, "Reboot command sent - camera will restart in approximately 60 seconds"
        except Exception as e:
            logger.error(f"Failed to reboot camera {camera_serial}: {e}")
            return False, f"Reboot failed: {str(e)}"
        finally:
            await api.logout()

    # Reboot uses asyncio.run() directly (not the shared loop) because
    # it's a one-off operation and we don't want to tie up the PTZ loop
    try:
        return asyncio.run(_do_reboot())
    except Exception as e:
        logger.error(f"Baichuan reboot error for {camera_serial}: {e}")
        return False, f"Reboot failed: {str(e)}"
