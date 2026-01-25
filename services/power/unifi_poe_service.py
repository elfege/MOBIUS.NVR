#!/usr/bin/env python3
"""
UniFi POE Power Service

Provides power cycling functionality for POE-powered cameras via UniFi Network
Controller. Controls individual switch ports to power cycle cameras.

Architecture:
    CameraStateTracker (detects OFFLINE state)
           |
           | (callback)
           v
    UnifiPoePowerService
           |
           | (HTTP POST to UniFi Controller API)
           v
    UniFi Switch → POE Port Cycle

Features:
- Automatic power cycling when camera goes OFFLINE
- 5-minute cooldown between power cycles per camera
- Manual power control via API endpoints
- Support for multiple UniFi switches
- Switch/port discovery for camera configuration

UniFi Controller API:
    Standard Controller: /api/s/{site}/cmd/devmgr
    UDM/UCG: /proxy/network/api/s/{site}/cmd/devmgr

    Commands:
    - power-cycle: Cycle POE port (off then on automatically)
    - set-poe-mode: Set POE mode ('auto' for on, 'off' for disabled)

    Required: Local user account (not Ubiquiti SSO)

Author: NVR System
Date: January 24, 2026
"""

import logging
import os
import threading
import time
import requests
import urllib3
from typing import TYPE_CHECKING, Optional, Dict, List, Any
from dataclasses import dataclass
from enum import Enum

# Suppress InsecureRequestWarning for self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

# Type hints for lazy imports (avoid circular dependencies)
if TYPE_CHECKING:
    from services.camera_state_tracker import CameraStateTracker, CameraState, CameraAvailability


class PoeCycleState(Enum):
    """
    State of a POE power cycle operation for a camera.

    IDLE: No power cycle in progress
    CYCLING: POE port being cycled
    COMPLETE: Power cycle finished successfully
    FAILED: Power cycle failed
    """
    IDLE = "idle"
    CYCLING = "cycling"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class PoeCycleStatus:
    """
    Status information for a camera's POE power cycle operation.

    Attributes:
        camera_id: Camera serial number
        state: Current power cycle state
        switch_mac: MAC address of the UniFi switch
        port: Port number on the switch
        started_at: Timestamp when power cycle started
        completed_at: Timestamp when power cycle completed
        error: Error message if failed
    """
    camera_id: str
    state: PoeCycleState = PoeCycleState.IDLE
    switch_mac: Optional[str] = None
    port: Optional[int] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'camera_id': self.camera_id,
            'state': self.state.value,
            'switch_mac': self.switch_mac,
            'port': self.port,
            'started_at': self.started_at,
            'completed_at': self.completed_at,
            'error': self.error
        }


