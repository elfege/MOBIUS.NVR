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
    Blueprint, abort, jsonify, render_template, request, send_file,
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
# GET /evidence — Collected Data browse page
# =========================================================================
#
# User-facing label is "Collected Data" (less legally loaded than
# "Evidence"). The route stays /evidence so it matches the existing
# /api/evidence/* contract; only the UI labels change. The page is a
# thick client over the existing JSON endpoints and pulls all its data
# via fetch — this handler just serves the HTML shell.

@evidence_bp.route("/evidence", methods=["GET"])
@login_required
def collected_data_page():
    """Render the Collected Data browse page (KPIs + feed + cases)."""
    return render_template("collected.html")


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
    # The manifest is the tamper-evident chain-of-custody record. Append
    # FIRST: if this fails, no DB row should exist (a DB row without a
    # corresponding manifest line would be a record without a
    # cryptographic anchor — useless for legal purposes).
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

    # ----- write the DB row ---------------------------------------------
    # Pair the manifest line with a queryable DB row in
    # ``evidence_disclosure_acks``. The DB row carries manifest_id +
    # manifest_hash so an auditor can re-verify the linkage at any time.
    # Done via PostgREST (same pattern as the rest of this blueprint).
    #
    # If the DB write fails the manifest line still exists — that is
    # acceptable: the manifest is the legal record of record. We log
    # loudly and return 502 so the UI can prompt the user to retry; on
    # retry, a fresh manifest entry is appended (the chain preserves the
    # full sequence) and a matching DB row is written.
    import requests as _requests  # lazy — only this endpoint
    db_payload = {
        "user_id":                str(user_id),
        "client_ip":              client_ip,
        "user_agent":             user_agent,
        "jurisdiction":           jurisdiction,
        "disclosure_version":     disclosure_version,
        "disclosure_text_sha256": disclosure_text_sha256,
        "manifest_id":            int(entry["manifest_id"]),
        "manifest_hash":          str(entry["this_hash"]),
    }
    try:
        r = _requests.post(
            f"{_shared_settings_postgrest_url()}/evidence_disclosure_acks",
            json=db_payload,
            headers={"Prefer": "return=representation"},
            timeout=10,
        )
        if not r.ok:
            logger.error(
                "disclosure_ack: DB insert failed (manifest_id=%d): HTTP %s %s",
                entry["manifest_id"], r.status_code, r.text[:300],
            )
            return jsonify({
                "error": "DB insert failed; manifest entry was written. "
                         "Re-submit to retry.",
                "manifest_id": entry["manifest_id"],
            }), 502
    except Exception as e:
        logger.exception(
            "disclosure_ack: DB insert raised (manifest_id=%d)",
            entry["manifest_id"],
        )
        return jsonify({
            "error": f"DB insert raised: {e}; manifest entry was written. "
                     "Re-submit to retry.",
            "manifest_id": entry["manifest_id"],
        }), 502

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
        # Latest disclosure ack for the *currently authenticated user*.
        # The UI reads this to pre-check the disclosure box when the
        # user has previously acked the same jurisdiction+version+hash.
        # ``null`` if the user has never acked anything.
        "last_disclosure_ack": None,
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

    # ----- latest disclosure ack for the current user -------------------
    # Read the most recent row from evidence_disclosure_acks for whoever
    # is logged in. The UI uses this to render the disclosure checkbox
    # in its previously-acked state on page load — so a user who acked
    # US-NY-v1 on Monday sees the box pre-checked on Tuesday (assuming
    # they still have US-NY selected; switching jurisdiction or bumping
    # the disclosure version will leave the box unchecked, forcing a
    # fresh ack of the new text).
    try:
        user_id = (
            getattr(current_user, "id", None)
            or getattr(current_user, "username", None)
            or "unknown"
        )
        import requests as _requests  # lazy
        url = (
            f"{_shared_settings_postgrest_url()}/evidence_disclosure_acks"
            f"?user_id=eq.{str(user_id)}"
            f"&order=acked_at.desc&limit=1"
        )
        r = _requests.get(url, timeout=5)
        if r.ok:
            rows = r.json()
            if rows:
                a = rows[0]
                payload["last_disclosure_ack"] = {
                    "jurisdiction":           a.get("jurisdiction"),
                    "disclosure_version":     a.get("disclosure_version"),
                    "disclosure_text_sha256": a.get("disclosure_text_sha256"),
                    "manifest_id":            a.get("manifest_id"),
                    "manifest_hash":          a.get("manifest_hash"),
                    "acked_at":               a.get("acked_at"),
                }
    except Exception:
        # Best-effort — UI degrades gracefully (box stays unchecked).
        logger.exception("status: failed to read last disclosure ack")

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


