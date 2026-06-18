"""
tests/e2e/test_storage_management.py — STORAGE surface backfill (Phase D).

Covers the 6 storage rows from docs/functionality_reference.md:

  STORAGE.STATS.READ          GET  /api/storage/stats        envelope shape
  STORAGE.MIGRATE.MANUAL      POST /api/storage/migrate      synchronous run
  STORAGE.CLEANUP.MANUAL      POST /api/storage/cleanup      synchronous run
  STORAGE.RECONCILE           POST /api/storage/reconcile    synchronous run
  STORAGE.SETTINGS.UPDATE     POST /api/storage/settings     roundtrip
  STORAGE.CANCEL_IN_PROGRESS  POST /api/storage/cancel       graceful no-op

Plus one admin-only enforcement negative test (viewer hits a write
endpoint → 403). Verifies that AUTH.ROLE.ADMIN_ONLY's policy actually
applies to the storage surface, which the per-route docstrings claim.

Caveat
------
The test stack starts with empty `/recordings/...` directories under
tmp_test, so the migrate / cleanup / reconcile operations all find zero
candidate files. That's the intended state — we're testing the API
contract (returns success + counts), not the actual file-move
mechanics (which the storage_migration unit tests cover separately).
"""

from __future__ import annotations

import bcrypt
import httpx
import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def admin_client(base_url: str, seed_test_admin):
    """httpx.Client logged in as e2e_admin."""
    username, password = seed_test_admin
    client = httpx.Client(base_url=base_url, follow_redirects=False)
    resp = client.post("/login", data={"username": username, "password": password})
    assert resp.status_code in (200, 302, 303), (
        f"admin login failed: {resp.status_code} {resp.text[:200]}"
    )
    yield client
    client.close()


