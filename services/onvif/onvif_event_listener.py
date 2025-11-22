"""
ONVIF Event Listener - Motion Detection
Location: ~/0_NVR/services/onvif/onvif_event_listener.py

Subscribes to ONVIF motion detection events and triggers recordings.
"""

import logging
import threading
import time
from datetime import timedelta
from typing import Dict, Callable, Optional

logger = logging.getLogger(__name__)


class ONVIFEventListener:
    """
    Listens for ONVIF motion detection events from cameras.
    
    Uses PullPoint subscription to receive motion events from ONVIF-capable
    cameras and triggers motion recordings via RecordingService.
    """
    
    def __init__(self, camera_repository, recording_service):
        """
        Initialize ONVIF event listener.
        
        Args:
            camera_repository: CameraRepository instance
            recording_service: RecordingService instance
        """
        self.camera_repo = camera_repository
        self.recording_service = recording_service
        self.active_listeners = {}
        self.listener_threads = {}
        self._stop_event = threading.Event()
        
        logger.info("ONVIF Event Listener initialized (SKELETON)")
    
    
    def start_listener(self, camera_id: str) -> bool:
        """
        Start listening for motion events from a camera.
        
        Args:
            camera_id: Camera identifier
        
        Returns:
            True if listener started successfully
        """
        # Verify camera exists and has ONVIF capability
        camera = self.camera_repo.get_camera(camera_id)
        if not camera:
            logger.error(f"Camera not found: {camera_id}")
            return False
        
        capabilities = camera.get('capabilities', [])
        if 'ONVIF' not in capabilities:
            logger.warning(f"Camera {camera_id} does not support ONVIF")
            return False
        
        # Check if already listening
        if camera_id in self.active_listeners:
            logger.info(f"Already listening to {camera_id}")
            return True
        
        # TODO: Implement actual ONVIF subscription
        logger.info(f"Starting ONVIF listener for {camera_id}")
        
        # Start listener thread
        thread = threading.Thread(
            target=self._listen_loop,
            args=(camera_id, camera),
            daemon=True
        )
        thread.start()
        
        self.active_listeners[camera_id] = True
        self.listener_threads[camera_id] = thread
        
        return True
    
    
    def stop_listener(self, camera_id: str):
        """
        Stop listening for motion events from a camera.
        
        Args:
            camera_id: Camera identifier
        """
        if camera_id in self.active_listeners:
            self.active_listeners.pop(camera_id)
            logger.info(f"Stopped ONVIF listener for {camera_id}")
    
    
    def stop_all(self):
        """Stop all active listeners."""
        self._stop_event.set()
        self.active_listeners.clear()
        logger.info("Stopped all ONVIF listeners")
    
    def _listen_loop(self, camera_id: str, camera: Dict):
        """Main event listening loop for a camera."""
        from datetime import timedelta
        
        logger.info(f"ONVIF listener loop started for {camera_id}")
        
        try:
            # Get ONVIF connection
            from services.onvif.onvif_client import ONVIFClient
            
            # Get credentials
            camera_type = camera.get('type', '').lower()
            
            if camera_type == 'amcrest':
                from services.credentials.amcrest_credential_provider import AmcrestCredentialProvider
                cred_provider = AmcrestCredentialProvider()
            elif camera_type == 'reolink':
                from services.credentials.reolink_credential_provider import ReolinkCredentialProvider
                cred_provider = ReolinkCredentialProvider()
            elif camera_type == 'unifi':
                from services.credentials.unifi_credential_provider import UniFiCredentialProvider
                cred_provider = UniFiCredentialProvider()
            else:
                logger.error(f"Unsupported camera type for ONVIF: {camera_type}")
                return
            
            credentials = cred_provider.get_credentials(camera_id)
            
            if isinstance(credentials, tuple):
                username, password = credentials
            else:
                username = credentials.get('username')
                password = credentials.get('password')
            
            onvif_cam = ONVIFClient.get_camera(
                host=camera.get('host'),
                username=username,
                password=password,
                camera_serial=camera_id
            )
            
            if not onvif_cam:
                logger.error(f"Failed to connect to ONVIF camera: {camera_id}")
                return
            
            # Create event service
            event_service = onvif_cam.create_events_service()
            
            # Create PullPoint subscription
            subscription_response = event_service.CreatePullPointSubscription()
            subscription_address = subscription_response.SubscriptionReference.Address._value_1
            
            logger.info(f"ONVIF subscription created for {camera_id}")
            
            # Create pullpoint service using event_service.zeep_client (not onvif_cam.zeep_client)
            pullpoint_service = event_service.zeep_client.create_service(
                '{http://www.onvif.org/ver10/events/wsdl}PullPointSubscriptionBinding',
                subscription_address
            )
            
            # Poll for events
            while camera_id in self.active_listeners and not self._stop_event.is_set():
                try:
                    messages = pullpoint_service.PullMessages(
                        Timeout=timedelta(seconds=5),
                        MessageLimit=10
                    )
                    
                    if hasattr(messages, 'NotificationMessage') and messages.NotificationMessage:
                        for msg in messages.NotificationMessage:
                            # Get topic
                            topic = msg.Topic._value_1 if hasattr(msg.Topic, '_value_1') else None
                            
                            if topic and ('Motion' in str(topic) or 'MotionAlarm' in str(topic)):
                                logger.info(f"Motion detected via ONVIF: {camera_id}, Topic: {topic}")
                                self._handle_motion_event(camera_id)
                    
                except Exception as e:
                    logger.error(f"Event pull error for {camera_id}: {e}")
                    time.sleep(5)
        
        except Exception as e:
            logger.error(f"ONVIF listener error for {camera_id}: {e}")
        finally:
            if camera_id in self.active_listeners:
                self.active_listeners.pop(camera_id)
            logger.info(f"ONVIF listener loop ended for {camera_id}")


    def _is_motion_event(self, message) -> bool:
        """Check if message is a motion event."""
        try:
            # Topic is an object with _value_1 attribute
            topic = str(message.Topic._value_1) if hasattr(message.Topic, '_value_1') else str(message.Topic)
            logger.info(f"[DEBUG] Parsed topic: {topic}")
            return 'Motion' in topic or 'MotionAlarm' in topic
        except Exception as e:
            logger.error(f"Error parsing topic: {e}")
            return False


    def _parse_motion_state(self, message) -> bool:
        """Parse motion state from event message."""
        try:
            # Motion state is in Message.Data.SimpleItem
            if hasattr(message, 'Message') and hasattr(message.Message, '_value_1'):
                msg_data = message.Message._value_1
                # Look for SimpleItem elements
                for child in msg_data:
                    if hasattr(child, 'Name') and child.Name == 'State':
                        state_value = str(child.Value).lower()
                        logger.info(f"[DEBUG] State value: {state_value}")
                        return state_value == 'true'
            return False
        except Exception as e:
            logger.error(f"Error parsing motion state: {e}")
            return False
          
    def _handle_motion_event(self, camera_id: str):
        """
        Handle motion detection event.
        
        Args:
            camera_id: Camera that detected motion
        """
        logger.info(f"Motion detected via ONVIF: {camera_id}")
        
        try:
            # Trigger recording via recording service
            recording_id = self.recording_service.start_motion_recording(camera_id)
            
            if recording_id:
                logger.info(f"Started motion recording for {camera_id}: {recording_id}")
            else:
                logger.warning(f"Failed to start motion recording for {camera_id}")
        
        except Exception as e:
            logger.error(f"Error starting motion recording for {camera_id}: {e}")


def create_onvif_listener(camera_repository, recording_service) -> ONVIFEventListener:
    """
    Factory function to create ONVIF listener instance.
    
    Args:
        camera_repository: CameraRepository instance
        recording_service: RecordingService instance
    
    Returns:
        ONVIFEventListener instance
    """
    return ONVIFEventListener(camera_repository, recording_service)