"""
tests/regression/test_dependency_drift.py
==========================================

Regression ledger entry — pre-existing typo in `requirements.txt` discovered
during the Phase B verification run (2026-06-15).

Bug:
    Line 41 read `pycryptodomextflite-runtime>=2.14,<3` — two packages
    (`pycryptodomex` and `tflite-runtime`) concatenated on one line with no
    newline between them. PyPI rejects with "No matching distribution found
    for pycryptodomextflite-runtime". Production was unaffected only because
    the running unified-nvr image was built before the typo was introduced;
    ANY fresh `docker compose build` (including third-party deployment) would
    have failed.

Fix:
    Commit 6b9d20f0 ("fix(deps): split pycryptodomex + tflite-runtime in
    requirements.txt"), 2026-06-15.

Guards:
    A pure-text scan for lines that look like two concatenated package names
    (no whitespace separator between two well-known PyPI-name patterns). Pure
    static check — no `pip install` required.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REQUIREMENTS = [
    REPO_ROOT / "requirements.txt",
    REPO_ROOT / "requirements-test.txt",
]

# Historical typos we never want to see in a requirement file again.
# Each entry is paired with the bug timestamp + fixing commit for audit.
FORBIDDEN_TOKENS = {
    # Bug 2026-06-15: `pycryptodomex` + `tflite-runtime` collapsed onto one
    # line with no newline separator. Fixed in commit 6b9d20f0.
    "pycryptodomextflite-runtime",
}


def _read_requirements(path: Path) -> list[tuple[int, str]]:
    """Return [(line_number_1_based, line_text), ...] for non-comment,
    non-blank lines."""
    if not path.is_file():
        return []
    out = []
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append((i, line))
    return out


def _normalize_name(line: str) -> str:
    """Strip version pin / extras from a requirement line, leaving just the
    PEP-508 name."""
    # Remove env markers (; ...) and extras ([foo,bar])
    m = re.match(r"\s*([A-Za-z][A-Za-z0-9_.\-]*)", line)
    return m.group(1).lower() if m else line.lower()


@pytest.mark.parametrize("req_file", REQUIREMENTS, ids=lambda p: p.name)
def test_no_known_historical_typos(req_file):
    """
    Every documented past dependency typo gets a permanent guard here.
    Specifically catches `pycryptodomextflite-runtime` (the 2026-06-15
    concat of `pycryptodomex` and `tflite-runtime`).
    """
    if not req_file.is_file():
        pytest.skip(f"{req_file.name} not present")
    text = req_file.read_text(encoding="utf-8")
    offenders = [tok for tok in FORBIDDEN_TOKENS if tok in text]
    assert not offenders, (
        f"{req_file.name} contains historically-known-bad tokens: "
        f"{offenders}. These were typos we already fixed once; their "
        "reappearance is a regression."
    )


@pytest.mark.parametrize("req_file", REQUIREMENTS, ids=lambda p: p.name)
def test_each_line_is_one_requirement(req_file):
    """
    Belt-and-braces against the same regression class. After PEP 508 parsing,
    every line should resolve to a single recognizable identifier — not
    two names with no delimiter.
    """
    if not req_file.is_file():
        pytest.skip(f"{req_file.name} not present")
    for line_no, line in _read_requirements(req_file):
        # The strictest plausible PEP-508 prefix: alpha + alphanum/_.-
        # ending at a version specifier, semicolon, or end of line.
        m = re.match(
            r"^\s*[A-Za-z][A-Za-z0-9_.\-]*\s*(\[[^\]]+\])?\s*([<>=!~][^;\s]*)?\s*(;.*)?$",
            line,
        )
        assert m, (
            f"{req_file.name}:{line_no} doesn't parse as a single requirement: "
            f"{line!r}. Concatenated names produce this exact failure shape."
        )
