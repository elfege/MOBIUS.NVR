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

# set -e

deactivate &>/dev/null || true

SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"
SCRIPT_R_PATH=$(realpath "${BASH_SOURCE[0]}")
SCRIPT_DIR="${SCRIPT_R_PATH%${SCRIPT_NAME}}"

cd "$SCRIPT_DIR" &>/dev/null || true

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

# Source bash_utils if available (needed for AWS mode only)
_HAS_BASH_UTILS=false
if [[ -f ~/.bash_utils ]]; then
	. ~/.bash_utils &>/dev/null && _HAS_BASH_UTILS=true
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

# Detect host IP early (needed for LAN-cache decision + container env)
export NVR_LOCAL_HOST_IP=$(ip route get 1.1.1.1 2>/dev/null | awk '{print $7}' | head -1)

# =============================================================================
# Determine credential mode
# =============================================================================
# ENV_BASED_CONFIG=true  -> read from secrets.env, skip AWS entirely
# ENV_BASED_CONFIG=false -> pull from AWS Secrets Manager (requires bash_utils)
_ENV_MODE=false
if [[ "${ENV_BASED_CONFIG:-false}" == "true" ]]; then
	_ENV_MODE=true
fi

echo "=========================================="
echo "  Unified NVR - Container Startup"
echo "=========================================="
echo ""
if $_ENV_MODE; then
	echo -e "  Credential mode: ${CYAN}ENV file${NC} (secrets.env)"
else
	echo -e "  Credential mode: ${CYAN}AWS Secrets Manager${NC}"
fi
echo ""

# =============================================================================
# Create necessary directories
# =============================================================================
echo "Creating directories..."
mkdir -p logs streams config

# Ensure entrypoint.sh is executable (bind mount overrides Docker build chmod)
chmod +x entrypoint.sh 2>/dev/null || true

