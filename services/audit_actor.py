"""
services/audit_actor.py — apply Flask-stashed audit actor IDs to a
psycopg2 cursor's session.

Phase 2 trigger-based audit (2026-05-13). The Postgres trigger function
audit_setting_change() reads the actor identity via:
    current_setting('audit.user_id',   true)
    current_setting('audit.client_id', true)
    current_setting('audit.origin',    true)

These GUCs only have a value for the current transaction if the handler
called `SET LOCAL audit.* = ...` first. This helper is the single line
each direct-psycopg2 mutating endpoint should call right after opening
its cursor:

    cur.execute("UPDATE cameras SET ...")
                ^
                # right before this, do:
                from services.audit_actor import apply_audit_actor
                apply_audit_actor(cur)

If the request is not a mutation, or there's no Flask context, the call
is a no-op. Cheap, safe, never raises.

Without this call, the trigger still writes an audit row — just with
NULL user_id/client_id. The change is fully captured (WHAT/WHEN/WHERE);
only the WHO is missing. So forgetting to call apply_audit_actor() in a
new endpoint degrades gracefully, doesn't break.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def apply_audit_actor(cur) -> None:
    """
    Apply Flask `g.audit_*` values to the current psycopg2 transaction
    via SET LOCAL. Safe to call from any request context — no-op outside
    a Flask request.

    Args:
        cur: an open psycopg2 cursor on the SAME connection that will
             perform the mutating UPDATE/INSERT. SET LOCAL is
             transaction-scoped, so the connection used must be inside
             a transaction (default for psycopg2 unless autocommit=True).
    """
    try:
        from flask import has_request_context, g
        if not has_request_context():
            return
        uid    = getattr(g, 'audit_user_id', None)
        cid    = getattr(g, 'audit_client_id', None)
        origin = getattr(g, 'audit_origin', None)

        # Only set the keys that have values; SET LOCAL with empty
        # strings is harmless but noisy.
        if uid is not None:
            cur.execute("SELECT set_config('audit.user_id', %s, true)", (str(uid),))
        if cid:
            cur.execute("SELECT set_config('audit.client_id', %s, true)", (cid,))
        if origin:
            cur.execute("SELECT set_config('audit.origin', %s, true)", (origin,))
    except Exception:
        # Audit-actor stashing must never break the originating request.
        logger.exception("apply_audit_actor: failed (continuing)")
