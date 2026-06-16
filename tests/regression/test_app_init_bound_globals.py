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
    """
    Find every variable / class / function name that is GUARANTEED to
    exist once Python finishes loading this file.

    "Guaranteed" = the name appears in an assignment, def, or import
    that is at the TOP level of the file and NOT wrapped inside an
    `if`, `try`, `for`, `while`, or `with` block. Such a line runs every
    single time the file is loaded, so the name is unconditionally bound.

    Conversely, a name only defined inside `if some_condition:` may or
    may not exist depending on whether the branch was taken — those are
    NOT collected here.

    Returns the set of unconditionally-bound names. The caller uses this
    set as the "definitely safe to reference at module scope" allow-list.
    """
    names = set()

    # `tree.body` is the list of top-level statements in the file —
    # one entry per line that isn't indented under something else.
    for node in tree.body:

        # --- Form 1: plain assignment  ----------------------------------
        # Matches:   x = 5
        #            a, b = foo()
        #            x = y = 0          (multiple targets, same value)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                # Single name on the left: `x = ...`
                if isinstance(target, ast.Name):
                    names.add(target.id)
                # Tuple-unpack on the left: `a, b = foo()`
                elif isinstance(target, ast.Tuple):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name):
                            names.add(elt.id)

        # --- Form 2: assignment with type hint  -------------------------
        # Matches:   x: int = 5
        #            x: SomeType
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)

        # --- Form 3: augmented assignment  ------------------------------
        # Matches:   x += 1
        # Strictly speaking, += requires x to ALREADY exist — but we
        # treat it as "x is bound after this line runs" which is true.
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)

        # --- Form 4: def / async def / class  ---------------------------
        # Matches:   def foo(): ...
        #            class Bar: ...
        # The function/class name becomes bound at module scope.
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)

        # --- Form 5: plain import  --------------------------------------
        # Matches:   import os
        #            import requests as rq
        #            import services.foo            (binds `services`)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                # `as`-renamed → use the alias; otherwise the FIRST
                # segment of the dotted path is the bound name
                # (e.g. `import services.foo` binds `services`).
                names.add(alias.asname or alias.name.split(".")[0])

        # --- Form 6: from-import  ---------------------------------------
        # Matches:   from os import path
        #            from services.foo import bar as b
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)

    return names


def _collect_referenced_names_after_init(tree: ast.Module) -> set[str]:
    """
    Find every variable name that is READ at module level — i.e., every
    place the code uses a name to fetch its value at module scope.

    The bug we're guarding against: `app.py` does something like

        if some_camera_list:
            x = make_thing(...)         # x bound only sometimes
        ...
        register(thing=x)               # but USED unconditionally

    The read of `x` on the last line is exactly the kind of thing this
    function captures. Cross-referencing those reads against the set of
    GUARANTEED-bound names (the other helper) reveals reads that could
    fail with a NameError.

    Caveat — what we DON'T descend into:
      Function bodies, async function bodies, class bodies. Those code
      blocks only run when something calls them (later). At MODULE-LOAD
      time they're never executed, so a `def` referencing some name
      doesn't tell us anything about whether that name was bound at the
      moment of loading. We only care about the top-level execution flow.
    """
    refs = set()

    # ast.NodeVisitor is a helper class: subclass it, define
    # `visit_<NodeType>` methods, then call `visit(tree)` and Python
    # walks the tree calling our methods on every matching node.
    class _Visitor(ast.NodeVisitor):

        # Every time the visitor encounters a `Name` node — i.e., the
        # use of an identifier in the code — we check WHAT it's being
        # used for. `ctx` is the "context" attribute that distinguishes
        # READ from WRITE:
        #
        #   Load   → x is being read    (x + 1, print(x), foo(x))
        #   Store  → x is being written (x = ...)
        #   Del    → x is being deleted (del x)
        #
        # We only care about reads here — writes were already counted
        # by _collect_unconditional_module_assignments().
        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Load):
                refs.add(node.id)

        # The next three overrides STOP the visitor from descending into
        # function and class bodies — see the docstring above. Each
        # method simply `pass`es without calling generic_visit, which is
        # what would normally walk the children. By not walking, we
        # ignore everything inside.
        def visit_FunctionDef(self, node):
            pass

        def visit_AsyncFunctionDef(self, node):
            pass

        def visit_ClassDef(self, node):
            pass

    # Walk each TOP-LEVEL statement with a fresh visitor. We could
    # use a single visitor and call `.visit(tree)` once, but visiting
    # statement-by-statement lets us reason about each one
    # independently if we ever want per-line diagnostics.
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
