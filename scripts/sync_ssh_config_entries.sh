#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# sync_ssh_config_entries.sh — Phase 2c v2 (operator request 2026-05-13).
#
# Purpose
#   The container's /api/host-agent/install-via-ssh endpoint records a
#   minimal `Host <label>` stanza in
#     ${NVR_SSH_CONFIG_ENTRIES_PATH:-./data/ssh_config_entries}/<label>.conf
#   every time the operator successfully installs the host-agent on a
#   target via SSH. This script reads that directory on the host side
#   and (in v2) DRY-RUNS the merge into the operator's ~/.ssh/config.
#
# Why dry-run only
#   The operator (Elfege, per the 2026-05-13 addendum) wants to vet the
#   behaviour for at least one full install cycle before allowing a
#   process to touch ~/.ssh/config. Once vetted, flip the
#   "# FUTURE: enable real merge after vetting" block below AND the
#   matching block in start.sh.
#
# Idempotency contract (for the future real-merge mode)
#   - NEVER overwrite an existing `Host <alias>` stanza already present
#     in ~/.ssh/config. Skip by alias.
#   - Only ADD missing entries.
#   - Wrap any block we add in a header comment so the operator can
#     audit / remove it later:
#         # ---- mobius-nvr ssh_config_entries sync (auto-added on <date>) ----
#
# Usage
#   $ ./scripts/sync_ssh_config_entries.sh           # dry-run (default)
#   $ ./scripts/sync_ssh_config_entries.sh --commit  # ENABLE real merge
#       (still a no-op today — gated on the FUTURE block below)
#
# Exit codes
#   0  — success (including "nothing to do" / "dry-run printed")
#   1  — mirror directory missing or unreadable
#   2  — invalid invocation
# ---------------------------------------------------------------------------
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

# Mirror dir path resolution: prefer the env var if explicitly set (matches
# the docker-compose NVR_SSH_CONFIG_ENTRIES_PATH override semantic);
# otherwise default to the repo-relative path.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MIRROR_DIR="${NVR_SSH_CONFIG_ENTRIES_PATH:-$REPO_ROOT/data/ssh_config_entries}"
SSH_CONFIG="${HOME}/.ssh/config"

mode='dry-run'
if [[ $# -ge 1 ]]; then
    case "$1" in
        --commit) mode='commit' ;;
        --dry-run) mode='dry-run' ;;
        -h|--help)
            sed -n '2,40p' "$0"
            exit 0
            ;;
        *)
            echo -e "${RED}unknown argument: $1${NC}" >&2
            exit 2
            ;;
    esac
fi

if [[ ! -d "$MIRROR_DIR" ]]; then
    echo -e "${YELLOW}mirror directory not present:${NC} $MIRROR_DIR"
    echo "  (no SSH installs have been performed yet, or the bind mount is"
    echo "   not configured — see docker-compose.yml)"
    exit 0
fi

shopt -s nullglob
entries=("$MIRROR_DIR"/*.conf)
if [[ ${#entries[@]} -eq 0 ]]; then
    echo -e "${CYAN}sync_ssh_config_entries:${NC} no entries in $MIRROR_DIR — nothing to do."
    exit 0
fi

echo -e "${CYAN}sync_ssh_config_entries:${NC} found ${#entries[@]} stanza file(s) in $MIRROR_DIR"
echo "  ssh_config target: $SSH_CONFIG"
echo "  mode: $mode"
echo

# Track which entries are new vs. already present.
to_add=()
already_present=()
malformed=()

for f in "${entries[@]}"; do
    # Each file should declare exactly one `Host <label>` line. Extract it.
    host_alias="$(awk '/^Host[[:space:]]+/ {print $2; exit}' "$f" || true)"
    if [[ -z "$host_alias" ]]; then
        malformed+=("$f")
        continue
    fi

    if [[ -f "$SSH_CONFIG" ]] && grep -Eq "^Host[[:space:]]+(${host_alias}([[:space:]]|$))" "$SSH_CONFIG"; then
        already_present+=("$host_alias")
    else
        to_add+=("$f")
    fi
done

if [[ ${#malformed[@]} -gt 0 ]]; then
    echo -e "${YELLOW}skipped (no 'Host <alias>' line):${NC}"
    for f in "${malformed[@]}"; do echo "  - $f"; done
    echo
fi

if [[ ${#already_present[@]} -gt 0 ]]; then
    echo -e "${GREEN}already present in $SSH_CONFIG (skipped):${NC}"
    for a in "${already_present[@]}"; do echo "  - Host $a"; done
    echo
fi

if [[ ${#to_add[@]} -eq 0 ]]; then
    echo -e "${GREEN}nothing new to merge — all mirror entries already exist in $SSH_CONFIG.${NC}"
    exit 0
fi

echo -e "${CYAN}would-add (dry-run preview):${NC}"
echo "# ---- mobius-nvr ssh_config_entries sync (auto-added on $(date '+%Y-%m-%d %H:%M %Z')) ----"
for f in "${to_add[@]}"; do
    echo
    cat "$f"
done
echo
echo "# ---- end mobius-nvr ssh_config_entries sync ----"
echo

# ---------------------------------------------------------------------------
# FUTURE: enable real merge after vetting
# ---------------------------------------------------------------------------
# Once Elfege has reviewed at least one full install-cycle's dry-run output
# and confirmed it never proposes anything destructive, REMOVE the `false`
# guard below. Until then, --commit is intentionally a no-op so this script
# can be invoked unconditionally from start.sh without surprising side
# effects.
#
# When enabling, also bump the FUTURE block in start.sh and consider:
#   - backing up ~/.ssh/config to ~/.ssh/config.bak.YYYYMMDD-HHMMSS before
#     the first real write,
#   - asking the operator y/n on each new alias (interactive flow), and
#   - confirming the file is writable (mode 0600 typically — preserve it).
# ---------------------------------------------------------------------------
if [[ "$mode" == 'commit' ]] && false; then
    # Intentionally unreachable — see comment block above.
    : 'real merge implementation goes here'
fi

if [[ "$mode" == 'commit' ]]; then
    echo -e "${YELLOW}--commit specified, but real merge is GATED OFF pending operator vetting.${NC}"
    echo "  Edit scripts/sync_ssh_config_entries.sh to remove the 'false' guard."
    echo "  Nothing was written to $SSH_CONFIG."
fi

exit 0
