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
from pathlib import Path
from typing import Any, Dict, Optional

from flask import Blueprint, Response, jsonify, request, send_file
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


# --------------------------------------------------------------------------
# Over-the-wire delivery — /host-agent/install.sh and /host-agent/agent.py
#
# Phase 2c originally only exposed `/api/host-agent/install-command`, which
# returned a curl one-liner pointing at `/host-agent/install.sh`. That route
# was never wired up. The operator running the printed one-liner got the
# Flask 404 HTML page piped into bash:
#
#     bash: line 1: syntax error near unexpected token `newline'
#     bash: line 1: `<!doctype html>'
#
# These two routes close that gap. The bash is SELF-CONTAINED — it pulls
# agent.py from the sibling endpoint and writes everything else inline. No
# git clone, no repo checkout on the kiosk.
#
# Auth model: the install.sh route accepts the bearer NVR_API_TOKEN as the
# `token` query-string instead of the Authorization header (curl-via-bash
# can't easily pass headers, and the bash script we hand back is itself a
# credential carrier — it bakes the token into the kiosk's config file).
# When NVR_API_TOKEN is unset we fall back to the same LAN-only posture as
# `_check_bearer` so dev environments still work.
#
# The agent.py route is a static file serve. We treat it as a public
# artifact protected by the same query-string token; agent.py contains no
# secrets, but gating the download keeps the surface tight.
# --------------------------------------------------------------------------
_HOST_AGENT_DIR = Path(__file__).resolve().parent.parent / "services" / "host_agent"


def _check_query_token() -> bool:
    """
    Validate the `token=` query-string against NVR_API_TOKEN.

    Falls back to LAN-only access when no server token is configured, so
    a dev environment without a vault-injected token still works on a
    trusted LAN. Mirrors `_check_bearer` but reads from `request.args`
    because curl-piped-to-bash cannot easily carry Authorization headers.
    """
    qtok = (request.args.get("token") or "").strip()
    if _API_TOKEN:
        return bool(qtok) and qtok == _API_TOKEN
    # Dev fallback — allow LAN sources only when no token is configured.
    ip = (
        request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or request.remote_addr
        or ""
    )
    return ip.startswith(("10.", "172.", "192.168.", "127.")) or ip == "::1"


