#!/usr/bin/env bash
# eufy_bridge.sh

PORT=${1:-3000}

echo "RUNNING EUFY BRIDGE ON PORT $PORT"
echo "EUFY_BRIDGE_USERNAME: $EUFY_BRIDGE_USERNAME"

[[ -z "${EUFY_BRIDGE_USERNAME}" || -z "${EUFY_BRIDGE_PASSWORD}" || -z "${EUFY_CONFIG_PATH}" -z "${TRUSTED_DEVICE_NAME}" ]] && get_cameras_credentials "EUFY_CAMERAS"

COUNTRY="US"
LANGUAGE="en"


cleanup() {
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
    }' >./tmp/eufy_bridge.json
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
      trustedDeviceName: $trust
    }' >./tmp/eufy_bridge.json
}

execute_bridge() {
	./node_modules/.bin/eufy-security-server \
		--config ./tmp/eufy_bridge.json \
		--port "$PORT" \
		--host "0.0.0.0" 2>&1 |
		while IFS= read -r line; do
			echo "line: $line"
			if [[ "$line" == *"Please send requested"* ]]; then
				echo "****************///////////////////////*********************"
				read -r -p "Enter 2FA verification code from email: " TEMPCODE </dev/tty
				echo "Submitting 2FA code $TEMPCODE to bridge..."
				curl -s -X POST "http://127.0.0.1:$PORT/api/verify_code" \
					-H "Content-Type: application/json" \
					-d "{\"code\":\"$TEMPCODE\"}"

			fi
		done
}

pkill -f eufy-security-server
populate_config
execute_bridge
