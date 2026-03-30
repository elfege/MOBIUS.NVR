#!/bin/bash
# =============================================================================
# start.sh - Start Unified NVR container
#
# Idempotent startup script. Supports two credential modes:
#   1. AWS mode (default): Pull secrets from AWS Secrets Manager
#   2. ENV mode: Read from secrets.env file (set ENV_BASED_CONFIG=true in .env)
#
# For fresh installs without AWS, copy secrets.env.example to secrets.env,
# fill in your credentials, and set ENV_BASED_CONFIG=true in .env.
# =============================================================================

# No set -e — too strict for a script with many optional/fallible steps.
# Critical steps use run_step() which exits 1 on failure/timeout.

deactivate &>/dev/null || true

SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"
SCRIPT_R_PATH=$(realpath "${BASH_SOURCE[0]}")
SCRIPT_DIR="${SCRIPT_R_PATH%${SCRIPT_NAME}}"

cd "$SCRIPT_DIR" &>/dev/null || true

# =============================================================================
# Single-instance lock — kill previous instance if running, then take over
# =============================================================================
LOCKFILE="/tmp/nvr_start.lock"
PIDFILE="/tmp/nvr_start.pid"

if [[ -f "$PIDFILE" ]]; then
	_old_pid=$(cat "$PIDFILE" 2>/dev/null)
	if [[ -n "$_old_pid" ]] && kill -0 "$_old_pid" 2>/dev/null; then
		echo "Killing previous start.sh (PID $_old_pid)..."
		kill -TERM "$_old_pid" 2>/dev/null
		# Wait up to 5s for it to die
		for _i in {1..10}; do
			kill -0 "$_old_pid" 2>/dev/null || break
			sleep 0.5
		done
		# Force kill if still alive
		kill -9 "$_old_pid" 2>/dev/null || true
	fi
fi

echo $$ > "$PIDFILE"

# Status file — sidecar reads this to report to UI
STATUSFILE="/tmp/nvr_start_status.json"
echo '{"status":"running","step":"initializing","error":null}' > "$STATUSFILE"

# Clean up PID file on exit; write final status
_on_exit() {
	local exit_code=$?
	rm -f "$PIDFILE"
	if [[ $exit_code -ne 0 ]]; then
		echo "{\"status\":\"failed\",\"step\":\"${_CURRENT_STEP:-unknown}\",\"error\":\"exit code $exit_code\",\"ts\":\"$(date -Iseconds)\"}" > "$STATUSFILE"
		echo -e "${RED}start.sh FAILED at step '${_CURRENT_STEP:-unknown}' (exit $exit_code)${NC}"
	else
		echo "{\"status\":\"success\",\"step\":\"done\",\"error\":null,\"ts\":\"$(date -Iseconds)\"}" > "$STATUSFILE"
	fi
}
trap '_on_exit' EXIT

_CURRENT_STEP="init"

# Run a command with a timeout (default 30s). Exits 1 on timeout or failure.
# Usage: run_step "description" [timeout_seconds] command [args...]
run_step() {
	local desc="$1"; shift
	local timeout=30
	if [[ "$1" =~ ^[0-9]+$ ]]; then
		timeout="$1"; shift
	fi
	_CURRENT_STEP="$desc"
	echo "{\"status\":\"running\",\"step\":\"$desc\",\"error\":null}" > "$STATUSFILE"
	echo -e "${CYAN}[$desc]${NC}"
	if ! timeout "$timeout" "$@"; then
		echo -e "${RED}FAILED: $desc (timeout=${timeout}s)${NC}"
		exit 1
	fi
}

# =============================================================================
# Portable color/utility setup — works with or without ~/.env.colors
# =============================================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# Source color overrides if available
[[ -f ~/.env.colors ]] && . ~/.env.colors

# Source logger (spinner functions) if available, otherwise provide no-ops
if [[ -f ~/logger.sh ]]; then
	. ~/logger.sh --no-exec &>/dev/null
else
	start_spinner() { echo -e "  $2"; }
	stop_spinner() { :; }
fi

# Source bash_utils if available (optional — for spinner functions)
if [[ -f ~/.bash_utils ]]; then
	. ~/.bash_utils &>/dev/null
fi



# =============================================================================
# Verify we're running from the project root
# =============================================================================
if [[ ! -f docker-compose.yml ]] || [[ ! -f Dockerfile ]]; then
	echo -e "${RED}ERROR: start.sh must be run from the NVR project root${NC}"
	echo "Expected: ~/0_MOBIUS.NVR/"
	echo "Current:  $(pwd)"
	exit 1
fi


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

# =============================================================================
# - Camera credentials are now stored in the DB and added via the UI, but this supports legacy workflows.
# =============================================================================
if declare -f get_cameras_credentials >/dev/null 2>&1; then
	get_cameras_credentials --temp=/tmp/nvr.credentials &>/dev/null || {
		echo -e "${YELLOW}WARNING: Failed to load camera credentials from secrets.env${NC}"
		echo "  Ensure secrets.env is properly formatted or switch to DB-based credentials via the UI."
		exit 1
	}
	. /tmp/nvr.credentials &>/dev/null || {
		echo -e "${YELLOW}WARNING: Failed to source camera credentials from secrets.env${NC}"
		echo "  Ensure secrets.env is properly formatted or switch to DB-based credentials via the UI."
		exit 1
	}
