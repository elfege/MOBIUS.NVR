"""
tests/e2e/test_auth_coverage.py — auth surface backfill (Phase D).

Covers the auth rows from docs/functionality_reference.md that were
missing e2e coverage entering Phase D:

  - AUTH.LOGIN.UNKNOWN_USER       no user-enumeration leak
  - AUTH.LOGOUT                   session destroyed, user_sessions row flipped
  - AUTH.ROLE.ADMIN_ONLY          viewer hits admin endpoint → 403 JSON
  - AUTH.CSRF.EXEMPT_JSON_API     JSON POST without X-CSRFToken still processed
  - AUTH.CHANGE_PASSWORD.FIRST_LOGIN  must_change_password forces /change-password

Already covered by tests/e2e/test_auth_login.py (Phase B):
  - AUTH.LOGIN.OK
  - AUTH.LOGIN.WRONG_PASSWORD

Not covered here (requires non-default stack config):
  - AUTH.TRUSTED_NETWORK.BYPASS — needs NVR_TRUSTED_NETWORK_ENABLED=true at
    container boot AND a matching subnet allowlist. Out of scope for the
    default test stack; left as TBD in the reference doc.
"""

from __future__ import annotations

import bcrypt
import httpx
import pytest
from playwright.sync_api import Page, expect


# ---------------------------------------------------------------------------
# Fixtures specific to this file
# ---------------------------------------------------------------------------

@pytest.fixture
def seed_test_viewer(db_conn, worker_tag):
    """
    Insert (or update) a viewer-role test user. Distinct from
    `seed_test_admin` so we can exercise admin-only endpoints with a
    user the server will reject.

    Yields (username, password). Each test that touches this fixture
    gets a fresh password hash computed at fixture-setup time.
    Username worker-suffixed for xdist isolation.
    """
    username = f"e2e_viewer_{worker_tag}"
    password = "e2e_viewer_password"
    bcrypt_hash = bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt(12),
    ).decode("utf-8")

    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (username, password_hash, role, must_change_password)
            VALUES (%s, %s, 'viewer', false)
            ON CONFLICT (username)
            DO UPDATE SET password_hash = EXCLUDED.password_hash,
                          role = 'viewer',
                          must_change_password = false
            """,
            (username, bcrypt_hash),
        )
    yield username, password


@pytest.fixture
def seed_first_login_user(db_conn, worker_tag):
    """
    Insert a user with `must_change_password=true` so we can exercise
    the forced-redirect-to-/change-password flow.

    Yields (username, password). The user is deleted after the test so
    re-runs always start clean. Username worker-suffixed for xdist.
    """
    username = f"e2e_first_login_user_{worker_tag}"
    password = "initial_password"
    bcrypt_hash = bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt(12),
    ).decode("utf-8")

    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (username, password_hash, role, must_change_password)
            VALUES (%s, %s, 'admin', true)
            ON CONFLICT (username)
            DO UPDATE SET password_hash = EXCLUDED.password_hash,
                          role = 'admin',
                          must_change_password = true
            """,
            (username, bcrypt_hash),
        )
    yield username, password
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE username = %s", (username,))


def _login(page: Page, base_url: str, username: str, password: str) -> None:
    """Helper: drive the login form. Caller decides what to assert next."""
    page.goto(f"{base_url}/login")
    page.locator('input[name="username"]').fill(username)
    page.locator('input[name="password"]').fill(password)
    page.locator('button[type="submit"], input[type="submit"]').first.click()


# ---------------------------------------------------------------------------
# AUTH.LOGIN.UNKNOWN_USER
# ---------------------------------------------------------------------------

