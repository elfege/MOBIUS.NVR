"""
routes/host_agent_install.py — Phase 2c of the host-agent UX:
surface the install command directly in the Performance settings tab
so the operator doesn't have to remember the curl one-liner that
start.sh prints at boot.

Two endpoints:

  GET /api/host-agent/install-command?label=<host_label>
      Returns the curl one-liner the operator pastes into a kiosk's
      terminal to install/refresh the host-agent. Same shape as the
      hint already emitted by start.sh (see start.sh ~line 487):

          curl -sSLk "<NVR_URL>/host-agent/install.sh?label=<label>&token=<TOKEN>" | bash

      If NVR_API_TOKEN is not set in the server environment, the token
      query-string is omitted and a `warning` field flags the operator
      that authenticated-state retrieval won't be available until the
      vault is unsealed. Label is validated against the same identifier
      regex used elsewhere for safety: ^[a-z][a-z0-9_-]*$.

      Auth: session cookie (login_required) OR Bearer NVR_API_TOKEN.
      Either is sufficient — same dual-auth posture as host_state.py.

  GET /api/host-agent/compatibility?os=<linux|darwin|windows|unknown>
      Pure-function lookup: given a client-detected OS string, return
      whether the host-agent install path exists for it. No auth — the
      response is a static compatibility matrix and exposes nothing
      sensitive. The frontend uses this to grey-out the install button
      and surface the matching TODO file for the missing port.

Why a separate blueprint:
    The host-agent install flow is a thin UX layer over services that
    already exist (start.sh hint output, services/host_agent/*). Keeping
    it out of routes/host_state.py preserves that file's single
    responsibility (agent ingest + host_settings CRUD) and makes the
    Phase 2c diff small and reviewable.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request
from flask_login import current_user

logger = logging.getLogger(__name__)

host_agent_install_bp = Blueprint("host_agent_install", __name__)

# --------------------------------------------------------------------------
# Auth — same bearer-or-session posture as routes/host_state.py. We do NOT
# decorate with @login_required because the install command flow is also
# legitimately callable from CLI tooling that holds NVR_API_TOKEN.
# --------------------------------------------------------------------------
_API_TOKEN = os.environ.get("NVR_API_TOKEN", "").strip()


def _check_bearer() -> bool:
    """Validate Authorization: Bearer <token>. Mirrors host_state._check_bearer."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and _API_TOKEN:
        return auth[7:] == _API_TOKEN
    if not _API_TOKEN:
        # Dev fallback — allow LAN sources only.
        ip = (
            request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote_addr
            or ""
        )
        return ip.startswith(("10.", "172.", "192.168.", "127.")) or ip == "::1"
    return False


def _is_authenticated() -> bool:
    """True if the requester has a valid session OR a valid bearer token."""
    try:
        if current_user and current_user.is_authenticated:
            return True
    except Exception:
        # current_user is a proxy — outside a request context (unlikely here)
        # or when the login_manager isn't fully wired, it raises. Fall through.
        pass
    return _check_bearer()


# --------------------------------------------------------------------------
# Validation — same shape used for camera nicknames / operator identifiers.
# Lower-case start, then alnum + dash/underscore. Keeps the value safe to
# embed in a shell command without quoting gymnastics.
# --------------------------------------------------------------------------
_LABEL_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_LABEL_MAX_LEN = 64


def _validate_label(label: str) -> Optional[str]:
    """Return None if valid, or a human-readable error message otherwise."""
    if not label:
        return "label is required"
    if len(label) > _LABEL_MAX_LEN:
        return f"label too long (max {_LABEL_MAX_LEN} chars)"
    if not _LABEL_RE.match(label):
        return (
            "label must start with a lowercase letter and contain only "
            "lowercase letters, digits, '-' or '_'"
        )
    return None


