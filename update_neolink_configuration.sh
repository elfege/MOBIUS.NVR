#!/bin/bash
################################################################################
#
# GENERATE NEOLINK CONFIGURATION FROM CAMERAS.JSON
#
# Purpose: Auto-generate config/neolink.toml from cameras.json
# Usage: Called by start.sh or run manually: ./generate_neolink_config.sh
# Location: ~/0_NVR/0_MAINTENANCE_SCRIPTS/generate_neolink_config.sh
#
# This script:
# 1. Reads config/cameras.json
# 2. Filters for cameras with stream_type="NEOLINK"
# 3. Retrieves credentials from environment (REOLINK_USERNAME, REOLINK_PASSWORD)
# 4. Generates config/neolink.toml (always overwrites)
#
################################################################################

# set -e # Exit on error
# set -u # Exit on undefined variable
# set -x  # Print each command before executing

trap 'exit_code=$?; if [ $exit_code -ne 0 ]; then echo -e "\n${RED}✗ Script interrupted! Exiting...${NC} function: ${FUNCNAME[*]} COMMAND: ${BASH_COMMAND} line(s) ${LINENO[*]} exit_code:$exit_code"; fi; trap - INT TERM EXIT ERR; exit $exit_code' INT TERM EXIT ERR

SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"
SCRIPT_R_PATH=$(realpath "${BASH_SOURCE[0]}")
SCRIPT_DIR="${SCRIPT_R_PATH%${SCRIPT_NAME}}"

echo "SCRIPT_DIR: $SCRIPT_DIR"
# Navigate to project root
cd "$SCRIPT_DIR" || exit 1

# Source colors (already in environment from /etc/profile.d/custom-env.sh)
# But being explicit for standalone execution
. ~/.env.colors 2>/dev/null || true

################################################################################
# CONFIGURATION
################################################################################

CAMERAS_JSON="config/cameras.json"
OUTPUT_FILE="config/neolink.toml"

################################################################################
# HELPER FUNCTIONS
################################################################################

print_success() {
	echo -e "${GREEN}✓${NC} $1"
}

print_error() {
	echo -e "${RED}✗${NC} $1"
}

print_warning() {
	echo -e "${YELLOW}⚠${NC} $1"
}

print_info() {
	echo -e "${CYAN}ℹ${NC} $1"
}

################################################################################
# MAIN SCRIPT
################################################################################

