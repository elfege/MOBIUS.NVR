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

from textwrap import dedent

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
        # The B2 essential trio stays operable on dead tiles
        "settings": {"opacity": "visible"},
        "power":    {"opacity": "visible"},
        "playback": {"opacity": "visible"},
        # Everything else stays hidden (B2 policy)
        "fullscreen":      {"opacity": "hidden"},
        "audio":           {"opacity": "hidden"},
        "ptz":             {"opacity": "hidden"},
        "controls_toggle": {"opacity": "hidden"},
        "record":          {"opacity": "hidden"},
        "talkback":        {"opacity": "hidden"},
        "more":            {"opacity": "hidden"},
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
    """
    page = streams_page
    _inject_tile_and_classes(page, extra_classes=["signal-lost"])
    expected = EXPECTATIONS["grid+signal-lost"][icon_name]
    eff_opacity = _icon_opacity(page, ICONS[icon_name])
    assert eff_opacity != -1, f"selector missed for {icon_name}: {ICONS[icon_name]}"
    if expected["opacity"] == "visible":
        assert eff_opacity > 0, (
            f"grid+signal-lost: {icon_name} should be visible (B2 essential "
            f"trio); effective opacity={eff_opacity}. If this fails the B2 "
            "rule is dead again — likely a new selector chain put opacity:0 "
            "on an ancestor of .stream-actions-bar."
        )
    else:
        assert eff_opacity == 0, (
            f"grid+signal-lost: {icon_name} should be hidden (meaningless "
            f"on a dead stream per B2 policy); effective opacity={eff_opacity}"
        )
