#!/usr/bin/env python3
"""
Hubitat Power Service

Provides power cycling functionality for cameras via Hubitat smart plugs.
Integrates with CameraStateTracker to automatically power cycle cameras
that become OFFLINE (3+ consecutive failures).

Architecture:
    CameraStateTracker (detects OFFLINE state)
           |
           | (callback)
           v
    HubitatPowerService
           |
           | (HTTP GET to Hubitat Maker API)
           v
    Smart Plug → Camera Power Cycle

Features:
- Automatic power cycling when camera goes OFFLINE
- 5-minute cooldown between power cycles per camera
- Manual power control via API endpoints
- Device discovery with smart matching based on camera name
- Hubitat device ID saved to cameras.json for persistence

Hubitat Maker API:
    GET http://{hub_ip}/apps/api/{app_number}/devices/all?access_token={token}
    GET http://{hub_ip}/apps/api/{app_number}/devices/{device_id}/{command}?access_token={token}

Author: NVR System
Date: January 24, 2026
"""

import logging
import os
import threading
import time
import requests
from typing import TYPE_CHECKING, Optional, Dict, List, Any, Callable
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

# Type hints for lazy imports (avoid circular dependencies)
if TYPE_CHECKING:
    from services.camera_state_tracker import CameraStateTracker, CameraState, CameraAvailability


class PowerCycleState(Enum):
    """
    State of a power cycle operation for a camera.

    IDLE: No power cycle in progress
    POWERING_OFF: Smart plug turned off, waiting for shutdown
    POWERING_ON: Smart plug turned on, camera booting
    COMPLETE: Power cycle finished successfully
    FAILED: Power cycle failed
    """
    IDLE = "idle"
    POWERING_OFF = "powering_off"
    POWERING_ON = "powering_on"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class PowerCycleStatus:
    """
    Status information for a camera's power cycle operation.

    Attributes:
        camera_id: Camera serial number
        state: Current power cycle state
        device_id: Hubitat device ID controlling the camera's power
        started_at: Timestamp when power cycle started
        completed_at: Timestamp when power cycle completed
        error: Error message if failed
    """
    camera_id: str
    state: PowerCycleState = PowerCycleState.IDLE
    device_id: Optional[str] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'camera_id': self.camera_id,
            'state': self.state.value,
            'device_id': self.device_id,
            'started_at': self.started_at,
            'completed_at': self.completed_at,
            'error': self.error
        }


