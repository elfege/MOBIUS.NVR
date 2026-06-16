#!/usr/bin/env bash
# =============================================================================
# status.sh — show the state of the MOBIUS.NVR docker stack(s).
#
# Default (no args): the prod stack (containers WITHOUT the nvr_test_ prefix).
# --test           : the e2e test stack (containers WITH the nvr_test_ prefix).
# --all            : both stacks side-by-side.
#
# Wired to the operator's `status` shell alias (~/.bash_aliases):
#   alias status="/bin/clear && \$(pwd)/status.sh"
#
# Also called by test.sh's "Stack status" menu option (with --test).
# =============================================================================

set -uo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ANSI helpers — only colour when stdout is a TTY.
if [[ -t 1 ]]; then
    BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'
    YELLOW=$'\033[33m'; CYAN=$'\033[36m'; NC=$'\033[0m'
else
    BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; CYAN=""; NC=""
fi

usage() {
    cat <<EOF
Usage: $0 [--prod|--test|--all]
  (no args)   Same as --prod.
  --prod      Show the prod stack only.
  --test      Show the e2e test stack only (nvr_test_* containers).
  --all       Show both stacks.
EOF
}

# Render one named "section" — a filtered docker ps for the given name pattern.
# Args: $1 = section title, $2 = docker name filter (passed to --filter name=)
render_section() {
    local title="$1"
    local filter="$2"
    local ids
    ids="$(docker ps -a --filter "name=$filter" --format '{{.ID}}' 2>/dev/null)"
    local container_count
    container_count=$(printf '%s\n' "$ids" | grep -c .)

    echo
    echo -e "${BOLD}${CYAN}── $title ──${NC} ${DIM}(filter: name=$filter)${NC}"
    if [[ $container_count -eq 0 ]]; then
        echo -e "  ${DIM}no containers${NC}"
        return
    fi

    # State (running/exited/...) and health (healthy/unhealthy/starting/none)
    # come from `docker inspect`; we merge them with the standard `docker ps`
    # columns into one table.
    printf "  %-26s %-30s %-9s %-10s %-9s %s\n" "NAME" "IMAGE" "STATE" "UPTIME" "HEALTH" "PORTS"
    printf "  %-26s %-30s %-9s %-10s %-9s %s\n" "----" "-----" "-----" "------" "------" "-----"

    while IFS= read -r id; do
        [[ -z "$id" ]] && continue
        # One inspect per container — cheap, parallelizable if it ever matters.
        local raw name image state uptime health ports
        raw="$(docker inspect \
            --format '{{.Name}}|{{.Config.Image}}|{{.State.Status}}|{{.State.StartedAt}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}-{{end}}' \
            "$id" 2>/dev/null)"
        IFS='|' read -r name image state started health <<<"$raw"
        name="${name#/}"
        ports="$(docker port "$id" 2>/dev/null | head -1 | tr -d '\n')"
        # Uptime = NOW - StartedAt, rendered as "Nh" / "Nm" / "Ns".
        local started_epoch=$(date -d "$started" +%s 2>/dev/null || echo 0)
        local now_epoch=$(date +%s)
        local elapsed=$(( now_epoch - started_epoch ))
        if   (( elapsed < 60 ));     then uptime="${elapsed}s"
        elif (( elapsed < 3600 ));   then uptime="$(( elapsed / 60 ))m"
        elif (( elapsed < 86400 ));  then uptime="$(( elapsed / 3600 ))h"
        else                              uptime="$(( elapsed / 86400 ))d"
        fi

        local state_colour="$NC"
        case "$state" in
            running) state_colour="$GREEN";;
            exited|dead) state_colour="$RED";;
            restarting|created|paused) state_colour="$YELLOW";;
        esac
        local health_colour="$NC"
        case "$health" in
            healthy) health_colour="$GREEN";;
            unhealthy) health_colour="$RED";;
            starting) health_colour="$YELLOW";;
        esac

        printf "  %-26s %-30.30s ${state_colour}%-9s${NC} %-10s ${health_colour}%-9s${NC} %s\n" \
            "$name" "$image" "$state" "$uptime" "$health" "$ports"
    done <<<"$ids"
}

# Resource-usage footer for the matching containers — cheap one-shot
# `docker stats --no-stream`. Helps answer "is this stack idle or busy?"
render_stats() {
    local filter="$1"
    local ids
    ids="$(docker ps --filter "name=$filter" --format '{{.ID}}' 2>/dev/null)"
    [[ -z "$ids" ]] && return
    echo -e "  ${DIM}live resource usage:${NC}"
    # shellcheck disable=SC2086
    docker stats --no-stream --format "  {{.Name}}|{{.CPUPerc}}|{{.MemUsage}}" $ids 2>/dev/null | \
        awk -F'|' '{printf "    %-26s cpu=%-8s mem=%s\n", $1, $2, $3}'
}

mode="prod"
case "${1:-}" in
    ""|--prod) mode="prod" ;;
    --test)    mode="test" ;;
    --all)     mode="all" ;;
    -h|--help) usage; exit 0 ;;
    *)         usage; exit 1 ;;
esac

case "$mode" in
    prod)
        render_section "PROD stack" "^/nvr-"
        render_stats "^/nvr-"
        ;;
    test)
        render_section "TEST stack" "^/nvr_test_"
        render_stats "^/nvr_test_"
        ;;
    all)
        render_section "PROD stack" "^/nvr-"
        render_stats "^/nvr-"
        render_section "TEST stack" "^/nvr_test_"
        render_stats "^/nvr_test_"
        ;;
esac

echo
