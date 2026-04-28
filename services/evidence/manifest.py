"""
Evidence Manifest — append-only, hash-chained event log for /litigation/.

Each line of MANIFEST.jsonl is a JSON object representing one capture event
(or a lifecycle/admin event such as the genesis entry, retention pruning,
or the initial collection-enabled disclosure acknowledgment).

The hash chain works as follows:

    entry[0]   { manifest_id: 0,  previous_hash: "sha256:GENESIS",  ... ,  this_hash: H0 }
    entry[1]   { manifest_id: 1,  previous_hash: H0,                ... ,  this_hash: H1 }
    entry[2]   { manifest_id: 2,  previous_hash: H1,                ... ,  this_hash: H2 }
    ...

``this_hash`` is the SHA-256 of the canonical JSON encoding of the entry
*with the this_hash field removed*, prefixed with ``"sha256:"``. Canonical
encoding sorts keys and uses tight separators so the hash is reproducible
regardless of how the entry was constructed.

Tampering with any entry — adding, removing, or modifying fields — breaks
the chain from that point forward. Verification is a single forward scan:
recompute each this_hash and confirm each entry's previous_hash matches
the prior entry's this_hash.

The writer is thread-safe (serialized via an instance-level lock) and
performs an fsync on the file descriptor after each append so that a
hardware crash mid-append cannot leave a half-written line that would
corrupt the chain.

Concurrency model
-----------------
Within a single process: protected by the instance lock.
Across processes: protected by ``fcntl.flock`` on the file (Linux) — only
one process at a time may hold the write lock.
"""

import fcntl
import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

logger = logging.getLogger(__name__)

# Resolve the litigation volume root.
#
# The volume's canonical mount point on dellserver is /litigation/
# (sdd, ext4, label LITIGATION). For project-tree access, the same
# directory is also reachable as <project_root>/litigation:
#   - On the host: a symlink at ~/0_MOBIUS.NVR/litigation -> /litigation
#   - In the container: a bind mount /litigation -> /app/litigation
#
# Resolution priority:
#   1. ``LITIGATION_ROOT`` environment variable (explicit override —
#      used by tests and alternate deployments).
#   2. ``<project_root>/litigation`` if it exists (preferred — keeps
#      pipeline code agnostic to whether it runs inside or outside
#      the container).
#   3. ``/litigation`` as a final fallback.
#
# Project root is two levels up from this file:
#   services/evidence/manifest.py  →  <project_root>
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve_litigation_root() -> Path:
    env = os.environ.get("LITIGATION_ROOT")
    if env:
        return Path(env)
    project_local = _PROJECT_ROOT / "litigation"
    if project_local.exists():
        return project_local
    return Path("/litigation")


LITIGATION_ROOT = _resolve_litigation_root()
MANIFEST_PATH = LITIGATION_ROOT / "MANIFEST.jsonl"

# Special sentinel used as previous_hash on the genesis entry. Anything
# else here would imply a prior entry exists, breaking the chain check.
GENESIS_PREVIOUS_HASH = "sha256:GENESIS"


def _canonical_json(obj: Dict[str, Any]) -> bytes:
    """Encode ``obj`` to canonical UTF-8 JSON bytes for hashing.

    Canonical means: sorted keys at every depth, no whitespace between
    tokens, no trailing newline. The same input always produces the same
    bytes regardless of dict insertion order.
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _compute_this_hash(entry_without_this_hash: Dict[str, Any]) -> str:
    """Return ``"sha256:<hex>"`` for an entry's canonical JSON."""
    digest = hashlib.sha256(_canonical_json(entry_without_this_hash)).hexdigest()
    return f"sha256:{digest}"


