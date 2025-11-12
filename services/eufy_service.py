#!/usr/bin/env python3
"""
Eufy camera service with integrated bridge process management
Single class handling both bridge management and camera operations
"""

import asyncio
import json
import websockets
import subprocess
import threading
import time
import logging
import os
from .camera_base import CameraService

logger = logging.getLogger(__name__)

class EufyCameraService(CameraService):
    """Eufy camera service with integrated bridge process management"""
    
    # Class variables for shared bridge process
    _bridge_process = None
    _bridge_running = False
    _ready_event = threading.Event()
    _bridge_lock = threading.Lock()
    
    def __init__(self, camera_config):
        super().__init__(camera_config)
        self.serial = camera_config['serial']
        self.rtsp_config = camera_config.get('rtsp', {})
        self.image_mirrored = camera_config.get('image_mirrored', False)
        self.bridge_url = "ws://127.0.0.1:3000"
        
        # PTZ direction mapping
        self.directions = {
            'left': 0,
            'right': 1,
            'up': 2,
            'down': 3,
            '360': 4,
            'stop': 5
        }
        
        # Start shared bridge if not running (only one instance will actually start it)
        self._ensure_bridge_running()
    
    @classmethod
    def _ensure_bridge_running(cls):
        """Ensure the shared bridge process is running"""
        with cls._bridge_lock:
            if not cls._bridge_running:
                cls._start_bridge_process()
    
    @classmethod
    def _start_bridge_process(cls, port=3000):
        """Start the shared bridge process"""
        try:
            # Kill any existing bridge process
            subprocess.run(f"pkill -f 'eufy-security-server.*port {port}'", 
                         shell=True, stderr=subprocess.DEVNULL)
            time.sleep(1)
            
            # Check if bridge script exists
            bridge_script = "eufy_bridge.sh"
            if not os.path.exists(bridge_script):
                logger.error(f"Bridge script {bridge_script} not found")
                return False
            
            # Start bridge process
            cls._bridge_process = subprocess.Popen(
                f"bash {bridge_script} {port}",
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                universal_newlines=True
            )
            
            cls._bridge_running = True
            
            # Start monitoring thread
            monitor_thread = threading.Thread(target=cls._monitor_bridge, daemon=True)
            monitor_thread.start()
            
            logger.info(f"Eufy bridge process started on port {port}")
            return True
            
        except Exception as e:
            logger.error(f"Error starting bridge: {e}")
            return False
    
    @classmethod
    def _monitor_bridge(cls):
        """Monitor bridge output for readiness"""
        if not cls._bridge_process:
            return
            
        try:
            for line in iter(cls._bridge_process.stdout.readline, ''):
                logger.debug(f"Bridge output: {line.strip()}")
                if "Push notification connection successfully established" in line:
                    cls._ready_event.set()
                    logger.info("Eufy bridge is ready for commands")
                    break
                elif cls._bridge_process.poll() is not None:
                    cls._bridge_running = False
                    break
        except Exception as e:
            logger.error(f"Error monitoring bridge: {e}")
    
    def authenticate(self) -> bool:
        """Check if bridge is running and ready"""
        # Wait for bridge to be ready
        for _ in range(10):  # Wait up to 10 seconds
            if self._ready_event.is_set():
                self.session_active = True
                return True
            time.sleep(1)
        
        self.session_active = False
        return False
    
    def get_snapshot(self) -> bytes:
        """Get snapshot via RTSP (placeholder)"""
        logger.warning(f"Snapshot not implemented for Eufy camera {self.name}")
        return None
    
    def get_stream_url(self) -> str:
        """Return RTSP stream URL"""
        return self.rtsp_config.get('url', '')
    
    def ptz_move(self, direction: str) -> bool:
        """Execute PTZ movement with direction correction"""
        if not self._ready_event.is_set():
            logger.error(f"Bridge not ready for PTZ command on {self.name}")
            return False
        
        # Apply direction correction for mirrored cameras
        corrected_direction = self._correct_direction(direction)
        
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(self._execute_ptz_command(corrected_direction))
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"PTZ command failed for {self.name}: {e}")
            return False
    
    def _correct_direction(self, direction):
        """Correct direction based on camera orientation"""
        if self.image_mirrored:
            direction_corrections = {
                'left': 'right',
                'right': 'left',
                'up': 'up',
                'down': 'down',
                '360': '360'
            }
            corrected = direction_corrections.get(direction, direction)
            if corrected != direction:
                logger.info(f"Camera {self.name} is mirrored: {direction} → {corrected}")
            return corrected
        return direction
    
    async def _execute_ptz_command(self, direction):
        """Execute PTZ command with automatic stop"""
        direction_code = self.directions.get(direction)
        if direction_code is None:
            logger.error(f"Invalid PTZ direction: {direction}")
            return False
        
        try:
            async with websockets.connect(self.bridge_url, open_timeout=5) as ws:
                # Set API schema version
                await ws.send(json.dumps({
                    "messageId": "schema",
                    "command": "set_api_schema",
                    "schemaVersion": 21
                }))
                await ws.recv()
                
                # Start listening
                await ws.send(json.dumps({
                    "messageId": "start",
                    "command": "start_listening"
                }))
                await ws.recv()
                
                # Send PTZ start command
                cmd = {
                    "messageId": "ptz_start",
                    "command": "device.pan_and_tilt",
                    "serialNumber": self.serial,
                    "direction": direction_code
                }
                await ws.send(json.dumps(cmd))
                response = await ws.recv()
                
                start_result = json.loads(response)
                if not start_result.get("success", False):
                    logger.error(f"PTZ start command failed for {self.name}")
                    return False
                
                # For 360 degree rotation, don't send stop
                if direction == '360':
                    logger.info(f"PTZ 360° rotation started for {self.name}")
                    return True
                
                # Wait for movement duration
                await asyncio.sleep(0.5)  # 500ms movement
                
                # Send PTZ stop command
                stop_cmd = {
                    "messageId": "ptz_stop",
                    "command": "device.pan_and_tilt",
                    "serialNumber": self.serial,
                    "direction": 5  # Stop command
                }
                await ws.send(json.dumps(stop_cmd))
                await ws.recv()
                
                logger.info(f"PTZ {direction} completed for {self.name}")
                return True
                
        except Exception as e:
            logger.error(f"PTZ WebSocket error for {self.name}: {e}")
            return False