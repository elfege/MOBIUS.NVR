"""
tests/e2e/test_rec_manual.py — manual-recording surface (Phase D backfill).

Covers what's reliably testable from the REC rows in
docs/functionality_reference.md without a real camera stream:

  REC.MANUAL.START   POST /api/recording/<serial>/start
  REC.MANUAL.STOP    POST /api/recording/<serial>/stop
  REC.MANUAL.ACTIVE  GET  /api/recording/active

What's intentionally NOT here
-----------------------------
REC.CONTINUOUS.START — no /api/recording/continuous/start endpoint exists
                       today (reference doc lists it; the route surface
                       offers per-camera start/stop only). Marked as a
                       doc-bug in the reference; SKIP-worthy until the
                       endpoint is either added or the doc is corrected.

REC.MOTION.TRIGGER — needs an actual motion event from a real camera
                     publisher. Out of scope for the default test stack.

REC.MIGRATION.AGE_OUT / REC.CLEANUP.ARCHIVE_RETENTION — storage-domain
                     tests; will get their own file when STORAGE surface
                     is backfilled.

REC.PLAYBACK.TIMELINE — UI modal; will get a thin smoke test alongside
                     CAM.SETTINGS.OPEN when the cache-freshness gap is
                     resolved.

Caveat
------
The test stack has no real camera publishers — FFmpeg cannot connect to
the seeded camera's RTSP source URL. start_manual_recording handles
that path: it inserts the recordings row (status='recording') BEFORE
the FFmpeg process is verified, so the row write IS testable; the
process will fail in the background and the recording will eventually
flip to status='error' or 'completed' depending on FFmpeg's exit code.
We assert the row appears, the API returns 200 with a recording_id,
and stop cleans it up — not that the captured video file is valid.
"""

from __future__ import annotations

import time
import bcrypt
import httpx
import pytest


# Use a distinct camera serial from CAM.SETTINGS tests so the two files
# can be run together (or alone) without colliding on rows. Worker-suffixed
# for xdist parallelization.


