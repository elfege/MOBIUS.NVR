#!/bin/bash
# start.sh - Start Unified NVR container with credentials

# set -e

deactivate &>/dev/null || true

SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"
SCRIPT_R_PATH=$(realpath "${BASH_SOURCE[0]}")
SCRIPT_DIR="${SCRIPT_R_PATH%${SCRIPT_NAME}}"

cd "$SCRIPT_DIR" &>/dev/null || true

. ~/.env.colors
. ~/logger.sh --no-exec &>/dev/null
. ~/.bash_utils &>/dev/null || { 
	echo -e "${RED}✗ Failed to source ~/.bash_utils - it is required to pull secrets${NC}"
	exit 1
}

echo "=========================================="
echo "  Unified NVR - Container Startup"
echo "=========================================="
echo ""

# Create necessary directories
echo ""
echo "Creating directories..."
mkdir -p logs streams config

# Ensure entrypoint.sh is executable (bind mount overrides Docker build chmod)
chmod +x entrypoint.sh 2>/dev/null || true

# Check if config/cameras.json exists
if [ ! -f config/cameras.json ]; then
	echo -e "${YELLOW}⚠️  No cameras.json found in config/!${NC}"
	echo -e "${YELLOW}⚠️  Please edit config/cameras.json with  camera details${NC}"
	exit 1
fi

# Stop existing container if running
if docker ps | grep -q unified-nvr; then
	echo ""
	echo "Stopping existing container..."
	docker compose down
fi

# export credentials
set -a
. ~/0_NVR/.env
# start_spinner 20 "$BLUE Exporting Secrets..."
pull_nvr_secrets # >/dev/null

# Detect and export host IP
export LOCAL_HOST_IP=$(ip route get 1.1.1.1 | awk '{print $7}' | head -1)
stop_spinner
set +a

if [[ -f ~/0_NVR/scripts/update_mediamtx_paths.sh && -f ~/0_NVR/packager/mediamtx.yml ]]; then
	start_spinner 20 "$CYAN Appending packager/mediamtx.yml"
	~/0_NVR/scripts/update_mediamtx_paths.sh >/dev/null
	stop_spinner
fi

if [[ -f ~/0_NVR/scripts/update_neolink_config.sh && -f ~/0_NVR/config/neolink.toml ]]; then
	start_spinner 20 "$CYAN Updating config/neolink.toml"
	~/0_NVR/scripts/update_neolink_config.sh >/dev/null
	stop_spinner
fi

if [[ -f ~/0_NVR/scripts/update_recording_settings.sh && -f ~/0_NVR/config/recording_settings.json ]]; then
	start_spinner 20 "$CYAN Syncing recording_settings.json with cameras"
	~/0_NVR/scripts/update_recording_settings.sh >/dev/null
	stop_spinner
fi

# Ensure recording paths have proper permissions (UID 1000 for container appuser)
if [[ -f ~/0_NVR/ensure_recording_paths.sh ]]; then
	echo ""
	echo "Ensuring recording path permissions..."
	~/0_NVR/ensure_recording_paths.sh >/dev/null 
fi

# Ensure TLS certs exist (MediaMTX + nginx need them)
if [ ! -f certs/dev/fullchain.pem ] || [ ! -f certs/dev/privkey.pem ]; then
	echo ""
	echo "TLS certs missing — generating self-signed certs..."
	~/0_NVR/0_MAINTENANCE_SCRIPTS/make_self_signed_tls.sh
	echo -e "${GREEN}✓ TLS certs generated${NC}"
fi

# Start the container
echo ""
echo "Starting container..."
docker compose up -d

# Wait for container to start
echo ""
echo "Waiting for container to start..."
sleep 10

# Check container status
if docker ps | grep -q unified-nvr; then
	echo -e "${GREEN}✓ Container is running!${NC}"
	# AFTER container is running
	echo ""
	echo "Access the NVR at:"
	echo "  - HTTPS (edge, HTTP/2): https://$(hostname -I | awk '{print $1}')/"
	echo "  - HTTP  (direct app):   http://$(hostname -I | awk '{print $1}'):5000"
	echo ""
	echo "Useful commands:"
	echo "  View logs:        docker compose logs -f"
	echo "  Follow logs:      docker compose logs -f nvr"
	echo "  Stop container:   docker compose down"
	echo "  Restart:          docker compose restart"
	echo "  Rebuild:          ./deploy.sh"
	echo "  Shell access:     docker exec -it unified-nvr /bin/bash"
	echo ""
	echo "Checking health in 20 seconds..."
	sleep 20

	# Check health
	if curl -s http://localhost:5000/api/status >/dev/null 2>&1; then
		echo -e "${GREEN}✓ HTTP Health check passed!${NC}"
	else
		echo -e "${YELLOW}⚠️  Health check failed - check logs${NC}"
		echo "Run: docker compose logs -f"
	fi
	if curl -kI https://localhost:8443/api/status >/dev/null 2>&1; then
		echo -e "${GREEN}✓ HTTPS Health check passed!${NC}"
	else
		echo -e "${YELLOW}⚠️  Health check failed - check logs${NC}"
		echo "Run: docker compose logs -f"
	fi

else
	echo -e "${RED}✗ Container failed to start${NC}"
	echo "Check logs with: docker compose logs"
	exit 1
fi
