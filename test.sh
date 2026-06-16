#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# test.sh — interactive test-suite launcher for MOBIUS.NVR.
#
# Triggered from the project root by the `test` shell function in
# ~/.bash_aliases (which falls through to the `test`/[ builtin in non-project
# directories). Mirrors start.sh / stop.sh / status.sh in the dev-tooling
# family.
#
# Two invocation modes:
#
#   1. Interactive numbered menu
#         test
#
#   2. Direct dispatch — by NUMBER (muscle memory) or NAMED FLAG (scriptable):
#         test 4              <=>  test --e2e
#         test 6              <=>  test --smoke
#         test --custom tests/regression/test_dependency_drift.py
#
#   Named flags are preferred for scripting / CI — they don't shift if the menu
#   re-orders. Numbers are preferred for muscle memory at the prompt.
#
# Stack-lifecycle options (9, 10, 11) delegate to status.sh / start.sh / stop.sh
# with --test. The formatting logic lives in status.sh (one home for table
# rendering); this script just launches it.
# ─────────────────────────────────────────────────────────────────────────────

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_ROOT"

# Prefer the project venv; fall back to PATH (CI / container).
if [[ -x "$REPO_ROOT/venv/bin/pytest" ]]; then
    PYTEST="$REPO_ROOT/venv/bin/pytest"
else
    PYTEST="$(command -v pytest || true)"
fi
if [[ -x "$REPO_ROOT/venv/bin/ruff" ]]; then
    RUFF="$REPO_ROOT/venv/bin/ruff"
else
    RUFF="$(command -v ruff || true)"
fi

if [[ -z "$PYTEST" ]]; then
    echo "✗ pytest not found. Run: pip install -r requirements-test.txt"
    exit 1
fi

# Colour helpers — only when stdout is a TTY.
if [[ -t 1 ]]; then
    BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'
    CYAN=$'\033[36m'; YELLOW=$'\033[33m'; NC=$'\033[0m'
else
    BOLD=""; DIM=""; RED=""; GREEN=""; CYAN=""; YELLOW=""; NC=""
fi

print_menu() {
    cat <<EOF
${BOLD}${CYAN}─────────────────────────────────────────────────────────────────${NC}
${BOLD} MOBIUS.NVR — test launcher${NC}
${BOLD}${CYAN}─────────────────────────────────────────────────────────────────${NC}
  ${BOLD}1${NC}) ${GREEN}ALL${NC}                  ${DIM}--all${NC}            static + e2e (stack must be UP)
  ${BOLD}2${NC}) ${GREEN}Static${NC}               ${DIM}--static${NC}         audit + env + regression (~1s, no stack)
  ${BOLD}3${NC}) ${GREEN}Regression only${NC}      ${DIM}--regression${NC}     tests/regression/
  ${BOLD}4${NC}) ${GREEN}E2E${NC}                  ${DIM}--e2e${NC}            tests/e2e/ (stack must be UP)
  ${BOLD}5${NC}) ${GREEN}E2E auth only${NC}        ${DIM}--auth${NC}           tests/e2e/test_auth*
  ${BOLD}6${NC}) ${GREEN}Pre-commit smoke${NC}     ${DIM}--smoke${NC}          what scripts/hooks/pre-commit runs
  ${BOLD}7${NC}) ${GREEN}Ruff F821 lint${NC}       ${DIM}--ruff${NC}           ruff check .
  ${BOLD}8${NC}) ${GREEN}Regression ledger${NC}    ${DIM}--ledger${NC}         print the bug-ledger table (no tests run)
  ${BOLD}9${NC}) ${YELLOW}Stack status${NC}         ${DIM}--stack-status${NC}   status.sh --test
 ${BOLD}10${NC}) ${YELLOW}Stack up${NC}             ${DIM}--stack-up${NC}       start.sh --test
 ${BOLD}11${NC}) ${YELLOW}Stack down${NC}           ${DIM}--stack-down${NC}     stop.sh  --test
 ${BOLD}12${NC}) ${GREEN}Custom path${NC}          ${DIM}--custom <path>${NC}  arbitrary pytest path
  ${BOLD}q${NC}) Quit
${BOLD}${CYAN}─────────────────────────────────────────────────────────────────${NC}
EOF
}

