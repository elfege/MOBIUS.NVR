#!/bin/bash
# ╔══════════════════════════════════════════════════════════════════════════════════════╗
# ║  scripts/publish_public_mirror.sh                                                    ║
# ║                                                                                      ║
# ║  Build a cleansed (filter-repo'd) view of the private MOBIUS.NVR-dev repo and push   ║
# ║  it to the PUBLIC mirror at elfege/MOBIUS.NVR. Default: fast-forward only — refuses  ║
# ║  to rewrite already-published portfolio history. Use --rewrite-portfolio-history to  ║
# ║  force a deliberate rewrite (e.g. after a filter-rule change or the one-time         ║
# ║  historical scrub).                                                                  ║
# ║                                                                                      ║
# ║  Adapted from MOBIUS.TILES's reference implementation on 2026-05-29. Canonical:      ║
# ║    ~/0_MOBIUS.TILES/docs/plans/dual_repo_canonical_runbook_and_pitfalls_for_         ║
# ║                                  tiles_and_nvr_2026_05_29.md                         ║
# ║                                                                                      ║
# ║  NVR-specific judgment-call strips beyond the canonical §3.2 list (per MSG-299       ║
# ║  refined per-file rule + the operator-approved CLAUDE.md / internal-infra calls):    ║
# ║    - CLAUDE.md (operator instructions: hostnames, IPs, AWS secret names)             ║
# ║    - data/ssh_config_entries/README.md (internal SSH host_label scheme)              ║
# ║    - docs/PROPOSAL_database_config_migration.md (internal planning)                  ║
# ║    - docs/README_plan_for_user_based_settings_implementation.md (internal plan)      ║
# ║    - docs/publisher_state_coordination_design.md (internal design notes)             ║
# ║                                                                                      ║
# ║  FLAGS:                                                                              ║
# ║    [<branch>]                       branch to publish (default: main)                ║
# ║    --rewrite-portfolio-history      ⚠ force-rewrite the public mirror (operator      ║
# ║                                       confirms; never automated)                     ║
# ║    --no-confirm                     skip the interactive confirmation (for scripted  ║
# ║                                       operator-only invocations)                     ║
# ║    --help, -h                       show usage and exit                              ║
# ║                                                                                      ║
# ║  CANONICAL EXCEPTIONS (documented):                                                  ║
# ║    S.2.1  source_global_env replaced by S.2.18 portable helper sourcing.             ║
# ║    S.2.17 builtin cd used for SCRIPT_DIR / REPO_ROOT resolution.                     ║
# ╚══════════════════════════════════════════════════════════════════════════════════════╝

[[ -t 1 ]] && clear

SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"
SCRIPT_R_PATH=$(realpath "${BASH_SOURCE[0]}")
SCRIPT_DIR="${SCRIPT_R_PATH%${SCRIPT_NAME}}"
REPO_ROOT="$(builtin cd "${SCRIPT_DIR}/.." && pwd)"

# Color helpers — home copy preferred, in-repo copy fallback, tolerated absent (S.2.18).
. ~/.env.colors 2>/dev/null || . "${REPO_ROOT}/.env.colors" 2>/dev/null || true

########################################################################-########################################################################
NVR_PUBLISH__ARGS=("$@")
NVR_PUBLISH__BRANCH="main"
NVR_PUBLISH__REWRITE=false
NVR_PUBLISH__NO_CONFIRM=false
NVR_PUBLISH__PUBLIC_URL="https://github.com/elfege/MOBIUS.NVR.git"
NVR_PUBLISH__BUILD=""
NVR_PUBLISH__REWRITE_CONFIRM_PHRASE="rewrite portfolio history"
########################################################################-########################################################################

safe_exit() {
	# Exit cleanly whether the script is sourced or executed.
	local exit_code=${1:-$?}
	if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
		exit "$exit_code"
	else
		return "$exit_code"
	fi
}