# --------------------------------------------------------------------------
# Compatibility matrix — single source of truth for which OS targets have
# a working host-agent install path. Updated as new ports land.
# --------------------------------------------------------------------------
_COMPATIBILITY: Dict[str, Dict[str, Any]] = {
    "linux": {
        "compatible": True,
        "reason": None,
        "todo": None,
    },
    "darwin": {
        "compatible": False,
        "reason": "launchd port not yet implemented",
        "todo": "services/host_agent/install_host_agent_darwin.sh",
    },
    "windows": {
        "compatible": False,
        "reason": "PowerShell service not yet implemented",
        "todo": "services/host_agent/Install-HostAgent.ps1 + agent_windows.py",
    },
    "unknown": {
        "compatible": False,
        "reason": "unrecognized OS",
        "todo": None,
    },
}


def _nvr_base_url() -> str:
    """
    Build the NVR base URL the kiosk should curl back to.

    Preference order:
      1. NVR_PUBLIC_URL env var (operator override — e.g. when the NVR is
         behind a reverse proxy with a stable hostname).
      2. request.host_url (what the browser used to reach us). This is the
         realistic default and matches what the operator just typed in
         their address bar.

    request.host_url ends with a trailing slash; strip it for clean
    concatenation with the /host-agent/install.sh path.
    """
    override = os.environ.get("NVR_PUBLIC_URL", "").strip()
    if override:
        return override.rstrip("/")
    return (request.host_url or "").rstrip("/")


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@host_agent_install_bp.route("/api/host-agent/install-command", methods=["GET"])
def api_install_command():
    """
    Build the curl one-liner that installs the host-agent on a kiosk
    machine, parameterized by the target's host_label.

    Returns:
        200 { label, command, server_url[, warning] }
        400 { error } when label fails validation
        401 { error } when neither session nor bearer auth is present
    """
    if not _is_authenticated():
        return jsonify({"error": "authentication required"}), 401

    label = (request.args.get("label") or "").strip()
    err = _validate_label(label)
    if err:
        return jsonify({"error": err}), 400

    server_url = _nvr_base_url()
    token = _API_TOKEN
    warning: Optional[str] = None

    # The install endpoint is served by the same NVR at /host-agent/install.sh.
    # Format identical to the hint printed by start.sh — operators who saw the
    # boot output and operators who use the UI see the SAME command.
    if token:
        command = (
            f'curl -sSLk "{server_url}/host-agent/install.sh'
            f'?label={label}&token={token}" | bash'
        )
    else:
        command = (
            f'curl -sSLk "{server_url}/host-agent/install.sh'
            f'?label={label}" | bash'
        )
        warning = (
            "NVR_API_TOKEN is not set in the server environment — the install "
            "command is being returned without an embedded token. The agent "
            "will install but won't be able to authenticate to /api/host/state "
            "until a token is configured on both sides."
        )

    payload: Dict[str, Any] = {
        "label": label,
        "command": command,
        "server_url": server_url,
    }
    if warning:
        payload["warning"] = warning
    return jsonify(payload)


@host_agent_install_bp.route("/api/host-agent/compatibility", methods=["GET"])
def api_compatibility():
    """
    Static lookup against the compatibility matrix. No auth: the response
    is identical for everyone and exposes nothing sensitive. Used by the
    frontend to decide whether to enable the install button or show a
    greyed-out "not yet implemented" state with the corresponding TODO.

    Returns:
        200 { os, compatible, reason, todo }
    """
    raw = (request.args.get("os") or "").strip().lower()

    # Normalize a few common synonyms client code might send.
    synonyms = {
        "mac": "darwin",
        "macos": "darwin",
        "osx": "darwin",
        "win": "windows",
        "win32": "windows",
        "win64": "windows",
    }
    os_key = synonyms.get(raw, raw)

    entry = _COMPATIBILITY.get(os_key)
    if entry is None:
        # Anything we don't recognise collapses to the "unknown" bucket so
        # the frontend gets a consistent shape and isn't forced to handle
        # 404s in the gating logic.
        os_key = "unknown"
        entry = _COMPATIBILITY["unknown"]

    return jsonify({
        "os": os_key,
        "compatible": entry["compatible"],
        "reason": entry["reason"],
        "todo": entry["todo"],
    })
