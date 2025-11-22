"""
Reolink Motion Detection Service
Location: ~/0_NVR/services/motion/reolink_motion_service.py

Uses native Baichuan TCP push protocol for real-time motion events.
Async event loop runs in background thread, similar to FFmpegMotionDetector pattern.
"""

import asyncio
import logging
import threading
import time
from typing import Dict, Optional
from reolink_aio.api import Host

logger = logging.getLogger(__name__)


class ReolinkMotionService:
    """
    Manages Baichuan motion detection for Reolink cameras.
    
    Architecture:
    - Async event loop in background thread
    - One Host instance per Reolink camera
    - Callbacks trigger recording_service.start_motion_recording()
    - Auto-reconnect on connection loss
    """
    
    def __init__(self, camera_repository, recording_service, recording_config, credential_provider):
        """
        Initialize Reolink motion detection service.
        
        Args:
            camera_repository: CameraRepository instance
            recording_service: RecordingService instance
            recording_config: RecordingConfig instance
            credential_provider: ReolinkCredentialProvider instance
        """
        self.camera_repo = camera_repository
        self.recording_service = recording_service
        self.config = recording_config
        self.credentials = credential_provider
        
        # Camera connections: {camera_id: Host}
        self.hosts: Dict[str, Host] = {}
        
        # Event loop and thread
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.thread: Optional[threading.Thread] = None
        self.running = False
        
        # Motion state tracking for debouncing
        self.motion_states: Dict[str, float] = {}  # {camera_id: last_motion_time}
        
        # Cooldown from recording config
        self.cooldown_sec = self.config.base_config.get('motion_detection', {}).get('cooldown_sec', 60)
        
        logger.info("Reolink motion service initialized")
    
    
    def start(self):
        """Start the motion detection service in background thread"""
        if self.running:
            logger.warning("Reolink motion service already running")
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._run_event_loop, daemon=True, name="ReolinkMotionThread")
        self.thread.start()
        logger.info("Reolink motion service started")
    
    
    def stop(self):
        """Stop the motion detection service"""
        if not self.running:
            return
        
        logger.info("Stopping Reolink motion service...")
        self.running = False
        
        if self.loop and self.loop.is_running():
            # Schedule cleanup in event loop
            asyncio.run_coroutine_threadsafe(self._cleanup_all(), self.loop)
            
            # Wait for thread to finish
            if self.thread:
                self.thread.join(timeout=10)
        
        logger.info("Reolink motion service stopped")
    
    
    def _run_event_loop(self):
        """Run asyncio event loop in background thread"""
        try:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            
            # Start monitoring all enabled cameras
            self.loop.run_until_complete(self._monitor_all_cameras())
            
        except Exception as e:
            logger.error(f"Reolink event loop error: {e}", exc_info=True)
        finally:
            if self.loop:
                self.loop.close()
    
    
    async def _monitor_all_cameras(self):
        """Monitor all Reolink cameras with Baichuan detection enabled"""
        try:
            reolink_cameras = list(self.camera_repo.get_cameras_by_type('reolink').items())
            
            logger.info(f"Found {len(reolink_cameras)} Reolink cameras")
            
            # Start monitoring tasks for enabled cameras
            tasks = []
            for camera_id, camera_data in reolink_cameras:
                # Check if motion recording enabled with baichuan method
                if self.config.is_recording_enabled(camera_id, 'motion'):
                    detection_method = self.config.get_motion_detection_method(camera_id)
                    
                    if detection_method == 'baichuan':
                        logger.info(f"Starting Baichuan monitoring for {camera_id}")
                        task = asyncio.create_task(self._monitor_camera(camera_id))
                        tasks.append(task)
                    else:
                        logger.debug(f"Camera {camera_id} uses {detection_method} detection, skipping Baichuan")
            
            if not tasks:
                logger.warning("No Reolink cameras configured for Baichuan detection")
                return
            
            # Run all monitoring tasks concurrently
            await asyncio.gather(*tasks, return_exceptions=True)
            
        except Exception as e:
            logger.error(f"Error monitoring cameras: {e}", exc_info=True)
    
    
    async def _monitor_camera(self, camera_id: str):
        """
        Monitor single camera for motion events.
        
        Args:
            camera_id: Camera identifier
        """
        camera = self.camera_repo.get_camera(camera_id)
        if not camera:
            logger.error(f"Camera not found: {camera_id}")
            return
        
        camera_ip = camera.get('host')
        camera_name = camera.get('name', camera_id)
        
        # Get credentials
        username, password = self.credentials.get_credentials(camera_id)
        if not username or not password:
            logger.error(f"Missing credentials for {camera_id}")
            return
        
        # Connection retry loop
        retry_delay = 10  # seconds
        while self.running:
            try:
                logger.info(f"Connecting to {camera_name} ({camera_ip})...")
                
                # Create Host instance
                host = Host(host=camera_ip, username=username, password=password)
                self.hosts[camera_id] = host
                
                # Get device capabilities
                await host.get_host_data()
                logger.info(
                    f"Connected to {camera_name}: "
                    f"{host.camera_model(0) if hasattr(host, 'camera_model') else 'Unknown'}"
                )
                
                # Register motion callback
                def motion_callback():
                    """Callback fired on ANY Baichuan push event"""
                    if host.motion_detected(0):
                        self._handle_motion_detected(camera_id, camera_name)
                
                host.baichuan.register_callback(f"motion_{camera_id}", motion_callback)
                
                # Subscribe to Baichuan events
                await host.baichuan.subscribe_events()
                logger.info(f"Subscribed to Baichuan events for {camera_name}")
                
                # Keep connection alive
                while self.running:
                    await asyncio.sleep(1)
                    
                    # Check if connection still alive (host maintains keepalive internally)
                    if not host or not hasattr(host, 'baichuan'):
                        logger.warning(f"Connection lost to {camera_name}, reconnecting...")
                        break
                
                # Cleanup on exit
                await host.baichuan.unsubscribe_events()
                await host.logout()
                logger.info(f"Disconnected from {camera_name}")
                
            except Exception as e:
                logger.error(f"Error monitoring {camera_name}: {e}", exc_info=True)
                
                # Cleanup host instance
                if camera_id in self.hosts:
                    try:
                        await self.hosts[camera_id].logout()
                    except:
                        pass
                    del self.hosts[camera_id]
                
                # Wait before retry
                if self.running:
                    logger.info(f"Retrying {camera_name} in {retry_delay}s...")
                    await asyncio.sleep(retry_delay)
    
    
    def _handle_motion_detected(self, camera_id: str, camera_name: str):
        """
        Handle motion detection event (runs in callback context).
        
        Args:
            camera_id: Camera that detected motion
            camera_name: Friendly camera name for logging
        """
        # Debounce check
        last_motion = self.motion_states.get(camera_id, 0)
        current_time = time.time()
        
        if current_time - last_motion < self.cooldown_sec:
            logger.debug(f"Motion detected for {camera_name} but in {self.cooldown_sec}s cooldown period")
            return
        
        logger.info(f"Motion detected via Baichuan: {camera_name}")
        
        try:
            # Update last motion time
            self.motion_states[camera_id] = current_time
            
            # Trigger recording via recording service
            recording_id = self.recording_service.start_motion_recording(camera_id)
            
            if recording_id:
                logger.info(f"Started motion recording for {camera_name}: {recording_id}")
            else:
                logger.warning(f"Failed to start motion recording for {camera_name}")
        
        except Exception as e:
            logger.error(f"Error starting motion recording for {camera_name}: {e}", exc_info=True)
    
    
    async def _cleanup_all(self):
        """Cleanup all camera connections"""
        logger.info("Cleaning up all Baichuan connections...")
        
        for camera_id, host in list(self.hosts.items()):
            try:
                await host.baichuan.unsubscribe_events()
                await host.logout()
                logger.debug(f"Cleaned up connection for {camera_id}")
            except Exception as e:
                logger.error(f"Error cleaning up {camera_id}: {e}")
        
        self.hosts.clear()
        logger.info("All Baichuan connections cleaned up")
    
    
    def get_active_detectors(self) -> Dict[str, str]:
        """
        Get status of active Baichuan detectors.
        
        Returns:
            Dict mapping camera_id to connection status
        """
        status = {}
        for camera_id, host in self.hosts.items():
            camera = self.camera_repo.get_camera(camera_id)
            camera_name = camera.get('name', camera_id) if camera else camera_id
            
            # Check if host is connected (has baichuan attribute)
            connected = hasattr(host, 'baichuan') and host.baichuan is not None
            status[camera_id] = {
                'name': camera_name,
                'connected': connected,
                'last_motion': self.motion_states.get(camera_id, 0)
            }
        
        return status


def create_reolink_motion_service(camera_repository, recording_service, recording_config, credential_provider):
    """
    Factory function to create Reolink motion service instance.
    
    Args:
        camera_repository: CameraRepository instance
        recording_service: RecordingService instance
        recording_config: RecordingConfig instance
        credential_provider: ReolinkCredentialProvider instance
    
    Returns:
        ReolinkMotionService instance
    """
    return ReolinkMotionService(
        camera_repository,
        recording_service,
        recording_config,
        credential_provider
    )