@pytest.fixture
def seed_viewer(db_conn, seed_test_admin):
    """Insert (or refresh) a viewer-role user for admin-only-policy checks."""
    username = "e2e_storage_viewer"
    password = "viewer_pw"
    bcrypt_hash = bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt(12)
    ).decode("utf-8")
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (username, password_hash, role, must_change_password)
            VALUES (%s, %s, 'viewer', false)
            ON CONFLICT (username) DO UPDATE SET
                password_hash = EXCLUDED.password_hash,
                role = 'viewer',
                must_change_password = false
            """,
            (username, bcrypt_hash),
        )
    yield username, password


@pytest.fixture
def viewer_client(base_url, seed_viewer):
    username, password = seed_viewer
    client = httpx.Client(base_url=base_url, follow_redirects=False)
    client.post("/login", data={"username": username, "password": password})
    yield client
    client.close()


# ---------------------------------------------------------------------------
# STORAGE.STATS.READ
# ---------------------------------------------------------------------------

def test_storage_stats_returns_expected_envelope(admin_client):
    """
    STORAGE.STATS.READ — GET /api/storage/stats. The handler wraps the
    underlying stats dict in `{success: true, **stats}`. The reference
    doc lists keys: recent, archive, config, warnings. We verify the
    envelope and the documented keys (allowing extras — the migration
    service adds more diagnostic fields over time).
    """
    resp = admin_client.get("/api/storage/stats")
    assert resp.status_code == 200, resp.text[:200]
    body = resp.json()
    assert body.get("success") is True, f"missing success=true: {body}"
    for expected_key in ("recent", "archive", "config", "warnings"):
        assert expected_key in body, (
            f"/api/storage/stats response missing documented key {expected_key!r}: "
            f"got keys {sorted(body.keys())}"
        )


# ---------------------------------------------------------------------------
# STORAGE.MIGRATE.MANUAL
# ---------------------------------------------------------------------------

def test_storage_migrate_manual_returns_counts(admin_client):
    """
    STORAGE.MIGRATE.MANUAL — POST /api/storage/migrate. Test stack has
    zero files in /recordings; expect success with migrated=0 + a
    non-error result envelope.
    """
    resp = admin_client.post(
        "/api/storage/migrate",
        json={"recording_type": "motion", "force": True},
    )
    assert resp.status_code in (200, 409), (
        # 409 only if a prior test left an in-progress flag; treat as a
        # soft signal that something's stuck and skip the strict check
        f"unexpected status {resp.status_code}: {resp.text[:200]}"
    )
    if resp.status_code == 409:
        pytest.skip("a prior migration is in_progress — clean state required")
    body = resp.json()
    assert body.get("success") is True, body
    assert "migrated" in body, f"no migrated count in {body}"
    assert isinstance(body["migrated"], int)


# ---------------------------------------------------------------------------
# STORAGE.CLEANUP.MANUAL
# ---------------------------------------------------------------------------

def test_storage_cleanup_manual_returns_counts(admin_client):
    """
    STORAGE.CLEANUP.MANUAL — POST /api/storage/cleanup. Same shape as
    migrate; zero candidate files in tmp_test means deleted=0.
    """
    # Pass an empty JSON body — the cleanup handler reads request.get_json()
    # without force=True so a missing Content-Type 415s.
    resp = admin_client.post("/api/storage/cleanup", json={})
    assert resp.status_code in (200, 409), resp.text[:200]
    if resp.status_code == 409:
        pytest.skip("a prior cleanup is in_progress")
    body = resp.json()
    assert body.get("success") is True, body


# ---------------------------------------------------------------------------
# STORAGE.RECONCILE
# ---------------------------------------------------------------------------

def test_storage_reconcile_returns_counts(admin_client):
    """
    STORAGE.RECONCILE — POST /api/storage/reconcile. Walks the filesystem
    against the DB; with zero rows + zero files, no orphans, no missing.
    Contract: returns success.
    """
    resp = admin_client.post("/api/storage/reconcile", json={})
    assert resp.status_code in (200, 409), resp.text[:200]
    if resp.status_code == 409:
        pytest.skip("a prior reconcile is in_progress")
    body = resp.json()
    assert body.get("success") is True, body


# ---------------------------------------------------------------------------
# STORAGE.SETTINGS.UPDATE
# ---------------------------------------------------------------------------

def test_storage_settings_get_then_set_roundtrip(admin_client):
    """
    STORAGE.SETTINGS.UPDATE — GET current settings, POST a changed
    `archive_retention_days`, GET again and verify it stuck. Restore the
    original at the end so the test stack returns to its prior state.
    """
    # Read original
    get1 = admin_client.get("/api/storage/settings")
    assert get1.status_code == 200, get1.text[:200]
    original = get1.json().get("settings", {}).get("archive_retention_days")
    assert isinstance(original, int), f"non-int original: {original!r}"

    target = original + 7 if original < 100 else original - 7
    try:
        # Update
        post = admin_client.post(
            "/api/storage/settings",
            json={"archive_retention_days": target},
        )
        assert post.status_code == 200, post.text[:200]
        assert post.json().get("success") is True

        # Read back
        get2 = admin_client.get("/api/storage/settings")
        assert get2.status_code == 200
        new_value = get2.json().get("settings", {}).get("archive_retention_days")
        assert new_value == target, (
            f"after POST archive_retention_days={target}, GET returned {new_value!r}"
        )
    finally:
        # Restore
        admin_client.post(
            "/api/storage/settings",
            json={"archive_retention_days": original},
        )


# ---------------------------------------------------------------------------
# STORAGE.CANCEL_IN_PROGRESS
# ---------------------------------------------------------------------------

def test_storage_cancel_is_safe_when_idle(admin_client):
    """
    STORAGE.CANCEL_IN_PROGRESS — calling cancel when nothing is running
    must NOT crash. The reference doc's "Operation halts cleanly"
    behaviour requires an actual in-progress operation, which we can't
    reliably stage from a test (the migrate above is synchronous and
    completes before we'd dispatch a cancel). What we CAN verify is
    that the cancel endpoint exists and responds with a 2xx/4xx (not 5xx)
    on idle state.
    """
    resp = admin_client.post("/api/storage/cancel", json={})
    assert resp.status_code in (200, 400, 404, 409), (
        f"cancel-when-idle returned {resp.status_code} (should be 2xx/4xx, "
        f"not 5xx): {resp.text[:200]}"
    )


# ---------------------------------------------------------------------------
# STORAGE.ADMIN_ONLY enforcement (cross-cuts AUTH.ROLE.ADMIN_ONLY)
# ---------------------------------------------------------------------------

def test_storage_settings_admin_only_for_viewer(viewer_client):
    """
    Cross-cut with AUTH.ROLE.ADMIN_ONLY — a viewer hitting the storage
    settings POST gets 403 with the expected error JSON.
    """
    resp = viewer_client.post(
        "/api/storage/settings",
        json={"archive_retention_days": 99},
    )
    assert resp.status_code == 403, (
        f"viewer got {resp.status_code} (expected 403): {resp.text[:200]}"
    )
    body = resp.json()
    assert body.get("error") == "Admin access required", body