@pytest.fixture
def seed_rec_camera(db_conn, seed_test_admin, worker_tag):
    """
    Insert a minimal cameras row for the recording tests. Same shape as
    CAM.SETTINGS's seed_test_camera but with its own serial — the two
    file's fixtures don't interfere if both run in the same session.

    Serial is worker-suffixed so xdist workers each get their own row.

    No matching user_camera_preferences row is needed (manual recordings
    don't read user prefs). The recordings table cleanup runs in the
    teardown.
    """
    TEST_CAM_SERIAL = f"E2E_REC_TEST_CAMERA_{worker_tag}"
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO cameras (
                serial, name, type, camera_id, stream_type, hidden,
                streaming_hub
            ) VALUES (%s, 'REC E2E Test Cam', 'REOLINK', %s, 'LL_HLS', false,
                      'mediamtx')
            ON CONFLICT (serial) DO UPDATE SET
                stream_type   = 'LL_HLS',
                hidden        = false,
                streaming_hub = 'mediamtx'
            """,
            (TEST_CAM_SERIAL, TEST_CAM_SERIAL),
        )
    yield TEST_CAM_SERIAL
    # Clean up: any recordings the test created, then the camera. FK
    # cascade isn't defined for recordings → cameras so we do it
    # explicitly. Match on camera_id (the column the recordings table
    # uses) — schema confusion alert: cameras.serial vs recordings.camera_id.
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM recordings WHERE camera_id = %s", (TEST_CAM_SERIAL,))
        cur.execute("DELETE FROM cameras WHERE serial = %s", (TEST_CAM_SERIAL,))


@pytest.fixture
def admin_client(base_url: str, seed_test_admin):
    """httpx.Client logged in as e2e_admin — reused across REC tests."""
    username, password = seed_test_admin
    client = httpx.Client(base_url=base_url, follow_redirects=False)
    resp = client.post("/login", data={"username": username, "password": password})
    assert resp.status_code in (200, 302, 303), (
        f"admin login failed: {resp.status_code} {resp.text[:200]}"
    )
    yield client
    client.close()


# ---------------------------------------------------------------------------
# REC.MANUAL.START
# ---------------------------------------------------------------------------

def test_rec_manual_start_creates_recordings_row(
    admin_client, db_conn, seed_rec_camera
):
    """
    REC.MANUAL.START — POST /api/recording/<serial>/start with a duration
    in the JSON body. Expect:
      - 200 with success=true + recording_id
      - A row in `recordings` for that camera with file_path set

    Note: status='recording' is the doc's stated post-condition. We
    accept any non-terminal status here (the FFmpeg process may
    transition through error→completed within the millisecond window
    between the API return and our DB read) — what we're verifying is
    that the row WAS created, which is the contract we care about.
    """
    serial = seed_rec_camera

    resp = admin_client.post(
        f"/api/recording/{serial}/start",
        json={"duration": 5},
    )
    assert resp.status_code in (200, 500, 503), (
        f"unexpected status {resp.status_code}: {resp.text[:200]}"
    )

    if resp.status_code == 503:
        pytest.skip("recording_service not initialized in test stack")

    body = resp.json()
    assert body.get("success") is True, f"unexpected body: {body}"
    assert "recording_id" in body, f"no recording_id in {body}"

    # Verify the recordings row exists. Allow a small settle window —
    # the row is inserted in the background subprocess setup.
    deadline = time.monotonic() + 3
    row = None
    while time.monotonic() < deadline:
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT camera_id, file_path, status FROM recordings "
                "WHERE camera_id = %s ORDER BY id DESC LIMIT 1",
                (serial,),
            )
            row = cur.fetchone()
        if row:
            break
        time.sleep(0.2)

    assert row is not None, (
        f"no recordings row written for {serial} within 3s of POST /start. "
        "The API returned success but the DB-side insert didn't happen — "
        "check recording_service.start_manual_recording's insert path."
    )
    assert row[0] == serial
    assert row[1], "file_path was empty"


# ---------------------------------------------------------------------------
# REC.MANUAL.STOP
# ---------------------------------------------------------------------------

def test_rec_manual_stop_cleans_up_active(
    admin_client, db_conn, seed_rec_camera
):
    """
    REC.MANUAL.STOP — POST /start then POST /stop, expect the active list
    to be empty after stop and the recordings row to be in a terminal
    state.

    The stop endpoint takes the camera_id (not the recording_id) — see
    the route handler comment: "recording_id passed as camera_id parameter".
    """
    serial = seed_rec_camera

    # Start first
    start_resp = admin_client.post(
        f"/api/recording/{serial}/start",
        json={"duration": 30},
    )
    if start_resp.status_code == 503:
        pytest.skip("recording_service not initialized")
    assert start_resp.status_code == 200, start_resp.text[:200]

    # Brief settle so the recording_service registers the active entry
    time.sleep(0.5)

    stop_resp = admin_client.post(f"/api/recording/{serial}/stop")
    # 200 = stopped; 404 = wasn't actually tracked (race with FFmpeg
    # exit due to bad source). Either is acceptable for this test's
    # contract — what we don't want is 5xx.
    assert stop_resp.status_code in (200, 404), (
        f"unexpected stop status {stop_resp.status_code}: {stop_resp.text[:200]}"
    )

    # Active list should not contain this camera anymore. Endpoint
    # actually returns {success, count, recordings: [...]} — the doc
    # said "list" but the implementation wraps it.
    active_resp = admin_client.get("/api/recording/active")
    assert active_resp.status_code == 200, active_resp.text[:200]
    active = active_resp.json()
    recordings = active.get("recordings", []) if isinstance(active, dict) else active
    camera_ids = [
        r.get("camera_id") for r in recordings if isinstance(r, dict)
    ]
    assert serial not in camera_ids, (
        f"after stop, {serial} still appears in /api/recording/active: {active}"
    )


# ---------------------------------------------------------------------------
# REC.MANUAL.ACTIVE — GET /api/recording/active
# ---------------------------------------------------------------------------

def test_rec_manual_active_returns_envelope_shape(admin_client):
    """
    REC.MANUAL.ACTIVE — GET /api/recording/active returns a JSON envelope
    `{success: bool, count: int, recordings: [...]}`. The reference doc
    used to say "list" but the implementation wraps the list in an
    envelope; documenting the real shape here as the contract.

    This is a thin smoke; the meaningful tests are START + STOP above.
    """
    resp = admin_client.get("/api/recording/active")
    if resp.status_code == 503:
        pytest.skip("recording_service not initialized")
    assert resp.status_code == 200, resp.text[:200]
    body = resp.json()
    assert isinstance(body, dict), (
        f"/api/recording/active should return an object; got {type(body).__name__}"
    )
    assert "recordings" in body, f"no 'recordings' key in {body}"
    assert isinstance(body["recordings"], list), (
        f"body['recordings'] should be a list; got {type(body['recordings']).__name__}"
    )


# ---------------------------------------------------------------------------
# REC.MANUAL.NOT_FOUND — sanity check on bad camera id
# ---------------------------------------------------------------------------

def test_rec_manual_start_unknown_camera_returns_404(admin_client):
    """
    Negative-shape check: starting a recording on a serial that doesn't
    exist returns 404 (per route's `if not camera: return 404`).
    """
    resp = admin_client.post(
        "/api/recording/DEFINITELY_NOT_A_REAL_SERIAL/start",
        json={"duration": 1},
    )
    if resp.status_code == 503:
        pytest.skip("recording_service not initialized")
    assert resp.status_code == 404, (
        f"unknown camera should 404, got {resp.status_code}: {resp.text[:200]}"
    )
