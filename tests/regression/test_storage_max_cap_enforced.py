"""
Regression: the Storage "max storage" byte caps must actually be enforced,
and 0 must mean unlimited.

Operator report 2026-06-27: the Storage tab tooltip "Set max storage to 0 for
unlimited (uses % threshold only)" was a lie. `max_recent_storage_mb` /
`max_archive_storage_mb` were written by the form and saved by routes/storage.py
but read by NOTHING — capacity-based migration/cleanup triggered only on the
percent-free threshold. So:

  * setting a non-zero cap did nothing (storage was never capped at it), and
  * "0 = unlimited, % threshold only" was vacuously "true" only because every
    value behaved as unlimited.

The fix wires the caps into StorageMigrationService.check_capacity_trigger — the
single gate both migration ('recent') and cleanup ('archive') flow through — and
adds RecordingConfig.get_max_recent_storage_mb / get_max_archive_storage_mb.

These tests pin the contract so it cannot silently rot again:
  1. cap == 0  -> byte cap DISABLED (no trigger from usage alone; % only)
  2. cap == 0  -> the % threshold still triggers normally
  3. cap  > 0 and used > cap  -> triggers even when free % is healthy
  4. cap  > 0 and used <= cap -> no byte-trigger when free % is healthy
  5. the archive tier honors its own cap
  6. config defaults expose the caps as 0 (unlimited)
"""

import os
import sys

import pytest

# storage_migration.py does `from recording_config_loader import RecordingConfig`
# (a top-level import). recording_config_loader lives in config/, which the app
# puts on sys.path at boot but pytest's conftest only adds the repo root. Add
# config/ here so the import resolves the same way it does in production.
_CONFIG_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "config")
)
if _CONFIG_DIR not in sys.path:
    sys.path.insert(0, _CONFIG_DIR)

from services.recording.storage_migration import StorageMigrationService

MB = 1024 * 1024


class _FakeConfig:
    """Minimal stand-in exposing only the getters check_capacity_trigger reads."""

    def __init__(self, min_free=20, max_recent_mb=0, max_archive_mb=0):
        self._min_free = min_free
        self._max_recent = max_recent_mb
        self._max_archive = max_archive_mb

    def get_min_free_space_percent(self):
        return self._min_free

    def get_max_recent_storage_mb(self):
        return self._max_recent

    def get_max_archive_storage_mb(self):
        return self._max_archive


def _make_service(tmp_path, *, min_free=20, max_recent_mb=0, max_archive_mb=0,
                  free_percent=50.0, used_bytes=0):
    """Build a service without __init__ I/O, with a fake config and a stubbed
    get_disk_usage returning controlled numbers."""
    svc = StorageMigrationService.__new__(StorageMigrationService)
    svc.config = _FakeConfig(min_free, max_recent_mb, max_archive_mb)
    svc.recent_base = tmp_path / "recent"
    svc.archive_base = tmp_path / "archive"
    (svc.archive_base / "motion").mkdir(parents=True, exist_ok=True)
    # Shadow the bound method with controlled usage numbers.
    svc.get_disk_usage = lambda path: {
        "free_percent": free_percent,
        "used_bytes": used_bytes,
    }
    return svc


def test_cap_zero_is_unlimited_no_trigger_from_usage(tmp_path):
    """cap == 0 → byte cap disabled: even 10 TB used must NOT trigger when free% is fine."""
    svc = _make_service(
        tmp_path, max_recent_mb=0, free_percent=50.0, used_bytes=10_000_000 * MB
    )
    needs_action, free_pct = svc.check_capacity_trigger("recent")
    assert needs_action is False, "max=0 must be unlimited — usage alone must not trigger"
    assert free_pct == 50.0


def test_cap_zero_still_honors_percent_threshold(tmp_path):
    """cap == 0 → the % free threshold must still trigger (the 'uses % threshold only' promise)."""
    svc = _make_service(
        tmp_path, min_free=20, max_recent_mb=0, free_percent=10.0, used_bytes=0
    )
    needs_action, _ = svc.check_capacity_trigger("recent")
    assert needs_action is True, "below min_free% must trigger even with cap disabled"


def test_cap_triggers_when_used_exceeds_cap(tmp_path):
    """cap > 0 and used > cap → must trigger even when free % is healthy."""
    svc = _make_service(
        tmp_path, min_free=20, max_recent_mb=1000, free_percent=80.0, used_bytes=2000 * MB
    )
    needs_action, _ = svc.check_capacity_trigger("recent")
    assert needs_action is True, "used (2000MB) over cap (1000MB) must trigger despite healthy free%"


def test_cap_no_trigger_when_under_cap_and_healthy(tmp_path):
    """cap > 0 and used <= cap and free% healthy → no trigger."""
    svc = _make_service(
        tmp_path, min_free=20, max_recent_mb=1000, free_percent=80.0, used_bytes=500 * MB
    )
    needs_action, _ = svc.check_capacity_trigger("recent")
    assert needs_action is False, "used (500MB) under cap (1000MB) with healthy free% must not trigger"


def test_archive_tier_honors_its_own_cap(tmp_path):
    """The archive tier must enforce max_archive_storage_mb (not the recent cap)."""
    svc = _make_service(
        tmp_path, min_free=20, max_recent_mb=0, max_archive_mb=1000,
        free_percent=80.0, used_bytes=2000 * MB,
    )
    needs_action, _ = svc.check_capacity_trigger("archive")
    assert needs_action is True, "archive used over its cap must trigger cleanup"


def test_config_defaults_expose_caps_as_unlimited(tmp_path):
    """RecordingConfig must default both caps to 0 (unlimited) and surface the getters."""
    from recording_config_loader import RecordingConfig

    cfg = RecordingConfig(str(tmp_path / "nonexistent_recording_settings.json"))
    assert cfg.get_max_recent_storage_mb() == 0
    assert cfg.get_max_archive_storage_mb() == 0
    mig = cfg.get_migration_config()
    assert mig.get("max_recent_storage_mb") == 0
    assert mig.get("max_archive_storage_mb") == 0
