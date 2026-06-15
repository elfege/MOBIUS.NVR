#!/bin/bash
# =============================================================================
# psql/02-apply-migrations.sh — first-run migration runner for Postgres init.
# =============================================================================
# Postgres processes files in /docker-entrypoint-initdb.d/ in alphabetical
# order on the FIRST initialization of a fresh data directory:
#
#   01-schema.sql           ← init-db.sql, baseline schema
#   02-apply-migrations.sh  ← this script, applies psql/migrations/*.sql
#
# After first init, this script never runs again (Postgres records init
# completion in PG_VERSION). For an already-initialized prod database
# start.sh re-applies migrations idempotently against the live container —
# this script is the equivalent path for ephemeral test stacks where the
# data dir starts empty every run.
#
# Idempotent migrations are assumed (the codebase uses IF NOT EXISTS /
# ON CONFLICT DO NOTHING patterns). A non-idempotent migration will fail
# loudly here on a fresh init.
# =============================================================================
set -e

MIGRATIONS_DIR="/docker-entrypoint-initdb.d/migrations"

if [[ ! -d "$MIGRATIONS_DIR" ]]; then
    echo "[init] No migrations directory at $MIGRATIONS_DIR — skipping."
    exit 0
fi

echo "[init] Applying migrations from $MIGRATIONS_DIR..."
applied=0
for mig in $(ls "$MIGRATIONS_DIR"/*.sql 2>/dev/null | sort); do
    name="$(basename "$mig")"
    echo "[init]   -> $name"
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
        -f "$mig" >/dev/null
    applied=$((applied + 1))
done

echo "[init] Done. $applied migration(s) applied."
