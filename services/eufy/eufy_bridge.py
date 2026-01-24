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
        
        # PTZ direction mapping (from eufy-security-client PanTiltDirection enum)
        # ROTATE360=0, LEFT=1, RIGHT=2, UP=3, DOWN=4
        # NOTE: There is NO stop command - cameras auto-stop after movement
        self.directions = {
            '360': 0,     # ROTATE360
            'left': 1,    # LEFT
            'right': 2,   # RIGHT
            'up': 3,      # UP
            'down': 4,    # DOWN
        }

        # PTZ preset positions (4 slots available: 0-3)
        # Supported on: T8416 (S350), T8425 (Floodlight), T8423 (Floodlight)
        self.PRESET_SLOTS = 4
    
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
        """Execute PTZ command - Eufy cameras auto-stop, no explicit stop needed"""
        print(f"[EUFY PTZ CMD] Starting: serial={camera_serial}, direction={direction}")

        if not self.is_ready():
            print(f"[EUFY PTZ CMD] ERROR: Bridge not ready!")
            raise Exception("Bridge not ready")

        # 'stop' command from frontend - Eufy doesn't support this, cameras auto-stop
        if direction == 'stop':
            print(f"[EUFY PTZ CMD] Stop command ignored - Eufy cameras auto-stop")
            return True

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

                # Send PTZ command
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

                # Eufy cameras auto-stop after movement - no explicit stop needed
                print(f"[EUFY PTZ CMD] PTZ command completed successfully (camera will auto-stop)")
                return True

        except Exception as e:
            print(f"[EUFY PTZ CMD] Exception: {e}")
            logger.error(f"PTZ command error: {e}")
            return False

    def move_camera(self, camera_serial, direction, device_manager=None):
        """Public method to move camera with improved control.

        Note: Direction correction is now handled in the frontend via the
        'Rev. Pan' and 'Rev. Tilt' checkboxes (ptz-controller.js applyReversal).
        The legacy _correct_direction method is kept but no longer called to
        avoid double-correction issues.
        """
        print(f"[EUFY BRIDGE] move_camera called: serial={camera_serial}, direction={direction}")

        if not self.is_running():
            print(f"[EUFY BRIDGE] ERROR: Bridge not running!")
            raise Exception("Bridge not running")

        # Direction correction now handled in frontend (ptz-controller.js applyReversal)
        # to avoid double-correction when both image_mirrored and reversed_pan are set

        try:
            # Run async command in event loop
            print(f"[EUFY BRIDGE] Creating event loop for PTZ command...")
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(
                    self._execute_ptz_command(camera_serial, direction)
                )
                print(f"[EUFY BRIDGE] PTZ command result: {result}")
                return result
            finally:
                loop.close()

        except Exception as e:
            print(f"[EUFY BRIDGE] Move camera error: {e}")
            logger.error(f"Move camera error: {e}")
            return False
            
    # =========================================================================
    # PTZ Preset Methods
    # =========================================================================

    async def _execute_preset_command(self, camera_serial, command, preset_index):
        """
        Execute PTZ preset command via WebSocket bridge.

        Args:
            camera_serial: Camera serial number (e.g., T8416P0023352DA9)
            command: One of 'goto', 'save', 'delete'
            preset_index: Preset slot (0-3)

        Returns:
            bool: True if command succeeded
        """
        print(f"[EUFY PRESET] Starting: serial={camera_serial}, cmd={command}, preset={preset_index}")

        if not self.is_ready():
            print(f"[EUFY PRESET] ERROR: Bridge not ready!")
            raise Exception("Bridge not ready")

        if not (0 <= preset_index < self.PRESET_SLOTS):
            raise ValueError(f"Invalid preset index: {preset_index}. Must be 0-{self.PRESET_SLOTS - 1}")

        # Map command names to eufy-security-ws commands
        command_map = {
            'goto': 'device.preset_position',
            'save': 'device.save_preset_position',
            'delete': 'device.delete_preset_position'
        }

        ws_command = command_map.get(command)
        if not ws_command:
            raise ValueError(f"Invalid preset command: {command}")

        print(f"[EUFY PRESET] Command: {ws_command}, connecting to {self.bridge_url}")

        try:
            async with websockets.connect(self.bridge_url, open_timeout=5) as ws:
                print(f"[EUFY PRESET] WebSocket connected")

                # Set API schema version
                await ws.send(json.dumps({
                    "messageId": "schema",
                    "command": "set_api_schema",
                    "schemaVersion": 21
                }))
                schema_result = await self._wait_for_message(ws, "schema")
                print(f"[EUFY PRESET] Schema response: {schema_result}")

                # Start listening
                await ws.send(json.dumps({
                    "messageId": "start",
                    "command": "start_listening"
                }))
                listen_result = await self._wait_for_message(ws, "start")
                print(f"[EUFY PRESET] Listen response: {listen_result}")

                # Send preset command
                cmd = {
                    "messageId": f"preset_{command}",
                    "command": ws_command,
                    "serialNumber": camera_serial,
                    "position": preset_index
                }
                print(f"[EUFY PRESET] Sending command: {cmd}")
                await ws.send(json.dumps(cmd))
                result = await self._wait_for_message(ws, f"preset_{command}")
                print(f"[EUFY PRESET] Response: {result}")

                if not result or not result.get("success", False):
                    error = result.get("errorCode", "unknown") if result else "timeout"
                    print(f"[EUFY PRESET] Command failed: {error}")
                    return False

                print(f"[EUFY PRESET] Command completed successfully")
                return True

        except Exception as e:
            print(f"[EUFY PRESET] Exception: {e}")
            logger.error(f"Preset command error: {e}")
            return False

    def goto_preset(self, camera_serial, preset_index):
        """
        Move camera to a saved preset position.

        Args:
            camera_serial: Camera serial number
            preset_index: Preset slot (0-3)

        Returns:
            bool: True if command succeeded
        """
        print(f"[EUFY BRIDGE] goto_preset: serial={camera_serial}, preset={preset_index}")

        if not self.is_running():
            raise Exception("Bridge not running")

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(
                    self._execute_preset_command(camera_serial, 'goto', preset_index)
                )
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"goto_preset error: {e}")
            return False

    def save_preset(self, camera_serial, preset_index):
        """
        Save current camera position as a preset.

        Args:
            camera_serial: Camera serial number
            preset_index: Preset slot (0-3)

        Returns:
            bool: True if command succeeded
        """
        print(f"[EUFY BRIDGE] save_preset: serial={camera_serial}, preset={preset_index}")

        if not self.is_running():
            raise Exception("Bridge not running")

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(
                    self._execute_preset_command(camera_serial, 'save', preset_index)
                )
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"save_preset error: {e}")
            return False

    def delete_preset(self, camera_serial, preset_index):
        """
        Delete a preset position.

        Args:
            camera_serial: Camera serial number
            preset_index: Preset slot (0-3)

        Returns:
            bool: True if command succeeded
        """
        print(f"[EUFY BRIDGE] delete_preset: serial={camera_serial}, preset={preset_index}")

        if not self.is_running():
            raise Exception("Bridge not running")

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(
                    self._execute_preset_command(camera_serial, 'delete', preset_index)
                )
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"delete_preset error: {e}")
            return False

    def get_presets(self, camera_serial):
        """
        Get list of available presets for Eufy camera.

        Note: Eufy cameras have 4 fixed preset slots (0-3).
        The API doesn't return which slots are actually configured,
        so we return all 4 slots with generic names.

        Args:
            camera_serial: Camera serial number

        Returns:
            list: List of preset dicts with 'token' (index) and 'name'
        """
        # Eufy has 4 fixed preset slots - we can't query which are configured
        # Return all 4 with generic names
        return [
            {'token': 0, 'name': 'Preset 1'},
            {'token': 1, 'name': 'Preset 2'},
            {'token': 2, 'name': 'Preset 3'},
            {'token': 3, 'name': 'Preset 4'},
        ]

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