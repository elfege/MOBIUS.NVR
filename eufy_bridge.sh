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
# WHAT IT DOES:
#   1. Loads Eufy credentials from AWS Secrets Manager
#   2. Creates config file at /tmp/eufy_bridge.json
#   3. Starts eufy-security-server on port 3000 (WebSocket server)
#   4. Handles CAPTCHA verification (4-digit image code)
#   5. Handles 2FA verification (6-digit email code)
#
# AUTHENTICATION FLOW:
#   Step 1: CAPTCHA (4-digit from image) → driver.set_captcha XXXX
#   Step 2: 2FA (6-digit from email) → driver.set_verify_code XXXXXX
#   Step 3: Connected + Trusted Device
#
# USAGE:
#   From host machine:
#     docker exec -it unified-nvr bash /app/eufy_bridge.sh
#   
#   From inside container:
#     bash /app/eufy_bridge.sh
#
# ============================================================================

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
    echo "ERROR: This script must be run inside the Docker container"
    echo ""
    echo "From your host machine, run:"
    echo "  docker exec -it unified-nvr bash /app/eufy_bridge.sh"
    echo ""
    exit 1
fi

echo "Container check passed"

PORT=${1:-3000}

echo "============================================================================"
echo "STEP 1: LOADING CREDENTIALS"
echo "============================================================================"

echo "EUFY_BRIDGE_USERNAME: $EUFY_BRIDGE_USERNAME"

EUFY_CONFIG_PATH=/tmp/eufy_bridge.json
TRUSTED_DEVICE_NAME="EufyBridge"

# Pull AWS secrets if bridge credentials missing
if [[ -z "${EUFY_BRIDGE_USERNAME}" || -z "${EUFY_BRIDGE_PASSWORD}" ]]; then
    echo "Credentials not in environment, attempting to load from AWS Secrets Manager..."
    pull_aws_secrets "EUFY_CAMERAS"
fi

# Verify bridge credentials loaded
if [[ -z "${EUFY_BRIDGE_USERNAME}" || -z "${EUFY_BRIDGE_PASSWORD}" ]]; then
    echo ""
    echo "ERROR: Bridge credentials missing after attempting to load from AWS"
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

echo "Credentials loaded successfully"
echo "  Username: $EUFY_BRIDGE_USERNAME"
echo "  Password: ******** (hidden)"
echo ""

COUNTRY="US"
LANGUAGE="en"

echo "============================================================================"
echo "STEP 2: CREATING CONFIGURATION"
echo "============================================================================"

cleanup() {
	echo ""
	return 0

	
	echo "Cleaning up configuration file..."
	jq -n \
		--arg user "PLACEHOLDER" \
		--arg pass "PLACEHOLDER" \
		--arg coun "$COUNTRY" \
		--arg lang "$LANGUAGE" \
		--arg trust "$TRUSTED_DEVICE_NAME" \
		'{
      country:$coun,
      language:$lang,
      username:$user,
      password:$pass,
      trustedDeviceName: $trust
    }' >"${EUFY_CONFIG_PATH}"
	echo "Cleanup complete"
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
	
	echo "Configuration file created: ${EUFY_CONFIG_PATH}"
	echo "  Country: $COUNTRY"
	echo "  Language: $LANGUAGE"
	echo "  Trusted Device Name: $TRUSTED_DEVICE_NAME"
	echo "  Persistent Directory: /app (for storing auth tokens)"
	echo ""
}