class UnifiPoePowerService:
    """
    Service for power cycling cameras via UniFi POE switches.

    Uses UniFi Network Controller API to control POE ports on managed switches.
    Integrates with CameraStateTracker via callbacks to automatically power cycle
    cameras that become OFFLINE.

    Configuration:
        Environment variables:
        - UNIFI_CONTROLLER_HOST: Controller IP/hostname (e.g., 192.168.10.3)
        - UNIFI_CONTROLLER_USERNAME: Local user account username
        - UNIFI_CONTROLLER_PASSWORD: Local user account password
        - UNIFI_CONTROLLER_SITE: Site name (default: 'default')
        - UNIFI_CONTROLLER_TYPE: 'udm' or 'controller' (default: 'udm')

        Camera config (cameras.json):
        - power_supply: "poe" - Camera is powered by POE
        - poe_switch_mac: "aa:bb:cc:dd:ee:ff" - Switch MAC address
        - poe_port: 12 - Port number on the switch (1-48)

    Usage:
        service = UnifiPoePowerService(camera_repo, camera_state_tracker)
        service.start()

        # Manual power cycle
        service.power_cycle("68d49398005cf203e400043f")

        # Get all switches
        switches = service.get_switches()

        # Get ports on a switch
        ports = service.get_switch_ports("aa:bb:cc:dd:ee:ff")

    Thread Safety:
        All public methods are thread-safe via internal locking.
    """

    # Minimum time between power cycles for the same camera (seconds)
    POWER_CYCLE_COOLDOWN_SECONDS = 300  # 5 minutes

    # HTTP request timeout (seconds)
    REQUEST_TIMEOUT = 15

    # Session refresh interval (seconds)
    SESSION_REFRESH_INTERVAL = 3600  # 1 hour

    def __init__(
        self,
        camera_repo,
        camera_state_tracker: "CameraStateTracker",
        controller_host: Optional[str] = None
    ):
        """
        Initialize the UniFi POE power service.

        Args:
            camera_repo: CameraRepository instance for camera configuration
            camera_state_tracker: CameraStateTracker for health monitoring
            controller_host: UniFi controller IP/hostname (default: from env)
        """
        self._camera_repo = camera_repo
        self._state_tracker = camera_state_tracker

        # Load configuration from environment
        self._controller_host = controller_host or os.environ.get(
            'UNIFI_CONTROLLER_HOST', ''
        )
        self._username = os.environ.get('UNIFI_CONTROLLER_USERNAME', '')
        self._password = os.environ.get('UNIFI_CONTROLLER_PASSWORD', '')
        self._site = os.environ.get('UNIFI_CONTROLLER_SITE', 'default')
        self._controller_type = os.environ.get(
            'UNIFI_CONTROLLER_TYPE', 'udm'
        ).lower()

        # API prefix differs between UDM and standard controller
        if self._controller_type == 'udm':
            self._api_prefix = '/proxy/network'
        else:
            self._api_prefix = ''

        # Track power cycle state per camera
        self._power_cycle_status: Dict[str, PoeCycleStatus] = {}
        self._last_power_cycle: Dict[str, float] = {}  # camera_id -> timestamp

        # HTTP session for authenticated API calls
        self._session: Optional[requests.Session] = None
        self._session_created_at: float = 0
        self._authenticated = False

        # Thread safety
        self._lock = threading.RLock()

        # Service state
        self._running = False
        self._callbacks_registered = False

        # Validate configuration
        self._enabled = bool(
            self._controller_host and
            self._username and
            self._password
        )

        if self._enabled:
            logger.info(
                f"UnifiPoePowerService initialized (controller: {self._controller_host}, "
                f"site: {self._site}, type: {self._controller_type})"
            )
        else:
            logger.warning(
                "UnifiPoePowerService DISABLED - missing UNIFI_CONTROLLER_HOST, "
                "UNIFI_CONTROLLER_USERNAME, or UNIFI_CONTROLLER_PASSWORD"
            )

    def start(self) -> None:
        """
        Start the power service and register callbacks.

        Registers state change callbacks with CameraStateTracker for all
        cameras with power_supply='poe'. Callbacks trigger automatic
        power cycling when cameras go OFFLINE.
        """
        if not self._enabled:
            logger.warning("UnifiPoePowerService not starting - credentials not configured")
            return

        if self._running:
            logger.warning("UnifiPoePowerService already running")
            return

        self._running = True
        self._register_callbacks()
        logger.info("UnifiPoePowerService started")

    def stop(self) -> None:
        """
        Stop the power service and close session.
        """
        if not self._running:
            return

        self._running = False
        self._close_session()
        logger.info("UnifiPoePowerService stopped")

    def is_enabled(self) -> bool:
        """Check if service is enabled (credentials configured)."""
        return self._enabled

    def _register_callbacks(self) -> None:
        """
        Register state change callbacks for POE-powered cameras.
        """
        if self._callbacks_registered:
            return

        poe_cameras = self._get_poe_cameras()
        logger.info(f"Registering callbacks for {len(poe_cameras)} POE-powered cameras")

        for camera in poe_cameras:
            serial = camera.get('serial')
            if serial:
                self._state_tracker.register_callback(serial, self._on_camera_state_change)
                logger.debug(f"Registered POE power callback for camera {serial}")

        self._callbacks_registered = True

    def _get_poe_cameras(self) -> List[Dict]:
        """
        Get all cameras with power_supply='poe'.

        Returns:
            List of camera configuration dictionaries
        """
        all_cameras = self._camera_repo.get_all_cameras(include_hidden=True)
        return [
            {**config, 'serial': serial}
            for serial, config in all_cameras.items()
            if config.get('power_supply') == 'poe'
        ]

    def _on_camera_state_change(self, state: "CameraState") -> None:
        """
        Handle camera state changes from CameraStateTracker.

        Triggers power cycle when camera transitions to OFFLINE state.

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

        # Verify camera is POE-powered
        camera = self._camera_repo.get_camera(camera_id)
        if not camera or camera.get('power_supply') != 'poe':
            return

        # Check if switch and port are configured
        switch_mac = camera.get('poe_switch_mac')
        port = camera.get('poe_port')

        if not switch_mac or not port:
            logger.warning(
                f"[UNIFI-POE] Camera {camera_id} is POE-powered but has no "
                "poe_switch_mac/poe_port configured - skipping power cycle"
            )
            return

        # Trigger power cycle (with cooldown check)
        self._maybe_power_cycle(camera_id, switch_mac, port)

    def _maybe_power_cycle(self, camera_id: str, switch_mac: str, port: int) -> bool:
        """
        Attempt to power cycle a camera if cooldown has elapsed.

        Args:
            camera_id: Camera serial number
            switch_mac: MAC address of the switch
            port: Port number on the switch

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
                    f"[UNIFI-POE] Camera {camera_id} power cycle skipped - "
                    f"cooldown active ({remaining:.0f}s remaining)"
                )
                return False

            # Check if already in progress
            status = self._power_cycle_status.get(camera_id)
            if status and status.state == PoeCycleState.CYCLING:
                logger.info(f"[UNIFI-POE] Camera {camera_id} power cycle already in progress")
                return False

            # Start power cycle in background thread
            threading.Thread(
                target=self._do_power_cycle,
                args=(camera_id, switch_mac, port),
                name=f"UnifiPoeCycle-{camera_id}",
                daemon=True
            ).start()

            return True

    def _do_power_cycle(self, camera_id: str, switch_mac: str, port: int) -> None:
        """
        Execute POE power cycle via UniFi Controller API.

        Uses the 'power-cycle' command which turns the port off then on.

        Args:
            camera_id: Camera serial number
            switch_mac: MAC address of the switch
            port: Port number on the switch
        """
        status = PoeCycleStatus(
            camera_id=camera_id,
            switch_mac=switch_mac,
            port=port,
            state=PoeCycleState.CYCLING,
            started_at=time.time()
        )

        with self._lock:
            self._power_cycle_status[camera_id] = status
            self._last_power_cycle[camera_id] = time.time()

        try:
            logger.warning(
                f"[UNIFI-POE] Power cycling camera {camera_id} "
                f"(switch: {switch_mac}, port: {port})"
            )

            # Ensure authenticated session
            if not self._ensure_session():
                raise Exception("Failed to authenticate with UniFi Controller")

            # Normalize MAC address (remove colons/dashes, lowercase)
            normalized_mac = switch_mac.replace(':', '').replace('-', '').lower()

            # Send power-cycle command
            # The API expects MAC without separators
            url = (
                f"https://{self._controller_host}"
                f"{self._api_prefix}/api/s/{self._site}/cmd/devmgr"
            )

            payload = {
                'cmd': 'power-cycle',
                'mac': normalized_mac,
                'port_idx': port
            }

            response = self._session.post(
                url,
                json=payload,
                verify=False,
                timeout=self.REQUEST_TIMEOUT
            )

            if response.status_code == 401:
                # Session expired, re-authenticate and retry
                logger.warning("[UNIFI-POE] Session expired, re-authenticating...")
                self._authenticated = False
                if not self._ensure_session():
                    raise Exception("Re-authentication failed")

                response = self._session.post(
                    url,
                    json=payload,
                    verify=False,
                    timeout=self.REQUEST_TIMEOUT
                )

            response.raise_for_status()
            result = response.json()

            # Check for API-level errors
            if result.get('meta', {}).get('rc') != 'ok':
                error_msg = result.get('meta', {}).get('msg', 'Unknown error')
                raise Exception(f"API error: {error_msg}")

            # Success
            with self._lock:
                status.state = PoeCycleState.COMPLETE
                status.completed_at = time.time()

            duration = status.completed_at - status.started_at
            logger.info(
                f"[UNIFI-POE] Power cycle complete for camera {camera_id} "
                f"(duration: {duration:.1f}s)"
            )

        except Exception as e:
            with self._lock:
                status.state = PoeCycleState.FAILED
                status.error = str(e)
                status.completed_at = time.time()

            logger.error(f"[UNIFI-POE] Power cycle failed for camera {camera_id}: {e}")

    def _ensure_session(self) -> bool:
        """
        Ensure we have an authenticated session with UniFi Controller.

        Returns:
            True if session is valid, False otherwise
        """
        with self._lock:
            # Check if existing session is still valid
            if (self._session and self._authenticated and
                    time.time() - self._session_created_at < self.SESSION_REFRESH_INTERVAL):
                return True

            # Need new session
            return self._authenticate()

    def _authenticate(self) -> bool:
        """
        Authenticate with UniFi Controller and create session.

        Returns:
            True if authentication successful, False otherwise
        """
        try:
            self._close_session()
            self._session = requests.Session()

            # Login endpoint differs between controller types
            if self._controller_type == 'udm':
                login_url = f"https://{self._controller_host}/api/auth/login"
            else:
                login_url = f"https://{self._controller_host}/api/login"

            login_data = {
                "username": self._username,
                "password": self._password
            }

            response = self._session.post(
                login_url,
                json=login_data,
                verify=False,
                timeout=self.REQUEST_TIMEOUT
            )

            if response.status_code == 200:
                self._authenticated = True
                self._session_created_at = time.time()
                logger.info("[UNIFI-POE] Authenticated with UniFi Controller")
                return True
            else:
                logger.error(
                    f"[UNIFI-POE] Authentication failed: {response.status_code} - "
                    f"{response.text[:200]}"
                )
                self._close_session()
                return False

        except Exception as e:
            logger.error(f"[UNIFI-POE] Authentication error: {e}")
            self._close_session()
            return False

    def _close_session(self) -> None:
        """Close HTTP session if open."""
        if self._session:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None
            self._authenticated = False

    # ===== Public API Methods =====

    def power_cycle(self, camera_id: str) -> Dict[str, Any]:
        """
        Manually trigger POE power cycle for a camera.

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

        if camera.get('power_supply') != 'poe':
            return {
                'success': False,
                'error': f'Camera {camera_id} is not POE-powered'
            }

        switch_mac = camera.get('poe_switch_mac')
        port = camera.get('poe_port')

        if not switch_mac or not port:
            return {
                'success': False,
                'error': f'Camera {camera_id} has no poe_switch_mac/poe_port configured'
            }

        # Force power cycle (ignore cooldown for manual trigger)
        with self._lock:
            self._last_power_cycle[camera_id] = 0  # Reset cooldown

        started = self._maybe_power_cycle(camera_id, switch_mac, port)

        if started:
            return {'success': True, 'message': 'POE power cycle started'}
        else:
            return {'success': False, 'error': 'Power cycle could not be started'}

    def get_power_status(self, camera_id: str) -> Dict[str, Any]:
        """
        Get current POE power cycle status for a camera.

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
        switch_mac = camera.get('poe_switch_mac') if camera else None
        port = camera.get('poe_port') if camera else None
        power_supply = camera.get('power_supply') if camera else None

        return {
            'camera_id': camera_id,
            'state': 'idle',
            'switch_mac': switch_mac,
            'port': port,
            'power_supply': power_supply,
            'started_at': None,
            'completed_at': None,
            'error': None
        }

    def get_switches(self) -> List[Dict[str, Any]]:
        """
        Get all UniFi switches from the controller.

        Returns:
            List of switch dictionaries with mac, name, model, port_count
        """
        if not self._enabled:
            return []

        try:
            if not self._ensure_session():
                return []

            url = (
                f"https://{self._controller_host}"
                f"{self._api_prefix}/api/s/{self._site}/stat/device"
            )

            response = self._session.get(
                url,
                verify=False,
                timeout=self.REQUEST_TIMEOUT
            )
            response.raise_for_status()
            result = response.json()

            # Filter for switches (devices with port_table)
            switches = []
            for device in result.get('data', []):
                # Switches have type 'usw' or have port_table
                if device.get('type') == 'usw' or 'port_table' in device:
                    mac = device.get('mac', '').lower()
                    # Format MAC with colons for display
                    formatted_mac = ':'.join(
                        mac[i:i+2] for i in range(0, len(mac), 2)
                    )
                    switches.append({
                        'mac': formatted_mac,
                        'name': device.get('name', 'Unknown Switch'),
                        'model': device.get('model', 'Unknown'),
                        'port_count': len(device.get('port_table', [])),
                        'adopted': device.get('adopted', False)
                    })

            logger.debug(f"[UNIFI-POE] Found {len(switches)} switches")
            return switches

        except Exception as e:
            logger.error(f"[UNIFI-POE] Failed to get switches: {e}")
            return []

    def get_switch_ports(self, switch_mac: str) -> List[Dict[str, Any]]:
        """
        Get all ports on a specific switch with POE status.

        Args:
            switch_mac: MAC address of the switch

        Returns:
            List of port dictionaries with port_idx, name, poe_mode, poe_power
        """
        if not self._enabled:
            return []

        try:
            if not self._ensure_session():
                return []

            # Normalize MAC
            normalized_mac = switch_mac.replace(':', '').replace('-', '').lower()

            url = (
                f"https://{self._controller_host}"
                f"{self._api_prefix}/api/s/{self._site}/stat/device/{normalized_mac}"
            )

            response = self._session.get(
                url,
                verify=False,
                timeout=self.REQUEST_TIMEOUT
            )
            response.raise_for_status()
            result = response.json()

            ports = []
            device_data = result.get('data', [{}])[0]
            port_table = device_data.get('port_table', [])

            for port in port_table:
                port_info = {
                    'port_idx': port.get('port_idx'),
                    'name': port.get('name', f"Port {port.get('port_idx')}"),
                    'poe_capable': port.get('poe_caps', 0) > 0,
                    'poe_mode': port.get('poe_mode', 'off'),
                    'poe_power': port.get('poe_power', 0),
                    'speed': port.get('speed', 0),
                    'up': port.get('up', False)
                }
                ports.append(port_info)

            logger.debug(f"[UNIFI-POE] Found {len(ports)} ports on switch {switch_mac}")
            return ports

        except Exception as e:
            logger.error(f"[UNIFI-POE] Failed to get switch ports: {e}")
            return []

    def set_camera_poe_config(
        self,
        camera_id: str,
        switch_mac: str,
        port: int
    ) -> bool:
        """
        Set the POE configuration for a camera.

        Saves to cameras.json for persistence.

        Args:
            camera_id: Camera serial number
            switch_mac: MAC address of the switch
            port: Port number on the switch

        Returns:
            True if successful, False otherwise
        """
        camera = self._camera_repo.get_camera(camera_id)
        if not camera:
            logger.error(f"Camera not found: {camera_id}")
            return False

        # Update both fields
        success1 = self._camera_repo.update_camera_setting(
            camera_id, 'poe_switch_mac', switch_mac
        )
        success2 = self._camera_repo.update_camera_setting(
            camera_id, 'poe_port', int(port)
        )

        if success1 and success2:
            logger.info(
                f"[UNIFI-POE] Set POE config for camera {camera_id}: "
                f"switch={switch_mac}, port={port}"
            )
            return True

        return False

    def get_poe_cameras(self) -> List[Dict[str, Any]]:
        """
        Get all cameras with power_supply='poe'.

        Returns:
            List of camera config dicts with serial numbers
        """
        return self._get_poe_cameras()
