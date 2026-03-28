#!/bin/bash

# Sync mediamtx.yml with cameras.json settings
# - Updates webrtcEncryption: setting from webrtc_global_settings.enable_dtls
# - Updates paths: section with entries for ALL cameras MediaMTX should serve
#
# Source per camera depends on streaming_hub field:
#   streaming_hub=go2rtc (or stream_type=GO2RTC):
#     source: rtsp://nvr-go2rtc:8555/{serial}
#     MediaMTX pulls from go2rtc RTSP re-export (go2rtc is the single camera consumer)
#
#   streaming_hub=mediamtx (default):
#     source: publisher
#     FFmpeg connects directly to camera and pushes to MediaMTX
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
ENABLE_DTLS=$(jq -r '.webrtc_global_settings.enable_dtls // false' "$CAMERAS_JSON")

if [[ "$ENABLE_DTLS" == "true" ]]; then
    WEBRTC_ENCRYPTION="yes"
    echo -e "${GREEN}✓${NC} DTLS enabled (iOS Safari WebRTC support)"
else
    WEBRTC_ENCRYPTION="no"
    echo -e "${YELLOW}!${NC} DTLS disabled (iOS will fall back to HLS)"
fi

if grep -q "^webrtcEncryption:" "$MEDIAMTX_YML"; then
    sed -i "s/^webrtcEncryption:.*/webrtcEncryption: ${WEBRTC_ENCRYPTION} # Controlled by cameras.json webrtc_global_settings.enable_dtls/" "$MEDIAMTX_YML"
    echo -e "${GREEN}✓${NC} Updated webrtcEncryption: ${WEBRTC_ENCRYPTION}"
else
    echo -e "${YELLOW}⚠${NC} webrtcEncryption line not found in mediamtx.yml"
fi
echo

# ============================================================================
# Camera Paths Section
# ============================================================================
# Build MediaMTX path entries for cameras MediaMTX should serve.
#
# A camera gets a MediaMTX path if it has:
#   - streaming_hub=go2rtc: pulls from go2rtc RTSP re-export
#   - stream_type in (LL_HLS, NEOLINK, NEOLINK_LL_HLS, WEBRTC, HLS): publisher push
#   - go2rtc_source set (any type): also added as publisher for pre-staging
#
# Each camera gets both sub ({serial}) and main ({serial}_main) paths.
# Sub = low-res transcoded for grid; Main = native resolution for fullscreen.

echo -e "${YELLOW}Building MediaMTX paths section...${NC}"

# Extract cameras qualifying for MediaMTX paths
ALL_CAMERAS=$(jq -r '
    .devices | to_entries[] |
    select(
        .value.streaming_hub == "go2rtc" or
        .value.stream_type == "GO2RTC" or
        .value.stream_type == "LL_HLS" or
        .value.stream_type == "NEOLINK" or
        .value.stream_type == "NEOLINK_LL_HLS" or
        .value.stream_type == "WEBRTC" or
        .value.stream_type == "HLS" or
        (.value.go2rtc_source != null and .value.go2rtc_source != "")
    ) |
    {
        serial: .key,
        name: .value.name,
        stream_type: (.value.stream_type // "LL_HLS"),
        streaming_hub: (.value.streaming_hub // "mediamtx")
    } | @json
' "$CAMERAS_JSON")

if [[ -z "$ALL_CAMERAS" ]]; then
    echo -e "${YELLOW}No cameras found for MediaMTX paths${NC}"
    exit 0
fi

CAMERA_COUNT=$(echo "$ALL_CAMERAS" | wc -l)
echo -e "${YELLOW}Found $CAMERA_COUNT camera(s) for MediaMTX paths:${NC}"

# Build the paths section
PATHS_SECTION="paths:"

while IFS= read -r camera_json; do
    serial=$(echo "$camera_json" | jq -r '.serial')
    name=$(echo "$camera_json" | jq -r '.name')
    hub=$(echo "$camera_json" | jq -r '.streaming_hub')
    stream_type=$(echo "$camera_json" | jq -r '.stream_type')

    # Determine MediaMTX source based on streaming hub:
    #   go2rtc hub → MediaMTX pulls from go2rtc RTSP re-export
    #                Single-consumer policy: go2rtc connects to camera, MediaMTX reads from go2rtc
    #   mediamtx hub → FFmpeg pushes (publisher mode, existing architecture)
    if [[ "$hub" == "go2rtc" ]] || [[ "$stream_type" == "GO2RTC" ]]; then
        SOURCE="rtsp://nvr-go2rtc:8555/${serial}"
        HUB_LABEL="go2rtc→MediaMTX"
    else
        SOURCE="publisher"
        HUB_LABEL="FFmpeg→MediaMTX"
    fi

    echo "  - $name ($serial) [${HUB_LABEL}]"

    # Sub stream path (grid view — transcoded low-res)
    PATHS_SECTION="${PATHS_SECTION}
  ${serial}:
    source: ${SOURCE}"

    # Main stream path (fullscreen — native resolution)
    PATHS_SECTION="${PATHS_SECTION}
  ${serial}_main:
    source: ${SOURCE}"

done <<< "$ALL_CAMERAS"

echo

# Replace everything from "paths:" onward in mediamtx.yml
awk -v new="$PATHS_SECTION" '/^paths:/ {print new; exit} {print}' "$MEDIAMTX_YML" > "${MEDIAMTX_YML}.tmp"
mv "${MEDIAMTX_YML}.tmp" "$MEDIAMTX_YML"

echo -e "${GREEN}✓${NC} Updated mediamtx.yml with $CAMERA_COUNT camera path entries"
echo -e "${YELLOW}Note: docker compose restart nvr-packager needed to reload paths${NC}"
