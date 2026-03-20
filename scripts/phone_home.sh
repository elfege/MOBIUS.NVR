#!/bin/bash
# =============================================================================
# NVR Phone-Home Heartbeat
# =============================================================================
# Sends an anonymous deployment fingerprint to a monitoring endpoint.
# Non-blocking, silent failure. Does not affect NVR operation.
#
# The fingerprint is a SHA-256 hash of hardware identifiers — no personal
# data is collected. Used solely to detect unauthorized commercial deployments.
# =============================================================================

# Lambda function URL (set after deploying infrastructure/lambda/phone_home/)
_NVR_PHONE_HOME_URL="${NVR_PHONE_HOME_URL:-}"

# Generate a stable hardware fingerprint from MAC addresses + machine-id
_nvr_hw_fingerprint() {
    local _macs _machine_id
    _macs=$(ip link show 2>/dev/null | awk '/ether/ {print $2}' | sort | tr '\n' ':')
    _machine_id=$(cat /etc/machine-id 2>/dev/null || echo "unknown")
    echo -n "${_macs}${_machine_id}" | sha256sum | awk '{print $1}'
}

# Send a single heartbeat
nvr_phone_home() {
    # Skip if no URL configured
    [[ -z "$_NVR_PHONE_HOME_URL" ]] && return 0

    local _fingerprint _version _hostname_hash
    _fingerprint="${NVR_HW_FINGERPRINT:-$(_nvr_hw_fingerprint)}"
    _version=$(git -C "$(dirname "${BASH_SOURCE[0]}")/.." describe --tags --always 2>/dev/null || echo "unknown")
    _hostname_hash=$(echo -n "$(hostname)" | sha256sum | awk '{print $1}')

    # 5s timeout, silent failure, always return success
    curl -sf --max-time 5 \
        -X POST "$_NVR_PHONE_HOME_URL" \
        -H "Content-Type: application/json" \
        -d "{
            \"fingerprint\": \"$_fingerprint\",
            \"version\": \"$_version\",
            \"hostname_hash\": \"$_hostname_hash\"
        }" >/dev/null 2>&1 || true
}

# Periodic heartbeat (every 24h) — used inside the container
nvr_phone_home_periodic() {
    while true; do
        nvr_phone_home
        sleep 86400
    done
}