def _render_install_script(label: str, token: str, server_url: str) -> str:
    """
    Build the self-contained installer bash script.

    The script:
      1. Writes agent.py to ~/.local/lib/mobius-nvr-host-agent/agent.py by
         downloading it from the sibling /host-agent/agent.py route on the
         same NVR. Token is reused.
      2. Writes the systemd user unit from an inline heredoc (no template
         file needed on the target). __AGENT_PATH__ is substituted at
         render time on the server, not on the target.
      3. Writes ~/.config/mobius-nvr-host-agent/config with the resolved
         SERVER_URL, HOST_LABEL and API_TOKEN baked in.
      4. Enables loginctl linger (sudo) so the daemon survives logout.
      5. Reloads systemd --user, enables and starts the unit.

    All steps run under `set -e`; failures print a clear "step N failed"
    message and exit non-zero so the operator sees the failure immediately
    in the terminal they pasted the curl into.
    """
    # Render the systemd unit body. We avoid relying on the tmpl file on
    # the target (one fewer round-trip + no temp-file management on the
    # kiosk). The path placeholder is replaced server-side so the kiosk
    # receives a ready-to-write unit.
    unit_template = _HOST_AGENT_DIR / "host-agent.service.tmpl"
    try:
        unit_body = unit_template.read_text(encoding="utf-8")
    except FileNotFoundError:
        # If the tmpl is missing the install MUST fail loudly, not produce
        # a half-written unit. Emit a script that just exits 1.
        return (
            "#!/usr/bin/env bash\n"
            "echo 'host-agent.service.tmpl missing on the NVR server — "
            "cannot generate installer' >&2\n"
            "exit 1\n"
        )

    # Where on the kiosk we'll drop agent.py. Outside the repo so the
    # installer works on machines that don't have the repo cloned.
    #
    # IMPORTANT: TWO renderings of this path are needed:
    #   - agent_install_path (with $HOME) — used in the BASH script, where
    #     $HOME expands at script run time. Bash variable assignments
    #     that contain this string MUST use double-quotes (single-quotes
    #     would defeat the expansion and leave a literal "$HOME" in the
    #     value, which then propagates everywhere).
    #   - agent_unit_path (with %h) — used in the systemd UNIT FILE.
    #     systemd does NOT expand $HOME inside ExecStart; it interprets
    #     $X as a literal environment-variable reference and silently
    #     blanks unknown ones. systemd's own user-home specifier is %h.
    #     This is the documented portable way (systemd.unit(5)).
    #
    # Earlier version used $HOME for both AND single-quoted the bash
    # assignment AND heredoc'd the unit with a single-quoted marker.
    # Net effect: systemd received the literal string `$HOME/.local/...`
    # and the service exited with status=0 in ~300ms before doing
    # anything. Observed on rog 2026-05-14.
    agent_install_dir  = "$HOME/.local/lib/mobius-nvr-host-agent"
    agent_install_path = f"{agent_install_dir}/agent.py"
    agent_unit_path    = "%h/.local/lib/mobius-nvr-host-agent/agent.py"

    # Substitute __AGENT_PATH__ now — the kiosk doesn't need the template
    # placeholder logic at all. Use the systemd-specifier rendering so
    # ExecStart gets a path systemd will actually resolve.
    unit_rendered = unit_body.replace("__AGENT_PATH__", agent_unit_path)

    # Build the agent download URL with the same token the operator gave us.
    # Pass label too for diagnostic logging on the server side later.
    agent_url = (
        f"{server_url}/host-agent/agent.py?label={label}&token={token}"
        if token
        else f"{server_url}/host-agent/agent.py?label={label}"
    )

    # Heredoc-safe rendering: bash heredocs respect $variable expansion
    # unless the marker is quoted. We use 'UNIT_EOF' / 'CFG_EOF' (single-
    # quoted markers) so the content is taken verbatim, no $X surprises.
    return f"""#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# mobius-nvr-host-agent over-the-wire installer
#
# Generated by the NVR at /host-agent/install.sh for host_label='{label}'.
# Self-contained: downloads agent.py from the NVR, writes the systemd
# user unit + config inline, enables linger, starts the service.
#
# Re-running this is idempotent (the unit file is regenerated, the
# service is restarted, the existing config is OVERWRITTEN with the
# server-provided values — operators who want to keep a custom config
# should edit ~/.config/mobius-nvr-host-agent/config after install).
# ---------------------------------------------------------------------------
set -euo pipefail

GREEN='\\033[0;32m'
YELLOW='\\033[1;33m'
RED='\\033[0;31m'
NC='\\033[0m'

UNIT_NAME='mobius-nvr-host-agent.service'
UNIT_DIR="${{XDG_CONFIG_HOME:-$HOME/.config}}/systemd/user"
UNIT_PATH="$UNIT_DIR/$UNIT_NAME"
CFG_DIR="${{XDG_CONFIG_HOME:-$HOME/.config}}/mobius-nvr-host-agent"
CFG_FILE="$CFG_DIR/config"
AGENT_DIR="{agent_install_dir}"
AGENT_PATH="{agent_install_path}"
AGENT_URL="{agent_url}"

step() {{ echo -e "${{GREEN}}>>> $1${{NC}}"; }}
warn() {{ echo -e "${{YELLOW}}!!! $1${{NC}}"; }}
fail() {{ echo -e "${{RED}}*** $1${{NC}}" >&2; exit 1; }}

echo '================================================================='
echo '  MOBIUS.NVR host-agent — over-the-wire installer'
echo '================================================================='
echo "  host_label : {label}"
echo "  agent.py   : $AGENT_PATH"
echo "  unit       : $UNIT_PATH"
echo "  config     : $CFG_FILE"
echo

# 1) sanity — python3 and requests
command -v python3 >/dev/null 2>&1 || fail 'python3 not on PATH — install python3 first'
if ! python3 -c 'import requests' 2>/dev/null; then
    warn 'python3 requests module missing — installing for user'
    python3 -m pip install --user requests || \\
        fail 'pip install requests failed — install manually then re-run'
fi

# 2) download agent.py
step 'downloading agent.py from the NVR'
mkdir -p "$AGENT_DIR"
if command -v curl >/dev/null 2>&1; then
    curl -sSLk -o "$AGENT_PATH" "$AGENT_URL" || fail 'agent.py download failed (curl)'
elif command -v wget >/dev/null 2>&1; then
    wget -q --no-check-certificate -O "$AGENT_PATH" "$AGENT_URL" || fail 'agent.py download failed (wget)'
else
    fail 'neither curl nor wget found — install one and re-run'
fi
# Sanity-check the download: it must be a python script, not the Flask 404
# HTML page (which is what triggered the original bug). Detect the shebang
# at byte 0 and bail loudly if it's anything else.
head -c 2 "$AGENT_PATH" | grep -q '^#!' || \\
    fail "agent.py download corrupted — file does not start with shebang (server returned an error page?)"
chmod +x "$AGENT_PATH"

# 3) write systemd user unit
step 'installing systemd user unit'
mkdir -p "$UNIT_DIR"
cat > "$UNIT_PATH" <<'UNIT_EOF'
{unit_rendered}UNIT_EOF

# 4) write config with token + label baked in
step 'writing config (mode 600)'
mkdir -p "$CFG_DIR"
chmod 700 "$CFG_DIR"
cat > "$CFG_FILE" <<'CFG_EOF'
# mobius-nvr-host-agent — per-host configuration.
# Generated by the over-the-wire installer. Edit and `systemctl --user
# restart mobius-nvr-host-agent.service` to pick up changes.
SERVER_URL={server_url}
HOST_LABEL={label}
API_TOKEN={token}
POLL_INTERVAL=5
INSECURE_TLS=1
CFG_EOF
chmod 600 "$CFG_FILE"

# 5) import DISPLAY into the user's systemd environment so xset works
if [[ -n "${{DISPLAY:-}}" ]]; then
    systemctl --user import-environment DISPLAY XAUTHORITY 2>/dev/null || true
    step "imported DISPLAY=$DISPLAY into systemd --user"
else
    warn 'DISPLAY not set in this shell — run after graphical login:'
    warn '    systemctl --user import-environment DISPLAY XAUTHORITY'
fi

# 6) enable linger so the agent persists across logout
if command -v loginctl >/dev/null 2>&1; then
    if loginctl show-user "$USER" 2>/dev/null | grep -q '^Linger=yes'; then
        step "linger already enabled for $USER"
    else
        warn "enabling linger for $USER (sudo required)"
        sudo loginctl enable-linger "$USER" || \\
            warn 'enable-linger failed — agent will stop at logout until you fix this'
    fi
fi

# 7) reload + enable + start
step 'systemctl --user daemon-reload'
systemctl --user daemon-reload
step "enabling + starting $UNIT_NAME"
systemctl --user enable "$UNIT_NAME" 2>&1 | tail -1
systemctl --user restart "$UNIT_NAME"

sleep 1
echo
echo '================================================================='
echo '  Status'
echo '================================================================='
systemctl --user status "$UNIT_NAME" --no-pager 2>&1 | head -12 || true
echo
echo "  Logs:    journalctl --user -u $UNIT_NAME -f"
echo "  Restart: systemctl --user restart $UNIT_NAME"
echo "  Stop:    systemctl --user disable --now $UNIT_NAME"
echo
"""


