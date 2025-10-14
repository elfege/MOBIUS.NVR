
#!/usr/bin/env python3
# services/app_restart_handler.py
#
# Stream Management Class for LL-HLS streaming
#

import time
import os
import threading

class AppRestartHandler:
    """Handles graceful application restart for resource monitoring"""

    def __init__(self, stream_manager, bridge_watchdog, eufy_bridge):
        self.stream_manager = stream_manager
        self.bridge_watchdog = bridge_watchdog
        self.eufy_bridge = eufy_bridge
        self.restart_requested = False

    def restart_app(self, reason):
        """Perform graceful application restart"""
        if self.restart_requested:
            print("Restart already in progress")
            return

        self.restart_requested = True
        print(f"🚨 Starting graceful application restart: {reason}")

        def restart_thread():
            try:
                # Stop all streams first

                try:
                    print("🛑 Stopping all streams...")
                    self.stream_manager.stop_all_streams()
                    self.stream_manager.emergency_cleanup()
                except:
                    pass

                try:
                    # Stop bridge services
                    print("🛑 Stopping bridge services...")
                    self.bridge_watchdog.stop()
                    self.eufy_bridge.stop()
                except:
                    pass

                # Wait a moment for cleanup
                time.sleep(5)

                # Exit - let process manager restart us
                print("✅ Graceful cleanup complete - exiting for restart")
                os._exit(1)

            except Exception as e:
                print(f"❌ Error during graceful restart: {e}")
                # Force exit anyway
                os._exit(1)

        # Run restart in separate thread to avoid blocking
        threading.Thread(target=restart_thread, daemon=True).start()
