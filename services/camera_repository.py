#!/usr/bin/env python3
"""
Camera Repository - Data Access Layer with Database-First Loading

Loads camera configurations from PostgreSQL database (via PostgREST) with
automatic fallback to cameras.json when the database is unavailable.

The database is the runtime source of truth. cameras.json remains as the
canonical reset source and is used for auto-sync of new cameras.

All consuming services use this repository's interface and are unaffected
by the storage backend change (DB vs JSON).
"""

import json
import os
import logging
import requests
from typing import Optional, Dict
from datetime import datetime

logger = logging.getLogger(__name__)

POSTGREST_URL = os.getenv('POSTGREST_URL', 'http://postgrest:3001')


class CameraRepository:
    """
    Repository pattern for camera configuration data access.
    DB-first with JSON fallback. Hidden camera filtering built-in.

    Storage priority:
    1. PostgreSQL database (via PostgREST) - runtime source of truth
    2. cameras.json (filesystem) - fallback if DB unavailable, canonical for resets

    The in-memory cache (self.cameras_data) is populated from whichever source
    loads successfully. All read methods operate on this cache for performance.
    Write methods update both DB and cache (and optionally JSON for backup).
    """

    def __init__(self, config_dir: str = './config'):
        """
        Initialize repository. Attempts to load from database first,
        falls back to JSON if database is unavailable.

        Args:
            config_dir: Directory containing configuration files
        """
        self.config_dir = config_dir
        self.cameras_file = os.path.join(config_dir, 'cameras.json')
        self.unifi_config_file = os.path.join(config_dir, 'unifi_protect.json')
        self.eufy_config_file = os.path.join(config_dir, 'eufy_bridge.json')
        self.reolink_config_file = os.path.join(config_dir, 'reolink.json')
        self.amcrest_config_file = os.path.join(config_dir, 'amcrest.json')

        # Track which source we loaded from
        self._source = 'none'

        # Load camera data: DB first, JSON fallback
        self.cameras_data = self._load_cameras_from_db()
        if self.cameras_data.get('devices'):
            self._source = 'database'
            logger.info(
                f"Loaded {self.get_camera_count(include_hidden=True)} cameras from database")
        else:
            # DB unavailable or empty: fall back to JSON
            self.cameras_data = self._load_json(self.cameras_file, {})
            if self.cameras_data.get('devices'):
                self._source = 'json'
                logger.warning(
                    f"Database unavailable, loaded {self.get_camera_count(include_hidden=True)} "
                    f"cameras from {self.cameras_file}")
            else:
                logger.error("No camera data available from database or JSON")

        # Vendor configs always from JSON (static infrastructure config)
        self.unifi_config = self._load_json(self.unifi_config_file, {})
        self.eufy_config = self._load_json(self.eufy_config_file, {})
        self.reolink_config = self._load_json(self.reolink_config_file, {})
        self.amcrest_config = self._load_json(self.amcrest_config_file, {})

        logger.info(
            f"CameraRepository initialized: {self.get_camera_count()} visible cameras "
            f"(source: {self._source})")

    # ===== Database Loading =====

    def _load_cameras_from_db(self) -> dict:
        """
        Load all camera configurations from the database via PostgREST.

        Returns:
            Dict in cameras.json format: {'devices': {serial: config, ...}, ...}
            Returns empty dict if database is unavailable.
        """
        try:
            response = requests.get(
                f"{POSTGREST_URL}/cameras",
                timeout=5
            )

            if response.status_code != 200:
                logger.warning(
                    f"Database returned HTTP {response.status_code} when loading cameras")
                return {}

            rows = response.json()
            if not rows:
                logger.info("No cameras found in database (table may be empty)")
                return {}

            # Transform DB rows into cameras.json devices format
            devices = {}
            for row in rows:
                serial = row['serial']
                config = self._db_row_to_camera_config(row)
                devices[serial] = config

            # Also load webrtc_global_settings from JSON (not in DB)
            json_data = self._load_json(self.cameras_file, {})
            webrtc_settings = json_data.get('webrtc_global_settings', {})

            return {
                'devices': devices,
                'last_updated': datetime.now().isoformat(),
                'total_devices': len(devices),
                'webrtc_global_settings': webrtc_settings,
            }

        except requests.RequestException as e:
            logger.warning(f"Cannot reach database for camera loading: {e}")
            return {}

    def _db_row_to_camera_config(self, row: dict) -> dict:
        """
        Transform a database row into the camera config dict format
        that all consuming services expect (same as cameras.json device format).

        Args:
            row: Database row dict from PostgREST

        Returns:
            Camera config dict compatible with cameras.json format
        """
        config = {}

        # Direct scalar fields
        direct_fields = [
            'serial', 'name', 'type', 'camera_id', 'host', 'mac',
            'packager_path', 'stream_type', 'rtsp_alias', 'max_connections',
            'onvif_port', 'power_supply', 'hidden', 'ui_health_monitor',
            'reversed_pan', 'reversed_tilt', 'notes', 'power_supply_device_id',
            'true_mjpeg',
        ]

        for field in direct_fields:
            if field in row and row[field] is not None:
                config[field] = row[field]

        # JSONB fields (stored as JSON objects in DB, already deserialized by PostgREST)
        jsonb_fields = [
            'capabilities', 'll_hls', 'mjpeg_snap', 'neolink',
            'player_settings', 'rtsp_input', 'rtsp_output',
            'two_way_audio', 'power_cycle_on_failure',
        ]

        for field in jsonb_fields:
            if field in row and row[field] is not None:
                config[field] = row[field]

        # Merge extra_config fields back into the config dict
        extra = row.get('extra_config')
        if extra and isinstance(extra, dict):
            config.update(extra)

        return config

    # ===== Database Write Operations =====

    def _update_camera_in_db(self, serial: str, updates: dict) -> bool:
        """
        Update specific fields for a camera in the database.

        Args:
            serial: Camera serial number
            updates: Dict of field:value pairs to update

        Returns:
            True if successful, False otherwise
        """
        try:
            response = requests.patch(
                f"{POSTGREST_URL}/cameras",
                params={'serial': f'eq.{serial}'},
                json=updates,
                headers={'Content-Type': 'application/json'},
                timeout=5
            )
            if response.status_code in (200, 204):
                return True
            logger.error(
                f"Failed to update camera {serial} in DB: "
                f"HTTP {response.status_code}: {response.text}")
            return False
        except requests.RequestException as e:
            logger.error(f"DB update failed for camera {serial}: {e}")
            return False

    def _save_camera_to_db(self, serial: str, config: dict) -> bool:
        """
        Upsert a complete camera record to the database.

        Args:
            serial: Camera serial number
            config: Full camera config dict

        Returns:
            True if successful, False otherwise
        """
        # Import here to avoid circular imports at module level
        from services.camera_config_sync import _build_camera_record

        record = _build_camera_record(serial, config)

        try:
            response = requests.post(
                f"{POSTGREST_URL}/cameras",
                json=record,
                headers={
                    'Prefer': 'resolution=merge-duplicates,return=representation',
                    'Content-Type': 'application/json',
                },
                timeout=10
            )
            return response.status_code in (200, 201)
        except requests.RequestException as e:
            logger.error(f"DB upsert failed for camera {serial}: {e}")
            return False

    # ===== JSON File Operations (kept for fallback and backup) =====

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
        """Save data to JSON file (backup only)"""
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
        Filter out hidden cameras unless explicitly requested.

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
        """Get single camera configuration by serial number."""
        return self.cameras_data.get('devices', {}).get(serial)

    def get_all_cameras(self, include_hidden: bool = False) -> Dict[str, Dict]:
        """
        Get all camera configurations.

        Args:
            include_hidden: If True, include hidden cameras. Default False.

        Returns:
            Dictionary of cameras {serial: config}
        """
        all_cameras = self.cameras_data.get('devices', {})
        return self._filter_hidden(all_cameras, include_hidden)

    def get_cameras_by_type(self, camera_type: str, include_hidden: bool = False) -> Dict[str, Dict]:
        """
        Get all cameras of a specific type.

        Args:
            camera_type: 'eufy', 'unifi', 'reolink', 'sv3c', 'amcrest'
            include_hidden: If True, include hidden cameras. Default False.
        """
        all_cameras = self.get_all_cameras(include_hidden=True)
        type_filtered = {
            serial: config
            for serial, config in all_cameras.items()
            if config.get('type') == camera_type
        }
        return self._filter_hidden(type_filtered, include_hidden)

    def get_cameras_by_capability(self, capability: str, include_hidden: bool = False) -> Dict[str, Dict]:
        """
        Get cameras with specific capability.

        Args:
            capability: 'streaming', 'ptz', 'doorbell', etc.
            include_hidden: If True, include hidden cameras. Default False.
        """
        all_cameras = self.get_all_cameras(include_hidden=True)
        capability_filtered = {
            serial: config
            for serial, config in all_cameras.items()
            if capability in config.get('capabilities', [])
        }
        return self._filter_hidden(capability_filtered, include_hidden)

    def get_streaming_cameras(self, include_hidden: bool = False) -> Dict[str, Dict]:
        """Get all cameras with streaming capability."""
        return self.get_cameras_by_capability('streaming', include_hidden)

    def get_ptz_cameras(self, include_hidden: bool = False) -> Dict[str, Dict]:
        """Get all PTZ-capable cameras."""
        return self.get_cameras_by_capability('ptz', include_hidden)

    def get_camera_name(self, serial: str) -> Optional[str]:
        """Get camera display name."""
        camera = self.get_camera(serial)
        return camera.get('name') if camera else None

    def get_camera_count(self, include_hidden: bool = False) -> int:
        """
        Get total number of cameras.

        Args:
            include_hidden: If True, include hidden cameras. Default False.

        Returns:
            Count of cameras (excluding hidden by default)
        """
        return len(self.get_all_cameras(include_hidden))

    def get_amcrest_config(self) -> Dict:
        """Get Amcrest configuration."""
        return self.amcrest_config

    def is_camera_hidden(self, serial: str) -> bool:
        """Check if a camera is hidden."""
        camera = self.get_camera(serial)
        return camera.get('hidden', False) if camera else False

    def camera_exists(self, serial: str) -> bool:
        """Check if camera exists (regardless of hidden status)."""
        return serial in self.cameras_data.get('devices', {})

    def save_cameras(self, cameras_data: dict) -> bool:
        """
        Save camera configuration. Writes to database first, then JSON as backup.

        Args:
            cameras_data: Complete cameras data structure
        """
        cameras_data['last_updated'] = datetime.now().isoformat()
        self.cameras_data = cameras_data

        # Write each camera to database
        db_success = True
        devices = cameras_data.get('devices', {})
        for serial, config in devices.items():
            if not self._save_camera_to_db(serial, config):
                db_success = False

        if not db_success:
            logger.warning("Some cameras failed to save to database, saving JSON as backup")

        # Always save JSON as backup
        json_success = self._save_json(self.cameras_file, cameras_data)

        return db_success or json_success

    def update_camera_setting(self, serial: str, key: str, value) -> bool:
        """
        Update a single setting for a camera. Writes to DB and updates cache.

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

        # Update in-memory cache
        camera[key] = value

        # Determine if this is a direct DB column or goes into extra_config
        db_columns = {
            'serial', 'name', 'type', 'camera_id', 'host', 'mac',
            'packager_path', 'stream_type', 'rtsp_alias', 'max_connections',
            'onvif_port', 'power_supply', 'hidden', 'ui_health_monitor',
            'reversed_pan', 'reversed_tilt', 'notes', 'power_supply_device_id',
            'true_mjpeg', 'capabilities', 'll_hls', 'mjpeg_snap', 'neolink',
            'player_settings', 'rtsp_input', 'rtsp_output', 'two_way_audio',
            'power_cycle_on_failure',
        }

        if key in db_columns:
            # Direct column update
            db_ok = self._update_camera_in_db(serial, {key: value})
        else:
            # Update extra_config JSONB field
            extra = camera.get('extra_config', {})
            if not isinstance(extra, dict):
                extra = {}
            extra[key] = value
            db_ok = self._update_camera_in_db(serial, {'extra_config': extra})

        # Also save to JSON as backup
        json_ok = self._save_json(self.cameras_file, self.cameras_data)

        if not db_ok:
            logger.warning(f"DB update failed for {serial}.{key}, JSON backup saved")

        return db_ok or json_ok

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

        updates = {}
        if reversed_pan is not None:
            camera['reversed_pan'] = reversed_pan
            updates['reversed_pan'] = reversed_pan
            logger.info(f"Set reversed_pan={reversed_pan} for camera {serial}")

        if reversed_tilt is not None:
            camera['reversed_tilt'] = reversed_tilt
            updates['reversed_tilt'] = reversed_tilt
            logger.info(f"Set reversed_tilt={reversed_tilt} for camera {serial}")

        # Update DB directly with specific fields
        db_ok = self._update_camera_in_db(serial, updates) if updates else True

        # Save JSON backup
        json_ok = self._save_json(self.cameras_file, self.cameras_data)

        return db_ok or json_ok

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

    # ===== Stream Type Resolution (Per-User Override) =====

    def get_effective_stream_type(self, serial: str, user_id: int = None) -> str:
        """
        Resolve the effective stream type for a camera, considering user preferences.

        Priority:
        1. User's preferred_stream_type from user_camera_preferences table
        2. Camera's default stream_type from cameras table / config

        Args:
            serial: Camera serial number
            user_id: User ID to check preferences for. If None, returns camera default.

        Returns:
            Stream type string (e.g., 'LL_HLS', 'WEBRTC', 'MJPEG', 'HLS', 'NEOLINK')
        """
        if user_id:
            try:
                response = requests.get(
                    f"{POSTGREST_URL}/user_camera_preferences",
                    params={
                        'user_id': f'eq.{user_id}',
                        'camera_serial': f'eq.{serial}',
                        'select': 'preferred_stream_type',
                    },
                    timeout=3
                )
                if response.status_code == 200:
                    rows = response.json()
                    if rows and rows[0].get('preferred_stream_type'):
                        return rows[0]['preferred_stream_type']
            except requests.RequestException as e:
                logger.warning(
                    f"Failed to fetch user stream preference for {serial}: {e}")

        # Fall back to camera's configured stream type
        camera = self.get_camera(serial)
        return camera.get('stream_type', 'LL_HLS') if camera else 'LL_HLS'

    # ===== Vendor-Specific Config Access (always from JSON) =====

    def get_unifi_protect_config(self) -> Dict:
        """Get UniFi Protect console configuration."""
        return self.unifi_config

    def get_eufy_bridge_config(self) -> Dict:
        """Get Eufy bridge configuration."""
        return self.eufy_config

    def get_reolink_config(self) -> Dict:
        """Get Reolink NVR configuration."""
        return self.reolink_config

    # ===== Utility Methods =====

    def get_data_source(self) -> str:
        """Get which source camera data was loaded from ('database', 'json', or 'none')."""
        return self._source

    def get_last_updated(self) -> str:
        """Get when cameras were last updated."""
        return self.cameras_data.get('last_updated', 'Never')

    def reload(self):
        """
        Reload all configurations. Tries database first, falls back to JSON.
        Vendor configs always reloaded from JSON.
        """
        db_data = self._load_cameras_from_db()
        if db_data.get('devices'):
            self.cameras_data = db_data
            self._source = 'database'
            logger.info(
                f"Reloaded {self.get_camera_count(include_hidden=True)} cameras from database")
        else:
            self.cameras_data = self._load_json(self.cameras_file, {})
            self._source = 'json'
            logger.warning("Database unavailable on reload, using JSON fallback")

        # Vendor configs always from JSON
        self.unifi_config = self._load_json(self.unifi_config_file, {})
        self.eufy_config = self._load_json(self.eufy_config_file, {})
        self.reolink_config = self._load_json(self.reolink_config_file, {})
        self.amcrest_config = self._load_json(self.amcrest_config_file, {})

        logger.info("Reloaded all configurations")
