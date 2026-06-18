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
    CYAN=$'\033[36m'; YELLOW=$'\033[33m'; MAGENTA=$'\033[35m'; NC=$'\033[0m'
else
    BOLD=""; DIM=""; RED=""; GREEN=""; CYAN=""; YELLOW=""; MAGENTA=""; NC=""
fi

# ─────────────────────────────────────────────────────────────────────────────
# E2E surface auto-discovery
#
# Every tests/e2e/test_<NAME>.py file is a "surface" — a coherent unit of
# tests for one functional area (auth, telemetry, storage, etc.). The
# launcher discovers them at runtime so adding a new test file requires
# zero edits to this script.
#
# The "auth" surface is special-cased to bundle test_auth_login.py +
# test_auth_coverage.py — the two are tightly related and operators
# usually want both.
# ─────────────────────────────────────────────────────────────────────────────

discover_e2e_surfaces() {
    # Output: one surface name per line, sans the test_ prefix and .py
    # suffix. E.g. tests/e2e/test_audit_log.py → "audit_log".
    #
    # Sort order: non-PTZ surfaces alphabetically first, then PTZ
    # surfaces (anything containing "ptz") at the end. The menu prints a
    # "PTZ (vendor-aware)" banner right before the PTZ block so the
    # subsection is visually clear. The numbering applied at print time
    # (13+) sticks to this same order, so surface_number_to_name() stays
    # consistent.
    if [[ ! -d "$REPO_ROOT/tests/e2e" ]]; then
        return
    fi
    local all
    all="$(ls "$REPO_ROOT/tests/e2e/"test_*.py 2>/dev/null | \
           sed -e 's|.*/test_||' -e 's|\.py$||' | sort -u)"
    # Emit non-PTZ first, then PTZ
    grep -v 'ptz' <<<"$all"
    grep 'ptz' <<<"$all"
}

# Map a surface name to its pytest path(s). The "auth" shorthand
# expands to both auth_login + auth_coverage (operator convention).
surface_to_pytest_args() {
    local surface="$1"
    case "$surface" in
        all|ALL)
            # Sentinel — run every tests/e2e/test_*.py. Same payload as
            # menu option 4 / --e2e, exposed via the surface flag so a
            # copy-pasted `test --surface=all` works the way readers
            # naturally expect.
            echo "tests/e2e"
            ;;
        auth)
            echo "tests/e2e/test_auth_login.py tests/e2e/test_auth_coverage.py"
            ;;
        *)
            local path="$REPO_ROOT/tests/e2e/test_${surface}.py"
            if [[ ! -f "$path" ]]; then
                return 1
            fi
            echo "tests/e2e/test_${surface}.py"
            ;;
    esac
}

print_menu() {
    cat <<EOF
${BOLD}${CYAN}─────────────────────────────────────────────────────────────────${NC}
${BOLD} MOBIUS.NVR — test launcher${NC}
${BOLD}${CYAN}─────────────────────────────────────────────────────────────────${NC}
  ${BOLD}1${NC}) ${GREEN}ALL${NC}                  ${DIM}--all${NC}            static + e2e (stack must be UP)
  ${BOLD}2${NC}) ${GREEN}Static${NC}               ${DIM}--static${NC}         audit + env + regression (~1s, no stack)
  ${BOLD}3${NC}) ${GREEN}Regression only${NC}      ${DIM}--regression${NC}     tests/regression/
  ${BOLD}4${NC}) ${GREEN}E2E${NC}                  ${DIM}--e2e${NC}            tests/e2e/ (stack must be UP)
  ${BOLD}5${NC}) ${GREEN}E2E surface${NC}          ${DIM}--surface=NAME${NC}   pick one (auto-discovered, see list at bottom)
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

    # Append the discovered surface list. Number them so they're
    # selectable from the main "Choose:" prompt — surfaces get number
    # 0 (the all-surfaces shorthand) and 13+ (one per file, avoiding
    # collision with the main actions 1-12). Each line ALSO shows the
    # copy-paste-ready --surface=NAME form for shell-history reuse.
    #
    # PTZ surfaces (the ones with `ptz` in the filename) get a separate
    # subsection because PTZ is vendor-aware (different protocols per
    # vendor — Reolink Baichuan, Amcrest CGI/ONVIF, Eufy bridge, SV3C
    # ONVIF-only) and operators usually want to scope a PTZ debug
    # session to just those tests.
    local surfaces
    surfaces="$(discover_e2e_surfaces)"
    if [[ -n "$surfaces" ]]; then
        echo -e " ${BOLD}E2E surfaces${NC} ${DIM}(pick number at the Choose: prompt, or copy --surface=NAME):${NC}"
        printf "  ${MAGENTA}%2d${NC}) ${GREEN}test --surface=all${NC}   ${DIM}(every surface — same as menu option 4)${NC}\n" 0

        # Split into PTZ vs non-PTZ so the PTZ block stays cohesive.
        local non_ptz_surfaces=""
        local ptz_surfaces=""
        while IFS= read -r s; do
            if [[ "$s" == *ptz* ]]; then
                ptz_surfaces+="$s"$'\n'
            else
                non_ptz_surfaces+="$s"$'\n'
            fi
        done <<<"$surfaces"

        # We must number every surface in the SAME order discover_e2e_surfaces
        # returned, so surface_number_to_name() (sed -n "$idx p" against the
        # SAME ordering) stays consistent. Print the non-PTZ ones first AT
        # THEIR ORIGINAL INDEX, then a PTZ banner, then the PTZ ones at
        # their original index. To do this cleanly we re-iterate the
        # original list and switch between sections on the fly.
        local n=13
        local in_ptz=0
        while IFS= read -r s; do
            if [[ "$s" == *ptz* ]] && (( in_ptz == 0 )); then
                echo -e " ${BOLD}PTZ (vendor-aware)${NC} ${DIM}— Reolink/Amcrest/SV3C/Eufy each have a different protocol; tests dispatch per camera_type:${NC}"
                in_ptz=1
            fi
            printf "  ${MAGENTA}%2d${NC}) ${GREEN}test --surface=%s${NC}\n" "$n" "$s"
            n=$((n + 1))
        done <<<"$surfaces"
        echo
    fi
}

