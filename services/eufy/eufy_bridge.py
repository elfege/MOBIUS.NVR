#!/usr/bin/env python3
# eufy_bridge.py
#
# Eufy Bridge Management Class
#
# Manages the eufy-security-ws WebSocket server process and provides
# PTZ control, preset management, and P2P livestream support for Eufy cameras.
#
# Self-healing: When the eufy-security-server crashes (common due to P2P
# session key expiration), the bridge auto-detects via ConnectionRefusedError
# on port 3000, marks itself dead, attempts one automatic restart, and
# reports meaningful errors to callers if recovery fails.
#
import asyncio
import json
import socket
import websockets
import traceback
import subprocess
import signal
import time
import os
from threading import Thread, Event, Lock
import logging

logger = logging.getLogger(__name__)


class BridgeCrashedError(Exception):
    """Raised when the eufy-security-server process is detected as dead.

    The WebSocket server on port 3000 is not accepting connections.
    This typically happens when P2P session keys expire and the server
    crashes with decryption errors.
    """
    pass


class BridgeAuthRequiredError(Exception):
    """Raised when the bridge needs re-authentication (captcha/2FA).

    The server started but Eufy cloud requires human interaction
    to complete login (captcha image, 2FA code, etc.).
    """
    pass


class EufyBridge:
    """Manages the Eufy WebSocket bridge process and PTZ commands.

    Architecture:
        Python (this class) -> WebSocket -> eufy-security-ws (Node.js) -> Eufy Cloud/P2P

    The eufy-security-server binary runs as a subprocess managed by eufy_bridge.sh.
    PTZ commands are sent via WebSocket to localhost:3000. The bridge requires
    Eufy cloud authentication for P2P session establishment, even though the
    actual PTZ commands travel over the local P2P connection.

    Self-healing:
        When a PTZ command fails with ConnectionRefusedError (port 3000 dead),
        the bridge attempts one automatic restart. If the restart succeeds
        (cached auth tokens still valid), the PTZ command is retried. If the
        restart fails (auth expired, captcha required), a clear error is returned
        directing the user to /eufy-auth.
    """

    # Maximum number of automatic restart attempts before giving up
    MAX_AUTO_RESTARTS = 2
    # Cooldown between restart attempts (seconds)
    RESTART_COOLDOWN = 10
    # Interval between keepalive health checks (seconds) — prevents P2P session expiry
    KEEPALIVE_INTERVAL = 1800  # 30 minutes

    def __init__(self, port=3000):
        self.port = port
        self.bridge_url = f"ws://127.0.0.1:{port}"
        self.process = None
        self.ready_event = Event()
        self._running = False
        self._restart_lock = Lock()
        self._last_restart_attempt = 0
        self._crash_reason = None  # Store why the bridge died
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

            # Start keepalive thread to prevent P2P session expiration
            keepalive_thread = Thread(target=self._keepalive_loop, daemon=True,
                                     name="eufy-keepalive")
            keepalive_thread.start()

            return True
            
        except Exception as e:
            logger.error(f"Error starting bridge: {e}")
            return False
    
    def stop(self):
        """Stop the Eufy bridge process and clean up all state."""
        if self.process:
            self.process.terminate()
            self.process = None

        self._running = False
        self.ready_event.clear()
        self._crash_reason = None

    def _mark_bridge_dead(self, reason):
        """Mark the bridge as dead with a specific reason.

        Called when we detect the eufy-security-server has crashed
        (e.g., ConnectionRefusedError on port 3000). Updates internal state
        so that is_running() returns False and subsequent callers get
        the 503 "bridge not running" response instead of misleading
        "Movement failed".

        Args:
            reason: Human-readable crash reason (logged and returned to callers)
        """
        self._running = False
        self.ready_event.clear()
        self._crash_reason = reason
        logger.error(f"[EUFY BRIDGE] Bridge marked DEAD: {reason}")
        print(f"[EUFY BRIDGE] Bridge marked DEAD: {reason}")

    def _check_port_alive(self):
        """Lightweight check: is the WebSocket server actually listening on its port?

        Returns:
            bool: True if port 3000 accepts TCP connections, False otherwise.
        """
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=1):
                return True
        except (ConnectionRefusedError, OSError):
            return False

    def is_running(self):
        """Check if bridge process is running AND the WebSocket server is alive.

        This checks both the subprocess wrapper AND the actual TCP port.
        The shell wrapper (eufy_bridge.sh) can remain alive after the
        inner eufy-security-server Node.js process crashes, so checking
        process.poll() alone is insufficient.
        """
        if not (self._running and self.process is not None and self.process.poll() is None):
            return False

        # The subprocess wrapper is alive, but is the actual server listening?
        if not self._check_port_alive():
            self._mark_bridge_dead(
                "eufy-security-server crashed (port 3000 not responding). "
                "Common cause: P2P session key expiration."
            )
            return False

        return True

    def is_ready(self):
        """Check if bridge is ready to accept commands."""
        return self.ready_event.is_set()

    def restart(self):
        """Attempt to restart the bridge after a crash.

        Thread-safe: uses a lock to prevent concurrent restart attempts.
        Respects RESTART_COOLDOWN to avoid restart storms.

        Returns:
            bool: True if the bridge restarted and is ready for commands.
        """
        with self._restart_lock:
            now = time.time()
            if now - self._last_restart_attempt < self.RESTART_COOLDOWN:
                remaining = self.RESTART_COOLDOWN - (now - self._last_restart_attempt)
                print(f"[EUFY BRIDGE] Restart cooldown active ({remaining:.0f}s remaining)")
                return False

            self._last_restart_attempt = now
            print("[EUFY BRIDGE] Attempting automatic restart...")
            logger.info("[EUFY BRIDGE] Attempting automatic restart after crash")

            # Kill the old process tree
            self.stop()
            time.sleep(1)

            # Try to start fresh
            started = self.start()
            if not started:
                print("[EUFY BRIDGE] Restart failed: process did not start")
                return False

            # Wait for the server to become ready (up to 30s)
            deadline = time.time() + 30
            while time.time() < deadline:
                if self.is_ready():
                    print("[EUFY BRIDGE] Restart successful - bridge is ready")
                    logger.info("[EUFY BRIDGE] Restart successful")
                    self._crash_reason = None
                    return True
                time.sleep(1)

            print("[EUFY BRIDGE] Restart failed: server started but never became ready "
                  "(likely needs re-authentication at /eufy-auth)")
            self._crash_reason = (
                "Bridge restarted but authentication failed. "
                "Visit /eufy-auth to re-authenticate."
            )
            return False

    def get_status(self):
        """Get detailed bridge status for diagnostics.

        Returns:
            dict: Status info including running state, crash reason, port check.
        """
        port_alive = self._check_port_alive()
        process_alive = self.process is not None and self.process.poll() is None
        return {
            'running': self._running,
            'ready': self.ready_event.is_set(),
            'process_alive': process_alive,
            'port_alive': port_alive,
            'crash_reason': self._crash_reason,
            'port': self.port,
        }

    def _monitor_bridge(self):
        """Monitor bridge output for readiness and ongoing health.

        Runs in a daemon thread. Two phases:
        1. Wait for "server listening" line to set ready_event
        2. Continue reading output to detect process death
        """
        if not self.process:
            return

        ready_detected = False
        try:
            for line in iter(self.process.stdout.readline, ''):
                # Phase 1: Detect server readiness
                if not ready_detected:
                    if "Eufy Security server listening" in line or "server listening" in line:
                        self.ready_event.set()
                        ready_detected = True
                        print("[EUFY BRIDGE MONITOR] Server is ready and listening")

                # Check if process exited while we were reading
                if self.process and self.process.poll() is not None:
                    self._mark_bridge_dead(
                        f"Bridge process exited (code {self.process.returncode})"
                    )
                    return

            # readline returned '' — stdout closed, process is dead
            if self.process and self.process.poll() is not None:
                self._mark_bridge_dead(
                    f"Bridge process exited (code {self.process.returncode})"
                )
            else:
                self._mark_bridge_dead("Bridge output stream closed unexpectedly")

        except Exception as e:
            print(f"[EUFY BRIDGE MONITOR] Error: {e}")
            traceback.print_exc()
            self._mark_bridge_dead(f"Monitor thread error: {e}")

    def _keepalive_loop(self):
        """Periodic health check to prevent P2P session key expiration.

        The eufy-security-server P2P sessions can expire after extended inactivity.
        This thread pings the port every KEEPALIVE_INTERVAL seconds and proactively
        restarts the bridge if it's found dead — before any user action fails.
        """
        print(f"[EUFY BRIDGE KEEPALIVE] Started (interval: {self.KEEPALIVE_INTERVAL}s)")
        while self._running:
            # Sleep in small increments so we can exit quickly on stop()
            for _ in range(self.KEEPALIVE_INTERVAL):
                if not self._running:
                    return
                time.sleep(1)

            if not self._running:
                return

            try:
                if not self._check_port_alive():
                    logger.warning("[EUFY BRIDGE KEEPALIVE] Port check failed, attempting restart")
                    print("[EUFY BRIDGE KEEPALIVE] Port check failed, attempting restart")
                    self._mark_bridge_dead("Keepalive detected port 3000 not responding")
                    if self.restart():
                        print("[EUFY BRIDGE KEEPALIVE] Proactive restart succeeded")
                        logger.info("[EUFY BRIDGE KEEPALIVE] Proactive restart succeeded")
                    else:
                        print("[EUFY BRIDGE KEEPALIVE] Proactive restart failed")
                        logger.error("[EUFY BRIDGE KEEPALIVE] Proactive restart failed")
                else:
                    print("[EUFY BRIDGE KEEPALIVE] Bridge healthy (port 3000 responding)")
            except Exception as e:
                logger.error(f"[EUFY BRIDGE KEEPALIVE] Error: {e}")

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
        """Execute PTZ command via WebSocket to eufy-security-ws.

        Eufy cameras auto-stop after movement — no explicit stop needed.

        Raises:
            BridgeCrashedError: If WebSocket connection to port 3000 is refused
                (server process has crashed).
        """
        print(f"[EUFY PTZ CMD] Starting: serial={camera_serial}, direction={direction}")

        if not self.is_ready():
            print(f"[EUFY PTZ CMD] ERROR: Bridge not ready!")
            raise BridgeCrashedError("Bridge not ready — server may have crashed")

        # 'stop' command from frontend — Eufy doesn't support this, cameras auto-stop
        if direction == 'stop':
            print(f"[EUFY PTZ CMD] Stop command ignored — Eufy cameras auto-stop")
            return True

        direction_code = self.directions.get(direction)
        if direction_code is None:
            print(f"[EUFY PTZ CMD] ERROR: Invalid direction: {direction}")
            raise ValueError(f"Invalid direction: {direction}")

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

                # Eufy cameras auto-stop after movement — no explicit stop needed
                print(f"[EUFY PTZ CMD] PTZ command completed successfully (camera will auto-stop)")
                return True

        except (ConnectionRefusedError, OSError) as e:
            # Server process is dead — port 3000 not listening
            self._mark_bridge_dead(
                f"Connection refused on port {self.port}: eufy-security-server has crashed. "
                f"Likely cause: P2P session key expiration or cloud auth failure."
            )
            raise BridgeCrashedError(self._crash_reason) from e

        except Exception as e:
            print(f"[EUFY PTZ CMD] Exception: {e}")
            logger.error(f"PTZ command error: {e}")
            # Check if this is a wrapped ConnectionRefusedError
            if "Connect call failed" in str(e) or "Connection refused" in str(e):
                self._mark_bridge_dead(
                    f"Connection refused on port {self.port}: eufy-security-server has crashed."
                )
                raise BridgeCrashedError(self._crash_reason) from e
            return False

    def move_camera(self, camera_serial, direction, device_manager=None):
        """Move an Eufy camera with self-healing on bridge crash.

        If the bridge has crashed (ConnectionRefusedError on port 3000),
        this method will:
        1. Mark the bridge as dead
        2. Attempt one automatic restart
        3. Retry the PTZ command if restart succeeds
        4. Return a meaningful error message if recovery fails

        Direction correction is handled in the frontend via the
        'Rev. Pan' and 'Rev. Tilt' checkboxes (ptz-controller.js applyReversal).

        Returns:
            tuple: (success: bool, message: str)
                - success=True, message="Camera moved {direction}"
                - success=False, message="<detailed error explanation>"
        """
        print(f"[EUFY BRIDGE] move_camera called: serial={camera_serial}, direction={direction}")

        if not self.is_running():
            crash_info = self._crash_reason or "Bridge process is not running"
            print(f"[EUFY BRIDGE] Bridge not running: {crash_info}")

            # Attempt auto-restart
            print("[EUFY BRIDGE] Attempting auto-restart...")
            if self.restart():
                print("[EUFY BRIDGE] Auto-restart succeeded, retrying PTZ command")
            else:
                return (False, f"Eufy bridge crashed and auto-restart failed. "
                               f"Reason: {crash_info}. Visit /eufy-auth to re-authenticate.")

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(
                    self._execute_ptz_command(camera_serial, direction)
                )
                print(f"[EUFY BRIDGE] PTZ command result: {result}")
                if result:
                    return (True, f"Camera moved {direction}")
                else:
                    return (False, "PTZ command was sent but camera did not confirm movement")
            finally:
                loop.close()

        except BridgeCrashedError as e:
            # Server just crashed during this command — try one restart
            print(f"[EUFY BRIDGE] Bridge crashed during PTZ: {e}")
            if self.restart():
                # Restart succeeded — retry the command once
                print("[EUFY BRIDGE] Restart succeeded, retrying PTZ command...")
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        result = loop.run_until_complete(
                            self._execute_ptz_command(camera_serial, direction)
                        )
                        if result:
                            return (True, f"Camera moved {direction} (after bridge restart)")
                        else:
                            return (False, "PTZ command failed after bridge restart")
                    finally:
                        loop.close()
                except BridgeCrashedError:
                    return (False, "Eufy bridge crashed again after restart. "
                                   "Authentication may have expired. Visit /eufy-auth.")
                except Exception as retry_err:
                    return (False, f"PTZ retry failed after restart: {retry_err}")
            else:
                return (False, f"Eufy bridge crashed and auto-restart failed. "
                               f"Visit /eufy-auth to re-authenticate.")

        except Exception as e:
            print(f"[EUFY BRIDGE] Move camera error: {e}")
            logger.error(f"Move camera error: {e}")
            return (False, f"PTZ error: {e}")
            
    # =========================================================================
    # PTZ Preset Methods
    # =========================================================================

    async def _execute_preset_command(self, camera_serial, command, preset_index):
        """Execute PTZ preset command via WebSocket bridge.

        Args:
            camera_serial: Camera serial number (e.g., T8416P0023352DA9)
            command: One of 'goto', 'save', 'delete'
            preset_index: Preset slot (0-3)

        Returns:
            bool: True if command succeeded

        Raises:
            BridgeCrashedError: If WebSocket connection is refused.
        """
        print(f"[EUFY PRESET] Starting: serial={camera_serial}, cmd={command}, preset={preset_index}")

        if not self.is_ready():
            print(f"[EUFY PRESET] ERROR: Bridge not ready!")
            raise BridgeCrashedError("Bridge not ready — server may have crashed")

        if not (0 <= preset_index < self.PRESET_SLOTS):
            raise ValueError(f"Invalid preset index: {preset_index}. Must be 0-{self.PRESET_SLOTS - 1}")

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

                await ws.send(json.dumps({
                    "messageId": "schema",
                    "command": "set_api_schema",
                    "schemaVersion": 21
                }))
                schema_result = await self._wait_for_message(ws, "schema")
                print(f"[EUFY PRESET] Schema response: {schema_result}")

                await ws.send(json.dumps({
                    "messageId": "start",
                    "command": "start_listening"
                }))
                listen_result = await self._wait_for_message(ws, "start")
                print(f"[EUFY PRESET] Listen response: {listen_result}")

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

        except (ConnectionRefusedError, OSError) as e:
            self._mark_bridge_dead(
                f"Connection refused on port {self.port}: eufy-security-server has crashed."
            )
            raise BridgeCrashedError(self._crash_reason) from e

        except Exception as e:
            print(f"[EUFY PRESET] Exception: {e}")
            logger.error(f"Preset command error: {e}")
            if "Connect call failed" in str(e) or "Connection refused" in str(e):
                self._mark_bridge_dead(
                    f"Connection refused on port {self.port}: eufy-security-server has crashed."
                )
                raise BridgeCrashedError(self._crash_reason) from e
            return False

    def _run_bridge_command(self, command_name, async_fn, *args):
        """Helper: run an async bridge command with auto-restart on crash.

        Handles the common pattern:
            check running -> run async -> catch crash -> restart with backoff -> retry

        Retries up to MAX_AUTO_RESTARTS times with increasing wait between attempts
        (5s, 10s, ...). Each retry restarts the bridge first, then re-executes the command.

        Args:
            command_name: Human-readable name for logging (e.g., "goto_preset")
            async_fn: Async coroutine function to call
            *args: Arguments to pass to async_fn

        Returns:
            tuple: (success: bool, message: str)
        """
        print(f"[EUFY BRIDGE] {command_name} called")

        # Pre-flight: if bridge is down, try to restart before executing
        if not self.is_running():
            crash_info = self._crash_reason or "Bridge process is not running"
            print(f"[EUFY BRIDGE] Bridge not running for {command_name}: {crash_info}")
            if self.restart():
                print(f"[EUFY BRIDGE] Auto-restart succeeded for {command_name}")
            else:
                return (False, f"Eufy bridge crashed and auto-restart failed. "
                               f"Reason: {crash_info}. Visit /eufy-auth to re-authenticate.")

        # Execute the command
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(async_fn(*args))
                if result:
                    return (True, f"{command_name} succeeded")
                else:
                    return (False, f"{command_name} command was sent but not confirmed")
            finally:
                loop.close()

        except BridgeCrashedError:
            # Bridge crashed mid-command — retry with backoff
            print(f"[EUFY BRIDGE] Bridge crashed during {command_name}")
            for attempt in range(self.MAX_AUTO_RESTARTS):
                wait_time = (attempt + 1) * 5  # 5s, 10s
                print(f"[EUFY BRIDGE] Restart attempt {attempt + 1}/{self.MAX_AUTO_RESTARTS} "
                      f"(waiting {wait_time}s)")
                time.sleep(wait_time)

                if not self.restart():
                    print(f"[EUFY BRIDGE] Restart attempt {attempt + 1} failed")
                    continue

                # Restart succeeded — retry the command
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        result = loop.run_until_complete(async_fn(*args))
                        if result:
                            return (True, f"{command_name} succeeded "
                                         f"(after restart attempt {attempt + 1})")
                        else:
                            return (False, f"{command_name} failed after restart")
                    finally:
                        loop.close()
                except BridgeCrashedError:
                    print(f"[EUFY BRIDGE] Bridge crashed again on attempt {attempt + 1}")
                    continue
                except Exception as retry_err:
                    return (False, f"{command_name} retry failed: {retry_err}")

            # All retry attempts exhausted
            return (False, f"Eufy bridge crashed and {self.MAX_AUTO_RESTARTS} restart "
                           f"attempts failed. Visit /eufy-auth to re-authenticate.")

        except Exception as e:
            logger.error(f"{command_name} error: {e}")
            return (False, f"{command_name} error: {e}")

    def goto_preset(self, camera_serial, preset_index):
        """Move camera to a saved preset position.

        Args:
            camera_serial: Camera serial number
            preset_index: Preset slot (0-3)

        Returns:
            tuple: (success: bool, message: str)
        """
        return self._run_bridge_command(
            "goto_preset",
            self._execute_preset_command,
            camera_serial, 'goto', preset_index
        )

    def save_preset(self, camera_serial, preset_index):
        """Save current camera position as a preset.

        Args:
            camera_serial: Camera serial number
            preset_index: Preset slot (0-3)

        Returns:
            tuple: (success: bool, message: str)
        """
        return self._run_bridge_command(
            "save_preset",
            self._execute_preset_command,
            camera_serial, 'save', preset_index
        )

    def delete_preset(self, camera_serial, preset_index):
        """Delete a preset position.

        Args:
            camera_serial: Camera serial number
            preset_index: Preset slot (0-3)

        Returns:
            tuple: (success: bool, message: str)
        """
        return self._run_bridge_command(
            "delete_preset",
            self._execute_preset_command,
            camera_serial, 'delete', preset_index
        )

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
            # eufy-security-ws uses Buffer.from(message.buffer) which expects
            # an array of bytes, NOT a base64 string. We need to decode the
            # base64 and send as a list of integers (byte values).
            import base64
            audio_bytes = base64.b64decode(audio_data)
            byte_array = list(audio_bytes)  # Convert to list of ints for JSON

            await ws.send(json.dumps({
                "messageId": "talkback_audio",
                "command": "device.talkback_audio_data",
                "serialNumber": camera_serial,
                "buffer": byte_array  # Array of bytes, not base64 string
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