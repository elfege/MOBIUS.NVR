"""
Evidence pipeline global master switch.

The evidence-collection pipeline (audio extractors, YAMNet classifier, Whisper
transcriber, manifest) is a BETA feature. Left unattended it taps every
audio-capable camera, writes clips to /litigation/intake, and runs always-on
ML — accumulating files and CPU for nothing when the operator isn't actively
using it.

This module is the single source of truth for whether the pipeline may run.
It reads one global setting from nvr_settings (key/value table, same store as
Settings.get_global) so the switch can be toggled at runtime without a restart:

    nvr_settings.key = 'evidence_collection_enabled'
    value in {'1','true','yes','on'}  -> enabled
    anything else / MISSING            -> DISABLED  (default-off, beta)

DEFAULT OFF is deliberate: a fresh deployment (no key) must not collect. The
operator opts in explicitly via the Eufy/Evidence settings UI, which writes the
key. Background services re-read this every poll cycle so flipping it on/off
takes effect without bouncing the container.
"""

import os
import logging

import requests

logger = logging.getLogger(__name__)

POSTGREST_URL = os.environ.get("NVR_POSTGREST_URL", "http://postgrest:3001")

_TRUTHY = {"1", "true", "yes", "on"}

# nvr_settings key holding the master switch.
EVIDENCE_ENABLED_KEY = "evidence_collection_enabled"


def evidence_collection_enabled(default: bool = False) -> bool:
    """Return True iff the evidence pipeline is globally enabled.

    Reads nvr_settings via PostgREST directly (background services don't carry
    a Settings instance). Any error (DB unreachable, etc.) falls back to
    ``default`` — which is False, so a transient DB hiccup fails SAFE (pipeline
    stays off) rather than silently resuming collection.

    Args:
        default: value returned when the key is missing or unreadable.
    """
    try:
        r = requests.get(
            f"{POSTGREST_URL}/nvr_settings",
            params={"key": f"eq.{EVIDENCE_ENABLED_KEY}", "select": "value"},
            timeout=5,
        )
        if r.ok:
            rows = r.json()
            if rows:
                return str(rows[0].get("value", "")).strip().lower() in _TRUTHY
    except Exception as e:
        logger.debug("evidence gate read failed (%s); defaulting to %s", e, default)
    return default
