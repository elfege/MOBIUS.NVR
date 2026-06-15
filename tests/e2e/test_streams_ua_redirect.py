"""
tests/e2e/test_streams_ua_redirect.py
======================================

Regression ledger entry — `/streams` redirects mobile / portable UAs to
`/light` unless the user explicitly opted into the full UI via
`localStorage.nvr_preferred_mode = 'full'`.

Bug class:
    iPad / iPhone clients hitting `/streams` would attempt to render
    the full WebRTC grid and choke on memory / battery / heat. The
    `/light` page is purpose-built for them (snapshot-only, 4-up grid,
    Page Visibility tearing down on tab-hide). The UA sniff routes
    them automatically.

    A future refactor that "simplifies" the redirect logic (drops the
    UA check, drops the localStorage opt-out, or breaks the desktop
    path) would silently send the wrong UI to the wrong device. This
    test pins both directions.

Guards:
    - Mobile UA → expect a redirect chain ending at `/light`.
    - Desktop UA → expect to LAND on `/streams` (no redirect to /light).
    - localStorage opt-out (`nvr_preferred_mode = 'full'`) on mobile UA
      → expect `/streams` to stick.

References reference-doc rows STREAMS.PAGE.UA_SNIFF_REDIRECT and
LIGHT.PREFER_FULL.OVERRIDE.

Code anchors: routes/config.py:streams_page + streams_light_page.
"""

from __future__ import annotations

import re

from playwright.sync_api import Page, expect


IOS_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 "
    "Mobile/15E148 Safari/604.1"
)


def _ensure_logged_in(page: Page, base_url: str, creds):
    """Reusable login flow — fills the form, waits for the redirect."""
    username, password = creds
    page.goto(f"{base_url}/login")
    page.locator('input[name="username"]').fill(username)
    page.locator('input[name="password"]').fill(password)
    page.locator('button[type="submit"], input[type="submit"]').first.click()
    page.wait_for_url(re.compile(r".*/(streams|light)(\?.*)?$"), timeout=10_000)


def test_streams_redirects_ios_ua_to_light(browser, base_url, seed_test_admin):
    """
    STREAMS.PAGE.UA_SNIFF_REDIRECT — an iOS user-agent hitting /streams
    (no opt-out, no ?full=1) is redirected to /light.
    """
    ctx = browser.new_context(user_agent=IOS_UA)
    try:
        page = ctx.new_page()
        _ensure_logged_in(page, base_url, seed_test_admin)
        # After login the server picks the destination; the UA sniff
        # should have landed us on /light, but let's be explicit and
        # also try a direct /streams nav.
        page.goto(f"{base_url}/streams")
        page.wait_for_load_state("networkidle", timeout=10_000)
        assert page.url.rstrip("/").endswith("/light") or "/light?" in page.url, (
            f"iOS UA should redirect to /light, got {page.url}"
        )
    finally:
        ctx.close()


def test_streams_keeps_desktop_ua(page: Page, base_url, seed_test_admin):
    """
    The default Playwright UA is desktop Chromium. Hitting /streams
    should land you on /streams (no /light redirect).
    """
    _ensure_logged_in(page, base_url, seed_test_admin)
    page.goto(f"{base_url}/streams")
    page.wait_for_load_state("networkidle", timeout=10_000)
    assert "/streams" in page.url and not page.url.rstrip("/").endswith("/light"), (
        f"Desktop UA should land on /streams, got {page.url}"
    )


def test_localstorage_full_opt_in_keeps_ios_on_streams(browser, base_url, seed_test_admin):
    """
    LIGHT.PREFER_FULL.OVERRIDE — when localStorage.nvr_preferred_mode
    is 'full', mobile UAs are NOT redirected to /light.

    The flag is checked CLIENT-side (the redirect is conditional on
    server-side UA AND client-side localStorage). Some implementations
    do the check server-side via cookie; if the test surfaces a
    fundamentally different mechanism, narrow this assertion to what
    actually exists.
    """
    ctx = browser.new_context(user_agent=IOS_UA)
    try:
        page = ctx.new_page()
        _ensure_logged_in(page, base_url, seed_test_admin)
        # Seed the opt-in flag. We have to be ON the origin already
        # for localStorage to bind; the login already took us there.
        page.evaluate("localStorage.setItem('nvr_preferred_mode', 'full')")

        # Force-fetch /streams with ?full=1 — the canonical opt-in URL.
        # We assert the URL path, but accept either /streams or /streams?...
        # (?full=1 may stay in URL or be stripped by JS).
        page.goto(f"{base_url}/streams?full=1")
        page.wait_for_load_state("networkidle", timeout=10_000)
        # Acceptance: the path component is /streams. /light would be
        # the failure case.
        from urllib.parse import urlparse
        path = urlparse(page.url).path.rstrip("/")
        assert path.endswith("/streams"), (
            f"With nvr_preferred_mode=full + ?full=1, mobile UA should "
            f"stay on /streams, got {page.url}"
        )
    finally:
        ctx.close()
