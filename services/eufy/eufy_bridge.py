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
            '360': 0,           # ROTATE360
            'recalibrate': 0,   # ROTATE360 (alias for recalibration)
            'left': 1,          # LEFT
            'right': 2,         # RIGHT
            'up': 3,            # UP
            'down': 4,          # DOWN
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

    async def _wait_for_event(self, ws, event_name, serial_number, timeout=15):
        """
        Wait for a specific event from the WebSocket.

        Events have format: {'type': 'event', 'event': {'source': 'device',
                            'event': 'livestream started', 'serialNumber': 'XXX'}}

        Args:
            ws: WebSocket connection
            event_name: Event name to wait for (e.g., 'livestream started')
            serial_number: Camera serial number to match
            timeout: Maximum wait time in seconds

        Returns:
            dict: The event data if found, None if timeout
        """
        import time
        log_prefix = "[EUFY EVENT]"
        start = time.time()

        print(f"{log_prefix} Waiting for event '{event_name}' for {serial_number} (timeout={timeout}s)")

        while time.time() - start < timeout:
            try:
                response = await asyncio.wait_for(ws.recv(), timeout=1)
                data = json.loads(response)

                # Check if this is an event message
                if data.get("type") == "event":
                    event_data = data.get("event", {})
                    event_type = event_data.get("event")
                    event_serial = event_data.get("serialNumber")

                    print(f"{log_prefix} Received event: {event_type} for {event_serial}")

                    if event_type == event_name and event_serial == serial_number:
                        print(f"{log_prefix} Matched target event!")
                        return data
                else:
                    # Log other message types
                    msg_id = data.get("messageId", "unknown")
                    msg_type = data.get("type", "unknown")
                    print(f"{log_prefix} Received {msg_type} message (id={msg_id}), continuing to wait...")

            except asyncio.TimeoutError:
                continue

        print(f"{log_prefix} Timeout waiting for event '{event_name}'")
        return None

    async def _wait_for_livestream_ready(self, ws, serial_number, timeout=15):
        """
        Wait for livestream to be ready - accepts multiple event types.

        When starting a P2P livestream:
        - NEW stream: fires 'livestream started' event
        - ALREADY RUNNING stream: immediately sends 'livestream video data' events

        This method accepts either as proof that the stream is ready.

        Args:
            ws: WebSocket connection
            serial_number: Camera serial number to match
            timeout: Maximum wait time in seconds

        Returns:
            dict: The event data if found, None if timeout
        """
        import time
        log_prefix = "[EUFY LIVESTREAM]"
        start = time.time()

        # Events that indicate the livestream is ready
        ready_events = {'livestream started', 'livestream video data', 'livestream audio data'}

        print(f"{log_prefix} Waiting for livestream ready for {serial_number} (timeout={timeout}s)")
        print(f"{log_prefix} Accepting events: {ready_events}")

        while time.time() - start < timeout:
            try:
                response = await asyncio.wait_for(ws.recv(), timeout=1)
                data = json.loads(response)

                # Check if this is an event message
                if data.get("type") == "event":
                    event_data = data.get("event", {})
                    event_type = event_data.get("event")
                    event_serial = event_data.get("serialNumber")

                    # Check if this is a ready event for our camera
                    if event_type in ready_events and event_serial == serial_number:
                        print(f"{log_prefix} Livestream ready! (received: {event_type})")
                        return data
                    else:
                        # Log but continue waiting
                        print(f"{log_prefix} Received event: {event_type} for {event_serial}")
                else:
                    # Log other message types
                    msg_id = data.get("messageId", "unknown")
                    msg_type = data.get("type", "unknown")
                    print(f"{log_prefix} Received {msg_type} message (id={msg_id})")

            except asyncio.TimeoutError:
                continue

        print(f"{log_prefix} Timeout waiting for livestream ready")
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
    
    # =========================================================================
    # P2P Livestream Methods (for talkback support)
    # =========================================================================
    # Talkback requires an active P2P livestream session. These methods manage
    # the P2P stream lifecycle separately from RTSP streams used for viewing.

    # Track active P2P livestream sessions for talkback
    # {camera_serial: True} when P2P stream is running
    _active_p2p_sessions = {}

    # Track persistent talkback WebSocket sessions
    # {camera_serial: {'ws': websocket, 'loop': event_loop, 'thread': Thread}}
    # These sessions stay open for the duration of talkback to keep P2P alive
    _talkback_sessions = {}

    async def _execute_livestream_command(self, camera_serial, command):
        """
        Execute P2P livestream command via WebSocket bridge.

        Commands:
            - start: Start P2P livestream (required before talkback)
            - stop: Stop P2P livestream

        The start command returns with {async: True} immediately, but the actual
        stream takes a few seconds to establish. We must wait for the
        'livestream started' event before the stream is ready for talkback.

        Args:
            camera_serial: Camera serial number
            command: 'start' or 'stop'

        Returns:
            bool: True if command succeeded
        """
        log_prefix = "[EUFY P2P STREAM]"
        print(f"{log_prefix} {command}: serial={camera_serial}")

        if not self.is_ready():
            print(f"{log_prefix} ERROR: Bridge not ready!")
            raise Exception("Bridge not ready")

        command_map = {
            'start': 'device.start_livestream',
            'stop': 'device.stop_livestream'
        }

        # Map commands to the events we need to wait for
        event_map = {
            'start': 'livestream started',
            'stop': 'livestream stopped'
        }

        ws_command = command_map.get(command)
        if not ws_command:
            raise ValueError(f"Invalid livestream command: {command}")

        try:
            async with websockets.connect(self.bridge_url, open_timeout=10) as ws:
                print(f"{log_prefix} WebSocket connected")

                # Set API schema version
                await ws.send(json.dumps({
                    "messageId": "schema",
                    "command": "set_api_schema",
                    "schemaVersion": 21
                }))
                await self._wait_for_message(ws, "schema")

                # Start listening (required to receive events)
                await ws.send(json.dumps({
                    "messageId": "start",
                    "command": "start_listening"
                }))
                await self._wait_for_message(ws, "start")

                # Send livestream command
                cmd = {
                    "messageId": f"livestream_{command}",
                    "command": ws_command,
                    "serialNumber": camera_serial
                }
                print(f"{log_prefix} Sending: {ws_command}")
                await ws.send(json.dumps(cmd))

                # Wait for command acknowledgment
                result = await self._wait_for_message(ws, f"livestream_{command}", timeout=10)
                print(f"{log_prefix} Command response: {result}")

                if not result or not result.get("success", False):
                    error = result.get("errorCode", "unknown") if result else "timeout"
                    print(f"{log_prefix} Command failed: {error}")
                    return False

                # For 'start' command: wait for 'livestream started' event
                # The command returns {async: True} but stream isn't ready until event fires
                if command == 'start':
                    is_async = result.get("result", {}).get("async", False)
                    if is_async:
                        print(f"{log_prefix} Async operation - waiting for 'livestream started' event...")
                        event = await self._wait_for_event(
                            ws,
                            event_map[command],
                            camera_serial,
                            timeout=15
                        )
                        if not event:
                            print(f"{log_prefix} Timeout waiting for livestream started event")
                            return False
                        print(f"{log_prefix} Livestream started event received!")

                # Track session state
                if command == 'start':
                    self._active_p2p_sessions[camera_serial] = True
                elif command == 'stop':
                    self._active_p2p_sessions.pop(camera_serial, None)

                print(f"{log_prefix} {command} completed successfully")
                return True

        except Exception as e:
            print(f"{log_prefix} Exception: {e}")
            logger.error(f"Livestream command error: {e}")
            return False

    def start_p2p_livestream(self, camera_serial):
        """
        Start P2P livestream for talkback support.

        This opens a P2P tunnel to the camera which is required before
        talkback can be initiated. The stream runs alongside RTSP.

        Args:
            camera_serial: Camera serial number

        Returns:
            bool: True if P2P stream started successfully
        """
        print(f"[EUFY BRIDGE] start_p2p_livestream: serial={camera_serial}")

        # Check if already running
        if self._active_p2p_sessions.get(camera_serial):
            print(f"[EUFY BRIDGE] P2P stream already active for {camera_serial}")
            return True

        if not self.is_running():
            raise Exception("Bridge not running")

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(
                    self._execute_livestream_command(camera_serial, 'start')
                )
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"start_p2p_livestream error: {e}")
            return False

    def stop_p2p_livestream(self, camera_serial):
        """
        Stop P2P livestream.

        Should be called after talkback ends to release resources.

        Args:
            camera_serial: Camera serial number

        Returns:
            bool: True if P2P stream stopped successfully
        """
        print(f"[EUFY BRIDGE] stop_p2p_livestream: serial={camera_serial}")

        if not self._active_p2p_sessions.get(camera_serial):
            print(f"[EUFY BRIDGE] No active P2P stream for {camera_serial}")
            return True

        if not self.is_running():
            raise Exception("Bridge not running")

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(
                    self._execute_livestream_command(camera_serial, 'stop')
                )
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"stop_p2p_livestream error: {e}")
            return False

    def is_p2p_streaming(self, camera_serial):
        """Check if P2P livestream is active for camera."""
        return self._active_p2p_sessions.get(camera_serial, False)

    # =========================================================================
    # Two-Way Audio (Talkback) Methods
    # =========================================================================

    async def _execute_talkback_command(self, camera_serial, command, audio_data=None):
        """
        Execute talkback command via WebSocket bridge.

        The eufy-security-client supports two-way audio through TalkbackStream.
        Commands:
            - start_talkback: Initiates talkback session with camera
            - stop_talkback: Ends talkback session
            - talkback_audio_data: Sends audio frame to camera (requires audio_data)

        Args:
            camera_serial: Camera serial number (e.g., T8416P0023352DA9)
            command: One of 'start', 'stop', 'audio_data'
            audio_data: Base64-encoded audio data (only for 'audio_data' command)

        Returns:
            bool: True if command succeeded
        """
        log_prefix = "[EUFY TALKBACK]"
        print(f"{log_prefix} Starting: serial={camera_serial}, cmd={command}")

        if not self.is_ready():
            print(f"{log_prefix} ERROR: Bridge not ready!")
            raise Exception("Bridge not ready")

        # Map command names to eufy-security-ws commands
        command_map = {
            'start': 'device.start_talkback',
            'stop': 'device.stop_talkback',
            'audio_data': 'device.talkback_audio_data'
        }

        ws_command = command_map.get(command)
        if not ws_command:
            raise ValueError(f"Invalid talkback command: {command}")

        print(f"{log_prefix} Command: {ws_command}, connecting to {self.bridge_url}")

        try:
            async with websockets.connect(self.bridge_url, open_timeout=5) as ws:
                print(f"{log_prefix} WebSocket connected")

                # Set API schema version
                await ws.send(json.dumps({
                    "messageId": "schema",
                    "command": "set_api_schema",
                    "schemaVersion": 21
                }))
                schema_result = await self._wait_for_message(ws, "schema")
                print(f"{log_prefix} Schema response: {schema_result}")

                # Start listening
                await ws.send(json.dumps({
                    "messageId": "start",
                    "command": "start_listening"
                }))
                listen_result = await self._wait_for_message(ws, "start")
                print(f"{log_prefix} Listen response: {listen_result}")

                # Build talkback command
                cmd = {
                    "messageId": f"talkback_{command}",
                    "command": ws_command,
                    "serialNumber": camera_serial
                }

                # Add audio data for audio_data command
                if command == 'audio_data' and audio_data:
                    cmd["audioData"] = audio_data

                print(f"{log_prefix} Sending command: {cmd.get('command')} (data len: {len(audio_data) if audio_data else 0})")
                await ws.send(json.dumps(cmd))
                result = await self._wait_for_message(ws, f"talkback_{command}")
                print(f"{log_prefix} Response: {result}")

                if not result or not result.get("success", False):
                    error = result.get("errorCode", "unknown") if result else "timeout"
                    print(f"{log_prefix} Command failed: {error}")
                    return False

                print(f"{log_prefix} Command completed successfully")
                return True

        except Exception as e:
            print(f"{log_prefix} Exception: {e}")
            logger.error(f"Talkback command error: {e}")
            return False

    async def _start_talkback_session(self, camera_serial):
        """
        Start a PERSISTENT talkback session with P2P livestream.

        CRITICAL: The P2P stream only stays alive while the WebSocket connection
        that started it remains open. This method establishes the connection and
        stores it for continued audio transmission.

        The WebSocket is kept open until stop_talkback() is called.

        Args:
            camera_serial: Camera serial number

        Returns:
            bool: True if talkback started successfully
        """
        log_prefix = "[EUFY TALKBACK SESSION]"
        print(f"{log_prefix} Starting persistent session for {camera_serial}")

        if not self.is_ready():
            print(f"{log_prefix} ERROR: Bridge not ready!")
            raise Exception("Bridge not ready")

        # Check if session already exists
        if camera_serial in self._talkback_sessions:
            print(f"{log_prefix} Session already exists for {camera_serial}")
            return True

        try:
            # Create persistent WebSocket connection (NOT using async with)
            ws = await websockets.connect(self.bridge_url, open_timeout=10)
            print(f"{log_prefix} WebSocket connected")

            # Set API schema version
            await ws.send(json.dumps({
                "messageId": "schema",
                "command": "set_api_schema",
                "schemaVersion": 21
            }))
            await self._wait_for_message(ws, "schema")

            # Start listening (required to receive events)
            await ws.send(json.dumps({
                "messageId": "start",
                "command": "start_listening"
            }))
            await self._wait_for_message(ws, "start")

            # === Step 1: Start P2P livestream ===
            print(f"{log_prefix} Step 1: Starting P2P livestream...")
            await ws.send(json.dumps({
                "messageId": "livestream_start",
                "command": "device.start_livestream",
                "serialNumber": camera_serial
            }))

            # Wait for command acknowledgment
            result = await self._wait_for_message(ws, "livestream_start", timeout=10)
            print(f"{log_prefix} P2P command response: {result}")

            if not result or not result.get("success", False):
                error = result.get("errorCode", "unknown") if result else "timeout"
                print(f"{log_prefix} P2P start failed: {error}")
                await ws.close()
                return False

            # Wait for 'livestream started' event OR data events (stream already active)
            is_async = result.get("result", {}).get("async", False)
            if is_async:
                print(f"{log_prefix} Waiting for livestream to be ready...")
                # Accept either 'livestream started' (new stream) or 'livestream video data' (already running)
                event = await self._wait_for_livestream_ready(ws, camera_serial, timeout=15)
                if not event:
                    print(f"{log_prefix} Timeout waiting for P2P stream")
                    await ws.close()
                    return False
                print(f"{log_prefix} P2P livestream ready!")

            self._active_p2p_sessions[camera_serial] = True

            # === Step 2: Start talkback (on the SAME WebSocket session) ===
            print(f"{log_prefix} Step 2: Starting talkback...")
            await ws.send(json.dumps({
                "messageId": "talkback_start",
                "command": "device.start_talkback",
                "serialNumber": camera_serial
            }))

            result = await self._wait_for_message(ws, "talkback_start", timeout=10)
            print(f"{log_prefix} Talkback response: {result}")

            if not result or not result.get("success", False):
                error = result.get("errorCode", "unknown") if result else "timeout"
                print(f"{log_prefix} Talkback start failed: {error}")
                self._active_p2p_sessions.pop(camera_serial, None)
                await ws.close()
                return False

            # Store the persistent session
            self._talkback_sessions[camera_serial] = {
                'ws': ws,
                'started': time.time()
            }

            print(f"{log_prefix} Talkback session established! WebSocket kept open for audio.")
            return True

        except Exception as e:
            print(f"{log_prefix} Exception: {e}")
            logger.error(f"start_talkback_session error: {e}")
            self._active_p2p_sessions.pop(camera_serial, None)
            return False

    async def _send_audio_to_session(self, camera_serial, audio_data):
        """
        Send audio data through the persistent talkback session.

        Args:
            camera_serial: Camera serial number
            audio_data: Base64-encoded audio data

        Returns:
            bool: True if audio sent successfully
        """
        session = self._talkback_sessions.get(camera_serial)
        if not session:
            print(f"[EUFY AUDIO] No active session for {camera_serial}")
            return False

        ws = session.get('ws')
        # websockets 10+ uses .state instead of .closed
        if not ws or (hasattr(ws, 'closed') and ws.closed) or (hasattr(ws, 'state') and ws.state.name != 'OPEN'):
            print(f"[EUFY AUDIO] WebSocket closed for {camera_serial}")
            self._talkback_sessions.pop(camera_serial, None)
            return False

        try:
            # eufy-security-ws expects 'buffer' property, not 'audioData'
            # The buffer should be base64-encoded AAC ADTS audio
            await ws.send(json.dumps({
                "messageId": "talkback_audio",
                "command": "device.talkback_audio_data",
                "serialNumber": camera_serial,
                "buffer": audio_data  # Changed from 'audioData' to 'buffer'
            }))
            return True
        except Exception as e:
            print(f"[EUFY AUDIO] Send error: {e}")
            return False

    async def _stop_talkback_session(self, camera_serial):
        """
        Stop the persistent talkback session and close WebSocket.

        Args:
            camera_serial: Camera serial number

        Returns:
            bool: True if stopped successfully
        """
        log_prefix = "[EUFY TALKBACK SESSION]"
        print(f"{log_prefix} Stopping session for {camera_serial}")

        session = self._talkback_sessions.pop(camera_serial, None)
        if not session:
            print(f"{log_prefix} No active session for {camera_serial}")
            return True

        ws = session.get('ws')
        # websockets 10+ uses .state instead of .closed
        ws_is_open = ws and ((hasattr(ws, 'closed') and not ws.closed) or (hasattr(ws, 'state') and ws.state.name == 'OPEN'))
        if ws_is_open:
            try:
                # Stop talkback
                await ws.send(json.dumps({
                    "messageId": "talkback_stop",
                    "command": "device.stop_talkback",
                    "serialNumber": camera_serial
                }))
                await self._wait_for_message(ws, "talkback_stop", timeout=5)

                # Stop P2P livestream
                await ws.send(json.dumps({
                    "messageId": "livestream_stop",
                    "command": "device.stop_livestream",
                    "serialNumber": camera_serial
                }))
                await self._wait_for_message(ws, "livestream_stop", timeout=5)

                # Close the WebSocket
                await ws.close()
                print(f"{log_prefix} WebSocket closed cleanly")

            except Exception as e:
                print(f"{log_prefix} Error during stop: {e}")
                try:
                    await ws.close()
                except:
                    pass

        self._active_p2p_sessions.pop(camera_serial, None)
        print(f"{log_prefix} Session stopped for {camera_serial}")
        return True

    def start_talkback(self, camera_serial):
        """
        Start talkback session with camera.

        This establishes a PERSISTENT WebSocket connection with P2P livestream
        and talkback enabled. The connection stays open for audio transmission
        until stop_talkback() is called.

        Args:
            camera_serial: Camera serial number

        Returns:
            bool: True if talkback session started successfully
        """
        print(f"[EUFY BRIDGE] start_talkback: serial={camera_serial}")

        if not self.is_running():
            raise Exception("Bridge not running")

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(
                    self._start_talkback_session(camera_serial)
                )
            finally:
                # NOTE: We do NOT close the loop here - we need to keep it
                # for subsequent audio calls. Store it with the session.
                pass
        except Exception as e:
            logger.error(f"start_talkback error: {e}")
            return False

    def stop_talkback(self, camera_serial):
        """
        Stop talkback session with camera.

        Ends the talkback audio stream, stops the P2P livestream, and
        closes the persistent WebSocket connection.

        Args:
            camera_serial: Camera serial number

        Returns:
            bool: True if talkback session stopped successfully
        """
        print(f"[EUFY BRIDGE] stop_talkback: serial={camera_serial}")

        if not self.is_running():
            raise Exception("Bridge not running")

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(
                    self._stop_talkback_session(camera_serial)
                )
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"stop_talkback error: {e}")
            # Force cleanup
            self._talkback_sessions.pop(camera_serial, None)
            self._active_p2p_sessions.pop(camera_serial, None)
            return False

    def send_talkback_audio(self, camera_serial, audio_data):
        """
        Send audio frame to camera through the persistent talkback session.

        Audio must be sent after start_talkback() and before stop_talkback().
        Format expected: Base64-encoded PCM audio (16kHz, mono, 16-bit).

        Args:
            camera_serial: Camera serial number
            audio_data: Base64-encoded audio data

        Returns:
            bool: True if audio frame sent successfully
        """
        # Note: Not logging every audio frame to avoid log spam
        if not self.is_running():
            return False

        # Check if session exists
        session = self._talkback_sessions.get(camera_serial)
        if not session:
            print(f"[EUFY BRIDGE] No active talkback session for {camera_serial}")
            return False

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(
                    self._send_audio_to_session(camera_serial, audio_data)
                )
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"send_talkback_audio error: {e}")
            return False

    def __del__(self):
        """Cleanup on destruction"""
        self.stop()