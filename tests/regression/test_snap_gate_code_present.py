"""
tests/regression/test_snap_gate_code_present.py
=================================================

Regression ledger entry — `/api/snap/<camera_id>` must return 503 (not
200 with stale frame) when `camera_state_tracker.availability` is
`degraded` or `offline`. Without this gate the UI shows a frozen frame
instead of a "signal lost" overlay, and the operator can't tell that
the publisher has died.

Bug (2026-06-13):
    Operator screenshotted a tile showing a frozen frame of a dead
    stream. Words: "still have a snapshot instead of black stream when
    stream is down. THIS IS MISLEADING AND PREVENTS ME FROM SEEING IT
    IS NOT WORKING."

Fix:
    Commit 33b31431 (snap_gates_by_publisher_state_JUN_13_2026_a) added
    a publisher-state check at the top of `/api/snap/<camera_id>` that
    short-circuits with 503 when the tracker reports degraded/offline.

Guard (static — no live stack):
    Parse `routes/streaming.py`. The snap endpoint must contain BOTH:
      - A call to `camera_state_tracker.get_camera_state(...)`, AND
      - A 503 return path triggered by `availability` ∈ {degraded, offline}.
    If a future refactor removes either, this fires.

    A true end-to-end check (force a camera to DEGRADED → assert /api/snap
    returns 503) is gated by access to internal tracker state from the
    test runner. The static guard covers the most common regression
    shape — somebody refactors the snap handler and forgets to keep
    the gate.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
STREAMING_ROUTES = REPO_ROOT / "routes" / "streaming.py"


def test_snap_endpoint_consults_state_tracker():
    """The `/api/snap` handler must read camera_state_tracker before
    serving a frame, otherwise a stale buffer can be returned for a
    dead publisher."""
    assert STREAMING_ROUTES.is_file(), f"{STREAMING_ROUTES} not found"
    src = STREAMING_ROUTES.read_text(encoding="utf-8")

    # Find the snap-endpoint function. Its decorator references /api/snap.
    snap_section = re.search(
        r"@\w+\.route\(['\"]/api/snap[^'\"]+['\"][^)]*\)\s*[\s\S]+?(?=@\w+\.route|\Z)",
        src,
    )
    assert snap_section, (
        "Couldn't locate `/api/snap/...` handler in routes/streaming.py. "
        "Either the URL pattern moved or the regex needs updating."
    )

    body = snap_section.group(0)
    assert "camera_state_tracker" in body, (
        "The /api/snap handler doesn't reference `camera_state_tracker`. "
        "The publisher-state gate that prevents stale-frame serves to "
        "dead streams (commit 33b31431, 2026-06-13) is missing."
    )


def test_snap_endpoint_returns_503_on_degraded_or_offline():
    """The snap handler's degraded/offline branch must short-circuit
    with 503 (not 200, not silent fallthrough)."""
    src = STREAMING_ROUTES.read_text(encoding="utf-8")

    # We're looking for a 503 path that's reachable when availability
    # is in ('degraded', 'offline'). Pattern: a string with both states
    # near a `return ..., 503`.
    has_degraded = "degraded" in src.lower()
    has_offline = "offline" in src.lower()
    has_503 = "503" in src

    assert has_degraded and has_offline and has_503, (
        f"snap-gate signal lost in routes/streaming.py: "
        f"degraded={has_degraded} offline={has_offline} 503={has_503}. "
        "The fix (33b31431) requires all three to be present."
    )

    # Sharper check — the 503 branch must be wired to the availability
    # check, not e.g. a separate auth failure 503.
    gate = re.search(
        r"availability[\s\S]{0,300}503|503[\s\S]{0,300}availability",
        src,
    )
    assert gate, (
        "Couldn't find a 503 return tied to `availability` in "
        "routes/streaming.py. The snap-gate against degraded/offline "
        "publishers appears to have been removed."
    )
