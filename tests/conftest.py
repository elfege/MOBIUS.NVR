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
