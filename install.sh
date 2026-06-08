#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════════════════════╗
# ║  install.sh — THIN BOOTSTRAP                                                         ║
# ║                                                                                      ║
# ║  Installs MOBIUS.NVR by delegating to the central MOBIUS.INSTALLER orchestrator.    ║
# ║                                                                                      ║
# ║  This script is intentionally minimal: it fetches the latest tagged version of      ║
# ║  https://github.com/elfege/MOBIUS.INSTALLER into a /tmp build dir, then execs        ║
# ║  mobius_install.sh with --component=NVR. All real install logic — host prep,        ║
# ║  AWS-vs-.env seeding, dependency resolution, deploy.sh orchestration — lives in     ║
# ║  the central installer so a single bug fix there propagates to every project.       ║
# ║                                                                                      ║
# ║  USAGE:                                                                              ║
# ║    curl -fsSL https://raw.githubusercontent.com/elfege/MOBIUS.NVR/main/install.sh \  ║
# ║      | bash                                                                          ║
# ║                              — or, after a manual clone —                            ║
# ║    ./install.sh [--dry-run] [--yes] [--no-bootstrap] [--help]                        ║
# ║                                                                                      ║
# ║  Flags pass through to mobius_install.sh.                                            ║
# ║                                                                                      ║
# ║  ENV OVERRIDES:                                                                      ║
# ║    MOBIUS_INSTALLER_REPO   default https://github.com/elfege/MOBIUS.INSTALLER.git    ║
# ║    MOBIUS_INSTALLER_REF    default = latest v*.*.* tag                               ║
# ║    NVR_USE_AWS_SECRETS     true → AWS Secrets Manager mode (skips the prompt)        ║
# ║    NVR_AWS_PROFILE / _AWS_SECRET_NAMES  seed the AWS-mode .env stub                  ║
# ╚══════════════════════════════════════════════════════════════════════════════════════╝

set -u

MOBIUS_INSTALL_TARGET="NVR"
MOBIUS_INSTALLER_REPO="${MOBIUS_INSTALLER_REPO:-https://github.com/elfege/MOBIUS.INSTALLER.git}"
MOBIUS_INSTALLER_REF="${MOBIUS_INSTALLER_REF:-}"

# Inline colour fallbacks — under `curl | bash` no sibling helpers exist yet.
: "${RED:=$'\033[0;31m'}"
: "${GREEN:=$'\033[0;32m'}"
: "${YELLOW:=$'\033[1;33m'}"
: "${CYAN:=$'\033[0;36m'}"
: "${BOLD:=$'\033[1m'}"
: "${NC:=$'\033[0m'}"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    cat <<EOF

${BOLD}install.sh${NC} — thin bootstrap for MOBIUS.NVR

This script delegates to the central installer at:
  ${CYAN}${MOBIUS_INSTALLER_REPO}${NC}

It fetches the latest v*.*.* tag (or the ref pinned via MOBIUS_INSTALLER_REF)
into a temp dir, then execs mobius_install.sh with --component=NVR. All flags
you pass here are forwarded.

${BOLD}Quick use:${NC}
  ${GREEN}curl -fsSL https://raw.githubusercontent.com/elfege/MOBIUS.NVR/main/install.sh | bash${NC}

For full flag list, the central installer's --help is authoritative.
EOF
    exit 0
fi

# Need git + curl to bootstrap. If missing, point the user at apt/dnf since
# this thin bootstrap intentionally has no package-install logic of its own.
command -v git  >/dev/null 2>&1 || { echo -e "${RED}✗ 'git' is required to bootstrap the MOBIUS installer${NC}" >&2; exit 1; }

tmp="$(mktemp -d /tmp/mobius_installer_bootstrap.XXXX)"
trap 'rm -rf "$tmp"' EXIT

# Resolve installer ref: explicit override → latest v*.*.* tag → fail.
if [[ -z "$MOBIUS_INSTALLER_REF" ]]; then
    MOBIUS_INSTALLER_REF="$(git ls-remote --tags --refs "$MOBIUS_INSTALLER_REPO" 'v*.*.*' 2>/dev/null \
        | awk '{print $2}' | sed 's|refs/tags/||' \
        | sort -V | tail -1)"
    if [[ -z "$MOBIUS_INSTALLER_REF" ]]; then
        echo -e "${RED}✗ No v*.*.* tag found on ${MOBIUS_INSTALLER_REPO}${NC}" >&2
        echo -e "  Override with: ${CYAN}MOBIUS_INSTALLER_REF=main $0${NC}" >&2
        exit 1
    fi
fi

echo -e "${BOLD}→ Fetching MOBIUS.INSTALLER ${CYAN}${MOBIUS_INSTALLER_REF}${NC} for component ${CYAN}${MOBIUS_INSTALL_TARGET}${NC}"
git clone -q --depth=1 --branch "$MOBIUS_INSTALLER_REF" "$MOBIUS_INSTALLER_REPO" "$tmp" || {
    echo -e "${RED}✗ git clone failed${NC}" >&2
    exit 1
}

if [[ ! -x "$tmp/mobius_install.sh" ]]; then
    echo -e "${RED}✗ mobius_install.sh missing or non-executable in installer checkout${NC}" >&2
    exit 1
}

exec "$tmp/mobius_install.sh" --component="$MOBIUS_INSTALL_TARGET" "$@"
