#!/bin/bash
# ─────────────────────────────────────────────────────────────────────
# _auto_version.sh — main-branch version-tagging discipline
#
# Idempotent helper invoked from post-merge AND post-commit hooks.
# Runs ONLY when HEAD is on `main`. On any other branch it exits 0
# silently so feature branches aren't tagged.
#
# Source of truth: ./.tag (gitignored, untracked, one-line version).
# Mirror in git: annotated tag `v<version>` at the commit that took
# ./.tag to that value.
#
# Behaviour on a main-branch commit/merge:
#   1. Find latest `v*.*.*` git tag.
#   2. Scan commits between that tag and HEAD. Infer bump level:
#        - major  → any commit subject contains `!:` or body has `BREAKING CHANGE`
#        - minor  → any commit subject starts with `feat:` / `feat(...)`, OR
#                   any new `psql/migrations/*.sql` was added since the tag
#                   AND at least one of those migrations is NOT pure-drop.
#                   "Pure-drop" = the file contains DROP statements
#                   (DROP TABLE / INDEX / COLUMN / SEQUENCE / POLICY / etc.
#                   or ALTER TABLE ... DROP COLUMN/CONSTRAINT) AND NO
#                   additive verbs (CREATE TABLE / INDEX / SEQUENCE / TYPE
#                   / FUNCTION / TRIGGER / VIEW / POLICY, INSERT INTO,
#                   ALTER TABLE ... ADD COLUMN/CONSTRAINT). A migration
#                   with neither additive nor drop verbs has unknown
#                   shape → conservative default = minor.
#        - patch  → anything else (incl. all-added migrations are pure-drop)
#   3. expected = bump(latest_tag, level).
#   4. If ./.tag < expected → overwrite ./.tag with expected. If the
#      operator pre-edited ./.tag to a HIGHER value, that wins (the
#      script never downgrades). Manual override path.
#   5. Create annotated tag `v$(cat ./.tag)` at HEAD if missing.
#
# Operator manual override:
#   - Edit ./.tag to e.g. `6.0.0` before merging. The hook will respect
#     it (still tags HEAD as v6.0.0) because expected (e.g. v5.33.146)
#     is lower and the script never downgrades ./.tag.
#
# First-run seed:
#   - When no `v*.*.*` tag exists yet, the script writes ./.tag = 5.33.145
#     (if missing) and tags HEAD as v5.33.145. Subsequent merges follow
#     the normal bump flow.
# ─────────────────────────────────────────────────────────────────────

set -e

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
TAG_FILE="$REPO_ROOT/.tag"
SEED_VERSION="5.33.145"

branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
[ "$branch" = "main" ] || exit 0

# --- helpers ---------------------------------------------------------

parse_version() {
    # "v5.33.145" or "5.33.145" → "5 33 145"
    echo "$1" | sed 's/^v//' | awk -F. '{print $1, $2, $3}'
}

bump_version() {
    # bump_version <version> <level: major|minor|patch>
    local v="$1" level="$2" maj min pat
    read -r maj min pat <<< "$(parse_version "$v")"
    case "$level" in
        major) echo "$((maj+1)).0.0" ;;
        minor) echo "$maj.$((min+1)).0" ;;
        patch) echo "$maj.$min.$((pat+1))" ;;
    esac
}

semver_gt() {
    # 0 (true) if $1 > $2; 1 otherwise.
    local a_maj a_min a_pat b_maj b_min b_pat
    read -r a_maj a_min a_pat <<< "$(parse_version "$1")"
    read -r b_maj b_min b_pat <<< "$(parse_version "$2")"
    [ "$a_maj" -gt "$b_maj" ] && return 0
    [ "$a_maj" -lt "$b_maj" ] && return 1
    [ "$a_min" -gt "$b_min" ] && return 0
    [ "$a_min" -lt "$b_min" ] && return 1
    [ "$a_pat" -gt "$b_pat" ] && return 0
    return 1
}

# --- main logic ------------------------------------------------------

latest_tag="$(git tag -l 'v*.*.*' | sort -V | tail -1)"
latest_version="${latest_tag#v}"

# First-run seed path: no version tag exists yet anywhere.
if [ -z "$latest_version" ]; then
    if [ ! -f "$TAG_FILE" ]; then
        echo "$SEED_VERSION" > "$TAG_FILE"
    fi
    current="$(tr -d '[:space:]' < "$TAG_FILE")"
    [ -z "$current" ] && { echo "$SEED_VERSION" > "$TAG_FILE"; current="$SEED_VERSION"; }
    if ! git rev-parse "v$current" >/dev/null 2>&1; then
        git tag -a "v$current" -m "Version $current (initial seed)"
        echo "[auto-version] seeded ./.tag=$current and tagged HEAD as v$current"
    fi
    exit 0
fi

