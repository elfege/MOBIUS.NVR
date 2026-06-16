"""
tests/regression/test_app_init_bound_globals.py
================================================

Regression-test guard for "module-level reference to a name that's only
conditionally bound" — the bug class that crashed gunicorn boot with
`NameError: name 'unifi_resource_monitor' is not defined` on every host
that had `unifi_cameras` falsy (commit ecb2f6f1, 2026-06-15).

Source-of-truth narrative for this test is in
`tests/regression/ledger.yaml` under id `app-init-unbound-globals`. This
file imports that narrative at failure time so the assertion message
shows the original incident context.

Algorithm
---------
We walk app.py and compute two sets at module scope:

  DEFINITELY_BOUND
      Names guaranteed to exist by the time module loading finishes.
      Recursive: handles `if/else` (bound iff every branch binds),
      `try/except` (bound iff body AND every handler bind), `for` and
      `with` (treated as binding their target + body bindings).

  REFERENCED
      Names read at module scope outside function/class bodies.

A name appearing in `REFERENCED` but NOT in `DEFINITELY_BOUND` AND
assigned somewhere conditionally at module scope is flagged as the
NameError-at-import bug shape.

Inversion from earlier model (2026-06-16)
-----------------------------------------
Earlier iteration of this test enumerated KNOWN-BAD names
(`HISTORICAL_OFFENDERS = {"unifi_resource_monitor"}`) and flagged only
those. That worked once but meant every NEW occurrence of the bug class
required someone to remember to add the new name to the set. Inverted
to scan-all + WHITELIST per operator note 2026-06-16. The whitelist
holds names the analyzer flags falsely (e.g. names provided by
`from X import *`) plus genuine exceptions explained inline.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.regression._ledger import entry_for, format_failure_context


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
APP_PY = REPO_ROOT / "app.py"


# Names the scan-all analyzer flags but which are NOT bugs. Add an entry
# here when a false positive surfaces; never to silence a real one.
#
# Format: name → reason. The reason must explain WHY this isn't a NameError
# risk — typically because the name comes from outside the analyzer's
# vision (e.g. `from X import *`, a builtin masked at runtime, a name
# provided by `globals().update(...)`).
WHITELIST: dict[str, str] = {
    # No legitimate exceptions yet. As of 2026-06-16 the analyzer (with
    # recursive if/try/for/with handling) cleanly distinguishes definitely
    # bound from conditionally bound on app.py with zero false positives.
}


# ---------------------------------------------------------------------------
# AST analysis
# ---------------------------------------------------------------------------

def _names_assigned_by(node: ast.stmt) -> set[str]:
    """
    Return the set of names that the given statement BINDS if it executes
    to completion (without raising).

    Recursive — descends into nested if/try/for/while/with bodies so that
    e.g. `try: x = 1; except: x = 2` correctly yields {"x"}.
    """
    # ----- atomic assignment forms ------------------------------------------
    if isinstance(node, ast.Assign):
        names = set()
        for target in node.targets:
            names |= _names_from_target(target)
        return names

    if isinstance(node, ast.AnnAssign) and node.value is not None:
        # `x: int = 5` binds x; `x: int` (no value) is a declaration, not a bind.
        return _names_from_target(node.target)

    if isinstance(node, ast.AugAssign):
        return _names_from_target(node.target)

    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}

    if isinstance(node, ast.Import):
        return {a.asname or a.name.split(".")[0] for a in node.names}

    if isinstance(node, ast.ImportFrom):
        return {a.asname or a.name for a in node.names}

    # ----- compound statements (recursive) ----------------------------------
    if isinstance(node, ast.If):
        # `if/elif/else`: a name is bound iff EVERY branch binds it.
        # No `else` clause? Then the implicit empty else binds nothing,
        # so any name only bound in the `if` body is conditional.
        body_bound = _bound_by_block(node.body)
        if not node.orelse:
            return set()
        else_bound = _bound_by_block(node.orelse)
        return body_bound & else_bound

    if isinstance(node, ast.Try):
        # `try: ... except E: ...`: from the perspective of code AFTER
        # the try block, we reach it via one of two paths:
        #   (a) the try body completed normally, OR
        #   (b) an except handler ran AND didn't terminate.
        # A name is bound at "after the try" iff every reachable path
        # binds it. Handlers that are NORETURN (raise / return /
        # sys.exit() / exit() / os._exit() etc.) don't actually reach
        # the post-try code — we can therefore exclude them from the
        # intersection. This is critical for the common pattern
        #   try:
        #       service = make_service()      # <-- bound here
        #   except Exception as e:
        #       print(e); exit(1)             # noreturn; doesn't fall through
        # where `service` IS unconditionally bound at any line after.
        body_bound = _bound_by_block(node.body)
        finally_bound = _bound_by_block(node.finalbody) if node.finalbody else set()
        live_handlers = [h for h in node.handlers if not _is_noreturn_block(h.body)]
        if not live_handlers:
            # Every handler is noreturn — only the try body's bindings reach
            # post-try code. The finally still runs in either case.
            return body_bound | finally_bound
        handler_sets = [_bound_by_block(h.body) for h in live_handlers]
        common_handler = set.intersection(*handler_sets) if handler_sets else set()
        return (body_bound & common_handler) | finally_bound

    if isinstance(node, ast.For):
        # `for x in iterable:` — if iterable is non-empty, x is bound to
        # the last element after the loop. We accept the rare empty-
        # iterable false negative since the bug class we're guarding is
        # `if` shape, not `for` shape.
        names = _names_from_target(node.target)
        names |= _bound_by_block(node.body)
        # else clause runs only when the loop completes normally;
        # treat its bindings as unconditional.
        names |= _bound_by_block(node.orelse)
        return names

    if isinstance(node, ast.While):
        # Same conservative stance as `for`: assume loop ran at least once.
        names = _bound_by_block(node.body)
        names |= _bound_by_block(node.orelse)
        return names

    if isinstance(node, ast.With):
        # `with X() as foo:` binds foo. Body's bindings propagate.
        names = set()
        for item in node.items:
            if item.optional_vars is not None:
                names |= _names_from_target(item.optional_vars)
        names |= _bound_by_block(node.body)
        return names

    return set()


def _names_from_target(target: ast.expr) -> set[str]:
    """Pull every Name out of an assignment target (handles Tuple/List
    unpacking and Starred forms)."""
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        out: set[str] = set()
        for elt in target.elts:
            out |= _names_from_target(elt)
        return out
    if isinstance(target, ast.Starred):
        return _names_from_target(target.value)
    return set()


def _bound_by_block(stmts: list[ast.stmt]) -> set[str]:
    """Names bound after executing a sequential block of statements."""
    out: set[str] = set()
    for s in stmts:
        out |= _names_assigned_by(s)
    return out


# Calls that terminate the process / unwind the stack. If an except
# handler ends with one of these (possibly after some prelude), the
# post-try code is unreachable from that handler.
_NORETURN_CALL_NAMES = {"exit", "quit"}
_NORETURN_ATTRS = {
    ("sys", "exit"),
    ("os", "_exit"),
    ("os", "abort"),
}


def _is_noreturn_block(stmts: list[ast.stmt]) -> bool:
    """
    True if the given statement list always exits before falling through
    to the next sibling statement — by `raise`, `return`, `continue`,
    `break`, or a call to a known process-terminating function.

    Used to identify except handlers that don't reach post-try code, so
    the binding analyzer can treat the try body's bindings as
    unconditional for everything after the try block.
    """
    if not stmts:
        return False
    last = stmts[-1]
    if isinstance(last, (ast.Raise, ast.Return, ast.Continue, ast.Break)):
        return True
    if isinstance(last, ast.Expr) and isinstance(last.value, ast.Call):
        call = last.value
        func = call.func
        if isinstance(func, ast.Name) and func.id in _NORETURN_CALL_NAMES:
            return True
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and (func.value.id, func.attr) in _NORETURN_ATTRS
        ):
            return True
    # An `if` whose every branch is noreturn is itself noreturn.
    if isinstance(last, ast.If) and last.orelse:
        return _is_noreturn_block(last.body) and _is_noreturn_block(last.orelse)
    return False


def _collect_definitely_bound(tree: ast.Module) -> set[str]:
    """Names guaranteed bound after the module finishes loading — the
    full recursive analyser version."""
    return _bound_by_block(tree.body)


def _collect_referenced_at_module(tree: ast.Module) -> set[str]:
    """
    Find every variable name READ at module scope, outside function and
    class bodies. Those bodies don't execute at module load time, so
    references inside them tell us nothing about import-time NameErrors.
    """
    refs: set[str] = set()

    class _Visitor(ast.NodeVisitor):
        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Load):
                refs.add(node.id)

        # Don't descend — function/class bodies run later, not at import.
        def visit_FunctionDef(self, node):
            pass

        def visit_AsyncFunctionDef(self, node):
            pass

        def visit_ClassDef(self, node):
            pass

    for stmt in tree.body:
        _Visitor().visit(stmt)
    return refs


def _collect_module_scope_references_with_lines(tree: ast.Module) -> dict[str, list[int]]:
    """
    Like _collect_referenced_at_module but returns line numbers per name.
    Used by the flow-aware suspect check.
    """
    by_line: dict[str, list[int]] = {}

    class _Visitor(ast.NodeVisitor):
        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Load):
                by_line.setdefault(node.id, []).append(node.lineno)

        def visit_FunctionDef(self, node):
            pass

        def visit_AsyncFunctionDef(self, node):
            pass

        def visit_ClassDef(self, node):
            pass

    for stmt in tree.body:
        _Visitor().visit(stmt)
    return by_line


def _collect_conditional_bind_ranges(tree: ast.Module) -> dict[str, list[tuple[int, int]]]:
    """
    For each name that's assigned somewhere at module scope INSIDE a
    conditional (if/try/for/while/with) block, return the list of
    (start_lineno, end_lineno) ranges of every enclosing block that
    contains a bind to that name.

    A reference falling inside one of a name's bind ranges is treated as
    safe-by-source-order — i.e. we assume the bind precedes the reference
    within the same block, the common pattern for `try: x = make()` /
    `if ok: x = make(); use(x)`. A reference OUTSIDE every such range
    is the bug shape (the `restart_handler = ... ` / referenced outside
    the try / NameError-if-handler-fires pattern).
    """
    ranges: dict[str, list[tuple[int, int]]] = {}

    def _record_for_block(block: ast.stmt, stmts: list[ast.stmt]) -> None:
        bound_here = _bound_by_block_recursive(stmts)
        if bound_here:
            start = block.lineno
            end = getattr(block, "end_lineno", block.lineno)
            for name in bound_here:
                ranges.setdefault(name, []).append((start, end))

    def _walk(stmts: list[ast.stmt]) -> None:
        for s in stmts:
            if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(s, ast.If):
                _record_for_block(s, s.body)
                _record_for_block(s, s.orelse)
                _walk(s.body)
                _walk(s.orelse)
            elif isinstance(s, ast.Try):
                _record_for_block(s, s.body)
                for h in s.handlers:
                    _record_for_block(s, h.body)
                _record_for_block(s, s.orelse)
                _record_for_block(s, s.finalbody)
                _walk(s.body)
                for h in s.handlers:
                    _walk(h.body)
                _walk(s.orelse)
                _walk(s.finalbody)
            elif isinstance(s, (ast.For, ast.AsyncFor, ast.While)):
                _record_for_block(s, s.body)
                _record_for_block(s, s.orelse)
                _walk(s.body)
                _walk(s.orelse)
            elif isinstance(s, (ast.With, ast.AsyncWith)):
                _record_for_block(s, s.body)
                _walk(s.body)

    _walk(tree.body)
    return ranges


def _bound_by_block_recursive(stmts: list[ast.stmt]) -> set[str]:
    """
    All names that any statement in `stmts` (or any nested statement,
    excluding function/class bodies) MIGHT bind. Looser than
    _bound_by_block — used to find "where does this name get assigned
    at module scope" for the flow-aware check, not for the
    definitely-bound calculation.
    """
    out: set[str] = set()
    for s in stmts:
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(s.name)
            continue
        out.update(_names_assigned_by(s))
        if isinstance(s, ast.If):
            out |= _bound_by_block_recursive(s.body)
            out |= _bound_by_block_recursive(s.orelse)
        elif isinstance(s, ast.Try):
            out |= _bound_by_block_recursive(s.body)
            for h in s.handlers:
                out |= _bound_by_block_recursive(h.body)
            out |= _bound_by_block_recursive(s.orelse)
            out |= _bound_by_block_recursive(s.finalbody)
        elif isinstance(s, (ast.For, ast.AsyncFor, ast.While)):
            out |= _bound_by_block_recursive(s.body)
            out |= _bound_by_block_recursive(s.orelse)
        elif isinstance(s, (ast.With, ast.AsyncWith)):
            out |= _bound_by_block_recursive(s.body)
    return out


def _collect_assigned_anywhere(tree: ast.Module) -> set[str]:
    """
    Every name assigned ANYWHERE at module scope, including inside
    conditional / try / for / while / with blocks — but NOT inside
    function or class bodies (those names are locals, not module-scope).

    Used to distinguish "the code clearly intends to bind this name
    (maybe-conditionally)" from "this name comes from somewhere else
    entirely (builtin, import *, etc.)". The bug shape we're guarding
    against is the FIRST case only.
    """
    out: set[str] = set()

    def _walk_module_scope(stmts: list[ast.stmt]) -> None:
        for s in stmts:
            # Don't descend into function/class bodies — but DO record
            # the def/class name itself (handled by _names_assigned_by).
            if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.add(s.name)
                continue
            out.update(_names_assigned_by(s))
            # Recurse into compound-statement bodies that share module
            # scope (if/try/for/while/with).
            if isinstance(s, ast.If):
                _walk_module_scope(s.body)
                _walk_module_scope(s.orelse)
            elif isinstance(s, ast.Try):
                _walk_module_scope(s.body)
                for h in s.handlers:
                    _walk_module_scope(h.body)
                _walk_module_scope(s.orelse)
                _walk_module_scope(s.finalbody)
            elif isinstance(s, (ast.For, ast.AsyncFor, ast.While)):
                _walk_module_scope(s.body)
                _walk_module_scope(s.orelse)
            elif isinstance(s, (ast.With, ast.AsyncWith)):
                _walk_module_scope(s.body)

    _walk_module_scope(tree.body)
    return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_app_module_parses():
    """Trivial smoke check — app.py must be syntactically valid Python."""
    assert APP_PY.is_file(), f"app.py not found at {APP_PY}"
    ast.parse(APP_PY.read_text(encoding="utf-8"))


def test_no_module_scope_reference_to_conditionally_bound_name():
    """
    Scan-all guard (replaces the earlier HISTORICAL_OFFENDERS enumerate-
    known-bad model). For every name READ at module scope and ASSIGNED
    somewhere at module scope, that name must EITHER be definitely-bound
    by the time the read executes OR the read must be co-located inside
    the same conditional block that performs the bind.

    The bug we're catching: a name bound inside an `if`/`try` block at
    module scope, but read OUTSIDE that block — when the conditional
    doesn't fire (or the handler swallows the exception), the name never
    binds and the outside read crashes import with NameError.

    To silence a genuine false positive, add the name + a one-line reason
    to WHITELIST above. NEVER silence a real bug this way — fix the code
    so the name is unconditionally bound first.
    """
    assert APP_PY.is_file(), f"app.py not found at {APP_PY}"
    tree = ast.parse(APP_PY.read_text(encoding="utf-8"))

    definitely_bound   = _collect_definitely_bound(tree)
    assigned_anywhere  = _collect_assigned_anywhere(tree)
    referenced_lines   = _collect_module_scope_references_with_lines(tree)
    conditional_ranges = _collect_conditional_bind_ranges(tree)

    # Suspect name set: bound somewhere at module scope but not on every
    # path AND has at least one reference at module scope.
    candidate_names = (
        set(referenced_lines) & assigned_anywhere
    ) - definitely_bound - set(WHITELIST)

    # Flow-aware filter: drop names whose ONLY module-scope references all
    # fall INSIDE a conditional block that itself binds the name (safe by
    # source order — bind precedes read within the same block). What's
    # left is references that escape every binding's enclosing block.
    truly_unsafe: dict[str, list[int]] = {}
    for name in candidate_names:
        bind_ranges = conditional_ranges.get(name, [])
        outside_refs = [
            ln for ln in referenced_lines.get(name, [])
            if not any(start <= ln <= end for (start, end) in bind_ranges)
        ]
        if outside_refs:
            truly_unsafe[name] = sorted(set(outside_refs))

    if not truly_unsafe:
        return  # all clean

    entry = entry_for(__file__)
    context = format_failure_context(entry)

    suspect_lines = "\n".join(
        f"  - {name} (read at line(s) {lines})"
        for name, lines in sorted(truly_unsafe.items())
    )
    raise AssertionError(
        "Module-level reference(s) to conditionally-assigned name(s) in "
        "app.py — same shape as the 2026-06-15 `unifi_resource_monitor` "
        "bug. When the conditional doesn't fire (or the except handler "
        "swallows the exception), these reads will hit `NameError` at "
        "import.\n"
        f"\n{suspect_lines}\n"
        "\n"
        "Fix options (in preference order):\n"
        "  1. Initialize the variable unconditionally BEFORE the "
        "conditional block (e.g. `x = None`).\n"
        "  2. Wrap the conditional so ALL branches bind the name (every "
        "if/elif has a matching else; every except handler also binds).\n"
        "  3. Move the read INSIDE the binding's enclosing conditional "
        "if it's only meaningful when the bind succeeded.\n"
        "  4. If the name truly should be invisible to the analyzer "
        "(e.g. it comes from `from X import *`), add it to WHITELIST in "
        "this test file with a one-line reason.\n"
        + context
    )
