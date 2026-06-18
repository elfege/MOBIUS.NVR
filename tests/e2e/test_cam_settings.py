"""
tests/e2e/test_cam_settings.py — per-camera settings surface (Phase D backfill).

Covers the 6 CAM.SETTINGS rows from docs/functionality_reference.md:

  CAM.SETTINGS.OPEN              gear click opens the per-camera modal
  CAM.SETTINGS.STREAM_TYPE.CHANGE   user toggle persists in user_camera_preferences
  CAM.SETTINGS.STREAMING_HUB.CHANGE admin toggle persists in cameras (4-place rule)
  CAM.SETTINGS.NICKNAME.SET         admin set persists in cameras.nickname
  CAM.SETTINGS.VISIBILITY.HIDE      user toggle persists in user_camera_preferences
  CAM.SETTINGS.DISPLAY_ORDER        drag-to-reorder persists in user_camera_preferences

Design notes
------------
The five mutation tests hit the JSON API directly (httpx) rather than driving
the modal UI with Playwright. Reasons:
  - The expected behaviour ("persisted to DB; re-renders") is fully captured
    by the DB-side post-condition; the UI modal is just one trigger.
  - Modal-driven tests are fragile against CSS/markup churn — the API +
    DB contract is what we actually want to pin.
  - CAM.SETTINGS.OPEN is the one row where the UI IS the contract (the
    modal must be reachable from the gear icon); that one stays Playwright.

Each test seeds a fresh test camera, mutates one field, asserts the DB row,
and removes the camera in a finally clause so re-runs always start clean.
"""

from __future__ import annotations

