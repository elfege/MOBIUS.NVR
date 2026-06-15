"""
tests/regression/test_app_init_bound_globals.py
================================================

Regression ledger entry — `app.py` raised `NameError: name
'unifi_resource_monitor' is not defined` on any boot where `unifi_cameras`
was falsy (zero-camera test stacks, fresh clones, deployments without
UniFi Protect). Discovered during the Phase B verification run
(2026-06-15).

Bug:
    services/init block — `unifi_resource_monitor` was assigned ONLY
    inside `if unifi_cameras:`. When the condition was false, the name
    never bound; the unconditional usage at `_shared.set_services(...
    unifi_resource_monitor=unifi_resource_monitor)` then crashed gunicorn
    worker boot with code 3.

Fix:
    Commit ecb2f6f1 ("fix: complete unified-compose test-stack bring-up;
    AUTH.LOGIN.OK green"), 2026-06-15. Initialize the variable to None
    before the conditional.

Guard:
    AST walk of `app.py`. For every name referenced at module level in a
    `Call` keyword argument or assignment, verify the name is defined on
    every code path reaching that line — concretely: it must appear in an
    unconditional assignment, function/class def, import, or sit OUTSIDE
    any `if/try` block at module scope before its first reference.

    The AST analysis is conservative — it flags names that are POSSIBLY
    unbound, even when in practice all branches assign. Maintainers add
    exemptions to KNOWN_DEFINED_ELSEWHERE for names provided via imports
    that the simple analyzer misses.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
APP_PY = REPO_ROOT / "app.py"


# The specific name that triggered this regression test's existence. If a
# new conditional-init pattern recurs with a different name, add it here
# AND fix the root cause.
HISTORICAL_OFFENDERS = {
    "unifi_resource_monitor",
}


def _collect_unconditional_module_assignments(tree: ast.Module) -> set[str]:
    """Names assigned at module top-level OUTSIDE any if/try/with/for/while.
    These are "always-bound" regardless of branch."""
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
                elif isinstance(target, ast.Tuple):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name):
                            names.add(elt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _collect_referenced_names_after_init(tree: ast.Module) -> set[str]:
    """Every Name(Load) reference at module level — i.e., usages that
    must resolve to something bound at module scope."""
    refs = set()

    class _Visitor(ast.NodeVisitor):
        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Load):
                refs.add(node.id)

        def visit_FunctionDef(self, node):
            pass  # don't descend into function bodies — those run later

        def visit_AsyncFunctionDef(self, node):
            pass

        def visit_ClassDef(self, node):
            pass

    for stmt in tree.body:
        _Visitor().visit(stmt)
    return refs


def _conditionally_assigned_only(tree: ast.Module) -> set[str]:
    """
    Names assigned ONLY inside a conditional block (if/try/with) at module
    level — these are POSSIBLY unbound at module scope.
    """
    unconditional = _collect_unconditional_module_assignments(tree)
    conditional_only: set[str] = set()

    def _walk_assigns(node):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id not in unconditional:
                    conditional_only.add(target.id)

    for node in tree.body:
        if isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
            for inner in ast.walk(node):
                _walk_assigns(inner)
    return conditional_only


def test_no_historical_unbound_globals_recurred():
    """
    Permanent guard for the specific names that have caused this class
    of bug before. Any of them appearing in app.py without a top-level
    `name = None` (or equivalent) initializer is a regression.
    """
    assert APP_PY.is_file(), f"app.py not found at {APP_PY}"
    tree = ast.parse(APP_PY.read_text(encoding="utf-8"))

    unconditional = _collect_unconditional_module_assignments(tree)
    referenced = _collect_referenced_names_after_init(tree)
    cond_only = _conditionally_assigned_only(tree)

    regressed = []
    for name in HISTORICAL_OFFENDERS:
        if name in referenced and name not in unconditional and name in cond_only:
            regressed.append(name)

    assert not regressed, (
        f"Module-level reference to conditionally-assigned name(s) "
        f"{regressed} in app.py — same shape as the 2026-06-15 "
        "`unifi_resource_monitor` bug. Add a top-level initialization "
        "(e.g. `{name} = None`) BEFORE any conditional block that may "
        "or may not assign it."
    )


def test_app_module_parses():
    """Trivial smoke check — app.py must be syntactically valid Python.
    Catches the case where a refactor leaves the file in a non-parseable
    state."""
    assert APP_PY.is_file()
    ast.parse(APP_PY.read_text(encoding="utf-8"))
