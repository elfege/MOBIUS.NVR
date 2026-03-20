#!/bin/bash
# =============================================================================
# NVR — Customer Startup Script
# =============================================================================
# Decrypts and loads the NVR Docker image, validates the license,
# and starts the NVR container.
#
# Prerequisites: Docker, docker compose, gpg, curl
#
# Usage: ./customer_start.sh
#
# You need:
#   1. nvr-image.tar.gpg (encrypted Docker image — provided by vendor)
#   2. GPG passphrase (provided by vendor)
#   3. NVR_LICENSE_KEY (purchased at elfege.com)
#   4. secrets.env (camera credentials — copy secrets.env.example)
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

IMAGE_FILE="nvr-image.tar.gpg"
SECRETS_FILE="nvr-secrets.tar.gpg"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo "=========================================="
echo "  NVR — Customer Startup"
echo "=========================================="
echo ""

# =============================================================================
# Check prerequisites
# =============================================================================
for cmd in docker gpg curl; do
    if ! command -v $cmd &>/dev/null; then
        echo -e "${RED}ERROR: '$cmd' is required but not installed.${NC}"
        exit 1
    fi
done

if ! docker compose version &>/dev/null; then
    echo -e "${RED}ERROR: 'docker compose' is required.${NC}"
    exit 1
fi

if [[ ! -f docker-compose.yml ]]; then
    echo -e "${RED}ERROR: docker-compose.yml not found in $(pwd)${NC}"
    exit 1
fi

# =============================================================================
# Step 1: Decrypt Docker image
# =============================================================================
if [[ -f "$IMAGE_FILE" ]]; then
    echo -e "${CYAN}Decrypting Docker image...${NC}"

    if [[ -n "$GPG_PASSPHRASE" ]]; then
        echo "$GPG_PASSPHRASE" | gpg --batch --yes --passphrase-fd 0 \
            --decrypt -o nvr-image.tar "$IMAGE_FILE"
    else
        gpg --decrypt -o nvr-image.tar "$IMAGE_FILE"
    fi

    if [[ $? -ne 0 ]]; then
        echo -e "${RED}Decryption failed. Wrong passphrase?${NC}"
        exit 1
    fi

    echo -e "${CYAN}Loading Docker image...${NC}"
    docker load -i nvr-image.tar
    rm -f nvr-image.tar
    echo -e "${GREEN}Docker image loaded${NC}"
    echo ""
else
    # Check if image already loaded
    if docker images | grep -q "nvr.*prod"; then
        echo -e "${GREEN}Docker image already loaded${NC}"
    else
        echo -e "${RED}ERROR: $IMAGE_FILE not found and no image loaded${NC}"
        exit 1
    fi
fi

# =============================================================================
# Step 2: Decrypt secrets (if encrypted bundle exists)
# =============================================================================
if [[ -f "$SECRETS_FILE" ]]; then
    echo -e "${CYAN}Decrypting secrets...${NC}"

    if [[ -n "$GPG_PASSPHRASE" ]]; then
        echo "$GPG_PASSPHRASE" | gpg --batch --yes --passphrase-fd 0 \
            --decrypt -o nvr-secrets.tar "$SECRETS_FILE"
    else
        gpg --decrypt -o nvr-secrets.tar "$SECRETS_FILE"
    fi

    tar xf nvr-secrets.tar
    rm -f nvr-secrets.tar
    echo -e "${GREEN}Secrets decrypted${NC}"
    echo ""
fi

# =============================================================================
# Step 3: Load environment
# =============================================================================
set -a
if [[ -f .env ]]; then
    . .env
else
    echo -e "${YELLOW}No .env file found — using defaults${NC}"
fi

if [[ -f secrets.env ]]; then
    . secrets.env
    echo -e "${GREEN}Secrets loaded from secrets.env${NC}"
