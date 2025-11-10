#!/usr/bin/env bash
# eufy_bridge_login.sh
#
# ============================================================================
# EUFY BRIDGE AUTHENTICATION HANDLER
# ============================================================================
#
# PURPOSE:
#   Handles Eufy bridge authentication (captcha + 2FA) through web interface
#
# CALLED BY:
#   eufy_bridge.sh (when authentication is required)
#
# WHAT IT DOES:
#   1. Monitors bridge output for captcha/2FA events
#   2. Decodes captcha images to PNG
#   3. Saves to /app/static/eufy_captcha.png
#   4. Opens browser to https://localhost:8443/eufy-auth
#   5. Waits for authentication completion
#
# RETURNS:
#   0 = Authentication successful
#   1 = Authentication failed
#   2 = Timeout
#
# ============================================================================

set -euo pipefail

# Configuration
BRIDGE_PORT="${1:-3000}"

# Get host IP (not container IP)
# Try multiple methods to get the actual host IP on the LAN
if [[ -n "${LOCAL_HOST_IP}" ]]; then
    # Use environment variable if set
    SERVER_IP="${LOCAL_HOST_IP}"
elif command -v ip &> /dev/null; then
    # Get default route interface IP (usually the LAN IP)
    SERVER_IP=$(ip route get 1.1.1.1 | awk '{print $7}' | head -1)
elif command -v hostname &> /dev/null; then
    # Fallback to hostname -I (may return multiple IPs)
    SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
fi

# If still empty or looks like container IP (172.x or 127.x), use hardcoded fallback
if [[ -z "${SERVER_IP}" ]] || [[ "${SERVER_IP}" =~ ^172\. ]] || [[ "${SERVER_IP}" =~ ^127\. ]]; then
    # Hardcoded fallback - update this to match your server's IP
    SERVER_IP="192.168.10.20"
fi

FLASK_URL="${FLASK_URL:-https://${SERVER_IP}:8443}"
AUTH_TIMEOUT="${2:-300}"  # 5 minutes default
POLL_INTERVAL=2
STATIC_DIR="/app/static"
CAPTCHA_FILE="${STATIC_DIR}/eufy_captcha.png"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
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

# Check if captcha event exists in bridge output
wait_for_captcha() {
    log_info "Waiting for captcha event from bridge..."
    
    local start_time=$(date +%s)
    local timeout=30
    
    while true; do
        # Check timeout
        local current_time=$(date +%s)
        local elapsed=$((current_time - start_time))
        
        if [ $elapsed -gt $timeout ]; then
            log_error "Timeout waiting for captcha event"
            return 1
        fi
        
        # Check if bridge is emitting captcha
        # Note: This assumes the bridge output is being monitored by calling script
        # For now, we'll just wait a bit for the captcha to be ready
        sleep 2
        
        # Check if captcha file was created by calling script
        if [ -f "$CAPTCHA_FILE" ]; then
            log_success "Captcha image ready: $CAPTCHA_FILE"
            return 0
        fi
        
        sleep 1
    done
}

# Decode base64 captcha to PNG
decode_captcha() {
    local base64_data="$1"
    
    log_info "Decoding captcha image..."
    
    # Ensure static directory exists
    mkdir -p "$STATIC_DIR"
    
    # Remove data URI prefix if present
    base64_data=$(echo "$base64_data" | sed 's/data:image\/png;base64,//')
    
    # Decode to PNG
    if echo "$base64_data" | base64 -d > "$CAPTCHA_FILE" 2>/dev/null; then
        log_success "Captcha decoded and saved: $CAPTCHA_FILE"
        return 0
    else
        log_error "Failed to decode captcha image"
        return 1
    fi
}

