#!/usr/bin/env bash
# eufy_bridge.sh
#
# ============================================================================
# EUFY SECURITY BRIDGE SERVER - STARTUP SCRIPT
# ============================================================================
#
# PURPOSE:
#   Starts the eufy-security-ws WebSocket server that allows local control
#   of Eufy cameras (PTZ, streaming, etc.) via the eufy-security-client library
#
# REQUIREMENTS:
#   1. Must run INSIDE Docker container (unified-nvr)
#   2. AWS credentials configured with EUFY_CAMERAS secret
#   3. Node.js with eufy-security-ws package installed
#   4. Network access to Eufy cloud servers (for initial auth only)
#
# AUTHENTICATION:
#   - Handled via browser: https://localhost:8443/eufy-auth
#   - Calls eufy_bridge_login.sh when authentication required
#   - Uses Flask API for code submission
#
# USAGE:
#   From host machine:
#     docker exec -it unified-nvr bash /app/services/eufy/eufy_bridge.sh
#
#   From inside container:
#     bash /app/services/eufy/eufy_bridge.sh
#
# ============================================================================

set -euo pipefail

# Configuration
PORT=${1:-3000}
EUFY_CONFIG_PATH=/tmp/eufy_bridge.json
TRUSTED_DEVICE_NAME="EufyBridge"
COUNTRY="US"
LANGUAGE="en"
STATIC_DIR="/app/static"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
	echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
	echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
	echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
	echo -e "${RED}[ERROR]${NC} $1"
}

echo "============================================================================"
echo "EUFY SECURITY BRIDGE - STARTING UP"
echo "============================================================================"
echo ""
echo "Configuration:"
echo "  Port: ${PORT}"
echo "  WebSocket URL: ws://0.0.0.0:${PORT}"
echo ""

# Check if running inside container
if [[ ! -f /.dockerenv ]] && [[ ! -f /run/.containerenv ]]; then
	echo ""
	log_error "This script must be run inside the Docker container"
	echo ""
	echo "From your host machine, run:"
	echo "  docker exec -it unified-nvr bash /app/services/eufy/eufy_bridge.sh"
	echo ""
	exit 1
fi

log_success "Container check passed"

echo "============================================================================"
echo "STEP 1: LOADING CREDENTIALS"
echo "============================================================================"

echo "EUFY_BRIDGE_USERNAME: $EUFY_BRIDGE_USERNAME"

# Pull AWS secrets if bridge credentials missing
if [[ -z "${EUFY_BRIDGE_USERNAME}" || -z "${EUFY_BRIDGE_PASSWORD}" ]]; then
	log_warn "Credentials not in environment, attempting to load from AWS Secrets Manager..."
	pull_aws_secrets "EUFY_CAMERAS" 2>/dev/null || true
fi

# Verify bridge credentials loaded
if [[ -z "${EUFY_BRIDGE_USERNAME}" || -z "${EUFY_BRIDGE_PASSWORD}" ]]; then
	echo ""
	log_error "Bridge credentials missing after attempting to load from AWS"
	echo ""
	echo "EUFY_BRIDGE_USERNAME: ${EUFY_BRIDGE_USERNAME:-NOT SET}"
	echo "EUFY_BRIDGE_PASSWORD: ${EUFY_BRIDGE_PASSWORD:+SET (hidden)}"
	echo ""
	echo "TROUBLESHOOTING:"
	echo "1. Verify AWS credentials are configured"
	echo "2. Check that EUFY_CAMERAS secret exists in AWS Secrets Manager"
	echo "3. Ensure secret contains: EUFY_BRIDGE_USERNAME and EUFY_BRIDGE_PASSWORD"
	echo ""
	exit 1
fi

log_success "Credentials loaded successfully"
echo "  Username: $EUFY_BRIDGE_USERNAME"
echo "  Password: ******** (hidden)"
echo ""

echo "============================================================================"
echo "STEP 2: CREATING CONFIGURATION"
echo "============================================================================"

cleanup() {
	log_info "Cleaning up..."
	return 0
}

trap 'pkill -f eufy-security-server; cleanup' EXIT INT TERM

populate_config() {
	jq -n \
		--arg user "$EUFY_BRIDGE_USERNAME" \
		--arg pass "$EUFY_BRIDGE_PASSWORD" \
		--arg coun "$COUNTRY" \
		--arg lang "$LANGUAGE" \
		--arg trust "$TRUSTED_DEVICE_NAME" \
		'{
      country:$coun,
      language:$lang,
      username:$user,
      password:$pass,
      trustedDeviceName: $trust,
      persistentDir: "/app"
    }' >"${EUFY_CONFIG_PATH}"

	log_success "Configuration file created: ${EUFY_CONFIG_PATH}"
	echo "  Country: $COUNTRY"
	echo "  Language: $LANGUAGE"
	echo "  Trusted Device Name: $TRUSTED_DEVICE_NAME"
	echo "  Persistent Directory: /app (for storing auth tokens)"
	echo ""
}

