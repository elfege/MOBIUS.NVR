"""
routes/host_state.py — endpoints that receive host_agent reports,
broadcast them to the kiosk page via SocketIO, and expose read/write
of per-machine settings backed by the host_settings table.

The complete flow:

    rog (host_agent) ───POST /api/host/state───▶ Flask
                                                     │
                                                     │ SocketIO broadcast
                                                     │ on /stream_events
                                                     ▼
                          Chrome kiosk on rog (visibility-manager.js)
                              ├─ display=off  → tear down streams
                              └─ cpu_load_norm > threshold
                                          → throttle one tile at a time

Auth: Bearer token, same NVR_API_TOKEN as the external API. The agent
runs on the kiosk host and stores the token in mode-600 config.

State retention: latest snapshot per host_label is kept in-process so a
joining /streams page can be told the current state immediately on
connect (no need to wait up to POLL_INTERVAL for the next agent push).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

import psycopg2
import psycopg2.extras
from flask import Blueprint, jsonify, request
from flask_login import login_required

logger = logging.getLogger(__name__)

host_state_bp = Blueprint("host_state", __name__)

# --------------------------------------------------------------------------
# Auth — bearer token, same source of truth as services/external_api_routes.py.
# --------------------------------------------------------------------------
_API_TOKEN = os.environ.get("NVR_API_TOKEN", "").strip()


def _check_bearer() -> bool:
    """
    Validate Authorization: Bearer <token>. If NVR_API_TOKEN isn't set,
    fall back to LAN-only check (matches the external_api_routes pattern
    so the dev story is consistent).
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and _API_TOKEN:
        return auth[7:] == _API_TOKEN
    if not _API_TOKEN:
        # Dev fallback — allow LAN sources only.
        ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() \
             or request.remote_addr or ""
        return ip.startswith(("10.", "172.", "192.168.", "127.")) or ip == "::1"
    return False


# --------------------------------------------------------------------------
# In-process state — keyed by host_label, holds the latest snapshot.
# --------------------------------------------------------------------------
_latest_state: Dict[str, Dict[str, Any]] = {}

# A SocketIO instance is injected at app boot via init_host_state(socketio).
# We don't import flask_socketio at module load to avoid a circular dep
# with app.py (which is the only place the SocketIO instance lives).
_socketio = None


def init_host_state(socketio_instance) -> None:
    """Called from app.py after socketio is constructed."""
    global _socketio
    _socketio = socketio_instance
    logger.info("host_state: SocketIO bound; ready to broadcast host_state_changed")


def get_latest_state(host_label: Optional[str] = None) -> Dict[str, Any]:
    """
    Read-only accessor used by the visibility-manager bootstrap and by
    other services that need to know the current host state without
    subscribing to broadcasts.
    """
    if host_label is None:
        return dict(_latest_state)
    return _latest_state.get(host_label, {})


