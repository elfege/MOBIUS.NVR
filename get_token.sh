#!/bin/bash

# get_token.sh - UniFi Protect Authentication Script
# Gets authentication token for UCKG2 Plus and saves cookies

set -e

# Configuration
UNIFI_HOST="192.168.10.3"
SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
COOKIES_FILE="$HOME/0_UNIFI_NVR/cookies/cookies.txt"
AUTH_LOG="$SCRIPT_DIR/auth.log"
ENV_FILE="$HOME/0_UNIFI_NVR/LL-HLS/.env.unifi"

: >"$COOKIES_FILE"

# Load environment file if it exists
. "$ENV_FILE" &>/dev/null || true

pull_secrets_from_aws UniFi-Camera-Credentials

# Create cookies directory if it doesn't exist
mkdir -p "$(dirname "$COOKIES_FILE")"

# Check if jq is available and auto-install
if ! command -v jq &>/dev/null; then
	echo "Installing jq..."
	sudo apt install jq -y
fi

echo "=== UniFi Protect Authentication ==="
echo "Host: https://$UNIFI_HOST"
echo

# Prompt for PROTECT_USERNAME
if [[ -z "$PROTECT_USERNAME" ]]; then
	read -p "Username: " PROTECT_USERNAME
fi

export PROTECT_USERNAME
export PROTECT_SERVER_PASSWORD



if [[ -z "$PROTECT_SERVER_PASSWORD" ]]; then
	# Prompt for PROTECT_SERVER_PASSWORD (hidden input)
	echo -n "Password: "
	read -s PROTECT_SERVER_PASSWORD
	# Save password to environment file for future use
	: >"$ENV_FILE"
	read -r -p "Save password in $(basename "$ENV_FILE")? (Be Cautious)" save
	if [[ "$save" =~ ^[yY]([eE][sS])?$ ]]; then
		echo "PROTECT_SERVER_PASSWORD=$PROTECT_SERVER_PASSWORD" >>"$ENV_FILE"
		echo "password saved to $(basename "$ENV_FILE")"
	fi
	echo
fi


# Validate inputs
if [[ -z "$PROTECT_USERNAME" || -z "$PROTECT_SERVER_PASSWORD" ]]; then
	echo "Error: Username and PROTECT_SERVER_PASSWORD are required"
	exit 1
fi

echo
echo "Authenticating..."

# Perform initial authentication
response=$(curl -k -s -w "HTTPSTATUS:%{http_code}" \
	-X POST "https://$UNIFI_HOST/api/auth/login" \
	-H "Content-Type: application/json" \
	-d "{\"username\": \"$PROTECT_USERNAME\", \"password\": \"$PROTECT_SERVER_PASSWORD\"}" \
	-c "$COOKIES_FILE")

# Extract HTTP status and body
http_code=$(echo "$response" | tr -d '\n' | sed -e 's/.*HTTPSTATUS://')
body=$(echo "$response" | sed -e 's/HTTPSTATUS:.*//g')

# Log the attempt
echo "$(date): Initial auth attempt for $PROTECT_USERNAME - HTTP $http_code" | tee -a "$AUTH_LOG"

display_block "response body: $body"

