"""
tests/regression/test_csrf_blueprint_coverage.py
=================================================

Regression ledger entry — every blueprint registered on the Flask app
MUST be in the `csrf.exempt(bp)` loop at app.py:201-205. Discovered
during the v6.2.x telemetry feature ship (2026-06-14).

Bug:
    Operator hit `Error: Unexpected token '<', "<!doctype "... is not
    valid JSON` when trying to save settings in the new Data tab. The
    POST to `/api/telemetry/settings` was being intercepted by Flask-WTF
    CSRF (no X-CSRFToken header in the JSON fetch), and Flask returned
    the session-required HTML page that the browser tried to JSON.parse.

    Root cause: I'd added the `@csrf_exempt` decorator from
    routes/helpers.py on the view function — but THAT decorator is a
    no-op (it sets `f._csrf_exempt = True` which Flask-WTF doesn't
    consult). Flask-WTF reads its own internal `_exempt_blueprints`
    set, populated only via `csrf.exempt(bp)` at app boot. The
    telemetry_bp was missing from that list.

Fix:
    Commit 989775d6 ("fix(telemetry): exempt telemetry_bp from
    Flask-WTF CSRF (the actual cause)"), 2026-06-14.

Guard:
    Parse app.py — collect (a) every `<name>_bp` blueprint imported,
    and (b) every blueprint passed to `csrf.exempt(bp)`. Assert (a) ⊆ (b),
    minus any explicit allow-list of CSRF-protected blueprints.

    Policy today: the app is a JSON-API + jQuery frontend. CSRF tokens
    add friction without preventing the relevant attack class on this
    surface. Every blueprint is exempt by design. If that ever changes
    — i.e. a blueprint genuinely should enforce CSRF — add it to
    CSRF_PROTECTED_BLUEPRINTS below with a comment explaining why.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
APP_PY = REPO_ROOT / "app.py"

# Blueprints intentionally NOT csrf.exempt'd. Empty today — every
# blueprint is exempt for the reasons in the module docstring. If you
# add one here, leave a comment explaining the threat model.
CSRF_PROTECTED_BLUEPRINTS: set[str] = set()


def _extract_imported_blueprints(tree: ast.Module) -> set[str]:
    """Return every `<name>_bp` symbol the app.py module imports."""
    bps: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                name = alias.asname or alias.name
                if name.endswith("_bp"):
                    bps.add(name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                if name.endswith("_bp"):
                    bps.add(name)
    return bps


def _extract_exempted_blueprints(source: str) -> set[str]:
    """
    Find the `for bp in [...]: csrf.exempt(bp)` block (or any direct
    `csrf.exempt(<name_bp>)` calls) and return the set of names.
    """
    bps: set[str] = set()
    tree = ast.parse(source)

    class _V(ast.NodeVisitor):
        def visit_For(self, node):
            # for bp in [auth_bp, camera_bp, ...]: csrf.exempt(bp)
            if not isinstance(node.iter, ast.List):
                return self.generic_visit(node)
            calls_csrf_exempt = any(
                isinstance(s, ast.Expr)
                and isinstance(s.value, ast.Call)
                and isinstance(s.value.func, ast.Attribute)
                and s.value.func.attr == "exempt"
                and isinstance(s.value.func.value, ast.Name)
                and s.value.func.value.id == "csrf"
                for s in node.body
            )
            if calls_csrf_exempt:
                for elt in node.iter.elts:
                    if isinstance(elt, ast.Name) and elt.id.endswith("_bp"):
                        bps.add(elt.id)
            self.generic_visit(node)

        def visit_Call(self, node):
            # direct csrf.exempt(name_bp) outside a loop
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "exempt"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "csrf"
                and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id.endswith("_bp")
            ):
                bps.add(node.args[0].id)
            self.generic_visit(node)

    _V().visit(tree)
    return bps


def _extract_registered_blueprints(source: str) -> set[str]:
    """
    Return every `<name>_bp` passed to `app.register_blueprint(...)`.
    A blueprint that's imported but never registered isn't part of the
    running app, so we use the registered set as the authoritative list
    of "blueprints the app actually serves."
    """
    bps: set[str] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "register_blueprint"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id.endswith("_bp")
        ):
            bps.add(node.args[0].id)
    return bps


def test_every_registered_blueprint_is_csrf_exempt():
    """
    Every blueprint registered on the Flask app must appear in the
    csrf.exempt() loop, modulo the explicit CSRF_PROTECTED_BLUEPRINTS
    allow-list.

    Catches the 2026-06-14 telemetry_bp omission (commit 989775d6).
    """
    assert APP_PY.is_file(), f"app.py not found at {APP_PY}"
    source = APP_PY.read_text(encoding="utf-8")

    registered = _extract_registered_blueprints(source)
    exempted = _extract_exempted_blueprints(source)

    missing = sorted((registered - exempted) - CSRF_PROTECTED_BLUEPRINTS)
    assert not missing, (
        f"Blueprints registered on the Flask app but NOT in the "
        f"`csrf.exempt(...)` loop: {missing}. Same shape as the "
        "2026-06-14 telemetry CSRF bug — POSTs to these blueprints "
        "would return Flask-WTF's HTML 'session required' page, which "
        "then breaks any JSON.parse() in the JS fetch handler. Add the "
        "blueprint(s) to the csrf.exempt for-loop in app.py."
    )


def test_no_orphan_exemptions():
    """
    Reverse direction — every blueprint in the csrf.exempt loop should
    actually be registered. A name in the exempt list that isn't
    register_blueprint'd is dead code, often left over from a removed
    feature, and worth flagging.
    """
    source = APP_PY.read_text(encoding="utf-8")
    registered = _extract_registered_blueprints(source)
    exempted = _extract_exempted_blueprints(source)

    orphans = sorted(exempted - registered)
    assert not orphans, (
        f"Blueprints in csrf.exempt(...) but NOT registered: {orphans}. "
        "Either register them or remove from the exempt list — dead "
        "names in this loop are a red flag that something got half-"
        "removed."
    )
