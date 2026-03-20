#!/bin/bash
# =============================================================================
# Generic NVR - Container Startup
# =============================================================================
# Idempotent startup script. No personal environment dependencies.
# Reads credentials from secrets.env (copy secrets.env.example as template).
#
# Requirements: Docker, docker compose, curl
#
# Usage: ./_start.sh
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

# Colors (inline — no external dependencies)
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# =============================================================================
# Prerequisites
# =============================================================================
if [[ ! -f docker-compose.yml ]] || [[ ! -f Dockerfile ]]; then
    echo -e "${RED}ERROR: _start.sh must be run from the NVR project root${NC}"
    echo "Current:  $(pwd)"
    exit 1
fi

echo "=========================================="
echo "  NVR - Container Startup"
echo "=========================================="
echo ""

# =============================================================================
# Load .env (non-secret config: feature flags, ports, paths)
# =============================================================================
set -a
if [[ -f .env ]]; then
    . .env
else
    echo -e "${RED}ERROR: .env file not found. Copy .env.example and configure.${NC}"
    exit 1
fi

# Detect host IP — only export if detection succeeds; otherwise docker compose uses .env value.
_detected_ip=$(ip route get 1.1.1.1 2>/dev/null | awk '{print $7}' | head -1)
if [[ -n "$_detected_ip" ]]; then
    export NVR_LOCAL_HOST_IP="$_detected_ip"
else
    echo -e "${YELLOW}WARNING: Could not auto-detect host IP via ip route. Using .env value: ${NVR_LOCAL_HOST_IP:-unset}${NC}"
fi

# =============================================================================
# Load secrets from secrets.env
# =============================================================================
if [[ -f secrets.env ]]; then
    . secrets.env
    echo -e "${GREEN}Secrets loaded from secrets.env${NC}"
else
    echo -e "${YELLOW}WARNING: secrets.env not found.${NC}"
    echo "  Copy secrets.env.example to secrets.env and fill in your credentials."
    echo ""
    # Generate minimum required secret
    if [[ -z "$POSTGRES_PASSWORD" ]]; then
        export POSTGRES_PASSWORD=$(python3 -c "import secrets; print(secrets.token_hex(16))" 2>/dev/null || openssl rand -hex 16)
        echo "POSTGRES_PASSWORD=$POSTGRES_PASSWORD" > secrets.env
        echo -e "${YELLOW}Generated POSTGRES_PASSWORD and wrote to secrets.env${NC}"
    fi
fi

# Generate Flask secret key if not set
if [[ -z "$NVR_SECRET_KEY" ]]; then
    export NVR_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || openssl rand -hex 32)
    echo "NVR_SECRET_KEY=$NVR_SECRET_KEY" >> secrets.env 2>/dev/null
    echo -e "${YELLOW}NVR_SECRET_KEY not found — generated and saved to secrets.env${NC}"
fi
set +a

# =============================================================================
# Create necessary directories
# =============================================================================
echo "Creating directories..."
mkdir -p logs streams config

# Ensure entrypoint.sh is executable
chmod +x entrypoint.sh 2>/dev/null || true

# Copy cameras.json from example if missing
if [[ ! -f config/cameras.json ]]; then
    if [[ -f config/cameras.json.example ]]; then
        echo -e "${YELLOW}No cameras.json found — copying from example template${NC}"
        cp config/cameras.json.example config/cameras.json
    else
        echo -e "${RED}ERROR: No cameras.json or cameras.json.example found in config/${NC}"
        exit 1
    fi
fi

# =============================================================================
# Stop existing container if running
# =============================================================================
if docker ps 2>/dev/null | grep -q unified-nvr; then
    echo ""
    echo "Stopping existing container..."
    docker compose down
fi

# =============================================================================
# Run config update scripts (if they exist)
# =============================================================================
[[ -f scripts/update_mediamtx_paths.sh && -f packager/mediamtx.yml ]] && \
    scripts/update_mediamtx_paths.sh >/dev/null 2>&1
[[ -f scripts/update_neolink_config.sh && -f config/neolink.toml ]] && \
    scripts/update_neolink_config.sh >/dev/null 2>&1
