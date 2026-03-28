#!/bin/bash

# Sync mediamtx.yml from DB cameras table.
#
# Source of truth: DB cameras table (serial, name, streaming_hub columns).
# cameras.json is legacy/export-import only — NOT read for camera data.
# cameras.json IS still read for webrtc_global_settings.enable_dtls until
# that setting is migrated to the nvr_settings table.
#
# Generates two sections:
#
#   1. webrtcEncryption — controlled by enable_dtls setting
#
#   2. paths: — one entry per camera, source determined by streaming_hub:
#        streaming_hub=go2rtc → source: rtsp://nvr-go2rtc:8555/{serial}
#          (MediaMTX pulls from go2rtc RTSP re-export; go2rtc is the single
#           camera consumer per the single-consumer policy)
#        streaming_hub=mediamtx (default) → source: publisher
#          (FFmpeg connects to camera and pushes to MediaMTX)
#
#      Each camera gets both sub ({serial}) and main ({serial}_main) paths.
#      Sub = low-res transcoded for grid view.
#      Main = native resolution for fullscreen.
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

echo -e "${GREEN}=== Sync MediaMTX with DB ===${NC}"
echo

# Backup
mkdir -p "$BACKUP_DIR"
cp "$MEDIAMTX_YML" "${BACKUP_DIR}/mediamtx.yml.$(date +%Y%m%d_%H%M%S)"
echo -e "${GREEN}✓${NC} Backup created"
echo

# ============================================================================
# DTLS/WebRTC Encryption Setting
# ============================================================================
# TODO: migrate enable_dtls to nvr_settings DB table; fall back to cameras.json
# for now since nvr_settings doesn't have it yet.
ENABLE_DTLS=$(jq -r '.webrtc_global_settings.enable_dtls // false' "$CAMERAS_JSON" 2>/dev/null || echo "false")

if [[ "$ENABLE_DTLS" == "true" ]]; then
    WEBRTC_ENCRYPTION="yes"
    echo -e "${GREEN}✓${NC} DTLS enabled (iOS Safari WebRTC support)"
else
    WEBRTC_ENCRYPTION="no"
    echo -e "${YELLOW}!${NC} DTLS disabled (iOS will fall back to HLS)"
fi

if grep -q "^webrtcEncryption:" "$MEDIAMTX_YML"; then
    sed -i "s/^webrtcEncryption:.*/webrtcEncryption: ${WEBRTC_ENCRYPTION} # Controlled by cameras.json webrtc_global_settings.enable_dtls (TODO: move to nvr_settings)/" "$MEDIAMTX_YML"
    echo -e "${GREEN}✓${NC} Updated webrtcEncryption: ${WEBRTC_ENCRYPTION}"
else
    echo -e "${YELLOW}⚠${NC} webrtcEncryption line not found in mediamtx.yml"
fi
echo

# ============================================================================
# Camera Paths Section — query DB
# ============================================================================
echo -e "${YELLOW}Querying DB for camera streaming_hub configuration...${NC}"

# Check for global hub override in nvr_settings.
# If set, all cameras use that hub regardless of per-camera streaming_hub.
GLOBAL_HUB=$(docker exec nvr-postgres psql -U nvr_api -d nvr -A -t \
    -c "SELECT value FROM nvr_settings WHERE key = 'streaming_hub_global';" 2>/dev/null | tr -d '[:space:]')

if [[ -n "$GLOBAL_HUB" && "$GLOBAL_HUB" != "null" ]]; then
    echo -e "${CYAN}Global hub override: ${GLOBAL_HUB} (overrides per-camera settings)${NC}"
else
    GLOBAL_HUB=""
    echo -e "${CYAN}No global hub override — using per-camera streaming_hub${NC}"
fi

# Fetch all cameras from DB.
# If global hub is set, use it for all cameras; otherwise use per-camera streaming_hub.
if [[ -n "$GLOBAL_HUB" ]]; then
    DB_ROWS=$(docker exec nvr-postgres psql -U nvr_api -d nvr -A -t \
        -c "SELECT serial, COALESCE(name, serial), '${GLOBAL_HUB}'
            FROM cameras ORDER BY name;" 2>/dev/null) || {
        echo -e "${RED}Error: Could not query DB (is nvr-postgres running?). Aborting.${NC}"
        exit 1
    }
else
    DB_ROWS=$(docker exec nvr-postgres psql -U nvr_api -d nvr -A -t \
        -c "SELECT serial,
                   COALESCE(name, serial),
                   COALESCE(streaming_hub, 'mediamtx')
            FROM cameras
            ORDER BY name;" 2>/dev/null) || {
        echo -e "${RED}Error: Could not query DB (is nvr-postgres running?). Aborting.${NC}"
        exit 1
    }
fi

if [[ -z "$DB_ROWS" ]]; then
    echo -e "${YELLOW}No cameras found in DB${NC}"
    exit 0
fi

CAMERA_COUNT=$(echo "$DB_ROWS" | grep -c '^' || true)
echo -e "${YELLOW}Found $CAMERA_COUNT camera(s)${NC}"
echo

# Build the paths section
PATHS_SECTION="paths:"

while IFS='|' read -r serial name hub; do
    # Determine MediaMTX source based on streaming_hub:
    #   go2rtc → MediaMTX pulls from go2rtc RTSP re-export (:8555)
    #   mediamtx → FFmpeg pushes (publisher mode)
    if [[ "$hub" == "go2rtc" ]]; then
        SOURCE="rtsp://nvr-go2rtc:8555/${serial}"
        HUB_LABEL="go2rtc→MediaMTX"
    else
        SOURCE="publisher"
        HUB_LABEL="FFmpeg→MediaMTX"
    fi

    echo "  - $name ($serial) [${HUB_LABEL}]"

    # Sub stream (grid view — transcoded low-res)
    PATHS_SECTION="${PATHS_SECTION}
  ${serial}:
    source: ${SOURCE}"

    # Main stream (fullscreen — native resolution)
    PATHS_SECTION="${PATHS_SECTION}
  ${serial}_main:
    source: ${SOURCE}"

done <<< "$DB_ROWS"

echo

# Replace everything from "paths:" onward in mediamtx.yml
awk -v new="$PATHS_SECTION" '/^paths:/ {print new; exit} {print}' "$MEDIAMTX_YML" > "${MEDIAMTX_YML}.tmp"
mv "${MEDIAMTX_YML}.tmp" "$MEDIAMTX_YML"

echo -e "${GREEN}✓${NC} Updated mediamtx.yml with $CAMERA_COUNT camera path entries"
echo -e "${YELLOW}Note: docker compose restart nvr-packager needed to reload paths${NC}"
