#!/bin/bash
# =============================================================================
# Generic NVR - Docker Image Build & Deploy
# =============================================================================
# Rebuilds all Docker images and starts the stack.
# Use ./_start.sh if you want to skip rebuild.
#
# No personal environment dependencies. Works with Docker only.
#
# Usage:
#   ./_deploy.sh                     # Rebuild + start
#   ./_deploy.sh --prune             # Prune docker system first
#   ./_deploy.sh --no-cache          # Force full rebuild (no cache)
#   ./_deploy.sh --prune --no-cache  # Both
#
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

# Colors (inline — no external dependencies)
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# Parse flags
do_prune=false
do_nocache=false
for arg in "$@"; do
    case $arg in
        --prune) do_prune=true ;;
        --no-cache) do_nocache=true ;;
    esac
done

echo "=========================================="
echo "  NVR - Docker Image Build"
echo "=========================================="
echo ""

# Check prerequisites
if [[ ! -f Dockerfile ]]; then
    echo -e "${RED}ERROR: Dockerfile not found in $(pwd)${NC}"
    exit 1
fi
if [[ ! -f docker-compose.yml ]]; then
    echo -e "${RED}ERROR: docker-compose.yml not found in $(pwd)${NC}"
    exit 1
fi

# Prune if requested
if $do_prune; then
    echo "Pruning Docker resources..."
    docker system prune -f || true
    echo ""
fi

# Stop and remove existing containers
echo -e "${CYAN}Cleaning up containers and images...${NC}"
docker compose down 2>/dev/null || true
docker rmi 0_nvr-nvr 2>/dev/null || true
echo -e "${GREEN}Cleanup complete${NC}"
echo ""

# Load env files for build context
set -a
[[ -f .env ]] && . .env
[[ -f secrets.env ]] && . secrets.env
set +a

# Build
if $do_nocache; then
    echo "Building Docker image (--no-cache, full rebuild)..."
    docker compose build --no-cache
else
    echo "Building Docker image (cached)..."
    docker compose build
fi

if [[ $? -eq 0 ]]; then
    echo ""
    echo -e "${GREEN}Docker image built successfully${NC}"
    echo ""
    ./_start.sh
else
    echo -e "${RED}Docker build failed${NC}"
    exit 1
fi
