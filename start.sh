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

# ── Wait for internet / AWS connectivity (post-power-loss guard) ─────────────
_AWS_WAIT_URL="https://sts.amazonaws.com"
_LOG_FILE="${LOG_FILE:-$HOME/0_LOGS/log.log}"
mkdir -p "$(dirname "$_LOG_FILE")"
if ! curl -sf --max-time 5 "$_AWS_WAIT_URL" -o /dev/null 2>&1; then
    _msg="[$(date '+%H:%M:%S')] Waiting for internet/AWS (${_AWS_WAIT_URL}) — logging every 5s to: $_LOG_FILE"
    echo -e "${FLASH_ACCENT_YELLOW:-\033[5;33m}${_msg}${NC:-\033[0m}"
    echo "$_msg" >> "$_LOG_FILE"
    until curl -sf --max-time 5 "$_AWS_WAIT_URL" -o /dev/null 2>&1; do
        _msg="[$(date '+%H:%M:%S')] Still waiting for internet/AWS — retrying in 5s"
        echo -e "${FLASH_ACCENT_YELLOW:-\033[5;33m}${_msg}${NC:-\033[0m}"
        echo "$_msg" >> "$_LOG_FILE"
        sleep 5
    done
fi
echo -e "${GREEN:-\033[0;32m}[$(date '+%H:%M:%S')] Internet/AWS connectivity confirmed — proceeding${NC:-\033[0m}"
echo "[$(date '+%H:%M:%S')] Internet/AWS connectivity confirmed" >> "$_LOG_FILE"
# ─────────────────────────────────────────────────────────────────────────────

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
. ~/0_MOBIUS.NVR/.env

# Detect host IP early (needed for LAN-cache decision + container env)
export NVR_LOCAL_HOST_IP=$(ip route get 1.1.1.1 | awk '{print $7}' | head -1)

# ── Parallel secrets pull with LAN cache ──────────────────────────────────────
# On the home LAN (192.168.10.0/24) AWS secrets are cached to disk so
# subsequent start.sh runs skip the ~13s of serial AWS round-trips entirely.
# Off-LAN the cache is not persisted — secrets are always pulled fresh.
#
# Parallelization: list-secrets (1 call, serial) then N get-secret-value calls
# fired simultaneously.  Each subshell writes KEY=VALUE lines to a temp file;
# the parent concatenates them into the cache and sources it under set -a.
# ──────────────────────────────────────────────────────────────────────────────
_NVR_SECRETS_CACHE="$HOME/.cache/nvr_secrets.env"
_on_lan=false
[[ "$NVR_LOCAL_HOST_IP" == 192.168.10.* ]] && _on_lan=true
_need_pull=true

# Try LAN cache first
if $_on_lan && [[ -f "$_NVR_SECRETS_CACHE" ]]; then
	. "$_NVR_SECRETS_CACHE"
	if [[ -n "$POSTGRES_PASSWORD" ]]; then
		echo -e "${GREEN}✓ Secrets loaded from LAN cache${NC}"
		_need_pull=false
	else
		echo -e "${YELLOW}⚠️  Cached secrets invalid — re-pulling from AWS${NC}"
		rm -f "$_NVR_SECRETS_CACHE"
	fi
fi