# Commit messages and added files since latest tag.
msgs="$(git log "$latest_tag..HEAD" --format='%s%n%b' 2>/dev/null || true)"
added_files="$(git log "$latest_tag..HEAD" --diff-filter=A --name-only --format='' 2>/dev/null || true)"

level="patch"
if printf '%s\n' "$msgs" | grep -qE '(^|[^-])!:|BREAKING CHANGE'; then
    level="major"
elif printf '%s\n' "$msgs" | grep -qE '^feat(\(|:)'; then
    level="minor"
elif printf '%s\n' "$added_files" | grep -qE '^psql/migrations/.*\.sql$'; then
    # New migration file(s) present. The old rule was "any new migration
    # → minor" but that overweighted pure-cleanup drops (e.g. 044 dropping
    # the obsolete presence table got a minor bump v6.9.3 → v6.10.0 even
    # though the underlying refactor was a patch). 2026-06-19 refinement:
    # demote PURE-DROP migrations to patch by scanning each added file's
    # content. Additive verbs (CREATE TABLE/INDEX/etc., INSERT, ALTER ADD
    # COLUMN/CONSTRAINT) → minor. Pure DROPs → patch. Unknown shape (no
    # DROPs either) → minor (conservative default).
    added_migs="$(printf '%s\n' "$added_files" | grep -E '^psql/migrations/.*\.sql$' || true)"
    has_non_drop_migration=false
    while IFS= read -r mig; do
        [ -z "$mig" ] && continue
        # Pipeline:
        #   1. Read content as it lives in HEAD (the file is in the merge tree).
        #   2. perl -0pe slurps the whole file as one string and strips:
        #      (a) /* ... */ block comments — these are NOT rare; operators
        #          use them for multi-paragraph migration prologues that
        #          mention DDL verbs as explanation.
        #      (b) -- line comments — same false-positive risk (e.g. a
        #          drop migration's docstring mentioning what CREATE
        #          blocks were removed elsewhere).
        #      The `s` flag makes `.` match newlines; `?` makes `/*..*/`
        #      non-greedy so we don't swallow more than one block per match.
        #   3. tr collapses newlines/tabs to spaces so multi-line DDL
        #      (`ALTER TABLE foo\n  ADD COLUMN ...`) becomes a single line
        #      the [[:space:]] regex matches against without -z gymnastics.
        #   4. tr -s squeezes repeated spaces.
        # Perl is in the standard Ubuntu base — already required by
        # filter-repo (the public-mirror pipeline). No new dependency.
        content="$(git show "HEAD:$mig" 2>/dev/null | \
            perl -0pe 's|/\*.*?\*/||sg; s|--[^\n]*||g' 2>/dev/null | \
            tr '\n\t' '  ' | \
            tr -s ' ' || true)"
        # Any additive verb → not pure-drop → minor.
        if printf '%s' "$content" | grep -qiE 'CREATE TABLE|CREATE INDEX|CREATE SEQUENCE|CREATE TYPE|CREATE FUNCTION|CREATE TRIGGER|CREATE VIEW|CREATE POLICY|INSERT INTO|ALTER TABLE[[:space:]]+[^;]+ADD[[:space:]]+(COLUMN|CONSTRAINT)'; then
            has_non_drop_migration=true
            break
        fi
        # If no DROPs either, the migration's shape isn't recognizable as
        # a cleanup — default conservative (minor).
        if ! printf '%s' "$content" | grep -qiE 'DROP TABLE|DROP INDEX|DROP COLUMN|DROP TYPE|DROP FUNCTION|DROP TRIGGER|DROP VIEW|DROP SEQUENCE|DROP POLICY|ALTER TABLE[[:space:]]+[^;]+DROP[[:space:]]+(COLUMN|CONSTRAINT)'; then
            has_non_drop_migration=true
            break
        fi
        # Otherwise: this migration is pure-drop; loop continues to check
        # any siblings. All-pure-drop set → level stays "patch".
    done <<< "$added_migs"
    if [ "$has_non_drop_migration" = true ]; then
        level="minor"
    fi
fi

expected="$(bump_version "$latest_version" "$level")"

if [ ! -f "$TAG_FILE" ]; then
    echo "$latest_version" > "$TAG_FILE"
fi
current="$(tr -d '[:space:]' < "$TAG_FILE")"
[ -z "$current" ] && current="$latest_version"

# Forward-only update: write expected only if it's higher than current.
if semver_gt "$expected" "$current"; then
    echo "$expected" > "$TAG_FILE"
    echo "[auto-version] ./.tag: $current -> $expected (inferred bump: $level)"
    current="$expected"
fi

# Tag HEAD if no tag at this version yet.
if ! git rev-parse "v$current" >/dev/null 2>&1; then
    git tag -a "v$current" -m "Version $current"
    echo "[auto-version] tagged HEAD as v$current"
fi
