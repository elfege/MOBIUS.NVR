#!/usr/bin/env python3
# services/app_restart_handler.py
#
# Handles graceful application restart via two modes:
#   1. Full restart: SSH to host to run start.sh
#      (regenerates configs, docker compose down/up — full rebuild)
#   2. Container restart: os._exit(1), Docker restart policy restarts container only
#
# Inspired by OHVD_APP_PROD's Express admin container approach:
# container SSHes to host to execute scripts, no sidecar needed.
#

import time
import os
import subprocess
import threading
import logging

logger = logging.getLogger(__name__)


class AppRestartHandler:
    """Handles graceful application restart."""

    TRIGGER_FILE = '/dev/shm/nvr-restart/trigger'

    def __init__(self, stream_manager, bridge_watchdog, eufy_bridge):
        self.stream_manager = stream_manager
        self.bridge_watchdog = bridge_watchdog
        self.eufy_bridge = eufy_bridge
        self.restart_requested = False

    def _graceful_stop(self):
        """Stop all streams and bridges before restart."""
        try:
            logger.info("[Restart] Stopping all streams...")
            self.stream_manager.stop_all_streams()
            self.stream_manager.emergency_cleanup()
        except Exception:
            pass

        try:
            logger.info("[Restart] Stopping bridge services...")
            if self.bridge_watchdog:
                self.bridge_watchdog.stop()
            if self.eufy_bridge:
                self.eufy_bridge.stop()
        except Exception:
            pass

    def restart_full(self, reason):
        """
        Full restart via trigger file.

        Writes "reboot" to /dev/shm/nvr-restart/trigger. The host-side
        nvr-restart-watcher systemd service picks it up and runs start.sh,
        which does docker compose down/up — full rebuild.
        """
        if self.restart_requested:
            logger.warning("[Restart] Already in progress")
            return False

        self.restart_requested = True
        logger.info(f"[Restart] Full restart requested: {reason}")

        def restart_thread():
            try:
                self._graceful_stop()
                time.sleep(1)

                # Write trigger file — host watcher will pick it up and run start.sh
                logger.info("[Restart] Writing reboot trigger...")
                try:
                    with open(self.TRIGGER_FILE, 'w') as f:
                        f.write('reboot')
                    logger.info("[Restart] Trigger written. Host watcher will run start.sh.")
                except Exception as e:
                    logger.error(f"[Restart] Failed to write trigger file: {e} — falling back to container restart")
                    os._exit(1)

            except Exception as e:
                logger.error(f"[Restart] Failed: {e} — falling back to container restart")
                os._exit(1)

        threading.Thread(target=restart_thread, daemon=True, name="full-restart").start()
        return True

    def restart_app(self, reason):
        """
        Container-only restart: graceful stop then os._exit(1).
        Docker restart policy will restart the container.
        Does NOT regenerate go2rtc.yaml or mediamtx.yml.
        """
        if self.restart_requested:
            logger.warning("[Restart] Already in progress")
            return

        self.restart_requested = True
        logger.info(f"[Restart] Container restart requested: {reason}")

        def restart_thread():
            try:
                self._graceful_stop()
                time.sleep(5)
                logger.info("[Restart] Graceful cleanup complete — exiting for container restart")
                os._exit(1)
            except Exception as e:
                logger.error(f"[Restart] Error during restart: {e}")
                os._exit(1)

        threading.Thread(target=restart_thread, daemon=True, name="container-restart").start()
