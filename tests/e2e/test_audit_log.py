"""
tests/e2e/test_audit_log.py — AUDIT surface backfill (Phase D).

Covers the 7 AUDIT rows from docs/functionality_reference.md to the
extent each is e2e-testable:

  AUDIT.SETTINGS.INSERT_ROW_FIRES_TRIGGER  INSERT → setting_audit_log row
  AUDIT.SETTINGS.UPDATE_ROW_FIRES_TRIGGER  UPDATE → setting_audit_log row
  AUDIT.LOG.GET_BY_ADMIN (new)             GET /api/audit/log envelope
  AUDIT.LOG.ADMIN_ONLY_READ                viewer → /api/audit/log → 403
  UI_EVENT.OUTBOX.POST_BATCH               POST batch → ui_event_log rows
  UI_EVENT.PASSWORD_MASK                   doc-clarify: masking is CLIENT-SIDE

Already covered:
  AUDIT.COVERAGE.STATIC_CHECK   tests/test_audit_coverage.py (static SQL)
  AUDIT.LISTEN_NOTIFY.LIVE_FANOUT  requires socketio subscription — SKIP
"""

from __future__ import annotations

import bcrypt
import httpx
import pytest


@pytest.fixture
def admin_client(base_url, seed_test_admin):
    username, password = seed_test_admin
    client = httpx.Client(base_url=base_url, follow_redirects=False)
    client.post("/login", data={"username": username, "password": password})
    yield client
    client.close()


