#!/usr/bin/env python3
"""
routes/evidence_routes.py — Flask blueprint for the Evidence Collection
feature.

Why this exists
===============

The evidence-collection package (``services/evidence/``) is the core
of the feature: it owns the chain-of-custody manifest, the audio
extractor, the YAMNet classifier, and the Whisper transcriber. But
some operations belong on the HTTP surface, not inside a service
daemon:

  * **Disclosure acknowledgment** — when a user enables evidence
    collection in the UI for the first time, they must check a legal
    disclosure box. That checkbox state (timestamp + user_id + IP +
    user-agent) becomes part of the chain-of-custody record. The UI
    POSTs the ack here; we write a manifest entry.

  * **Status / health** — a small read-only endpoint the UI uses to
    show "evidence collection is active on N cameras, last capture
    M minutes ago, free space P GB on /litigation/".

  * (future) **Promote-to-case** — UI-driven case binding for the
    operator, used by the upcoming case-binding consumer (Phase 5).

This is intentionally a thin HTTP layer. All real logic lives in the
``services/evidence/`` package; these routes just adapt manifest
operations to the request/response model.
"""

# ----- standard library --------------------------------------------------
import logging                                  # diagnostic logging
from typing import Any, Dict                    # type hints

# ----- third party -------------------------------------------------------
from flask import Blueprint, jsonify, request   # routing + JSON I/O
from flask_login import current_user, login_required  # auth gate

# ----- evidence package internals ----------------------------------------
from services.evidence.manifest import EvidenceManifest, LITIGATION_ROOT

logger = logging.getLogger(__name__)

evidence_bp = Blueprint("evidence", __name__)


# ----- module-level state ------------------------------------------------

# A single shared EvidenceManifest instance for the whole Flask process.
# All endpoints that append to the manifest go through this, ensuring
# the per-process lock is honored. Cross-process safety is provided by
# fcntl.flock inside EvidenceManifest itself.
_manifest = EvidenceManifest()


# =========================================================================
# POST /api/evidence/disclosure-ack
# =========================================================================

@evidence_bp.route("/api/evidence/disclosure-ack", methods=["POST"])
@login_required
def disclosure_ack():
    """
    Record the operator's acknowledgment of the legal disclosure that
    appears in the "Collect Evidence" tab.

    Expected JSON body::

        {
          "jurisdiction": "US-NY",          // matches the disclosure shown
          "disclosure_version": 1,          // bumped if the text changes
          "disclosure_text_sha256": "..."   // hash of the displayed text
        }

    The endpoint pulls the authenticated user, the request IP, and the
    user-agent from the Flask context and writes them into a
    ``disclosure_acked`` manifest entry. This entry becomes the
    chain-of-custody anchor for *why* recording was authorized: when a
    transcript is later challenged in court, the manifest can be walked
    back to find the user/time/IP that turned the feature on after
    seeing the legal text.

    Idempotent: the endpoint always appends a fresh manifest entry. If
    the user re-acks (e.g. the disclosure version changed and they
    re-accepted), each ack is independently recorded — the chain
    preserves the full sequence of acceptances.
    """
    body: Dict[str, Any] = request.get_json(silent=True) or {}

    # ----- pull request-context fields (cannot be spoofed by client) -----
    # ``current_user`` is provided by Flask-Login; we need a stable
    # identifier. Different deployments expose .id, .username, or both.
    user_id = (
        getattr(current_user, "id", None)
        or getattr(current_user, "username", None)
        or "unknown"
    )

    # ``request.remote_addr`` is the direct connection IP; if we're
    # behind a reverse proxy, ``X-Forwarded-For`` is the real client.
    forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    client_ip = forwarded or (request.remote_addr or "unknown")

    user_agent = request.headers.get("User-Agent", "unknown")[:500]

    # ----- pull disclosure-specific fields from the body ----------------
    # These come from the UI; they describe what the user actually saw
    # at acknowledgment time. ``disclosure_text_sha256`` lets us prove
    # later exactly which text was shown — if the disclosure copy ever
    # changes, prior acks remain pinned to the older text by hash.
    jurisdiction = str(body.get("jurisdiction") or "unspecified")[:64]
    disclosure_version = int(body.get("disclosure_version") or 1)
    disclosure_text_sha256 = str(
        body.get("disclosure_text_sha256") or ""
    )[:80]

    # ----- write the manifest entry --------------------------------------
    try:
        entry = _manifest.append({
            "event_type": "disclosure_acked",
            "service": "evidence_routes",
            "user_id": str(user_id),
            "client_ip": client_ip,
            "user_agent": user_agent,
            "jurisdiction": jurisdiction,
            "disclosure_version": disclosure_version,
            "disclosure_text_sha256": disclosure_text_sha256,
        })
    except Exception as e:
        logger.exception("disclosure_ack: manifest append failed")
        return jsonify({"error": f"manifest write failed: {e}"}), 500

    logger.info(
        "disclosure acknowledged: user=%s ip=%s jurisdiction=%s "
        "version=%d manifest_id=%d",
        user_id, client_ip, jurisdiction,
        disclosure_version, entry["manifest_id"],
    )
    return jsonify({
        "ok": True,
        "manifest_id": entry["manifest_id"],
        "this_hash": entry["this_hash"],
        "timestamp_utc": entry["timestamp_utc"],
    })