main() {
	echo ""
	echo -e "${CYAN}╔════════════════════════════════════════════════════════════════════╗${NC}"
	echo -e "${CYAN}║${NC} Neolink Configuration Generator"
	echo -e "${CYAN}╚════════════════════════════════════════════════════════════════════╝${NC}"
	echo ""

	# Check if cameras.json exists
	if [[ ! -f "$CAMERAS_JSON" ]]; then
		print_error "cameras.json not found at: $CAMERAS_JSON"
		exit 1
	fi
	print_success "Found cameras.json"

	# Check if jq is available
	if ! command -v jq &>/dev/null; then
		print_error "jq not found! Please install: apt-get install jq"
		exit 1
	fi

	get_cameras_credentials

	# Get Reolink credentials from environment
	REOLINK_USERNAME="${REOLINK_USERNAME:-}"
	REOLINK_PASSWORD="${REOLINK_PASSWORD:-}"

	if [[ -z "$REOLINK_USERNAME" ]] || [[ -z "$REOLINK_PASSWORD" ]]; then
		print_warning "Reolink credentials not found in environment"
		print_info "Expected: REOLINK_USERNAME and REOLINK_PASSWORD"
		exit 1
	else
		print_success "Using credentials: $REOLINK_USERNAME / $(printf '%*s' ${#REOLINK_PASSWORD} | tr ' ' '*')"
	fi

	# Filter cameras.json for Neolink cameras
	print_info "Filtering for Neolink cameras..."

	# Query for cameras with stream_type="NEOLINK" and type="reolink"
	NEOLINK_CAMERAS=$(jq -r '
    .devices 
    | to_entries[] 
    | select(.value.stream_type? == "NEOLINK" and .value.type? == "reolink")
    | @json
' "$CAMERAS_JSON")

	# Count cameras
	# CAMERA_COUNT=$(echo "$NEOLINK_CAMERAS" | grep -c "serial" || echo "0")
	CAMERA_COUNT=$(echo "$NEOLINK_CAMERAS" | jq -s 'length')

	if [[ "$CAMERA_COUNT" -eq 0 ]]; then
		print_warning "No cameras with stream_type='NEOLINK' found"
		print_info "To add Neolink cameras:"
		print_info "  1. Edit $CAMERAS_JSON"
		print_info "  2. Set 'stream_type': 'NEOLINK' for desired Reolink cameras"
		print_info "  3. Optionally add 'neolink' section with custom config"
		print_info "  4. Re-run this script"
	else
		print_success "Found $CAMERA_COUNT Neolink camera(s)"
	fi

	# Generate neolink.toml (always overwrite with >)
	print_info "Generating $OUTPUT_FILE..."

	# Start with header (> overwrites file)
	cat >"$OUTPUT_FILE" <<EOF
################################################################################
# NEOLINK CONFIGURATION - AUTO-GENERATED
#
# Generated: $(date '+%Y-%m-%d %H:%M:%S')
# Source: $CAMERAS_JSON (filtered for stream_type="NEOLINK")
# Script: 0_MAINTENANCE_SCRIPTS/generate_neolink_config.sh
#
# DO NOT EDIT MANUALLY - Regenerate with: ./generate_neolink_config.sh
################################################################################

# Global Neolink settings
# bind = "0.0.0.0:8554"  # RTSP server bind address (internal to container)
bind = "0.0.0.0"
bind_port = 8554
log_level = "info"     # Options: error, warn, info, debug, trace

################################################################################
# CAMERAS
################################################################################

EOF

	# If no cameras, add helpful comment
	if [[ "$CAMERA_COUNT" -eq 0 ]]; then
		cat >>"$OUTPUT_FILE" <<EOF
# No cameras configured with stream_type="NEOLINK"
# To add cameras:
#   1. Edit $CAMERAS_JSON
#   2. Set "stream_type": "NEOLINK" for desired Reolink cameras
#   3. Add "neolink" section with configuration (optional):
#      "neolink": {
#        "baichuan_port": 9000,
#        "rtsp_path": "mainStream",
#        "enabled": true
#      }
#   4. Re-run this script

EOF
	else
		# Process each camera
		echo "$NEOLINK_CAMERAS" | while IFS= read -r camera_json; do
			# Skip empty lines
			[[ -z "$camera_json" ]] && continue

			# Extract camera details using jq
			SERIAL=$(echo "$camera_json" | jq -r '.key')
			NAME=$(echo "$camera_json" | jq -r '.value.name // .key')
			HOST=$(echo "$camera_json" | jq -r '.value.host')

			# Get neolink-specific settings with defaults
			BAICHUAN_PORT=$(echo "$camera_json" | jq -r '.value.neolink.baichuan_port // 9000')
			RTSP_PATH=$(echo "$camera_json" | jq -r '.value.neolink.rtsp_path // "mainStream"')
			ENABLED=$(echo "$camera_json" | jq -r '.value.neolink.enabled // true')
			BUFFER_SIZE=$(echo "$camera_json" | jq -r '.value.neolink.buffer_size // 20')


			# Append camera configuration
			cat >>"$OUTPUT_FILE" <<EOF
################################################################################
# Camera: $NAME
# Serial: $SERIAL
# IP: $HOST
################################################################################

[[cameras]]
name = "$SERIAL"
username = "$REOLINK_USERNAME"
password = "$REOLINK_PASSWORD"
uid = ""  # Leave empty - Neolink auto-discovers UID
address = "$HOST:$BAICHUAN_PORT"

# Stream configuration
# Options: mainStream (high quality), subStream (low quality)
stream = "$RTSP_PATH"

# Enable/disable this camera
enabled = $ENABLED

# Buffer size (number of frames to buffer)
# Small (10-20): Lower latency, less resilient to network issues
# Large (100+): Higher latency, more resilient to network issues  
buffer_size = $BUFFER_SIZE

# Available RTSP paths after Neolink starts:
#   rtsp://localhost:8554/$SERIAL/mainStream
#   rtsp://localhost:8554/$SERIAL/subStream

EOF

			print_info "  Added: $SERIAL ($NAME) @ $HOST"
		done
	fi

	# Add technical notes
	cat >>"$OUTPUT_FILE" <<'EOF'
################################################################################
# TECHNICAL NOTES
################################################################################
#
# Baichuan Protocol (Port 9000):
#   - Proprietary binary protocol by Reolink's parent company
#   - Used by official Reolink mobile/desktop apps
#   - Lower latency than standard RTSP (~100-300ms native)
#   - Reverse engineered by George Hilliard (2020)
#
# Neolink Bridge:
#   - Translates Baichuan <-> RTSP with minimal overhead
#   - No transcoding (pure protocol conversion)
#   - Expected latency: ~600ms-1.5s (vs 1-2s direct RTSP)
#   - Open source: https://github.com/QuantumEntangledAndy/neolink
#
# Integration Flow:
#   Camera:9000 <--Baichuan--> Neolink:8554 <--RTSP--> FFmpeg <--HLS--> Browser
#
# Credential Management:
#   - Username/password read from environment variables
#   - Set in container: REOLINK_USERNAME, REOLINK_PASSWORD
#   - Shared across all Reolink cameras (NVR-level credentials)
#
# Stream Types:
#   - mainStream: High quality (1080p/5MP, higher bitrate)
#   - subStream: Low quality (640x480, lower bitrate for mobile)
#
################################################################################
EOF

	print_success "Generated: $OUTPUT_FILE"

	# Show file size
	FILE_SIZE=$(stat -f%z "$OUTPUT_FILE" 2>/dev/null || stat -c%s "$OUTPUT_FILE" 2>/dev/null || echo "unknown")
	print_info "File size: $FILE_SIZE bytes"

	# Summary
	echo ""
	echo -e "${GREEN}═══════════════════════════════════════════════════════════════════${NC}"
	echo -e "${GREEN}Configuration generated successfully!${NC}"
	echo -e "${GREEN}═══════════════════════════════════════════════════════════════════${NC}"
	echo ""
	echo "Configuration file: $OUTPUT_FILE"
	echo "Cameras configured: $CAMERA_COUNT"
	echo ""

	if [[ "$CAMERA_COUNT" -gt 0 ]]; then
		echo "Configured cameras:"
		echo "$NEOLINK_CAMERAS" | while IFS= read -r camera_json; do
			[[ -z "$camera_json" ]] && continue
			SERIAL=$(echo "$camera_json" | jq -r '.key')
			NAME=$(echo "$camera_json" | jq -r '.value.name // .key')
			HOST=$(echo "$camera_json" | jq -r '.value.host')
			echo "  • $SERIAL ($NAME) @ $HOST"
		done
		echo ""
	fi

	echo -e "${CYAN}Next steps:${NC}"
	echo "  1. Review config: cat $OUTPUT_FILE"
	if [[ "$REOLINK_PASSWORD" == "CHANGEME" ]]; then
		echo -e " 2. ${YELLOW}UPDATE PASSWORD in $OUTPUT_FILE${NC}"
	fi
	echo "  3. Test Neolink: ./neolink/target/release/neolink rtsp --config=$OUTPUT_FILE"
	echo "  4. Rebuild container: docker compose build unified-nvr"
	echo ""
}

# Run main function
main "$@"
