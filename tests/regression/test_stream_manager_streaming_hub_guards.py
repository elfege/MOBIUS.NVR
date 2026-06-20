"""
tests/regression/test_stream_manager_streaming_hub_guards.py
=============================================================

Regression ledger entry — `streaming/stream_manager.py:start_stream()`
must early-return for BOTH `go2rtc` AND `native_mjpeg` cameras. Without
the native_mjpeg guard a second consumer is opened against cameras whose
contract is "single HTTP-snapshot poller via SV3CMJPEGCaptureService" —
breaking Rule 11 (1 camera = 1 input) silently.

Bug (2026-06-20):
    Audit of stream_manager.start_stream() found an explicit early-return
    for go2rtc cameras (`is_go2rtc_camera()` guard) but no counterpart for
    `native_mjpeg`. generate_streaming_configs.py correctly excluded
    these cameras from MediaMTX / go2rtc configs at startup, but the
    runtime stream_manager call had no matching guard — so any code path
    calling start_stream() for a native_mjpeg camera would attempt
    FFmpeg-against-RTSP regardless of hub, violating Rule 11.

Fix:
    Added an `is_native_mjpeg_camera()` early-return mirroring the
    go2rtc guard. Both guards now coexist; both must stay present.

Guard (static — no live stack):
    Parse `streaming/stream_manager.py`. The `start_stream()` function
    must contain BOTH:
      - `is_go2rtc_camera(` call AND a `return None` reachable from its
        truthy branch.
      - `is_native_mjpeg_camera(` call AND a `return None` reachable
        from its truthy branch.

    A future refactor that simplifies the early-return block to a
    single check (or removes one camera type) fires this test.

    A true end-to-end check (mock the FFmpeg spawn, call start_stream
    with a native_mjpeg camera, assert no spawn) would require deep
    mocking of the helper chain inside start_stream — the static guard
    catches the dominant regression shape (somebody touches the guards
    block and drops one).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.regression._ledger import entry_for, format_failure_context


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
STREAM_MANAGER = REPO_ROOT / "streaming" / "stream_manager.py"
_LEDGER_CONTEXT = format_failure_context(entry_for(__file__))


def _start_stream_body() -> str:
    """Extract the body of `start_stream()` for inspection.

    We can't usefully `import` stream_manager from this test (it has
    heavy module-import side effects + requires a running Postgres),
    so we parse the source. The function body spans from its `def` to
    the next top-level `def` / `class` / `EOF`.
    """
    assert STREAM_MANAGER.is_file(), f"{STREAM_MANAGER} not found"
    src = STREAM_MANAGER.read_text(encoding="utf-8")
    match = re.search(
        r"def start_stream\(self[^)]*\)[^:]*:(.+?)(?=\n    def |\nclass |\Z)",
        src,
        re.DOTALL,
    )
    assert match, "Could not locate start_stream() function body in stream_manager.py"
    return match.group(1)


def test_start_stream_has_go2rtc_early_return():
    """Sanity check: the go2rtc guard exists. If THIS fails the
    refactor is bigger than just dropping native_mjpeg — the entire
    early-return block has been restructured and both guards need
    re-verification."""
    body = _start_stream_body()
    assert "is_go2rtc_camera(" in body, (
        f"start_stream() no longer calls is_go2rtc_camera() — the streaming-hub "
        f"early-return block has been refactored away. This pre-existing guard "
        f"is a prerequisite for the native_mjpeg guard below.\n\n{_LEDGER_CONTEXT}"
    )


def test_start_stream_has_native_mjpeg_early_return():
    """The actual guard this regression file exists to lock in."""
    body = _start_stream_body()
    assert "is_native_mjpeg_camera(" in body, (
        f"start_stream() no longer calls is_native_mjpeg_camera() — Rule 11 "
        f"is at risk. A native_mjpeg camera (SV3C hi3510 etc.) calling through "
        f"this function will now attempt FFmpeg-against-RTSP, opening a SECOND "
        f"consumer alongside SV3CMJPEGCaptureService. Restore the guard "
        f"alongside is_go2rtc_camera().\n\n{_LEDGER_CONTEXT}"
    )


def test_start_stream_native_mjpeg_guard_returns_none():
    """The is_native_mjpeg_camera() check must lead to a `return None`
    (matching the go2rtc guard's behaviour). A truthy branch that
    LOGS but doesn't return defeats the guard — FFmpeg still spawns."""
    body = _start_stream_body()
    # Find the position of the native_mjpeg check and look for a
    # `return None` within the next ~6 lines (the typical early-return
    # block size). A larger window would let a return-inside-an-
    # unrelated-branch slip through.
    idx = body.find("is_native_mjpeg_camera(")
    assert idx != -1, "is_native_mjpeg_camera() not in body (test_start_stream_has_native_mjpeg_early_return covers this)"
    window = body[idx:idx + 600]   # ~6-10 lines of indented code
    assert "return None" in window, (
        f"is_native_mjpeg_camera() is referenced but no `return None` follows "
        f"within the guard block — FFmpeg can still spawn. The guard must "
        f"early-return like the go2rtc one does, not just log.\n\n{_LEDGER_CONTEXT}"
    )
