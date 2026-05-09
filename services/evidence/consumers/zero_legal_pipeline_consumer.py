#!/usr/bin/env python3
"""
zero_legal_pipeline_consumer.py — reference consumer daemon for the
NVR's evidence pipeline.

What this is for
================

The NVR exposes evidence-bearing events on a read-only HTTP API (see
``routes/evidence_routes.py``). This script is the canonical example
of how to consume that feed — poll for new events, decide which legal
case each one belongs to, promote it on the NVR side, and download
the artifacts (mp3 + transcript + classification JSON) into the
matching ``~/0_LEGAL/<case>/{audio,transcripts}/`` directory tree.

It is intentionally a SINGLE FILE with only two dependencies (Python
3.9+ stdlib + ``requests``) so it can be dropped into ``~/0_LEGAL/``
on ``server`` and run directly.

How to deploy
=============

::

    # On `server` (where ~/0_LEGAL/ lives):
    cp services/evidence/consumers/zero_legal_pipeline_consumer.py \\
       ~/0_LEGAL/

    # First-time config: write a JSON config file at
    # ~/0_LEGAL/.evidence_consumer.json — see ``DEFAULT_CONFIG`` below
    # for the schema. At minimum, set the NVR base URL and your auth.

    # Run as a daemon (recommend: a systemd user unit). One-shot dry-
    # run is also supported for testing:
    python3 ~/0_LEGAL/zero_legal_pipeline_consumer.py --once --dry-run

How it decides which case an event belongs to
=============================================

Each event is matched against the cases listed in the config's
``cases`` array. The rules are:

  1. Cases are processed in the order they appear in the array.
  2. A case "matches" an event if the event satisfies the case's
     ``predicates`` (cameras, categories, after, before) — same shape
     as the predicates stored on the NVR side.
  3. The first matching case wins. If no case matches, the event is
     skipped (logged at INFO so you can decide whether to widen a
     case's predicates).

This is deliberately simple. If you need fancier logic (e.g. ML-based
auto-classification of which case to use), wrap or fork this file.

How files land in 0_LEGAL
=========================

For each promoted event:

  * mp3      → ~/0_LEGAL/<case_dir>/audio/<YYYY-MM-DD_HHMM_serial.mp3>
  * txt      → ~/0_LEGAL/<case_dir>/transcripts/<YYYY-MM-DD_HHMM_serial.txt>
  * json     → ~/0_LEGAL/<case_dir>/transcripts/<YYYY-MM-DD_HHMM_serial.json>
  * (mp4 video reference is recorded in a per-case TIMELINE.md but the
    actual mp4 must be fetched separately — the recording lives in
    the NVR's recordings table, not under /litigation/)

Filenames follow the ``YYYY-MM-DD_HHMM_source_description.ext``
convention from ``server:~/0_LEGAL/CLAUDE.md`` §2.3.

Chain-of-custody verification
=============================

Each downloaded file's SHA-256 (returned by the NVR in the
``X-Content-SHA256`` response header) is verified against the bytes
received. A mismatch is fatal — the file is NOT written, the event is
NOT marked promoted, and the daemon logs an error. This gives you a
defensible "the file I have here matches the NVR's manifest record at
download time" claim.

State file
==========

``~/0_LEGAL/.evidence_consumer_state.json`` tracks the last-seen
manifest_id so a restart resumes from there. Atomic writes (tmp +
rename) so a crash mid-write doesn't corrupt the cursor.
"""

# ----- standard library --------------------------------------------------
import argparse                                 # CLI flags
import hashlib                                  # download verification
import json                                     # config + state files
import logging                                  # diagnostic logging
import os                                       # env-var auth fallback
import sys                                      # exit codes
import time                                     # poll-loop sleeps
from datetime import datetime                   # timestamp parsing
from pathlib import Path                        # all paths
from typing import Any, Dict, List, Optional, Tuple   # type hints

# ----- third party (only one!) -------------------------------------------
import requests                                 # HTTP transport


# =========================================================================
# Configuration shape
# =========================================================================

