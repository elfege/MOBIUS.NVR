"""
tests/e2e/test_camera_management.py — CAMERA surface backfill (Phase D).

Reference-doc reality check
---------------------------
CAM.ADD is now a real API: POST /api/cameras (added 2026-06-27) inserts a
camera row, stores encrypted credentials, and reloads the live camera repo —
covered by the CAM.ADD tests at the bottom of this file. (DELETE
/api/cameras/<id> still does not exist; removal remains a DB/file op.) The
file-seed path also still exists:

  - cameras.json on disk holds the seed; sync_cameras_json_to_db()
    copies it into the cameras table at startup (and NEVER deletes DB rows,
    so an API-added camera survives restarts)
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
# CAM.ADD — POST /api/cameras  (added 2026-06-27)
#   CAM.ADD.OK           POST /api/cameras                     DB row + encrypted creds
#   CAM.ADD.EUFY_REJECT  POST /api/cameras type=eufy           400 (P2P → Eufy Bridge)
#   CAM.ADD.MISSING      POST /api/cameras missing serial      400 negative-shape
#   CAM.ADD.DUPLICATE    POST /api/cameras existing serial     409 conflict
# ---------------------------------------------------------------------------

@pytest.fixture
def add_camera_serial(db_conn, worker_tag):
    """Serial for the add-camera tests; row + credentials cleaned up after."""
    serial = f"E2E_ADDCAM_{worker_tag}"
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM camera_credentials WHERE credential_key = %s", (serial,))
        cur.execute("DELETE FROM cameras WHERE serial = %s", (serial,))
    yield serial
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM camera_credentials WHERE credential_key = %s", (serial,))
        cur.execute("DELETE FROM cameras WHERE serial = %s", (serial,))


def test_add_camera_creates_row_and_credentials(admin_client, db_conn, add_camera_serial):
    """CAM.ADD.OK — inserts the row with defaults applied + stores creds."""
    resp = admin_client.post("/api/cameras", json={
        "serial": add_camera_serial,
        "name": "E2E Added Cam",
        "type": "amcrest",
        "host": "10.0.0.99",
        "username": "e2euser",
        "password": "e2epass",
    })
    assert resp.status_code == 200, f"{resp.status_code} {resp.text[:200]}"
    assert resp.json().get("success") is True

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT name, type, host, stream_type, streaming_hub "
            "FROM cameras WHERE serial = %s",
            (add_camera_serial,),
        )
        row = cur.fetchone()
    assert row is not None, "camera row was not created"
    name, ctype, host, stream_type, hub = row
    assert (name, ctype, host) == ("E2E Added Cam", "amcrest", "10.0.0.99")
    assert stream_type == "LL_HLS"   # default applied
    assert hub == "mediamtx"          # default applied

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM camera_credentials "
            "WHERE credential_key = %s AND credential_type = 'camera'",
            (add_camera_serial,),
        )
        assert cur.fetchone()[0] == 1, "encrypted credentials were not stored"


def test_add_camera_rejects_eufy(admin_client, add_camera_serial):
    """CAM.ADD.EUFY_REJECT — eufy is P2P (Bridge), must be 400."""
    resp = admin_client.post("/api/cameras", json={
        "serial": add_camera_serial, "name": "Nope", "type": "eufy",
    })
    assert resp.status_code == 400, f"{resp.status_code} {resp.text[:200]}"
    body = resp.text.lower()
    assert "eufy" in body or "bridge" in body


def test_add_camera_requires_core_fields(admin_client):
    """CAM.ADD.MISSING — missing serial → 400 (no row created)."""
    resp = admin_client.post("/api/cameras", json={"name": "no serial", "type": "amcrest"})
    assert resp.status_code == 400, f"{resp.status_code} {resp.text[:200]}"


def test_add_camera_duplicate_conflict(admin_client, add_camera_serial):
    """CAM.ADD.DUPLICATE — adding an existing serial → 409."""
    first = admin_client.post("/api/cameras", json={
        "serial": add_camera_serial, "name": "First", "type": "amcrest",
    })
    assert first.status_code == 200, first.text[:200]
    dup = admin_client.post("/api/cameras", json={
        "serial": add_camera_serial, "name": "Dup", "type": "sv3c",
    })
    assert dup.status_code == 409, f"{dup.status_code} {dup.text[:200]}"


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