def _utc_now_iso() -> str:
    """Current UTC time in ISO-8601 with millisecond precision and ``Z`` suffix."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


class ManifestIntegrityError(Exception):
    """Raised when the hash chain fails verification."""


class EvidenceManifest:
    """Append-only, hash-chained event log writer/reader.

    Typical use::

        m = EvidenceManifest()
        m.ensure_genesis(operator_user_id="elfege")
        m.append({
            "event_type": "capture",
            "camera_serial": "T8410P0023352DA9",
            "camera_name": "Living Room",
            "timestamp_utc": "2026-04-27T06:14:33.241Z",
            "files": { ... },
            "yamnet": [ ... ],
            "whisper": { ... },
        })

    The append() call fills in ``manifest_id``, ``previous_hash``, and
    ``this_hash`` automatically — callers must NOT supply those.
    """

    def __init__(self, manifest_path: Path = MANIFEST_PATH):
        self.path = Path(manifest_path)
        # Serialize appends within this process. Cross-process serialization
        # is provided by fcntl.flock on the open file descriptor.
        self._lock = threading.Lock()

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------

    def append(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Append ``entry`` to the manifest. Returns the entry with the
        chain fields (``manifest_id``, ``previous_hash``, ``this_hash``)
        populated.

        Caller-supplied ``manifest_id``, ``previous_hash``, or ``this_hash``
        keys are silently overwritten — only the writer is allowed to
        compute them, otherwise the chain is meaningless.
        """
        # Strip any caller-supplied chain fields. Trying to set them is
        # a programmer error; we don't want to fight about it.
        entry = {k: v for k, v in entry.items()
                 if k not in ("manifest_id", "previous_hash", "this_hash")}

        # Default timestamp if the caller didn't provide one. Captures
        # should always provide their own; admin/lifecycle entries can
        # rely on this default.
        entry.setdefault("timestamp_utc", _utc_now_iso())

        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Open for append in binary mode so newline handling is
            # explicit and ``fcntl.flock`` works portably.
            with open(self.path, "ab") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    last = self._read_last_entry_locked()
                    if last is None:
                        next_id = 0
                        previous_hash = GENESIS_PREVIOUS_HASH
                    else:
                        next_id = last["manifest_id"] + 1
                        previous_hash = last["this_hash"]

                    entry["manifest_id"] = next_id
                    entry["previous_hash"] = previous_hash
                    entry["this_hash"] = _compute_this_hash(entry)

                    # Single line per entry, terminated by exactly one \n.
                    line = _canonical_json(entry) + b"\n"
                    f.write(line)
                    f.flush()
                    os.fsync(f.fileno())
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

        logger.info(
            "manifest append id=%d type=%s prev=%s this=%s",
            entry["manifest_id"],
            entry.get("event_type", "?"),
            entry["previous_hash"][-12:],
            entry["this_hash"][-12:],
        )
        return entry

    def ensure_genesis(self, operator_user_id: Optional[str] = None) -> Dict[str, Any]:
        """Write the manifest_id=0 genesis entry if and only if the
        manifest is empty. Idempotent: returns the existing entry 0 if
        the manifest already has content.

        The genesis entry records when the volume was provisioned and
        which operator authorized evidence collection. It is part of the
        chain-of-custody record.
        """
        last = self._read_last_entry()
        if last is not None:
            # Manifest already has content. Confirm entry 0 is genesis;
            # if not, we have a corrupted manifest and should refuse to
            # silently "fix" it by writing a new genesis on top.
            zero = self._read_entry_at(0)
            if zero is None:
                raise ManifestIntegrityError(
                    "Manifest is non-empty but entry 0 is missing. "
                    "Investigate before writing further entries."
                )
            return zero

        return self.append({
            "event_type": "manifest_genesis",
            "description": (
                "Evidence collection volume provisioned. This entry is "
                "the chain anchor; all subsequent entries' previous_hash "
                "fields ultimately link back to this one."
            ),
            "operator_user_id": operator_user_id,
            "volume_label": "LITIGATION",
            "volume_uuid": "22b05160-1494-4cee-bdaf-e5a678aa46c5",
            "schema_version": 1,
        })

    def last_entry(self) -> Optional[Dict[str, Any]]:
        """Return the highest-id entry, or ``None`` if the manifest is empty."""
        return self._read_last_entry()

    def iter_entries(self, from_id: int = 0,
                     to_id: Optional[int] = None) -> Iterator[Dict[str, Any]]:
        """Yield manifest entries in order, optionally bounded.

        ``from_id`` is inclusive; ``to_id`` is inclusive if given, else
        iteration runs to end-of-file.
        """
        if not self.path.exists():
            return
        with open(self.path, "rb") as f:
            for raw in f:
                if not raw.strip():
                    continue
                entry = json.loads(raw)
                mid = entry["manifest_id"]
                if mid < from_id:
                    continue
                if to_id is not None and mid > to_id:
                    return
                yield entry

    def verify_chain(self, from_id: int = 0,
                     to_id: Optional[int] = None) -> Tuple[bool, Optional[str]]:
        """Walk the manifest forward and verify the hash chain.

        Returns ``(True, None)`` on success, ``(False, "<reason>")`` on
        the first detected break. Verification recomputes each entry's
        this_hash from its content and confirms each previous_hash matches
        the prior entry's this_hash.
        """
        expected_previous = GENESIS_PREVIOUS_HASH
        expected_id = from_id
        seen_any = False
        for entry in self.iter_entries(from_id=from_id, to_id=to_id):
            seen_any = True
            mid = entry["manifest_id"]
            if mid != expected_id:
                return False, (
                    f"manifest_id discontinuity: expected {expected_id}, "
                    f"got {mid}"
                )
            if entry["previous_hash"] != expected_previous:
                return False, (
                    f"previous_hash mismatch at id={mid}: "
                    f"expected {expected_previous}, got {entry['previous_hash']}"
                )
            stored_hash = entry["this_hash"]
            recomputed = _compute_this_hash(
                {k: v for k, v in entry.items() if k != "this_hash"}
            )
            if stored_hash != recomputed:
                return False, (
                    f"this_hash mismatch at id={mid}: "
                    f"stored {stored_hash}, recomputed {recomputed}"
                )
            expected_previous = stored_hash
            expected_id += 1
        if not seen_any and from_id == 0:
            # Empty manifest is technically valid (nothing to verify).
            return True, None
        return True, None

    # -----------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------

    def _read_last_entry(self) -> Optional[Dict[str, Any]]:
        """Public entry point that takes the lock around the read."""
        with self._lock:
            return self._read_last_entry_locked()

    def _read_last_entry_locked(self) -> Optional[Dict[str, Any]]:
        """Read the last entry. Caller must hold ``self._lock``.

        Implementation note: for very large manifests we'd want to seek
        backwards from EOF, but this manifest is expected to grow on the
        order of thousands of entries per year — linear scan is fine
        and avoids edge cases with multi-GB JSONL files.
        """
        if not self.path.exists():
            return None
        last = None
        with open(self.path, "rb") as f:
            for raw in f:
                if raw.strip():
                    last = raw
        if last is None:
            return None
        return json.loads(last)

    def _read_entry_at(self, manifest_id: int) -> Optional[Dict[str, Any]]:
        """Return the entry with the given id, or None."""
        for entry in self.iter_entries(from_id=manifest_id, to_id=manifest_id):
            return entry
        return None