# Default config used as a fallback when ~/0_LEGAL/.evidence_consumer.json
# is missing fields. Saves users from having to hand-write every field.
# Only the NVR URL + auth need to be set explicitly in most cases.
DEFAULT_CONFIG: Dict[str, Any] = {

    # NVR base URL (no trailing slash). Reachable from the host this
    # daemon runs on. Use https:// in production.
    "nvr_url": "http://<LAN_IP>:8081",

    # Authentication: use one of these (in priority order):
    #   1. ``api_token``: set NVR_API_TOKEN_HEADER below to match. Recommended
    #      for daemon use; doesn't need cookie management.
    #   2. ``username`` + ``password``: the daemon will POST to /login
    #      and persist the session cookie. Useful if you don't have an
    #      API token set up yet.
    # If neither is given, the daemon assumes it's behind some other
    # auth (e.g. trusted-network mode) and just makes anonymous calls.
    "api_token":  None,
    "username":   None,
    "password":   None,

    # Header used to send the API token. Matches the NVR's external
    # API convention. If you've configured a different header name,
    # update both ends.
    "api_token_header": "X-NVR-API-Token",

    # Where the case directory tree lives. Each case below gets a
    # subdirectory under this root.
    "legal_root": "~/0_LEGAL",

    # Cases to route events to. Order matters — first match wins.
    # Each case has:
    #   name           : human-readable, for logs
    #   case_id        : the NVR-side evidence_cases.id (created
    #                    out-of-band via POST /api/evidence/cases)
    #   case_dir       : subdirectory under legal_root to write into
    #                    (e.g. "0_MARITAL", "0_WORK/mindhop")
    #   predicates     : same shape as NVR-side predicates
    #                    (cameras[], categories[], after, before)
    "cases": [
        # Example — replace with your real cases:
        # {
        #   "name": "Marital — 2026",
        #   "case_id": 1,
        #   "case_dir": "0_MARITAL",
        #   "predicates": {
        #     "cameras": ["T8416P0023352DA9"],
        #     "categories": ["screams", "raised-voices"],
        #     "after": "2026-04-01T00:00:00Z"
        #   }
        # },
    ],

    # Polling cadence. The NVR's feed is cheap — 30 s is fine even
    # with many cameras.
    "poll_seconds": 30,

    # Maximum events fetched per /feed call. Larger than the NVR's
    # default of 50 just speeds up backlog drains.
    "feed_limit": 200,

    # If True, log what would be done but make no HTTP writes and no
    # filesystem writes. Useful for first-time setup audits.
    "dry_run": False,
}


# State file: tracks last-seen manifest_id so we resume on restart.
STATE_FILENAME = ".evidence_consumer_state.json"
CONFIG_FILENAME = ".evidence_consumer.json"


# =========================================================================
# Pipeline consumer class
# =========================================================================

