"""
tests/test_audit_coverage.py — CI guard for the settings-audit trigger surface.

When a new settings table is added, we want to fail loudly if the
corresponding audit trigger is missing. The Phase 2 audit framework
(psql/migrations/036_setting_audit_log_and_triggers.sql, 2026-05-13)
captures changes via per-table `audit_<table>` triggers that all call
the shared `audit_setting_change(pk_col)` trigger function. Forgetting
the trigger on a new audit-tracked table means changes write through
silently with NO audit row — which would be a hard problem to notice
without this test.

The test is intentionally schema-text-based: it parses migration SQL
as strings, so it runs in CI without spinning up a Postgres. The trade-
off is that we don't catch runtime drift (e.g. someone manually
DROPped a trigger on the live DB); for that we'd need a separate live-
DB integration test, which is appropriate as a separate concern.

How to add a new audit-tracked table:
    1. Write the CREATE TABLE migration as usual.
    2. Add a `CREATE TRIGGER audit_<table>` line in a migration
       (036 already has the pattern; new tables can add a follow-up
       migration that just adds the trigger).
    3. Add the table to EXPECTED_AUDIT_TABLES below.
    4. CI passes again.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "psql" / "migrations"


# Canonical list of tables expected to have an audit trigger.
# IF YOU ADD A NEW SETTINGS TABLE: extend this set in the same PR that
# adds the CREATE TRIGGER. The mismatch tests below will fail otherwise.
EXPECTED_AUDIT_TABLES = frozenset({
    "cameras",
    "host_settings",
    "user_camera_preferences",
    "nvr_settings",
    "trusted_devices",
    "camera_credentials",
    "evidence_camera_settings",
})


def _read_all_migrations() -> str:
    """Concatenate every *.sql under psql/migrations/. Order doesn't matter
    for the static checks below — we're looking at the union of all
    declarations across the lifetime of the schema."""
    if not MIGRATIONS_DIR.is_dir():
        pytest.fail(f"migrations directory not found: {MIGRATIONS_DIR}")
    chunks = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def _strip_sql_comments(sql: str) -> str:
    """Strip `--` line comments and `/* */` block comments so the regex
    matchers below don't pick up *examples* / docstrings inside migration
    headers. (Migration 036 has the pattern in a comment block which would
    otherwise falsely satisfy these matchers.)"""
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", "", sql)
    return sql


def _find_audit_triggers(sql_text: str) -> dict:
    """
    Return {table_name: full_create_trigger_statement} for every
    `CREATE TRIGGER audit_<table>` declaration in the merged migration
    text. Matches both `CREATE TRIGGER ...` and `CREATE OR REPLACE
    TRIGGER ...` forms.
    """
    out = {}
    # Match the WHOLE CREATE TRIGGER ... ; statement so the per-statement
    # asserts below can inspect the body (target table + function args).
    pattern = re.compile(
        r"""
        CREATE \s+ (?:OR\s+REPLACE\s+)? TRIGGER \s+
        audit_(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)
        \s+ .*? ;                       # everything up to the statement end
        """,
        re.IGNORECASE | re.DOTALL | re.VERBOSE,
    )
    for m in pattern.finditer(sql_text):
        out[m.group("name")] = m.group(0)
    return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_every_expected_table_has_a_trigger():
    """The forward direction: each table the project considers audit-tracked
    must have an `audit_<table>` trigger declared somewhere in migrations."""
    sql = _strip_sql_comments(_read_all_migrations())
    triggers = _find_audit_triggers(sql)
    missing = sorted(EXPECTED_AUDIT_TABLES - set(triggers.keys()))
    assert not missing, (
        "Audit-tracked tables missing a CREATE TRIGGER audit_<table>: "
        f"{missing}. Either add the trigger in a migration, or remove "
        "the table from EXPECTED_AUDIT_TABLES if it really shouldn't be "
        "audited (uncommon — settings tables almost always should)."
    )


def test_no_orphan_audit_triggers():
    """The reverse direction: every `audit_<table>` trigger we find must
    correspond to a table that the project knows is audit-tracked. A
    trigger we don't recognize is either a forgotten-to-list addition
    or a leftover from a deleted table."""
    sql = _strip_sql_comments(_read_all_migrations())
    triggers = _find_audit_triggers(sql)
    orphans = sorted(set(triggers.keys()) - EXPECTED_AUDIT_TABLES)
    assert not orphans, (
        "audit_<table> triggers exist in migrations but the table is NOT "
        f"in EXPECTED_AUDIT_TABLES: {orphans}. Either add it to the "
        "constant in this test file, or remove the trigger from migrations."
    )


def test_every_trigger_uses_the_shared_function():
    """All audit triggers must dispatch to `audit_setting_change(pk_col)`.
    A trigger that calls some other function is almost certainly a typo or
    a divergent half-built feature — fail loud."""
    sql = _strip_sql_comments(_read_all_migrations())
    triggers = _find_audit_triggers(sql)
    bad = []
    for table, body in triggers.items():
        if "audit_setting_change" not in body:
            bad.append(table)
    assert not bad, (
        "These audit triggers do not call audit_setting_change(): "
        f"{bad}. The audit framework relies on this shared function — "
        "a divergent trigger function won't fan out via the LISTEN/NOTIFY "
        "channel in services/audit_listener.py."
    )


def test_every_trigger_fires_after_insert_or_update():
    """Audit must capture both INSERTs (new rows) and UPDATEs (mutations).
    Triggers wired only on UPDATE would silently miss row-creation events;
    DELETE is intentionally NOT captured (separate concern, append-only)."""
    sql = _strip_sql_comments(_read_all_migrations())
    triggers = _find_audit_triggers(sql)
    bad = []
    for table, body in triggers.items():
        # Be permissive with whitespace; SQL formatters vary.
        normalized = re.sub(r"\s+", " ", body.upper())
        if "AFTER INSERT OR UPDATE" not in normalized:
            bad.append(table)
    assert not bad, (
        "These audit triggers do NOT fire on `AFTER INSERT OR UPDATE`: "
        f"{bad}. A trigger wired only on UPDATE would miss new rows; "
        "the audit framework is designed to capture both. Fix the "
        "trigger definition in the migration."
    )