fi

# Detect host IP early (needed for LAN-cache decision + container env).
# Only export if detection succeeds — if it fails, docker compose falls back to .env value.
_detected_ip=$(ip route get 1.1.1.1 2>/dev/null | awk '{print $7}' | head -1)
if [[ -n "$_detected_ip" ]]; then
    export NVR_LOCAL_HOST_IP="$_detected_ip"
else
    echo -e "${YELLOW}WARNING: Could not auto-detect host IP via ip route. Using .env value: ${NVR_LOCAL_HOST_IP:-unset}${NC}"
fi

echo "=========================================="
echo "  Unified NVR - Container Startup"
echo "=========================================="
echo ""

# =============================================================================
# Create necessary directories
# =============================================================================
echo "Creating directories..."
mkdir -p logs streams config

# Host IP for go2rtc WebRTC ICE candidates — browser needs this to reach media UDP port
export NVR_HOST_IP=$(hostname -I | awk '{print $1}')
echo -e "${GREEN}✓${NC} NVR_HOST_IP=${NVR_HOST_IP}"

# Ensure entrypoint.sh is executable (bind mount overrides Docker build chmod)
chmod +x entrypoint.sh 2>/dev/null || true

# Database is the sole source of truth for camera configuration.
# cameras.json is NOT used at runtime. Add cameras via the UI.

# =============================================================================
# Stop existing container if running
# =============================================================================
# No sidecar complexity — admin console is a host systemd service.
# Simple docker compose down works cleanly now.
if docker ps 2>/dev/null | grep -q unified-nvr; then
	echo ""
	echo "Stopping existing containers..."
	docker compose down --remove-orphans
fi

# =============================================================================
# Secrets: No secrets.env needed.
# - POSTGRES_PASSWORD: hardcoded in docker-compose.yml (internal Docker network)
# - NVR_SECRET_KEY: auto-generated by app.py, stored in nvr_settings DB table
# - Camera credentials: stored in camera_credentials DB table (added via UI)
# =============================================================================
set +a

# =============================================================================
# Run config update scripts (if they exist)
# Several scripts query nvr-postgres directly. Start it early and wait for
# readiness before running any DB-dependent config scripts.
# =============================================================================
_needs_postgres=false
[[ -f scripts/update_mediamtx_paths.sh && -f packager/mediamtx.yml ]]       && _needs_postgres=true
[[ -f scripts/generate_go2rtc_config.py && -f config/go2rtc.yaml ]]          && _needs_postgres=true
[[ -f scripts/update_recording_settings.sh && -f config/recording_settings.json ]] && _needs_postgres=true

if [[ "$_needs_postgres" == "true" ]]; then
	echo ""
	echo -e "${CYAN}Starting nvr-postgres early (required for config scripts)...${NC}"
	docker compose up -d postgres
	_pg_wait=0
	_pg_timeout=60
	until docker exec nvr-postgres pg_isready -U nvr_api -d nvr -q 2>/dev/null; do
		sleep 1
		(( _pg_wait++ ))
		if [[ $_pg_wait -ge $_pg_timeout ]]; then
			echo -e "${RED}ERROR: nvr-postgres did not become ready after ${_pg_timeout}s${NC}"
			echo "  Check: docker logs nvr-postgres"
			echo "  Container state: $(docker inspect --format='{{.State.Status}}' nvr-postgres 2>/dev/null || echo 'not found')"
			exit 1
		fi
	done
	echo -e "${GREEN}✓${NC} nvr-postgres ready"
fi

if [[ -f scripts/seed_credentials.py ]]; then
	start_spinner 20 "$CYAN Seeding service credentials from env → DB"
	venv/bin/python3 scripts/seed_credentials.py
	stop_spinner
fi

# Generate streaming configs for all three hubs (exclusive assignment).
# Each camera appears in exactly ONE hub's config based on DB streaming_hub field.
echo -e "${CYAN}Generating streaming configs (MediaMTX + go2rtc + neolink)...${NC}"
if [[ -f scripts/generate_streaming_configs.py ]]; then
	venv/bin/python3 scripts/generate_streaming_configs.py
fi
echo -e "${GREEN}✓${NC} Streaming configs generated"

if [[ -f scripts/update_recording_settings.sh && -f config/recording_settings.json ]]; then
	start_spinner 20 "$CYAN Syncing recording_settings.json with cameras"
	scripts/update_recording_settings.sh >/dev/null
	stop_spinner
fi

# Ensure recording paths have proper permissions
if [[ -f ensure_recording_paths.sh ]]; then
	echo ""
	echo "Ensuring recording path permissions..."
	./ensure_recording_paths.sh >/dev/null
