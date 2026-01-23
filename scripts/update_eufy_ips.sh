#!/usr/bin/env bash
# update_eufy_ips.sh
#
# ============================================================================
# EUFY CAMERA IP DISCOVERY AND UPDATE SCRIPT
# ============================================================================
#
# PURPOSE:
#   Discovers Eufy camera IPs on the local network via ARP and updates
#   cameras.json with the correct host and MAC addresses.
#
# PREREQUISITES:
#   - Run from host machine (not Docker container)
#   - Eufy cameras must be powered on and connected to network
#   - arp command available
#   - jq installed
#
# USAGE:
#   ./scripts/update_eufy_ips.sh [--dry-run]
#
# OPTIONS:
#   --dry-run    Show what would be updated without making changes
#
# ============================================================================

set -euo pipefail

# Configuration
CAMERAS_JSON="config/cameras.json"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=true
    log_info "Dry run mode - no changes will be made"
fi

cd "$PROJECT_DIR"

# Check prerequisites
if ! command -v jq &> /dev/null; then
    log_error "jq is required but not installed"
    exit 1
fi

if [[ ! -f "$CAMERAS_JSON" ]]; then
    log_error "cameras.json not found at $CAMERAS_JSON"
    exit 1
fi

# Known Eufy MAC prefixes (OUI)
# 04:17:b6 - Eufy (Anker Innovation)
# 10:2c:b1 - Eufy (Anker Innovation)
EUFY_MAC_PREFIXES=("04:17:b6" "10:2c:b1")

echo "============================================================================"
echo "EUFY CAMERA IP DISCOVERY"
echo "============================================================================"
echo ""

# Get all Eufy cameras from cameras.json
log_info "Reading Eufy cameras from cameras.json..."
eufy_cameras=$(jq -r '.devices | to_entries[] | select(.value.type == "eufy") | "\(.key):\(.value.name):\(.value.rtsp.host // .value.host // "unknown")"' "$CAMERAS_JSON")

if [[ -z "$eufy_cameras" ]]; then
    log_warn "No Eufy cameras found in cameras.json"
    exit 0
fi

echo "Found Eufy cameras:"
echo "$eufy_cameras" | while IFS=: read -r serial name current_ip; do
    echo "  - $name ($serial): current IP = $current_ip"
done
echo ""

# Scan network for Eufy devices via ARP
log_info "Scanning ARP table for Eufy devices..."
echo ""

# Build associative array of IP -> MAC from ARP
declare -A arp_cache
while read -r line; do
    # Parse ARP output: ? (192.168.10.84) at 04:17:b6:f4:30:c7 [ether] on ...
    ip=$(echo "$line" | grep -oP '\(\K[0-9.]+(?=\))')
    mac=$(echo "$line" | awk '{print $4}')
    if [[ -n "$ip" && -n "$mac" && "$mac" != "<incomplete>" ]]; then
        arp_cache["$ip"]="$mac"
    fi
done < <(arp -a 2>/dev/null)

# Find Eufy devices by MAC prefix
declare -A eufy_devices  # IP -> MAC
for ip in "${!arp_cache[@]}"; do
    mac="${arp_cache[$ip]}"
    mac_prefix="${mac:0:8}"
    for prefix in "${EUFY_MAC_PREFIXES[@]}"; do
        if [[ "$mac_prefix" == "$prefix" ]]; then
            eufy_devices["$ip"]="$mac"
            log_info "Found Eufy device: $ip -> $mac"
        fi
    done
done

if [[ ${#eufy_devices[@]} -eq 0 ]]; then
    log_warn "No Eufy devices found on network via ARP"
    log_info "Try pinging your Eufy camera IPs first to populate ARP cache"
    exit 0
fi

echo ""
echo "============================================================================"
echo "UPDATING cameras.json"
echo "============================================================================"
echo ""

# Build jq update command
jq_updates=""

# For each camera, try to find matching IP from rtsp.host or existing host
echo "$eufy_cameras" | while IFS=: read -r serial name current_ip; do
    if [[ "$current_ip" == "unknown" || "$current_ip" == "null" ]]; then
        log_warn "Camera $name ($serial) has no known IP - skipping"
        continue
    fi

    # Check if this IP is in our discovered devices
    if [[ -v "eufy_devices[$current_ip]" ]]; then
        mac="${eufy_devices[$current_ip]}"
        log_success "Matched $name ($serial): $current_ip -> $mac"

        if [[ "$DRY_RUN" == false ]]; then
            # Update cameras.json
            tmp_file=$(mktemp)
            jq --arg serial "$serial" --arg ip "$current_ip" --arg mac "$mac" \
                '.devices[$serial].host = $ip | .devices[$serial].mac = $mac' \
                "$CAMERAS_JSON" > "$tmp_file" && mv "$tmp_file" "$CAMERAS_JSON"
        else
            echo "  [DRY RUN] Would set: host=$current_ip, mac=$mac"
        fi
    else
        log_warn "Camera $name IP $current_ip not found in ARP cache"
        log_info "  Try: ping -c 1 $current_ip"
    fi
done

echo ""
if [[ "$DRY_RUN" == false ]]; then
    log_success "cameras.json updated"
    echo ""
    echo "Updated Eufy camera entries:"
    jq -r '.devices | to_entries[] | select(.value.type == "eufy") | "  \(.value.name): host=\(.value.host // "null"), mac=\(.value.mac // "null")"' "$CAMERAS_JSON"
else
    log_info "Dry run complete - no changes made"
fi

echo ""
echo "============================================================================"
echo "NEXT STEPS"
echo "============================================================================"
echo ""
echo "1. Restart NVR container to apply changes:"
echo "   docker compose restart nvr"
echo ""
echo "2. The eufy_bridge.sh will automatically read these IPs"
echo "   and configure stationIPAddresses for P2P connections"
echo ""
