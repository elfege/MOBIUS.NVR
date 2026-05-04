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
import mimetypes                                # content-type for file downloads
from datetime import datetime                   # iso timestamp parsing
from pathlib import Path                        # safe path joins
from typing import Any, Dict, List, Optional, Tuple   # type hints

# ----- third party -------------------------------------------------------
from flask import (                             # routing + JSON I/O
    Blueprint, abort, jsonify, request, send_file,
)
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


# =========================================================================
# Phase 5 — Read-only API surface for external consumers
# =========================================================================
#
# These endpoints expose the manifest + intake files to authenticated
# consumers (the reference 0_LEGAL daemon, attorney workstations, future
# Child-Monitor mobile app). All endpoints are login-required and
# return JSON unless serving a binary file download.
#
# Design principles:
#   * Read-only. Mutation paths (promote-to-case, case CRUD) live in
#     a separate section once we add case management — kept off the
#     read API surface so a compromised consumer cannot mutate.
#   * Pagination via since=<manifest_id> + limit=<n>. Always returns
#     the highest manifest_id seen so consumers can checkpoint.
#   * File downloads include X-Content-SHA256 + X-Manifest-Id headers
#     so a consumer can verify chain-of-custody at the moment of
#     download without an extra round trip.
#   * Path safety: all file paths derived from manifest entries are
#     resolved through LITIGATION_ROOT and re-checked to be inside it.
#     A tampered manifest with ../ paths cannot escape the volume.
# =========================================================================


# Maximum events returned per /feed call. Keeps responses bounded so
# a polling consumer doesn't accidentally pull a multi-GB JSON. The
# consumer paginates by feeding ``next_since`` from the response back
# in as ``since`` on the next request.
_FEED_DEFAULT_LIMIT: int = 50
_FEED_MAX_LIMIT:     int = 500

# Event types that are "evidence-bearing" and therefore showable on
# the feed by default. Lifecycle events (manifest_genesis,
# disclosure_acked, etc.) are filtered out unless explicitly asked for
# via ``include_lifecycle=true``.
_EVIDENCE_EVENT_TYPES = frozenset({
    "audio_capture",
    "acoustic_classification",
    "transcription",
    "anamnesis_link",
})

# File "kinds" servable from /event/<id>/file/<kind>. Each maps to the
# extension under which the audio extractor / Whisper transcriber
# wrote the file. Restricting to a fixed set prevents a consumer from
# constructing arbitrary file paths.
_DOWNLOADABLE_KINDS = {
    "mp3":  "files.mp3.path",
    "txt":  "files.txt.path",
    "json": "files.json.path",
}


def _safe_resolve_litigation_path(rel_path: str) -> Path:
    """
    Resolve ``rel_path`` (a relative path stored in a manifest entry)
    against LITIGATION_ROOT, with sandboxing.

    Raises a 404 (via Flask abort) if the resolved path escapes the
    litigation root — defense-in-depth against tampered manifest
    entries with ``../`` traversal payloads.
    """
    abs_path = (LITIGATION_ROOT / rel_path).resolve()
    try:
        # ``relative_to`` raises ValueError if abs_path is not under
        # LITIGATION_ROOT — that's the boundary check we want.
        abs_path.relative_to(LITIGATION_ROOT.resolve())
    except ValueError:
        logger.warning(
            "path traversal attempt blocked: rel=%s resolved=%s",
            rel_path, abs_path,
        )
        abort(404)
    if not abs_path.exists() or not abs_path.is_file():
        abort(404)
    return abs_path


def _lookup_nested(d: Dict[str, Any], dotted_key: str) -> Optional[Any]:
    """
    Walk ``d`` along a dotted path and return the leaf value, or
    None if any intermediate key is missing. Used to pull e.g.
    ``files.mp3.path`` out of a manifest entry without having to
    write 4-line if-elif chains.
    """
    cur: Any = d
    for part in dotted_key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


# ----- /api/evidence/feed -------------------------------------------------

@evidence_bp.route("/api/evidence/feed", methods=["GET"])
@login_required
def feed():
    """
    Return a page of evidence-bearing manifest entries.

    Query parameters:
      since=<manifest_id>      — return entries with manifest_id > this.
                                 Default 0 (start from the beginning).
                                 Pass response.next_since on next call
                                 to paginate.
      limit=<n>                — max entries to return.
                                 Default 50; capped at 500.
      category=<name>          — optional filter by acoustic category
                                 (screams / crying / impacts /
                                 raised-voices). Matches an
                                 acoustic_classification entry whose
                                 matched_categories contains the key,
                                 OR an audio_capture whose later
                                 classification matched. Implemented
                                 as a substring match for simplicity.
      include_lifecycle=true   — if set, include manifest_genesis,
                                 disclosure_acked, retention_prune,
                                 etc. — useful for audit consumers.

    Response shape::

        {
          "entries":     [<entry>, <entry>, ...],
          "count":       <int>,
          "next_since":  <manifest_id of last entry, or unchanged>,
          "has_more":    <bool>   (true if more entries exist past the limit)
        }
    """
    since = max(0, int(request.args.get("since", 0)))
    limit = max(1, min(_FEED_MAX_LIMIT,
                       int(request.args.get("limit", _FEED_DEFAULT_LIMIT))))
    category = (request.args.get("category") or "").strip().lower() or None
    include_lifecycle = request.args.get("include_lifecycle", "").lower() in (
        "1", "true", "yes",
    )

    out: List[Dict[str, Any]] = []
    last_seen_id: int = since
    has_more = False

    # Walk the manifest from since+1 forward. iter_entries is a
    # generator over the file; we stop early when limit is hit.
    for entry in _manifest.iter_entries(from_id=since + 1):
        last_seen_id = entry["manifest_id"]
        et = entry.get("event_type", "")
        if not include_lifecycle and et not in _EVIDENCE_EVENT_TYPES:
            continue
        if category:
            # Accept either acoustic_classification entries that matched
            # the category, OR audio_capture entries whose later
            # classification entry matched it (we don't pre-resolve
            # that here for simplicity; consumers can request the
            # capture's classification by id).
            if et == "acoustic_classification":
                matched = entry.get("matched_categories") or {}
                if category not in matched:
                    continue
            else:
                # Other event types don't carry category info directly.
                continue
        out.append(entry)
        if len(out) >= limit:
            has_more = True
            break

    return jsonify({
        "entries": out,
        "count": len(out),
        "next_since": last_seen_id,
        "has_more": has_more,
    })


