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
        self.script_path = "./services/eufy/eufy_bridge.sh"
        
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
            
            print("########### KILL eufy-security-server ###########")
            # Kill any existing bridge process
            subprocess.run(f"pkill -f 'eufy-security-server.*port {self.port}'", 
                         shell=True, stderr=subprocess.DEVNULL)
            time.sleep(1)
            
            
            print("########### Starting bridge process ###########")
            # Start bridge process
            self.process = subprocess.Popen(
                f"bash {self.script_path} {self.port}",
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True
            )
            
            # Give it a moment to start
            time.sleep(2)
            
            # CHECK IF STILL ALIVE
            if self.process.poll() is not None:
                # Process died! Get the output
                output = self.process.stdout.read()
                print(f"❌ BRIDGE CRASHED IMMEDIATELY!")
                print(f"Exit code: {self.process.returncode}")
                print(f"Output:\n{output}")
                return False
            
            print("########### BRIDGE PROCESS STARTED ###########")
            self._running = True  # Mark as running so is_running() returns True

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
    
    async def _wait_for_message(self, ws, expected_message_id, timeout=5):
        """Wait for a specific messageId response, discarding others"""
        import time
        start = time.time()
        while time.time() - start < timeout:
            try:
                response = await asyncio.wait_for(ws.recv(), timeout=1)
                data = json.loads(response)
                msg_id = data.get("messageId")
                print(f"[EUFY PTZ CMD] Received messageId={msg_id}, waiting for {expected_message_id}")
                if msg_id == expected_message_id:
                    return data
                # Keep waiting for the right message
            except asyncio.TimeoutError:
                continue
        return None

    async def _execute_ptz_command(self, camera_serial, direction):
        """Execute PTZ command with automatic stop after duration"""
        print(f"[EUFY PTZ CMD] Starting: serial={camera_serial}, direction={direction}")

        if not self.is_ready():
            print(f"[EUFY PTZ CMD] ERROR: Bridge not ready!")
            raise Exception("Bridge not ready")

        direction_code = self.directions.get(direction)
        if direction_code is None:
            print(f"[EUFY PTZ CMD] ERROR: Invalid direction: {direction}")
            raise Exception(f"Invalid direction: {direction}")

        print(f"[EUFY PTZ CMD] Direction code: {direction_code}, connecting to {self.bridge_url}")

        try:
            async with websockets.connect(self.bridge_url, open_timeout=5) as ws:
                print(f"[EUFY PTZ CMD] WebSocket connected")

                # Set API schema version
                await ws.send(json.dumps({
                    "messageId": "schema",
                    "command": "set_api_schema",
                    "schemaVersion": 21
                }))
                schema_result = await self._wait_for_message(ws, "schema")
                print(f"[EUFY PTZ CMD] Schema response: {schema_result}")

                # Start listening
                await ws.send(json.dumps({
                    "messageId": "start",
                    "command": "start_listening"
                }))
                listen_result = await self._wait_for_message(ws, "start")
                print(f"[EUFY PTZ CMD] Listen response: {listen_result}")

                # Send PTZ start command
                cmd = {
                    "messageId": "ptz_move",
                    "command": "device.pan_and_tilt",
                    "serialNumber": camera_serial,
                    "direction": direction_code
                }
                print(f"[EUFY PTZ CMD] Sending PTZ command: {cmd}")
                await ws.send(json.dumps(cmd))
                ptz_result = await self._wait_for_message(ws, "ptz_move")
                print(f"[EUFY PTZ CMD] PTZ response: {ptz_result}")

                if not ptz_result or not ptz_result.get("success", False):
                    error = ptz_result.get("errorCode", "unknown") if ptz_result else "timeout"
                    print(f"[EUFY PTZ CMD] PTZ command failed: {error}")
                    return False

                # For 360 degree rotation or stop command, don't send additional stop
                if direction in ('360', 'stop'):
                    print(f"[EUFY PTZ CMD] {direction} command - no additional stop needed")
                    return True

                # Wait for movement duration
                await asyncio.sleep(0.5)  # 500ms movement

                # Send PTZ stop command (some cameras don't support this - that's OK)
                stop_cmd = {
                    "messageId": "ptz_stop",
                    "command": "device.pan_and_tilt",
                    "serialNumber": camera_serial,
                    "direction": 5  # Stop command
                }
                print(f"[EUFY PTZ CMD] Sending stop command")
                await ws.send(json.dumps(stop_cmd))
                stop_result = await self._wait_for_message(ws, "ptz_stop")
                if stop_result and not stop_result.get("success", False):
                    # Stop not supported - camera likely auto-stops after movement
                    print(f"[EUFY PTZ CMD] Stop not supported (camera auto-stops): {stop_result.get('errorCode')}")
                else:
                    print(f"[EUFY PTZ CMD] Stop response: {stop_result}")

                print(f"[EUFY PTZ CMD] PTZ command completed successfully")
                return True

        except Exception as e:
            print(f"[EUFY PTZ CMD] Exception: {e}")
            logger.error(f"PTZ command error: {e}")
            return False

    def move_camera(self, camera_serial, direction, device_manager=None):
        """Public method to move camera with improved control"""
        print(f"[EUFY BRIDGE] move_camera called: serial={camera_serial}, direction={direction}")

        if not self.is_running():
            print(f"[EUFY BRIDGE] ERROR: Bridge not running!")
            raise Exception("Bridge not running")

        # Apply orientation correction if device_manager is provided
        corrected_direction = direction
        if device_manager:
            corrected_direction = self._correct_direction(camera_serial, direction, device_manager)
            print(f"[EUFY BRIDGE] Direction after correction: {corrected_direction}")

        try:
            # Run async command in event loop
            print(f"[EUFY BRIDGE] Creating event loop for PTZ command...")
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(
                    self._execute_ptz_command(camera_serial, corrected_direction)
                )
                print(f"[EUFY BRIDGE] PTZ command result: {result}")
                return result
            finally:
                loop.close()

        except Exception as e:
            print(f"[EUFY BRIDGE] Move camera error: {e}")
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