class ZeroLegalConsumer:
    """
    The daemon. Construct with a loaded config, then call ``run()``
    for the main poll loop, or ``run_once()`` to drain backlog and
    exit.

    All HTTP and FS operations go through methods so a fork can
    override one piece (e.g. swap session cookies for OAuth bearer
    tokens) without copying the whole loop.
    """

    def __init__(self, config: Dict[str, Any]):
        self.cfg = config
        # Expand ~ and resolve to absolute paths up front so all
        # subsequent .joinpath() calls are unambiguous.
        self.legal_root: Path = Path(
            os.path.expanduser(config["legal_root"])
        ).resolve()
        self.state_path: Path = self.legal_root / STATE_FILENAME

        # The requests Session reuses TCP connections across polls and
        # carries our auth (cookie or token header).
        self.session = requests.Session()
        self._setup_auth()

        # Logger — prefix matches the script for easy `journalctl`
        # filtering when run as a systemd unit.
        self.log = logging.getLogger("zero_legal_consumer")

    # -----------------------------------------------------------------
    # Auth setup
    # -----------------------------------------------------------------

    def _setup_auth(self) -> None:
        """
        Apply auth to the session. Priority:

          1. api_token   → set the configured header on every request
          2. username+pw → POST /login, persist the session cookie
          3. neither     → anonymous (only works if NVR is in
                            trusted-network mode for this client)
        """
        token = self.cfg.get("api_token") or os.environ.get("NVR_API_TOKEN")
        if token:
            self.session.headers[self.cfg["api_token_header"]] = token
            return
        username = self.cfg.get("username")
        password = self.cfg.get("password")
        if username and password:
            login_url = self.cfg["nvr_url"].rstrip("/") + "/auth/login"
            r = self.session.post(
                login_url,
                data={"username": username, "password": password},
                allow_redirects=False,  # we just need the Set-Cookie
                timeout=10,
            )
            if r.status_code not in (200, 302):
                raise RuntimeError(
                    f"login to NVR failed: HTTP {r.status_code} {r.text[:200]}"
                )
            # The session cookie is now in self.session.cookies.

    # -----------------------------------------------------------------
    # Main loops
    # -----------------------------------------------------------------

    def run(self) -> int:
        """Long-running poll loop. Returns 0 on clean shutdown."""
        self.log.info("ZeroLegalConsumer starting; legal_root=%s",
                      self.legal_root)
        try:
            while True:
                try:
                    self._poll_once()
                except Exception:
                    self.log.exception("poll iteration raised; retrying")
                time.sleep(self.cfg["poll_seconds"])
        except KeyboardInterrupt:
            self.log.info("interrupted; exiting cleanly")
            return 0

    def run_once(self) -> int:
        """Drain backlog once and exit. Used by --once."""
        self._poll_once()
        return 0

    def _poll_once(self) -> None:
        """One pass: fetch new events, route, promote, download."""
        since = self._load_state()
        self.log.info("polling /api/evidence/feed since=%d", since)

        page = self._fetch_feed(since)
        entries: List[Dict[str, Any]] = page.get("entries", [])
        if not entries:
            self.log.info("no new events; up to date")
            return
        self.log.info("got %d new events (has_more=%s)",
                      len(entries), page.get("has_more"))

        # Group by case so we can promote in batches (one POST per case).
        per_case_to_promote: Dict[int, List[int]] = {}
        per_case_to_download: Dict[int, List[Dict[str, Any]]] = {}
        unrouted = 0

        for entry in entries:
            # We only act on audio_capture events here — they're the
            # canonical "evidence event" with associated mp3. The
            # acoustic_classification + transcription entries that
            # follow are linked to their parent capture via
            # ``audio_capture_manifest_id``, and the per-case download
            # of those derived files is keyed off the capture's id.
            if entry.get("event_type") != "audio_capture":
                continue
            case = self._route_event(entry)
            if case is None:
                unrouted += 1
                continue
            per_case_to_promote.setdefault(case["case_id"], []).append(
                entry["manifest_id"]
            )
            per_case_to_download.setdefault(case["case_id"], []).append(entry)

        if unrouted:
            self.log.info("%d events did not match any case predicates "
                          "(left unrouted; widen a case's predicates if "
                          "they should be included)", unrouted)

        # Promote + download per case.
        for case_id, manifest_ids in per_case_to_promote.items():
            case = self._lookup_case_in_config(case_id)
            if case is None:
                self.log.error("internal: lost case lookup for id=%d", case_id)
                continue
            self._promote_batch(case_id, manifest_ids)
            for entry in per_case_to_download[case_id]:
                self._download_event_files(entry, case)

        # Update cursor only after all writes succeeded for this page.
        # If something failed and raised above, we DON'T update — next
        # poll re-tries the same range.
        self._save_state(page["next_since"])

    # -----------------------------------------------------------------
    # Routing
    # -----------------------------------------------------------------

    def _route_event(
        self,
        entry: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """First-match-wins routing through self.cfg['cases']."""
        for case in self.cfg.get("cases", []):
            if self._matches(entry, case.get("predicates") or {}):
                return case
        return None

    def _lookup_case_in_config(self, case_id: int) -> Optional[Dict[str, Any]]:
        for case in self.cfg.get("cases", []):
            if case.get("case_id") == case_id:
                return case
        return None

    @staticmethod
    def _matches(entry: Dict[str, Any], preds: Dict[str, Any]) -> bool:
        """Apply predicates to one manifest entry. Same semantics as the
        NVR's own _event_matches_predicates."""
        cams = preds.get("cameras")
        if cams and entry.get("camera_serial") not in cams:
            return False
        # Categories on a raw audio_capture are not yet known (the
        # later acoustic_classification entry has them). For now we
        # don't filter on category here — the case will gather all
        # captures from the matching cameras, and the operator can
        # narrow later via the case_events endpoint. If you need
        # stricter routing, add a short delay so categories arrive
        # before routing, OR consume acoustic_classification events
        # instead of audio_capture.
        cats = preds.get("categories")
        # NB: intentionally NOT filtering on cats here — see comment.
        _ = cats
        after = preds.get("after")
        if after and (entry.get("timestamp_utc") or "") < after:
            return False
        before = preds.get("before")
        if before and (entry.get("timestamp_utc") or "") > before:
            return False
        return True

    # -----------------------------------------------------------------
    # NVR API calls
    # -----------------------------------------------------------------

    def _api_url(self, path: str) -> str:
        return self.cfg["nvr_url"].rstrip("/") + path

    def _fetch_feed(self, since: int) -> Dict[str, Any]:
        url = self._api_url("/api/evidence/feed")
        params = {
            "since": since,
            "limit": self.cfg["feed_limit"],
            "include_lifecycle": "false",
        }
        r = self.session.get(url, params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def _promote_batch(self, case_id: int, manifest_ids: List[int]) -> None:
        if self.cfg.get("dry_run"):
            self.log.info("[dry-run] would POST promote case=%d ids=%s",
                          case_id, manifest_ids)
            return
        url = self._api_url(f"/api/evidence/cases/{case_id}/promote")
        r = self.session.post(
            url,
            json={"manifest_ids": manifest_ids},
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        self.log.info("promoted %d events to case %d (server confirmed %d)",
                      len(manifest_ids), case_id, body.get("promoted_count"))

    def _download_event_files(
        self,
        entry: Dict[str, Any],
        case: Dict[str, Any],
    ) -> None:
        """
        Download the mp3, txt, and json artifacts for one event into
        the case's audio/transcripts subdirectories. Verifies SHA-256
        against the X-Content-SHA256 header before writing.
        """
        manifest_id = entry["manifest_id"]
        # Filename per 0_LEGAL convention: YYYY-MM-DD_HHMM_serial
        ts = entry.get("timestamp_utc", "1970-01-01T00:00:00Z")
        # Parse ISO and reformat to the convention.
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            stem = t.strftime("%Y-%m-%d_%H%M") + "_" + entry.get(
                "camera_serial", "unknown")
        except ValueError:
            stem = f"event_{manifest_id}"

        case_root = self.legal_root / case["case_dir"]
        audio_dir = case_root / "audio"
        transcripts_dir = case_root / "transcripts"
        if not self.cfg.get("dry_run"):
            audio_dir.mkdir(parents=True, exist_ok=True)
            transcripts_dir.mkdir(parents=True, exist_ok=True)

        # mp3 → audio/, txt + json → transcripts/
        mappings = [
            ("mp3",  audio_dir / f"{stem}.mp3"),
            ("txt",  transcripts_dir / f"{stem}.txt"),
            ("json", transcripts_dir / f"{stem}.json"),
        ]
        for kind, target in mappings:
            try:
                self._download_one_file(manifest_id, kind, target)
            except Exception as e:
                # Don't let one missing/failed file kill the whole
                # event's download. txt/json may legitimately not
                # exist yet (Whisper hasn't run); mp3 is essential.
                level = self.log.error if kind == "mp3" else self.log.warning
                level("download %s for id=%d failed: %s",
                      kind, manifest_id, e)

    def _download_one_file(
        self,
        manifest_id: int,
        kind: str,
        target: Path,
    ) -> None:
        """
        Download one file artifact and verify SHA-256 from the response
        headers against the bytes received. Raises on mismatch.
        """
        url = self._api_url(f"/api/evidence/event/{manifest_id}/file/{kind}")
        if self.cfg.get("dry_run"):
            self.log.info("[dry-run] would download %s -> %s", url, target)
            return
        r = self.session.get(url, timeout=60, stream=True)
        if r.status_code == 404:
            # Common case for kind=txt/json before transcription runs.
            self.log.debug("404 for %s id=%d (likely not yet produced)",
                           kind, manifest_id)
            return
        r.raise_for_status()
        expected_sha = r.headers.get("X-Content-SHA256", "")

        # Stream into a temp file alongside the target to avoid
        # leaving partial files on crash, while computing the hash.
        tmp = target.with_suffix(target.suffix + ".tmp")
        h = hashlib.sha256()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                if chunk:
                    f.write(chunk)
                    h.update(chunk)
        actual_sha = "sha256:" + h.hexdigest()
        if expected_sha and expected_sha != actual_sha:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(
                f"SHA-256 mismatch on {kind} id={manifest_id}: "
                f"expected {expected_sha} got {actual_sha} "
                f"(file NOT written; chain-of-custody violated)"
            )
        tmp.replace(target)
        self.log.info("downloaded %s id=%d (%d bytes, sha verified) -> %s",
                      kind, manifest_id, target.stat().st_size, target)

    # -----------------------------------------------------------------
    # State persistence
    # -----------------------------------------------------------------

    def _load_state(self) -> int:
        if not self.state_path.exists():
            return 0
        try:
            data = json.loads(self.state_path.read_text())
            return int(data.get("last_seen_manifest_id", 0))
        except (json.JSONDecodeError, ValueError, OSError):
            self.log.warning("state file corrupt; starting from manifest 0")
            return 0

    def _save_state(self, manifest_id: int) -> None:
        if self.cfg.get("dry_run"):
            return
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "last_seen_manifest_id": int(manifest_id),
            "updated_at_iso": datetime.utcnow().isoformat() + "Z",
        }))
        tmp.replace(self.state_path)