display_captcha_instructions() {
	local captcha_data="$1"
	
	# Decode base64 image automatically
	local output_file="/mnt/user-data/outputs/eufy_captcha.png"
	echo "$captcha_data" | sed 's/data:image\/png;base64,//' | base64 -d > "$output_file" 2>/dev/null
	
	if [[ $? -eq 0 ]] && [[ -f "$output_file" ]]; then
		echo ""
		echo "========================================================================"
		echo "                 CAPTCHA VERIFICATION REQUIRED                         "
		echo "========================================================================"
		echo ""
		echo "✓ Captcha image automatically decoded and saved!"
		echo ""
		echo "IMPORTANT: You have 5 minutes to read and submit the code"
		echo ""
		echo "========================================================================"
		echo "INSTRUCTIONS:"
		echo "========================================================================"
		echo ""
		echo "1. VIEW THE CAPTCHA IMAGE:"
		echo ""
		echo "   File location: $output_file"
		echo ""
		echo "   - Download from your browser: computer:///mnt/user-data/outputs/eufy_captcha.png"
		echo "   - OR view directly if you have access to the container filesystem"
		echo ""
		echo "2. READ THE 4-DIGIT CODE from the image"
		echo ""
		echo "3. SUBMIT THE CAPTCHA CODE:"
		echo ""
		echo "   - Open a NEW terminal window/tab"
		echo "   - Run: docker exec -it unified-nvr node /app/node_modules/eufy-security-ws/dist/bin/client.js"
		echo "   - At the 'eufy-security>' prompt, type:"
		echo ""
		echo "     driver.set_captcha YOUR_4_DIGIT_CODE"
		echo ""
		echo "   - Press ENTER"
		echo "   - If successful, you'll see: { type: 'result', success: true, ... }"
		echo ""
		echo "4. AFTER CAPTCHA: You'll receive a 2FA email code (6 digits)"
		echo "   - Use the SAME terminal and submit:"
		echo ""
		echo "     driver.set_verify_code YOUR_6_DIGIT_CODE"
		echo ""
		echo "========================================================================"
		echo ""
		echo "⏳ Waiting for captcha submission..."
		echo ""
	else
		# Fallback to manual decode if automatic decode fails
		echo ""
		echo "========================================================================"
		echo "                 CAPTCHA VERIFICATION REQUIRED                         "
		echo "========================================================================"
		echo ""
		echo "⚠️  Automatic decode failed. Manual decode required."
		echo ""
		echo "1. Copy the base64 string below to: https://www.base64decode.net/base64-image-decoder"
		echo ""
		echo "$captcha_data"
		echo ""
		echo "2. Read the 4-digit code from the decoded image"
		echo ""
		echo "3. Submit: driver.set_captcha YOUR_4_DIGIT_CODE"
		echo ""
		echo "========================================================================"
		echo ""
	fi
}

execute_bridge() {
	echo "============================================================================"
	echo "STEP 3: STARTING EUFY SECURITY SERVER"
	echo "============================================================================"
	echo ""
	echo "Server will start on: ws://0.0.0.0:$PORT"
	echo "Connecting to Eufy cloud servers for authentication..."
	echo ""
	echo "NOTE: Eufy requires CAPTCHA verification, then 2FA."
	echo "      Instructions will appear below when needed."
	echo ""
	echo "----------------------------------------------------------------------------"
	
	# Start with verbose logging to capture captcha events
	./node_modules/.bin/eufy-security-server \
		--config "${EUFY_CONFIG_PATH}" \
		--port "$PORT" \
		--host "0.0.0.0" \
		-v \
		2>&1 |
		while IFS= read -r line; do
			# Check for captcha in the line (it appears in DEBUG logs)
			if [[ "$line" =~ captcha.*data:image/png\;base64 ]]; then
				# Extract just the base64 data
				captcha_data=$(echo "$line" | grep -oP 'data:image/png;base64,[^"'\'' ]+' | head -1)
				if [[ -n "$captcha_data" ]]; then
					display_captcha_instructions "$captcha_data"
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
				echo ""
				echo "========================================================================"
				echo "                    2FA VERIFICATION REQUIRED                           "
				echo "========================================================================"
				echo ""
				echo "Eufy has sent a 6-digit verification code to your email address."
				echo ""
				echo "IMPORTANT: You have 5 minutes to submit the code before it expires"
				echo ""
				echo "========================================================================"
				echo "INSTRUCTIONS:"
				echo "========================================================================"
				echo ""
				echo "1. Check your email for the Eufy verification code (6 digits)"
				echo ""
				echo "2. In your EXISTING terminal with 'eufy-security>' prompt:"
				echo ""
				echo "   driver.set_verify_code YOUR_6_DIGIT_CODE"
				echo ""
				echo "3. Press ENTER"
				echo ""
				echo "4. If successful, you'll see: { type: 'result', success: true, ... }"
				echo ""
				echo "========================================================================"
				echo ""
				echo "Waiting for 2FA code submission..."
				echo ""
			fi
			
			# Only show INFO, WARN, ERROR lines (filter out DEBUG noise)
			if [[ "$line" =~ (INFO|WARN|ERROR) ]] || [[ "$line" =~ "listening on" ]] || [[ "$line" =~ "connected" ]]; then
				echo "$line"
			fi
		done
}

echo "============================================================================"
echo "STEP 4: CLEANUP & START"
echo "============================================================================"
echo ""
echo "Killing any existing eufy-security-server processes..."
pkill -f eufy-security-server
sleep 1
echo "Cleanup complete"
echo ""

populate_config
execute_bridge