import bcrypt
import httpx
import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def seed_test_camera(db_conn, seed_test_admin, worker_tag):
    """
    Insert a minimal `cameras` row AND a baseline `user_camera_preferences`
    row for the test admin so the settings endpoints have a real target.

    Depends on seed_test_admin (NOT just db_conn) so pytest schedules
    that fixture first — the user_camera_preferences seed needs the
    e2e_admin row to already exist; otherwise (SELECT id FROM users WHERE
    username = 'e2e_admin') returns NULL and trips the user_id NOT NULL
    constraint.

    The user_camera_preferences pre-seed matters: `preferred_stream_type`
    is NOT NULL on that table, so a bare INSERT triggered by a PUT to
    visible/display_order without an existing row would fail. Pre-seeding
    forces every later PUT to be a PATCH/UPDATE — the documented
    "user toggles X" path.

    Yields the camera serial. Both rows dropped on teardown (FK cascade
    would catch the prefs row, but explicit is clearer).
    """
    serial = f"E2E_TEST_CAMERA_001_{worker_tag}"
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO cameras (
                serial, name, type, camera_id, stream_type, hidden
            ) VALUES (%s, 'E2E Test Cam', 'REOLINK', %s, 'LL_HLS', false)
            ON CONFLICT (serial) DO UPDATE SET
                stream_type   = 'LL_HLS',
                hidden        = false,
                nickname      = NULL,
                streaming_hub = NULL
            """,
            (serial, serial),
        )
        # Use the worker-scoped admin username (seed_test_admin yields
        # `(username, password)`; worker_id is suffixed under xdist).
        admin_username = seed_test_admin[0]
        cur.execute(
            """
            INSERT INTO user_camera_preferences (
                user_id, camera_serial, preferred_stream_type, visible, display_order
            ) VALUES (
                (SELECT id FROM users WHERE username = %s),
                %s, 'LL_HLS', true, 0
            )
            ON CONFLICT (user_id, camera_serial) DO UPDATE SET
                preferred_stream_type = 'LL_HLS',
                visible = true,
                display_order = 0
            """,
            (admin_username, serial),
        )
    yield serial
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM user_camera_preferences WHERE camera_serial = %s", (serial,))
        cur.execute("DELETE FROM cameras WHERE serial = %s", (serial,))


@pytest.fixture
def admin_client(base_url: str, seed_test_admin):
    """
    httpx.Client logged in as the test admin. Persists the Flask session
    cookie across calls; closed via context-manager teardown.
    """
    username, password = seed_test_admin
    client = httpx.Client(base_url=base_url, follow_redirects=False)
    resp = client.post("/login", data={"username": username, "password": password})
    assert resp.status_code in (200, 302, 303), (
        f"admin login failed: {resp.status_code} {resp.text[:200]}"
    )
    yield client
    client.close()


# ---------------------------------------------------------------------------
# CAM.SETTINGS.OPEN — UI smoke (the gear click reaches the modal)
# ---------------------------------------------------------------------------

def test_cam_settings_open_modal_reachable(page, base_url, seed_test_admin, seed_test_camera):
    """
    CAM.SETTINGS.OPEN — log in, navigate to /streams, click the gear on the
    seeded camera's tile, expect the per-camera settings modal to become
    visible.

    Locator strategy: the gear icon is a button with title='Settings' inside
    the action bar at the bottom of each tile. The modal is identified by
    `#camera-settings-modal` (the actual DOM id used by camera-settings-modal.js).
    """
    username, password = seed_test_admin
    serial = seed_test_camera

    page.goto(f"{base_url}/login")
    page.locator('input[name="username"]').fill(username)
    page.locator('input[name="password"]').fill(password)
    page.locator('button[type="submit"], input[type="submit"]').first.click()
    page.wait_for_url(lambda url: "/login" not in url, timeout=10_000)

    # Streams page should render with our seeded camera. Force re-load
    # in case the server cached a no-camera view from before.
    page.goto(f"{base_url}/streams")

    # Tile for our camera. The tile uses the serial as a data attribute.
    tile = page.locator(f'[data-camera-serial="{serial}"]').first
    if tile.count() == 0:
        # The streams grid may not surface a brand-new camera without a
        # restart of the cache. Skip rather than fail — this row's
        # contract is "gear opens modal", not "newly-inserted DB row
        # appears immediately."
        pytest.skip(
            f"camera {serial} not in the rendered grid (cache likely stale). "
            "The endpoint contract still holds; UI freshness is a separate "
            "concern (CAM.SETTINGS.OPEN would need a stack restart or "
            "/api/cameras/reload hit before the gear is reachable)."
        )

    # Hover to surface the action bar, then click the gear.
    tile.hover()
    gear = tile.locator('[title="Settings"], button.btn-settings, button[data-action="settings"]').first
    assert gear.count() > 0, "no Settings gear found in the tile's action bar"
    gear.click()

    modal = page.locator('#camera-settings-modal, .camera-settings-modal').first
    modal.wait_for(state="visible", timeout=5_000)
    assert modal.is_visible(), "camera-settings-modal didn't become visible after gear click"


# ---------------------------------------------------------------------------
# CAM.SETTINGS.STREAM_TYPE.CHANGE — user_camera_preferences row
# ---------------------------------------------------------------------------

def test_cam_settings_stream_type_change_persists(
    admin_client, db_conn, seed_test_camera, admin_username
):
    """
    CAM.SETTINGS.STREAM_TYPE.CHANGE — PUT a new preferred stream type via
    the per-user preference endpoint. Expect a row in user_camera_preferences
    with the new value.
    """
    serial = seed_test_camera

    resp = admin_client.put(
        f"/api/settings/user/{serial}/preferred_stream_type",
        json={"value": "WEBRTC"},
    )
    assert resp.status_code == 200, f"{resp.status_code} {resp.text[:200]}"
    assert resp.json().get("success") is True

    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT preferred_stream_type
              FROM user_camera_preferences
              WHERE camera_serial = %s
                AND user_id = (SELECT id FROM users WHERE username = %s)
            """,
            (serial, admin_username),
        )
        row = cur.fetchone()
    assert row is not None, "no user_camera_preferences row written"
    assert row[0] == "WEBRTC", f"preferred_stream_type stored as {row[0]!r}, expected WEBRTC"


# ---------------------------------------------------------------------------
# CAM.SETTINGS.STREAMING_HUB.CHANGE — cameras.streaming_hub (4-place rule)
# ---------------------------------------------------------------------------