# =========================================================================
# Phase 5b — Cases CRUD + promote-to-case
# =========================================================================
#
# Cases are how evidence-bearing events get bound to a real-world legal
# matter. The pipeline itself is intentionally case-agnostic — it just
# records events. Cases live in the ``evidence_cases`` table (created
# by migration 027) and an event becomes "promoted" to a case when its
# ``audio_events.case_id`` is set.
#
# Why these endpoints exist on Flask (not just PostgREST direct):
#   1. ``GET /cases/<id>/events`` filters by the case's stored
#      predicates JSONB; that filtering logic is non-trivial and we
#      want it in Python, not in a tangle of PostgREST query string
#      operators.
#   2. ``POST /cases/<id>/promote`` is a multi-row UPDATE plus a
#      manifest write (chain-of-custody record of the promotion). It
#      would be awkward to express atomically through PostgREST, and
#      we want the operator action recorded in the manifest.
#   3. CRUD on the ``cases`` table itself goes through Flask too for
#      consistency and so the entire mutation surface for evidence
#      lives behind ``login_required``.
#
# Predicate schema (stored in ``evidence_cases.predicates``):
#   {
#     "cameras":    ["serial1", "serial2"],   // optional
#     "categories": ["screams", "crying"],    // optional
#     "after":      "2026-01-01T00:00:00Z",   // optional iso lower bound
#     "before":     "2027-01-01T00:00:00Z"    // optional iso upper bound
#   }
# All fields optional. An event matches iff every present predicate
# matches it. Empty predicates match everything (use sparingly).
# =========================================================================


def _shared_settings_postgrest_url() -> str:
    """The PostgREST URL that the Flask process uses for DB queries."""
    import os
    return os.environ.get("NVR_POSTGREST_URL") or "http://postgrest:3001"


def _event_matches_predicates(
    event: Dict[str, Any],
    predicates: Dict[str, Any],
) -> bool:
    """
    Return True if ``event`` matches every present predicate in
    ``predicates``. Used by GET /cases/<id>/events.

    Event format here is a row from ``audio_events`` (already a join-
    friendly index over the manifest). Camera + category + timestamp
    predicates map directly to columns.
    """
    cams = predicates.get("cameras")
    if cams and event.get("camera_serial") not in cams:
        return False
    cats = predicates.get("categories")
    if cats and event.get("primary_label") not in cats:
        return False
    # Time bounds: event.timestamp_utc is an ISO string from postgres,
    # we string-compare iso timestamps (lexicographic equals temporal
    # ordering for fixed-format ISO 8601).
    after = predicates.get("after")
    if after and (event.get("timestamp_utc") or "") < after:
        return False
    before = predicates.get("before")
    if before and (event.get("timestamp_utc") or "") > before:
        return False
    return True


# ----- POST /api/evidence/cases ------------------------------------------

@evidence_bp.route("/api/evidence/cases", methods=["POST"])
@login_required
def create_case():
    """
    Register a new case.

    Body::

        {
          "name":        "Marital — 2026",
          "consumer_id": "0_LEGAL/0_MARITAL",
          "predicates":  { "cameras": [...], "categories": [...] }
        }

    Returns the created row with its assigned id.
    """
    import requests
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    consumer_id = (body.get("consumer_id") or "").strip()
    if not consumer_id:
        return jsonify({"error": "consumer_id is required"}), 400
    predicates = body.get("predicates") or {}
    if not isinstance(predicates, dict):
        return jsonify({"error": "predicates must be an object"}), 400

    payload = {
        "name": name,
        "consumer_id": consumer_id,
        "predicates": predicates,
    }
    r = requests.post(
        f"{_shared_settings_postgrest_url()}/evidence_cases",
        json=payload,
        headers={"Prefer": "return=representation"},
        timeout=10,
    )
    if not r.ok:
        return jsonify({"error": f"DB insert failed: {r.text[:300]}"}), 502
    rows = r.json()
    new_row = rows[0] if rows else None

    # Record the case creation in the manifest (chain-of-custody).
    if new_row:
        try:
            _manifest.append({
                "event_type": "case_created",
                "service": "evidence_routes",
                "case_id": new_row["id"],
                "case_name": new_row["name"],
                "consumer_id": new_row["consumer_id"],
                "predicates": new_row.get("predicates") or {},
                "created_by_user_id": str(
                    getattr(current_user, "id", None)
                    or getattr(current_user, "username", None)
                    or "unknown"
                ),
            })
        except Exception:
            logger.exception("create_case: manifest append failed")

    return jsonify(new_row), 201


