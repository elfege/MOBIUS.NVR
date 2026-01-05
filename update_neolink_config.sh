#!/bin/bash
#
# update_neolink_config.sh
# Syncs neolink.toml with cameras that have stream_type: "NEOLINK" in cameras.json
#
# Called by start.sh before container startup
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CAMERAS_JSON="${SCRIPT_DIR}/config/cameras.json"
NEOLINK_TOML="${SCRIPT_DIR}/config/neolink.toml"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}Updating neolink.toml...${NC}"

if [[ ! -f "$CAMERAS_JSON" ]]; then
    echo -e "${RED}Error: cameras.json not found at $CAMERAS_JSON${NC}"
    exit 1
fi

# Get Reolink credentials from environment or AWS
if [[ -z "$REOLINK_USERNAME" ]] || [[ -z "$REOLINK_PASSWORD" ]]; then
    echo -e "${YELLOW}Warning: REOLINK_USERNAME/PASSWORD not set, using defaults${NC}"
    REOLINK_USERNAME="admin"
    REOLINK_PASSWORD="password"
fi

# Extract NEOLINK cameras from cameras.json
# Now includes neolink.stream field (defaults to "subStream" for backward compatibility)
NEOLINK_CAMERAS=$(jq -r '
    .devices | to_entries[] |
    select(.value.stream_type == "NEOLINK") |
    select(.value.type == "reolink") |
    {
        serial: .key,
        name: .value.name,
        host: .value.host,
        port: (.value.neolink.port // 8554),
        buffer_size: (.value.neolink.buffer_size // 100),
        stream: (.value.neolink.stream // "subStream")
    } | @json
' "$CAMERAS_JSON")

if [[ -z "$NEOLINK_CAMERAS" ]]; then
    echo -e "${GREEN}No NEOLINK cameras found in cameras.json${NC}"
    # Create minimal config
    cat > "$NEOLINK_TOML" << 'EOF'
################################################################################
# NEOLINK CONFIGURATION - AUTO-GENERATED
# No cameras configured for NEOLINK stream_type
################################################################################

bind = "0.0.0.0"
bind_port = 8554
log_level = "info"
EOF
    exit 0
fi

# Count cameras
CAMERA_COUNT=$(echo "$NEOLINK_CAMERAS" | wc -l)
echo -e "${CYAN}Found $CAMERA_COUNT NEOLINK camera(s)${NC}"

# Generate neolink.toml
cat > "$NEOLINK_TOML" << EOF
################################################################################
# NEOLINK CONFIGURATION - AUTO-GENERATED
#
# Generated: $(date '+%Y-%m-%d %H:%M:%S')
# Source: config/cameras.json (filtered for stream_type="NEOLINK", type="reolink")
# Script: update_neolink_config.sh
#
# DO NOT EDIT MANUALLY - Changes will be overwritten on container restart
################################################################################

# Global Neolink settings
bind = "0.0.0.0"
bind_port = 8554
log_level = "info"

################################################################################
# CAMERAS
################################################################################
EOF

# Add each camera
echo "$NEOLINK_CAMERAS" | while read -r camera_json; do
    serial=$(echo "$camera_json" | jq -r '.serial')
    name=$(echo "$camera_json" | jq -r '.name')
    host=$(echo "$camera_json" | jq -r '.host')
    buffer_size=$(echo "$camera_json" | jq -r '.buffer_size')
    stream=$(echo "$camera_json" | jq -r '.stream')

    echo "" >> "$NEOLINK_TOML"
    cat >> "$NEOLINK_TOML" << EOF
################################################################################
# Camera: $name
# Serial: $serial
# Host: $host
################################################################################

[[cameras]]
name = "$serial"
username = "$REOLINK_USERNAME"
password = "$REOLINK_PASSWORD"
uid = ""
address = "$host:9000"
stream = "$stream"

# Buffer settings
# - buffer_size: Number of frames to keep in memory before overflow
# - buffer_duration: Duration in ms (1-15000) before buffer is considered full
buffer_size = $buffer_size
buffer_duration = 1000

# Show splash screen when paused (helps debug pause state visually)
use_splash = true

# Disconnect from camera when no clients for 30s (saves bandwidth and camera resources)
idle_disconnect = true

# Disable push notifications (not needed for NVR integration, reduces traffic)
push_notifications = false

# Pause configuration - prevents buffer overflow by pausing when no clients
# This is the correct way to enable on-demand streaming in Neolink
#
# CRITICAL: TOML SYNTAX NOTES
# - [[cameras]] creates an array element (NOT [cameras])
# - Sub-tables within array elements MUST be indented with 2 spaces
# - Without indentation, [cameras.pause] would create a new top-level table
# - The indentation tells TOML parser this belongs to the previous [[cameras]] entry
# - See: https://toml.io/en/v1.0.0#array-of-tables
  [cameras.pause]
  on_client = true   # Pause stream when no RTSP client connected (on_disconnect alias)
  timeout = 2.0      # Seconds to wait before pausing after client disconnects

EOF

    echo -e "  ${GREEN}+${NC} $name ($serial)"
done

# Add technical notes
cat >> "$NEOLINK_TOML" << 'EOF'
################################################################################
# TECHNICAL NOTES
################################################################################
#
# Baichuan Protocol (Port 9000):
#   - Proprietary binary protocol by Reolink
#   - Used by official Reolink mobile/desktop apps
#   - Works even when RTSP (port 554) is unresponsive
#
# Neolink Bridge:
#   - Translates Baichuan <-> RTSP
#   - No transcoding (pure protocol conversion)
#   - Open source: https://github.com/QuantumEntangledAndy/neolink
#
# RTSP Paths:
#   - rtsp://neolink:8554/{serial}/sub (subStream)
#   - rtsp://neolink:8554/{serial}/main (mainStream)
#
# Pause Mode ([cameras.pause] with on_client = true):
#   - Pauses stream when no RTSP client is connected
#   - Prevents buffer overflow from GStreamer's internal buffers
#   - Recommended for NVR integration to avoid "Buffer full" errors
#
################################################################################
EOF

echo -e "${GREEN}Done! neolink.toml updated with $CAMERA_COUNT camera(s)${NC}"