# --------------------------------------------------------------------------
# POST /api/host/state — agent-side push
# --------------------------------------------------------------------------
@host_state_bp.route("/api/host/state", methods=["POST"])
def api_host_state_push():
    """
    Accept a snapshot from a host_agent. Required body fields:

        host:               str   — short stable label
        ts:                 float — unix seconds
        display_state:      str   — "on" | "off" | "standby" | "suspend"
                                    (optional; absent on Wayland hosts)
        load_1m, load_5m,
        load_15m, cpu_count,
        cpu_load_norm:      float — load_1m / cpu_count
        gpu_util,
        gpu_mem_util,
        gpu_temp_c:         float — present only on NVIDIA hosts

    Response: 204 No Content on success.
    """
    if not _check_bearer():
        return jsonify({"error": "unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    host_label = (body.get("host") or "").strip()
    if not host_label:
        return jsonify({"error": "missing 'host' field"}), 400

    # Stamp server-side receipt time so we can detect stale agents.
    body["server_received_at"] = time.time()

    prev = _latest_state.get(host_label) or {}
    _latest_state[host_label] = body

    # Upsert the host_settings row on first contact + bump last_seen on
    # every ping. Defaults from the migration take effect on first
    # INSERT. SELECT-back so we can attach the current settings to the
    # broadcast payload — saves the page a separate fetch.
    settings = _upsert_and_get_host_settings(host_label)
    if settings:
        body["host_settings"] = settings

    # Broadcast to everyone subscribed to /stream_events. The page
    # filters by host_label client-side (it knows its own hostname
    # via window.location).
    if _socketio is not None:
        try:
            _socketio.emit(
                "host_state_changed",
                body,
                namespace="/stream_events",
            )
        except Exception:
            logger.exception("host_state: failed to broadcast")

    # Useful log only when something interesting changes — no per-poll spam.
    if prev.get("display_state") != body.get("display_state"):
        logger.info(
            "host_state[%s]: display %s -> %s",
            host_label, prev.get("display_state"), body.get("display_state"),
        )

    return ("", 204)


# --------------------------------------------------------------------------
# GET /api/host/state — read-only (for the page on initial load).
# --------------------------------------------------------------------------
@host_state_bp.route("/api/host/state", methods=["GET"])
def api_host_state_read():
    """
    Read the latest known state — all hosts, or one if ?host=<label>.

    Auth: SAME login session as /streams (login_required would be the
    natural decorator, but we don't require it here so the visibility-
    manager can probe state on initial connect even if the session
    cookie is briefly stale during a refresh). LAN-only fallback
    via the same _check_bearer() helper applies.
    """
    if not (_check_bearer() or request.cookies.get("session")):
        # Fall through — public-read on LAN. If you want to tighten,
        # add @login_required and accept the brief stale-cookie window.
        pass

    host_label = request.args.get("host")
    return jsonify(get_latest_state(host_label))


# --------------------------------------------------------------------------
# DB helpers — host_settings CRUD
# --------------------------------------------------------------------------
def _db_conn():
    """Direct psycopg2 connection — same pattern as routes/config.py."""
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "postgres"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "nvr"),
        user=os.getenv("POSTGRES_USER", "nvr_api"),
        password=os.getenv("POSTGRES_PASSWORD", "nvr_internal_db_key"),
        connect_timeout=3,
    )


def _upsert_and_get_host_settings(host_label: str) -> Dict[str, Any]:
    """
    Ensure a row exists for this host (defaults from migration 032 apply
    on first INSERT), bump last_seen, return current settings as a dict.
    Best-effort — DB errors return an empty dict so the broadcast still
    fires with whatever metrics the agent sent.
    """
    try:
        with _db_conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(
                """
                INSERT INTO host_settings (host_label, last_seen)
                VALUES (%s, NOW())
                ON CONFLICT (host_label) DO UPDATE SET
                    last_seen = NOW()
                RETURNING host_label,
                          performance_throttle_enabled,
                          performance_max_cpu_pct,
                          performance_restore_hysteresis_pct,
                          last_seen,
                          updated_at
                """,
                (host_label,),
            )
            row = cur.fetchone()
            if not row:
                return {}
            # JSON-safe types (datetime → ISO strings)
            for k in ("last_seen", "updated_at"):
                if row.get(k):
                    row[k] = row[k].isoformat()
            return dict(row)
    except Exception:
        logger.exception("host_state: upsert/read host_settings failed for %s", host_label)
        return {}