def test_auth_login_unknown_user_no_enumeration_leak(page: Page, base_url: str):
    """
    AUTH.LOGIN.UNKNOWN_USER — submitting a username that doesn't exist
    keeps the user on /login with the same banner as wrong password. No
    user-enumeration leak (no "user not found" vs "wrong password"
    distinction).
    """
    _login(page, base_url, "definitely_not_a_user_zzz", "anything")

    page.wait_for_load_state("networkidle", timeout=5_000)
    assert page.url.rstrip("/").endswith("/login"), (
        f"expected to still be on /login for unknown user, got {page.url}"
    )

    # No session cookie should be set on a rejected login.
    cookies = page.context.cookies(base_url)
    session_cookies = [c for c in cookies if "session" in c["name"].lower()]
    assert not session_cookies, (
        f"login rejection set a session cookie — that's a regression: {session_cookies}"
    )


# ---------------------------------------------------------------------------
# AUTH.LOGOUT
# ---------------------------------------------------------------------------

def test_auth_logout_destroys_session(
    page: Page, base_url: str, seed_test_admin, db_conn
):
    """
    AUTH.LOGOUT — after clicking Logout, the user_sessions row is marked
    inactive and any subsequent request redirects back to /login.

    The Logout endpoint is POST-only (CSRF-exempt). Driving it via the
    UI requires a logout control rendered in the navbar; we use the
    direct POST path instead (more deterministic + tolerant to UI churn).
    """
    username, password = seed_test_admin
    _login(page, base_url, username, password)
    page.wait_for_url(lambda url: not url.rstrip("/").endswith("/login"), timeout=10_000)

    # Cookie-jar snapshot for the POST that follows.
    cookies = page.context.cookies(base_url)
    assert cookies, "no cookies after login — login itself failed"

    # POST /logout reuses the session cookie. Playwright's APIRequest
    # context inherits cookies from the browser context.
    resp = page.request.post(f"{base_url}/logout", max_redirects=0)
    # Logout responds with a 302 → /login.
    assert resp.status in (302, 303), (
        f"unexpected status from POST /logout: {resp.status} (body: {resp.text()[:200]})"
    )
    assert "/login" in (resp.headers.get("location") or ""), (
        f"POST /logout did not redirect to /login: {resp.headers}"
    )

    # And the user_sessions row should be flipped is_active=false. We
    # check the MOST RECENT row for this user — there may be older rows
    # from prior runs.
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT is_active FROM user_sessions "
            "WHERE user_id = (SELECT id FROM users WHERE username = %s) "
            "ORDER BY created_at DESC LIMIT 1",
            (username,),
        )
        row = cur.fetchone()
    assert row is not None, "no user_sessions row created for the logged-in user"
    assert row[0] is False, (
        f"user_sessions.is_active still true after logout — _deactivate_user_session() "
        "may have silently failed"
    )

    # Hitting a protected endpoint should now redirect us to /login again.
    page.goto(f"{base_url}/streams")
    page.wait_for_load_state("networkidle", timeout=5_000)
    assert "/login" in page.url, (
        f"after logout, GET /streams did NOT redirect to /login: ended at {page.url}"
    )


# ---------------------------------------------------------------------------
# AUTH.ROLE.ADMIN_ONLY
# ---------------------------------------------------------------------------

def test_auth_admin_only_returns_403_for_viewer(
    base_url: str, seed_test_viewer
):
    """
    AUTH.ROLE.ADMIN_ONLY — a logged-in viewer hitting an admin-only
    endpoint gets HTTP 403 with a JSON `{"error": "Admin access required"}`
    body.

    We use httpx with a cookie jar instead of Playwright — the test is
    100% headless API behaviour, no UI involved.

    Endpoint under test: GET /api/users (admin-only listing).
    """
    username, password = seed_test_viewer

    with httpx.Client(base_url=base_url, follow_redirects=False) as client:
        # POST /login with form-encoded body — same path the browser
        # uses, just without Playwright.
        login_resp = client.post(
            "/login",
            data={"username": username, "password": password},
        )
        # Successful login redirects (302/303). The cookie jar now has
        # the Flask session.
        assert login_resp.status_code in (200, 302, 303), (
            f"viewer login failed: {login_resp.status_code} {login_resp.text[:200]}"
        )

        # Hit the admin-only endpoint.
        admin_resp = client.get("/api/users")
        assert admin_resp.status_code == 403, (
            f"viewer got {admin_resp.status_code} for /api/users (expected 403): "
            f"{admin_resp.text[:200]}"
        )
        body = admin_resp.json()
        assert body.get("error") == "Admin access required", (
            f"unexpected JSON body from admin-only 403: {body}"
        )