if $_need_pull; then
	_sec_start=$(date +%s)
	mkdir -p "$(dirname "$_NVR_SECRETS_CACHE")"
	export AWS_PROFILE=personal

	# Authenticate (serial — cached SSO check is fast)
	if ! aws_auth 2>/dev/null; then
		echo -e "${RED}✗ AWS authentication failed${NC}"
		exit 1
	fi

	# List all secret names (1 API call)
	_list_output=$(aws secretsmanager list-secrets \
		--query 'SecretList[].Name' --output text 2>&1)
	if [[ $? -ne 0 ]]; then
		echo -e "${RED}✗ Failed to list secrets: $_list_output${NC}"
		exit 1
	fi
	readarray -t _secret_names < <(echo "$_list_output" | tr '\t' '\n')
	echo "Fetching ${#_secret_names[@]} secrets in parallel..."

	# Parallel fetch: each secret → temp file with KEY='value' lines
	# Values are jq @sh-escaped so sourcing is safe for any content.
	_tmpdir=$(mktemp -d)
	for _sname in "${_secret_names[@]}"; do
		(
			aws secretsmanager get-secret-value \
				--secret-id "$_sname" \
				--query SecretString \
				--output text 2>/dev/null \
			| jq -r 'to_entries | .[] | "\(.key)=\(.value | @sh)"' \
			> "$_tmpdir/${_sname}.env" 2>/dev/null
		) &
	done
	wait

	# Combine into single env file, source under set -a (auto-export)
	cat "$_tmpdir"/*.env > "$_NVR_SECRETS_CACHE" 2>/dev/null
	rm -rf "$_tmpdir"
	. "$_NVR_SECRETS_CACHE"

	_sec_end=$(date +%s)
	echo -e "${GREEN}✓ All secrets loaded in $((_sec_end - _sec_start))s (${#_secret_names[@]} secrets, parallel)${NC}"

	# Off-LAN: don't persist the cache
	if ! $_on_lan; then
		rm -f "$_NVR_SECRETS_CACHE"
	fi

	# Verify critical secrets
	if [[ -z "$POSTGRES_PASSWORD" ]]; then
		echo -e "${RED}✗ POSTGRES_PASSWORD missing after pull — check AWS secrets${NC}"
		exit 1
	fi
fi
# ──────────────────────────────────────────────────────────────────────────────
set +a

if [[ -f ~/0_MOBIUS.NVR/scripts/update_mediamtx_paths.sh && -f ~/0_MOBIUS.NVR/packager/mediamtx.yml ]]; then
	start_spinner 20 "$CYAN Appending packager/mediamtx.yml"
	~/0_MOBIUS.NVR/scripts/update_mediamtx_paths.sh >/dev/null
	stop_spinner
fi

if [[ -f ~/0_MOBIUS.NVR/scripts/update_neolink_config.sh && -f ~/0_MOBIUS.NVR/config/neolink.toml ]]; then
	start_spinner 20 "$CYAN Updating config/neolink.toml"
	~/0_MOBIUS.NVR/scripts/update_neolink_config.sh >/dev/null
	stop_spinner
fi

if [[ -f ~/0_MOBIUS.NVR/scripts/update_recording_settings.sh && -f ~/0_MOBIUS.NVR/config/recording_settings.json ]]; then
	start_spinner 20 "$CYAN Syncing recording_settings.json with cameras"
	~/0_MOBIUS.NVR/scripts/update_recording_settings.sh >/dev/null
	stop_spinner
fi

# Ensure recording paths have proper permissions (UID 1000 for container appuser)
if [[ -f ~/0_MOBIUS.NVR/ensure_recording_paths.sh ]]; then
	echo ""
	echo "Ensuring recording path permissions..."
	~/0_MOBIUS.NVR/ensure_recording_paths.sh >/dev/null 
fi

# Ensure TLS certs exist (MediaMTX + nginx need them)
# Uses CA-based certs so users can install the CA and avoid browser warnings.
# The CA persists across regenerations; only the server cert is recreated if missing.
if [ ! -f certs/dev/fullchain.pem ] || [ ! -f certs/dev/privkey.pem ]; then
	echo ""
	echo "TLS certs missing — generating CA-signed certs..."
	~/0_MOBIUS.NVR/0_MAINTENANCE_SCRIPTS/make_ca_signed_tls.sh
	echo -e "${GREEN}✓ TLS certs generated${NC}"
fi

# Ensure the external network exists (shared with proxy and other MOBIUS stacks)
docker network inspect 0_mobiusnvr_nvr-net >/dev/null 2>&1 || \
    docker network create 0_mobiusnvr_nvr-net

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

	# ── Run pending DB migrations ─────────────────────────────────────────────
	# All migration files use IF NOT EXISTS guards — safe to run on every start.
	# New columns/tables are applied automatically; existing ones are skipped.
	echo ""
	echo "Running DB migrations..."
	_migration_dir="$(dirname "$SCRIPT_R_PATH")/psql/migrations"
	_migration_ok=0
	_migration_fail=0
	for _mig in $(ls "$_migration_dir"/*.sql 2>/dev/null | sort); do
		_mig_name="$(basename "$_mig")"
		if docker exec -i nvr-postgres psql -U nvr_api -d nvr < "$_mig" >/dev/null 2>&1; then
			echo -e "  ${GREEN}✓${NC} $_mig_name"
			(( _migration_ok++ ))
		else
			echo -e "  ${RED}✗${NC} $_mig_name — check logs: docker logs nvr-postgres"
			(( _migration_fail++ ))
		fi
	done
	if [[ $_migration_fail -eq 0 ]]; then
		echo -e "${GREEN}✓ Migrations complete ($_migration_ok files)${NC}"
	else
		echo -e "${YELLOW}⚠️  $_migration_fail migration(s) failed — DB may be incomplete${NC}"
	fi
	# ─────────────────────────────────────────────────────────────────────────

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
	if curl -kI https://localhost:8444/api/health >/dev/null 2>&1; then
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
