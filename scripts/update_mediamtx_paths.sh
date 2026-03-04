#!/bin/bash

# Sync mediamtx.yml with cameras.json settings
# - Updates paths: section with camera IDs for LL_HLS/NEOLINK/WEBRTC streams
# - Updates webrtcEncryption: setting from webrtc_global_settings.enable_dtls
#
# Run on container startup via start.sh

set -e

SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
cd "$SCRIPT_DIR"
PROJECT_ROOT=~/0_MOBIUS.NVR

. ~/.env.colors

CAMERAS_JSON="${PROJECT_ROOT}/config/cameras.json"
MEDIAMTX_YML="${PROJECT_ROOT}/packager/mediamtx.yml"
BACKUP_DIR="${PROJECT_ROOT}/packager/backups"

echo -e "${GREEN}=== Sync MediaMTX with cameras.json ===${NC}"
echo

# Backup
mkdir -p "$BACKUP_DIR"
cp "$MEDIAMTX_YML" "${BACKUP_DIR}/mediamtx.yml.$(date +%Y%m%d_%H%M%S)"
echo -e "${GREEN}✓${NC} Backup created"
echo

# ============================================================================
# DTLS/WebRTC Encryption Setting
# ============================================================================
# Read enable_dtls from cameras.json webrtc_global_settings
# This is required for iOS Safari to use WebRTC (iOS requires DTLS-SRTP)
ENABLE_DTLS=$(jq -r '.webrtc_global_settings.enable_dtls // false' "$CAMERAS_JSON")

if [[ "$ENABLE_DTLS" == "true" ]]; then
    WEBRTC_ENCRYPTION="yes"
    echo -e "${GREEN}✓${NC} DTLS enabled (iOS Safari WebRTC support)"
else
    WEBRTC_ENCRYPTION="no"
    echo -e "${YELLOW}!${NC} DTLS disabled (iOS will fall back to HLS)"
fi

# Update webrtcEncryption line in mediamtx.yml
# Uses sed to replace the value while preserving comments
if grep -q "^webrtcEncryption:" "$MEDIAMTX_YML"; then
    # Preserve any inline comment after the value
    sed -i "s/^webrtcEncryption:.*/webrtcEncryption: ${WEBRTC_ENCRYPTION} # Controlled by cameras.json webrtc_global_settings.enable_dtls/" "$MEDIAMTX_YML"
    echo -e "${GREEN}✓${NC} Updated webrtcEncryption: ${WEBRTC_ENCRYPTION}"
else
    echo -e "${YELLOW}⚠${NC} webrtcEncryption line not found in mediamtx.yml"
fi
echo

# ============================================================================
# Camera Paths Section
# ============================================================================
# Extract LL_HLS, NEOLINK, and WEBRTC camera IDs (all use MediaMTX paths)
# WEBRTC also needs MediaMTX paths - same FFmpeg→MediaMTX pipeline, different delivery to browser
LL_HLS_PATHS=$(jq -r '.devices | to_entries[] | select(.value.stream_type == "LL_HLS" or .value.stream_type == "NEOLINK" or .value.stream_type == "WEBRTC") | .key' "$CAMERAS_JSON")

if [[ -z "$LL_HLS_PATHS" ]]; then
    echo -e "${YELLOW}No LL_HLS, NEOLINK, or WEBRTC cameras found${NC}"
    exit 0
fi

echo -e "${YELLOW}Found LL_HLS/NEOLINK/WEBRTC cameras (creating sub + main paths):${NC}"
echo "$LL_HLS_PATHS" | while read -r path; do
    echo "  - $path (sub)"
    echo "  - ${path}_main (main)"
done
echo

# Build new paths section with both sub and main streams
# Sub stream: /camera_serial (transcoded, low-res for grid)
# Main stream: /camera_serial_main (passthrough, full-res for fullscreen)
PATHS_SECTION="paths:"
for path in $LL_HLS_PATHS; do
    # Sub stream path (default, used for grid view)
    PATHS_SECTION="${PATHS_SECTION}
  ${path}:
    source: publisher"
    # Main stream path (full resolution for fullscreen)
    PATHS_SECTION="${PATHS_SECTION}
  ${path}_main:
    source: publisher"
done

# Replace everything after "paths:" line
awk -v new="$PATHS_SECTION" '/^paths:/ {print new; exit} {print}' "$MEDIAMTX_YML" > "${MEDIAMTX_YML}.tmp"
mv "${MEDIAMTX_YML}.tmp" "$MEDIAMTX_YML"

echo -e "${GREEN}✓${NC} Updated mediamtx.yml"
echo -e "${YELLOW}Restart: docker compose restart nvr-packager${NC}"