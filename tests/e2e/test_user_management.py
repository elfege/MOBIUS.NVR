"""
tests/e2e/test_user_management.py — USER surface backfill (Phase D).

Covers the 3 USER rows from docs/functionality_reference.md:

  USER.ADD                      POST /api/users → bcrypt hash, must_change_password=true
  USER.ROLE.CHANGE              PATCH /api/users/<id> with {role: 'viewer'} → role flipped
  USER.ACCESS_CONTROL.PER_CAMERA  PUT /api/users/<id>/camera-access → allowlist applied

Caveat: USER.ACCESS_CONTROL.PER_CAMERA's "User's grid only shows allowed
cameras" UI-side enforcement requires the streams page to actually
filter; we test the DB-side allowlist write (the contract that the UI
reads from). UI rendering filter is a separate /streams concern not
covered here.
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
def disposable_username():
    """
    A unique username per test so re-runs don't collide on the UNIQUE
    constraint. The teardown deletes any user with this prefix in
    /api/users/<id> DELETE, but we add a DB-side cleanup as a belt
    against partial-test failures.
    """
    name = "e2e_user_add_target"
    yield name


@pytest.fixture(autouse=True)
def cleanup_disposable_users(db_conn):
    """
    Remove any e2e_user_* users created by these tests, before AND
    after each test, so re-runs always start clean. Independent of
    test-side cleanup so a mid-test crash doesn't leave junk users
    forever.
    """
    def _wipe():
        with db_conn.cursor() as cur:
            cur.execute(
                "DELETE FROM users WHERE username LIKE 'e2e_user_%'"
            )
    _wipe()
    yield
    _wipe()


# ---------------------------------------------------------------------------
# USER.ADD
# ---------------------------------------------------------------------------

def test_user_add_creates_user_with_bcrypt_and_force_change(
    admin_client, db_conn, disposable_username
):
    """
    USER.ADD — POST /api/users with username+password+role. Verify:
      - 200 with success=true + user.id
      - DB row exists with role + must_change_password=true (default)
      - password_hash is a bcrypt blob (starts with $2)
    """
    resp = admin_client.post(
        "/api/users",
        json={
            "username": disposable_username,
            "password": "initial_pw_at_least_8_chars",
            "role": "user",
        },
    )
    assert resp.status_code == 200, f"{resp.status_code} {resp.text[:200]}"
    body = resp.json()
    assert body.get("success") is True
    new_id = body["user"]["id"]
    assert isinstance(new_id, int)

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT role, must_change_password, password_hash FROM users WHERE id = %s",
            (new_id,),
        )
        row = cur.fetchone()
    assert row is not None, "user row missing in DB"
    role, must_change, pw_hash = row
    assert role == "user", f"role stored as {role!r}"
    assert must_change is True, (
        "must_change_password defaulted to False; doc says new users must "
        "be forced to change password on first login"
    )
    assert pw_hash.startswith("$2"), (
        f"password_hash {pw_hash[:10]}... isn't a bcrypt blob; the hashing "
        "may have been skipped"
    )


# ---------------------------------------------------------------------------
# USER.ROLE.CHANGE
# ---------------------------------------------------------------------------

def test_user_role_change_admin_to_viewer(admin_client, db_conn, disposable_username):
    """
    USER.ROLE.CHANGE — create a user as 'user', PATCH role to 'viewer',
    verify the DB row reflects the change.
    """
    # Create
    create = admin_client.post(
        "/api/users",
        json={
            "username": disposable_username,
            "password": "initial_pw_at_least_8",
            "role": "user",
        },
    )
    assert create.status_code == 200, create.text[:200]
    user_id = create.json()["user"]["id"]

    # PATCH the role
    patch = admin_client.patch(
        f"/api/users/{user_id}",
        json={"role": "viewer"},
    )
    assert patch.status_code == 200, f"{patch.status_code} {patch.text[:200]}"

    # Verify in DB
    with db_conn.cursor() as cur:
        cur.execute("SELECT role FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
    assert row is not None, "user disappeared after PATCH"
    assert row[0] == "viewer", f"role still {row[0]!r} after PATCH"


# ---------------------------------------------------------------------------
# USER.ACCESS_CONTROL.PER_CAMERA
# ---------------------------------------------------------------------------

def test_user_camera_access_allowlist_persists(
    admin_client, db_conn, disposable_username
):
    """
    USER.ACCESS_CONTROL.PER_CAMERA — seed a camera, create a user, PUT
    a camera-access allowlist, then GET and assert the allowlist comes
    back with just that camera.
    """
    serial = "E2E_USER_ACCESS_CAM"

    # Seed a camera (no need for user_camera_preferences here)
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO cameras (serial, name, type, camera_id, stream_type, hidden)
            VALUES (%s, 'Access Test Cam', 'REOLINK', %s, 'LL_HLS', false)
            ON CONFLICT (serial) DO UPDATE SET hidden = false
            """,
            (serial, serial),
        )

    try:
        # Create user
        create = admin_client.post(
            "/api/users",
            json={
                "username": disposable_username,
                "password": "initial_pw_at_least_8",
                "role": "user",
            },
        )
        assert create.status_code == 200, create.text[:200]
        user_id = create.json()["user"]["id"]

        # PUT allowlist
        put = admin_client.put(
            f"/api/users/{user_id}/camera-access",
            json={
                "cameras": [
                    {"camera_serial": serial, "allowed": True},
                ],
            },
        )
        assert put.status_code in (200, 201, 204), f"{put.status_code} {put.text[:200]}"

        # GET back
        get = admin_client.get(f"/api/users/{user_id}/camera-access")
        assert get.status_code == 200, get.text[:200]
        access = get.json()
        # Endpoint returns a list of {camera_serial, allowed} rows. Only
        # rows the PUT seeded with allowed=true exist (the impl deletes
        # any allowed=false entries before inserting).
        assert isinstance(access, list), f"unexpected shape: {access}"
        serials_allowed = {r["camera_serial"] for r in access if r.get("allowed")}
        assert serial in serials_allowed, (
            f"after PUT, GET /camera-access doesn't show {serial}: {access}"
        )
    finally:
        # Cleanup the camera (user-side cleanup handled by autouse fixture)
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM cameras WHERE serial = %s", (serial,))


# ---------------------------------------------------------------------------
# USER.ADD.WEAK_PASSWORD — negative shape
# ---------------------------------------------------------------------------

def test_user_add_rejects_short_password(admin_client, disposable_username):
    """
    Cross-cut hardening — POST /api/users with a 7-char password is
    rejected by the route's `len(password) < 8` guard. Not in the
    reference doc as its own row but worth pinning so the policy isn't
    relaxed without intent.
    """
    resp = admin_client.post(
        "/api/users",
        json={
            "username": disposable_username,
            "password": "short!!",  # 7 chars
            "role": "user",
        },
    )
    assert resp.status_code == 400, (
        f"7-char password got {resp.status_code} (expected 400): {resp.text[:200]}"
    )
    assert "8" in resp.json().get("error", ""), resp.json()
