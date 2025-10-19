#!/bin/bash
# start.sh - Start Unified NVR container with credentials

set -e

deactivate &>/dev/null || true

SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"
SCRIPT_R_PATH=$(realpath "${BASH_SOURCE[0]}")
SCRIPT_DIR="${SCRIPT_R_PATH%${SCRIPT_NAME}}"

cd "$SCRIPT_DIR" &>/dev/null || true

. ~/.env.colors
. ~/logger.sh --no-exec &>/dev/null

echo "=========================================="
echo "  Unified NVR - Container Startup"
echo "=========================================="
echo ""

# Source credentials from .bash_utils if available
if [ -f ~/.bash_utils ]; then
	echo "Loading AWS secrets from .bash_utils..."
	source ~/.bash_utils --no-exec >/dev/null

	# Try to pull Protect credentials from AWS
	if command -v pull_secrets_from_aws &>/dev/null; then
		if pull_secrets_from_aws Unifi-Camera-Credentials 2>/dev/null; then
			echo -e "${GREEN}✓ Loaded credentials from AWS Secrets Manager${NC}"

			# Export for Docker Compose to use
			export PROTECT_USERNAME
			export PROTECT_SERVER_PASSWORD
			echo -e "${GREEN}✓ Credentials exported for container${NC}"
		fi
	fi
fi

# Create necessary directories
echo ""
echo "Creating directories..."
mkdir -p logs streams config

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
start_spinner 20 "$BLUE Exporting Cameras Credentials..."
get_cameras_credentials >/dev/null
stop_spinner
set +a

# Start the container
echo ""
echo "Starting container..."
docker compose up -d

# Wait for container to start
echo ""
echo "Waiting for container to start..."
sleep 3

# Check container status
if docker ps | grep -q unified-nvr; then
	echo -e "${GREEN}✓ Container is running!${NC}"
	# AFTER container is running
	echo ""
	echo "Access the NVR at:"
	echo "  - HTTPS (edge, HTTP/2): https://$(hostname -I | awk '{print $1}')/"
	echo "  - HTTP  (direct app):   http://$(hostname -I | awk '{print $1}'):5000"
	echo ""
	if [ ! -f certs/dev/fullchain.pem ] || [ ! -f certs/dev/privkey.pem ]; then
		echo "⚠️  TLS certs not found in certs/dev/. Run:"
		echo "    ~/0_NVR/0_MAINTENANCE_SCRIPTS/make_self_signed_tls.sh"
	fi
	echo "Useful commands:"
	echo "  View logs:        docker compose logs -f"
	echo "  Follow logs:      docker compose logs -f nvr"
	echo "  Stop container:   docker compose down"
	echo "  Restart:          docker compose restart"
	echo "  Rebuild:          ./deploy.sh"
	echo "  Shell access:     docker exec -it unified-nvr /bin/bash"
	echo ""
	echo "Checking health in 10 seconds..."
	sleep 10

	# Check health
	if curl -kI https://localhost/api/status >/dev/null 2>&1; then
		echo -e "${GREEN}✓ HTTPS Health check passed!${NC}"
	else
		echo -e "${YELLOW}⚠️  Health check failed - check logs${NC}"
		echo "Run: docker compose logs -f"
	fi
	if curl -s http://localhost:5000/api/status >/dev/null 2>&1; then
		echo -e "${GREEN}✓ HTTP Health check passed!${NC}"
	else
		echo -e "${YELLOW}⚠️  Health check failed - check logs${NC}"
		echo "Run: docker compose logs -f"
	fi
else
	echo -e "${RED}✗ Container failed to start${NC}"
	echo "Check logs with: docker compose logs"
	exit 1
fi
