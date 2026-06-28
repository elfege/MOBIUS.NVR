"""
Regression: desktop stream startup must stay PARALLEL.

Operator report 2026-06-27: /streams loaded slowly after a refresh ("used to be
much faster — maybe we broke parallelism"). Root cause: stream.js
`startAllStreams()` awaited each `startStream()` serially with a 300ms delay
between every camera. That stagger is real protection for iOS Safari's ~4-8
simultaneous-video-decode hard limit, but commit c628655f over-generalized it to
"all UIs for consistent behavior" — so a desktop wall of N cameras paid
N x (handshake + 300ms) (~10-15s for 16 cams) for a limit desktops don't have.

Fix: a desktop fast-path — `if (!isPortableDevice())` fires all `startStream()`
calls in parallel via `Promise.allSettled` and returns BEFORE the sequential
loop. iOS / portable still falls through to the sequential+300ms path.

This guard pins that shape so a future refactor can't silently re-serialize
desktop startup (the exact c628655f regression). Static source check — no live
browser/stack needed (a real timing e2e would be flaky: it needs live cameras
and is timing-sensitive). Mirrors the established static-guard regression style
(see test_snap_gate_code_present, test_stream_manager_streaming_hub_guards).
"""

import re
from pathlib import Path

import pytest

STREAM_JS = Path(__file__).resolve().parents[2] / "static" / "js" / "streaming" / "stream.js"


def _start_all_streams_body() -> str:
    """Return the source of the `async startAllStreams(...)` method body via
    brace matching from its opening brace."""
    assert STREAM_JS.is_file(), f"{STREAM_JS} not found"
    src = STREAM_JS.read_text(encoding="utf-8")
    m = re.search(r"async\s+startAllStreams\s*\([^)]*\)\s*\{", src)
    assert m, "startAllStreams() not found in stream.js"
    start = m.end() - 1  # index of the opening '{'
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError("could not brace-match the startAllStreams() body")


def test_desktop_fast_path_is_parallel():
    """Desktop (!isPortableDevice) must fire stream starts in parallel."""
    body = _start_all_streams_body()
    assert "isPortableDevice()" in body, \
        "startAllStreams() must branch on device capability (isPortableDevice)"
    assert re.search(r"if\s*\(\s*!\s*isPortableDevice\(\)\s*\)", body), \
        "missing the desktop fast-path branch `if (!isPortableDevice())`"
    assert "Promise.allSettled" in body, \
        "the desktop fast-path must fire startStream() calls in parallel via Promise.allSettled"


def test_parallel_branch_precedes_sequential_loop():
    """The parallel desktop branch must come BEFORE the sequential 300ms-delay
    loop and return, so desktop never falls into the iOS stagger."""
    body = _start_all_streams_body()
    par = body.find("Promise.allSettled")
    seq = body.find("for (let index")
    assert par != -1, "parallel desktop branch (Promise.allSettled) is missing"
    assert seq != -1, "sequential loop (`for (let index`) missing — it must remain for iOS/portable"
    assert par < seq, \
        "the desktop parallel branch must precede (and return before) the sequential iOS loop"


def test_sequential_path_retained_for_ios():
    """The iOS/portable sequential+delay path must still exist (we did NOT rip
    out the decode-limit protection — only gated it off for desktop)."""
    body = _start_all_streams_body()
    assert "delayMs" in body, \
        "the sequential inter-stream delay (delayMs) must remain for iOS/portable"
    assert re.search(r"setTimeout\(\s*r\s*,\s*delayMs", body), \
        "the sequential path must still stagger with setTimeout(r, delayMs) for iOS decode limits"
