#!/bin/bash
# update_eufy_ips.sh - Update Eufy camera IPs in cameras.json from DHCP/ARP
#
# Called by start.sh before container startup to ensure cameras.json
# has current IP addresses for Eufy cameras (used by eufy_bridge.sh
# to build stationIPAddresses for P2P connections).
#
# Updates: config/cameras.json (host field for each Eufy camera)
#
# Usage:
#   ~/0_NVR/scripts/update_eufy_ips.sh           # Update cameras.json
#   ~/0_NVR/scripts/update_eufy_ips.sh --dry-run # Show what would change

SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"
SCRIPT_R_PATH=$(realpath "${BASH_SOURCE[0]}")
SCRIPT_DIR="${SCRIPT_R_PATH%${SCRIPT_NAME}}"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR" &>/dev/null || true

CAMERAS_JSON="config/cameras.json"
DRY_RUN=false

[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

if [[ ! -f "$CAMERAS_JSON" ]]; then
    echo "ERROR: $CAMERAS_JSON not found"
    exit 1
fi

# Get list of Eufy cameras with their current IPs from cameras.json
# We use the existing host or rtsp.host as the IP to verify
eufy_cameras=$(jq -r '
    .devices | to_entries[]
    | select(.value.type == "eufy")
    | "\(.key)|\(.value.name)|\(.value.host // .value.rtsp.host // "unknown")"
' "$CAMERAS_JSON")

if [[ -z "$eufy_cameras" ]]; then
    echo "No Eufy cameras found in cameras.json"
    exit 0
fi

updated=0

echo "$eufy_cameras" | while IFS='|' read -r serial name current_ip; do
    if [[ "$current_ip" == "unknown" || "$current_ip" == "null" || -z "$current_ip" ]]; then
        echo "WARN: $name ($serial) has no IP configured - skipping"
        continue
    fi

    # Ping to ensure ARP cache is fresh
    ping -c 1 -W 1 "$current_ip" &>/dev/null

    # Get MAC from ARP
    mac=$(arp -a 2>/dev/null | grep "($current_ip)" | head -1 | awk '{print $4}')

    if [[ -z "$mac" || "$mac" == "<incomplete>" ]]; then
        echo "WARN: $name ($serial) at $current_ip - no ARP entry (device offline?)"
        continue
    fi

    # Check current values in cameras.json
    current_host=$(jq -r ".devices[\"$serial\"].host // \"null\"" "$CAMERAS_JSON")
    current_mac=$(jq -r ".devices[\"$serial\"].mac // \"null\"" "$CAMERAS_JSON")

    if [[ "$current_host" == "$current_ip" && "$current_mac" == "$mac" ]]; then
        echo "OK: $name - host=$current_ip, mac=$mac (no change)"
        continue
    fi

    echo "UPDATE: $name ($serial)"
    echo "  host: $current_host -> $current_ip"
    echo "  mac:  $current_mac -> $mac"

    if [[ "$DRY_RUN" == false ]]; then
        # Update cameras.json using jq (preserves structure)
        tmp=$(mktemp)
        jq --arg serial "$serial" --arg ip "$current_ip" --arg mac "$mac" \
            '.devices[$serial].host = $ip | .devices[$serial].mac = $mac' \
            "$CAMERAS_JSON" > "$tmp" && mv "$tmp" "$CAMERAS_JSON"
        ((updated++))
    fi
done

if [[ "$DRY_RUN" == true ]]; then
    echo ""
    echo "(dry-run mode - no changes made)"
fi
