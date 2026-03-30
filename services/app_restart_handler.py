#!/usr/bin/env python3
# services/app_restart_handler.py
#
# Handles graceful application restart via two modes:
#   1. Full restart: POST to nvr-admin-console sidecar, which runs start.sh
#      (regenerates configs, docker compose down/up — full rebuild)
#   2. Container restart: os._exit(1), Docker restart policy restarts container only
#

import time
import os
import threading
import logging
import urllib.request
import json

logger = logging.getLogger(__name__)


class AppRestartHandler:
    """Handles graceful application restart."""

    # The admin console is a host systemd service (not a container).
    # NVR_LOCAL_HOST_IP is the host's LAN IP, injected via docker-compose env.
    ADMIN_PORT = os.environ.get('NVR_ADMIN_PORT', '9100')
    HOST_IP = os.environ.get('NVR_LOCAL_HOST_IP', '192.168.10.20')

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
        Full restart via admin console sidecar.

        Sends POST to nvr-admin-console:9100/restart, which runs start.sh
        on the host (via Docker socket). start.sh regenerates go2rtc.yaml,
        mediamtx.yml, then does docker compose down/up — rebuilding everything.
        The sidecar survives because it uses restart: always.
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

                # Call the admin console sidecar — fire and forget.
                # start.sh will docker compose down (killing this container),
                # then docker compose up (bringing it back).
                logger.info("[Restart] Calling admin console sidecar...")
                payload = json.dumps({'reason': reason}).encode()
                url = f'http://{self.HOST_IP}:{self.ADMIN_PORT}/restart'
                req = urllib.request.Request(
                    url,
                    data=payload,
                    headers={'Content-Type': 'application/json'},
                    method='POST'
                )
                try:
                    resp = urllib.request.urlopen(req, timeout=5)
                    logger.info(f"[Restart] Sidecar responded: {resp.read().decode()}")
                except Exception as e:
                    logger.error(f"[Restart] Sidecar call failed: {e} — falling back to container restart")
                    os._exit(1)

                # Sidecar will run start.sh which kills this container.
                # Nothing more to do here.
                logger.info("[Restart] Sidecar triggered. Waiting for container teardown...")

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