nvr_publish__show_help() {
	echo ""
	echo -e "${BOLD:-}${CYAN:-}Usage:${NC:-} $0 [<branch>] [--rewrite-portfolio-history] [--no-confirm] [--help|-h]"
	echo ""
	echo -e "  Build a cleansed (filter-repo'd) view of the private NVR-dev repo and push it"
	echo -e "  to the PUBLIC mirror at ${NVR_PUBLISH__PUBLIC_URL}."
	echo ""
	echo -e "${BOLD:-}Default mode (safe):${NC:-}"
	echo -e "  Fast-forward only. Aborts loudly if the push would rewrite already-published"
	echo -e "  portfolio history (which happens when the filter rules change or on the very"
	echo -e "  first cleansed publish over a previously raw-pushed mirror)."
	echo ""
	echo -e "${BOLD:-}Options:${NC:-}"
	echo -e "  ${CYAN:-}<branch>${NC:-}                          Branch to publish. Default: main."
	echo -e "  ${CYAN:-}--rewrite-portfolio-history${NC:-}       ⚠ Force-rewrite the public mirror. Required"
	echo -e "                                       after filter-rule changes or for the one-time"
	echo -e "                                       historical scrub. Prompts the operator to type"
	echo -e "                                       the exact phrase '${NVR_PUBLISH__REWRITE_CONFIRM_PHRASE}'"
	echo -e "                                       to confirm. NEVER call this from CI or a hook."
	echo -e "  ${CYAN:-}--no-confirm${NC:-}                      Skip the interactive confirmation. Operator-only,"
	echo -e "                                       for scripted invocations. Combine with"
	echo -e "                                       --rewrite-portfolio-history at your own risk."
	echo -e "  ${CYAN:-}--help${NC:-}, ${CYAN:-}-h${NC:-}                        Show this message and exit."
	echo ""
	echo -e "${BOLD:-}Examples:${NC:-}"
	echo -e "  ${GREEN:-}$0${NC:-}                                  # publish main (default), fast-forward only"
	echo -e "  ${GREEN:-}$0 main --rewrite-portfolio-history${NC:-} # one-time historical scrub of the public mirror"
	echo ""
	safe_exit 0
}

nvr_publish__parse_args() {
	local a saw_branch=false
	for a in "${NVR_PUBLISH__ARGS[@]}"; do
		case "$a" in
		--rewrite-portfolio-history) NVR_PUBLISH__REWRITE=true ;;
		--no-confirm) NVR_PUBLISH__NO_CONFIRM=true ;;
		--help | -h) nvr_publish__show_help ;;
		--*)
			echo -e "${RED:-}✗ unknown flag: $a${NC:-}" >&2
			safe_exit 2
			;;
		*)
			if $saw_branch; then
				echo -e "${RED:-}✗ unexpected positional: $a (branch already set to '${NVR_PUBLISH__BRANCH}')${NC:-}" >&2
				safe_exit 2
			fi
			NVR_PUBLISH__BRANCH="$a"
			saw_branch=true
			;;
		esac
	done
}

nvr_publish__verify_repo() {
	if ! git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
		echo -e "${RED:-}✗ $REPO_ROOT is not a git checkout${NC:-}" >&2
		safe_exit 1
	fi
}

nvr_publish__prepare_build() {
	NVR_PUBLISH__BUILD="$(mktemp -d /tmp/nvr_public_build.XXXX)"
	echo -e "${BOLD:-}→ build dir:${NC:-} ${NVR_PUBLISH__BUILD}"
}

nvr_publish__cleanup() {
	local exit_code=${1:-$?}
	local lineno=${2:-}
	local command=${3:-}

	trap - EXIT INT TSTP TERM ERR

	if [[ -n "$NVR_PUBLISH__BUILD" && -d "$NVR_PUBLISH__BUILD" ]]; then
		if [[ "$exit_code" -ne 0 ]]; then
			echo -e "${YELLOW:-}⚠ leaving build dir for inspection: ${NVR_PUBLISH__BUILD}${NC:-}" >&2
		else
			rm -rf "$NVR_PUBLISH__BUILD"
		fi
	fi

	if [[ "$exit_code" -ne 0 && -n "$lineno" ]]; then
		echo -e "${RED:-}✗ exit ${exit_code} at line ${lineno}: ${command}${NC:-}" >&2
	fi

	safe_exit "$exit_code"
}

