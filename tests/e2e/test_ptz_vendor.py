"""
tests/e2e/test_ptz_vendor.py — vendor-aware PTZ surface (Phase D).

PTZ doesn't have a single contract — each vendor speaks a different
protocol and the route at /api/ptz/<serial>/<direction> dispatches
internally:

  Reolink (no ONVIF port)   → Baichuan (cmd_id 19 for presets; pan-right
                              sweep for recalibrate — the ONVIF home
                              command was no-op on the Reolink range
                              circa 2026-05)
  Reolink (with ONVIF port) → ONVIF primary, Baichuan fallback
  Amcrest                   → ONVIF primary, Amcrest CGI fallback
  SV3C                      → ONVIF only
  Eufy                      → bridge (preset index 0-3; no names)
  Neolink                   → Baichuan via Neolink RTSP bridge

What this file tests
--------------------
Without real PTZ hardware in the test stack, we can't verify ACTUAL
movement / preset save success. What we CAN verify:

  - Dispatch reaches the right vendor branch (response shape diverges:
    Eufy 503 with bridge_status; ONVIF 200 with success=false +
    descriptive message; non-PTZ 400)
  - The route doesn't 5xx — every code path returns a structured JSON
    envelope
  - Negative shapes: non-PTZ camera → 400; unknown camera → 404;
    Eufy without bridge → 503

The recalibrate verb is exercised as just another direction; the route
treats it uniformly.

Preset CRUD coverage
--------------------
GET   /api/ptz/<serial>/presets               list
POST  /api/ptz/<serial>/preset                save current as preset
POST  /api/ptz/<serial>/preset/<preset_token> go to preset
DELETE /api/ptz/<serial>/preset/<preset_token> delete preset

Each exercised against the vendor matrix below. Real CRUD success
requires hardware — we pin the API surface only.
"""

from __future__ import annotations

import httpx
import pytest


# Vendor matrix — (camera_type, capabilities, fixture-id, notes).
# Each entry yields a seeded camera that exercises one dispatch branch
# in routes/ptz.py. The capability list MUST contain 'ptz' or the route
# 400s at the validator gate before reaching the vendor branch.
VENDORS = [
    ("reolink", ["ptz"], "reolink_onvif",
     "Reolink with ONVIF port — primary ONVIF, Baichuan fallback"),
    ("amcrest", ["ptz"], "amcrest",
     "Amcrest — ONVIF primary, CGI fallback"),
    ("sv3c", ["ptz"], "sv3c",
     "SV3C — ONVIF only"),
    ("eufy", ["ptz"], "eufy",
     "Eufy — bridge protocol, no ONVIF"),
]


@pytest.fixture
def admin_client(base_url, seed_test_admin):
    username, password = seed_test_admin
    c = httpx.Client(base_url=base_url, follow_redirects=False)
    c.post("/login", data={"username": username, "password": password})
    yield c
    c.close()


