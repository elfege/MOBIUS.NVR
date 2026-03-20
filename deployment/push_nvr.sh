#!/bin/bash
# =============================================================================
# NVR — Build, Encrypt, and Ship Docker Image
# =============================================================================
# Builds the hardened production Docker image, encrypts it with GPG,
# and optionally ships it to a remote host via SCP.
#
# Based on MOBIUS.JIRA push_jira.sh pattern.
#
# Usage:
#   ./push_nvr.sh                     # Build + encrypt only
#   ./push_nvr.sh --ship user@host    # Build + encrypt + SCP to remote
#   ./push_nvr.sh --ship user@host -J jumphost   # Via SSH proxy
#
# Output: nvr-image.tar.gpg (encrypted Docker image)
#
# The recipient uses customer_start.sh to decrypt and load.
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

IMAGE_NAME="nvr"
IMAGE_TAG="prod"
FULL_IMAGE="${IMAGE_NAME}:${IMAGE_TAG}"
TAR_FILE="nvr-image.tar"
GPG_FILE="${TAR_FILE}.gpg"
SECRETS_TAR="nvr-secrets.tar"
SECRETS_GPG="${SECRETS_TAR}.gpg"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# Parse args
SHIP_TO=""
PROXY_JUMP=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --ship) SHIP_TO="$2"; shift 2 ;;
        -J) PROXY_JUMP="$2"; shift 2 ;;
        *) shift ;;
    esac
done

echo "=========================================="
echo "  NVR — Production Image Build & Ship"
echo "=========================================="
echo ""

# =============================================================================
# Step 1: Build hardened production image
# =============================================================================
echo -e "${CYAN}Building production Docker image...${NC}"
docker build --target production -t "$FULL_IMAGE" -f Dockerfile.production .

if [[ $? -ne 0 ]]; then
    echo -e "${RED}Docker build failed${NC}"
    exit 1
fi
echo -e "${GREEN}Image built: $FULL_IMAGE${NC}"
echo ""

# =============================================================================
# Step 2: Save image to tar
# =============================================================================
echo -e "${CYAN}Saving image to ${TAR_FILE}...${NC}"
docker save "$FULL_IMAGE" -o "$TAR_FILE"
TAR_SIZE=$(du -h "$TAR_FILE" | awk '{print $1}')
echo -e "${GREEN}Image saved: ${TAR_FILE} (${TAR_SIZE})${NC}"
echo ""

# =============================================================================
# Step 3: Encrypt with GPG (AES-256 symmetric)
# =============================================================================
echo -e "${CYAN}Encrypting image...${NC}"

if [[ -n "$GPG_PASSPHRASE" ]]; then
    echo "$GPG_PASSPHRASE" | gpg --batch --yes --passphrase-fd 0 \
        --symmetric --cipher-algo AES256 \
        -o "$GPG_FILE" "$TAR_FILE"
else
    gpg --symmetric --cipher-algo AES256 -o "$GPG_FILE" "$TAR_FILE"
fi

if [[ $? -ne 0 ]]; then
    echo -e "${RED}GPG encryption failed${NC}"
    rm -f "$TAR_FILE"
    exit 1
fi

GPG_SIZE=$(du -h "$GPG_FILE" | awk '{print $1}')
echo -e "${GREEN}Encrypted: ${GPG_FILE} (${GPG_SIZE})${NC}"

# Remove unencrypted tar
rm -f "$TAR_FILE"
echo ""

# =============================================================================
# Step 4: Encrypt secrets bundle (if secrets.env exists)
# =============================================================================
if [[ -f secrets.env ]]; then
    echo -e "${CYAN}Encrypting secrets bundle...${NC}"
    tar cf "$SECRETS_TAR" secrets.env
    if [[ -n "$GPG_PASSPHRASE" ]]; then
        echo "$GPG_PASSPHRASE" | gpg --batch --yes --passphrase-fd 0 \
            --symmetric --cipher-algo AES256 \
            -o "$SECRETS_GPG" "$SECRETS_TAR"
    else
        gpg --symmetric --cipher-algo AES256 -o "$SECRETS_GPG" "$SECRETS_TAR"
    fi
    rm -f "$SECRETS_TAR"
    echo -e "${GREEN}Secrets encrypted: ${SECRETS_GPG}${NC}"
    echo ""
fi

# =============================================================================
# Step 5: Ship to remote (if --ship specified)
# =============================================================================
if [[ -n "$SHIP_TO" ]]; then
    echo -e "${CYAN}Shipping to ${SHIP_TO}...${NC}"

    SCP_OPTS=""
    [[ -n "$PROXY_JUMP" ]] && SCP_OPTS="-J $PROXY_JUMP"

    # Create remote directory
    ssh $SCP_OPTS "$SHIP_TO" "mkdir -p ~/nvr-deploy"

    # Ship encrypted image
    scp $SCP_OPTS "$GPG_FILE" "${SHIP_TO}:~/nvr-deploy/"
    echo -e "${GREEN}Image shipped${NC}"

    # Ship encrypted secrets if they exist
    if [[ -f "$SECRETS_GPG" ]]; then
        scp $SCP_OPTS "$SECRETS_GPG" "${SHIP_TO}:~/nvr-deploy/"
        echo -e "${GREEN}Secrets shipped${NC}"
    fi

    # Ship customer startup script and docker-compose
    scp $SCP_OPTS deployment/customer_start.sh "${SHIP_TO}:~/nvr-deploy/"
    scp $SCP_OPTS docker-compose.yml "${SHIP_TO}:~/nvr-deploy/"
    scp $SCP_OPTS secrets.env.example "${SHIP_TO}:~/nvr-deploy/" 2>/dev/null || true
    echo -e "${GREEN}Startup script shipped${NC}"
    echo ""
fi

# =============================================================================
# Summary
# =============================================================================
echo "=========================================="
echo -e "  ${GREEN}Build & encrypt complete!${NC}"
echo "=========================================="
echo ""
echo "  Image:   $GPG_FILE ($GPG_SIZE)"
[[ -f "$SECRETS_GPG" ]] && echo "  Secrets: $SECRETS_GPG"
echo ""
if [[ -n "$SHIP_TO" ]]; then
    echo "  Shipped to: $SHIP_TO:~/nvr-deploy/"
    echo "  Remote: ssh $SHIP_TO 'cd ~/nvr-deploy && ./customer_start.sh'"
else
    echo "  To ship later:"
    echo "    ./push_nvr.sh --ship user@host"
    echo "    ./push_nvr.sh --ship user@host -J jumphost"
fi
echo ""