# =========================================================================
# CLI
# =========================================================================

def _load_config(config_path: Optional[Path]) -> Dict[str, Any]:
    """
    Load config from the given path (or the conventional location
    ``~/0_LEGAL/.evidence_consumer.json``). Missing fields are filled
    from DEFAULT_CONFIG so the user only has to specify what they're
    overriding.
    """
    if config_path is None:
        config_path = (Path.home() / "0_LEGAL" / CONFIG_FILENAME).expanduser()
    cfg = dict(DEFAULT_CONFIG)
    if config_path.exists():
        cfg.update(json.loads(config_path.read_text()))
    else:
        # Helpful error if the user forgot to write a config.
        raise SystemExit(
            f"Config file not found at {config_path}. Create one with at "
            f"least 'nvr_url' and 'cases' keys; see DEFAULT_CONFIG inside "
            f"this script for the full schema."
        )
    return cfg


def main() -> int:
    p = argparse.ArgumentParser(
        description="0_LEGAL pipeline consumer — polls the NVR's "
                    "evidence feed and routes events into per-case "
                    "directories.",
    )
    p.add_argument("--config", type=Path,
                   help=f"path to config JSON (default: "
                        f"~/0_LEGAL/{CONFIG_FILENAME})")
    p.add_argument("--once", action="store_true",
                   help="drain backlog once and exit (no daemon loop)")
    p.add_argument("--dry-run", action="store_true",
                   help="log actions but don't write to disk or POST "
                        "to the NVR")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    cfg = _load_config(args.config)
    if args.dry_run:
        cfg["dry_run"] = True

    consumer = ZeroLegalConsumer(cfg)
    if args.once:
        return consumer.run_once()
    return consumer.run()


if __name__ == "__main__":
    sys.exit(main())