fi

# =============================================================================
# TLS certificates
# =============================================================================
if [[ ! -f certs/dev/fullchain.pem ]] || [[ ! -f certs/dev/privkey.pem ]]; then
	echo ""
	echo "TLS certs missing — generating CA-signed certs..."
	if [[ -f 0_MAINTENANCE_SCRIPTS/make_ca_signed_tls.sh ]]; then
		0_MAINTENANCE_SCRIPTS/make_ca_signed_tls.sh
		echo -e "${GREEN}TLS certs generated${NC}"
	else
		echo -e "${YELLOW}WARNING: TLS cert script not found. HTTPS will not work.${NC}"
		echo "  Create certs/dev/fullchain.pem and certs/dev/privkey.pem manually."
	fi
fi

# =============================================================================
# Admin Console — host systemd service for restart-from-UI
# =============================================================================
_ADMIN_PORT="${NVR_ADMIN_PORT:-9100}"
_ADMIN_UNIT="nvr-admin-console.service"
_ADMIN_UNIT_PATH="/etc/systemd/system/${_ADMIN_UNIT}"
_ADMIN_SERVER="${SCRIPT_DIR}restart-sidecar/server.py"

# Install/update admin console in background — don't block container startup
(
	if [[ -f "$_ADMIN_SERVER" ]]; then
		# Write/update the systemd unit file
		sudo tee "$_ADMIN_UNIT_PATH" > /dev/null <<-UNIT
		[Unit]
		Description=NVR Admin Console (restart service)
		After=network.target docker.service

		[Service]
		Type=simple
		Environment=NVR_PROJECT_DIR=${SCRIPT_DIR%/}
		Environment=NVR_ADMIN_PORT=${_ADMIN_PORT}
		ExecStart=/usr/bin/python3 ${_ADMIN_SERVER}
		Restart=always
		RestartSec=3
		User=$(whoami)

		[Install]
		WantedBy=multi-user.target
		UNIT

		sudo systemctl daemon-reload
		sudo systemctl enable "$_ADMIN_UNIT" 2>/dev/null
		sudo systemctl restart "$_ADMIN_UNIT"

		if systemctl is-active --quiet "$_ADMIN_UNIT"; then
			echo -e "${GREEN}✓${NC} Admin console running on port ${_ADMIN_PORT}"
		else
			echo -e "${RED}ERROR: ${_ADMIN_UNIT} failed to start${NC}"
			echo "  Check: journalctl -u ${_ADMIN_UNIT} --no-pager -n 20"
		fi
	else
		echo -e "${YELLOW}WARNING: Admin console server.py not found — restart button won't work${NC}"
	fi
) &

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
_CURRENT_STEP="docker compose up"
echo "Starting containers..."
run_step "docker compose up" 120 docker compose up -d

# Wait for container to start
echo ""
echo "Waiting for container to start..."
sleep 10

# Check container status
if docker ps | grep -q unified-nvr; then
	echo -e "${GREEN}Container is running!${NC}"

	# Run pending DB migrations
	echo ""
	echo "Running DB migrations..."
	_migration_dir="$(dirname "$SCRIPT_R_PATH")/psql/migrations"
	_migration_ok=0
	_migration_fail=0
	for _mig in $(ls "$_migration_dir"/*.sql 2>/dev/null | sort); do
		_mig_name="$(basename "$_mig")"
		if docker exec -i nvr-postgres psql -U nvr_api -d nvr < "$_mig" >/dev/null 2>&1; then
			echo -e "  ${GREEN}OK${NC} $_mig_name"
			(( _migration_ok++ ))
		else
			echo -e "  ${RED}FAIL${NC} $_mig_name — check: docker logs nvr-postgres"
			(( _migration_fail++ ))
		fi
	done
	if [[ $_migration_fail -eq 0 ]]; then
		echo -e "${GREEN}Migrations complete ($_migration_ok files)${NC}"
	else
		echo -e "${YELLOW}$_migration_fail migration(s) failed — DB may be incomplete${NC}"
	fi

	# Print access info
	echo ""
	echo "Access the NVR at:"
	echo "  - HTTPS: https://$(hostname -I | awk '{print $1}'):${NVR_EDGE_HTTPS_PORT:-8444}/"
	echo "  - HTTP:  http://$(hostname -I | awk '{print $1}'):5000"
	echo ""
	echo "Useful commands:"
	echo "  View logs:    docker compose logs -f"
	echo "  Stop:         ./stop.sh"
	echo "  Rebuild:      ./deploy.sh"
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

	# Truncate secrets.env — credentials are loaded in the container's
	# environment and migrated to DB. No plaintext secrets on disk.
	> secrets.env
	echo -e "${GREEN}secrets.env truncated — no plaintext secrets on disk${NC}"

else
	_CURRENT_STEP="container startup"
	echo -e "${RED}Container failed to start${NC}"
	echo "Check logs with: docker compose logs"
	exit 1
fi

# Mark successful completion
_CURRENT_STEP="done"
