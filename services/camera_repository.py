#!/usr/bin/env python3
"""
Camera Repository - Pure Data Access Layer
Handles loading/saving camera configurations from JSON files
WITH HIDDEN CAMERA FILTERING
"""

import json
import os
import logging
from typing import Optional, Dict, List
from datetime import datetime

logger = logging.getLogger(__name__)


class CameraRepository:
    """
    Repository pattern for camera configuration data access
    Separates data access from business logic
    NOW WITH AUTOMATIC HIDDEN CAMERA FILTERING
    """

    def __init__(self, config_dir: str = './config'):
        """
        Initialize repository

        Args:
            config_dir: Directory containing configuration files
        """
        self.config_dir = config_dir
        self.cameras_file = os.path.join(config_dir, 'cameras.json')
        self.unifi_config_file = os.path.join(config_dir, 'unifi_protect.json')
        self.eufy_config_file = os.path.join(config_dir, 'eufy_bridge.json')
        self.reolink_config_file = os.path.join(config_dir, 'reolink.json')
        self.amcrest_config_file = os.path.join(config_dir, 'amcrest.json')
        
        # Load all configs
        self.cameras_data = self._load_json(self.cameras_file, {})
        self.unifi_config = self._load_json(self.unifi_config_file, {})
        self.eufy_config = self._load_json(self.eufy_config_file, {})
        self.reolink_config = self._load_json(self.reolink_config_file, {})
        self.amcrest_config = self._load_json(self.amcrest_config_file, {})


        logger.info(f"Loaded {self.get_camera_count()} cameras from {self.cameras_file}")

    def _load_json(self, filepath: str, default: dict) -> dict:
        """Load JSON file with error handling"""
        if not os.path.exists(filepath):
            logger.warning(f"Config file not found: {filepath}")
            return default

        try:
            with open(filepath, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in {filepath}: {e}")
            return default
        except Exception as e:
            logger.error(f"Error loading {filepath}: {e}")
            return default

    def _save_json(self, filepath: str, data: dict) -> bool:
        """Save data to JSON file"""
        try:
            with open(filepath, 'w') as f:
                json.dump(data, f, indent=2)
            logger.info(f"Saved configuration to {filepath}")
            return True
        except Exception as e:
            logger.error(f"Error saving {filepath}: {e}")
            return False

    # ===== HIDDEN CAMERA FILTERING =====

    def _filter_hidden(self, cameras: Dict[str, Dict], include_hidden: bool = False) -> Dict[str, Dict]:
        """
        Filter out hidden cameras unless explicitly requested

        Args:
            cameras: Dictionary of cameras {serial: config}
            include_hidden: If True, include hidden cameras. Default False.

        Returns:
            Filtered dictionary of cameras
        """
        if include_hidden:
            return cameras

        return {
            serial: config
            for serial, config in cameras.items()
            if not config.get('hidden', False)
        }

    # ===== Camera CRUD Operations =====

    def get_camera(self, serial: str) -> Optional[Dict]:
        """Get single camera configuration by serial number"""
        return self.cameras_data.get('devices', {}).get(serial)

    def get_all_cameras(self, include_hidden: bool = False) -> Dict[str, Dict]:
        """
        Get all camera configurations

        Args:
            include_hidden: If True, include hidden cameras. Default False.

        Returns:
            Dictionary of cameras {serial: config}
        """
        all_cameras = self.cameras_data.get('devices', {})
        return self._filter_hidden(all_cameras, include_hidden)

    def get_cameras_by_type(self, camera_type: str, include_hidden: bool = False) -> Dict[str, Dict]:
        """
        Get all cameras of a specific type

        Args:
            camera_type: 'eufy', 'unifi', or 'reolink'
            include_hidden: If True, include hidden cameras. Default False.
        """
        all_cameras = self.get_all_cameras(include_hidden=True)  # Get all first
        type_filtered = {
            serial: config
            for serial, config in all_cameras.items()
            if config.get('type') == camera_type
        }
        return self._filter_hidden(type_filtered, include_hidden)

    def get_cameras_by_capability(self, capability: str, include_hidden: bool = False) -> Dict[str, Dict]:
        """
        Get cameras with specific capability

        Args:
            capability: 'streaming', 'ptz', 'doorbell', etc.
            include_hidden: If True, include hidden cameras. Default False.
        """
        all_cameras = self.get_all_cameras(include_hidden=True)  # Get all first
        capability_filtered = {
            serial: config
            for serial, config in all_cameras.items()
            if capability in config.get('capabilities', [])
        }
        return self._filter_hidden(capability_filtered, include_hidden)

    def get_streaming_cameras(self, include_hidden: bool = False) -> Dict[str, Dict]:
        """
        Get all cameras with streaming capability

        Args:
            include_hidden: If True, include hidden cameras. Default False.
        """
        return self.get_cameras_by_capability('streaming', include_hidden)

    def get_ptz_cameras(self, include_hidden: bool = False) -> Dict[str, Dict]:
        """
        Get all PTZ-capable cameras

        Args:
            include_hidden: If True, include hidden cameras. Default False.
        """
        return self.get_cameras_by_capability('ptz', include_hidden)

    def get_camera_name(self, serial: str) -> Optional[str]:
        """Get camera display name"""
        camera = self.get_camera(serial)
        return camera.get('name') if camera else None

    def get_camera_count(self, include_hidden: bool = False) -> int:
        """
        Get total number of cameras

        Args:
            include_hidden: If True, include hidden cameras. Default False.

        Returns:
            Count of cameras (excluding hidden by default)
        """
        return len(self.get_all_cameras(include_hidden))

    def get_amcrest_config(self) -> Dict:
        """Get Amcrest configuration"""
        return self.amcrest_config
    
    def is_camera_hidden(self, serial: str) -> bool:
        """
        Check if a camera is hidden

        Args:
            serial: Camera serial number

        Returns:
            True if camera is hidden, False otherwise
        """
        camera = self.get_camera(serial)
        return camera.get('hidden', False) if camera else False

    def camera_exists(self, serial: str) -> bool:
        """Check if camera exists (regardless of hidden status)"""
        return serial in self.cameras_data.get('devices', {})

    def save_cameras(self, cameras_data: dict) -> bool:
        """
        Save camera configuration to file

        Args:
            cameras_data: Complete cameras data structure
        """
        cameras_data['last_updated'] = datetime.now().isoformat()
        self.cameras_data = cameras_data
        return self._save_json(self.cameras_file, cameras_data)

    def update_camera_setting(self, serial: str, key: str, value) -> bool:
        """
        Update a single setting for a camera and save to file.

        Args:
            serial: Camera serial number
            key: Setting key to update
            value: New value for the setting

        Returns:
            True if successful, False otherwise
        """
        camera = self.get_camera(serial)
        if not camera:
            logger.error(f"Camera not found: {serial}")
            return False

        camera[key] = value
        return self.save_cameras(self.cameras_data)

    def update_camera_ptz_reversal(self, serial: str, reversed_pan: bool = None, reversed_tilt: bool = None) -> bool:
        """
        Update PTZ reversal settings for a camera.

        Args:
            serial: Camera serial number
            reversed_pan: If provided, set reversed_pan to this value
            reversed_tilt: If provided, set reversed_tilt to this value

        Returns:
            True if successful, False otherwise
        """
        camera = self.get_camera(serial)
        if not camera:
            logger.error(f"Camera not found: {serial}")
            return False

        if reversed_pan is not None:
            camera['reversed_pan'] = reversed_pan
            logger.info(f"Set reversed_pan={reversed_pan} for camera {serial}")

        if reversed_tilt is not None:
            camera['reversed_tilt'] = reversed_tilt
            logger.info(f"Set reversed_tilt={reversed_tilt} for camera {serial}")

        return self.save_cameras(self.cameras_data)

    def get_camera_ptz_reversal(self, serial: str) -> Dict[str, bool]:
        """
        Get PTZ reversal settings for a camera.

        Args:
            serial: Camera serial number

        Returns:
            Dict with 'reversed_pan' and 'reversed_tilt' booleans
        """
        camera = self.get_camera(serial)
        if not camera:
            return {'reversed_pan': False, 'reversed_tilt': False}

        return {
            'reversed_pan': camera.get('reversed_pan', False),
            'reversed_tilt': camera.get('reversed_tilt', False)
        }

    # ===== Vendor-Specific Config Access =====

    def get_unifi_protect_config(self) -> Dict:
        """Get UniFi Protect console configuration"""
        return self.unifi_config

    def get_eufy_bridge_config(self) -> Dict:
        """Get Eufy bridge configuration"""
        return self.eufy_config

    def get_reolink_config(self) -> Dict:
        """Get Reolink NVR configuration"""
        return self.reolink_config

    # ===== Utility Methods =====

    def get_last_updated(self) -> str:
        """Get when cameras were last updated"""
        return self.cameras_data.get('last_updated', 'Never')

    def reload(self):
        """Reload all configurations from disk"""
        self.cameras_data = self._load_json(self.cameras_file, {})
        self.unifi_config = self._load_json(self.unifi_config_file, {})
        self.eufy_config = self._load_json(self.eufy_config_file, {})
        self.reolink_config = self._load_json(self.reolink_config_file, {})
        self.amcrest_config = self._load_json(self.amcrest_config_file, {})

        logger.info("Reloaded all configurations")