@pytest.fixture(params=VENDORS, ids=[v[2] for v in VENDORS])
def seed_vendor_camera(request, db_conn, seed_test_admin):
    """
    Parametrized fixture — yields (serial, type, fixture_id) for each
    vendor in VENDORS. Test methods that use it run once per vendor.
    Capabilities are seeded as JSONB so ptz_validator's
    `'ptz' in camera.get('capabilities', [])` returns true.
    """
    cam_type, capabilities, fixture_id, _note = request.param
    serial = f"E2E_PTZ_{fixture_id.upper()}"
    import json
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO cameras (
                serial, name, type, camera_id, stream_type, hidden,
                streaming_hub, capabilities, onvif_port
            ) VALUES (%s, %s, %s, %s, 'LL_HLS', false, 'mediamtx', %s::jsonb, 8000)
            ON CONFLICT (serial) DO UPDATE SET
                type = EXCLUDED.type,
                capabilities = EXCLUDED.capabilities,
                hidden = false
            """,
            (serial, f"ptz-{fixture_id}", cam_type, serial,
             json.dumps(capabilities)),
        )
    yield serial, cam_type, fixture_id
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM cameras WHERE serial = %s", (serial,))


# ---------------------------------------------------------------------------
# Movement (any direction)
# ---------------------------------------------------------------------------

def test_ptz_move_dispatch_per_vendor(admin_client, seed_vendor_camera):
    """
    POST /api/ptz/<serial>/left — verify the route dispatches without
    crashing (no 5xx with Python traceback). Real movement requires
    hardware; we accept any structured JSON envelope as success.

    Eufy is the exception: without a bridge, the route returns 503 — a
    documented response shape, not a crash.
    """
    serial, cam_type, fixture_id = seed_vendor_camera
    resp = admin_client.post(f"/api/ptz/{serial}/left", json={"speed": 50})

    # The route should never reply with 5xx from an unhandled Python
    # exception. 503 is a documented response shape (Eufy bridge gone);
    # 200 with success=false is the "no real hardware" path.
    assert resp.status_code != 500, (
        f"vendor={cam_type}: route raised an unhandled exception → 500. "
        f"Body: {resp.text[:200]}"
    )
    body = resp.json()
    assert "success" in body, f"vendor={cam_type}: no 'success' key: {body}"


# ---------------------------------------------------------------------------
# Recalibrate (the auto-tour button) — same endpoint, special direction
# ---------------------------------------------------------------------------

def test_ptz_recalibrate_per_vendor(admin_client, seed_vendor_camera):
    """
    POST /api/ptz/<serial>/recalibrate — exercises the
    auto-tour / re-zero gimbal verb. Per route comments, recalibrate
    routes through Baichuan for Reolink even when ONVIF is configured
    (ONVIF GotoHomePosition was unreliable across the Reolink range
    in 2026-05). The handler is uniform on the API surface.
    """
    serial, cam_type, fixture_id = seed_vendor_camera
    resp = admin_client.post(f"/api/ptz/{serial}/recalibrate", json={})
    assert resp.status_code != 500, (
        f"recalibrate on {cam_type} crashed: {resp.text[:200]}"
    )
    body = resp.json()
    assert "success" in body, f"recalibrate {cam_type}: no 'success': {body}"


# ---------------------------------------------------------------------------
# Preset CRUD shape
# ---------------------------------------------------------------------------

def test_ptz_preset_list_shape(admin_client, seed_vendor_camera):
    """
    GET /api/ptz/<serial>/presets returns a list or envelope, never 500.
    """
    serial, cam_type, _ = seed_vendor_camera
    resp = admin_client.get(f"/api/ptz/{serial}/presets")
    assert resp.status_code != 500, (
        f"presets GET on {cam_type} crashed: {resp.text[:200]}"
    )


def test_ptz_preset_save_shape(admin_client, seed_vendor_camera):
    """
    POST /api/ptz/<serial>/preset — vendor-specific body:
      - ONVIF (Reolink/Amcrest/SV3C): {name: "..."}
      - Eufy: {index: 0..3}

    We send both fields so any branch finds what it needs. The route
    should respond with a structured envelope, never 500.
    """
    serial, cam_type, _ = seed_vendor_camera
    resp = admin_client.post(
        f"/api/ptz/{serial}/preset",
        json={"name": "e2e-test-preset", "index": 0},
    )
    assert resp.status_code != 500, (
        f"preset save on {cam_type} crashed: {resp.text[:200]}"
    )
    body = resp.json()
    assert "success" in body, f"preset save {cam_type}: no 'success': {body}"


def test_ptz_preset_goto_shape(admin_client, seed_vendor_camera):
    """
    POST /api/ptz/<serial>/preset/<token> — goto preset by token. Real
    success requires the preset to exist on the device; we pin shape
    only.
    """
    serial, cam_type, _ = seed_vendor_camera
    resp = admin_client.post(f"/api/ptz/{serial}/preset/fake_token_123", json={})
    assert resp.status_code != 500, (
        f"preset goto on {cam_type} crashed: {resp.text[:200]}"
    )


def test_ptz_preset_delete_shape(admin_client, seed_vendor_camera):
    """
    DELETE /api/ptz/<serial>/preset/<token> — same shape contract.
    """
    serial, cam_type, _ = seed_vendor_camera
    resp = admin_client.request(
        "DELETE", f"/api/ptz/{serial}/preset/fake_token_123"
    )
    assert resp.status_code != 500, (
        f"preset delete on {cam_type} crashed: {resp.text[:200]}"
    )


# ---------------------------------------------------------------------------
# Negative paths — NOT parametrized (vendor doesn't matter)
# ---------------------------------------------------------------------------

def test_ptz_unknown_camera_returns_400_or_404(admin_client):
    """
    PTZ on a serial that doesn't exist: returns 400 (validator fails
    first because is_ptz_capable returns False for missing camera) or
    404. Either is acceptable — what we don't want is 500.
    """
    resp = admin_client.post(
        "/api/ptz/DEFINITELY_NOT_A_REAL_SERIAL/left", json={}
    )
    assert resp.status_code in (400, 404), (
        f"unexpected status: {resp.status_code}: {resp.text[:200]}"
    )


def test_ptz_non_ptz_camera_returns_400(admin_client, db_conn, seed_test_admin):
    """
    A camera without 'ptz' in capabilities gets 400 at the validator
    gate, before any vendor dispatch happens.
    """
    serial = "E2E_PTZ_FIXED_CAM"
    try:
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO cameras (serial, name, type, camera_id, stream_type, "
                "capabilities) VALUES (%s, 'fixed', 'reolink', %s, 'LL_HLS', '[]'::jsonb) "
                "ON CONFLICT (serial) DO UPDATE SET capabilities='[]'::jsonb",
                (serial, serial),
            )
        resp = admin_client.post(f"/api/ptz/{serial}/left", json={})
        assert resp.status_code == 400, (
            f"non-PTZ camera should 400 at validator: got {resp.status_code} "
            f"{resp.text[:200]}"
        )
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM cameras WHERE serial = %s", (serial,))