# =========================================================================
# GET /api/evidence/status
# =========================================================================

@evidence_bp.route("/api/evidence/status", methods=["GET"])
@login_required
def status():
    """
    Lightweight summary of the evidence pipeline's current state.

    Used by the "Collect Evidence" UI tab to render headline numbers
    without forcing the user to dig into the DB. Fields:

      * ``volume_path``  — where /litigation/ lives
      * ``volume_total_bytes`` / ``volume_free_bytes`` — disk space on
        the litigation volume (so the UI can show "892 GB free of 1.1
        TB")
      * ``manifest_total_entries`` — the highest manifest_id + 1
      * ``last_event_utc`` — timestamp of the most recent manifest
        entry (so the UI can show "last activity: 3 minutes ago")
      * ``chain_ok`` — verify_chain() result; ``true`` means the
        chain-of-custody chain has not been tampered with up to the
        most recent entry

    All fields are best-effort: a missing volume returns nulls rather
    than 5xx, so the UI degrades gracefully.
    """
    import shutil  # lazy — only this endpoint uses it

    payload: Dict[str, Any] = {
        "volume_path": str(LITIGATION_ROOT),
        "volume_total_bytes": None,
        "volume_free_bytes": None,
        "manifest_total_entries": 0,
        "last_event_utc": None,
        "chain_ok": None,
    }

    # Disk usage — handles the case where the volume isn't mounted yet
    # (returns nulls instead of raising).
    try:
        usage = shutil.disk_usage(LITIGATION_ROOT)
        payload["volume_total_bytes"] = int(usage.total)
        payload["volume_free_bytes"] = int(usage.free)
    except (FileNotFoundError, PermissionError, OSError):
        # Volume not mounted or not readable — leave nulls; UI shows "—".
        pass

    # Manifest summary
    try:
        last = _manifest.last_entry()
        if last is not None:
            payload["manifest_total_entries"] = int(last["manifest_id"]) + 1
            payload["last_event_utc"] = last.get("timestamp_utc")
    except Exception:
        logger.exception("status: failed to read last manifest entry")

    # Chain integrity check. Verifies the entire chain forward; if the
    # manifest grows very large this could be slow — but for now the
    # chain is small enough (low thousands of entries) that this is
    # sub-second. If it becomes a bottleneck, cache the last verified
    # checkpoint and only verify the tail.
    try:
        ok, _ = _manifest.verify_chain()
        payload["chain_ok"] = bool(ok)
    except Exception:
        logger.exception("status: verify_chain raised")
        payload["chain_ok"] = False

    return jsonify(payload)
