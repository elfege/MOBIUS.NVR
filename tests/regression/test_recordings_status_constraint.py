"""
tests/regression/test_recordings_status_constraint.py
=======================================================

Regression ledger entry — the `recordings.status` CHECK constraint
allows only `recording | completed | archived | error`. Any code path
INSERTing `status='failed'` (the intuitive but wrong word) will hit a
DB-level rejection and surface as a mysterious "constraint violated"
crash with no breadcrumb.

Bug (historical):
    A developer (or LLM) hand-writes `INSERT INTO recordings (..., status)
    VALUES (..., 'failed')` because `failed` is the intuitive word for
    a recording that errored out. The DB rejects with a CHECK constraint
    violation. The Python code path that triggered the INSERT bubbles
    the psycopg2 error up through Flask, the request 500s, and the
    operator sees a stack trace that doesn't make the constraint obvious.

    Documented in `~/.claude/projects/.../memory/project_recordings_status_constraint.md`:
    "`recordings.status` allows recording|completed|archived|error, NOT failed."

Fix:
    Use `error` instead of `failed` everywhere. The constraint itself
    is the source of truth; this test is the static guard so the trap
    never recurs in code.

Guard:
    Grep the Python code for string literals `'failed'` / `"failed"`
    appearing within ~5 lines of a `recordings` / `recording_status` /
    `status=` / `INSERT INTO recordings` context. False-positive-prone
    so we accept some lookahead noise.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.regression._ledger import entry_for, format_failure_context


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_LEDGER_CONTEXT = format_failure_context(entry_for(__file__))

# Directories to scan. Tests + venv excluded.
SCAN_DIRS = [
    REPO_ROOT / "services",
    REPO_ROOT / "routes",
    REPO_ROOT / "config",
    REPO_ROOT / "psql",
]

# Files inside the scan dirs to skip (e.g. test helpers, archived migrations).
SKIP_PATTERNS = (".pyc", "__pycache__/", "/test_", "/tests/")

# Forbidden patterns — restricted to ACTUAL SQL writes against the
# `recordings` table. In-memory dicts like {'status': 'failed'} that
# represent migration-result shapes (not DB writes) are intentionally
# allowed; the constraint applies only to what reaches Postgres.
FORBIDDEN_PATTERNS = [
    # SQL INSERT / UPDATE on recordings with a 'failed' literal anywhere
    # in the same statement window.
    re.compile(
        r"""(?:INSERT\s+INTO\s+recordings|UPDATE\s+recordings)\b[^;]{0,400}['"]failed['"]""",
        re.IGNORECASE | re.DOTALL,
    ),
    # PostgREST patch with a status=failed payload
    re.compile(
        r"""recordings[^;]{0,200}['"]status['"]\s*:\s*['"]failed['"]""",
        re.IGNORECASE | re.DOTALL,
    ),
]


def _iter_files():
    for d in SCAN_DIRS:
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if not p.is_file():
                continue
            if any(s in str(p) for s in SKIP_PATTERNS):
                continue
            if p.suffix not in (".py", ".sql", ".json"):
                continue
            yield p


def test_no_recordings_status_failed_literal():
    """
    Catches `status='failed'` literals (in Python, SQL, or JSON) that
    would be rejected by the recordings.status CHECK constraint.
    """
    offenders = []
    for path in _iter_files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pat in FORBIDDEN_PATTERNS:
            for m in pat.finditer(text):
                # Pull the surrounding line for the error message.
                line_no = text.count("\n", 0, m.start()) + 1
                line = text.splitlines()[line_no - 1].strip()
                offenders.append((str(path.relative_to(REPO_ROOT)), line_no, line))

    assert not offenders, (
        "Code path(s) reference recordings.status='failed' — the DB "
        f"CHECK constraint REJECTS that value. Offenders: {offenders}. "
        "Allowed values: recording | completed | archived | error. "
        "Use 'error' instead of 'failed' for error states."
        + _LEDGER_CONTEXT
    )


def test_check_constraint_unchanged_in_migrations():
    """
    Pin the constraint definition so a future migration that loosens
    it (adds 'failed' to the allowed list) fires this regression and
    forces the author to update the memory file + this test in lock-step.

    Currently the allowed set is {recording, completed, archived, error}.
    If you genuinely want to add a value, update this test AND
    project_recordings_status_constraint in memory AND every consumer.
    """
    migrations_dir = REPO_ROOT / "psql" / "migrations"
    init_db        = REPO_ROOT / "psql" / "init-db.sql"

    text_chunks = []
    if init_db.is_file():
        text_chunks.append(init_db.read_text(encoding="utf-8"))
    if migrations_dir.is_dir():
        for p in sorted(migrations_dir.glob("*.sql")):
            text_chunks.append(p.read_text(encoding="utf-8"))
    full = "\n".join(text_chunks)

    # The CHECK constraint on recordings.status — the actual codebase
    # uses an inline anonymous CHECK on the column definition rather
    # than a named constraint, so we match the inline form directly.
    constraint_re = re.compile(
        r"CHECK\s*\(\s*status\s+IN\s*\(([^)]+)\)\s*\)",
        re.IGNORECASE | re.DOTALL,
    )
    matches = constraint_re.findall(full)
    if not matches:
        pytest.skip(
            "Couldn't locate `recordings_status_check` in migrations — "
            "constraint may have been renamed. Update this regression test."
        )

    # Every observed constraint definition must include all four allowed
    # values, and none of the forbidden ones.
    allowed = {"recording", "completed", "archived", "error"}
    forbidden = {"failed"}
    for body in matches:
        body_lower = body.lower()
        missing = [v for v in allowed if v not in body_lower]
        snuck_in = [v for v in forbidden if v in body_lower]
        assert not missing, (
            f"recordings_status_check appears to have dropped allowed value(s): "
            f"{missing}. If intentional, update this regression test."
            + _LEDGER_CONTEXT
        )
        assert not snuck_in, (
            f"recordings_status_check now allows forbidden value(s): "
            f"{snuck_in}. If intentional (e.g., promoting 'failed' to a "
            "real state), update this regression test AND the "
            "project_recordings_status_constraint memory entry AND every "
            "code-side consumer." + _LEDGER_CONTEXT
        )
