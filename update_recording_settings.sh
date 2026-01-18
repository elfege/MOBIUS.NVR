#!/bin/bash

# Sync recording_settings.json camera_settings with cameras.json
# Adds missing cameras with sensible defaults based on type/capabilities

set -e

SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
cd "$SCRIPT_DIR"
PROJECT_ROOT=~/0_NVR

. ~/.env.colors

CAMERAS_JSON="${PROJECT_ROOT}/config/cameras.json"
RECORDING_SETTINGS="${PROJECT_ROOT}/config/recording_settings.json"
BACKUP_DIR="${PROJECT_ROOT}/config/backups"

echo -e "${GREEN}=== Sync recording_settings.json with cameras.json ===${NC}"
echo

# Verify files exist
if [[ ! -f "$CAMERAS_JSON" ]]; then
    echo -e "${RED}Error: cameras.json not found${NC}"
    exit 1
fi

if [[ ! -f "$RECORDING_SETTINGS" ]]; then
    echo -e "${RED}Error: recording_settings.json not found${NC}"
    exit 1
fi

# Backup
mkdir -p "$BACKUP_DIR"
cp "$RECORDING_SETTINGS" "${BACKUP_DIR}/recording_settings.json.$(date +%Y%m%d_%H%M%S)"
echo -e "${GREEN}✓${NC} Backup created"
echo

# Get all camera IDs from cameras.json
ALL_CAMERA_IDS=$(jq -r '.devices | keys[]' "$CAMERAS_JSON")

# Get existing camera IDs from recording_settings.json
EXISTING_CAMERA_IDS=$(jq -r '.camera_settings | keys[]' "$RECORDING_SETTINGS" 2>/dev/null || echo "")

# Find missing cameras
ADDED=0
for camera_id in $ALL_CAMERA_IDS; do
    if echo "$EXISTING_CAMERA_IDS" | grep -q "^${camera_id}$"; then
        continue  # Already exists
    fi

    # Get camera info
    CAMERA_TYPE=$(jq -r ".devices[\"$camera_id\"].type // \"unknown\"" "$CAMERAS_JSON")
    STREAM_TYPE=$(jq -r ".devices[\"$camera_id\"].stream_type // \"RTSP\"" "$CAMERAS_JSON")
    CAMERA_NAME=$(jq -r ".devices[\"$camera_id\"].name // \"$camera_id\"" "$CAMERAS_JSON")

    # Determine detection method based on camera type
    case "$CAMERA_TYPE" in
        reolink)
            DETECTION_METHOD="baichuan"
            ;;
        amcrest|sv3c)
            DETECTION_METHOD="onvif"
            ;;
        eufy|unifi)
            # Eufy/Unifi don't support ONVIF, use FFmpeg
            DETECTION_METHOD="ffmpeg"
            ;;
        *)
            DETECTION_METHOD="ffmpeg"
            ;;
    esac

    # Determine recording source based on stream type
    case "$STREAM_TYPE" in
        LL_HLS|HLS|NEOLINK_LL_HLS)
            RECORDING_SOURCE="mediamtx"
            ;;
        MJPEG)
            RECORDING_SOURCE="mjpeg_service"
            ;;
        *)
            RECORDING_SOURCE="rtsp"
            ;;
    esac

    echo -e "${YELLOW}Adding:${NC} $CAMERA_NAME ($camera_id)"
    echo -e " Type: $CAMERA_TYPE, Stream: $STREAM_TYPE"
    echo -e " Detection: $DETECTION_METHOD, Source: $RECORDING_SOURCE"

    # Add camera settings using jq
    RECORDING_SETTINGS_TMP="${RECORDING_SETTINGS}.tmp"
    jq --arg id "$camera_id" \
       --arg method "$DETECTION_METHOD" \
       --arg source "$RECORDING_SOURCE" \
       '.camera_settings[$id] = {
            "motion_recording": {
                "enabled": true,
                "detection_method": $method,
                "recording_source": $source,
                "segment_duration_sec": 30,
                "pre_buffer_enabled": false,
                "pre_buffer_sec": 5,
                "post_buffer_sec": 10,
                "max_age_days": 7,
                "quality": "main"
            },
            "continuous_recording": {
                "enabled": false,
                "segment_duration_sec": 3600,
                "max_age_days": 3,
                "quality": "sub"
            },
            "snapshots": {
                "enabled": true,
                "interval_sec": 300,
                "max_age_days": 14,
                "quality": 85
            }
        }' "$RECORDING_SETTINGS" > "$RECORDING_SETTINGS_TMP"

    mv "$RECORDING_SETTINGS_TMP" "$RECORDING_SETTINGS"
    ADDED=$((ADDED + 1))
done

echo
if [[ $ADDED -eq 0 ]]; then
    echo -e "${GREEN}✓${NC} All cameras already have recording settings"
else
    echo -e "${GREEN}✓${NC} Added $ADDED camera(s) to recording_settings.json"
fi

# Also check for cameras in recording_settings that no longer exist in cameras.json
echo
echo -e "${CYAN}Checking for stale entries...${NC}"
REMOVED=0
for camera_id in $EXISTING_CAMERA_IDS; do
    if ! echo "$ALL_CAMERA_IDS" | grep -q "^${camera_id}$"; then
        echo -e "${YELLOW}Removing stale:${NC} $camera_id"
        jq --arg id "$camera_id" 'del(.camera_settings[$id])' "$RECORDING_SETTINGS" > "${RECORDING_SETTINGS}.tmp"
        mv "${RECORDING_SETTINGS}.tmp" "$RECORDING_SETTINGS"
        REMOVED=$((REMOVED + 1))
    fi
done

if [[ $REMOVED -eq 0 ]]; then
    echo -e "${GREEN}✓${NC} No stale entries found"
else
    echo -e "${GREEN}✓${NC} Removed $REMOVED stale camera(s)"
fi

echo
echo -e "${GREEN}Done!${NC}"
