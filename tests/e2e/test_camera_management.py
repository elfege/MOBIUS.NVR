"""
tests/e2e/test_camera_management.py — CAMERA surface backfill (Phase D).

Reference-doc reality check
---------------------------
The CAM.ADD.OK / CAM.DELETE rows in the reference doc are documented as
"via UI (or cameras.json seed at startup)" — meaning the actual
contract is FILE+RESTART, not a single API. There is no POST /api/cameras
endpoint to add a camera, no DELETE /api/cameras/<id> to remove one.
What exists is:

  - cameras.json on disk holds the seed; sync_cameras_json_to_db()
    copies it into the cameras table at startup
  - The 4-place camera-field regression test (test_camera_field_4_place_rule)
    pins the column-write ↔ column-read contract that survives this
    sync

Those file+restart contracts are not e2e-testable in the routine pytest
loop without modifying the test stack's cameras.json and restarting the
container — a 30s cycle that swamps the test's signal.

What IS e2e-testable, and what this file covers:

  CAM.EDIT.HOST_CHANGE  PUT /api/settings/camera/<serial>/host           DB write
  CAM.RENAME            PUT /api/camera/<serial>/name                    DB + JSON + cache
  CAM.GET               GET /api/cameras/<serial>                        fresh-from-DB shape
  CAM.RENAME.EMPTY      PUT /api/camera/<serial>/name with ""            400 negative-shape
  CAM.RENAME.NOT_FOUND  PUT /api/camera/UNKNOWN/name                     404 negative-shape
"""

from __future__ import annotations

import bcrypt
import httpx
import pytest


@pytest.fixture
def admin_client(base_url: str, seed_test_admin):
    username, password = seed_test_admin
    client = httpx.Client(base_url=base_url, follow_redirects=False)
    resp = client.post("/login", data={"username": username, "password": password})
    assert resp.status_code in (200, 302, 303), (
        f"admin login failed: {resp.status_code} {resp.text[:200]}"
    )
    yield client
    client.close()


@pytest.fixture
def seed_camera(db_conn, seed_test_admin, worker_tag):
    """
    Seed a camera with a known starting state so each test sees the
    same baseline (name='Pre-rename', host=10.0.0.1). Serial is
    worker-suffixed so xdist workers don't collide.
    """
    cam_serial = f"E2E_CAM_MGMT_TEST_{worker_tag}"
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO cameras (
                serial, name, type, camera_id, stream_type, host, hidden,
                streaming_hub
            ) VALUES (%s, 'Pre-rename', 'REOLINK', %s, 'LL_HLS', '10.0.0.1', false,
                      'mediamtx')
            ON CONFLICT (serial) DO UPDATE SET
                name = 'Pre-rename',
                host = '10.0.0.1',
                stream_type = 'LL_HLS',
                hidden = false,
                streaming_hub = 'mediamtx'
            """,
            (cam_serial, cam_serial),
        )
    yield cam_serial
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM cameras WHERE serial = %s", (cam_serial,))


# All test functions take `seed_camera` (the fixture) and dereference the
# yielded value as `seed_camera` — the cam_serial value is the fixture's
# yield, not a module-level constant. No further changes needed below.


# ---------------------------------------------------------------------------
# CAM.EDIT.HOST_CHANGE
# ---------------------------------------------------------------------------

def test_cam_edit_host_change_persists(admin_client, db_conn, seed_camera):
    """
    CAM.EDIT.HOST_CHANGE — admin updates the camera's host via the
    settings PUT endpoint. Expect cameras.host to reflect the new value.
    The streaming hub side ("regenerates; FFmpeg uses new URL on next
    restart") is operational, not a DB-side contract — covered by the
    streaming-hub regeneration logic, not this test.
    """
    resp = admin_client.put(
        f"/api/settings/camera/{seed_camera}/host",
        json={"value": "192.168.50.99"},
    )
    assert resp.status_code == 200, f"{resp.status_code} {resp.text[:200]}"
    assert resp.json().get("success") is True

    with db_conn.cursor() as cur:
        cur.execute("SELECT host FROM cameras WHERE serial = %s", (seed_camera,))
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "192.168.50.99", f"host stored as {row[0]!r}"


# ---------------------------------------------------------------------------
# CAM.RENAME
# ---------------------------------------------------------------------------

def test_cam_rename_via_dedicated_endpoint(admin_client, db_conn, seed_camera):
    """
    CAM.RENAME — PUT /api/camera/<serial>/name. The handler updates BOTH
    cameras.name (DB) AND cameras.json on disk (per the route docstring).
    We verify the DB side here; the cameras.json side is implementation
    detail that the unit tests already cover.
    """
    new_name = "Post-rename Test Cam"
    resp = admin_client.put(
        f"/api/camera/{seed_camera}/name",
        json={"name": new_name},
    )
    assert resp.status_code == 200, f"{resp.status_code} {resp.text[:200]}"
    body = resp.json()
    assert body.get("success") is True
    assert body.get("name") == new_name
    assert body.get("previous_name") == "Pre-rename"

    with db_conn.cursor() as cur:
        cur.execute("SELECT name FROM cameras WHERE serial = %s", (seed_camera,))
        row = cur.fetchone()
    assert row is not None
    assert row[0] == new_name


# ---------------------------------------------------------------------------
# CAM.GET — single-camera fresh-from-DB read
# ---------------------------------------------------------------------------

def test_cam_get_returns_camera_config(admin_client, seed_camera):
    """
    CAM.GET — GET /api/cameras/<serial> returns the full camera config
    dict. The route reads "always fresh from DB" per its docstring; we
    verify the basic shape (serial, name, type) is present.
    """
    resp = admin_client.get(f"/api/cameras/{seed_camera}")
    assert resp.status_code == 200, f"{resp.status_code} {resp.text[:200]}"
    body = resp.json()
    assert isinstance(body, dict), f"unexpected shape: {body}"
    assert body.get("serial") == seed_camera
    assert "name" in body
    assert "type" in body


# ---------------------------------------------------------------------------
# CAM.RENAME.EMPTY — negative-shape
# ---------------------------------------------------------------------------

def test_cam_rename_rejects_empty_name(admin_client, seed_camera):
    """
    Empty string for name is rejected with 400. The handler explicitly
    guards against `not new_name` after stripping.
    """
    resp = admin_client.put(
        f"/api/camera/{seed_camera}/name",
        json={"name": "   "},  # whitespace-only → stripped → empty
    )
    assert resp.status_code == 400, f"{resp.status_code} {resp.text[:200]}"
    assert "empty" in resp.json().get("error", "").lower(), resp.json()


# ---------------------------------------------------------------------------
# CAM.RENAME.NOT_FOUND — negative-shape
# ---------------------------------------------------------------------------

def test_cam_rename_unknown_camera_returns_404(admin_client):
    """
    Renaming a serial that doesn't exist returns 404 with the
    "Camera not found" error JSON.
    """
    resp = admin_client.put(
        "/api/camera/DEFINITELY_NOT_A_REAL_SERIAL/name",
        json={"name": "Whatever"},
    )
    assert resp.status_code == 404, f"{resp.status_code} {resp.text[:200]}"
    assert "not found" in resp.json().get("error", "").lower(), resp.json()