# ----- GET /api/evidence/cases -------------------------------------------

@evidence_bp.route("/api/evidence/cases", methods=["GET"])
@login_required
def list_cases():
    """
    List cases. Optional query: ``include_archived=true`` to also
    return cases with archived_at IS NOT NULL (default: only active).
    """
    import requests
    include_archived = request.args.get("include_archived", "").lower() in (
        "1", "true", "yes",
    )
    url = f"{_shared_settings_postgrest_url()}/evidence_cases?select=*&order=created_at.desc"
    if not include_archived:
        url += "&archived_at=is.null"
    r = requests.get(url, timeout=10)
    if not r.ok:
        return jsonify({"error": f"DB query failed: {r.text[:300]}"}), 502
    return jsonify(r.json())


# ----- GET /api/evidence/cases/<id> --------------------------------------

@evidence_bp.route("/api/evidence/cases/<int:case_id>", methods=["GET"])
@login_required
def get_case(case_id: int):
    """Return one case by id, or 404."""
    import requests
    r = requests.get(
        f"{_shared_settings_postgrest_url()}/evidence_cases"
        f"?id=eq.{case_id}&select=*",
        timeout=10,
    )
    if not r.ok:
        return jsonify({"error": f"DB query failed: {r.text[:300]}"}), 502
    rows = r.json()
    if not rows:
        abort(404)
    return jsonify(rows[0])


# ----- PATCH /api/evidence/cases/<id> ------------------------------------

@evidence_bp.route("/api/evidence/cases/<int:case_id>", methods=["PATCH"])
@login_required
def patch_case(case_id: int):
    """
    Update a case. Whitelisted fields: name, predicates, archived_at.

    To archive: PATCH with {"archived_at": "<iso>"}.
    To reopen:  PATCH with {"archived_at": null}.
    """
    import requests
    body = request.get_json(silent=True) or {}
    allowed = {}
    for k in ("name", "predicates", "archived_at"):
        if k in body:
            allowed[k] = body[k]
    if not allowed:
        return jsonify({"error": "no whitelisted fields in body"}), 400

    r = requests.patch(
        f"{_shared_settings_postgrest_url()}/evidence_cases?id=eq.{case_id}",
        json=allowed,
        headers={"Prefer": "return=representation"},
        timeout=10,
    )
    if not r.ok:
        return jsonify({"error": f"DB update failed: {r.text[:300]}"}), 502
    rows = r.json()
    if not rows:
        abort(404)
    return jsonify(rows[0])


# ----- GET /api/evidence/cases/<id>/events -------------------------------

@evidence_bp.route("/api/evidence/cases/<int:case_id>/events", methods=["GET"])
@login_required
def case_events(case_id: int):
    """
    Return all audio_events whose row matches this case's predicates,
    plus all events already promoted to this case.

    The "matches predicates" set may be larger than the "promoted" set
    — predicates describe what the case is INTERESTED in; promotion is
    a deliberate user action that says "this event IS evidence for the
    case." UI typically shows both, distinguished by whether
    ``promoted_at`` is set.
    """
    import requests

    # 1) load the case to get its predicates
    case_r = requests.get(
        f"{_shared_settings_postgrest_url()}/evidence_cases"
        f"?id=eq.{case_id}&select=*",
        timeout=10,
    )
    if not case_r.ok:
        return jsonify({"error": f"case lookup failed: {case_r.text[:300]}"}), 502
    case_rows = case_r.json()
    if not case_rows:
        abort(404)
    case = case_rows[0]
    predicates = case.get("predicates") or {}

    # 2) load all audio_events (the full table is fine — index is small;
    # if it grows large, add a pre-filter on case_id and predicate
    # columns and merge in Python).
    ev_r = requests.get(
        f"{_shared_settings_postgrest_url()}/audio_events"
        f"?select=*&order=timestamp_utc.desc",
        timeout=20,
    )
    if not ev_r.ok:
        return jsonify({"error": f"events lookup failed: {ev_r.text[:300]}"}), 502
    all_events = ev_r.json()

    # 3) bucket each event into matching / promoted
    matching: List[Dict[str, Any]] = []
    promoted: List[Dict[str, Any]] = []
    for e in all_events:
        is_promoted = e.get("case_id") == case_id
        is_match = _event_matches_predicates(e, predicates)
        if is_promoted:
            promoted.append(e)
        elif is_match:
            matching.append(e)
    return jsonify({
        "case_id": case_id,
        "promoted": promoted,
        "matching_unpromoted": matching,
    })