@pytest.fixture
def seed_audit_viewer(db_conn, seed_test_admin):
    username = "e2e_audit_viewer"
    password = "viewer_pw"
    bcrypt_hash = bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt(12)
    ).decode("utf-8")
    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (username, password_hash, role, must_change_password)
            VALUES (%s, %s, 'viewer', false)
            ON CONFLICT (username) DO UPDATE SET
                password_hash = EXCLUDED.password_hash, role = 'viewer',
                must_change_password = false
            """,
            (username, bcrypt_hash),
        )
    yield username, password
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE username = %s", (username,))


@pytest.fixture
def viewer_client(base_url, seed_audit_viewer):
    username, password = seed_audit_viewer
    client = httpx.Client(base_url=base_url, follow_redirects=False)
    client.post("/login", data={"username": username, "password": password})
    yield client
    client.close()


# ---------------------------------------------------------------------------
# AUDIT.LOG.ADMIN_ONLY_READ
# ---------------------------------------------------------------------------

def test_audit_log_get_admin_only(viewer_client):
    """
    AUDIT.LOG.ADMIN_ONLY_READ — viewer hits /api/audit/log → 403.
    """
    resp = viewer_client.get("/api/audit/log")
    assert resp.status_code == 403, (
        f"viewer got {resp.status_code} on /api/audit/log (expected 403): "
        f"{resp.text[:200]}"
    )


# ---------------------------------------------------------------------------
# AUDIT.LOG.GET_BY_ADMIN (new row)
# ---------------------------------------------------------------------------

def test_audit_log_get_returns_envelope(admin_client):
    """
    AUDIT.LOG.GET_BY_ADMIN — admin GET /api/audit/log returns the
    documented envelope: {rows, total, limit, offset}.
    """
    resp = admin_client.get("/api/audit/log?limit=10")
    assert resp.status_code == 200, resp.text[:200]
    body = resp.json()
    for key in ("rows", "total", "limit", "offset"):
        assert key in body, (
            f"missing envelope key {key!r}; keys={sorted(body.keys())}"
        )
    assert isinstance(body["rows"], list)
    assert body["limit"] == 10


# ---------------------------------------------------------------------------
# AUDIT.SETTINGS.INSERT_ROW_FIRES_TRIGGER
# ---------------------------------------------------------------------------

def test_audit_settings_insert_fires_trigger(db_conn):
    """
    AUDIT.SETTINGS.INSERT_ROW_FIRES_TRIGGER — INSERT into cameras must
    produce a setting_audit_log row with op='INSERT'.

    We use a unique camera serial so we can find the audit row even if
    other tests are concurrently writing to the table.
    """
    serial = "E2E_AUDIT_INSERT_CAM"
    try:
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO cameras (serial, name, type, camera_id, stream_type) "
                "VALUES (%s, 'audit-test', 'REOLINK', %s, 'LL_HLS')",
                (serial, serial),
            )
            # Schema (migration 036) doesn't have an `op` column — INSERT/
            # UPDATE/DELETE is inferred from which of old_value/new_value
            # is non-NULL. For an INSERT: old_value IS NULL, new_value is
            # the full row.
            cur.execute(
                """
                SELECT old_value, new_value FROM setting_audit_log
                  WHERE table_name = 'cameras' AND row_pk = %s
                  ORDER BY ts DESC LIMIT 1
                """,
                (serial,),
            )
            row = cur.fetchone()
        assert row is not None, (
            f"no setting_audit_log row written for INSERT on cameras "
            f"serial={serial} — migration 036's trigger may have been "
            "removed or dropped."
        )
        old_value, new_value = row
        assert old_value is None, (
            f"INSERT trigger should leave old_value NULL; got {old_value!r}"
        )
        assert isinstance(new_value, dict), f"new_value not a dict: {new_value!r}"
        assert new_value.get("serial") == serial
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM cameras WHERE serial = %s", (serial,))


# ---------------------------------------------------------------------------
# AUDIT.SETTINGS.UPDATE_ROW_FIRES_TRIGGER
# ---------------------------------------------------------------------------

def test_audit_settings_update_fires_trigger(db_conn):
    """
    AUDIT.SETTINGS.UPDATE_ROW_FIRES_TRIGGER — UPDATE on cameras must
    produce a setting_audit_log row with op='UPDATE' and both old + new
    JSON snapshots.
    """
    serial = "E2E_AUDIT_UPDATE_CAM"
    try:
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO cameras (serial, name, type, camera_id, stream_type) "
                "VALUES (%s, 'audit-pre', 'REOLINK', %s, 'LL_HLS') "
                "ON CONFLICT (serial) DO NOTHING",
                (serial, serial),
            )
            cur.execute(
                "UPDATE cameras SET name = 'audit-post' WHERE serial = %s",
                (serial,),
            )
            # For UPDATE: both old_value AND new_value are non-NULL (the
            # before-and-after snapshots). The `name` field is the one
            # that diverges. Filter for that shape rather than an `op`
            # column (which the schema doesn't have).
            cur.execute(
                """
                SELECT old_value, new_value FROM setting_audit_log
                  WHERE table_name = 'cameras' AND row_pk = %s
                    AND old_value IS NOT NULL AND new_value IS NOT NULL
                  ORDER BY ts DESC LIMIT 1
                """,
                (serial,),
            )
            row = cur.fetchone()
        assert row is not None, "no UPDATE-shape row in setting_audit_log"
        old_value, new_value = row
        # old_value + new_value are JSONB — psycopg2 returns them as
        # parsed dicts. The "name" field should differ between them.
        assert isinstance(old_value, dict) and isinstance(new_value, dict), (
            f"old/new not dicts: {type(old_value).__name__}, {type(new_value).__name__}"
        )
        assert old_value.get("name") == "audit-pre", old_value
        assert new_value.get("name") == "audit-post", new_value
    finally:
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM cameras WHERE serial = %s", (serial,))


# ---------------------------------------------------------------------------
# UI_EVENT.OUTBOX.POST_BATCH
# ---------------------------------------------------------------------------

def test_ui_event_batch_writes_rows(admin_client, db_conn):
    """
    UI_EVENT.OUTBOX.POST_BATCH — POST a batch of 3 events; expect 3 rows
    in ui_event_log with the documented payload fields.
    """
    # Snapshot the current row count so we can confirm our 3 were added
    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM ui_event_log")
        before = cur.fetchone()[0]

    resp = admin_client.post(
        "/api/ui-event/batch",
        json={
            "host_label": "e2e-test-kiosk",
            "events": [
                {"kind": "click", "target_id": "btn1", "target_text": "Save"},
                {"kind": "click", "target_id": "btn2", "target_text": "Cancel"},
                {"kind": "focus", "target_id": "input1", "page_url": "/streams"},
            ],
        },
    )
    assert resp.status_code in (200, 207), f"{resp.status_code} {resp.text[:200]}"
    body = resp.json()
    assert body.get("accepted", 0) >= 3, f"accepted={body.get('accepted')}, body={body}"

    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM ui_event_log")
        after = cur.fetchone()[0]
    assert after >= before + 3, (
        f"ui_event_log row count {before} → {after}; expected at least +3"
    )
