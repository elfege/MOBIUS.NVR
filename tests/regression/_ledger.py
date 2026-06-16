"""
tests/regression/_ledger.py
============================

Loader + helper for the human-readable regression ledger at
`tests/regression/ledger.yaml`.

Purpose
-------
The ledger is the single source of truth for "which past bug does this
regression test guard against." Each test file has exactly one entry. At
failure time, the test asks this helper for the entry's narrative and embeds
it into the assertion message, so a future maintainer sees the original
incident context — not just a static-analysis complaint.

Public API
----------
    load_ledger() -> dict
        Parse and return the full ledger. Memoised.

    entry_for(test_file: str | Path) -> dict | None
        Look up the ledger entry whose `test_file` matches the caller's path
        (matched as a repo-relative POSIX path). Returns None if no entry
        exists yet — DO NOT crash, since a freshly-added test that hasn't
        been ledgered yet is a real workflow.

    format_failure_context(entry: dict | None) -> str
        Render an entry as a multi-line block suitable for appending to an
        assertion message. Returns "" if entry is None.

    iter_entries() -> Iterable[dict]
        Enumerate every entry — used by the `--regression-ledger` pytest
        flag to print the ledger as a table.

Why YAML and not Python lists / JSON
------------------------------------
Operator's call (see the 2026-06-16 wrap discussion). The ledger is going
to grow; YAML's block-scalar literal (`|`) keeps multi-paragraph symptom /
root-cause narratives readable when you `cat` the file. JSON forces every
newline into `\\n` escapes, which makes the file unreadable past a handful
of entries.

Caveats
-------
- PyYAML is a transitive dep of the system Python on every dev host. It's
  not pinned in requirements.txt; if the test suite ever moves to a stricter
  base image, add `PyYAML>=6` to requirements-test.txt.
- The helper is intentionally read-only. No code path ever WRITES to
  ledger.yaml from a test — that would let a buggy test silently mark
  itself as ledgered. Authors add entries by hand.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LEDGER_PATH = REPO_ROOT / "tests" / "regression" / "ledger.yaml"


# Memoise the parse — pytest invokes one process per session and the ledger
# never mutates during a run. The cache key is path-mtime-aware so an editor
# that touches the file between back-to-back test invocations is honoured.
_CACHE: tuple[float, dict] | None = None


def load_ledger() -> dict:
    """Parse and return the ledger as a dict. Cached per-file-mtime."""
    global _CACHE
    mtime = LEDGER_PATH.stat().st_mtime if LEDGER_PATH.is_file() else -1.0
    if _CACHE is not None and _CACHE[0] == mtime:
        return _CACHE[1]
    if not LEDGER_PATH.is_file():
        data: dict = {"entries": []}
    else:
        data = yaml.safe_load(LEDGER_PATH.read_text(encoding="utf-8")) or {}
        if "entries" not in data:
            data["entries"] = []
    _CACHE = (mtime, data)
    return data


def iter_entries() -> Iterable[dict]:
    """Yield every ledger entry. Used by the `--regression-ledger` flag."""
    return iter(load_ledger().get("entries", []))


def entry_for(test_file: str | Path) -> dict | None:
    """
    Return the ledger entry whose `test_file` field matches the caller's
    path, or None if no entry exists.

    The match is done on the repo-relative POSIX path string — callers can
    pass either the absolute path of their `__file__` or a pre-computed
    relative path; both work.
    """
    if isinstance(test_file, Path):
        path = test_file
    else:
        path = Path(test_file)
    if path.is_absolute():
        try:
            rel = path.resolve().relative_to(REPO_ROOT)
        except ValueError:
            rel = path
    else:
        rel = path
    needle = rel.as_posix()
    for entry in iter_entries():
        if entry.get("test_file") == needle:
            return entry
    return None


def format_failure_context(entry: dict | None) -> str:
    """
    Render a ledger entry as a multi-line block to append to an assertion
    message. Returns "" if entry is None (e.g. a brand-new regression test
    that hasn't been ledgered yet — the test still fires, just without the
    historical context).

    Output shape (lines wrapped at ~80 chars by the assertion formatter,
    not here):

        ─── regression ledger: <id> ────────────────────────────────────
        Title:      <title>
        Discovered: <discovered>   Fixed in: <fixed_in>
        Symptom:
            <symptom indented>
        Root cause:
            <root_cause indented>
        See: tests/regression/ledger.yaml (search for id: <id>)
    """
    if not entry:
        return ""
    header = f"─── regression ledger: {entry.get('id', '?')} " + "─" * 40
    header = header[:79]
    lines = [
        "",
        header,
        f"Title:      {entry.get('title', '?')}",
        f"Discovered: {entry.get('discovered', '?')}   "
        f"Fixed in: {entry.get('fixed_in', '?')}",
    ]
    for field in ("symptom", "root_cause"):
        body = (entry.get(field) or "").rstrip()
        if not body:
            continue
        label = "Symptom:" if field == "symptom" else "Root cause:"
        lines.append(label)
        for ln in body.splitlines():
            lines.append("    " + ln)
    lines.append(
        f"See: tests/regression/ledger.yaml (search for id: {entry.get('id', '?')})"
    )
    return "\n".join(lines)