# Check if config/cameras.json exists
if [[ ! -f config/cameras.json ]]; then
	if [[ -f config/cameras.json.example ]]; then
		echo -e "${YELLOW}No cameras.json found — copying from example template${NC}"
		cp config/cameras.json.example config/cameras.json
		echo -e "${YELLOW}Edit config/cameras.json with your camera details before adding cameras${NC}"
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
# Load secrets
# =============================================================================
if $_ENV_MODE; then
	# ── ENV-based mode: load from secrets.env ──────────────────────────────
	if [[ -f secrets.env ]]; then
		# Safe loader: handles passwords with special chars ()#!$ etc.
		# Uses cut to split on first '=' only (values can contain '=')
		while IFS= read -r _line; do
			# Skip comments and blank lines
			[[ -z "$_line" || "$_line" =~ ^[[:space:]]*# ]] && continue
			_key="${_line%%=*}"
			_val="${_line#*=}"
			# Remove surrounding quotes if present
			_val="${_val#\'}" && _val="${_val%\'}"
			_val="${_val#\"}" && _val="${_val%\"}"
			export "$_key=$_val"
		done < secrets.env
		echo -e "${GREEN}Secrets loaded from secrets.env${NC}"
	else
		echo -e "${YELLOW}WARNING: secrets.env not found.${NC}"
		echo "  Copy secrets.env.example to secrets.env and fill in your credentials."
		echo "  Camera credentials can be added later via the web UI."
		echo ""
		# Generate minimum required secrets
		if [[ -z "$POSTGRES_PASSWORD" ]]; then
			export POSTGRES_PASSWORD=$(python3 -c "import secrets; print(secrets.token_hex(16))")
			echo "POSTGRES_PASSWORD=$POSTGRES_PASSWORD" > secrets.env
			echo -e "${YELLOW}Generated POSTGRES_PASSWORD and wrote to secrets.env${NC}"
		fi
	fi
else
	# ── AWS mode: pull from Secrets Manager ────────────────────────────────
	if ! $_HAS_BASH_UTILS; then
		echo -e "${RED}ERROR: AWS mode requires ~/.bash_utils (not found)${NC}"
		echo ""
		echo "Options:"
		echo "  1. Install bash_utils (see project README)"
		echo "  2. Set ENV_BASED_CONFIG=true in .env and use secrets.env instead"
		exit 1
	fi

	# Wait for internet / AWS connectivity (post-power-loss guard)
	_AWS_WAIT_URL="https://sts.amazonaws.com"
	_LOG_FILE="${LOG_FILE:-$HOME/0_LOGS/log.log}"
	mkdir -p "$(dirname "$_LOG_FILE")"
	if ! curl -sf --max-time 5 "$_AWS_WAIT_URL" -o /dev/null 2>&1; then
		_msg="[$(date '+%H:%M:%S')] Waiting for internet/AWS (${_AWS_WAIT_URL})"
		echo -e "${YELLOW}${_msg}${NC}"
		echo "$_msg" >> "$_LOG_FILE"
		until curl -sf --max-time 5 "$_AWS_WAIT_URL" -o /dev/null 2>&1; do
			echo -e "${YELLOW}[$(date '+%H:%M:%S')] Still waiting for internet/AWS — retrying in 5s${NC}"
			sleep 5
		done
	fi
	echo -e "${GREEN}[$(date '+%H:%M:%S')] Internet/AWS connectivity confirmed${NC}"

	# Parallel secrets pull with LAN cache
	_NVR_SECRETS_CACHE="$HOME/.cache/nvr_secrets.env"
	_on_lan=false
	[[ "$NVR_LOCAL_HOST_IP" == 192.168.10.* ]] && _on_lan=true
	_need_pull=true

	# Try LAN cache first
	if $_on_lan && [[ -f "$_NVR_SECRETS_CACHE" ]]; then
		# Safe loader: handles passwords with special chars ()#!$
		while IFS= read -r _line; do
			[[ -z "$_line" || "$_line" =~ ^[[:space:]]*# ]] && continue
			_key="${_line%%=*}"; _val="${_line#*=}"
			_val="${_val#\'}" && _val="${_val%\'}"
			_val="${_val#\"}" && _val="${_val%\"}"
			export "$_key=$_val"
		done < "$_NVR_SECRETS_CACHE"
		if [[ -n "$POSTGRES_PASSWORD" ]]; then
			echo -e "${GREEN}Secrets loaded from LAN cache${NC}"
			_need_pull=false
		else
			echo -e "${YELLOW}Cached secrets invalid — re-pulling from AWS${NC}"
			rm -f "$_NVR_SECRETS_CACHE"
		fi
	fi

	if $_need_pull; then
		_sec_start=$(date +%s)
		mkdir -p "$(dirname "$_NVR_SECRETS_CACHE")"
		# Truncate stale cache to prevent cross-project contamination
		> "$_NVR_SECRETS_CACHE"
		export AWS_PROFILE=personal

		# Authenticate
		if ! aws_auth 2>/dev/null; then
			echo -e "${RED}AWS authentication failed${NC}"
			exit 1
		fi

		# Fetch only NVR-relevant secrets (not all secrets in the account)
		_secret_names=(
			"NVR-Secrets"
			"REOLINK_CAMERAS"
			"Unifi-Camera-Credentials"
			"EUFY_CAMERAS"
			"AMCREST_CAMERAS"
			"SV3C_CAMERAS"
		)
		echo "Fetching ${#_secret_names[@]} NVR secrets in parallel..."

		# Parallel fetch
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

		# Combine into cache
		cat "$_tmpdir"/*.env > "$_NVR_SECRETS_CACHE" 2>/dev/null
		rm -rf "$_tmpdir"
		# Safe loader: handles passwords with special chars ()#!$ etc.
		while IFS= read -r _line; do
			[[ -z "$_line" || "$_line" =~ ^[[:space:]]*# ]] && continue
			_key="${_line%%=*}"
			_val="${_line#*=}"
			_val="${_val#\'}" && _val="${_val%\'}"
			_val="${_val#\"}" && _val="${_val%\"}"
			export "$_key=$_val"
		done < "$_NVR_SECRETS_CACHE"

		_sec_end=$(date +%s)
		echo -e "${GREEN}All secrets loaded in $((_sec_end - _sec_start))s (${#_secret_names[@]} secrets, parallel)${NC}"

		# Off-LAN: don't persist the cache
		if ! $_on_lan; then
			rm -f "$_NVR_SECRETS_CACHE"
		fi

		# Verify critical secrets
		if [[ -z "$POSTGRES_PASSWORD" ]]; then
			echo -e "${RED}POSTGRES_PASSWORD missing after pull — check AWS secrets${NC}"
			exit 1
		fi
	fi

	# secrets.env is passed through to the container via env_file for
	# credential migration (env vars → DB). Not truncated here so the
	# migration can read camera credentials on first run.
fi

# No secrets.env needed — POSTGRES_PASSWORD is hardcoded in docker-compose.yml
# (internal Docker network only), NVR_SECRET_KEY is auto-generated by app.py
# and stored in the database (nvr_settings table).

set +a

# =============================================================================
# Run config update scripts (if they exist)
# =============================================================================
if [[ -f scripts/update_mediamtx_paths.sh && -f packager/mediamtx.yml ]]; then
	start_spinner 20 "$CYAN Appending packager/mediamtx.yml"
	scripts/update_mediamtx_paths.sh >/dev/null
	stop_spinner
fi

if [[ -f scripts/update_neolink_config.sh && -f config/neolink.toml ]]; then
	start_spinner 20 "$CYAN Updating config/neolink.toml"
	scripts/update_neolink_config.sh >/dev/null
	stop_spinner
fi

if [[ -f scripts/update_go2rtc_config.sh && -f config/go2rtc.yaml ]]; then
	start_spinner 20 "$CYAN Updating config/go2rtc.yaml video relay streams"
	scripts/update_go2rtc_config.sh >/dev/null
	stop_spinner
fi

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
	echo -e "${RED}Container failed to start${NC}"
	echo "Check logs with: docker compose logs"
	exit 1
fi
