#!/usr/bin/env python3
"""
ONVIF PTZ Handler - PTZ operations and preset management
Handles pan/tilt/zoom control and preset positions via ONVIF protocol
"""

import logging
from typing import Optional, List, Dict, Tuple
from services.onvif.onvif_client import ONVIFClient
from services.credentials.amcrest_credential_provider import AmcrestCredentialProvider
from services.credentials.reolink_credential_provider import ReolinkCredentialProvider

logger = logging.getLogger(__name__)

class ONVIFPTZHandler:
    """
    ONVIF PTZ operations handler
    
    Provides:
    - Continuous move (pan, tilt, zoom)
    - Absolute/relative positioning
    - Preset management (get, goto, set)
    - PTZ status queries
    """
    
    # Movement speeds (normalized -1.0 to 1.0)
    DEFAULT_PAN_SPEED = 0.5
    DEFAULT_TILT_SPEED = 0.5
    DEFAULT_ZOOM_SPEED = 0.5
    
    # Direction mappings
    DIRECTION_VECTORS = {
        'left': (-1, 0, 0),   # pan left
        'right': (1, 0, 0),   # pan right
        'up': (0, 1, 0),      # tilt up
        'down': (0, -1, 0),   # tilt down
        'zoom_in': (0, 0, 1), # zoom in
        'zoom_out': (0, 0, -1), # zoom out
        'stop': (0, 0, 0),     # stop all movement
        'home': None          # go to home position (handled separately)
    }
    
    # Credential providers (initialized on first use)
    _amcrest_provider = None
    _reolink_provider = None
    
    @classmethod
    def _get_credentials(cls, camera_serial: str, camera_type: str):
        """Get credentials using appropriate provider"""
        if camera_type == 'amcrest':
            if cls._amcrest_provider is None:
                cls._amcrest_provider = AmcrestCredentialProvider()
            return cls._amcrest_provider.get_credentials(camera_serial)
        elif camera_type == 'reolink':
            if cls._reolink_provider is None:
                cls._reolink_provider = ReolinkCredentialProvider()
            return cls._reolink_provider.get_credentials(camera_serial)
        elif camera_type == 'sv3c':
            from services.credentials.sv3c_credential_provider import SV3CCredentialProvider
            return SV3CCredentialProvider().get_credentials(camera_serial)
        return None, None
    
    @classmethod
    def move_camera(cls, 
                   camera_serial: str,
                   direction: str,
                   camera_config: Dict,
                   speed_multiplier: float = 1.0) -> Tuple[bool, str]:
        """
        Execute PTZ movement via ONVIF
        
        Args:
            camera_serial: Camera serial number
            direction: Movement direction (left/right/up/down/zoom_in/zoom_out/stop)
            camera_config: Camera configuration dict with host, port, credentials
            speed_multiplier: Speed adjustment (0.1 - 1.0)
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            # Validate direction
            if direction not in cls.DIRECTION_VECTORS:
                return False, f"Invalid direction: {direction}"

            # Get camera host and type
            host = camera_config.get('host')
            if not host:
                return False, "No host configured for camera"

            # Get credentials via provider
            camera_type = camera_config.get('type')
            username, password = cls._get_credentials(camera_serial, camera_type)
            if not username or not password:
                return False, "Missing credentials for camera"

            # Get ONVIF connection
            camera = ONVIFClient.get_camera(
                host=host,
                port=camera_config.get('onvif_port', 80),
                username=username,
                password=password,
                camera_serial=camera_serial
            )

            if not camera:
                return False, "Failed to connect to camera via ONVIF"

            # Get PTZ service
            ptz_service = ONVIFClient.get_ptz_service(camera)
            if not ptz_service:
                return False, "Camera does not support PTZ via ONVIF"

            # Get profile token
            profile_token = ONVIFClient.get_profile_token(camera)
            if not profile_token:
                return False, "Could not get media profile token"

            # Handle home position separately
            if direction == 'home':
                return cls.goto_home_position(ptz_service, profile_token, camera_serial)

            # Get movement vector
            pan, tilt, zoom = cls.DIRECTION_VECTORS[direction]

            # Apply speed multiplier
            pan_speed = cls.DEFAULT_PAN_SPEED * speed_multiplier * pan
            tilt_speed = cls.DEFAULT_TILT_SPEED * speed_multiplier * tilt
            zoom_speed = cls.DEFAULT_ZOOM_SPEED * speed_multiplier * zoom
            
            # Create velocity request
            request = ptz_service.create_type('ContinuousMove')
            request.ProfileToken = profile_token
            
            # Build velocity structure as dictionary (zeep converts to proper SOAP types)
            velocity = {}
            
            # Set pan/tilt velocity
            if pan != 0 or tilt != 0:
                velocity['PanTilt'] = {'x': pan_speed, 'y': tilt_speed}
            
            # Set zoom velocity
            if zoom != 0:
                velocity['Zoom'] = {'x': zoom_speed}
            
            # Apply velocity to request
            if velocity:
                request.Velocity = velocity
            
            # Execute movement
            if direction == 'stop':
                # Stop all movement
                stop_request = ptz_service.create_type('Stop')
                stop_request.ProfileToken = profile_token
                stop_request.PanTilt = True
                stop_request.Zoom = True
                ptz_service.Stop(stop_request)
                logger.info(f"ONVIF PTZ stopped for {camera_serial}")
            else:
                ptz_service.ContinuousMove(request)
                logger.info(f"ONVIF PTZ {direction} started for {camera_serial}")
            
            return True, "PTZ command executed successfully"
            
        except Exception as e:
            logger.error(f"ONVIF PTZ move failed for {camera_serial}: {e}")
            return False, f"PTZ operation failed: {str(e)}"
    
    @classmethod
    def get_presets(cls, camera_serial: str, camera_config: Dict) -> Tuple[bool, List[Dict]]:
        """
        Get list of available PTZ presets
        
        Args:
            camera_serial: Camera serial number
            camera_config: Camera configuration dict
            
        Returns:
            Tuple of (success: bool, presets: List[Dict])
            Preset dict format: {'token': str, 'name': str}
        """
        try:
            # Get camera host
            host = camera_config.get('host')
            if not host:
                return False, []
            
            # Get credentials via provider
            camera_type = camera_config.get('type')
            username, password = cls._get_credentials(camera_serial, camera_type)
            if not username or not password:
                return False, []
            
            # Get ONVIF connection
            camera = ONVIFClient.get_camera(
                host=host,
                port=camera_config.get('onvif_port', 80),
                username=username,
                password=password,
                camera_serial=camera_serial
            )
            
            if not camera:
                return False, []
            
            # Get PTZ service
            ptz_service = ONVIFClient.get_ptz_service(camera)
            if not ptz_service:
                return False, []
            
            # Get profile token
            profile_token = ONVIFClient.get_profile_token(camera)
            if not profile_token:
                return False, []
            
            # Get presets
            request = ptz_service.create_type('GetPresets')
            request.ProfileToken = profile_token
            
            presets_response = ptz_service.GetPresets(request)
            
            # Parse presets
            presets = []
            if presets_response:
                for preset in presets_response:
                    presets.append({
                        'token': preset.token,
                        'name': preset.Name if hasattr(preset, 'Name') else preset.token
                    })

            logger.info(f"Retrieved {len(presets)} presets for {camera_serial}: {presets}")
            return True, presets
            
        except Exception as e:
            logger.error(f"Failed to get presets for {camera_serial}: {e}")
            return False, []
    
    @classmethod
    def goto_preset(cls, 
                   camera_serial: str,
                   preset_token: str,
                   camera_config: Dict,
                   speed_multiplier: float = 1.0) -> Tuple[bool, str]:
        """
        Move camera to preset position
        
        Args:
            camera_serial: Camera serial number
            preset_token: Preset token to move to
            camera_config: Camera configuration dict
            speed_multiplier: Movement speed (0.1 - 1.0)
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            # Get camera host and type
            host = camera_config.get('host')
            if not host:
                return False, "No host configured for camera"
            
            # Get credentials via provider
            camera_type = camera_config.get('type')
            username, password = cls._get_credentials(camera_serial, camera_type)
            if not username or not password:
                return False, "Missing credentials for camera"
            
            # Get ONVIF connection
            camera = ONVIFClient.get_camera(
                host=host,
                port=camera_config.get('onvif_port', 80),
                username=username,
                password=password,
                camera_serial=camera_serial
            )
            
            if not camera:
                return False, "Failed to connect to camera via ONVIF"
            
            # Get PTZ service
            ptz_service = ONVIFClient.get_ptz_service(camera)
            if not ptz_service:
                return False, "Camera does not support PTZ via ONVIF"
            
            # Get profile token
            profile_token = ONVIFClient.get_profile_token(camera)
            if not profile_token:
                return False, "Could not get media profile token"
            
            # Create goto preset request
            request = ptz_service.create_type('GotoPreset')
            request.ProfileToken = profile_token
            request.PresetToken = preset_token
            
            # Set speed if supported (use dictionary for zeep auto-conversion)
            if hasattr(request, 'Speed'):
                request.Speed = {
                    'PanTilt': {
                        'x': cls.DEFAULT_PAN_SPEED * speed_multiplier,
                        'y': cls.DEFAULT_TILT_SPEED * speed_multiplier
                    },
                    'Zoom': {
                        'x': cls.DEFAULT_ZOOM_SPEED * speed_multiplier
                    }
                }
            
            # Execute goto preset
            ptz_service.GotoPreset(request)
            
            logger.info(f"Camera {camera_serial} moving to preset {preset_token}")
            return True, f"Moving to preset {preset_token}"
            
        except Exception as e:
            logger.error(f"Failed to goto preset for {camera_serial}: {e}")
            return False, f"Preset operation failed: {str(e)}"
    
    @classmethod
    def set_preset(cls,
                  camera_serial: str,
                  preset_name: str,
                  camera_config: Dict,
                  preset_token: Optional[str] = None) -> Tuple[bool, str]:
        """
        Save current position as preset
        
        Args:
            camera_serial: Camera serial number
            preset_name: Name for the preset
            camera_config: Camera configuration dict
            preset_token: Optional preset token to update (None = create new)
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            # Get camera host and type
            host = camera_config.get('host')
            if not host:
                return False, "No host configured for camera"
            
            # Get credentials via provider
            camera_type = camera_config.get('type')
            username, password = cls._get_credentials(camera_serial, camera_type)
            if not username or not password:
                return False, "Missing credentials for camera"
            
            # Get ONVIF connection
            camera = ONVIFClient.get_camera(
                host=host,
                port=camera_config.get('onvif_port', 80),
                username=username,
                password=password,
                camera_serial=camera_serial
            )
            
            if not camera:
                return False, "Failed to connect to camera via ONVIF"
            
            # Get PTZ service
            ptz_service = ONVIFClient.get_ptz_service(camera)
            if not ptz_service:
                return False, "Camera does not support PTZ via ONVIF"
            
            # Get profile token
            profile_token = ONVIFClient.get_profile_token(camera)
            if not profile_token:
                return False, "Could not get media profile token"
            
            # Create set preset request
            request = ptz_service.create_type('SetPreset')
            request.ProfileToken = profile_token
            request.PresetName = preset_name
            
            if preset_token:
                request.PresetToken = preset_token
            
            # Execute set preset
            response = ptz_service.SetPreset(request)
            
            token = response if isinstance(response, str) else getattr(response, 'PresetToken', 'unknown')
            logger.info(f"Preset '{preset_name}' saved for {camera_serial} (token: {token})")
            return True, f"Preset '{preset_name}' saved successfully"
            
        except Exception as e:
            logger.error(f"Failed to set preset for {camera_serial}: {e}")
            return False, f"Failed to save preset: {str(e)}"
    
    @classmethod
    def remove_preset(cls,
                     camera_serial: str,
                     preset_token: str,
                     camera_config: Dict) -> Tuple[bool, str]:
        """
        Remove a preset
        
        Args:
            camera_serial: Camera serial number
            preset_token: Token of preset to remove
            camera_config: Camera configuration dict
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            # Get camera host and type
            host = camera_config.get('host')
            if not host:
                return False, "No host configured for camera"
            
            # Get credentials via provider
            camera_type = camera_config.get('type')
            username, password = cls._get_credentials(camera_serial, camera_type)
            if not username or not password:
                return False, "Missing credentials for camera"
            
            # Get ONVIF connection
            camera = ONVIFClient.get_camera(
                host=host,
                port=camera_config.get('onvif_port', 80),
                username=username,
                password=password,
                camera_serial=camera_serial
            )
            
            if not camera:
                return False, "Failed to connect to camera via ONVIF"
            
            # Get PTZ service
            ptz_service = ONVIFClient.get_ptz_service(camera)
            if not ptz_service:
                return False, "Camera does not support PTZ via ONVIF"
            
            # Get profile token
            profile_token = ONVIFClient.get_profile_token(camera)
            if not profile_token:
                return False, "Could not get media profile token"
            
            # Create remove preset request
            request = ptz_service.create_type('RemovePreset')
            request.ProfileToken = profile_token
            request.PresetToken = preset_token
            
            # Execute remove preset
            ptz_service.RemovePreset(request)
            
            logger.info(f"Preset {preset_token} removed from {camera_serial}")
            return True, f"Preset removed successfully"
            
        except Exception as e:
            logger.error(f"Failed to remove preset for {camera_serial}: {e}")
            return False, f"Failed to remove preset: {str(e)}"

    @classmethod
    def goto_home_position(cls, ptz_service, profile_token: str, camera_serial: str) -> Tuple[bool, str]:
        """
        Move PTZ camera to home position (triggers stepper calibration)

        Args:
            ptz_service: ONVIF PTZ service instance
            profile_token: Media profile token
            camera_serial: Camera serial number for logging

        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            logger.info(f"Moving {camera_serial} to home position (stepper calibration)")

            # Create GotoHomePosition request
            request = ptz_service.create_type('GotoHomePosition')
            request.ProfileToken = profile_token

            # Execute home position movement
            ptz_service.GotoHomePosition(request)

            logger.info(f"{camera_serial} moved to home position successfully")
            return True, "Camera moved to home position (stepper calibration initiated)"

        except Exception as e:
            logger.error(f"Failed to move {camera_serial} to home position: {e}")
            return False, f"Failed to move to home position: {str(e)}"


# Module-level convenience functions
def move_ptz(camera_serial: str, direction: str, camera_config: Dict) -> Tuple[bool, str]:
    """Convenience function for PTZ movement"""
    return ONVIFPTZHandler.move_camera(camera_serial, direction, camera_config)


def get_camera_presets(camera_serial: str, camera_config: Dict) -> Tuple[bool, List[Dict]]:
    """Convenience function to get presets"""
    return ONVIFPTZHandler.get_presets(camera_serial, camera_config)


def goto_camera_preset(camera_serial: str, preset_token: str, camera_config: Dict) -> Tuple[bool, str]:
    """Convenience function to go to preset"""
    return ONVIFPTZHandler.goto_preset(camera_serial, preset_token, camera_config)