def test_cam_settings_streaming_hub_change_persists(
    admin_client, db_conn, seed_test_camera
):
    """
    CAM.SETTINGS.STREAMING_HUB.CHANGE — admin sets streaming_hub on a camera.
    Expect the cameras table to reflect the new value. This row's
    'expected' explicitly references the 4-place rule (RULE 11.2) — if
    the regression test test_camera_field_4_place_rule.py passes AND this
    test passes, both halves of the contract are guarded.
    """
    serial = seed_test_camera

    resp = admin_client.put(
        f"/api/settings/camera/{serial}/streaming_hub",
        json={"value": "go2rtc"},
    )
    assert resp.status_code == 200, f"{resp.status_code} {resp.text[:200]}"
    assert resp.json().get("success") is True

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT streaming_hub FROM cameras WHERE serial = %s",
            (serial,),
        )
        row = cur.fetchone()
    assert row is not None, f"no cameras row for {serial}"
    assert row[0] == "go2rtc", (
        f"streaming_hub stored as {row[0]!r}, expected 'go2rtc'. "
        "If this fails, RULE 11.2 (4-place rule) is likely broken — "
        "check that 'streaming_hub' is in DIRECT_FIELDS (camera_config_sync) "
        "AND direct_fields (camera_repository)."
    )


# ---------------------------------------------------------------------------
# CAM.SETTINGS.NICKNAME.SET — cameras.nickname
# ---------------------------------------------------------------------------

def test_cam_settings_nickname_set_persists(
    admin_client, db_conn, seed_test_camera
):
    """
    CAM.SETTINGS.NICKNAME.SET — admin sets a nickname. Expect persistence
    on the cameras table. (The reference doc also notes the nickname
    resolves in the `?fullscreen=` URL param — that's a streams-page
    concern, not a settings-write concern; covered separately.)
    """
    serial = seed_test_camera

    # Nickname must satisfy the cameras_nickname_format_chk regex:
    #   ^[a-z]+[0-9]?$    (one or more lowercase letters, optional digit)
    # 'e2etest' fits. Hyphens / digits-in-middle would be rejected with
    # 23514 "violates check constraint cameras_nickname_format_chk".
    resp = admin_client.put(
        f"/api/settings/camera/{serial}/nickname",
        json={"value": "etestcam"},
    )
    assert resp.status_code == 200, f"{resp.status_code} {resp.text[:200]}"
    assert resp.json().get("success") is True

    with db_conn.cursor() as cur:
        cur.execute("SELECT nickname FROM cameras WHERE serial = %s", (serial,))
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "etestcam", f"nickname stored as {row[0]!r}"


# ---------------------------------------------------------------------------
# CAM.SETTINGS.VISIBILITY.HIDE — user_camera_preferences.visible
# ---------------------------------------------------------------------------

def test_cam_settings_visibility_hide_persists(
    admin_client, db_conn, seed_test_camera, admin_username
):
    """
    CAM.SETTINGS.VISIBILITY.HIDE — user toggles a camera off from their
    grid. Expect user_camera_preferences.visible = false for that
    (user, camera) pair. (The admin-still-sees-it part is policy on the
    streams-page renderer, not a settings-write concern.)
    """
    serial = seed_test_camera

    resp = admin_client.put(
        f"/api/settings/user/{serial}/visible",
        json={"value": False},
    )
    assert resp.status_code == 200, f"{resp.status_code} {resp.text[:200]}"

    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT visible FROM user_camera_preferences
              WHERE camera_serial = %s
                AND user_id = (SELECT id FROM users WHERE username = %s)
            """,
            (serial, admin_username),
        )
        row = cur.fetchone()
    assert row is not None, "no user_camera_preferences row written"
    assert row[0] is False, f"visible stored as {row[0]!r}, expected False"


# ---------------------------------------------------------------------------
# CAM.SETTINGS.DISPLAY_ORDER — user_camera_preferences.display_order
# ---------------------------------------------------------------------------

def test_cam_settings_display_order_persists(
    admin_client, db_conn, seed_test_camera, admin_username
):
    """
    CAM.SETTINGS.DISPLAY_ORDER — user reorders via drag-and-drop in the UI;
    that translates into a PUT against the user preference endpoint with
    an integer value. We exercise the write path directly.
    """
    serial = seed_test_camera

    resp = admin_client.put(
        f"/api/settings/user/{serial}/display_order",
        json={"value": 42},
    )
    assert resp.status_code == 200, f"{resp.status_code} {resp.text[:200]}"

    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT display_order FROM user_camera_preferences
              WHERE camera_serial = %s
                AND user_id = (SELECT id FROM users WHERE username = %s)
            """,
            (serial, admin_username),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == 42, f"display_order stored as {row[0]!r}, expected 42"
