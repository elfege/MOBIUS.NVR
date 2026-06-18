"""
tests/e2e/test_settings_global.py — global settings modal (Phase D backfill).

Honest scoping
--------------
Most of the 12 "Settings — global modal" rows in
docs/functionality_reference.md describe MODAL behaviour (tab opens,
controls render, layout updates). Testing those via Playwright is
fragile against CSS/markup churn and adds 5-10s per case for low signal.

The API surface UNDER the modal IS testable and is what this file
covers:

  SETTINGS.GLOBAL.GET            GET  /api/settings/global/<key>
  SETTINGS.GLOBAL.SET            PUT  /api/settings/global/<key>
  SETTINGS.GLOBAL.LIST_ALL       GET  /api/settings/global
  SETTINGS.TAB.DATA.ADMIN_ONLY   viewer → GET /api/telemetry/settings → 403
  SETTINGS.TAB.STORAGE.STATS     admin → GET /api/storage/stats (re-pin
                                  — also tested in test_storage_management;
                                  here we verify it from the modal-fetch
                                  shape the Settings tab uses).

Rows intentionally marked `modal-driven:SKIP` in the reference doc:
  SETTINGS.MODAL.OPEN, SETTINGS.HEADER_SAVE, SETTINGS.TAB.SWITCH_AUTOSAVE_DATA,
  SETTINGS.TAB.VIEW.GRID_STYLE, SETTINGS.TAB.FULLSCREEN.AUTO_DELAY,
  SETTINGS.TAB.PERFORMANCE.THROTTLE, SETTINGS.TAB.EVIDENCE.HIDDEN_WHEN_OFF,
  SETTINGS.TAB.DATA.RENDER, SETTINGS.TAB.LOGS.OPEN_AUDIT_LOG.

These are UI behaviours; their underlying API contracts are pinned by
other test files (telemetry, storage, settings_routes) and by manual
verification (`manual:2026-06-14/15`).
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
    username, password = seed_test_admin
    client = httpx.Client(base_url=base_url, follow_redirects=False)
    resp = client.post("/login", data={"username": username, "password": password})
    assert resp.status_code in (200, 302, 303), resp.text[:200]
    yield client
    client.close()


@pytest.fixture
def seed_settings_viewer(db_conn, seed_test_admin):
    username = "e2e_settings_viewer"
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
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE username = %s", (username,))


@pytest.fixture
def viewer_client(base_url, seed_settings_viewer):
    username, password = seed_settings_viewer
    client = httpx.Client(base_url=base_url, follow_redirects=False)
    client.post("/login", data={"username": username, "password": password})
    yield client
    client.close()


# Test setting key — chosen to avoid colliding with any real prod key.
TEST_KEY = "_e2e_test_global_setting"


@pytest.fixture
def cleanup_test_setting(db_conn):
    """Remove our test key on enter + exit so re-runs are clean."""
    def _wipe():
        try:
            with db_conn.cursor() as cur:
                cur.execute("DELETE FROM nvr_settings WHERE key = %s", (TEST_KEY,))
        except Exception:
            pass  # nvr_settings might not exist in some test scenarios
    _wipe()
    yield
    _wipe()


# ---------------------------------------------------------------------------
# SETTINGS.GLOBAL.SET
# ---------------------------------------------------------------------------

def test_settings_global_set_persists(
    admin_client, db_conn, cleanup_test_setting
):
    """
    SETTINGS.GLOBAL.SET — PUT /api/settings/global/<key> writes to
    nvr_settings. Verify the row appears with the new value.
    """
    resp = admin_client.put(
        f"/api/settings/global/{TEST_KEY}",
        json={"value": "test_value_42"},
    )
    assert resp.status_code == 200, f"{resp.status_code} {resp.text[:200]}"
    assert resp.json().get("success") is True

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT value FROM nvr_settings WHERE key = %s", (TEST_KEY,)
        )
        row = cur.fetchone()
    assert row is not None, f"nvr_settings row for {TEST_KEY} missing after PUT"
    assert row[0] == "test_value_42", f"value stored as {row[0]!r}"


# ---------------------------------------------------------------------------
# SETTINGS.GLOBAL.GET
# ---------------------------------------------------------------------------

def test_settings_global_get_returns_value(
    admin_client, db_conn, cleanup_test_setting
):
    """
    SETTINGS.GLOBAL.GET — PUT then GET reads back the same value.
    """
    # Seed via PUT (also verifies the PUT path again — harmless)
    admin_client.put(
        f"/api/settings/global/{TEST_KEY}",
        json={"value": "roundtrip_xyz"},
    )

    resp = admin_client.get(f"/api/settings/global/{TEST_KEY}")
    assert resp.status_code == 200, resp.text[:200]
    body = resp.json()
    assert body.get("value") == "roundtrip_xyz", f"unexpected body: {body}"


# ---------------------------------------------------------------------------
# SETTINGS.GLOBAL.LIST_ALL
# ---------------------------------------------------------------------------

def test_settings_global_list_all_includes_our_key(
    admin_client, cleanup_test_setting
):
    """
    SETTINGS.GLOBAL.LIST_ALL — GET /api/settings/global returns a dict
    of every nvr_settings key. After PUTting a test key it must show up.
    """
    admin_client.put(
        f"/api/settings/global/{TEST_KEY}",
        json={"value": "list_visible"},
    )
    resp = admin_client.get("/api/settings/global")
    assert resp.status_code == 200, resp.text[:200]
    body = resp.json()
    # The handler returns a dict {key: value}. Verify our key appears.
    assert TEST_KEY in body, (
        f"PUT'd key {TEST_KEY!r} not in /api/settings/global response. "
        f"Keys: {sorted(body.keys())}"
    )
    assert body[TEST_KEY] == "list_visible"


# ---------------------------------------------------------------------------
# SETTINGS.TAB.DATA.ADMIN_ONLY (cross-cuts AUTH.ROLE.ADMIN_ONLY)
# ---------------------------------------------------------------------------

def test_settings_data_tab_admin_only_telemetry_settings(viewer_client):
    """
    SETTINGS.TAB.DATA.ADMIN_ONLY — the Data tab calls GET
    /api/telemetry/settings to render. A viewer hitting that endpoint
    directly gets 403 (the tab also hides client-side, but we test the
    backend gate, which is the real security boundary).
    """
    resp = viewer_client.get("/api/telemetry/settings")
    assert resp.status_code == 403, (
        f"viewer got {resp.status_code} on /api/telemetry/settings "
        f"(expected 403): {resp.text[:200]}"
    )


# ---------------------------------------------------------------------------
# SETTINGS.TAB.STORAGE.STATS — re-pin from the Settings-modal fetch shape
# ---------------------------------------------------------------------------

def test_settings_storage_stats_returns_modal_shape(admin_client):
    """
    SETTINGS.TAB.STORAGE.STATS — the Settings → Storage tab calls
    GET /api/storage/stats and expects the envelope to contain
    recent/archive/config/warnings (the same contract pinned in
    test_storage_management). Re-pin here because the Settings tab's
    JS render code reads specifically these keys.
    """
    resp = admin_client.get("/api/storage/stats")
    assert resp.status_code == 200, resp.text[:200]
    body = resp.json()
    assert body.get("success") is True
    for key in ("recent", "archive", "config", "warnings"):
        assert key in body, (
            f"/api/storage/stats response missing {key!r} (the Settings → "
            f"Storage tab needs all four): {sorted(body.keys())}"
        )
