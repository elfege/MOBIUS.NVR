#!/usr/bin/env python3
"""
Camera Audio Capability Survey
==============================

For each camera in the DB, probe its streaming-hub RTSP URL with ffprobe
and record whether an audio stream is published. This populates the
``cameras.audio_input_supported`` and ``cameras.audio_input_probed_at``
columns added by migration 028.

After audio probing, this script also seeds ``evidence_camera_settings``
with one row per camera (all initially ``enabled=FALSE``) so the
"Collect Evidence" UI matrix has a complete view of every camera. Cameras
without audio support get ``capture_audio=FALSE`` to make it visually
clear in the UI that audio capture is not available for them.

This script is **idempotent** — re-running it:
- Re-probes audio capability and refreshes ``audio_input_probed_at``.
- Skips DB writes for ``audio_input_supported`` if ffprobe failed
  (transient errors must not downgrade a known TRUE to FALSE).
- Upserts ``evidence_camera_settings`` rows so existing settings (e.g.
  ``enabled=TRUE``) are not clobbered.

Usage (inside the unified-nvr container)::

    docker exec -it unified-nvr python /app/scripts/survey_camera_audio.py

Or from host with explicit ports::

    NVR_POSTGREST_URL=http://localhost:3001 \\
        venv/bin/python scripts/survey_camera_audio.py

Flags::

    --camera SERIAL    only probe one camera
    --timeout SECS     ffprobe per-camera timeout (default 10)
    --dry-run          probe but do not write DB changes
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import requests

# Add project root to path so we can import services.streaming_hub
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.streaming_hub import get_rtsp_source_url  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

POSTGREST_URL = os.getenv("NVR_POSTGREST_URL", "http://postgrest:3001")
DEFAULT_TIMEOUT = 10  # seconds


# ---------------------------------------------------------------------
# DB helpers (via PostgREST)
# ---------------------------------------------------------------------

def fetch_cameras(serial: Optional[str] = None) -> List[Dict]:
    """Fetch cameras from the DB, optionally filtered by serial."""
    url = f"{POSTGREST_URL}/cameras"
    params = {}
    if serial:
        params["serial"] = f"eq.{serial}"
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def update_camera_audio(serial: str, audio_supported: Optional[bool]) -> None:
    """Update the audio capability columns for one camera.

    If ``audio_supported`` is None, only ``audio_input_probed_at`` is
    refreshed — used when ffprobe failed and we should not write a
    boolean result.
    """
    url = f"{POSTGREST_URL}/cameras"
    params = {"serial": f"eq.{serial}"}
    payload: Dict[str, object] = {
        "audio_input_probed_at": datetime.now(timezone.utc).isoformat(),
    }
    if audio_supported is not None:
        payload["audio_input_supported"] = audio_supported
    r = requests.patch(url, params=params, json=payload,
                       headers={"Prefer": "return=minimal"}, timeout=10)
    r.raise_for_status()


def upsert_evidence_settings(serial: str, audio_supported: bool) -> None:
    """Insert a default evidence_camera_settings row if none exists.

    Uses ``Prefer: resolution=ignore-duplicates`` so re-running the script
    does not clobber per-camera settings the user has already changed.
    """
    url = f"{POSTGREST_URL}/evidence_camera_settings"
    payload = {
        "serial": serial,
        "enabled": False,
        "capture_video": True,
        "capture_audio": bool(audio_supported),  # off by default if no mic
    }
    r = requests.post(
        url,
        json=payload,
        headers={
            "Prefer": "resolution=ignore-duplicates,return=minimal",
        },
        timeout=10,
    )
    # 201 created or 409 conflict (ignored due to the Prefer header) are both ok
    if r.status_code not in (201, 200, 409):
        r.raise_for_status()


# ---------------------------------------------------------------------
# ffprobe
# ---------------------------------------------------------------------

def probe_audio(rtsp_url: str, timeout: int) -> Tuple[Optional[bool], str]:
    """Run ffprobe against ``rtsp_url`` and report whether an audio
    stream is present.

    Returns ``(has_audio, detail)``:
      - ``has_audio = True``  → at least one stream with codec_type=audio
      - ``has_audio = False`` → ffprobe ran cleanly, no audio stream
      - ``has_audio = None``  → ffprobe failed; do not update the DB
    """
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_streams",
        "-of", "json",
        "-rtsp_transport", "tcp",  # TCP for reliability over WiFi
        "-timeout", str(timeout * 1_000_000),  # microseconds
        rtsp_url,
    ]
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 2,  # subprocess timeout > ffprobe timeout
        )
    except subprocess.TimeoutExpired:
        return None, "ffprobe subprocess timed out"
    if out.returncode != 0:
        return None, f"ffprobe rc={out.returncode}: {out.stderr.strip()[:200]}"
    try:
        info = json.loads(out.stdout or "{}")
    except json.JSONDecodeError as e:
        return None, f"ffprobe returned non-JSON output: {e}"
    streams = info.get("streams", [])
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    if audio_streams:
        codecs = ",".join(s.get("codec_name", "?") for s in audio_streams)
        return True, f"audio stream(s) found: {codecs}"
    return False, f"no audio stream (found {len(streams)} non-audio stream(s))"


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--camera", help="probe only this serial")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                   help=f"per-camera ffprobe timeout in seconds (default {DEFAULT_TIMEOUT})")
    p.add_argument("--dry-run", action="store_true",
                   help="probe but do not write to the DB")
    args = p.parse_args()

    cameras = fetch_cameras(serial=args.camera)
    if not cameras:
        logger.error("no cameras found%s",
                     f" matching serial={args.camera}" if args.camera else "")
        return 1
    logger.info("probing %d camera(s) with timeout=%ds (dry_run=%s)",
                len(cameras), args.timeout, args.dry_run)

    yes_count = 0
    no_count = 0
    fail_count = 0

    for cam in cameras:
        serial = cam["serial"]
        name = cam.get("name") or serial
        url = get_rtsp_source_url(serial, cam)
        logger.info("[%s] (%s) probing %s", serial, name, url)

        has_audio, detail = probe_audio(url, args.timeout)

        if has_audio is None:
            logger.warning("[%s] PROBE FAILED: %s", serial, detail)
            fail_count += 1
            if not args.dry_run:
                # Refresh probed_at but keep audio_input_supported untouched
                update_camera_audio(serial, audio_supported=None)
            continue

        if has_audio:
            logger.info("[%s] HAS AUDIO: %s", serial, detail)
            yes_count += 1
        else:
            logger.info("[%s] NO AUDIO: %s", serial, detail)
            no_count += 1

        if not args.dry_run:
            update_camera_audio(serial, audio_supported=has_audio)
            upsert_evidence_settings(serial, audio_supported=has_audio)

    logger.info("=" * 60)
    logger.info("survey complete: %d with audio, %d without, %d failed",
                yes_count, no_count, fail_count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