# Open browser to auth page (headless-friendly)
open_browser() {
    local auth_url="${FLASK_URL}/eufy-auth"
    
    echo ""
    echo "========================================================================"
    echo "               AUTHENTICATION URL                                       "
    echo "========================================================================"
    echo ""
    echo "  Open this URL in your browser:"
    echo ""
    echo "      ${auth_url}"
    echo ""
    echo "========================================================================"
    echo ""
    
    # Try to auto-open (will fail silently on headless servers)
    if command -v xdg-open &> /dev/null; then
        xdg-open "$auth_url" &> /dev/null 2>&1 &
        log_info "Attempted to open browser (may not work on headless server)"
    elif command -v open &> /dev/null; then
        open "$auth_url" &> /dev/null 2>&1 &
        log_info "Attempted to open browser (macOS)"
    else
        log_info "Running on headless server - manual browser navigation required"
    fi
}

# Poll Flask API to check authentication status
wait_for_authentication() {
    log_info "Waiting for authentication completion..."
    log_info "Please complete the authentication in your browser"
    
    local start_time=$(date +%s)
    local check_count=0
    
    while true; do
        # Check timeout
        local current_time=$(date +%s)
        local elapsed=$((current_time - start_time))
        
        if [ $elapsed -gt $AUTH_TIMEOUT ]; then
            log_error "Authentication timeout after ${AUTH_TIMEOUT}s"
            return 2
        fi
        
        # Poll Flask status endpoint
        local status_response=$(curl -sk "${FLASK_URL}/api/eufy-auth/status" 2>/dev/null || echo '{"connected":false}')
        
        # Parse JSON response (simple grep - could use jq if available)
        if echo "$status_response" | grep -q '"connected".*:.*true'; then
            log_success "Authentication successful!"
            return 0
        fi
        
        # Progress indicator
        check_count=$((check_count + 1))
        if [ $((check_count % 5)) -eq 0 ]; then
            local minutes=$((elapsed / 60))
            local seconds=$((elapsed % 60))
            log_info "Still waiting... (${minutes}m ${seconds}s elapsed)"
        fi
        
        sleep $POLL_INTERVAL
    done
}

# Main authentication flow
authenticate() {
    echo ""
    echo "========================================================================"
    echo "                EUFY BRIDGE AUTHENTICATION REQUIRED                     "
    echo "========================================================================"
    echo ""
    
    # Step 1: Wait for captcha to be ready
    if ! wait_for_captcha; then
        log_error "Failed to obtain captcha"
        return 1
    fi
    
    # Step 2: Open browser
    open_browser
    
    echo ""
    echo "========================================================================"
    echo "INSTRUCTIONS:"
    echo "========================================================================"
    echo ""
    echo "1. Open the URL above in ANY browser on your network"
    echo ""
    echo "2. Complete authentication in the browser window"
    echo "   - Enter the 4-digit captcha code from the image"
    echo "   - Check your email for the 6-digit verification code"
    echo "   - Enter the 6-digit code to complete authentication"
    echo ""
    echo "3. This script will detect when authentication completes"
    echo ""
    echo "========================================================================"
    echo ""
    
    # Step 3: Wait for authentication completion
    local exit_code
    wait_for_authentication
    exit_code=$?
    
    if [ $exit_code -eq 0 ]; then
        echo ""
        echo "========================================================================"
        echo "               AUTHENTICATION COMPLETED SUCCESSFULLY                    "
        echo "========================================================================"
        echo ""
        return 0
    elif [ $exit_code -eq 2 ]; then
        log_error "Authentication timed out"
        return 2
    else
        log_error "Authentication failed"
        return 1
    fi
}

# Cleanup function
cleanup() {
    log_info "Cleaning up..."
    # Remove captcha file
    rm -f "$CAPTCHA_FILE"
}

trap cleanup EXIT

# Parse command line arguments
case "${1:-authenticate}" in
    decode)
        if [ -z "${2:-}" ]; then
            log_error "Usage: $0 decode <base64_data>"
            exit 1
        fi
        decode_captcha "$2"
        ;;
    open)
        open_browser
        ;;
    wait)
        wait_for_authentication
        ;;
    authenticate|*)
        authenticate
        exit $?
        ;;
esac