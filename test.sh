#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# test.sh — interactive test-suite launcher for MOBIUS.NVR.
#
# Mirror of the `start.sh` / `stop.sh` / `deploy.sh` family. Triggered from
# the project root by the `test` shell function in ~/.bash_aliases.
#
# The launcher offers numbered choices for the most common pytest
# invocations: all, static-only, regression-only, e2e-only, pre-commit
# smoke subset, ruff lint, the regression-ledger table view, and an
# escape hatch for an arbitrary path.
#
# Stack readiness:
#   The e2e options assume the unified-compose test stack is up. If it's
#   not, pytest fails fast with the bring-up command (see
#   tests/e2e/conftest.py::_wait_for_stack). No pre-check here.
#
# Python environment:
#   Uses venv/bin/pytest + venv/bin/ruff if present (the canonical setup),
#   else falls back to whatever's on PATH (CI / container contexts).
# ─────────────────────────────────────────────────────────────────────────────

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_ROOT"

# Prefer the project venv. The fallback path handles fresh hosts where
# `pip install` happened at the system level — not the canonical setup,
# but functional.
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

# ANSI helpers — only colour when stdout is a TTY (the launcher is
# always interactive, but if someone pipes the menu we don't smear
# escape codes through their pipeline).
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
  ${BOLD}1${NC}) ${GREEN}ALL${NC}                  static + e2e (stack must be UP)
  ${BOLD}2${NC}) ${GREEN}Static${NC}               audit + env + regression (~1s, no stack)
  ${BOLD}3${NC}) ${GREEN}Regression only${NC}      tests/regression/
  ${BOLD}4${NC}) ${GREEN}E2E${NC}                  tests/e2e/ (stack must be UP)
  ${BOLD}5${NC}) ${GREEN}E2E auth only${NC}        tests/e2e/test_auth*
  ${BOLD}6${NC}) ${GREEN}Pre-commit smoke${NC}     what scripts/hooks/pre-commit runs
  ${BOLD}7${NC}) ${GREEN}Ruff F821 lint${NC}       ruff check .
  ${BOLD}8${NC}) ${GREEN}Regression ledger${NC}    print the bug-ledger table (no tests run)
  ${BOLD}9${NC}) ${GREEN}Custom path${NC}          prompt for an arbitrary pytest path
  ${BOLD}q${NC}) Quit
${BOLD}${CYAN}─────────────────────────────────────────────────────────────────${NC}
EOF
}

# Echo the command before running it — easy to copy/paste a single
# invocation into the shell later.
run() {
    echo -e "${DIM}\$ $*${NC}"
    "$@"
}

# If invoked with an argument, treat it as a direct menu choice (no
# prompt). Useful for scripting or muscle-memory: `./test.sh 2` runs
# the static tier without the prompt.
if [[ $# -ge 1 ]]; then
    choice="$1"
    shift
else
    print_menu
    read -rp "$(echo -e "${BOLD}Choose:${NC} ") " choice
    echo
fi

case "$choice" in
    1|all|ALL)
        run "$PYTEST" tests/ "$@"
        ;;
    2|static|Static)
        run "$PYTEST" tests/test_audit_coverage.py tests/test_env_conformity.py tests/regression "$@"
        ;;
    3|regression|Regression)
        run "$PYTEST" tests/regression "$@"
        ;;
    4|e2e|E2E)
        run "$PYTEST" tests/e2e "$@"
        ;;
    5|auth|AUTH)
        run "$PYTEST" tests/e2e/test_auth_login.py tests/e2e/test_auth_coverage.py "$@"
        ;;
    6|smoke|precommit|pre-commit)
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
    7|ruff|lint)
        if [[ -z "$RUFF" ]]; then
            echo "✗ ruff not found. Run: pip install -r requirements-test.txt"
            exit 1
        fi
        run "$RUFF" check . "$@"
        ;;
    8|ledger|Ledger)
        run "$PYTEST" --regression-ledger
        ;;
    9|custom|Custom)
        read -rp "$(echo -e "${BOLD}pytest path:${NC} ") " custom_path
        echo
        # shellcheck disable=SC2086  # word-splitting intentional for multi-path input
        run "$PYTEST" $custom_path "$@"
        ;;
    q|Q|quit|Quit|exit)
        exit 0
        ;;
    *)
        echo -e "${RED}Unknown choice: $choice${NC}"
        print_menu
        exit 1
        ;;
esac
