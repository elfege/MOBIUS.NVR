#!/usr/bin/env python3
# eufy_bridge.py
#
# Eufy Bridge Management Class
#
import asyncio
import json
import websockets
import traceback
import subprocess
import signal
import time
import os
from threading import Thread, Event
import logging

logger = logging.getLogger(__name__)

class EufyBridge:
    """Manages the Eufy WebSocket bridge process and PTZ commands"""
    
    def __init__(self, port=3000):
        self.port = port
        self.bridge_url = f"ws://127.0.0.1:{port}"
        self.process = None
        self.ready_event = Event()
        self._running = False
        
        # PTZ direction mapping
        self.directions = {
            'left': 0,
            'right': 1, 
            'up': 2,
            'down': 3,
            '360': 4, 
            'stop': 5 
        }
    
    def start(self):
        """Start the Eufy bridge process"""
        if self.is_running():
            return True
            
        try:
            # Kill any existing bridge process
            subprocess.run(f"pkill -f 'eufy-security-server.*port {self.port}'", 
                         shell=True, stderr=subprocess.DEVNULL)
            time.sleep(1)
            
            # Start bridge process
            self.process = subprocess.Popen(
                f"bash eufy_bridge.sh {self.port}",
                shell=True,
                # stdout=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True
            )
            
            self._running = True
            
            # Start monitoring thread
            monitor_thread = Thread(target=self._monitor_bridge, daemon=True)
            monitor_thread.start()
            
            return True
            
        except Exception as e:
            logger.error(f"Error starting bridge: {e}")
            return False
    
    def stop(self):
        """Stop the Eufy bridge process"""
        if self.process:
            self.process.terminate()
            self.process = None
        
        self._running = False
        self.ready_event.clear()
    
    def is_running(self):
        """Check if bridge process is running"""
        return self._running and (self.process is not None) and (self.process.poll() is None)
    
    def is_ready(self):
        """Check if bridge is ready to accept commands"""
        return self.ready_event.is_set()
    
    
    def _monitor_bridge(self):
        """Monitor bridge output for readiness"""
        if not self.process:
            return
            
        try:
            for line in iter(self.process.stdout.readline, ''):
                if "Eufy Security server listening" in line or "server listening" in line:
                    self.ready_event.set()
                    break
                # Fix: Add null check before calling poll()
                elif self.process and self.process.poll() is not None:
                    self._running = False  # Mark as not running when process exits
                    break
        except Exception as e:
            print(f"Error monitoring bridge: {e}")
            traceback.print_exc()
            self._running = False  # Ensure we mark as not running on error
    
    async def _wait_for_ready(self, timeout=30):
        """Wait for bridge to be ready"""
        for _ in range(timeout):
            if self.is_ready():
                return True
            await asyncio.sleep(1)
        return False
    
    async def _execute_ptz_command(self, camera_serial, direction):
        """Execute PTZ command with automatic stop after duration"""
        if not self.is_ready():
            raise Exception("Bridge not ready")
        
        direction_code = self.directions.get(direction)
        if direction_code is None:
            raise Exception(f"Invalid direction: {direction}")
        
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
                    "serialNumber": camera_serial,
                    "direction": direction_code
                }
                await ws.send(json.dumps(cmd))
                response = await ws.recv()
                
                start_result = json.loads(response)
                if not start_result.get("success", False):
                    return False
                
                # For 360 degree rotation, don't send stop
                if direction == '360':
                    return True
                
                # Wait for movement duration
                await asyncio.sleep(0.5)  # 500ms movement
                
                # Send PTZ stop command
                stop_cmd = {
                    "messageId": "ptz_stop", 
                    "command": "device.pan_and_tilt",
                    "serialNumber": camera_serial,
                    "direction": 5  # Stop command
                }
                await ws.send(json.dumps(stop_cmd))
                stop_response = await ws.recv()
                
                return True
                
        except Exception as e:
            logger.error(f"PTZ command error: {e}")
            return False

    def move_camera(self, camera_serial, direction, device_manager=None):
        """Public method to move camera with improved control"""
        if not self.is_running():
            raise Exception("Bridge not running")
        
        # Apply orientation correction if device_manager is provided
        corrected_direction = direction
        if device_manager:
            corrected_direction = self._correct_direction(camera_serial, direction, device_manager)
        
        try:
            # Run async command in event loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(
                    # self._execute_ptz_command_with_stop(camera_serial, corrected_direction)
                    self._execute_ptz_command(camera_serial, corrected_direction)
                )
            finally:
                loop.close()
                
        except Exception as e:
            logger.error(f"Move camera error: {e}")
            return False
            
    def _correct_direction(self, camera_serial, direction, device_manager):
        """Correct direction based on camera orientation"""
        try:
            camera_info = device_manager.get_camera(camera_serial)
            if not camera_info:
                logger.warning(f"Warning: No camera info found for {camera_serial}")
                return direction
            
            # Check if camera image is mirrored (mounted upside down)
            is_mirrored = camera_info.get('image_mirrored', False)
            
            if is_mirrored:
                # Swap left/right directions for mirrored cameras
                direction_corrections = {
                    'left': 'right',
                    'right': 'left',
                    'up': 'up',      # Up/down stay the same
                    'down': 'down',  # Up/down stay the same  
                    '360': '360'     # 360 stays the same
                }
                corrected = direction_corrections.get(direction, direction)
                if corrected != direction:
                    logger.info(f"Camera {camera_serial} is mirrored: {direction} → {corrected}")
                return corrected
            
            return direction
            
        except Exception as e:
            logger.error(f"Error correcting direction for {camera_serial}: {e}")
            return direction
    
    def __del__(self):
        """Cleanup on destruction"""
        self.stop()