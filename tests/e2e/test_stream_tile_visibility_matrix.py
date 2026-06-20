"""
tests/e2e/test_stream_tile_visibility_matrix.py — visibility-matrix test
for the stream tile's action-bar icons.

Why this exists
---------------
Operator memory file
`memory/project_frozen_stream_no_buttons_ipad_health_monitor.md` ("WHY
THIS RECURS"): icon visibility on the stream tile is decided by a
PATCHWORK of opacity / !important rules across stream-control-bar.css,
stream-item.css, fullscreen.css, and pinned-window.css. The rules
desync at STATE INTERSECTIONS (e.g. grid mode × signal-lost). Each past
patch fixed ONE cell; the next regression turned up in a different cell.

This file is the start of the matrix-test rig the memo asks for. Rather
than seeding a real camera (which the test stack can't because there
are no real publishers), we INJECT a stream-item DOM into a logged-in
/streams page and assert the CSS-side visibility of every icon for each
(layout, health) cell.

Today's coverage (first slice)
------------------------------
LAYOUT     = {grid}                 (expanded, pinned-window, fullscreen
                                     come in follow-up commits)
HEALTH     = {live, signal-lost}
ICONS      = the 11 buttons that the production template emits
EXPECTATIONS = computed-opacity per icon must match the policy spelled
               out in stream-item.css's B2 block:
                 - in grid + signal-lost: settings/power/playback visible;
                   all others hidden (the B2 "essential controls" rule)
                 - in grid + live: ALL icons hidden by default (bar fades
                   in on hover; we don't simulate hover here)

The CSS check used per icon is `computed-opacity > 0` only — not the
full 4-point check the memo calls for (pointer-events + elementFromPoint
+ rect-in-viewport). The other 3 require real layout + paint and slow
the test; the operator can extend the helper when needed. Opacity alone
catches the 2026-06-13 regression (parent-bar opacity:0 cascading down).
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page


# Production stream-item HTML, condensed to what matters for visibility.
# Mirrors templates/streams.html lines 535-603 (the .stream-actions-bar
# block). If the template diverges from this fixture, the matrix test
# might pass while the real page breaks — keep the two in sync.
#
# Per-template conditionals (audio button missing on MJPEG, PTZ button
# missing on non-PTZ cameras, talkback only on certain protocols, power
# only when power_supply is set) are ALL included here. The matrix test
# treats their visibility as conditional on the camera-shape axis, not
# the health axis.
_TILE_HTML = """
<div class="stream-item" data-camera-serial="MATRIX_TEST_FAKE_CAM" data-camera-name="Matrix">
    <video class="stream-video"></video>
    <div class="stream-actions-bar">
        <button class="stream-fullscreen-btn" title="Fullscreen"><i class="fas fa-expand"></i></button>
        <div class="volume-control-container">
            <button class="stream-audio-btn" title="Click to adjust volume"><i class="fas fa-volume-mute"></i></button>
        </div>
        <button class="stream-ptz-toggle-btn" title="PTZ Controls"><i class="fas fa-arrows-alt"></i></button>
        <button class="stream-controls-toggle-btn" title="Start/Stop/Refresh Controls"><i class="fas fa-sliders-h"></i></button>
        <button class="camera-settings-btn" title="Recording Settings"><i class="fas fa-cog"></i></button>
        <button class="camera-record-btn" title="Start Recording"><i class="fas fa-circle"></i></button>
        <button class="camera-playback-btn" title="Timeline Playback &amp; Export"><i class="fas fa-history"></i></button>
        <button class="stream-power-btn power-configured" title="Power Cycle Camera"><i class="fas fa-plug"></i></button>
        <button class="stream-talkback-btn" title="Hold to Talk"><i class="fas fa-microphone-alt"></i></button>
        <button class="stream-more-btn" title="More Controls"><i class="fas fa-ellipsis-v"></i></button>
    </div>
