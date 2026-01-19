#!/bin/bash
# =============================================================================
# ensure_recording_paths.sh - Fix permissions for NVR recording directories
# =============================================================================
#
# This script reads storage paths from recording_settings.json and ensures
# proper ownership for the NVR container (UID 1000 = appuser).
#
# The script should be called from start.sh BEFORE docker compose up to
# prevent permission errors when the container writes recordings/exports.
#
# Usage:
#   ./ensure_recording_paths.sh
#   ./ensure_recording_paths.sh --dry-run  # Show what would be done without executing
#
# =============================================================================

set -e

# Configuration
SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"
CONFIG_FILE="${SCRIPT_DIR}/config/recording_settings.json"
DRY_RUN=false

# Container user UID (matches Dockerfile USER appuser)
TARGET_UID=1000
TARGET_GID=1000

# Colors for output (optional, will work without colors if not available)
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            echo "Usage: $SCRIPT_NAME [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --dry-run    Show what would be done without executing"
            echo "  -h, --help   Show this help message"
            echo ""
            echo "This script reads storage_paths from recording_settings.json and ensures"
            echo "proper ownership (UID $TARGET_UID) for the NVR container."
            exit 0
            ;;
        *)
            echo "Unknown option $1"
            exit 1
            ;;
    esac
done

# Check if config file exists
if [[ ! -f "$CONFIG_FILE" ]]; then
    echo -e "${RED}ERROR: Config file not found: $CONFIG_FILE${NC}"
    exit 1
fi

# Check if jq is available
if ! command -v jq &> /dev/null; then
    echo -e "${RED}ERROR: jq is required but not installed${NC}"
    echo -e "${YELLOW}Install with: sudo apt-get install jq${NC}"
    exit 1
fi

echo -e "${BLUE}======================================${NC}"
echo -e "${BLUE}  NVR Recording Path Permission Fix${NC}"
echo -e "${BLUE}======================================${NC}"
if $DRY_RUN; then
    echo -e "${YELLOW}  Mode: DRY RUN (no changes will be made)${NC}"
fi
echo ""

# Extract storage paths from JSON config
RECENT_HOST_PATH=$(jq -r '.storage_paths.recent_host_path // empty' "$CONFIG_FILE")
ARCHIVE_HOST_PATH=$(jq -r '.storage_paths.archive_host_path // empty' "$CONFIG_FILE")

# Subdirectories that need to exist with proper permissions
SUBDIRS=(
    "motion"
    "continuous"
    "manual"
    "snapshots"
    "buffer"
    "exports"
)

# Function to ensure directory exists with proper ownership
ensure_dir() {
    local dir="$1"
    local desc="$2"

    if [[ -z "$dir" ]]; then
        echo -e "${YELLOW}  SKIP: $desc (path not configured)${NC}"
        return
    fi

    if [[ ! -d "$dir" ]]; then
        if $DRY_RUN; then
            echo -e "${BLUE}  WOULD CREATE: $dir${NC}"
        else
            echo -e "${GREEN}  CREATE: $dir${NC}"
            sudo mkdir -p "$dir"
        fi
    fi

    # Check current ownership
    if [[ -d "$dir" ]]; then
        local current_uid=$(stat -c '%u' "$dir" 2>/dev/null || echo "unknown")
        local current_gid=$(stat -c '%g' "$dir" 2>/dev/null || echo "unknown")

        if [[ "$current_uid" == "$TARGET_UID" && "$current_gid" == "$TARGET_GID" ]]; then
            echo -e "${GREEN}  OK: $dir (already owned by $TARGET_UID:$TARGET_GID)${NC}"
        else
            if $DRY_RUN; then
                echo -e "${BLUE}  WOULD CHOWN: $dir ($current_uid:$current_gid -> $TARGET_UID:$TARGET_GID)${NC}"
            else
                echo -e "${YELLOW}  FIX: $dir ($current_uid:$current_gid -> $TARGET_UID:$TARGET_GID)${NC}"
                sudo chown "$TARGET_UID:$TARGET_GID" "$dir"
            fi
        fi
    fi
}

# Function to process a base path and all its subdirectories
process_base_path() {
    local base_path="$1"
    local name="$2"

    if [[ -z "$base_path" ]]; then
        echo -e "${YELLOW}$name path not configured in recording_settings.json${NC}"
        return
    fi

    echo -e "${BLUE}Processing $name: $base_path${NC}"

    # Ensure base directory
    ensure_dir "$base_path" "$name base"

    # Ensure subdirectories
    for subdir in "${SUBDIRS[@]}"; do
        ensure_dir "$base_path/$subdir" "$name/$subdir"
    done

    echo ""
}

# Process Recent storage path
if [[ -n "$RECENT_HOST_PATH" ]]; then
    process_base_path "$RECENT_HOST_PATH" "Recent Storage"
else
    echo -e "${YELLOW}Warning: recent_host_path not found in config${NC}"
fi

# Process Archive storage path
if [[ -n "$ARCHIVE_HOST_PATH" ]]; then
    process_base_path "$ARCHIVE_HOST_PATH" "Archive Storage"
else
    echo -e "${YELLOW}Warning: archive_host_path not found in config${NC}"
fi

# Summary
echo -e "${BLUE}======================================${NC}"
if $DRY_RUN; then
    echo -e "${YELLOW}DRY RUN complete - no changes were made${NC}"
    echo -e "${YELLOW}Run without --dry-run to apply changes${NC}"
else
    echo -e "${GREEN}Permission fix complete${NC}"
fi
echo -e "${BLUE}======================================${NC}"
