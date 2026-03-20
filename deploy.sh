#!/bin/bash
# ============================================
# Unified NVR - Docker Image Build & Deploy
# ============================================
# Rebuilds all Docker images and starts the stack.
# Use ./start.sh if you want to skip rebuild.
#
# Usage:
#   ./deploy.sh                     # Rebuild + start (prompts for prune/no-cache)
#   ./deploy.sh --prune             # Prune first, skip prompt
#   ./deploy.sh --no-cache          # No-cache build, skip prompt
#   ./deploy.sh --prune --no-cache  # Both, no prompts
#
# ============================================

# set -e

deactivate &>/dev/null || true

SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"
SCRIPT_R_PATH=$(realpath "${BASH_SOURCE[0]}")
SCRIPT_DIR="${SCRIPT_R_PATH%${SCRIPT_NAME}}"

cd "$SCRIPT_DIR" &>/dev/null || true

sudo chown -R "$USER":"$USER" ./ &>/dev/null || true

# =============================================================================
# Portable color/utility setup — works with or without ~/.env.colors
# =============================================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

[[ -f ~/.env.colors ]] && . ~/.env.colors
if [[ -f ~/logger.sh ]]; then
	. ~/logger.sh --no-exec &>/dev/null
else
	start_spinner() { echo -e "  $2"; }
	stop_spinner() { :; }
fi

# Source custom-env.sh if available (non-critical)
[[ -f /etc/profile.d/custom-env.sh ]] && . /etc/profile.d/custom-env.sh --no-exec &>/dev/null

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
echo "  Unified NVR - Docker Image Build"
echo "=========================================="
echo ""

# Check prerequisites
if [[ ! -f Dockerfile ]]; then
	echo -e "${RED}ERROR: Dockerfile not found!${NC}"
	exit 1
fi
if [[ ! -f docker-compose.yml ]]; then
	echo -e "${RED}ERROR: docker-compose.yml not found!${NC}"
	exit 1
fi

# Prune: flag or prompt (defaults to no on timeout)
if ! $do_prune; then
	prune_answer="no"
	read -t 10 -r -p "Prune Docker system? (yes/no, 10s timeout = no): " prune_answer || true
	[[ "$prune_answer" == "yes" || "$prune_answer" == "YES" ]] && do_prune=true
fi
if $do_prune; then
	echo "Pruning Docker resources..."
	docker system prune -f || true
fi

# No-cache: flag or prompt (defaults to yes on timeout for a clean build)
if ! $do_nocache; then
	nocache_answer=""
	read -t 10 -r -p "No-cache build? (type 'no' to skip, ENTER/timeout = yes): " nocache_answer || true
	[[ "$nocache_answer" == "no" || "$nocache_answer" == "NO" ]] || do_nocache=true
fi

start_spinner "" "$CYAN Cleaning up containers and images..."
# Stop and remove containers
docker compose down &>/dev/null || true

echo -e "${GREEN}Containers stopped and removed${NC}"
echo ""
# Remove the old image
docker rmi 0_nvr-nvr &>/dev/null || true
echo -e "${GREEN}Cleanup complete${NC}"
echo ""

stop_spinner &>/dev/null || true

# Load secrets for the build (env vars used in docker compose build context)
# Uses same credential mode as start.sh
set -a
[[ -f .env ]] && . .env
[[ -f secrets.env ]] && . secrets.env

# If no secrets.env and bash_utils available, try AWS pull
if [[ ! -f secrets.env ]] && [[ -f ~/.bash_utils ]]; then
	. ~/.bash_utils &>/dev/null
	echo "Fetching camera credentials..."
	if type pull_nvr_secrets &>/dev/null; then
		pull_nvr_secrets >/dev/null
	fi
fi
set +a

# Build Docker image
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
	./start.sh
else
	echo -e "${RED}Docker build failed${NC}"
	exit 1
fi
