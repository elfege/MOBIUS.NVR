#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# install_host_agent.sh
#
# One-shot installer for mobius-nvr-host-agent on a kiosk host (rog,
# laptops, anything running the NVR's Chrome kiosk). Run this on the
# TARGET host (the one whose displays + load you want to report).
#
# What it does:
#   1. Verifies python3 and `requests` are available
#   2. Renders the systemd unit template with the absolute path to
#      agent.py (so updates to the repo are picked up automatically)
#   3. Installs the unit at ~/.config/systemd/user/mobius-nvr-host-agent.service
#   4. Imports DISPLAY/XAUTHORITY into the systemd user environment so
#      xset can talk to X11 from inside the service
#   5. Enables linger for the user (so the agent runs even when nobody
#      is logged in graphically — the NVR kiosk needs that)
#   6. Creates the config skeleton at
#      ~/.config/mobius-nvr-host-agent/config (mode 600), leaves the
#      operator to fill in API_TOKEN
#
# Idempotent: re-running upgrades the unit, restarts the service, and
# leaves the config alone if it already exists.
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_PATH="${SCRIPT_DIR}/agent.py"
TEMPLATE="${SCRIPT_DIR}/host-agent.service.tmpl"

UNIT_NAME="mobius-nvr-host-agent.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_PATH="${UNIT_DIR}/${UNIT_NAME}"
CFG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/mobius-nvr-host-agent"
CFG_FILE="${CFG_DIR}/config"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo "================================================================="
echo "  MOBIUS.NVR host agent — installer"
echo "================================================================="
echo "  agent.py:   $AGENT_PATH"
echo "  unit file:  $UNIT_PATH"
echo "  config:     $CFG_FILE"
echo

# 1) Sanity checks
if ! command -v python3 >/dev/null 2>&1; then
    echo -e "${RED}python3 not on PATH — install python3 first${NC}" >&2
    exit 1
fi

if ! python3 -c "import requests" 2>/dev/null; then
    echo -e "${YELLOW}python3 'requests' module not installed; installing for the user...${NC}"
    if ! python3 -m pip install --user requests; then
        echo -e "${RED}failed to install 'requests'. Install manually with:${NC}" >&2
        echo "    python3 -m pip install --user requests" >&2
        exit 1
    fi
fi

if [[ ! -f "$AGENT_PATH" ]]; then
    echo -e "${RED}agent.py not found at $AGENT_PATH${NC}" >&2
    exit 1
fi
chmod +x "$AGENT_PATH"

# 2) Render and install the systemd unit
mkdir -p "$UNIT_DIR"
sed "s|__AGENT_PATH__|$AGENT_PATH|g" "$TEMPLATE" > "$UNIT_PATH"
echo -e "${GREEN}installed unit: $UNIT_PATH${NC}"

# 3) Import the user's DISPLAY/XAUTHORITY into the systemd user environment.
#    Without this xset has nothing to talk to from inside the service.
if [[ -n "${DISPLAY:-}" ]]; then
    systemctl --user import-environment DISPLAY XAUTHORITY 2>/dev/null || true
    echo -e "${GREEN}imported DISPLAY=$DISPLAY${NC}"
else
    echo -e "${YELLOW}DISPLAY not set in current shell; you may need to run:${NC}"
    echo "    systemctl --user import-environment DISPLAY XAUTHORITY"
    echo "  AFTER logging into a graphical session."
fi

# 4) Enable linger so the agent persists across logout (kiosks must
#    keep reporting even when no human is logged in)
if command -v loginctl >/dev/null 2>&1; then
    if ! loginctl show-user "$USER" 2>/dev/null | grep -q '^Linger=yes'; then
        echo -e "${YELLOW}enabling linger for user $USER (sudo required)...${NC}"
        sudo loginctl enable-linger "$USER"
    else
        echo -e "${GREEN}linger already enabled for $USER${NC}"
    fi
fi

# 5) Reload systemd and (re)start the unit
systemctl --user daemon-reload
systemctl --user enable "$UNIT_NAME" 2>&1 | tail -1
systemctl --user restart "$UNIT_NAME"

# 6) Config skeleton — only if it doesn't exist
if [[ ! -f "$CFG_FILE" ]]; then
    mkdir -p "$CFG_DIR"
    cat > "$CFG_FILE" <<EOF
# mobius-nvr-host-agent — per-host configuration.
# Mode 600. Edit the values below, then:
#     systemctl --user restart $UNIT_NAME

# REQUIRED. The full URL to the NVR (matches the kiosk's address).
SERVER_URL=https://mobius.nvr:8444

# REQUIRED. A short, stable label this host reports under. The NVR
# uses it to address SocketIO broadcasts back to this kiosk.
HOST_LABEL=$(hostname)

# REQUIRED. The Bearer token. Must match the NVR_API_TOKEN env var on
# the server (set in ~/0_MOBIUS.NVR/.env or via secrets manager).
API_TOKEN=

# OPTIONAL. Seconds between snapshots. Default 5.
POLL_INTERVAL=5

# OPTIONAL. Set to 1 if the NVR uses a self-signed certificate that
# the host doesn't trust. Default 0 (verify TLS).
INSECURE_TLS=1
EOF
    chmod 600 "$CFG_FILE"
    echo -e "${YELLOW}created config skeleton at $CFG_FILE${NC}"
    echo -e "${YELLOW}edit it to set API_TOKEN, then restart:${NC}"
    echo "    systemctl --user restart $UNIT_NAME"
else
    echo -e "${GREEN}config already exists at $CFG_FILE — left untouched${NC}"
fi

# 7) Final status
sleep 1
echo
echo "================================================================="
echo "  Status"
echo "================================================================="
systemctl --user status "$UNIT_NAME" --no-pager 2>&1 | head -10 || true
echo
echo "  Logs:        journalctl --user -u $UNIT_NAME -f"
echo "  Restart:     systemctl --user restart $UNIT_NAME"
echo "  Disable:     systemctl --user disable --now $UNIT_NAME"
echo
