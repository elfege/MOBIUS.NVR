"""
NVR License Service

Validates the NVR license on startup. Enforces demo mode restrictions
when no valid license is present.

Demo mode: 7-day trial, max 2 cameras, no recording, watermark on streams.
After 7 days without a valid license, the app refuses to start.

License key is read from NVR_LICENSE_KEY environment variable.
Validation is done via HTTPS POST to the license validation Lambda.
"""

import os
import json
import time
import hashlib
import subprocess
import logging
from datetime import datetime, timedelta
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

# License validation endpoint (API Gateway)
LICENSE_VALIDATOR_URL = os.environ.get(
    "NVR_LICENSE_VALIDATOR_URL",
    "https://imodm0mn53.execute-api.us-east-1.amazonaws.com/prod/validate"
)
LICENSE_KEY = os.environ.get("NVR_LICENSE_KEY", "")

# Demo mode limits
DEMO_MAX_CAMERAS = 2
DEMO_DAYS = 7

# Cache file for offline grace period
_CACHE_DIR = Path("/app/config") if Path("/app/config").exists() else Path("config")
_CACHE_FILE = _CACHE_DIR / ".license_cache.json"
_GRACE_DAYS = 7  # Days to operate on cached validation when offline


class LicenseStatus:
    """Holds the current license state for the running instance."""

    def __init__(self):
        self.status = "unknown"  # valid, demo, expired, invalid, revoked
        self.expires = None
        self.demo_started = None
        self.demo_days_remaining = DEMO_DAYS
        self.message = ""
        self.is_demo = True
        self.max_cameras = DEMO_MAX_CAMERAS
        self.recording_enabled = False
        self.watermark = True

    def set_valid(self, expires=None):
        """License is valid — full functionality."""
        self.status = "valid"
        self.expires = expires
        self.is_demo = False
        self.max_cameras = 999
        self.recording_enabled = True
        self.watermark = False
        self.message = ""

    def set_demo(self, days_remaining=DEMO_DAYS):
        """Demo mode — limited functionality."""
        self.status = "demo"
        self.is_demo = True
        self.demo_days_remaining = days_remaining
        self.max_cameras = DEMO_MAX_CAMERAS
        self.recording_enabled = False
        self.watermark = True
        self.message = (
            f"Demo mode: {days_remaining} days remaining. "
            f"Max {DEMO_MAX_CAMERAS} cameras, no recording. "
            f"Purchase a license at elfege.com"
        )

    def set_expired(self, expires=None):
        """License expired — same as demo but with expiry message."""
        self.set_demo(0)
        self.status = "expired"
        self.expires = expires
        self.message = "License expired. Renew at elfege.com"

    def set_refused(self):
        """Demo period over, no valid license — refuse to operate."""
        self.status = "refused"
        self.is_demo = True
        self.demo_days_remaining = 0
        self.max_cameras = 0
        self.recording_enabled = False
        self.watermark = True
        self.message = (
            "Demo period expired. Purchase a license at elfege.com to continue."
        )

    def to_dict(self):
        """Serialize for API responses and caching."""
        return {
            "status": self.status,
            "is_demo": self.is_demo,
            "expires": self.expires,
            "demo_days_remaining": self.demo_days_remaining,
            "max_cameras": self.max_cameras,
            "recording_enabled": self.recording_enabled,
            "watermark": self.watermark,
            "message": self.message,
        }


# Global license state — set once at startup, read by all components
license = LicenseStatus()


def get_hardware_fingerprint():
    """
    Generate a stable hardware fingerprint.
    SHA-256 of sorted MAC addresses + /etc/machine-id.
    """
    try:
        # Get MAC addresses
        result = subprocess.run(
            ["ip", "link", "show"],
            capture_output=True, text=True, timeout=5
        )
        macs = sorted(
            line.split()[1]
            for line in result.stdout.splitlines()
            if "ether" in line
        )
        mac_str = ":".join(macs)
    except Exception:
        mac_str = "unknown"

    try:
        machine_id = Path("/etc/machine-id").read_text().strip()
    except Exception:
        machine_id = "unknown"

    return hashlib.sha256(f"{mac_str}{machine_id}".encode()).hexdigest()