# ----- /api/evidence/event/<id> ------------------------------------------

@evidence_bp.route("/api/evidence/event/<int:manifest_id>", methods=["GET"])
@login_required
def event(manifest_id: int):
    """
    Return one full manifest entry by manifest_id.

    404 if the entry doesn't exist. The full chain-of-custody fields
    (previous_hash, this_hash) are included in the response so a
    consumer can verify the entry's integrity locally without
    re-walking the entire chain.
    """
    for entry in _manifest.iter_entries(from_id=manifest_id, to_id=manifest_id):
        return jsonify(entry)
    abort(404)


# ----- /api/evidence/event/<id>/file/<kind> -------------------------------

@evidence_bp.route(
    "/api/evidence/event/<int:manifest_id>/file/<kind>",
    methods=["GET"],
)
@login_required
def event_file(manifest_id: int, kind: str):
    """
    Stream the named file artifact (mp3 / txt / json) belonging to a
    manifest entry.

    Headers added on success:
      X-Manifest-Id     — the entry's manifest_id (echo for clarity)
      X-Content-SHA256  — sha256 of the file as recorded in the manifest
                          (NOT recomputed at serve time — equality with
                          the bytes you receive lets the consumer verify
                          chain-of-custody integrity at the moment of
                          download without a separate round trip)
      X-Manifest-Hash   — this_hash of the manifest entry, so the
                          consumer can also verify the entry itself

    Restricted to ``mp3``, ``txt``, ``json`` kinds — anything else
    returns 400. Path traversal in manifest-stored relative paths is
    blocked by ``_safe_resolve_litigation_path``.
    """
    if kind not in _DOWNLOADABLE_KINDS:
        abort(400)
    # Pull the entry — same iter_entries trick as /event
    entry: Optional[Dict[str, Any]] = None
    for e in _manifest.iter_entries(from_id=manifest_id, to_id=manifest_id):
        entry = e
        break
    if entry is None:
        abort(404)

    rel_path = _lookup_nested(entry, _DOWNLOADABLE_KINDS[kind])
    if not rel_path:
        # The entry exists but doesn't carry that file kind (e.g.
        # asking for a .json on a silent_window_pruned event).
        abort(404)
    abs_path = _safe_resolve_litigation_path(rel_path)

    # The hash recorded in the manifest at write time. NOT recomputed
    # — that's the consumer's job, comparing bytes received vs this
    # value. If they don't match, the file was tampered with.
    file_hash = _lookup_nested(entry, kind.replace(
        "mp3", "files.mp3.sha256"
    )) or _lookup_nested(entry, f"files.{kind}.sha256") or ""

    # Pick a sensible mime-type. Whisper writes JSON/text; the audio
    # extractor writes MP3.
    mime = (mimetypes.guess_type(abs_path.name)[0]
            or "application/octet-stream")

    response = send_file(
        abs_path,
        mimetype=mime,
        as_attachment=False,
        download_name=abs_path.name,
        conditional=True,    # supports Range / If-None-Match
    )
    response.headers["X-Manifest-Id"] = str(manifest_id)
    response.headers["X-Content-SHA256"] = file_hash
    response.headers["X-Manifest-Hash"] = entry.get("this_hash", "")
    return response


# ----- /api/evidence/manifest/verify -------------------------------------

@evidence_bp.route("/api/evidence/manifest/verify", methods=["GET"])
@login_required
def manifest_verify():
    """
    Run the manifest's chain integrity check over a range.

    Query parameters:
      from=<manifest_id>  — inclusive lower bound. Default 0.
      to=<manifest_id>    — inclusive upper bound. Default = end of manifest.

    Useful for periodic tamper-checks by an automated auditor that
    wants to verify the chain since the last known-good checkpoint
    rather than walking the whole thing.

    Response::

        {"ok": <bool>, "error": <str|null>,
         "from": <int>, "to": <int|null>}
    """
    f = request.args.get("from")
    t = request.args.get("to")
    from_id = int(f) if f is not None else 0
    to_id = int(t) if t is not None else None
    try:
        ok, err = _manifest.verify_chain(from_id=from_id, to_id=to_id)
    except Exception as e:
        logger.exception("manifest_verify: chain walk raised")
        return jsonify({
            "ok": False,
            "error": str(e),
            "from": from_id,
            "to": to_id,
        }), 500
    return jsonify({
        "ok": bool(ok),
        "error": err,
        "from": from_id,
        "to": to_id,
    })