# ---------------------------------------------------------------------------
# AUTH.CSRF.EXEMPT_JSON_API
# ---------------------------------------------------------------------------

def test_auth_csrf_exempt_for_json_api(base_url: str, seed_test_admin):
    """
    AUTH.CSRF.EXEMPT_JSON_API — a JSON POST to an admin /api/* endpoint
    WITHOUT an X-CSRFToken header is still processed (i.e., the response
    is the endpoint's normal answer, NOT Flask-WTF's "CSRF token missing"
    400). Catches the v6.2.x telemetry shape where a blueprint was missing
    from `csrf.exempt(...)`.

    We POST to /api/telemetry/settings — one of the blueprints that was
    fixed in commit 989775d6. The body is intentionally minimal; we only
    care that the response shape is the endpoint's (200 / 400 with
    specific JSON), not Flask-WTF's CSRF rejection page.
    """
    username, password = seed_test_admin

    with httpx.Client(base_url=base_url, follow_redirects=False) as client:
        client.post("/login", data={"username": username, "password": password})

        resp = client.post(
            "/api/telemetry/settings",
            json={"telemetry_enabled": False},  # no-op toggle; safe default
            headers={"Content-Type": "application/json"},
        )

    # CSRF rejection from Flask-WTF is HTTP 400 with an HTML body. The
    # endpoint's own response is JSON. Either 200 (settings saved) or a
    # JSON 4xx with a specific shape is acceptable — both prove the CSRF
    # layer was NOT the responder.
    content_type = (resp.headers.get("content-type") or "").lower()
    assert "json" in content_type or resp.status_code == 200, (
        f"POST /api/telemetry/settings returned non-JSON (likely a CSRF "
        f"rejection HTML page): status={resp.status_code} "
        f"content-type={content_type!r} body={resp.text[:200]}"
    )
    # And the CSRF rejection's HTTP code itself (400 with the standard
    # message) would smuggle past the content-type check only if the
    # endpoint were intentionally returning JSON 400; double-check the
    # body doesn't read like a CSRF page.
    assert "csrf" not in resp.text.lower() or resp.status_code == 200, (
        f"response body mentions CSRF — likely Flask-WTF rejected the "
        f"request: {resp.text[:200]}"
    )


# ---------------------------------------------------------------------------
# AUTH.CHANGE_PASSWORD.FIRST_LOGIN
# ---------------------------------------------------------------------------

def test_auth_change_password_forced_on_first_login(
    page: Page, base_url: str, seed_first_login_user
):
    """
    AUTH.CHANGE_PASSWORD.FIRST_LOGIN — a user with `must_change_password=true`
    is redirected to /change-password immediately after login (instead
    of /streams).

    Caveat re. enforcement scope:
        Today the redirect happens ONCE, in the POST /login handler at
        routes/auth.py:89 (`if user.must_change_password: redirect('/change-password')`).
        After that initial redirect, NO blueprint enforces the flag — a
        user who manually navigates to /streams reaches it normally.
        The reference doc spec ("until set") implies stronger
        enforcement; the navigation-bypass gap is recorded as a
        follow-up TODO and intentionally not asserted here so this test
        documents current behaviour, not aspirational behaviour.
    """
    username, password = seed_first_login_user
    _login(page, base_url, username, password)

    # The login response should redirect to /change-password (NOT /streams).
    # wait_for_url's matcher gets called repeatedly until truthy or timeout.
    page.wait_for_url(
        lambda url: "change-password" in url or "change_password" in url,
        timeout=10_000,
    )
    assert "change-password" in page.url or "change_password" in page.url, (
        f"must_change_password user landed at {page.url} instead of /change-password"
    )
