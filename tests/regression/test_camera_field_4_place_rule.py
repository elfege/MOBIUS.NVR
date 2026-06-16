"""
tests/regression/test_camera_field_4_place_rule.py
===================================================

Regression ledger entry — the "4-places" camera-field rule
(CLAUDE.md RULE 11.2, marked TEMPORARY until the underlying design defect
is fixed; see docs/plans/eliminate_four_place_camera_field_duplication_*).

Bug (recurring class):
    Adding a new field to `cameras` requires touching FOUR separate
    places — `config/cameras.json` seed, the migration SQL, `DIRECT_FIELDS`
    in `services/camera_config_sync.py`, and `direct_fields` in
    `services/camera_repository._db_row_to_camera_config()`. If the LAST
    one is missed, the field exists in the DB but `camera.get('field')`
    returns None everywhere in the app — silent runtime invisibility.

    Historical victim: `streaming_hub` (commit a16bf12 fixed it after
    being shipped broken). Could recur with any new field.

Guards (in priority order):
    (1) Every field declared in `DIRECT_FIELDS` (the seed→DB writer) MUST
        also appear in `direct_fields` (the DB→app reader). The reverse
        is allowed: the reader may have more (runtime-only fields like
        ONVIF subscription state).
    (2) Every column added by `psql/migrations/` to the `cameras` table
        that's a SCALAR (not JSONB) and not bookkeeping (created_at,
        updated_at) must be reachable by `direct_fields`. Catches "added
        a column, forgot to read it."

This test does NOT (yet) catch a `cameras.json` field that's missing
from the migration; that needs a separate seed-schema vs migration-schema
diff. Captured as a follow-up TODO inline.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests.regression._ledger import entry_for, format_failure_context


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_SYNC = REPO_ROOT / "services" / "camera_config_sync.py"
REPOSITORY  = REPO_ROOT / "services" / "camera_repository.py"
MIGRATIONS_DIR = REPO_ROOT / "psql" / "migrations"
_LEDGER_CONTEXT = format_failure_context(entry_for(__file__))

# Bookkeeping columns the app doesn't need to read directly. Add to this
# set if a new column genuinely shouldn't be surfaced by camera_repository.
#
# IMPORTANT — if you ever start reading one of these via
# `camera_repo.get_camera(...).get('<field>')`, MOVE the field to
# `direct_fields` in services/camera_repository.py FIRST, then remove
# it from this exempt set. The 4-place rule applies the moment a code
# path uses the repository for the read.
SCALAR_COLUMNS_EXEMPT_FROM_REPO_READ = {
    "id",
    "created_at",
    "updated_at",
    # video_fit_mode (mig 026?) is read by routes/camera.py via a direct
    # PostgREST select — NOT through camera_repo.get_camera(). Surfacing
    # it through the repo would be cleaner long-term; until then, this
    # exemption is the honest contract.
    "video_fit_mode",
    # audio_input_supported (mig 037? for evidence collection) is read
    # by services/evidence/audio_extractor.py via PostgREST embedded-FK
    # query. Not used through camera_repo today. Same caveat.
    "audio_input_supported",
}

# Column types the test treats as JSONB (so they DON'T need to be in
# direct_fields — the repo handles JSONB separately).
JSONB_TYPES = {"JSONB", "JSON"}


# ---------------------------------------------------------------------------
# Extractors
# ---------------------------------------------------------------------------

def _extract_list_constant(source: str, identifier: str) -> set[str]:
    """
    Parse `source` as Python and return the string literals from a top-level
    or function-level assignment `identifier = [ '...' , '...' , ... ]`.
    The function-scoped variant is needed for `direct_fields` which lives
    inside `_db_row_to_camera_config()`.
    """
    tree = ast.parse(source)
    out: set[str] = set()

    def _harvest(node):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == identifier:
                    if isinstance(node.value, ast.List):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                out.add(elt.value)

    class _V(ast.NodeVisitor):
        def visit_Assign(self, node):
            _harvest(node)
            self.generic_visit(node)
        def visit_FunctionDef(self, node):
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.Assign):
                    _harvest(stmt)
        def visit_AsyncFunctionDef(self, node):
            self.visit_FunctionDef(node)

    _V().visit(tree)
    return out


# Capture `ALTER TABLE cameras ADD COLUMN [IF NOT EXISTS] <name> <type>`
ALTER_RE = re.compile(
    r"ALTER\s+TABLE\s+cameras\s+ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?P<name>[a-z_][a-z0-9_]*)\s+(?P<type>[A-Z][A-Z0-9_]*)",
    re.IGNORECASE,
)

# Capture columns inside an initial `CREATE TABLE cameras (...);` block.
CREATE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?cameras\s*\((?P<body>.*?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)
CREATE_COL_RE = re.compile(
    r"^\s*(?P<name>[a-z_][a-z0-9_]*)\s+(?P<type>[A-Z][A-Z0-9_]*)",
    re.IGNORECASE | re.MULTILINE,
)


def _collect_cameras_columns() -> dict[str, str]:
    """
    Return {column_name: type_str} for every column added to the `cameras`
    table across all migrations (CREATE TABLE + ALTER TABLE ADD COLUMN).
    """
    cols: dict[str, str] = {}
    if not MIGRATIONS_DIR.is_dir():
        return cols
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        text = path.read_text(encoding="utf-8", errors="replace")
        # Strip line comments — they sometimes contain example DDL.
        text_nocmt = re.sub(r"--[^\n]*", "", text)

        for m in CREATE_RE.finditer(text_nocmt):
            for col in CREATE_COL_RE.finditer(m.group("body")):
                cols[col.group("name").lower()] = col.group("type").upper()

        for m in ALTER_RE.finditer(text_nocmt):
            cols[m.group("name").lower()] = m.group("type").upper()
    return cols


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_direct_fields_writer_is_subset_of_reader():
    """
    Every field in `DIRECT_FIELDS` (camera_config_sync.py — the seed→DB
    writer) must appear in `direct_fields` (camera_repository.py — the
    DB→app reader). If the writer adds a field but the reader doesn't,
    the value lands in the DB and is then silently invisible.
    """
    sync_src = CONFIG_SYNC.read_text(encoding="utf-8")
    repo_src = REPOSITORY.read_text(encoding="utf-8")
    writer = _extract_list_constant(sync_src, "DIRECT_FIELDS")
    reader = _extract_list_constant(repo_src, "direct_fields")

    assert writer, "Couldn't find DIRECT_FIELDS = [...] in camera_config_sync.py"
    assert reader, "Couldn't find direct_fields = [...] in camera_repository.py"

    only_in_writer = sorted(writer - reader)
    assert not only_in_writer, (
        f"camera_config_sync.DIRECT_FIELDS lists fields that "
        f"camera_repository.direct_fields does NOT read: {only_in_writer}. "
        "The seed pipeline will write them to the DB and the app will "
        "never see them — same shape as the historical streaming_hub bug. "
        "Add the field(s) to camera_repository._db_row_to_camera_config()'s "
        "direct_fields list." + _LEDGER_CONTEXT
    )


def test_every_scalar_cameras_column_is_readable():
    """
    Every scalar (non-JSONB) column declared by the migrations on the
    `cameras` table must appear in `camera_repository.direct_fields` —
    otherwise the app can't see it.
    """
    cols = _collect_cameras_columns()
    if not cols:
        pytest.skip("Couldn't parse cameras columns from migrations.")

    repo_src = REPOSITORY.read_text(encoding="utf-8")
    reader = _extract_list_constant(repo_src, "direct_fields")
    assert reader, "Couldn't find direct_fields = [...] in camera_repository.py"

    missing = []
    for name, type_ in cols.items():
        if type_ in JSONB_TYPES:
            continue  # JSONB columns have a separate code path
        if name in SCALAR_COLUMNS_EXEMPT_FROM_REPO_READ:
            continue
        if name not in reader:
            missing.append((name, type_))

    assert not missing, (
        "Scalar cameras-table columns NOT in camera_repository.direct_fields: "
        f"{missing}. Each missing column is silently invisible to the app — "
        "this is the 'streaming_hub no-route' bug shape. Either add the "
        "column to direct_fields, or (if it's truly bookkeeping) add it to "
        "SCALAR_COLUMNS_EXEMPT_FROM_REPO_READ in this test file."
        + _LEDGER_CONTEXT
    )