# ----- POST /api/evidence/cases/<id>/promote -----------------------------

@evidence_bp.route(
    "/api/evidence/cases/<int:case_id>/promote",
    methods=["POST"],
)
@login_required
def promote_events(case_id: int):
    """
    Mark a list of audio_events as promoted to this case.

    Body::

        {"manifest_ids": [12834, 12835, 12840]}

    For each id we PATCH ``audio_events`` setting ``case_id`` and
    ``promoted_at = now()``, and we append a ``case_promotion``
    manifest entry naming all promoted ids in one entry (so the
    chain-of-custody record reflects this as one operator action,
    not N individual mutations).
    """
    import requests
    from datetime import datetime as _dt, timezone as _tz

    body = request.get_json(silent=True) or {}
    ids = body.get("manifest_ids")
    if not isinstance(ids, list) or not ids or not all(
        isinstance(i, int) for i in ids
    ):
        return jsonify({"error": "manifest_ids must be a non-empty list of ints"}), 400

    # First, verify the case exists.
    case_r = requests.get(
        f"{_shared_settings_postgrest_url()}/evidence_cases"
        f"?id=eq.{case_id}&select=id",
        timeout=10,
    )
    if not case_r.ok or not case_r.json():
        abort(404)

    # PATCH all matching audio_events rows in one PostgREST call by
    # using the IN filter. Atomic from PostgREST's perspective.
    iso_now = _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    in_filter = "in.(" + ",".join(str(i) for i in ids) + ")"
    patch_url = (f"{_shared_settings_postgrest_url()}/audio_events"
                 f"?manifest_id={in_filter}")
    p = requests.patch(
        patch_url,
        json={"case_id": case_id, "promoted_at": iso_now},
        headers={"Prefer": "return=representation"},
        timeout=15,
    )
    if not p.ok:
        return jsonify({"error": f"promote PATCH failed: {p.text[:300]}"}), 502
    updated = p.json()

    # Manifest entry — one event for the whole batch.
    try:
        _manifest.append({
            "event_type": "case_promotion",
            "service": "evidence_routes",
            "case_id": case_id,
            "promoted_audio_capture_manifest_ids": ids,
            "promoted_count": len(updated),
            "promoted_at_utc": iso_now,
            "promoted_by_user_id": str(
                getattr(current_user, "id", None)
                or getattr(current_user, "username", None)
                or "unknown"
            ),
            "client_ip": (
                request.headers.get("X-Forwarded-For", "")
                .split(",")[0].strip()
                or request.remote_addr or "unknown"
            ),
        })
    except Exception:
        logger.exception("promote_events: manifest append failed")

    return jsonify({
        "case_id": case_id,
        "promoted_count": len(updated),
        "promoted_at_utc": iso_now,
        "rows": updated,
    })


# =========================================================================
# UI-facing settings endpoints (Phase 4 wiring)
# =========================================================================
#
# The 'Collect Evidence' tab in the global settings UI needs to read +
# write the per-camera evidence_camera_settings table. We expose these
# operations as Flask routes (rather than letting the browser hit
# PostgREST directly) because:
#
#   * Nginx-edge has no /pgrest proxy by design — keeping PostgREST
#     hidden from clients reduces attack surface and avoids exposing
#     the whole DB query language to the browser.
#   * The UI needs a CAMERA + SETTINGS join, which is awkward to do
#     from the client side; one server-side endpoint is cleaner.
#   * Login_required guards every read/write, matching the rest of
#     the evidence API.
# =========================================================================


# ----- GET /api/evidence/cameras -----------------------------------------

