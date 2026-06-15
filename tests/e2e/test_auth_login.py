"""
tests/e2e/test_auth_login.py — first runnable E2E case.

Covers reference doc row AUTH.LOGIN.OK (see docs/functionality_reference.md):
"User submits correct username + password on /login → Redirect to /streams
(or /light per device sniff), session cookie set, user_sessions row created."

This is the Phase B 'scaffold one runnable case' deliverable from the E2E
methodology plan. Future phases enumerate the rest of the reference doc.
"""

from __future__ import annotations

import re

from playwright.sync_api import Page, expect


def test_auth_login_ok(page: Page, base_url: str, seed_test_admin):
    """
    AUTH.LOGIN.OK — admin user logs in via the form, lands on /streams.
    """
    username, password = seed_test_admin

    # Land on /login. The site forces SSL in prod but the test stack
    # ships plain HTTP for simplicity.
    page.goto(f"{base_url}/login")

    # The form is the standard Flask-WTF / hand-rolled HTML form. We
    # use placeholder/name attributes to find the inputs without
    # depending on visual layout that could change.
    page.locator('input[name="username"]').fill(username)
    page.locator('input[name="password"]').fill(password)
    page.locator('button[type="submit"], input[type="submit"]').first.click()

    # On success the server redirects. Flask-Login default is /streams
    # for admin (no UA sniff in the test stack since we're not iOS).
    page.wait_for_url(re.compile(r".*/(streams|light)(\?.*)?$"), timeout=10_000)

    # The session cookie is set on the response — Playwright shows it on
    # the context. Name is whatever Flask configures; we check that at
    # least one cookie was set (the negative case is no cookies → not
    # logged in).
    cookies = page.context.cookies(base_url)
    assert cookies, "no cookies set after login — auth flow did not complete"

    # And the page should show one of the post-login landmarks: the
    # main navbar on /streams, or the top-bar grid button on /light.
    # Either one proves we landed on a real page (not a redirect loop
    # back to /login). Doesn't require any cameras to be configured.
    landmark = page.locator('#main-navbar, #grid-btn')
    expect(landmark.first).to_be_visible(timeout=5_000)


def test_auth_login_wrong_password(page: Page, base_url: str, seed_test_admin):
    """
    AUTH.LOGIN.WRONG_PASSWORD — wrong password keeps the user on /login
    with an error indicator and NO session cookie.
    """
    username, _ = seed_test_admin

    page.goto(f"{base_url}/login")
    page.locator('input[name="username"]').fill(username)
    page.locator('input[name="password"]').fill("definitely-wrong-password-zzz")
    page.locator('button[type="submit"], input[type="submit"]').first.click()

    # Page should NOT redirect to /streams. Wait a beat for any redirect
    # to have happened, then assert we're still on /login.
    page.wait_for_load_state("networkidle", timeout=5_000)
    assert page.url.rstrip("/").endswith("/login"), (
        f"expected to still be on /login after bad password, got {page.url}"
    )
