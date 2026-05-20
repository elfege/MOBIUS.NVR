"""
services/go2rtc_snapshot_service.py — singleton-per-camera snapshot tap on go2rtc.

ARCHITECTURE: every snapshot consumer for a camera whose `streaming_hub`
is `go2rtc` reads from THIS service. The service runs ONE background
poller per camera that hits go2rtc's `/api/frame.jpeg?src=<stream>`
endpoint at a fixed cadence. go2rtc itself is the single consumer of
the physical camera; sampling its in-memory stream costs nothing
additional on the camera side.

This file exists to honour CLAUDE.md RULE 11 (Single-Consumer Policy):
the previous code path for go2rtc-hub cameras fell through to the
per-vendor `*_mjpeg_capture_service` modules which opened a SECOND
connection to the camera (direct HTTP CGI for Reolink etc.). That
violated the 1-camera-1-output law. The fix is this module + the
hub-based dispatch in routes/streaming.py.

API parity with mediaserver_mjpeg_service so api_snap_camera can swap
between the two on `streaming_hub` alone:

    add_client(camera_id, camera_config) -> bool
    get_latest_frame(camera_id) -> Optional[{'data': bytes, 'timestamp': float}]
    remove_client(camera_id) -> None   # decrement; if zero, stop poller
    get_status(camera_id) -> Optional[dict]
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)


# Cadence + freshness. 1Hz keeps go2rtc-side load trivial; 5s freshness
# matches mediaserver_mjpeg_service so the api_snap_camera retry logic
# behaves the same regardless of hub.
_POLL_INTERVAL_S = 1.0
_FRAME_MAX_AGE_S = 5.0

# Configurable so tests / non-default deployments can override without
# editing this module.
_GO2RTC_HOST = os.getenv("NVR_GO2RTC_HOST", "nvr-go2rtc")
_GO2RTC_PORT = int(os.getenv("NVR_GO2RTC_PORT", "1984"))


class Go2RTCSnapshotService:
    """One poller thread per camera. All clients of `/api/snap/<id>`
    read from the same in-memory frame buffer this thread populates."""

    def __init__(self, host: str = _GO2RTC_HOST, port: int = _GO2RTC_PORT) -> None:
        self.base_url = f"http://{host}:{port}"
        self.lock = threading.RLock()
        self.frame_buffers: Dict[str, dict] = {}     # camera_id -> {'data', 'timestamp'}
        self.active_captures: Dict[str, dict] = {}   # camera_id -> {'thread', 'stop_event', 'start_time'}
        self.client_counts: Dict[str, int] = {}      # ref-count so multiple callers don't fight

    # ── Public API ───────────────────────────────────────────────────

    def add_client(self, camera_id: str, camera_config: dict) -> bool:
        """Idempotent: starts the per-camera poller on first call,
        increments the ref-count on subsequent calls. Returns True if a
        new poller was started (mostly for logging), False if one was
        already running."""
        with self.lock:
            self.client_counts[camera_id] = self.client_counts.get(camera_id, 0) + 1
            if camera_id in self.active_captures:
                return False  # already running
            stream_name = self._stream_name_for(camera_id, camera_config)
            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._poll_loop,
                args=(camera_id, stream_name, stop_event),
                name=f"go2rtc-snap-{camera_id}",
                daemon=True,
            )
            self.active_captures[camera_id] = {
                "thread": thread,
                "stop_event": stop_event,
                "stream_name": stream_name,
                "start_time": time.time(),
            }
            thread.start()
            logger.info(
                "go2rtc snapshot poller started: camera=%s stream=%s url=%s",
                camera_id, stream_name, self._frame_url(stream_name),
            )
            return True

    def get_latest_frame(self, camera_id: str) -> Optional[dict]:
        """Returns the most recent frame for the camera, or None if no
        frame is available within the freshness window."""
        with self.lock:
            frame = self.frame_buffers.get(camera_id)
            if not frame:
                return None
            if (time.time() - frame["timestamp"]) > _FRAME_MAX_AGE_S:
                return None
            return frame

    def remove_client(self, camera_id: str) -> None:
        """Decrement the ref-count. When it hits zero, stop the poller.
        Snapshot consumers are mostly long-lived in practice; this is
        here for hygiene rather than reachable in normal flow."""
        with self.lock:
            n = self.client_counts.get(camera_id, 0) - 1
            if n > 0:
                self.client_counts[camera_id] = n
                return
            self.client_counts.pop(camera_id, None)
            capture = self.active_captures.pop(camera_id, None)
            self.frame_buffers.pop(camera_id, None)
        if capture:
            capture["stop_event"].set()
            logger.info("go2rtc snapshot poller stopped for %s", camera_id)

    def get_status(self, camera_id: str) -> Optional[dict]:
        with self.lock:
            cap = self.active_captures.get(camera_id)
            if not cap:
                return None
            frame = self.frame_buffers.get(camera_id, {})
            return {
                "camera_id": camera_id,
                "stream_name": cap["stream_name"],
                "active": True,
                "clients": self.client_counts.get(camera_id, 0),
                "uptime": time.time() - cap["start_time"],
                "last_frame_age": (time.time() - frame["timestamp"]) if frame else None,
                "frame_bytes": len(frame["data"]) if frame else 0,
            }

    # ── Internal ─────────────────────────────────────────────────────

    @staticmethod
    def _stream_name_for(camera_id: str, camera_config: dict) -> str:
        """go2rtc indexes streams by name. In this deployment the name
        is the camera serial (verified 2026-05-20 via
        GET /api/streams). camera_config is reserved as an override
        knob for future deployments that name streams differently."""
        return (camera_config or {}).get("go2rtc_stream_name") or camera_id

    def _frame_url(self, stream_name: str) -> str:
        return f"{self.base_url}/api/frame.jpeg?src={stream_name}"

    def _poll_loop(self, camera_id: str, stream_name: str, stop_event: threading.Event) -> None:
        """Background loop. Sleeps _POLL_INTERVAL_S between fetches;
        a single transient go2rtc hiccup just means the next poll tries
        again. Best-effort throughout — exceptions are caught + logged
        at debug; the loop never propagates them."""
        url = self._frame_url(stream_name)
        consecutive_failures = 0
        while not stop_event.is_set():
            try:
                resp = requests.get(url, timeout=3.0)
                if resp.status_code == 200 and resp.content:
                    with self.lock:
                        self.frame_buffers[camera_id] = {
                            "data": resp.content,
                            "timestamp": time.time(),
                        }
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    if consecutive_failures in (1, 5, 30):
                        # Log at 1st / 5th / 30th to surface flaps
                        # without spamming once a camera is reliably
                        # gone (steady-state silence).
                        logger.warning(
                            "go2rtc snapshot HTTP %s for %s (%s): %s",
                            resp.status_code, camera_id, url,
                            (resp.text or "")[:120],
                        )
            except requests.RequestException as e:
                consecutive_failures += 1
                if consecutive_failures in (1, 5, 30):
                    logger.warning(
                        "go2rtc snapshot fetch failed for %s (%s): %s",
                        camera_id, url, e,
                    )
            except Exception:
                logger.exception("go2rtc snapshot poller unexpected error for %s", camera_id)
            stop_event.wait(_POLL_INTERVAL_S)


# Module-level singleton, mirrors the mediaserver_mjpeg_service pattern.
go2rtc_snapshot_service = Go2RTCSnapshotService()