# Map a numeric main-prompt choice (0 or 13+) to a surface name.
# Returns "" if not in the surface range.
surface_number_to_name() {
    local n="$1"
    if [[ "$n" == "0" ]]; then echo "all"; return 0; fi
    if ! [[ "$n" =~ ^[0-9]+$ ]] || (( n < 13 )); then
        echo ""
        return 1
    fi
    local idx=$((n - 13))
    discover_e2e_surfaces | sed -n "$((idx + 1))p"
}

# Prompt for an e2e surface and echo the selected name. Returns 1 if
# the user picks q/Q or types something that doesn't match. Used by
# the interactive flow for menu option 5.
prompt_for_surface() {
    local surfaces
    surfaces="$(discover_e2e_surfaces)"
    if [[ -z "$surfaces" ]]; then
        echo -e "${RED}No tests/e2e/test_*.py files found${NC}" >&2
        return 1
    fi

    echo -e "${BOLD}E2E surfaces${NC} (each line copy-paste-ready):"
    # `all` is a sentinel — same payload as menu option 4 (run every
    # tests/e2e/test_*.py). Surface-to-pytest-args treats it specially.
    printf "  ${MAGENTA}%2d${NC}) ${GREEN}test --surface=all${NC}  ${DIM}(every surface — same as menu option 4)${NC}\n" 0
    local i=1
    local arr=()
    while IFS= read -r s; do
        printf "  ${MAGENTA}%2d${NC}) ${GREEN}test --surface=%s${NC}\n" "$i" "$s"
        arr+=("$s")
        i=$((i + 1))
    done <<<"$surfaces"
    echo
    read -rp "$(echo -e "${BOLD}Surface (number, name, or 'all'):${NC} ") " pick
    echo
    case "$pick" in
        q|Q|"") return 1 ;;
        0|all|ALL) echo "all"; return 0 ;;
    esac
    # Numeric pick?
    if [[ "$pick" =~ ^[0-9]+$ ]]; then
        local idx=$((pick - 1))
        if (( idx >= 0 && idx < ${#arr[@]} )); then
            echo "${arr[$idx]}"
            return 0
        fi
        echo -e "${RED}Out of range${NC}" >&2
        return 1
    fi
    # Name pick — must match a discovered surface or the "auth" alias
    if [[ "$pick" == "auth" ]] || printf '%s\n' "${arr[@]}" | grep -qx "$pick"; then
        echo "$pick"
        return 0
    fi
    echo -e "${RED}Unknown surface: $pick${NC}" >&2
    return 1
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
            # Backward-compat alias for the two-file auth bundle. Equivalent to
            # `test --surface=auth`. New code should prefer the surface form.
            run "$PYTEST" tests/e2e/test_auth_login.py tests/e2e/test_auth_coverage.py "$@"
            ;;
        surface)
            # `test --surface=NAME` (or numbered option 5) — auto-discover and
            # dispatch to tests/e2e/test_<NAME>.py. When called with no NAME,
            # show the discovered list and prompt.
            local surface_name="${1:-}"
            shift || true
            if [[ -z "$surface_name" ]]; then
                surface_name="$(prompt_for_surface)" || exit 1
            fi
            local pytest_args
            if ! pytest_args="$(surface_to_pytest_args "$surface_name")"; then
                echo -e "${RED}No tests/e2e/test_${surface_name}.py found${NC}"
                echo "Discovered surfaces:"
                discover_e2e_surfaces | sed 's/^/  /'
                exit 1
            fi
            # shellcheck disable=SC2086  # intentional word-splitting for multi-path
            run "$PYTEST" $pytest_args "$@"
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
# For surface-number inputs (0 / 13+) the caller should additionally call
# surface_number_to_name to recover the surface — those return the literal
# token "surface" here.
arg_to_action() {
    case "$1" in
        1|--all|all|ALL)                                  echo "all" ;;
        2|--static|static|Static)                         echo "static" ;;
        3|--regression|regression|Regression)             echo "regression" ;;
        4|--e2e|e2e|E2E)                                  echo "e2e" ;;
        5)                                                echo "surface" ;;
        --auth|auth|AUTH)                                 echo "auth" ;;
        6|--smoke|--pre-commit|smoke|precommit|pre-commit) echo "smoke" ;;
        7|--ruff|--lint|ruff|lint)                        echo "ruff" ;;
        8|--ledger|ledger|Ledger)                         echo "ledger" ;;
        9|--stack-status|stack-status)                    echo "stack-status" ;;
        10|--stack-up|stack-up)                           echo "stack-up" ;;
        11|--stack-down|stack-down)                       echo "stack-down" ;;
        12|--custom|custom|Custom)                        echo "custom" ;;
        q|Q|--quit|quit|Quit|exit)                        echo "quit" ;;
        # 0 = all surfaces; 13+ = a specific discovered surface. The
        # entry point pulls the surface name via surface_number_to_name.
        0|1[3-9]|2[0-9]|3[0-9])
            if [[ -n "$(surface_number_to_name "$1" 2>/dev/null)" ]]; then
                echo "surface"
            else
                echo ""
            fi
            ;;
        *) echo "" ;;
    esac
}