else
    echo -e "${YELLOW}WARNING: No secrets.env found.${NC}"
    echo "  Copy secrets.env.example, fill in credentials, and restart."
    # Generate minimum required
    if [[ -z "$POSTGRES_PASSWORD" ]]; then
        export POSTGRES_PASSWORD=$(openssl rand -hex 16 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(16))")
        echo "POSTGRES_PASSWORD=$POSTGRES_PASSWORD" > secrets.env
        echo -e "${YELLOW}Generated POSTGRES_PASSWORD${NC}"
    fi
fi

if [[ -z "$NVR_SECRET_KEY" ]]; then
    export NVR_SECRET_KEY=$(openssl rand -hex 32 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(32))")
    echo "NVR_SECRET_KEY=$NVR_SECRET_KEY" >> secrets.env 2>/dev/null
fi
set +a

# =============================================================================
# Step 4: Validate license
# =============================================================================
if [[ -n "$NVR_LICENSE_KEY" && -n "$NVR_LICENSE_VALIDATOR_URL" ]]; then
    echo -e "${CYAN}Validating license...${NC}"
    _fp=$(ip link show 2>/dev/null | awk '/ether/ {print $2}' | sort | tr '\n' ':')
    _mid=$(cat /etc/machine-id 2>/dev/null || echo "unknown")
    _fingerprint=$(echo -n "${_fp}${_mid}" | sha256sum | awk '{print $1}')

    _result=$(curl -sf --max-time 10 -X POST "$NVR_LICENSE_VALIDATOR_URL" \
        -H "Content-Type: application/json" \
        -d "{\"license_key\": \"$NVR_LICENSE_KEY\", \"hardware_fingerprint\": \"$_fingerprint\"}" 2>/dev/null || echo '{"status":"offline"}')

    _status=$(echo "$_result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null || echo "unknown")

    case $_status in
        valid)
            echo -e "${GREEN}License valid${NC}"
            ;;
        expired)
            echo -e "${YELLOW}License expired — running in demo mode${NC}"
            ;;
        invalid|revoked)
            echo -e "${RED}License invalid — running in demo mode (7-day trial)${NC}"
            ;;
        offline)
            echo -e "${YELLOW}Could not reach license server — using cached validation${NC}"
            ;;
    esac
    echo ""
else
    echo -e "${YELLOW}No license key configured — running in demo mode (7-day trial)${NC}"
    echo "  Set NVR_LICENSE_KEY in .env to activate full functionality."
    echo "  Purchase a license at elfege.com"
    echo ""
fi

# =============================================================================
# Step 5: Create directories and start
# =============================================================================
mkdir -p logs streams config

# Stop existing container if running
docker compose down 2>/dev/null || true

# Create Docker network if needed
_NETWORK_NAME="${NVR_NETWORK_NAME:-0_mobiusnvr_nvr-net}"
docker network inspect "$_NETWORK_NAME" >/dev/null 2>&1 || \
    docker network create "$_NETWORK_NAME"

echo "Starting NVR container..."
docker compose up -d

echo ""
echo "Waiting for container to start..."
sleep 10

if docker ps | grep -q unified-nvr; then
    echo -e "${GREEN}NVR is running!${NC}"
    echo ""
    echo "Access the NVR at:"
    echo "  - HTTPS: https://$(hostname -I | awk '{print $1}'):${NVR_EDGE_HTTPS_PORT:-8444}/"
    echo "  - HTTP:  http://$(hostname -I | awk '{print $1}'):5000"
    echo ""
    echo "Useful commands:"
    echo "  View logs:    docker compose logs -f"
    echo "  Stop:         docker compose down"
    echo "  Restart:      ./customer_start.sh"
else
    echo -e "${RED}Container failed to start${NC}"
    echo "Check logs with: docker compose logs"
    exit 1
fi

# =============================================================================
# Cleanup: remove encrypted artifacts after successful load
# =============================================================================
if [[ -f "$IMAGE_FILE" ]]; then
    echo ""
    echo -e "${CYAN}Cleaning up encrypted artifacts...${NC}"
    rm -f "$IMAGE_FILE" "$SECRETS_FILE"
    echo -e "${GREEN}Encrypted files removed (image is loaded in Docker)${NC}"
fi
