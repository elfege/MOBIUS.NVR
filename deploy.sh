#!/bin/bash
# deploy.sh - Build Docker image for Unified NVR

set -e

deactivate &>/dev/null || true

SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"
SCRIPT_R_PATH=$(realpath "${BASH_SOURCE[0]}")
SCRIPT_DIR="${SCRIPT_R_PATH%${SCRIPT_NAME}}"

cd "$SCRIPT_DIR" &>/dev/null || true

. ~/.env.colors
. ~/logger.sh --no-exec &>/dev/null

echo "=========================================="
echo "  Unified NVR - Docker Image Build"
echo "=========================================="
echo ""

# Check if Dockerfile exists
if [ ! -f Dockerfile ]; then
    echo -e "${RED}✗ Dockerfile not found!${NC}"
    exit 1
fi

# Check if docker-compose.yml exists
if [ ! -f docker-compose.yml ]; then
    echo -e "${RED}✗ docker-compose.yml not found!${NC}"
    exit 1
fi

# Stop and remove containers
docker compose down &>/dev/null || true 

# Remove the old image
docker rmi 0_nvr-nvr &>/dev/null || true 

# Clean up unused Docker resources
docker system prune -f  || true 

get_cameras_credentials >/dev/null 

echo "Building Docker image..."
docker compose build

if [ $? -eq 0 ]; then
    echo ""
    echo -e "${GREEN}✓ Docker image built successfully${NC}"
    echo ""
    echo "To start the container, run:"
    echo "  ./start.sh"
else
    echo -e "${RED}✗ Docker build failed${NC}"
    exit 1
fi