# Echo each command before running so a single invocation is copy/pastable.
run() {
    echo -e "${DIM}\$ $*${NC}"
    "$@"
}

# Single dispatch table — the numbered shortcut, the named flag, and the
# interactive menu all resolve to one of these canonical actions.
dispatch() {
    local action="$1"; shift
    case "$action" in
        all)
            run "$PYTEST" tests/ "$@"
            ;;
        static)
            run "$PYTEST" tests/test_audit_coverage.py tests/test_env_conformity.py tests/regression "$@"
            ;;
        regression)
            run "$PYTEST" tests/regression "$@"
            ;;
        e2e)
            run "$PYTEST" tests/e2e "$@"
            ;;
        auth)
            run "$PYTEST" tests/e2e/test_auth_login.py tests/e2e/test_auth_coverage.py "$@"
            ;;
        smoke)
            echo -e "${BOLD}[1/2] ruff check .${NC}"
            if [[ -n "$RUFF" ]]; then
                run "$RUFF" check . || { echo -e "${RED}ruff failed${NC}"; exit 1; }
            else
                echo -e "${YELLOW}ruff not installed — skipping lint step${NC}"
            fi
            echo
            echo -e "${BOLD}[2/2] pytest smoke subset${NC}"
            run "$PYTEST" tests/test_audit_coverage.py tests/test_env_conformity.py tests/regression "$@"
            ;;
        ruff)
            if [[ -z "$RUFF" ]]; then
                echo "✗ ruff not found. Run: pip install -r requirements-test.txt"
                exit 1
            fi
            run "$RUFF" check . "$@"
            ;;
        ledger)
            run "$PYTEST" --regression-ledger
            ;;
        stack-status)
            run "$REPO_ROOT/status.sh" --test
            ;;
        stack-up)
            run "$REPO_ROOT/start.sh" --test
            ;;
        stack-down)
            run "$REPO_ROOT/stop.sh"  --test
            ;;
        custom)
            local custom_path="${1:-}"
            shift || true
            if [[ -z "$custom_path" ]]; then
                read -rp "$(echo -e "${BOLD}pytest path:${NC} ") " custom_path
                echo
            fi
            # shellcheck disable=SC2086  # word-splitting intentional for multi-path input
            run "$PYTEST" $custom_path "$@"
            ;;
        quit)
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown action: $action${NC}"
            print_menu
            exit 1
            ;;
    esac
}

# Map any accepted token (number, named flag, bare word) to a canonical action.
arg_to_action() {
    case "$1" in
        1|--all|all|ALL)                                  echo "all" ;;
        2|--static|static|Static)                         echo "static" ;;
        3|--regression|regression|Regression)             echo "regression" ;;
        4|--e2e|e2e|E2E)                                  echo "e2e" ;;
        5|--auth|auth|AUTH)                               echo "auth" ;;
        6|--smoke|--pre-commit|smoke|precommit|pre-commit) echo "smoke" ;;
        7|--ruff|--lint|ruff|lint)                        echo "ruff" ;;
        8|--ledger|ledger|Ledger)                         echo "ledger" ;;
        9|--stack-status|stack-status)                    echo "stack-status" ;;
        10|--stack-up|stack-up)                           echo "stack-up" ;;
        11|--stack-down|stack-down)                       echo "stack-down" ;;
        12|--custom|custom|Custom)                        echo "custom" ;;
        q|Q|--quit|quit|Quit|exit)                        echo "quit" ;;
        *) echo "" ;;
    esac
}

# --- entry point -------------------------------------------------------------
if [[ $# -eq 0 ]]; then
    # No args → interactive menu.
    print_menu
    read -rp "$(echo -e "${BOLD}Choose:${NC} ") " choice
    echo
    action="$(arg_to_action "$choice")"
    if [[ -z "$action" ]]; then
        echo -e "${RED}Unknown choice: $choice${NC}"
        exit 1
    fi
    dispatch "$action"
else
    # First arg is the action (number or named flag). Remaining args pass through.
    first="$1"; shift
    action="$(arg_to_action "$first")"
    if [[ -z "$action" ]]; then
        echo -e "${RED}Unknown action: $first${NC}"
        echo
        print_menu
        exit 1
    fi
    dispatch "$action" "$@"
fi