[[ -f scripts/update_go2rtc_config.sh && -f config/go2rtc.yaml ]] && \
    scripts/update_go2rtc_config.sh >/dev/null 2>&1
[[ -f scripts/update_recording_settings.sh && -f config/recording_settings.json ]] && \
    scripts/update_recording_settings.sh >/dev/null 2>&1
[[ -f ensure_recording_paths.sh ]] && \
    ./ensure_recording_paths.sh >/dev/null 2>&1

# =============================================================================
# TLS certificates
# =============================================================================
if [[ ! -f certs/dev/fullchain.pem ]] || [[ ! -f certs/dev/privkey.pem ]]; then
    echo ""
    echo "TLS certs missing — generating..."
    if [[ -f 0_MAINTENANCE_SCRIPTS/make_ca_signed_tls.sh ]]; then
        0_MAINTENANCE_SCRIPTS/make_ca_signed_tls.sh
        echo -e "${GREEN}TLS certs generated${NC}"
    else
        echo -e "${YELLOW}WARNING: TLS cert script not found. HTTPS will not work.${NC}"
        echo "  Create certs/dev/fullchain.pem and certs/dev/privkey.pem manually."
    fi
fi

# =============================================================================
# Docker network
# =============================================================================
_NETWORK_NAME="${NVR_NETWORK_NAME:-0_mobiusnvr_nvr-net}"
docker network inspect "$_NETWORK_NAME" >/dev/null 2>&1 || \
    docker network create "$_NETWORK_NAME"

# =============================================================================
# Start the container
# =============================================================================
echo ""
echo "Starting container..."
docker compose up -d

# Wait for container
echo ""
echo "Waiting for container to start..."
sleep 10

if docker ps | grep -q unified-nvr; then
    echo -e "${GREEN}Container is running!${NC}"

    # Run pending DB migrations
    echo ""
    echo "Running DB migrations..."
    _migration_dir="$SCRIPT_DIR/psql/migrations"
    _migration_ok=0
    _migration_fail=0
    for _mig in $(ls "$_migration_dir"/*.sql 2>/dev/null | sort); do
        _mig_name="$(basename "$_mig")"
        if docker exec -i nvr-postgres psql -U nvr_api -d nvr < "$_mig" >/dev/null 2>&1; then
            echo -e "  ${GREEN}OK${NC} $_mig_name"
            (( _migration_ok++ ))
        else
            echo -e "  ${RED}FAIL${NC} $_mig_name"
            (( _migration_fail++ ))
        fi
    done
    if [[ $_migration_fail -eq 0 ]]; then
        echo -e "${GREEN}Migrations complete ($_migration_ok files)${NC}"
    else
        echo -e "${YELLOW}$_migration_fail migration(s) failed — check: docker logs nvr-postgres${NC}"
    fi

    # Print access info
    echo ""
    echo "Access the NVR at:"
    echo "  - HTTPS: https://$(hostname -I | awk '{print $1}'):${NVR_EDGE_HTTPS_PORT:-8444}/"
    echo "  - HTTP:  http://$(hostname -I | awk '{print $1}'):5000"
    echo ""
    echo "Useful commands:"
    echo "  View logs:    docker compose logs -f"
    echo "  Rebuild:      ./_deploy.sh"
    echo "  Shell:        docker exec -it unified-nvr /bin/bash"
    echo ""
    echo "Checking health in 20 seconds..."
    sleep 20

    # Health checks
    if curl -s http://localhost:5000/api/status >/dev/null 2>&1; then
        echo -e "${GREEN}HTTP health check passed!${NC}"
    else
        echo -e "${YELLOW}HTTP health check failed - check logs${NC}"
    fi
    if curl -kI https://localhost:${NVR_EDGE_HTTPS_PORT:-8444}/api/health >/dev/null 2>&1; then
        echo -e "${GREEN}HTTPS health check passed!${NC}"
    else
        echo -e "${YELLOW}HTTPS health check failed - check logs${NC}"
    fi

    # Phone-home heartbeat (non-blocking, silent failure)
    if [[ -f scripts/phone_home.sh ]]; then
        . scripts/phone_home.sh
        nvr_phone_home &
    fi
else
    echo -e "${RED}Container failed to start${NC}"
    echo "Check logs with: docker compose logs"
    exit 1
fi