nvr_publish__set_traps() {
	trap 'lineno=$LINENO; nvr_publish__cleanup "$?" "$lineno" "$BASH_COMMAND"' EXIT TERM ERR
	trap 'nvr_publish__cleanup 1 "USER INTERRUPT" "$BASH_COMMAND"' INT TSTP
}

nvr_publish__clone_into_build() {
	# Clone live repo to temp build dir; detach origin so a stray push can't reach private.
	echo -e "${BOLD:-}→ cloning${NC:-} ${REPO_ROOT} @ ${NVR_PUBLISH__BRANCH} → ${NVR_PUBLISH__BUILD}"
	git clone -q "$REPO_ROOT" "$NVR_PUBLISH__BUILD"
	builtin cd "$NVR_PUBLISH__BUILD"
	git checkout -q "$NVR_PUBLISH__BRANCH"
	git remote remove origin 2>/dev/null || true
}

nvr_publish__write_filter_rules() {
	# Content redactions (regex → replacement) for filter-repo's --replace-text.
	# Real LAN subnet 192.168.10.x → <LAN_IP>; common-example 192.168.1.x is left alone.
	local rt
	rt="$(mktemp /tmp/nvr_public_build.replace.XXXX)"
	printf 'regex:192\\.168\\.10\\.[0-9]{1,3}==><LAN_IP>\n' > "$rt"
	echo "$rt"
}

nvr_publish__run_filter_repo() {
	# Strip rules per canonical §3.2 + NVR-specific judgment-call additions (MSG-299).
	# Order: exact-match files (fastest) → directory prefixes → suffix patterns.
	local rt
	rt="$(nvr_publish__write_filter_rules)"
	echo -e "${BOLD:-}→ filter-repo (strip private paths + redact LAN subnet)${NC:-}"
	git filter-repo --force --prune-empty never --replace-text "$rt" --filename-callback '
strip_files = {
    # Canonical §3.2
    b"docs/README_handoff.md",
    b"docs/README_project_history.md",
    b"docs/README_port_mappings.md",
    b"chat.md",
    b"claude_rules.md",
    # NVR judgment-call additions (per MSG-299 + per-file portfolio rule)
    b"CLAUDE.md",
    b"data/ssh_config_entries/README.md",
    b"docs/PROPOSAL_database_config_migration.md",
    b"docs/README_plan_for_user_based_settings_implementation.md",
    b"docs/publisher_state_coordination_design.md",
}
strip_dirs = (
    b"docs/plans/",
    b"docs/history/",
    b"docs/teachings/",
    b"docs/weekly_summaries/",
    b"DOCS/",
    b"nginx/certs/",
)
strip_suffixes = (b".pem", b".key", b".crt", b".p12", b".pfx", b".sqlite", b".db")
strip_exact = {b".env", b"credentials.json"}

if filename in strip_files: return None
if filename in strip_exact: return None
for d in strip_dirs:
    if filename.startswith(d): return None
for s in strip_suffixes:
    if filename.endswith(s): return None
# Defensive: editor temp dotfiles like ".publisher_state_coordination_design.md.HkLp1w"
# basename starts with "." AND the original was a tracked .md → strip.
last_slash = filename.rfind(b"/")
base = filename[last_slash+1:] if last_slash >= 0 else filename
if base.startswith(b".") and base.count(b".") >= 2 and (b".md." in base or b".sh." in base or b".yml." in base):
    return None
return filename
'
	rm -f "$rt"
}