execute_bridge() {
	echo "============================================================================"
	echo "STEP 3: STARTING EUFY SECURITY SERVER"
	echo "============================================================================"
	echo ""
	echo "Server will start on: ws://0.0.0.0:$PORT"
	echo "Connecting to Eufy cloud servers for authentication..."
	echo ""
	echo "NOTE: If authentication is required, browser will open automatically"
	echo ""
	echo "----------------------------------------------------------------------------"

	local auth_handled=false
	captcha_count=0
	last_captcha="" # Track last captcha to prevent duplicate processing
	

	# Start with verbose logging to capture ALL output including DEBUG lines with captcha
	/app/node_modules/.bin/eufy-security-server \
		--config "${EUFY_CONFIG_PATH}" \
		--port "$PORT" \
		--host "0.0.0.0" \
		-v \
		2>&1 | tee /tmp/bridge_output.log |
		while IFS= read -r line; do
			# CRITICAL: Process EVERY line for captcha detection FIRST
			# Don't filter yet - we need to see DEBUG lines with base64 data!

			# Check if line contains captcha base64 data
			# NOTE: Deduplicate - only process if this is a NEW captcha (different base64)
			if echo "$line" | grep -q "data:image/png"; then
				# Extract base64 string
				extracted_captcha=$(echo "$line" | grep -oP 'data:image/png;base64,[A-Za-z0-9+/=]+')

				# Only process if we got valid data AND it's different from last one
				if [[ -n "$extracted_captcha" ]] && [[ "$extracted_captcha" != "$last_captcha" ]]; then
					captcha_count=$((captcha_count + 1))
					last_captcha="$extracted_captcha" # Store to prevent duplicates

					log_info "Captcha #${captcha_count} detected in logs - extracting base64 data"

					# Save to temp file
					echo "$extracted_captcha" >/tmp/captcha_base64.txt
					log_info "Base64 data extracted to /tmp/captcha_base64.txt"

					# Decode from temp file
					cat /tmp/captcha_base64.txt | sed 's/data:image\/png;base64,//' | base64 -d >"${STATIC_DIR}/eufy_captcha.png" 2>/dev/null

					if [[ -s "${STATIC_DIR}/eufy_captcha.png" ]]; then
						log_success "Captcha image decoded and saved to ${STATIC_DIR}/eufy_captcha.png"

						# Call authentication handler for EVERY captcha (handles retries after wrong submissions)
						# Kill any existing auth handlers first to prevent pileup
						pkill -f "eufy_bridge_login.sh" 2>/dev/null || true
						(
							if [[ -f "${SCRIPT_DIR}/eufy_bridge_login.sh" ]]; then
								bash "${SCRIPT_DIR}/eufy_bridge_login.sh"
							else
								log_error "Authentication handler not found: ${SCRIPT_DIR}/eufy_bridge_login.sh"
							fi
						) &
					else
						log_error "Failed to decode captcha image"
					fi
				else
					# Same captcha seen again - ignore silently
					:
				fi
			fi

			# Detect successful connection
			if [[ "$line" == *"connected"* ]] && [[ "$line" == *"true"* ]]; then
				echo ""
				echo "========================================================================"
				echo "               BRIDGE CONNECTED SUCCESSFULLY!                           "
				echo "========================================================================"
				echo ""
				echo "The Eufy Security Bridge is now running and ready to accept commands."
				echo ""
				echo "You can now:"
				echo "  - Use PTZ controls from your web interface"
				echo "  - Stream from Eufy cameras"
				echo "  - Send commands via WebSocket to ws://localhost:${PORT}"
				echo ""
				echo "To stop the bridge, press Ctrl+C"
				echo ""
			fi

			# Detect 2FA prompt (comes AFTER captcha)
			if [[ "$line" == *"Please send required verification code"* ]] || [[ "$line" == *"Requested verification code for 2FA"* ]]; then
				log_info "2FA code required - waiting for submission via browser"
			fi

			# Display filtered lines (but we process ALL lines above)
			# Filter out noisy "Client disconnected" messages from status polling
			if [[ "$line" =~ (INFO|WARN|ERROR) ]] || [[ "$line" =~ "listening on" ]] || [[ "$line" =~ "connected" ]]; then
				# Skip "Client disconnected" spam (from status polling)
				if [[ ! "$line" =~ "Client disconnected" ]]; then
					echo "$line"
				fi
			fi
		done
}

echo "============================================================================"
echo "STEP 4: CLEANUP & START"
echo "============================================================================"
echo ""
echo "Killing any existing eufy-security-server processes..."
pkill -f eufy-security-server 2>/dev/null || true
sleep 1
log_success "Cleanup complete"
# Remove any stale captcha images from previous sessions
if [[ -f "${STATIC_DIR}/eufy_captcha.png" ]]; then
	rm -f "${STATIC_DIR}/eufy_captcha.png"
	log_info "Removed stale captcha image"
fi
echo ""
echo ""

populate_config
execute_bridge