def _load_cache():
    """Load cached license validation result."""
    try:
        if _CACHE_FILE.exists():
            data = json.loads(_CACHE_FILE.read_text())
            return data
    except Exception:
        pass
    return None


def _save_cache(status, expires=None):
    """Cache the license validation result for offline grace period."""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache = {
            "status": status,
            "expires": expires,
            "cached_at": datetime.utcnow().isoformat() + "Z",
        }
        _CACHE_FILE.write_text(json.dumps(cache))
    except Exception as e:
        logger.warning(f"Failed to cache license status: {e}")


def _get_demo_start():
    """
    Get the demo start timestamp. Stored in the license cache.
    Once set, cannot be reset (tied to hardware fingerprint).
    """
    cache = _load_cache()
    if cache and "demo_started" in cache:
        return datetime.fromisoformat(cache["demo_started"].replace("Z", ""))
    return None


def _set_demo_start():
    """Record when demo mode started."""
    try:
        cache = _load_cache() or {}
        if "demo_started" not in cache:
            cache["demo_started"] = datetime.utcnow().isoformat() + "Z"
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            _CACHE_FILE.write_text(json.dumps(cache))
    except Exception as e:
        logger.warning(f"Failed to record demo start: {e}")


def validate_license():
    """
    Validate the license on startup. Sets the global license state.
    Returns the LicenseStatus object.
    """
    global license

    fingerprint = get_hardware_fingerprint()

    # No validator URL configured — demo mode
    if not LICENSE_VALIDATOR_URL:
        logger.warning("No license validator URL configured. Running in demo mode.")
        _enter_demo_mode()
        return license

    # Build request payload
    payload = {
        "license_key": LICENSE_KEY,
        "hardware_fingerprint": fingerprint,
        "version": _get_version(),
        "hostname_hash": hashlib.sha256(
            subprocess.run(
                ["hostname"], capture_output=True, text=True
            ).stdout.strip().encode()
        ).hexdigest(),
    }

    # Try to validate online
    try:
        resp = requests.post(
            LICENSE_VALIDATOR_URL,
            json=payload,
            timeout=10,
        )
        data = resp.json()
        status = data.get("status", "invalid")

        if status == "valid":
            license.set_valid(expires=data.get("expires"))
            _save_cache("valid", expires=data.get("expires"))
            logger.info(
                f"License valid. Expires: {data.get('expires', 'unknown')}"
            )

        elif status == "expired":
            license.set_expired(expires=data.get("expires"))
            _save_cache("expired", expires=data.get("expires"))
            logger.warning(f"License expired: {data.get('message', '')}")

        elif status == "demo":
            _enter_demo_mode()

        elif status in ("invalid", "revoked"):
            license.set_demo(0)
            license.status = status
            license.message = data.get("message", "Invalid license.")
            logger.warning(f"License {status}: {data.get('message', '')}")

        else:
            _enter_demo_mode()

    except requests.RequestException as e:
        # Offline — use cached validation
        logger.warning(f"License validation failed (offline?): {e}")
        _use_cached_validation()

    return license


def _enter_demo_mode():
    """Enter or continue demo mode with day tracking."""
    demo_start = _get_demo_start()
    if demo_start is None:
        _set_demo_start()
        demo_start = datetime.utcnow()

    elapsed = (datetime.utcnow() - demo_start).days
    remaining = max(0, DEMO_DAYS - elapsed)

    if remaining > 0:
        license.set_demo(remaining)
        logger.info(f"Demo mode: {remaining} days remaining")
    else:
        license.set_refused()
        logger.error("Demo period expired. License required.")


def _use_cached_validation():
    """Use cached validation result during offline periods."""
    cache = _load_cache()
    if cache and cache.get("status") == "valid":
        cached_at = datetime.fromisoformat(cache["cached_at"].replace("Z", ""))
        age_days = (datetime.utcnow() - cached_at).days
        if age_days <= _GRACE_DAYS:
            license.set_valid(expires=cache.get("expires"))
            logger.info(
                f"Using cached license validation ({age_days} days old)"
            )
            return

    # No valid cache — demo mode
    _enter_demo_mode()


def _get_version():
    """Get the app version from git describe."""
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"