nvr_publish__leak_gate() {
	# Last automated check before the public network hits public/main. Any survivor = P0.
	echo -e "${BOLD:-}→ leak gate${NC:-}"

	local leaks ip_leaks
	# shellcheck disable=SC2016
	leaks="$(git ls-files | grep -E '\.(pem|key|crt|p12|pfx|sqlite|db)$|^(docs/(plans|history|teachings|weekly_summaries)/|DOCS/|nginx/certs/|chat\.md$|claude_rules\.md$|docs/README_(handoff|project_history|port_mappings)\.md$|credentials\.json$|\.env$|CLAUDE\.md$|data/ssh_config_entries/README\.md$|docs/PROPOSAL_database_config_migration\.md$|docs/README_plan_for_user_based_settings_implementation\.md$|docs/publisher_state_coordination_design\.md$)' || true)"
	if [[ -n "$leaks" ]]; then
		echo -e "${RED:-}✗ ABORT — flagged path(s) survived the filter:${NC:-}" >&2
		echo "$leaks" >&2
		safe_exit 1
	fi

	ip_leaks="$(git grep -lE '192\.168\.10\.[0-9]' -- . 2>/dev/null || true)"
	if [[ -n "$ip_leaks" ]]; then
		echo -e "${RED:-}✗ ABORT — real LAN IPs survived redaction in:${NC:-}" >&2
		echo "$ip_leaks" >&2
		safe_exit 1
	fi

	local commit_count file_count
	commit_count=$(git rev-list --count HEAD)
	file_count=$(git ls-files | wc -l)
	echo -e "${GREEN:-}✓ clean:${NC:-} ${commit_count} commits, ${file_count} files"
}

nvr_publish__attach_public_remote() {
	git remote add public "$NVR_PUBLISH__PUBLIC_URL"
}

nvr_publish__is_fast_forward() {
	local new_head old_head
	new_head="$(git rev-parse HEAD)"

	if ! git ls-remote --exit-code public "refs/heads/${NVR_PUBLISH__BRANCH}" >/dev/null 2>&1; then
		echo -e "${YELLOW:-}⚠ public/${NVR_PUBLISH__BRANCH} does not exist yet — first publish, treating as fast-forward${NC:-}"
		return 0
	fi

	git fetch -q public "${NVR_PUBLISH__BRANCH}:refs/remotes/public/${NVR_PUBLISH__BRANCH}" 2>/dev/null || true
	old_head="$(git rev-parse "refs/remotes/public/${NVR_PUBLISH__BRANCH}" 2>/dev/null || true)"

	if [[ -z "$old_head" ]]; then
		echo -e "${YELLOW:-}⚠ couldn't resolve public/${NVR_PUBLISH__BRANCH} locally after fetch — treating as fast-forward${NC:-}"
		return 0
	fi

	if git merge-base --is-ancestor "$old_head" "$new_head"; then
		return 0
	fi
	return 1
}

nvr_publish__confirm_rewrite() {
	$NVR_PUBLISH__NO_CONFIRM && return 0
	echo ""
	echo -e "${BOLD:-}${RED:-}⚠ ABOUT TO REWRITE PUBLIC PORTFOLIO HISTORY ⚠${NC:-}"
	echo -e "  target: ${NVR_PUBLISH__PUBLIC_URL} :: ${NVR_PUBLISH__BRANCH}"
	echo -e "  effect: every commit SHA on the public ${NVR_PUBLISH__BRANCH} will change."
	echo -e "          external links to specific public commits will break."
	echo -e "          appropriate for filter-rule changes and one-time scrubs;"
	echo -e "          NOT appropriate for routine publishes."
	echo ""
	echo -e "  to confirm, type the exact phrase below and press ENTER:"
	echo -e "    ${CYAN:-}${NVR_PUBLISH__REWRITE_CONFIRM_PHRASE}${NC:-}"
	echo ""
	local typed=""
	read -r -p "> " typed </dev/tty 2>/dev/tty || true
	if [[ "$typed" != "$NVR_PUBLISH__REWRITE_CONFIRM_PHRASE" ]]; then
		echo -e "${RED:-}✗ confirmation phrase did not match — aborting${NC:-}" >&2
		safe_exit 1
	fi
}

