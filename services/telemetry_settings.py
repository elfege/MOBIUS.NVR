"""
services/telemetry_settings.py — typed wrapper around nvr_settings for the
per-layer telemetry event log feature.

The three keys live in nvr_settings (key/value table, audit-triggered):

    telemetry_enabled        — 'true' | 'false'   (default 'false')
    telemetry_max_size_mb    — integer string     (default '100')
    telemetry_retention_days — integer string     (default '7')

This module exists so the rest of the codebase never has to know about the
key names or do string-to-bool/int parsing. Everything that touches the
telemetry feature flag goes through is_enabled(), max_size_mb(), and
retention_days() below.

Defaults match the migration's INSERT — if the row were ever missing (it
shouldn't be), we still return safe values rather than throwing.

Constants are exposed at module level so other code can write to them via
shared.settings.set_global(TELEMETRY_ENABLED_KEY, 'true') if needed for
admin-flow toggles, but the recommended path is set_*() helpers in this
file, which validate the value before writing.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

TELEMETRY_ENABLED_KEY        = 'telemetry_enabled'
TELEMETRY_MAX_SIZE_MB_KEY    = 'telemetry_max_size_mb'
TELEMETRY_RETENTION_DAYS_KEY = 'telemetry_retention_days'

DEFAULT_ENABLED        = False
DEFAULT_MAX_SIZE_MB    = 100
DEFAULT_RETENTION_DAYS = 7

MIN_MAX_SIZE_MB        = 10
MAX_MAX_SIZE_MB        = 2048
ALLOWED_RETENTION_DAYS = (1, 7, 30)


def _settings_service():
    """Lazy import to avoid circular dependency at module load."""
    from routes import shared
    return shared.settings


def is_enabled() -> bool:
    """True if admin has flipped telemetry on. Defaults to False on any error."""
    svc = _settings_service()
    if svc is None:
        return DEFAULT_ENABLED
    try:
        raw = svc.get_global(TELEMETRY_ENABLED_KEY, default=str(DEFAULT_ENABLED).lower())
        return str(raw).strip().lower() == 'true'
    except Exception as e:
        logger.warning(f"[telemetry] is_enabled() failed, defaulting to disabled: {e}")
        return DEFAULT_ENABLED


def max_size_mb() -> int:
    """Admin-set max size for the telemetry_events table, in MB."""
    svc = _settings_service()
    if svc is None:
        return DEFAULT_MAX_SIZE_MB
    try:
        raw = svc.get_global(TELEMETRY_MAX_SIZE_MB_KEY, default=str(DEFAULT_MAX_SIZE_MB))
        v = int(raw)
        return max(MIN_MAX_SIZE_MB, min(MAX_MAX_SIZE_MB, v))
    except Exception as e:
        logger.warning(f"[telemetry] max_size_mb() failed, defaulting to {DEFAULT_MAX_SIZE_MB}: {e}")
        return DEFAULT_MAX_SIZE_MB


def retention_days() -> int:
    """Admin-set retention window in days. Constrained to ALLOWED_RETENTION_DAYS."""
    svc = _settings_service()
    if svc is None:
        return DEFAULT_RETENTION_DAYS
    try:
        raw = svc.get_global(TELEMETRY_RETENTION_DAYS_KEY, default=str(DEFAULT_RETENTION_DAYS))
        v = int(raw)
        return v if v in ALLOWED_RETENTION_DAYS else DEFAULT_RETENTION_DAYS
    except Exception as e:
        logger.warning(f"[telemetry] retention_days() failed, defaulting to {DEFAULT_RETENTION_DAYS}: {e}")
        return DEFAULT_RETENTION_DAYS


def set_enabled(enabled: bool) -> bool:
    """Persist the on/off flag. Caller is responsible for admin gating."""
    svc = _settings_service()
    if svc is None:
        return False
    return svc.set_global(TELEMETRY_ENABLED_KEY, 'true' if enabled else 'false')


def set_max_size_mb(value: int) -> bool:
    """Persist max-size cap. Value is clamped to the allowed band before write."""
    svc = _settings_service()
    if svc is None:
        return False
    v = max(MIN_MAX_SIZE_MB, min(MAX_MAX_SIZE_MB, int(value)))
    return svc.set_global(TELEMETRY_MAX_SIZE_MB_KEY, str(v))


def set_retention_days(value: int) -> bool:
    """Persist retention window. Value must be in ALLOWED_RETENTION_DAYS, else rejected."""
    if int(value) not in ALLOWED_RETENTION_DAYS:
        return False
    svc = _settings_service()
    if svc is None:
        return False
    return svc.set_global(TELEMETRY_RETENTION_DAYS_KEY, str(int(value)))


def snapshot() -> dict:
    """Return the current config as a JSON-serializable dict for the UI."""
    return {
        'enabled':        is_enabled(),
        'max_size_mb':    max_size_mb(),
        'retention_days': retention_days(),
        'allowed_retention_days': list(ALLOWED_RETENTION_DAYS),
        'min_max_size_mb': MIN_MAX_SIZE_MB,
        'max_max_size_mb': MAX_MAX_SIZE_MB,
    }
