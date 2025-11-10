# Add to app.py after manager initialization

import threading
import time
import logging
import subprocess  # Added missing import
import socket      # Added missing import

logger = logging.getLogger(__name__)

"""
Bounded counter: Stops at max attempts
Proper cleanup: Kills zombie processes and verifies port cleanup
Counter reset: Resets to 0 on successful restart
Cooldown periods: Prevents rapid restart loops
Port verification: Ensures clean restart environment
"""

class BridgeWatchdog:
    def __init__(self, bridge):
        self.bridge = bridge
        self.monitoring = False
        self.monitor_thread = None
        self.restart_attempts = 0
        self.max_restart_attempts = 5
        self.check_interval = 10  # seconds
        self.restart_cooldown = 30  # seconds between restart attempts
        
    def start_monitoring(self):
        if not self.monitoring:
            self.monitoring = True
            self.restart_attempts = 0  # Reset counter when starting
            self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self.monitor_thread.start()
            
    def stop(self):
        self.monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
            
    def _monitor_loop(self):
        # CRITICAL: Give bridge time to start before first check
        logger.info("Watchdog waiting 15 seconds for bridge startup...")
        time.sleep(120)
        logger.info("Watchdog monitoring started")
        
        while self.monitoring:
            try:
                if not self.bridge.is_running() or not self.bridge.is_ready():
                    self._handle_bridge_failure()
                time.sleep(self.check_interval)
            except Exception as e:
                logger.error(f"Watchdog monitor error: {e}")
                time.sleep(self.check_interval)
                
    def _handle_bridge_failure(self):
        if self.restart_attempts >= self.max_restart_attempts:
            logger.critical(f"Bridge failed {self.max_restart_attempts} times, giving up")
            self.monitoring = False
            return
            
        self.restart_attempts += 1
        logger.warning(f"Bridge not running, attempting restart {self.restart_attempts}/{self.max_restart_attempts}")
        
        # Proper cleanup before restart
        self._force_cleanup_bridge()
        
        # Wait for cooldown
        time.sleep(self.restart_cooldown)
        
        # Attempt restart
        if self.bridge.start():
            # Wait and verify restart was successful
            time.sleep(5)
            if self.bridge.is_ready():
                logger.info("Bridge restart successful, resetting counter")
                self.restart_attempts = 0  # Reset on successful restart
            else:
                logger.warning("Bridge started but not ready")
        else:
            logger.error(f"Bridge restart attempt {self.restart_attempts} failed")
            
    def _force_cleanup_bridge(self):
        """Force cleanup of zombie bridge processes"""
        try:
            # Stop via bridge object
            self.bridge.stop()
            
            # Force kill any remaining processes
            subprocess.run(['pkill', '-f', 'eufy-security-server'], 
                          stderr=subprocess.DEVNULL)
            
            # Verify port 3000 is freed
            bridge_port = getattr(self.bridge, 'port', 3000)
            for attempt in range(5):
                if self._is_port_free(bridge_port):
                    logger.info(f"Port {bridge_port} freed")
                    break
                logger.warning(f"Port {bridge_port} still in use, waiting...")
                time.sleep(1)
                
        except Exception as e:
            logger.error(f"Force cleanup error: {e}")
            
    def _is_port_free(self, port):
        """Check if port is available"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', port))
            sock.close()
            return result != 0  # Port is free if connection fails
        except:
            return True