@evidence_bp.route("/api/evidence/cameras", methods=["GET"])
@login_required
def cameras_with_settings():
    """
    Return audio-capable cameras (cameras.audio_input_supported = TRUE)
    joined with their evidence_camera_settings row (or a default shape
    if no row exists yet).

    Response::

        [
          {
            "serial":   "T8419P0024110C6A",
            "name":     "KITCHEN OFFICE",
            "audio_input_supported": true,
            "settings": {
              "enabled":               false,
              "capture_video":         true,
              "capture_audio":         true,
              "silence_db_threshold":  -40,
              "retention_days":        365,
              "classifier_categories": [...]
            }
          },
          ...
        ]

    The "settings" sub-object is always present. If the camera doesn't
    yet have a row in evidence_camera_settings, we return the schema
    defaults so the UI can render checkboxes consistently — the row
    will be INSERTed on first save.
    """
    import requests
    base = _shared_settings_postgrest_url()

    cams_r = requests.get(
        f"{base}/cameras?audio_input_supported=eq.true"
        f"&select=serial,name,audio_input_supported"
        f"&order=name.asc",
        timeout=10,
    )
    if not cams_r.ok:
        return jsonify({"error": f"cameras query failed: {cams_r.text[:300]}"}), 502
    cams = cams_r.json()

    settings_r = requests.get(
        f"{base}/evidence_camera_settings?select=*",
        timeout=10,
    )
    if not settings_r.ok:
        return jsonify({"error": f"settings query failed: {settings_r.text[:300]}"}), 502
    settings_by_serial = {s["serial"]: s for s in settings_r.json()}

    # Default shape — must match the column defaults declared in
    # migration 027. If migration 027 changes those defaults, update
    # here too (or refactor to read DEFAULTs from a shared module).
    DEFAULTS = {
        "enabled": False,
        "capture_video": True,
        "capture_audio": True,
        "silence_db_threshold": -40.0,
        "classifier_categories": [
            "screams", "crying", "impacts", "raised-voices",
        ],
        "retention_days": 365,
    }

    out = []
    for cam in cams:
        s = settings_by_serial.get(cam["serial"])
        merged = dict(DEFAULTS, **(s or {}))
        # Strip metadata that the UI doesn't need (and that PostgREST
        # may include like updated_at).
        for k in ("updated_at",):
            merged.pop(k, None)
        out.append({
            "serial": cam["serial"],
            "name": cam.get("name") or cam["serial"],
            "audio_input_supported": True,
            "settings": merged,
        })
    return jsonify(out)


# ----- PUT /api/evidence/camera-settings/<serial> ------------------------

@evidence_bp.route(
    "/api/evidence/camera-settings/<serial>", methods=["PUT"],
)
@login_required
def upsert_camera_settings(serial: str):
    """
    Insert-or-update one camera's evidence settings row.

    Body (all fields optional; only the ones present are written)::

        {
          "enabled":               <bool>,
          "capture_video":         <bool>,
          "capture_audio":         <bool>,
          "silence_db_threshold":  <-90 .. 0>,
          "retention_days":        <int>,
          "classifier_categories": ["screams", ...]
        }

    Behavior:
      * If a row exists for this serial, PATCH the listed fields.
      * If no row exists, INSERT a new row with the listed fields
        (other columns get migration-027 defaults).

    Returns the row as it stands after the write.
    """
    import requests
    body = request.get_json(silent=True) or {}

    # Whitelist — silently drops unknown keys so a future schema
    # change doesn't accidentally let clients write columns we haven't
    # vetted.
    allowed_fields = {
        "enabled", "capture_video", "capture_audio",
        "silence_db_threshold", "retention_days",
        "classifier_categories",
    }
    payload = {k: v for k, v in body.items() if k in allowed_fields}
    if not payload:
        return jsonify({"error": "no whitelisted fields in body"}), 400

    base = _shared_settings_postgrest_url()

    # Try PATCH first. If the row doesn't exist, PostgREST returns
    # 200 with [] (empty result) — we detect that and fall through
    # to INSERT.
    patch_url = (f"{base}/evidence_camera_settings"
                 f"?serial=eq.{requests.utils.quote(serial, safe='')}")
    patch_r = requests.patch(
        patch_url,
        json=payload,
        headers={"Prefer": "return=representation"},
        timeout=10,
    )
    if not patch_r.ok:
        return jsonify({"error": f"PATCH failed: {patch_r.text[:300]}"}), 502
    rows = patch_r.json()
    if rows:
        # Patched an existing row.
        return jsonify(rows[0])

    # No existing row → INSERT. Always include the serial.
    insert_payload = {"serial": serial, **payload}
    insert_r = requests.post(
        f"{base}/evidence_camera_settings",
        json=insert_payload,
        headers={"Prefer": "return=representation"},
        timeout=10,
    )
    if not insert_r.ok:
        return jsonify({"error": f"INSERT failed: {insert_r.text[:300]}"}), 502
    rows = insert_r.json()
    return jsonify(rows[0] if rows else insert_payload), 201
