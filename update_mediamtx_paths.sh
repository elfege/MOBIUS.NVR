#!/bin/bash

# Sync mediamtx.yml paths with cameras.json LL_HLS entries

set -e

SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
cd "$SCRIPT_DIR"
PROJECT_ROOT=~/0_NVR

. ~/.env.colors

CAMERAS_JSON="${PROJECT_ROOT}/config/cameras.json"
MEDIAMTX_YML="${PROJECT_ROOT}/packager/mediamtx.yml"
BACKUP_DIR="${PROJECT_ROOT}/packager/backups"

echo -e "${GREEN}=== Sync MediaMTX paths with cameras.json ===${NC}"
echo

# Backup
mkdir -p "$BACKUP_DIR"
cp "$MEDIAMTX_YML" "${BACKUP_DIR}/mediamtx.yml.$(date +%Y%m%d_%H%M%S)"
echo -e "${GREEN}✓${NC} Backup created"
echo

# Extract LL_HLS and NEOLINK camera IDs (both use MediaMTX LL-HLS path)
LL_HLS_PATHS=$(jq -r '.devices | to_entries[] | select(.value.stream_type == "LL_HLS" or .value.stream_type == "NEOLINK") | .key' "$CAMERAS_JSON")

if [[ -z "$LL_HLS_PATHS" ]]; then
    echo -e "${YELLOW}No LL_HLS or NEOLINK cameras found${NC}"
    exit 0
fi

echo -e "${YELLOW}Found LL_HLS/NEOLINK cameras:${NC}"
echo "$LL_HLS_PATHS" | while read -r path; do
    echo "  - $path"
done
echo

# Build new paths section
PATHS_SECTION="paths:"
for path in $LL_HLS_PATHS; do
    PATHS_SECTION="${PATHS_SECTION}
  ${path}:
    source: publisher"
done

# Replace everything after "paths:" line
awk -v new="$PATHS_SECTION" '/^paths:/ {print new; exit} {print}' "$MEDIAMTX_YML" > "${MEDIAMTX_YML}.tmp"
mv "${MEDIAMTX_YML}.tmp" "$MEDIAMTX_YML"

echo -e "${GREEN}✓${NC} Updated mediamtx.yml"
echo -e "${YELLOW}Restart: docker compose restart nvr-packager${NC}"