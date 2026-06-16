"""
tests/conftest.py — pytest configuration shared by every test module.

Keeping the suite zero-dependency-on-running-services where possible: the
audit-coverage test (the original reason this directory exists) parses
migration SQL as text. No DB connection required.
"""

from __future__ import annotations

import os
import sys

# Allow `from services...` / `from routes...` imports without installing
# the project as a package. The tests live in <repo>/tests/, and the
# project's top-level modules live at <repo>/. One-up is the right path.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# -----------------------------------------------------------------------------
# `pytest --regression-ledger` — print the human-readable bug ledger and exit.
#
# A read-only browse of `tests/regression/ledger.yaml` formatted as a table.
# Useful for "what regressions do we actually have a guard for?" without
# crawling YAML or test docstrings.
# -----------------------------------------------------------------------------

def pytest_addoption(parser):
    parser.addoption(
        "--regression-ledger",
        action="store_true",
        default=False,
        help="Print the regression-test ledger as a table and exit without "
             "running tests. The ledger lives at tests/regression/ledger.yaml.",
    )


def pytest_configure(config):
    if not config.getoption("--regression-ledger"):
        return

    import pytest as _pytest

    # Import lazily — the ledger helper depends on PyYAML, only needed here.
    from tests.regression._ledger import iter_entries

    entries = list(iter_entries())
    if not entries:
        print("(ledger is empty — see tests/regression/ledger.yaml)")
        _pytest.exit("regression-ledger printed", returncode=0)

    # Column widths: ID + Discovered + Title.
    id_w = max(len("ID"), max(len(str(e.get("id", "?"))) for e in entries))
    date_w = max(len("Discovered"), max(len(str(e.get("discovered", "?"))) for e in entries))
    title_w = max(len("Title"), max(len(str(e.get("title", "?"))) for e in entries))

    line = f"+-{'-' * id_w}-+-{'-' * date_w}-+-{'-' * title_w}-+"
    print(line)
    print(f"| {'ID':<{id_w}} | {'Discovered':<{date_w}} | {'Title':<{title_w}} |")
    print(line)
    for e in entries:
        print(
            f"| {str(e.get('id', '?')):<{id_w}} | "
            f"{str(e.get('discovered', '?')):<{date_w}} | "
            f"{str(e.get('title', '?')):<{title_w}} |"
        )
    print(line)
    print(f"\n{len(entries)} regression(s) in ledger. "
          "Source: tests/regression/ledger.yaml")
    _pytest.exit("regression-ledger printed", returncode=0)