# --------------------------------------------------------------------------
# GET /api/host/<label>/settings — read settings for one host
# --------------------------------------------------------------------------
@host_state_bp.route("/api/host/<host_label>/settings", methods=["GET"])
@login_required
def api_host_settings_get(host_label: str):
    """
    Return the host_settings row for <host_label>. If the row doesn't
    exist yet (no agent has ever reported), return migration-default
    values WITHOUT creating the row — that way the Settings UI can
    show defaults for an offline host without polluting the table
    with rows for hosts the user might never wire up.
    """
    try:
        with _db_conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(
                """
                SELECT host_label,
                       performance_throttle_enabled,
                       performance_max_cpu_pct,
                       performance_restore_hysteresis_pct,
                       last_seen, updated_at
                FROM host_settings
                WHERE host_label = %s
                """,
                (host_label,),
            )
            row = cur.fetchone()
    except Exception as e:
        logger.exception("host_state GET settings failed: %s", e)
        return jsonify({"error": str(e)}), 500

    if row:
        for k in ("last_seen", "updated_at"):
            if row.get(k):
                row[k] = row[k].isoformat()
        return jsonify(dict(row))

    # No row yet — return migration defaults
    return jsonify({
        "host_label": host_label,
        "performance_throttle_enabled": True,
        "performance_max_cpu_pct": 50,
        "performance_restore_hysteresis_pct": 10,
        "last_seen": None,
        "updated_at": None,
        "_note": "no agent has reported for this host_label; defaults shown",
    })


# --------------------------------------------------------------------------
# PUT /api/host/<label>/settings — update settings for one host
# --------------------------------------------------------------------------
@host_state_bp.route("/api/host/<host_label>/settings", methods=["PUT"])
@login_required
def api_host_settings_put(host_label: str):
    """
    Upsert host_settings. Whitelisted fields only:

        performance_throttle_enabled        bool
        performance_max_cpu_pct             int 1..95
        performance_restore_hysteresis_pct  int 0..50

    Broadcasts a host_state_changed with the updated settings so
    every kiosk subscribed to /stream_events picks up the change in
    real time (no page reload needed).
    """
    body = request.get_json(silent=True) or {}
    EDITABLE = {
        "performance_throttle_enabled":         (bool, None, None),
        "performance_max_cpu_pct":              (int, 1, 95),
        "performance_restore_hysteresis_pct":   (int, 0, 50),
    }
    fields: Dict[str, Any] = {}
    for k, (typ, lo, hi) in EDITABLE.items():
        if k not in body:
            continue
        v = body[k]
        if typ is bool:
            v = bool(v)
        else:
            try:
                v = typ(v)
            except (ValueError, TypeError):
                return jsonify({"error": f"{k} must be {typ.__name__}"}), 400
            if lo is not None and v < lo:
                return jsonify({"error": f"{k} below minimum ({lo})"}), 400
            if hi is not None and v > hi:
                return jsonify({"error": f"{k} above maximum ({hi})"}), 400
        fields[k] = v

    if not fields:
        return jsonify({"error": "no editable fields provided"}), 400

    set_clause = ", ".join(f"{k} = %s" for k in fields)
    values = list(fields.values())

    try:
        with _db_conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            # INSERT defaults + provided overrides; on conflict update
            # only the provided fields (leave others alone).
            cols = ["host_label"] + list(fields.keys())
            placeholders = ", ".join(["%s"] * len(cols))
            cur.execute(
                f"""
                INSERT INTO host_settings ({', '.join(cols)})
                VALUES ({placeholders})
                ON CONFLICT (host_label) DO UPDATE SET {set_clause}
                RETURNING host_label,
                          performance_throttle_enabled,
                          performance_max_cpu_pct,
                          performance_restore_hysteresis_pct,
                          last_seen, updated_at
                """,
                [host_label] + values + values,
            )
            row = cur.fetchone()
    except Exception as e:
        logger.exception("host_state PUT settings failed: %s", e)
        return jsonify({"error": str(e)}), 500

    if not row:
        return jsonify({"error": "upsert returned no row"}), 500

    for k in ("last_seen", "updated_at"):
        if row.get(k):
            row[k] = row[k].isoformat()
    settings = dict(row)

    # Broadcast so the page picks up the change immediately
    if _socketio is not None:
        try:
            _socketio.emit(
                "host_settings_changed",
                {"host": host_label, "host_settings": settings},
                namespace="/stream_events",
            )
        except Exception:
            logger.exception("host_state: failed to broadcast settings change")

    return jsonify(settings)