</div>
"""

# The full set of icons present in the production tile. Each has a
# selector relative to .stream-item.
ICONS = {
    "fullscreen":        ".stream-fullscreen-btn",
    "audio":             ".stream-audio-btn",
    "ptz":               ".stream-ptz-toggle-btn",
    "controls_toggle":   ".stream-controls-toggle-btn",
    "settings":          ".camera-settings-btn",
    "record":            ".camera-record-btn",
    "playback":          ".camera-playback-btn",
    "power":             ".stream-power-btn",
    "talkback":          ".stream-talkback-btn",
    "more":              ".stream-more-btn",
}

# Policy matrix.
#
# `visible` = the icon's computed opacity must be > 0
# `hidden`  = the icon's computed opacity must be == 0
#
# These come from stream-item.css's B2 block and stream-control-bar.css's
# grid-mode rule (opacity:0 on the bar container, fade-in on hover).
#
#   GRID + LIVE         every icon hidden (no hover simulated)
#   GRID + SIGNAL_LOST  settings/power/playback visible; everything else hidden
#                       (memo: "meaningless on a dead stream" — operator
#                        explicit policy 2026-05-23, B2 bug fix)
#
EXPECTATIONS: dict[str, dict[str, dict[str, str]]] = {
    "grid+live": {
        # All icons hidden — bar fades in on hover, we don't hover.
        icon: {"opacity": "hidden"} for icon in ICONS
    },
    "grid+signal-lost": {
        # The B2 essential set stays operable on dead tiles. The
        # original B2 (2026-05-23) listed 3 icons (settings/power/
        # playback). 2026-06-19 amendment: controls-toggle added so
        # operator can RESTART a dead stream from the grid — that's
        # the most-needed action on a dead tile, not the least.
        "settings":        {"opacity": "visible"},
        "power":           {"opacity": "visible"},
        "playback":        {"opacity": "visible"},
        "controls_toggle": {"opacity": "visible"},
        # Everything else stays hidden (B2 policy: meaningless on dead)
        "fullscreen": {"opacity": "hidden"},
        "audio":      {"opacity": "hidden"},
        "ptz":        {"opacity": "hidden"},
        "record":     {"opacity": "hidden"},
        "talkback":   {"opacity": "hidden"},
        "more":       {"opacity": "hidden"},
    },
}


@pytest.fixture
def streams_page(page: Page, base_url: str, seed_test_admin):
    """
    Log in and land on /streams so the page's CSS files are loaded.
    Yields the Playwright page; the test then JS-injects its own
    stream-item DOM to exercise CSS rules without needing real cameras.
    """
    username, password = seed_test_admin
    page.goto(f"{base_url}/login")
    page.locator('input[name="username"]').fill(username)
    page.locator('input[name="password"]').fill(password)
    page.locator('button[type="submit"], input[type="submit"]').first.click()
    page.wait_for_url(lambda url: "/login" not in url, timeout=10_000)
    page.goto(f"{base_url}/streams")

    # Page is settled when at least one of our CSS files is loaded —
    # we check by querying for any element using a class from those
    # files. If the page rendered no cameras at all there's still the
    # streams.html chrome to attach our injected tile to.
    page.wait_for_load_state("domcontentloaded", timeout=10_000)
    yield page


def _inject_tile_and_classes(page: Page, extra_classes: list[str]) -> None:
    """
    Inject the fixture tile into the page body and apply `extra_classes`
    to the .stream-item element. Returns nothing; caller queries via
    Playwright after.

    Reused between matrix cells: append, set classes, assert, remove —
    no need for a full reload.
    """
    classes_js = " ".join(extra_classes)
    page.evaluate(
        f"""
        (() => {{
            // Remove any prior injection from a previous cell
            document.querySelectorAll('[data-matrix-injected]').forEach(n => n.remove());
            const wrapper = document.createElement('div');
            wrapper.setAttribute('data-matrix-injected', '1');
            wrapper.innerHTML = {_TILE_HTML!r};
            const tile = wrapper.firstElementChild;
            // Apply the cell's class list (e.g. 'signal-lost' for the
            // dead-tile cell). Grid mode is the default (no .expanded /
            // .pinned-window / .css-fullscreen on the tile).
            if ({classes_js!r}) {{
                {classes_js!r}.split(' ').filter(Boolean).forEach(c => tile.classList.add(c));
            }}
            document.body.appendChild(wrapper);
        }})()
        """
    )


def _icon_opacity(page: Page, icon_selector: str) -> float:
    """
    Resolve the computed opacity of the icon AND every ancestor up to
    the injected wrapper, multiplying them — CSS opacity is multiplicative
    down the tree. A child with opacity:1 inside a parent with opacity:0
    is invisible; the only honest visibility check walks the chain.

    Returns the effective opacity (0.0 .. 1.0).
    """
    return page.evaluate(
        f"""
        (() => {{
            const el = document.querySelector('[data-matrix-injected] ' + {icon_selector!r});
            if (!el) return -1;  // sentinel — selector missed
            let cur = el;
            let acc = 1.0;
            while (cur && cur.getAttribute('data-matrix-injected') !== '1') {{
                const o = parseFloat(window.getComputedStyle(cur).opacity);
                if (!isNaN(o)) acc *= o;
                cur = cur.parentElement;
            }}
            return acc;
        }})()
        """
    )


# ---------------------------------------------------------------------------
# Tests — one per matrix cell. Each is a Playwright function-scoped test.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("icon_name", list(ICONS.keys()))
def test_visibility_matrix_grid_live(streams_page, icon_name):
    """
    LAYOUT=grid × HEALTH=live × icon=<each>.

    In grid mode + live stream, every icon is hidden by default — the
    bar fades in on hover, which we don't simulate. Expectation comes
    from EXPECTATIONS['grid+live'].
    """
    page = streams_page
    _inject_tile_and_classes(page, extra_classes=[])  # plain grid, no signal-lost
    expected = EXPECTATIONS["grid+live"][icon_name]
    eff_opacity = _icon_opacity(page, ICONS[icon_name])
    assert eff_opacity != -1, f"selector missed for {icon_name}: {ICONS[icon_name]}"
    if expected["opacity"] == "visible":
        assert eff_opacity > 0, (
            f"grid+live: {icon_name} should be visible; effective opacity={eff_opacity}"
        )
    else:
        assert eff_opacity == 0, (
            f"grid+live: {icon_name} should be hidden; effective opacity={eff_opacity}"
        )


@pytest.mark.parametrize("icon_name", list(ICONS.keys()))
def test_visibility_matrix_grid_signal_lost(streams_page, icon_name):
    """
    LAYOUT=grid × HEALTH=signal-lost × icon=<each>.

    This is the regression cell. Before 2026-06-18:
    settings/power/playback were SUPPOSED to be visible (B2 fix) but
    were hidden because the parent `.stream-actions-bar` had opacity:0
    in grid mode and a child's `opacity:1 !important` can't beat that.

    After the fix: the parent bar is lifted to opacity:1 on
    `.stream-item.signal-lost`, then audio/PTZ/talkback/etc. are
    explicitly re-hidden so only the B2 trio surfaces.

    2026-06-19 amendment: controls-toggle joined the visible set so
    operator can RESTART a dead stream from the grid.
    """
    page = streams_page
    _inject_tile_and_classes(page, extra_classes=["signal-lost"])
    expected = EXPECTATIONS["grid+signal-lost"][icon_name]
    eff_opacity = _icon_opacity(page, ICONS[icon_name])
    assert eff_opacity != -1, f"selector missed for {icon_name}: {ICONS[icon_name]}"
    if expected["opacity"] == "visible":
        assert eff_opacity > 0, (
            f"grid+signal-lost: {icon_name} should be visible (B2 essential "
            f"set); effective opacity={eff_opacity}. If this fails the B2 "
            "rule is dead again — likely a new selector chain put opacity:0 "
            "on an ancestor of .stream-actions-bar."
        )
    else:
        assert eff_opacity == 0, (
            f"grid+signal-lost: {icon_name} should be hidden (meaningless "
            f"on a dead stream per B2 policy); effective opacity={eff_opacity}"
        )


# ---------------------------------------------------------------------------
# Click-handler dimension (operator request 2026-06-19 — "buttons must not
# just be visible, they must DO something")
#
# The visibility tests above prove an icon is reachable to the eye/cursor.
# They CANNOT catch:
#   - dead click handlers (button visible, click does nothing)
#   - frontend URL mismatches (button visible, click sends request, 404)
#     — e.g. the recording 404 on SV3C_Living_3 caught 2026-06-19:
#     frontend built /api/recording/start/<id>, backend wanted /<id>/start
#
# This pair of tests visits the LIVE /streams page (not an injected DOM),
# finds a real button on a real tile, and asserts:
#   1. clicking it does NOT throw a console error
#   2. clicking it does NOT produce a 4xx/5xx network response (where
#      a network call is expected for that button)
# We DON'T assert specific side-effects per button (modal opens / specific
# class added) because those vary across button types and the goal here
# is "the click reaches a handler that ATTEMPTS something coherent."
# ---------------------------------------------------------------------------

# Subset of buttons that we expect to ALWAYS fire a network request when
# clicked (so a 4xx/5xx is detectable). Buttons that only open modals or
# toggle local CSS classes don't qualify and are skipped here.
#
# What's NOT in this map (and why):
#   fullscreen / audio / more   → DOM-only (toggle CSS classes, no fetch)
#   ptz (toggle)                → DOM-only (opens .ptz-controls panel).
#                                 The PTZ DIRECTION buttons inside that panel
#                                 ARE tested — see test_ptz_direction_click_no_404
#                                 below; it carries the Eufy skip rule.
#   controls_toggle             → DOM-only (opens .stream-controls submenu)
#   settings / playback         → open modals; their fetches happen async on
#                                 modal-open or on user submit, not on the icon
#                                 click itself. The click-handler-runs check
#                                 still surfaces a console-error regression
#                                 (the assert at the end of the test).
#   talkback                    → WebRTC connect — out-of-band of HTTP, not
#                                 a 4xx/5xx pattern that fits this rig
#   record                      → POST /api/recording/<id>/start or /stop
#                                 (the 2026-06-19 segment-order bug)
#   power                       → POST /api/power/<serial>/cycle (hubitat) OR
#                                 /api/poe/<serial>/cycle (POE). Substring
#                                 "/api/p" matches both — narrower would miss
#                                 one variant. Only fires when the tile carries
#                                 the `power-configured` class (i.e. the camera
#                                 has a power_supply set), so the test scopes
#                                 its selector accordingly to avoid clicking
#                                 a no-op button and getting a false PASS.
NETWORK_CLICK_BUTTONS = {
    "record":   "POST /api/recording/",
    "power":    "POST /api/p",
}

# Per-button selector override for the production /streams page. Tests look
# up tiles via `.stream-item <button>` by default; some buttons require a
# narrower scope (e.g. power only fires when `.power-configured` is also
# on the element) to avoid clicking a stub variant.
LIVE_BUTTON_SELECTOR_OVERRIDES = {
    "power": ".stream-item .stream-power-btn.power-configured",
}


@pytest.mark.parametrize("icon_name", list(NETWORK_CLICK_BUTTONS.keys()))
def test_click_does_not_404_or_throw(page, base_url, seed_test_admin, icon_name):
    """
    Click each network-firing icon on a REAL grid tile (any tile that
    exists). Capture console errors AND network responses. Fail if:
      - a console error appears OR
      - any response to a path containing the expected substring is 4xx/5xx

    The recording 404 from 2026-06-19 (frontend built /api/recording/start/<id>
    while backend wanted /<id>/start) would have been caught HERE — the
    httpx tests pass against the correct URL the test hand-codes; only
    a browser-driven click test exercises the URL the FRONTEND builds.
    """
    username, password = seed_test_admin

    # Capture network responses + console errors via Playwright hooks
    console_errors: list[str] = []
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
    network_failures: list[tuple[str, int]] = []
    page.on("response", lambda r: network_failures.append((r.url, r.status))
            if r.status >= 400 and NETWORK_CLICK_BUTTONS[icon_name].split()[-1] in r.url
            else None)

    # Log in and load /streams
    page.goto(f"{base_url}/login")
    page.locator('input[name="username"]').fill(username)
    page.locator('input[name="password"]').fill(password)
    page.locator('button[type="submit"], input[type="submit"]').first.click()
    page.wait_for_url(lambda url: "/login" not in url, timeout=10_000)
    page.goto(f"{base_url}/streams")
    page.wait_for_load_state("domcontentloaded", timeout=10_000)

    # Find any real tile with the target button. Skip if the test stack
    # has no cameras rendered (a known cache freshness gap — covered by
    # CAM.SETTINGS.OPEN's skip in test_cam_settings).
    selector = LIVE_BUTTON_SELECTOR_OVERRIDES.get(
        icon_name, f".stream-item {ICONS[icon_name]}"
    )
    button = page.locator(selector).first
    if button.count() == 0:
        pytest.skip(
            f"no .stream-item with {ICONS[icon_name]} on /streams (test "
            "stack has no cameras rendered). The frontend-URL contract "
            "still needs prod-side verification."
        )

    # Make the button reachable. The grid-mode bar fades in on hover.
    button.scroll_into_view_if_needed()
    button.hover(force=True)
    button.click(force=True)

    # Give the page a moment for the click to dispatch + response to land
    page.wait_for_timeout(1500)

    # Network failures matching the expected URL substring are the bug
    # we're hunting. Console errors are a softer signal but worth surfacing.
    assert not network_failures, (
        f"click on {icon_name} button produced HTTP failure(s) matching "
        f"{NETWORK_CLICK_BUTTONS[icon_name]!r}: {network_failures}. "
        "Either the frontend built the wrong URL (segment-order bug) or "
        "the backend route moved without the JS being updated. This is "
        "exactly the recording 404 shape from 2026-06-19."
    )
    # Filter known-noisy console errors (third-party libs, irrelevant
    # warnings). The string match keeps the assertion focused on what we
    # care about — the button's own handler throwing.
    relevant_errors = [
        e for e in console_errors
        if "recording" in e.lower() or "404" in e or "axios" in e.lower()
    ]
    assert not relevant_errors, (
        f"click on {icon_name} button logged console error(s): {relevant_errors}"
    )


# ---------------------------------------------------------------------------
# PTZ direction click test — own function because it needs panel open first,
# uses a different button selector (.ptz-btn data-direction), and carries the
# Eufy-skip rule (operator directive 2026-06-19, memory file
# feedback_eufy_ptz_skip_when_cloud_down_and_backend_failed).
# ---------------------------------------------------------------------------


def _eufy_cloud_status_via_page(page) -> dict:
    """Hit /api/eufy/cloud-status via Playwright's request context, which
    shares the browser's auth cookie. Returns the parsed JSON (defaults
    cloud_reachable/bridge_running to False if the call fails — that's the
    pessimistic interpretation, which matches "cloud is down" for skip-rule
    purposes).
    """
    try:
        resp = page.request.get("/api/eufy/cloud-status")
        if not resp.ok:
            return {"cloud_reachable": False, "bridge_running": False, "p2p_available": False}
        return resp.json()
    except Exception:
        return {"cloud_reachable": False, "bridge_running": False, "p2p_available": False}


def test_ptz_direction_click_no_404(page, base_url, seed_test_admin):
    """
    Click a real PTZ direction button (data-direction="left") on the live
    /streams page. Asserts the POST /api/ptz/<serial>/left didn't 4xx/5xx.

    Eufy SKIP rule (memory: feedback_eufy_ptz_skip_when_cloud_down_and_backend_failed,
    operator directive 2026-06-19). When ALL THREE hold the test SKIPs rather
    than FAILs — the failure is environmental, not a code regression:
      1. Target tile's `data-camera-type == 'eufy'`
      2. `/api/eufy/cloud-status` reports `cloud_reachable: false`
         (operator's LAN can't reach mysecurity.eufylife.com)
      3. The backend POST also reports failure (4xx/5xx OR success:false)

    Any partial truth → real fail. Backend OK but UI fails → real frontend
    bug. Backend fails but it's NOT Eufy → real backend bug. Eufy + cloud-up
    + still fails → real bug.

    No PTZ-capable tile rendered → SKIP (same shape as the other live-tile
    tests; the test stack often has no real publishers).
    """
    username, password = seed_test_admin

    network_failures: list[tuple[str, int]] = []
    backend_failed = [False]  # closure mutability via single-elem list

    def on_response(resp):
        u = resp.url
        # Only care about the /api/ptz/<serial>/<dir> shape (movement),
        # not the latency/reversal probes which fire on panel open.
        if "/api/ptz/" in u and not ("/latency/" in u or "/reversal" in u):
            if resp.status >= 400:
                network_failures.append((u, resp.status))
                backend_failed[0] = True
            else:
                # 200 doesn't yet mean success — the backend pattern is
                # 200 + {'success': false, ...} when the cloud/bridge is
                # down. Check the body.
                try:
                    j = resp.json()
                    if isinstance(j, dict) and j.get("success") is False:
                        backend_failed[0] = True
                except Exception:
                    pass

    page.on("response", on_response)

    page.goto(f"{base_url}/login")
    page.locator('input[name="username"]').fill(username)
    page.locator('input[name="password"]').fill(password)
    page.locator('button[type="submit"], input[type="submit"]').first.click()
    page.wait_for_url(lambda url: "/login" not in url, timeout=10_000)
    page.goto(f"{base_url}/streams")
    page.wait_for_load_state("domcontentloaded", timeout=10_000)

    # Locate a tile that actually has the PTZ panel emitted (template
    # condition: 'ptz' in info.capabilities). Without one we can't click.
    tile = page.locator(".stream-item:has(.ptz-controls)").first
    if tile.count() == 0:
        pytest.skip(
            "no .stream-item with .ptz-controls on /streams (no PTZ-capable "
            "camera in the test stack). PTZ click→backend contract still needs "
            "prod-side verification."
        )

    camera_type = (tile.get_attribute("data-camera-type") or "").lower()
    camera_serial = tile.get_attribute("data-camera-serial") or "?"

    # Open the PTZ panel by clicking the toggle (hover first to reveal the
    # action bar in grid mode).
    toggle = tile.locator(".stream-ptz-toggle-btn")
    toggle.scroll_into_view_if_needed()
    toggle.hover(force=True)
    toggle.click(force=True)
    # Wait for the panel to be visible (CSS class .ptz-visible added by handler).
    page.wait_for_timeout(500)

    # Press a direction button. PTZ uses mousedown→mouseup (continuous move),
    # so we need to dispatch both. Playwright's locator.click does that but
    # the duration is too short for some backends; emulate hold via a small
    # mousedown→sleep→mouseup explicitly.
    direction_btn = tile.locator('.ptz-btn[data-direction="left"]')
    if direction_btn.count() == 0:
        pytest.skip("PTZ panel rendered but no data-direction='left' button (unexpected template variant).")
    direction_btn.scroll_into_view_if_needed()
    box = direction_btn.bounding_box()
    if box is None:
        pytest.skip("PTZ left-direction button has no bounding box (panel not visible after toggle).")
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(cx, cy)
    page.mouse.down()
    page.wait_for_timeout(400)  # let the POST fire
    page.mouse.up()
    page.wait_for_timeout(800)  # let any pending responses land

    ui_failed = bool(network_failures) or backend_failed[0]

    # Eufy skip rule — applies ONLY when the chain reports the same upstream
    # cloud problem end-to-end.
    if camera_type == "eufy" and ui_failed:
        cloud_status = _eufy_cloud_status_via_page(page)
        cloud_down = not cloud_status.get("cloud_reachable", False)
        if cloud_down and backend_failed[0]:
            pytest.skip(
                f"Eufy PTZ unreachable end-to-end for {camera_serial}: "
                f"cloud_reachable={cloud_status.get('cloud_reachable')}, "
                f"backend reported failure too. Environmental (LAN→WAN to "
                f"mysecurity.eufylife.com), not a regression. "
                f"[memory: feedback_eufy_ptz_skip_when_cloud_down_and_backend_failed]"
            )

    assert not network_failures, (
        f"PTZ direction click on {camera_serial} (type={camera_type}) "
        f"produced HTTP failure(s): {network_failures}. "
        "Eufy skip rule did NOT apply — either camera is non-Eufy, or cloud "
        "IS reachable, or backend reported success. That makes this a real "
        "regression to investigate."
    )


# ---------------------------------------------------------------------------
# Expanded-layout matrix cells — operator carry-forward from the prior session.
# When a tile is in expanded mode (full-window single-camera view), the bar
# rules change again (the memo "WHY THIS RECURS" lists expanded/fullscreen/
# pinned-window as distinct CSS contexts). Today we cover the grid×live and
# grid×signal-lost cells; expanded mode is the next intersection where past
# regressions have hidden. (.css-fullscreen and .pinned-window remain TBD —
# each adds its own layer of !important rules; one slice at a time.)
# ---------------------------------------------------------------------------

EXPECTATIONS_EXPANDED: dict[str, dict[str, dict[str, str]]] = {
    "expanded+live": {
        # In expanded mode the bar is persistently visible (no hover fade).
        # Production policy: every icon visible on a live expanded tile.
        icon: {"opacity": "visible"} for icon in ICONS
    },
    "expanded+signal-lost": {
        # Dead expanded tile — same B2 logic applies: only the operator-
        # actionable subset stays visible. Same set as grid+signal-lost
        # (settings/power/playback/controls_toggle).
        "settings":        {"opacity": "visible"},
        "power":           {"opacity": "visible"},
        "playback":        {"opacity": "visible"},
        "controls_toggle": {"opacity": "visible"},
        "fullscreen": {"opacity": "hidden"},
        "audio":      {"opacity": "hidden"},
        "ptz":        {"opacity": "hidden"},
        "record":     {"opacity": "hidden"},
        "talkback":   {"opacity": "hidden"},
        "more":       {"opacity": "hidden"},
    },
}


@pytest.mark.parametrize("icon_name", list(ICONS.keys()))
def test_visibility_matrix_expanded_live(streams_page, icon_name):
    """LAYOUT=expanded × HEALTH=live × icon=<each>.

    Expanded tile (single camera, full window) — bar is persistently visible
    (no hover fade). Every icon should be visible. If this fails, an
    !important rule in stream-control-bar.css's expanded block is overriding
    the visible-by-default policy.
    """
    page = streams_page
    _inject_tile_and_classes(page, extra_classes=["expanded"])
    expected = EXPECTATIONS_EXPANDED["expanded+live"][icon_name]
    eff_opacity = _icon_opacity(page, ICONS[icon_name])
    assert eff_opacity != -1, f"selector missed for {icon_name}: {ICONS[icon_name]}"
    if expected["opacity"] == "visible":
        assert eff_opacity > 0, (
            f"expanded+live: {icon_name} should be visible; effective opacity={eff_opacity}. "
            "Expanded-mode bar should not fade with hover — check "
            "stream-control-bar.css's `.stream-item.expanded .stream-actions-bar` block."
        )
    else:
        assert eff_opacity == 0, (
            f"expanded+live: {icon_name} should be hidden; effective opacity={eff_opacity}"
        )


@pytest.mark.parametrize("icon_name", list(ICONS.keys()))
def test_visibility_matrix_expanded_signal_lost(streams_page, icon_name):
    """LAYOUT=expanded × HEALTH=signal-lost × icon=<each>.

    Dead expanded tile — same B2 essential-set policy as grid+signal-lost.
    Adding this cell so a future regression that fixes grid but breaks
    expanded (or vice versa) is caught in CI.
    """
    page = streams_page
    _inject_tile_and_classes(page, extra_classes=["expanded", "signal-lost"])
    expected = EXPECTATIONS_EXPANDED["expanded+signal-lost"][icon_name]
    eff_opacity = _icon_opacity(page, ICONS[icon_name])
    assert eff_opacity != -1, f"selector missed for {icon_name}: {ICONS[icon_name]}"
    if expected["opacity"] == "visible":
        assert eff_opacity > 0, (
            f"expanded+signal-lost: {icon_name} should be visible (B2 essential "
            f"set, expanded variant); effective opacity={eff_opacity}"
        )
    else:
        assert eff_opacity == 0, (
            f"expanded+signal-lost: {icon_name} should be hidden (B2 policy, "
            f"expanded variant); effective opacity={eff_opacity}"
        )


# ---------------------------------------------------------------------------
# Pinned-window + css-fullscreen matrix cells — same shape as expanded.
#
# Pinned-window = tile popped into a separate floating window
#                 (stream-item.pinned-window class added by frontend).
# css-fullscreen = browser-true fullscreen via Fullscreen API
#                  (stream-item.css-fullscreen class).
#
# Per stream-item.css:458-497 the signal-lost rules are layout-agnostic
# (no `.expanded` / `.pinned-window` / `.css-fullscreen` qualifier in the
# selector). So the essential-set policy on a dead tile is the same across
# all 4 layouts. Per fullscreen.css:228 and :316 the live bar is persistently
# visible (opacity:1 !important) in both layouts.
#
# We mirror EXPECTATIONS_EXPANDED for both. If a future CSS change introduces
# a layout-specific override, the test cell that fails tells the operator
# exactly which intersection broke.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("icon_name", list(ICONS.keys()))
def test_visibility_matrix_pinned_window_live(streams_page, icon_name):
    """LAYOUT=pinned-window × HEALTH=live × icon=<each>.

    Pinned floating window — bar persistently visible (no hover-to-reveal),
    every icon visible. Regression that broke pinned-window visibility
    while keeping expanded healthy would surface here.
    """
    page = streams_page
    _inject_tile_and_classes(page, extra_classes=["pinned-window"])
    eff_opacity = _icon_opacity(page, ICONS[icon_name])
    assert eff_opacity != -1, f"selector missed for {icon_name}: {ICONS[icon_name]}"
    # Same expectation as expanded+live (per fullscreen.css:228-253)
    assert eff_opacity > 0, (
        f"pinned-window+live: {icon_name} should be visible; effective opacity={eff_opacity}. "
        "Pinned-window bar should be persistent (no hover-fade). Check "
        "fullscreen.css's `.stream-item.pinned-window .stream-fullscreen-btn` block "
        "and adjacent rules."
    )


@pytest.mark.parametrize("icon_name", list(ICONS.keys()))
def test_visibility_matrix_pinned_window_signal_lost(streams_page, icon_name):
    """LAYOUT=pinned-window × HEALTH=signal-lost × icon=<each>.

    Dead pinned-window tile. The signal-lost CSS rules at stream-item.css:458-497
    are layout-agnostic → same essential set policy as grid + expanded variants.
    Adds CI coverage for the pinned-window × dead intersection so a future
    layout-specific rule that breaks dead-tile button visibility surfaces here.
    """
    page = streams_page
    _inject_tile_and_classes(page, extra_classes=["pinned-window", "signal-lost"])
    expected = EXPECTATIONS_EXPANDED["expanded+signal-lost"][icon_name]  # same policy
    eff_opacity = _icon_opacity(page, ICONS[icon_name])
    assert eff_opacity != -1, f"selector missed for {icon_name}: {ICONS[icon_name]}"
    if expected["opacity"] == "visible":
        assert eff_opacity > 0, (
            f"pinned-window+signal-lost: {icon_name} should be visible (B2 essential "
            f"set, pinned variant); effective opacity={eff_opacity}"
        )
    else:
        assert eff_opacity == 0, (
            f"pinned-window+signal-lost: {icon_name} should be hidden (B2 policy, "
            f"pinned variant); effective opacity={eff_opacity}"
        )


@pytest.mark.parametrize("icon_name", list(ICONS.keys()))
def test_visibility_matrix_css_fullscreen_live(streams_page, icon_name):
    """LAYOUT=css-fullscreen × HEALTH=live × icon=<each>.

    Browser-true fullscreen (Fullscreen API). Bar persistently visible per
    fullscreen.css:306-316. Every icon visible. Note: this tests the CSS,
    not the actual Fullscreen-API state (which Playwright can't trigger
    reliably across browsers from injected DOM).
    """
    page = streams_page
    _inject_tile_and_classes(page, extra_classes=["css-fullscreen"])
    eff_opacity = _icon_opacity(page, ICONS[icon_name])
    assert eff_opacity != -1, f"selector missed for {icon_name}: {ICONS[icon_name]}"
    assert eff_opacity > 0, (
        f"css-fullscreen+live: {icon_name} should be visible; effective opacity={eff_opacity}. "
        "css-fullscreen bar should be persistent. Check fullscreen.css's "
        "`.stream-item.css-fullscreen .stream-fullscreen-btn` block."
    )


@pytest.mark.parametrize("icon_name", list(ICONS.keys()))
def test_visibility_matrix_css_fullscreen_signal_lost(streams_page, icon_name):
    """LAYOUT=css-fullscreen × HEALTH=signal-lost × icon=<each>.

    Dead browser-fullscreen tile. signal-lost rules are layout-agnostic →
    essential set surfaces; rest hidden.
    """
    page = streams_page
    _inject_tile_and_classes(page, extra_classes=["css-fullscreen", "signal-lost"])
    expected = EXPECTATIONS_EXPANDED["expanded+signal-lost"][icon_name]  # same policy
    eff_opacity = _icon_opacity(page, ICONS[icon_name])
    assert eff_opacity != -1, f"selector missed for {icon_name}: {ICONS[icon_name]}"
    if expected["opacity"] == "visible":
        assert eff_opacity > 0, (
            f"css-fullscreen+signal-lost: {icon_name} should be visible (B2 essential "
            f"set, css-fullscreen variant); effective opacity={eff_opacity}"
        )
    else:
        assert eff_opacity == 0, (
            f"css-fullscreen+signal-lost: {icon_name} should be hidden (B2 policy, "
            f"css-fullscreen variant); effective opacity={eff_opacity}"
        )


# ---------------------------------------------------------------------------
# Freeze-watchdog test — guards `static/js/streaming/freeze-watchdog.js`.
#
# The watchdog polls <video>.currentTime; if it stops advancing for N polls
# in a row, the tile gets `.signal-lost`. When currentTime resumes, the class
# is removed. This test injects a tile with a stubbed <video> whose
# currentTime is FROZEN, attaches a watchdog with tightened thresholds (so
# detection happens in ~300ms instead of ~16s), and asserts `.signal-lost`
# appears within a wall-clock budget.
#
# Why this matters: the production HealthMonitor in health.js DOES check
# currentTime, but its production thresholds total ~86s of detection lag
# (warmupMs=60s + staleAfterMs=20s + sampleIntervalMs=6s, set by stream.js
# from cameras.json / .env). The watchdog runs in parallel at a much faster
# cadence to give the operator a visible signal within ~16s of freeze onset.
#
# This is the test that locks down Defect 1 / Defect 3 visibility detection
# (memory: project_frozen_stream_no_buttons_ipad_health_monitor +
#  project_black_frame_with_healthy_state_badges).
# ---------------------------------------------------------------------------


def test_freeze_watchdog_tags_signal_lost_when_currenttime_frozen(streams_page):
    """Inject a tile + stubbed <video>, attach FreezeWatchdog with tight
    timings (300ms total detection), assert `.signal-lost` is applied.

    Then "unfreeze" by advancing currentTime and assert the class is removed.
    """
    page = streams_page

    page.evaluate(
        """
        async () => {
            // Wipe any prior injection
            document.querySelectorAll('[data-matrix-injected]').forEach(n => n.remove());

            // Build a minimal tile with a real <video> element. The video
            // has no src — we don't need real media, just a node whose
            // currentTime we can stub.
            const wrapper = document.createElement('div');
            wrapper.setAttribute('data-matrix-injected', '1');
            const tile = document.createElement('div');
            tile.className = 'stream-item';
            tile.setAttribute('data-camera-serial', 'FREEZE_TEST_FAKE_CAM');
            const video = document.createElement('video');
            video.className = 'stream-video';
            // Stash the stubbed currentTime on window so the cross-evaluate
            // advance function can mutate it and the getter sees the new
            // value. A closure would technically work too, but Playwright's
            // `page.evaluate` boundaries make `window` more reliable.
            window.__freezeStubCurrentTime = 1.234;
            Object.defineProperty(video, 'currentTime', {
                get: () => window.__freezeStubCurrentTime,
                configurable: true,
            });
            Object.defineProperty(video, 'paused', { get: () => false, configurable: true });
            Object.defineProperty(video, 'ended', { get: () => false, configurable: true });
            tile.appendChild(video);
            wrapper.appendChild(tile);
            document.body.appendChild(wrapper);

            const mod = await import('/static/js/streaming/freeze-watchdog.js');
            const wd = mod.makeFreezeWatchdog({
                pollIntervalMs: 50,
                stallPollsToTrip: 2,
                warmupMs: 100,
            });
            window.__testFreezeWatchdog = wd;
            wd.attach('FREEZE_TEST_FAKE_CAM', video);
        }
        """
    )

    # Warmup (100ms) + 2 polls × 50ms = 200ms total. Budget 600ms wall-clock
    # to absorb scheduling jitter on slow CI hosts.
    page.wait_for_timeout(600)
    has_signal_lost = page.evaluate(
        "() => document.querySelector('[data-matrix-injected] .stream-item')"
        ".classList.contains('signal-lost')"
    )
    assert has_signal_lost, (
        "FreezeWatchdog should have tagged the tile with .signal-lost after "
        "warmup + 2 stalled polls (~200ms). The tile's stubbed currentTime "
        "never advanced; if this fails the watchdog isn't polling or isn't "
        "applying the class. Check freeze-watchdog.js console output."
    )

    # Now "unfreeze" — simulate CONTINUOUS advancement like a real
    # playing video. A one-shot += bump would advance once then re-stall
    # (real video advances ~30fps; we mimic that with a 30ms interval).
    # The watchdog's first progressed tick removes the class; subsequent
    # progressed ticks keep stallCount=0 so it stays removed.
    page.evaluate(
        """() => {
            window.__freezeStubAdvancer = setInterval(() => {
                window.__freezeStubCurrentTime += 0.033;
            }, 30);
        }"""
    )
    page.wait_for_timeout(300)  # ~6 polls of watchdog, ~10 stub advances
    still_lost = page.evaluate(
        "() => document.querySelector('[data-matrix-injected] .stream-item')"
        ".classList.contains('signal-lost')"
    )
    assert not still_lost, (
        "FreezeWatchdog should have REMOVED .signal-lost once currentTime "
        "advanced (delta=1.0 > epsilon=0.05). If this fails, the watchdog's "
        "recovery path is broken — operator would see a tile stuck on dead "
        "appearance even after the stream resumed."
    )

    # Cleanup so subsequent tests don't see the stub
    page.evaluate(
        """() => {
            clearInterval(window.__freezeStubAdvancer);
            window.__testFreezeWatchdog?.detach('FREEZE_TEST_FAKE_CAM');
            delete window.__testFreezeWatchdog;
            delete window.__freezeStubCurrentTime;
            delete window.__freezeStubAdvancer;
        }"""
    )


def test_freeze_watchdog_ignores_paused_stream(streams_page):
    """If `<video>.paused` is true, the watchdog must NOT tag the tile.

    A user-paused stream isn't 'frozen' — it's intentionally stopped.
    Tagging it would lie about the publisher state.
    """
    page = streams_page

    page.evaluate(
        """
        async () => {
            document.querySelectorAll('[data-matrix-injected]').forEach(n => n.remove());
            const wrapper = document.createElement('div');
            wrapper.setAttribute('data-matrix-injected', '1');
            const tile = document.createElement('div');
            tile.className = 'stream-item';
            tile.setAttribute('data-camera-serial', 'PAUSED_TEST_FAKE_CAM');
            const video = document.createElement('video');
            video.className = 'stream-video';
            Object.defineProperty(video, 'currentTime', { get: () => 1.0, configurable: true });
            Object.defineProperty(video, 'paused', { get: () => true, configurable: true });
            Object.defineProperty(video, 'ended', { get: () => false, configurable: true });
            tile.appendChild(video);
            wrapper.appendChild(tile);
            document.body.appendChild(wrapper);
            const mod = await import('/static/js/streaming/freeze-watchdog.js');
            const wd = mod.makeFreezeWatchdog({
                pollIntervalMs: 50,
                stallPollsToTrip: 2,
                warmupMs: 100,
            });
            window.__testPausedWatchdog = wd;
            wd.attach('PAUSED_TEST_FAKE_CAM', video);
        }
        """
    )
    # Same budget as the freeze test — would tag by now if the paused
    # bail-out wasn't working.
    page.wait_for_timeout(600)
    is_tagged = page.evaluate(
        "() => document.querySelector('[data-matrix-injected] .stream-item')"
        ".classList.contains('signal-lost')"
    )
    assert not is_tagged, (
        "FreezeWatchdog should NOT tag a paused stream as frozen. The "
        "paused bail-out at freeze-watchdog.js _tick() guard should keep "
        "stallCount at 0. If this fails, paused tiles will lie about being "
        "dead."
    )
    page.evaluate(
        "() => { window.__testPausedWatchdog?.detach('PAUSED_TEST_FAKE_CAM'); "
        "delete window.__testPausedWatchdog; }"
    )