@host_agent_install_bp.route("/host-agent/install.sh", methods=["GET"])
def host_agent_install_sh():
    """
    Serve the self-contained installer bash script.

    URL: GET /host-agent/install.sh?label=<host_label>&token=<NVR_API_TOKEN>

    The query-string token IS the auth — see _check_query_token. We do NOT
    require Authorization: Bearer in addition (curl-piped-to-bash can't
    set it). The token in the URL becomes the API_TOKEN baked into the
    kiosk's config, so they're the same secret either way.

    Returns text/plain (so the kiosk's terminal renders it readably if
    the operator runs `curl ... | less` to inspect instead of `| bash`).
    """
    if not _check_query_token():
        # 401 + text/plain so the operator who pasted into a terminal sees
        # 'unauthorized' instead of a Flask HTML error page that would
        # break the bash pipeline silently.
        return Response(
            "echo 'unauthorized — bad or missing token' >&2; exit 1\n",
            status=401,
            mimetype="text/plain",
        )

    label = (request.args.get("label") or "").strip()
    err = _validate_label(label)
    if err:
        # Same posture: emit valid bash that prints the error and exits
        # non-zero. The operator's `| bash` pipeline then surfaces the
        # message instead of a syntax error on '<!doctype html>'.
        return Response(
            f"echo 'install.sh: invalid label: {err}' >&2; exit 1\n",
            status=400,
            mimetype="text/plain",
        )

    server_url = _nvr_base_url()
    script = _render_install_script(
        label=label,
        token=_API_TOKEN,  # bake the SERVER's token into config; what the
                           # request used is already validated above.
        server_url=server_url,
    )
    return Response(
        script,
        mimetype="text/plain",
        headers={
            # Tell curl/wget the suggested filename if the operator runs
            # `curl -O` instead of piping to bash.
            "Content-Disposition": 'inline; filename="install.sh"',
            "Cache-Control": "no-store",
        },
    )


@host_agent_install_bp.route("/host-agent/agent.py", methods=["GET"])
def host_agent_agent_py():
    """
    Serve the literal services/host_agent/agent.py as text/x-python.

    Same auth as install.sh (query-string token). The installer script
    above downloads this URL onto the kiosk filesystem; no other consumer
    should hit it. Cached for one minute to spare the server on repeat
    installs from a script in a tight loop, but no longer (operators
    sometimes edit agent.py and re-install).
    """
    if not _check_query_token():
        return Response(
            "# unauthorized — bad or missing token\n",
            status=401,
            mimetype="text/plain",
        )

    agent_path = _HOST_AGENT_DIR / "agent.py"
    if not agent_path.is_file():
        logger.error("host-agent agent.py missing at %s", agent_path)
        return Response(
            "# server-side agent.py missing — check NVR install\n",
            status=500,
            mimetype="text/plain",
        )

    # send_file sets Content-Length and handles range requests; we override
    # the mimetype because the default for .py is application/octet-stream
    # on some Flask versions and we want the kiosk's curl to treat it as
    # text (so chmod +x is meaningful afterwards).
    return send_file(
        str(agent_path),
        mimetype="text/x-python",
        as_attachment=False,
        download_name="agent.py",
        max_age=60,
    )


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