# --- entry point -------------------------------------------------------------
#
# A few named flags carry an inline value (`--surface=NAME`); strip them
# into a separate `prefix_args` array passed to dispatch alongside the
# action name. Other flags / number-shortcuts don't carry inline values.
#
# We can't share-and-forward this through arg_to_action's stdout return
# (it'd need a struct), so the entry point does a small pre-parse pass.

split_value_arg() {
    # Recognise `--surface=NAME`. Echo the canonical action name on stdout
    # and the captured value on stderr; caller reads both via process subst.
    case "$1" in
        --surface=*) echo "surface"; echo "${1#--surface=}" >&2 ;;
        --custom=*)  echo "custom";  echo "${1#--custom=}"  >&2 ;;
        *) echo ""; ;;
    esac
}

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
    # Surface-number picks (0 / 13+) resolve to (action='surface', arg=<name>)
    if [[ "$action" == "surface" ]] && [[ "$choice" =~ ^[0-9]+$ ]]; then
        surf="$(surface_number_to_name "$choice")"
        if [[ -n "$surf" ]]; then
            dispatch "surface" "$surf"
            exit $?
        fi
    fi
    dispatch "$action"
else
    first="$1"; shift

    # Try value-carrying flag first (--surface=X / --custom=Y).
    value=""
    action="$( { split_value_arg "$first" 2>/tmp/_ts_value; } )"
    if [[ -n "$action" ]]; then
        value="$(cat /tmp/_ts_value 2>/dev/null)"
        rm -f /tmp/_ts_value
        # Prepend the captured value as the first dispatch arg so e.g.
        # `surface audit_log` reaches the case branch correctly.
        dispatch "$action" "$value" "$@"
        exit $?
    fi
    rm -f /tmp/_ts_value 2>/dev/null

    action="$(arg_to_action "$first")"
    if [[ -z "$action" ]]; then
        echo -e "${RED}Unknown action: $first${NC}"
        echo
        print_menu
        exit 1
    fi
    # Surface-number positional (0 / 13+) resolves the same way as
    # interactive: surface_number_to_name → dispatch surface <name>.
    if [[ "$action" == "surface" ]] && [[ "$first" =~ ^[0-9]+$ ]]; then
        surf="$(surface_number_to_name "$first")"
        if [[ -n "$surf" ]]; then
            dispatch "surface" "$surf" "$@"
            exit $?
        fi
    fi
    dispatch "$action" "$@"
fi