nvr_publish__push_branch() {
	if $NVR_PUBLISH__REWRITE; then
		# --force-with-lease needs a recent local view of public/main as its baseline.
		# In rewrite mode we skip the is_fast_forward check (which would otherwise
		# do this fetch), so do it explicitly here. Then pin the lease to the SHA
		# we just observed — refuses to push if someone else moved public/main
		# between the fetch and now.
		echo -e "${BOLD:-}→ seeding force-with-lease baseline (fetch public/${NVR_PUBLISH__BRANCH})${NC:-}"
		git fetch -q public "${NVR_PUBLISH__BRANCH}:refs/remotes/public/${NVR_PUBLISH__BRANCH}" 2>/dev/null || true
		local expected_sha
		expected_sha="$(git rev-parse "refs/remotes/public/${NVR_PUBLISH__BRANCH}" 2>/dev/null || true)"

		echo -e "${BOLD:-}${YELLOW:-}→ force-with-lease push (rewrite mode)${NC:-}"
		if [[ -n "$expected_sha" ]]; then
			git push --force-with-lease="${NVR_PUBLISH__BRANCH}:${expected_sha}" public "${NVR_PUBLISH__BRANCH}"
		else
			# public/main doesn't exist yet (impossible in practice for NVR) → plain force.
			git push --force public "${NVR_PUBLISH__BRANCH}"
		fi
	else
		echo -e "${BOLD:-}→ fast-forward push${NC:-}"
		git push public "${NVR_PUBLISH__BRANCH}"
	fi
}

nvr_publish__push_tags() {
	if $NVR_PUBLISH__REWRITE; then
		echo -e "${BOLD:-}${YELLOW:-}→ force tag push (rewrite mode)${NC:-}"
		git push --force public --tags
	else
		echo -e "${BOLD:-}→ tag push (add-only)${NC:-}"
		git push public --tags
	fi
}

nvr_publish__verify_ff_or_abort() {
	if $NVR_PUBLISH__REWRITE; then
		return 0
	fi
	if nvr_publish__is_fast_forward; then
		return 0
	fi
	echo "" >&2
	echo -e "${RED:-}✗ ABORT — fast-forward push to public/${NVR_PUBLISH__BRANCH} rejected.${NC:-}" >&2
	echo -e "${RED:-}  this push would REWRITE already-published portfolio history${NC:-}" >&2
	echo -e "${RED:-}  (filter rules likely changed since the last publish).${NC:-}" >&2
	echo "" >&2
	echo -e "  To accept the rewrite, re-run with:" >&2
	echo -e "    ${CYAN:-}$0 ${NVR_PUBLISH__BRANCH} --rewrite-portfolio-history${NC:-}" >&2
	echo "" >&2
	echo -e "  NEVER call --rewrite-portfolio-history from CI or a hook." >&2
	echo -e "  See ~/0_MOBIUS.TILES/docs/plans/dual_repo_canonical_runbook_..._2026_05_29.md §2." >&2
	safe_exit 1
}

nvr_publish__run() {
	nvr_publish__parse_args
	nvr_publish__verify_repo
	nvr_publish__prepare_build
	nvr_publish__set_traps

	nvr_publish__clone_into_build
	nvr_publish__run_filter_repo
	nvr_publish__leak_gate

	nvr_publish__attach_public_remote
	nvr_publish__verify_ff_or_abort
	$NVR_PUBLISH__REWRITE && nvr_publish__confirm_rewrite

	nvr_publish__push_branch
	nvr_publish__push_tags

	echo -e "${GREEN:-}✓ published to ${NVR_PUBLISH__PUBLIC_URL} :: ${NVR_PUBLISH__BRANCH}${NC:-}"
}

nvr_publish__run