# Check if 2FA is required (can be HTTP 499 or 200)
if [[ "$body" == *"MFA_AUTH_REQUIRED"* ]]; then
	echo "✓ 2FA required - processing..."

	# Parse available authenticators
	echo "Available 2FA methods:"
	echo "$body" | jq -r '.data.authenticators[] | "  \(.type): \(.name // .email)"'

	# Get the default MFA ID (iPhone push notification)
	default_mfa=$(echo "$body" | jq -r '.data.user.default_mfa')
	mfa_cookie=$(echo "$body" | jq -r '.data.mfaCookie')

	display_block "default_mfa: $default_mfa"
	display_block "mfa_cookie: $mfa_cookie"

	echo
	echo "Using default 2FA method (iPhone push notification)..."
	echo "Please check  iPhone/Watch for the push notification and approve it."
	echo

	# Extract and set the MFA cookie properly
	echo "Debug: MFA Cookie = $mfa_cookie"

	# Extract cookie value and APPEND to existing cookies
	cookie_value=$(echo "$mfa_cookie" | sed 's/UBIC_2FA=//')

	display_block "cookie_value: $cookie_value"

	echo -e "$UNIFI_HOST\tFALSE\t/\tTRUE\t0\tUBIC_2FA\t$cookie_value" >>"$COOKIES_FILE"

	log "=== Cookie file after writing ===" "$TO_TTY"
	cat "$COOKIES_FILE"
	echo

	# After getting the MFA info, BEFORE sending the challenge:
	echo
	echo "Using default 2FA method (iPhone push notification)..."
	echo
	echo "⚠️  IMPORTANT: Get  Unifi App ready!"
	echo "The next step will send a push notification that you need to approve."
	echo
	read -p "Press Enter when you're ready to receive the 2FA notification..."
	echo

	# NOW send the 2FA challenge
	echo "Sending 2FA challenge..."
	challenge_response=$(curl -k -s -w "HTTPSTATUS:%{http_code}" \
		-X POST "https://$UNIFI_HOST/api/auth/mfa/challenge" \
		-H "Content-Type: application/json" \
		-H "Cookie: $mfa_cookie" \
		-d "{\"authenticatorId\": \"$default_mfa\"}")

	challenge_code=$(echo "$challenge_response" | tr -d '\n' | sed -e 's/.*HTTPSTATUS://')
	challenge_body=$(echo "$challenge_response" | sed -e 's/HTTPSTATUS:.*//g')

	log "challenge_code: $challenge_code" "$TO_TTY"
	display_block "challenge_body: $challenge_body"

	if [[ "$challenge_code" -eq 200 ]]; then
		echo "✓ 2FA challenge sent to  iPhone/Watch"
		echo
		echo "Waiting for approval... (timeout in 2 minutes)"

		# Wait for 2FA approval with polling (extended timeout)
		for i in {1..24}; do
			echo -n "Checking approval attempt $i/24... "

			verify_response=$(curl -k -s -w "HTTPSTATUS:%{http_code}" \
				-X POST "https://$UNIFI_HOST/api/auth/mfa/verify" \
				-H "Content-Type: application/json" \
				-b "$COOKIES_FILE" \
				-d "{\"authenticatorId\": \"$default_mfa\"}")

			verify_code=$(echo "$verify_response" | tr -d '\n' | sed -e 's/.*HTTPSTATUS://')
			verify_body=$(echo "$verify_response" | sed -e 's/HTTPSTATUS:.*//g')

			if [[ "$verify_code" -eq 200 ]]; then
				echo "✓ 2FA verification successful!"
				break
			else
				echo "waiting..."
				sleep 5
			fi

			if [[ $i -eq 24 ]]; then
				echo "✗ 2FA verification timeout (2 minutes)"
				echo "Response: $verify_body"
				exit 1
			fi
		done

	else
		echo "✗ Failed to send 2FA challenge (HTTP $challenge_code)"
		echo "Response: $challenge_body"
		exit 1
	fi

elif [[ "$http_code" -eq 200 ]]; then
	echo "✓ Direct authentication successful (no 2FA required)!"
else
	echo "✗ Authentication failed (HTTP $http_code)"
	if [[ -n "$body" ]]; then
		echo "Response: $body"
	fi
	exit 1
fi

echo "✓ Authentication complete!"
echo "✓ Cookies saved to: $COOKIES_FILE"

# Test cookie validity with a simple API call
echo
echo "Testing authentication..."
test_response=$(curl -k -s -w "HTTPSTATUS:%{http_code}" \
	-b "$COOKIES_FILE" \
	"https://$UNIFI_HOST/proxy/protect/api/bootstrap")

test_code=$(echo "$test_response" | tr -d '\n' | sed -e 's/.*HTTPSTATUS://')

log "test_code: $test_code" "$TO_TTY"

if [[ "$test_code" -eq 200 ]]; then
	echo "✓ Token validation successful"
	echo
	echo "Ready to use Protect API with cookies from: $COOKIES_FILE"
else
	echo "⚠ Warning: Token validation failed (HTTP $test_code)"
fi

echo

log "Usage examples:" "$TO_TTY"
echo
repeat_print "═"
echo -e "${ACCENT_YELLOW}" " - List cameras:" "$NC"
echo -e "  curl -k -b '$COOKIES_FILE' https://$UNIFI_HOST/proxy/protect/api/cameras " "$NC"
echo
echo -e "${ACCENT_YELLOW}" " - Get bootstrap info:" "$NC"
echo -e " curl -k -b '$COOKIES_FILE' https://$UNIFI_HOST/proxy/protect/api/bootstrap " "$NC"
repeat_print "═"
