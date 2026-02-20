#!/usr/bin/env python3
"""
Presence Service

Manages presence status for household members with support for:
- Manual toggle via UI
- Hubitat presence sensor integration
- PostgreSQL persistence via PostgREST

Architecture:
    Frontend UI (toggle button)
           |
           | (POST /api/presence/<person>/toggle)
           v
    PresenceService
           |
           | (HTTP REST to PostgREST)
           v
    PostgreSQL presence table

Hubitat Integration (optional):
    Hubitat Maker API → PresenceService → PostgreSQL
    (polls presence sensors periodically)

Author: NVR System
Date: January 28, 2026
"""

import logging
import os
import threading
import time
import requests
from typing import Optional, Dict, List, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class PresenceStatus:
    """
    Presence status for a person.

    Attributes:
        person_name: Name of the person
        is_present: Current presence status
        hubitat_device_id: Associated Hubitat presence sensor device ID
        last_changed_at: Timestamp of last status change
        last_changed_by: Source of last change (manual, hubitat, api)
    """
    person_name: str
    is_present: bool = False
    hubitat_device_id: Optional[str] = None
    last_changed_at: Optional[str] = None
    last_changed_by: str = 'manual'

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'person_name': self.person_name,
            'is_present': self.is_present,
            'hubitat_device_id': self.hubitat_device_id,
            'last_changed_at': self.last_changed_at,
            'last_changed_by': self.last_changed_by
        }