class HubitatPowerService:
    """
    Service for power cycling cameras via Hubitat smart plugs.

    Uses Hubitat Maker API to control smart plugs connected to camera power supplies.
    Integrates with CameraStateTracker via callbacks to automatically power cycle
    cameras that become OFFLINE.

    Configuration:
        Environment variables:
        - HUBITAT_API_TOKEN: Maker API access token
        - HUBITAT_API_APP_NUMBER: Maker API app number
        - HUBITAT_HUB_IP: Hub IP address (default: hubitat.local)

        Camera config (cameras.json):
        - power_supply: "hubitat" - Camera is powered by Hubitat-controlled plug
        - hubitat_device_id: "123" - Hubitat device ID for the smart plug

    Usage:
        service = HubitatPowerService(camera_repo, camera_state_tracker)
        service.start()

        # Manual power cycle
        service.power_cycle("T8416P0023352DA9")

        # Get all switch devices
        devices = service.get_switch_devices()

        # Set device for camera
        service.set_camera_device("T8416P0023352DA9", "123")

    Thread Safety:
        All public methods are thread-safe via internal locking.
    """

    # Time to wait after turning off before turning back on (seconds)
    POWER_OFF_WAIT_SECONDS = 10

    # Time to wait after turning on for camera to boot and become ready (seconds)
    # Budget cameras like SV3C can take 30-60 seconds to initialize RTSP after power on
    CAMERA_BOOT_WAIT_SECONDS = 45

    # Minimum time between power cycles for the same camera (seconds)
    POWER_CYCLE_COOLDOWN_SECONDS = 300  # 5 minutes

    # HTTP request timeout (seconds)
    REQUEST_TIMEOUT = 10

    # Valid power supply types (user-configurable per camera)
    POWER_SUPPLY_TYPES = ['hubitat', 'poe', 'none']

    def __init__(
        self,
        camera_repo,
        camera_state_tracker: "CameraStateTracker",
        hub_ip: Optional[str] = None,
        stream_manager: Optional[Any] = None
    ):
        """
        Initialize the Hubitat power service.

        Args:
            camera_repo: CameraRepository instance for camera configuration
            camera_state_tracker: CameraStateTracker for health monitoring
            hub_ip: Hubitat hub IP address (default: from env or hubitat.local)
            stream_manager: StreamManager instance for triggering stream restarts after power cycle
        """
        self._camera_repo = camera_repo
        self._state_tracker = camera_state_tracker
        self._stream_manager = stream_manager

        # Load configuration from environment
        self._api_token = os.environ.get('HUBITAT_API_TOKEN', '')
        self._app_number = os.environ.get('HUBITAT_API_APP_NUMBER', '')
        self._hub_ip = hub_ip or os.environ.get('HUBITAT_HUB_IP', 'hubitat.local')

        # Track power cycle state per camera
        self._power_cycle_status: Dict[str, PowerCycleStatus] = {}
        self._last_power_cycle: Dict[str, float] = {}  # camera_id -> timestamp

        # Thread safety
        self._lock = threading.RLock()

        # Service state
        self._running = False
        self._callbacks_registered = False

        # Validate configuration
        self._enabled = bool(self._api_token and self._app_number)

        if self._enabled:
            logger.info(
                f"HubitatPowerService initialized (hub: {self._hub_ip}, "
                f"app: {self._app_number})"
            )
        else:
            logger.warning(
                "HubitatPowerService DISABLED - missing HUBITAT_API_TOKEN or "
                "HUBITAT_API_APP_NUMBER environment variables"
            )

    def start(self) -> None:
        """
        Start the power service and register callbacks.

        Registers state change callbacks with CameraStateTracker for all
        cameras with power_supply='hubitat'. Callbacks trigger automatic
        power cycling when cameras go OFFLINE.
        """
        if not self._enabled:
            logger.warning("HubitatPowerService not starting - credentials not configured")
            return

        if self._running:
            logger.warning("HubitatPowerService already running")
            return

        self._running = True
        self._register_callbacks()
        logger.info("HubitatPowerService started")

    def stop(self) -> None:
        """
        Stop the power service.

        Note: Callbacks cannot be unregistered from CameraStateTracker,
        but they will no-op when _running is False.
        """
        if not self._running:
            return

        self._running = False
        logger.info("HubitatPowerService stopped")

    def is_enabled(self) -> bool:
        """Check if service is enabled (credentials configured)."""
        return self._enabled

    def set_stream_manager(self, stream_manager) -> None:
        """
        Set the StreamManager instance for triggering stream restarts.

        Called during app initialization after StreamManager is created.
        Enables automatic stream restart after power cycle completes.

        Args:
            stream_manager: StreamManager instance
        """
        self._stream_manager = stream_manager
        logger.info("HubitatPowerService: StreamManager set for post-power-cycle restarts")

    def _register_callbacks(self) -> None:
        """
        Register state change callbacks for hubitat-powered cameras.

        Called once during start(). Registers a callback for each camera
        with power_supply='hubitat'.
        """
        if self._callbacks_registered:
            return

        hubitat_cameras = self._get_hubitat_cameras()
        logger.info(f"Registering callbacks for {len(hubitat_cameras)} hubitat-powered cameras")

        for camera in hubitat_cameras:
            serial = camera.get('serial')
            if serial:
                self._state_tracker.register_callback(serial, self._on_camera_state_change)
                logger.debug(f"Registered power callback for camera {serial}")

        self._callbacks_registered = True

    def _get_hubitat_cameras(self) -> List[Dict]:
        """
        Get all cameras with power_supply='hubitat'.

        Returns:
            List of camera configuration dictionaries
        """
        all_cameras = self._camera_repo.get_all_cameras(include_hidden=True)
        return [
            {**config, 'serial': serial}
            for serial, config in all_cameras.items()
            if config.get('power_supply') == 'hubitat'
        ]

    def _on_camera_state_change(self, state: "CameraState") -> None:
        """
        Handle camera state changes from CameraStateTracker.

        Triggers power cycle when camera transitions to OFFLINE state
        (3+ consecutive failures).

        Args:
            state: Current camera state from CameraStateTracker
        """
        # Import here to avoid circular dependency
        from services.camera_state_tracker import CameraAvailability

        if not self._running:
            return

        # Only trigger on OFFLINE state (3+ failures)
        if state.availability != CameraAvailability.OFFLINE:
            return

        camera_id = state.camera_id

        # Verify camera is hubitat-powered
        camera = self._camera_repo.get_camera(camera_id)
        if not camera or camera.get('power_supply') != 'hubitat':
            return

        # Check if device ID is configured (use power_supply_device_id field)
        device_id = camera.get('power_supply_device_id')
        if not device_id:
            logger.warning(
                f"[HUBITAT] Camera {camera_id} is hubitat-powered but has no "
                "power_supply_device_id configured - skipping power cycle"
            )
            return

        # Trigger power cycle (with cooldown check)
        self._maybe_power_cycle(camera_id, str(device_id))

    def _maybe_power_cycle(self, camera_id: str, device_id: str) -> bool:
        """
        Attempt to power cycle a camera if cooldown has elapsed.

        Args:
            camera_id: Camera serial number
            device_id: Hubitat device ID

        Returns:
            True if power cycle was started, False if skipped
        """
        with self._lock:
            # Check cooldown
            last_cycle = self._last_power_cycle.get(camera_id, 0)
            elapsed = time.time() - last_cycle

            if elapsed < self.POWER_CYCLE_COOLDOWN_SECONDS:
                remaining = self.POWER_CYCLE_COOLDOWN_SECONDS - elapsed
                logger.info(
                    f"[HUBITAT] Camera {camera_id} power cycle skipped - "
                    f"cooldown active ({remaining:.0f}s remaining)"
                )
                return False

            # Check if already in progress
            status = self._power_cycle_status.get(camera_id)
            if status and status.state in (PowerCycleState.POWERING_OFF, PowerCycleState.POWERING_ON):
                logger.info(f"[HUBITAT] Camera {camera_id} power cycle already in progress")
                return False

            # Start power cycle in background thread
            threading.Thread(
                target=self._do_power_cycle,
                args=(camera_id, device_id),
                name=f"HubitatPowerCycle-{camera_id}",
                daemon=True
            ).start()

            return True

    def _do_power_cycle(self, camera_id: str, device_id: str) -> None:
        """
        Execute power cycle: OFF -> wait -> ON -> wait for boot -> restart stream.

        Runs in a background thread. Updates power cycle status throughout.
        After camera boots, automatically triggers stream restart via StreamManager.

        Args:
            camera_id: Camera serial number
            device_id: Hubitat device ID
        """
        status = PowerCycleStatus(
            camera_id=camera_id,
            device_id=device_id,
            state=PowerCycleState.POWERING_OFF,
            started_at=time.time()
        )

        with self._lock:
            self._power_cycle_status[camera_id] = status
            self._last_power_cycle[camera_id] = time.time()

        try:
            logger.warning(
                f"[HUBITAT] Power cycling camera {camera_id} (device: {device_id})"
            )

            # Turn OFF
            if not self._send_command(device_id, 'off'):
                raise Exception("Failed to send OFF command")

            # Wait for camera to fully power down
            time.sleep(self.POWER_OFF_WAIT_SECONDS)

            # Update status
            with self._lock:
                status.state = PowerCycleState.POWERING_ON

            # Turn ON
            if not self._send_command(device_id, 'on'):
                raise Exception("Failed to send ON command")

            # Wait for camera to boot and become ready
            # Budget cameras like SV3C need 30-60 seconds to initialize RTSP
            logger.info(
                f"[HUBITAT] Camera {camera_id} powered on, waiting {self.CAMERA_BOOT_WAIT_SECONDS}s for boot..."
            )
            time.sleep(self.CAMERA_BOOT_WAIT_SECONDS)

            # Success
            with self._lock:
                status.state = PowerCycleState.COMPLETE
                status.completed_at = time.time()

            duration = status.completed_at - status.started_at
            logger.info(
                f"[HUBITAT] Power cycle complete for camera {camera_id} "
                f"(duration: {duration:.1f}s)"
            )

            # Trigger stream restart after power cycle completes
            # This ensures stream reconnects immediately rather than waiting for watchdog
            self._trigger_stream_restart(camera_id)

        except Exception as e:
            with self._lock:
                status.state = PowerCycleState.FAILED
                status.error = str(e)
                status.completed_at = time.time()

            logger.error(f"[HUBITAT] Power cycle failed for camera {camera_id}: {e}")

    def _trigger_stream_restart(self, camera_id: str) -> None:
        """
        Trigger stream restart after power cycle completes.

        Called automatically after power cycle to ensure stream reconnects
        immediately rather than waiting for the watchdog to detect the issue.

        Args:
            camera_id: Camera serial number
        """
        if not self._stream_manager:
            logger.warning(
                f"[HUBITAT] Cannot restart stream for {camera_id} - StreamManager not set"
            )
            return

        try:
            logger.info(f"[HUBITAT] Triggering stream restart for {camera_id} after power cycle")

            # Use StreamManager's restart_stream method
            success = self._stream_manager.restart_stream(camera_id)

            if success:
                logger.info(f"[HUBITAT] Stream restart successful for {camera_id}")
                # Reset CameraStateTracker's failure count since we just power cycled
                self._state_tracker.register_success(camera_id)
            else:
                logger.warning(f"[HUBITAT] Stream restart returned false for {camera_id}")

        except Exception as e:
            logger.error(f"[HUBITAT] Stream restart error for {camera_id}: {e}")

    def _send_command(self, device_id: str, command: str) -> bool:
        """
        Send command to Hubitat Maker API.

        Args:
            device_id: Hubitat device ID
            command: Command to send ('on', 'off', 'toggle', 'refresh')

        Returns:
            True if successful, False otherwise
        """
        # Hubitat Maker API uses GET for commands (confirmed from 0_TILES project)
        url = (
            f"http://{self._hub_ip}/apps/api/{self._app_number}/"
            f"devices/{device_id}/{command}?access_token={self._api_token}"
        )

        try:
            response = requests.get(url, timeout=self.REQUEST_TIMEOUT)
            response.raise_for_status()
            logger.debug(f"[HUBITAT] Sent {command} to device {device_id}")
            return True

        except requests.exceptions.Timeout:
            logger.error(f"[HUBITAT] Timeout sending {command} to device {device_id}")
            return False

        except requests.exceptions.RequestException as e:
            logger.error(f"[HUBITAT] Failed to send {command} to device {device_id}: {e}")
            return False

    # ===== Public API Methods =====

    def power_cycle(self, camera_id: str) -> Dict[str, Any]:
        """
        Manually trigger power cycle for a camera.

        Args:
            camera_id: Camera serial number

        Returns:
            Dict with 'success' boolean and optional 'error' message
        """
        if not self._enabled:
            return {'success': False, 'error': 'Service not configured'}

        camera = self._camera_repo.get_camera(camera_id)
        if not camera:
            return {'success': False, 'error': f'Camera not found: {camera_id}'}

        if camera.get('power_supply') != 'hubitat':
            return {
                'success': False,
                'error': f'Camera {camera_id} is not hubitat-powered'
            }

        device_id = camera.get('power_supply_device_id')
        if not device_id:
            return {
                'success': False,
                'error': f'Camera {camera_id} has no power_supply_device_id configured'
            }

        # Force power cycle (ignore cooldown for manual trigger)
        with self._lock:
            self._last_power_cycle[camera_id] = 0  # Reset cooldown

        started = self._maybe_power_cycle(camera_id, str(device_id))

        if started:
            return {'success': True, 'message': 'Power cycle started'}
        else:
            return {'success': False, 'error': 'Power cycle could not be started'}

    def get_power_status(self, camera_id: str) -> Dict[str, Any]:
        """
        Get current power cycle status for a camera.

        Args:
            camera_id: Camera serial number

        Returns:
            Dict with power cycle status information
        """
        with self._lock:
            status = self._power_cycle_status.get(camera_id)
            if status:
                return status.to_dict()

        # No power cycle status - return idle state with camera config
        camera = self._camera_repo.get_camera(camera_id)
        device_id = camera.get('power_supply_device_id') if camera else None
        power_supply = camera.get('power_supply') if camera else None

        return {
            'camera_id': camera_id,
            'state': 'idle',
            'device_id': device_id,
            'power_supply': power_supply,
            'started_at': None,
            'completed_at': None,
            'error': None
        }

    def get_switch_devices(self) -> List[Dict[str, Any]]:
        """
        Get all Hubitat devices with Switch capability.

        Used by device picker UI to show available smart plugs.

        Returns:
            List of device dictionaries with id, label, capabilities
        """
        if not self._enabled:
            return []

        url = (
            f"http://{self._hub_ip}/apps/api/{self._app_number}/"
            f"devices/all?access_token={self._api_token}"
        )

        try:
            response = requests.get(url, timeout=self.REQUEST_TIMEOUT)
            response.raise_for_status()
            all_devices = response.json()

            # Filter for devices with Switch capability
            switch_devices = [
                {
                    'id': str(device.get('id')),
                    'label': device.get('label', device.get('name', 'Unknown')),
                    'capabilities': device.get('capabilities', [])
                }
                for device in all_devices
                if 'Switch' in device.get('capabilities', [])
            ]

            logger.debug(f"[HUBITAT] Found {len(switch_devices)} switch devices")
            return switch_devices

        except Exception as e:
            logger.error(f"[HUBITAT] Failed to get devices: {e}")
            return []

    def set_camera_device(self, camera_id: str, device_id: str) -> bool:
        """
        Set the power_supply_device_id for a camera.

        Saves to cameras.json for persistence.

        Args:
            camera_id: Camera serial number
            device_id: Hubitat device ID

        Returns:
            True if successful, False otherwise
        """
        camera = self._camera_repo.get_camera(camera_id)
        if not camera:
            logger.error(f"Camera not found: {camera_id}")
            return False

        # Use existing update_camera_setting method
        # Store as integer for consistency with Hubitat device IDs
        success = self._camera_repo.update_camera_setting(
            camera_id, 'power_supply_device_id', int(device_id)
        )

        if success:
            logger.info(
                f"[HUBITAT] Set power_supply_device_id={device_id} for camera {camera_id}"
            )

        return success

    def set_camera_power_supply(self, camera_id: str, power_supply: str) -> bool:
        """
        Set the power_supply type for a camera.

        Args:
            camera_id: Camera serial number
            power_supply: Power supply type ('hubitat', 'poe', 'none')

        Returns:
            True if successful, False otherwise
        """
        if power_supply not in self.POWER_SUPPLY_TYPES:
            logger.error(f"Invalid power_supply type: {power_supply}")
            return False

        camera = self._camera_repo.get_camera(camera_id)
        if not camera:
            logger.error(f"Camera not found: {camera_id}")
            return False

        success = self._camera_repo.update_camera_setting(
            camera_id, 'power_supply', power_supply
        )

        if success:
            logger.info(f"Set power_supply={power_supply} for camera {camera_id}")

            # If changing to hubitat, register callback if not already registered
            if power_supply == 'hubitat' and self._running:
                self._state_tracker.register_callback(camera_id, self._on_camera_state_change)
                logger.info(f"Registered power callback for newly configured camera {camera_id}")

        return success

    def get_hubitat_cameras(self) -> List[Dict[str, Any]]:
        """
        Get all cameras with power_supply='hubitat'.

        Returns:
            List of camera config dicts with serial numbers
        """
        return self._get_hubitat_cameras()

    def get_power_supply_types(self) -> List[str]:
        """
        Get list of valid power supply types.

        Returns:
            List of valid power_supply values
        """
        return self.POWER_SUPPLY_TYPES.copy()
