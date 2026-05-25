#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# scrub_private_paths_from_history.sh
#
# Strips operator-private paths (docs/README_handoff.md and
# docs/README_project_history.md) from the COMPLETE git history of this
# repo, then force-pushes the rewritten history to both `origin`
# (MOBIUS.NVR-dev, private) and `public` (MOBIUS.NVR, the public mirror).
#
# Created 2026-05-13 after operator discovered the docs/ ignore exception
# was leaking handoff + history files to the public repo via the existing
# post-merge auto-push hook. CLAUDE.md updated in the same commit to
# include a hard rule preventing recurrence; a pre-push hook
# (scripts/hooks/pre-push-block-private-paths.sh) refuses any push that
# would re-introduce those paths.
#
# Why keep this as a script:
#   - Re-running it after a future leak is push-button.
#   - filter-repo's destructive behavior is well-contained and auditable
#     when scripted (single source of truth for which paths are scrubbed).
#   - Commit metadata (author, date, message) is preserved — the IP /
#     authorship chain stays intact, only the two private file paths
#     disappear from the rewritten history.
#
# Safety:
#   - Requires --force flag to actually execute. Default is dry-run.
#   - Tags `pre-scrub-backup-<date>` and `pre-scrub-public-backup-<date>`
#     before rewriting so the pre-scrub state is recoverable from
#     reflog or via the tag (until tag is deleted).
#   - Bails out with non-zero exit on any subcommand failure.
#
# Cost:
#   - Force-push rewrites every commit SHA. External references to
#     commit hashes die.
#   - GitHub keeps unreachable blobs ~30 days after force-push (their
#     internal garbage collection schedule). The leaked content remains
#     accessible via direct SHA URL during that window. Operator has
#     declined the alternative (delete + recreate the public repo) to
#     preserve IP / authorship evidence in the public history.
# ----------------------------------------------------------------------------
set -euo pipefail

PRIVATE_PATHS=(
    "docs/README_handoff.md"
    "docs/README_project_history.md"
    "docs/history/handoffs"
)

ORIGIN_REMOTE_URL="https://github.com/elfege/MOBIUS.NVR-dev.git"
PUBLIC_REMOTE_URL="https://github.com/elfege/MOBIUS.NVR.git"

DATE_TAG="$(date +%Y-%m-%d)"
BACKUP_TAG_LOCAL="pre-scrub-backup-${DATE_TAG}"
BACKUP_TAG_PUBLIC="pre-scrub-public-backup-${DATE_TAG}"

DRY_RUN=1
for arg in "$@"; do
    case "$arg" in
        --force) DRY_RUN=0 ;;
        -h|--help)
            sed -n '1,/^# ---*$/p' "$0" | sed 's/^# //; s/^#$//'
            exit 0
            ;;
    esac
done

echo "=========================================="
echo " scrub_private_paths_from_history"
echo "=========================================="
echo " Paths to strip:"
for p in "${PRIVATE_PATHS[@]}"; do echo "   - $p"; done
echo " Remotes affected: origin (private), public (public)"
echo " Dry-run: $DRY_RUN  (pass --force to execute)"
echo "=========================================="

if [[ $DRY_RUN -eq 1 ]]; then
    echo ""
    echo "[dry-run] Would tag current state:"
    echo "  git tag $BACKUP_TAG_LOCAL"
    echo ""
    echo "[dry-run] Would run filter-repo:"
    echo "  git filter-repo --invert-paths $(printf -- "--path %s " "${PRIVATE_PATHS[@]}") --force"
    echo ""
    echo "[dry-run] Would re-add remotes (filter-repo drops them as a safety):"
    echo "  git remote add origin $ORIGIN_REMOTE_URL"
    echo "  git remote add public $PUBLIC_REMOTE_URL"
    echo ""
    echo "[dry-run] Would force-push:"
    echo "  git push origin --force --all"
    echo "  git push origin --force --tags"
    echo "  git push public --force --all"
    echo "  git push public --force --tags"
    echo ""
    echo "Re-run with --force to actually do this."
    exit 0
fi

# --- LIVE EXECUTION FROM HERE ---

# 0. Sanity: working tree clean.
if [[ -n "$(git status --porcelain)" ]]; then
    echo "ERROR: working tree is not clean. Commit or stash before running." >&2
    exit 1
fi

# 1. Tag local backup pointing at current HEAD so we can recover.
echo ""
echo "[1/5] Tagging local backup → $BACKUP_TAG_LOCAL"
git tag -f "$BACKUP_TAG_LOCAL"

# 1b. Tag public/main backup too, if the remote ref exists locally.
if git rev-parse --verify --quiet refs/remotes/public/main >/dev/null; then
    echo "[1/5] Tagging public/main backup → $BACKUP_TAG_PUBLIC"
    git tag -f "$BACKUP_TAG_PUBLIC" refs/remotes/public/main
fi

# 2. Run filter-repo. --invert-paths means "remove these paths"; without
#    inversion it would KEEP only those paths.
echo ""
echo "[2/5] Running git filter-repo to scrub private paths from all history"
FILTER_ARGS=()
for p in "${PRIVATE_PATHS[@]}"; do
    FILTER_ARGS+=("--path" "$p")
done
git filter-repo --invert-paths "${FILTER_ARGS[@]}" --force

# 3. filter-repo deliberately removes remotes as a safety. Add them back.
echo ""
echo "[3/5] Re-adding remotes"
git remote add origin "$ORIGIN_REMOTE_URL" 2>/dev/null || git remote set-url origin "$ORIGIN_REMOTE_URL"
git remote add public "$PUBLIC_REMOTE_URL" 2>/dev/null || git remote set-url public "$PUBLIC_REMOTE_URL"

# 4. Force-push everything to both remotes.
echo ""
echo "[4/5] Force-pushing all branches and tags to origin (PRIVATE)"
git push origin --force --all
git push origin --force --tags

echo ""
echo "[4/5] Force-pushing all branches and tags to public (PUBLIC)"
git push public --force --all
git push public --force --tags

# 5. Done. Print summary.
echo ""
echo "[5/5] Verification:"
echo -n "  origin/main contains docs/README_handoff.md? "
git ls-tree -r origin/main --name-only | grep -F "docs/README_handoff.md" && echo "  YES — SOMETHING FAILED" || echo "  no (good)"
echo -n "  public/main contains docs/README_handoff.md? "
git fetch public main 2>/dev/null
git ls-tree -r public/main --name-only | grep -F "docs/README_handoff.md" && echo "  YES — SOMETHING FAILED" || echo "  no (good)"

echo ""
echo "Scrub complete. Recovery anchors (delete when you're confident):"
echo "  git tag -d $BACKUP_TAG_LOCAL"
[[ -n "$(git tag -l "$BACKUP_TAG_PUBLIC")" ]] && echo "  git tag -d $BACKUP_TAG_PUBLIC"
echo ""
echo "GitHub may keep unreachable blobs accessible via direct SHA URL"
echo "for ~30 days. After that, GitHub's internal GC purges them."