class PresenceService:
    """
    Service for managing household presence status.

    Supports manual toggle from UI and optional Hubitat presence sensor integration.
    Persists state to PostgreSQL via PostgREST.

    Configuration:
        Environment variables:
        - NVR_POSTGREST_URL: PostgREST server URL (default: http://postgrest:3001)
        - NVR_HUBITAT_API_TOKEN_4: Maker API access token (for presence sensors)
        - NVR_HUBITAT_API_NUMBER_4: Maker API app number
        - NVR_HUBITAT_HUB_IP_4: Hub IP address (default: hubitat.local)

    Usage:
        service = PresenceService()
        service.start()

        # Get all presence statuses
        statuses = service.get_all_presence()

        # Toggle presence
        service.toggle_presence("Elfege")

        # Set presence directly
        service.set_presence("Jessica", True)

    Thread Safety:
        All public methods are thread-safe via internal locking.
    """

    # Hubitat polling interval for presence sensors (seconds)
    HUBITAT_POLL_INTERVAL = 60

    # HTTP request timeout (seconds)
    REQUEST_TIMEOUT = 10

    def __init__(
        self,
        postgrest_url: Optional[str] = None,
        hub_ip: Optional[str] = None
    ):
        """
        Initialize the presence service.

        Args:
            postgrest_url: PostgREST server URL (default: from env)
            hub_ip: Hubitat hub IP address (default: from env or hubitat.local)
        """
        # PostgREST configuration
        self._postgrest_url = postgrest_url or os.getenv(
            'NVR_POSTGREST_URL', 'http://postgrest:3001'
        )

        # Hubitat configuration for presence sensor polling (Hub 4)
        self._api_token = os.environ.get('NVR_HUBITAT_API_TOKEN_4', '')
        self._app_number = os.environ.get('NVR_HUBITAT_API_NUMBER_4', '')
        self._hub_ip = hub_ip or os.environ.get('NVR_HUBITAT_HUB_IP_4', 'hubitat.local')

        # Track if Hubitat integration is enabled
        self._hubitat_enabled = bool(self._api_token and self._app_number)

        # Thread safety
        self._lock = threading.RLock()

        # Hubitat polling thread
        self._poll_thread: Optional[threading.Thread] = None
        self._running = False

        logger.info(
            f"[PRESENCE] Service initialized (PostgREST: {self._postgrest_url}, "
            f"Hubitat: {'enabled' if self._hubitat_enabled else 'disabled'})"
        )

    def start(self) -> None:
        """
        Start the presence service.

        Starts Hubitat polling thread if Hubitat integration is enabled.
        """
        with self._lock:
            if self._running:
                return

            self._running = True

            # Start Hubitat polling if enabled
            if self._hubitat_enabled:
                self._poll_thread = threading.Thread(
                    target=self._hubitat_poll_loop,
                    name="presence-hubitat-poll",
                    daemon=True
                )
                self._poll_thread.start()
                logger.info("[PRESENCE] Hubitat polling thread started")

    def stop(self) -> None:
        """Stop the presence service."""
        with self._lock:
            self._running = False

    def get_all_presence(self) -> List[PresenceStatus]:
        """
        Get presence status for all people.

        Returns:
            List of PresenceStatus objects
        """
        try:
            response = requests.get(
                f"{self._postgrest_url}/presence",
                headers={'Accept': 'application/json'},
                timeout=self.REQUEST_TIMEOUT
            )
            response.raise_for_status()
            data = response.json()

            return [
                PresenceStatus(
                    person_name=row['person_name'],
                    is_present=row['is_present'],
                    hubitat_device_id=row.get('hubitat_device_id'),
                    last_changed_at=row.get('last_changed_at'),
                    last_changed_by=row.get('last_changed_by', 'manual')
                )
                for row in data
            ]
        except Exception as e:
            logger.error(f"[PRESENCE] Failed to get presence: {e}")
            return []

    def get_presence(self, person_name: str) -> Optional[PresenceStatus]:
        """
        Get presence status for a specific person.

        Args:
            person_name: Name of the person

        Returns:
            PresenceStatus or None if not found
        """
        try:
            response = requests.get(
                f"{self._postgrest_url}/presence",
                params={'person_name': f'eq.{person_name}'},
                headers={'Accept': 'application/json'},
                timeout=self.REQUEST_TIMEOUT
            )
            response.raise_for_status()
            data = response.json()

            if data:
                row = data[0]
                return PresenceStatus(
                    person_name=row['person_name'],
                    is_present=row['is_present'],
                    hubitat_device_id=row.get('hubitat_device_id'),
                    last_changed_at=row.get('last_changed_at'),
                    last_changed_by=row.get('last_changed_by', 'manual')
                )
            return None
        except Exception as e:
            logger.error(f"[PRESENCE] Failed to get presence for {person_name}: {e}")
            return None

    def set_presence(
        self,
        person_name: str,
        is_present: bool,
        source: str = 'manual'
    ) -> bool:
        """
        Set presence status for a person.

        Args:
            person_name: Name of the person
            is_present: New presence status
            source: Source of change (manual, hubitat, api)

        Returns:
            True if successful, False otherwise
        """
        with self._lock:
            try:
                response = requests.patch(
                    f"{self._postgrest_url}/presence",
                    params={'person_name': f'eq.{person_name}'},
                    json={
                        'is_present': is_present,
                        'last_changed_by': source,
                        'last_changed_at': 'now()'
                    },
                    headers={
                        'Content-Type': 'application/json',
                        'Prefer': 'return=minimal'
                    },
                    timeout=self.REQUEST_TIMEOUT
                )
                response.raise_for_status()

                logger.info(
                    f"[PRESENCE] {person_name} set to "
                    f"{'present' if is_present else 'away'} (source: {source})"
                )
                return True

            except Exception as e:
                logger.error(
                    f"[PRESENCE] Failed to set presence for {person_name}: {e}"
                )
                return False

    def toggle_presence(self, person_name: str) -> Optional[bool]:
        """
        Toggle presence status for a person.

        Args:
            person_name: Name of the person

        Returns:
            New presence status, or None if failed
        """
        current = self.get_presence(person_name)
        if current is None:
            logger.error(f"[PRESENCE] Person not found: {person_name}")
            return None

        new_status = not current.is_present
        if self.set_presence(person_name, new_status, source='manual'):
            return new_status
        return None

    def add_person(
        self,
        person_name: str,
        hubitat_device_id: Optional[str] = None
    ) -> bool:
        """
        Add a new person to presence tracking.

        Args:
            person_name: Name of the person
            hubitat_device_id: Optional Hubitat presence sensor device ID

        Returns:
            True if successful, False otherwise
        """
        try:
            response = requests.post(
                f"{self._postgrest_url}/presence",
                json={
                    'person_name': person_name,
                    'is_present': False,
                    'hubitat_device_id': hubitat_device_id
                },
                headers={
                    'Content-Type': 'application/json',
                    'Prefer': 'return=minimal'
                },
                timeout=self.REQUEST_TIMEOUT
            )
            response.raise_for_status()

            logger.info(f"[PRESENCE] Added person: {person_name}")
            return True

        except Exception as e:
            logger.error(f"[PRESENCE] Failed to add person {person_name}: {e}")
            return False

    def remove_person(self, person_name: str) -> bool:
        """
        Remove a person from presence tracking.

        Args:
            person_name: Name of the person

        Returns:
            True if successful, False otherwise
        """
        try:
            response = requests.delete(
                f"{self._postgrest_url}/presence",
                params={'person_name': f'eq.{person_name}'},
                timeout=self.REQUEST_TIMEOUT
            )
            response.raise_for_status()

            logger.info(f"[PRESENCE] Removed person: {person_name}")
            return True

        except Exception as e:
            logger.error(f"[PRESENCE] Failed to remove person {person_name}: {e}")
            return False

    def set_hubitat_device(
        self,
        person_name: str,
        device_id: Optional[str]
    ) -> bool:
        """
        Associate a Hubitat presence sensor with a person.

        Args:
            person_name: Name of the person
            device_id: Hubitat device ID (or None to remove association)

        Returns:
            True if successful, False otherwise
        """
        try:
            response = requests.patch(
                f"{self._postgrest_url}/presence",
                params={'person_name': f'eq.{person_name}'},
                json={'hubitat_device_id': device_id},
                headers={
                    'Content-Type': 'application/json',
                    'Prefer': 'return=minimal'
                },
                timeout=self.REQUEST_TIMEOUT
            )
            response.raise_for_status()

            logger.info(
                f"[PRESENCE] Set Hubitat device {device_id} for {person_name}"
            )
            return True

        except Exception as e:
            logger.error(
                f"[PRESENCE] Failed to set Hubitat device for {person_name}: {e}"
            )
            return False

    def get_presence_devices(self) -> List[Dict[str, Any]]:
        """
        Get all Hubitat devices with PresenceSensor capability.

        Used by UI to show available presence sensors for association.

        Returns:
            List of device dictionaries with id, label, capabilities
        """
        if not self._hubitat_enabled:
            return []

        url = (
            f"http://{self._hub_ip}/apps/api/{self._app_number}/"
            f"devices/all?access_token={self._api_token}"
        )

        try:
            response = requests.get(url, timeout=self.REQUEST_TIMEOUT)
            response.raise_for_status()
            all_devices = response.json()

            # Filter for devices with PresenceSensor capability
            presence_devices = [
                {
                    'id': str(device.get('id')),
                    'label': device.get('label', device.get('name', 'Unknown')),
                    'capabilities': device.get('capabilities', [])
                }
                for device in all_devices
                if 'PresenceSensor' in device.get('capabilities', [])
            ]

            logger.debug(
                f"[PRESENCE] Found {len(presence_devices)} presence sensor devices"
            )
            return presence_devices

        except Exception as e:
            logger.error(f"[PRESENCE] Failed to get Hubitat devices: {e}")
            return []

    def _hubitat_poll_loop(self) -> None:
        """
        Background thread that polls Hubitat presence sensors.

        Updates PostgreSQL when presence status changes.
        """
        logger.info("[PRESENCE] Hubitat poll loop starting")

        while self._running:
            try:
                self._sync_hubitat_presence()
            except Exception as e:
                logger.error(f"[PRESENCE] Hubitat poll error: {e}")

            # Sleep in small increments to allow clean shutdown
            for _ in range(self.HUBITAT_POLL_INTERVAL):
                if not self._running:
                    break
                time.sleep(1)

        logger.info("[PRESENCE] Hubitat poll loop stopped")

    def _sync_hubitat_presence(self) -> None:
        """
        Sync presence status from Hubitat devices to PostgreSQL.

        For each person with a hubitat_device_id, query the device's
        presence state and update PostgreSQL if changed.
        """
        # Get all people with Hubitat device associations
        people = self.get_all_presence()
        people_with_devices = [
            p for p in people if p.hubitat_device_id
        ]

        if not people_with_devices:
            return

        for person in people_with_devices:
            try:
                # Query Hubitat device state
                device_state = self._get_hubitat_device_state(
                    person.hubitat_device_id
                )

                if device_state is None:
                    continue

                # Hubitat presence sensor has 'presence' attribute
                # Values: 'present' or 'not present'
                hubitat_present = (
                    device_state.get('presence', '').lower() == 'present'
                )

                # Update if changed
                if hubitat_present != person.is_present:
                    self.set_presence(
                        person.person_name,
                        hubitat_present,
                        source='hubitat'
                    )

            except Exception as e:
                logger.error(
                    f"[PRESENCE] Failed to sync Hubitat for {person.person_name}: {e}"
                )

    def _get_hubitat_device_state(
        self,
        device_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get current state of a Hubitat device.

        Args:
            device_id: Hubitat device ID

        Returns:
            Dictionary of device attributes, or None if failed
        """
        url = (
            f"http://{self._hub_ip}/apps/api/{self._app_number}/"
            f"devices/{device_id}?access_token={self._api_token}"
        )

        try:
            response = requests.get(url, timeout=self.REQUEST_TIMEOUT)
            response.raise_for_status()
            device = response.json()

            # Extract attributes into flat dictionary
            attributes = {}
            for attr in device.get('attributes', []):
                attr_name = attr.get('name')
                attr_value = attr.get('currentValue')
                if attr_name:
                    attributes[attr_name] = attr_value

            return attributes

        except Exception as e:
            logger.error(
                f"[PRESENCE] Failed to get Hubitat device {device_id} state: {e}"
            )
            return None
