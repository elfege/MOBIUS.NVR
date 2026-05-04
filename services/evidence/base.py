"""
Abstract base class for every service in the evidence-collection package.

Why this file exists
====================

Every concrete evidence service — the audio extractor, the weekly
summarizer, the future YAMNet classifier, the future people-recognition
module, the future Child-Monitor signal selector — needs the same set
of housekeeping affordances:

  * Access to the chain-of-custody manifest writer (``EvidenceManifest``).
  * A path to the litigation volume root (``LITIGATION_ROOT``).
  * A logger named after the concrete class (so logs are easy to filter).
  * A standard start/stop lifecycle (long-running daemons AND one-shot
    scheduled jobs both fit this).
  * "Archive before overwrite" semantics: when a service is about to
    replace an existing file (e.g. regenerating a weekly summary), the
    old version is moved to ``./archive/`` instead of being clobbered.
    Per the user's standing directive 2026-04-28: "use archive directory
    when you want to preserve what you are about to change."

Putting all of that into a base class means every new service starts
with sensible defaults and a uniform interface. The supervisor that
orchestrates services doesn't need to know whether it's looking at a
camera-bound extractor or a cron-scheduled summarizer — it just calls
``start()`` and ``stop()``.

Concurrency model
=================

The base class supports both:

  * **Daemon services** — services whose ``run()`` is a long loop that
    polls or watches something forever. ``start()`` spawns ``run()`` in
    a background thread; ``stop()`` signals the thread via the shared
    ``_stop`` ``threading.Event`` and joins it.
  * **One-shot services** — services whose ``run()`` does one finite
    job and returns. ``start()`` still spawns it in a thread (useful so
    the caller can do something else in parallel) but the thread is
    short-lived and ``stop()`` just joins.

Subclasses don't need to manage the thread themselves; they only need
to implement ``run()`` and check ``self._stop.is_set()`` periodically
inside any long loops.

Why we don't use ``asyncio``
============================

The rest of the NVR codebase (``services/recording/``, ``services/motion/``,
etc.) is thread-based, not asyncio-based. Mixing the two creates
event-loop ownership headaches (who runs the loop? what happens when a
thread-based service spawns an asyncio service?). Threads are good
enough for these workloads — none of them are I/O-bound at a scale
where asyncio's overhead amortizes.
"""

# ----- standard library --------------------------------------------------
import logging                                  # one logger per subclass
import shutil                                   # for archive_to() file moves
import threading                                # Event for stop, Thread for start
from abc import ABC, abstractmethod             # abstract base machinery
from datetime import datetime, timezone         # timestamps in archive names
from pathlib import Path                        # all paths are pathlib
from typing import Optional                     # type hints for clarity

# ----- evidence package internals ----------------------------------------
from services.evidence.manifest import (
    EvidenceManifest,                           # the chain-of-custody log
    LITIGATION_ROOT,                            # /litigation (or symlinked equivalent)
)

# ----- module-level constants --------------------------------------------

# The project-level archive directory, created at the user's instruction
# 2026-04-28. Distinct from ``docs/archive/`` (which is for retired docs).
# This one collects pre-mutation snapshots of files that the evidence
# pipeline is about to overwrite, so we never silently lose work product.
#
# Located at <project_root>/archive/ — i.e. ``~/0_MOBIUS.NVR/archive/``
# on the host, and ``/app/archive/`` inside the container. The path is
# computed relative to this file's location to stay container-agnostic.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_ROOT: Path = PROJECT_ROOT / "archive"


# =========================================================================
# EvidenceService — the abstract base
# =========================================================================

class EvidenceService(ABC):
    """
    Abstract base for every concrete service in the evidence package.

    A service is anything that:

      * **produces** manifest entries (capture events, lifecycle events,
        retention-prune events, classifier verdicts, summary writes…), or
      * **consumes** the manifest / litigation files to do something
        useful with them (digest a week, classify a clip, transcribe).

    Subclasses implement only one method — ``run()`` — which contains
    the actual work. Everything else (lifecycle, logging, archiving)
    is handled here so each new service starts with sensible defaults
    and so the supervisor can orchestrate them uniformly.

    Typical subclass shape::

        class MyService(EvidenceService):
            \"\"\"One-line summary, then a long descriptive docstring
            explaining what this service does, why it exists, and any
            non-obvious invariants the reader needs to know.\"\"\"

            def __init__(self, manifest, my_param):
                # ALWAYS call super().__init__() first — it sets up
                # logger, manifest, root, and the stop event.
                super().__init__(manifest=manifest)
                self.my_param = my_param

            def run(self):
                self.log.info("MyService running")
                while not self._stop.is_set():
                    # do work
                    self._stop.wait(timeout=30)  # cooperative sleep

    Lifecycle invariants:

      * ``start()`` is idempotent — calling it twice on the same instance
        is a no-op if the worker thread is already alive.
      * ``stop()`` is idempotent and safe to call from any thread,
        including the worker thread itself (though the worker should
        prefer to ``return`` cleanly when it sees ``_stop`` set).
      * After ``stop()`` returns, ``run()`` is guaranteed to have exited
        (or the timeout was hit, in which case a warning is logged but
        no exception is raised — best-effort cleanup).
    """

    # The default join timeout when ``stop()`` waits for the worker thread
    # to finish. Subclasses with long blocking I/O can override this.
    DEFAULT_STOP_TIMEOUT_SECONDS: float = 10.0

    def __init__(
        self,
        manifest: Optional[EvidenceManifest] = None,
        litigation_root: Optional[Path] = None,
    ) -> None:
        """
        Initialize shared service infrastructure.

        Parameters
        ----------
        manifest:
            The ``EvidenceManifest`` instance to log events to. If
            ``None``, a fresh one is constructed pointing at the
            default ``MANIFEST_PATH``. Pass an explicit instance when
            multiple services should share a manifest writer (which is
            usually the case — the supervisor builds one and hands it
            out to every service it spawns).

        litigation_root:
            Override the default ``LITIGATION_ROOT`` (``/litigation``).
            Almost never needed in production — useful for tests that
            point at a temporary directory.
        """
        # Subclass-specific logger. Using ``self.__class__.__name__``
        # rather than ``__name__`` gives one log channel per concrete
        # class (e.g. "AudioExtractorService", "WeeklySummarizerService"),
        # which is much friendlier when grepping logs across multiple
        # services running in the same process.
        self.log: logging.Logger = logging.getLogger(self.__class__.__name__)

        # The chain-of-custody log. If the caller didn't supply one we
        # build a fresh instance — but in practice the supervisor passes
        # in a shared one so all services append to the same chain.
        self.manifest: EvidenceManifest = manifest or EvidenceManifest()

        # Root of the litigation volume. Resolved through the same
        # priority chain as ``manifest.LITIGATION_ROOT`` (env var ->
        # project-local symlink -> /litigation). We just take the
        # already-resolved value unless the caller wants to override.
        self.litigation_root: Path = Path(litigation_root or LITIGATION_ROOT)

        # The stop event is the One True Way for the worker thread to
        # learn it should exit. ``stop()`` sets it; ``run()`` checks it.
        # Using ``threading.Event`` (rather than e.g. a boolean flag)
        # gives us ``wait(timeout=...)`` for free, which is the right
        # primitive for cooperative cancellation of long sleeps.
        self._stop: threading.Event = threading.Event()

        # The worker thread is created lazily by ``start()`` so that
        # subclasses can be safely constructed without spawning anything.
        # ``None`` means "not started or already stopped"; a Thread
        # instance means "started, possibly still running".
        self._worker: Optional[threading.Thread] = None

    # -----------------------------------------------------------------
    # Public lifecycle API — what the supervisor calls
    # -----------------------------------------------------------------

    def start(self) -> None:
        """
        Spawn the worker thread that calls ``run()``.

        Idempotent: if the worker is already alive this is a no-op.
        The worker is daemonic so it will not prevent process exit if
        the supervisor crashes without calling ``stop()`` — but cleanly
        calling ``stop()`` is still preferred because it gives ``run()``
        a chance to flush any in-flight work to the manifest.
        """
        # Already running? Nothing to do — preserves idempotency.
        if self._worker is not None and self._worker.is_alive():
            self.log.debug("start() called but worker is already alive")
            return

        # Reset the stop event so a service that was previously stopped
        # can be restarted on the same instance. (Rare, but allowed.)
        self._stop.clear()

        # Name the thread after the concrete class for ``ps`` / ``htop``
        # / debugger readability. The "evidence-" prefix groups them
        # visually in tools that sort thread names alphabetically.
        thread_name = f"evidence-{self.__class__.__name__}"

        # Daemon=True means: if the main thread exits, the OS reaps
        # this thread automatically. Production code should still call
        # stop() for clean shutdown, but daemon=True is a safety net.
        self._worker = threading.Thread(
            target=self._run_with_exception_logging,
            name=thread_name,
            daemon=True,
        )
        self._worker.start()
        self.log.info("started")

    def stop(self, timeout: Optional[float] = None) -> None:
        """
        Signal the worker to exit and wait for it (best-effort).

        Idempotent. Safe to call from the worker thread itself (in
        which case the join is skipped — joining your own thread is a
        deadlock, so we detect that case and just set the event).

        Parameters
        ----------
        timeout:
            Seconds to wait for the worker thread to exit. Defaults to
            ``DEFAULT_STOP_TIMEOUT_SECONDS``. If the worker doesn't
            finish in time, a warning is logged but no exception is
            raised — the daemon flag will let the process exit anyway.
        """
        # Setting the event is the ONLY way the worker is told to stop.
        # Subclasses must check it inside their loops.
        self._stop.set()

        # If we have no worker (never started, or already cleaned up),
        # we're done — nothing to join.
        if self._worker is None:
            self.log.debug("stop() called but no worker exists")
            return

        # Don't try to join your own thread; that's a deadlock.
        if threading.current_thread() is self._worker:
            self.log.debug("stop() called from inside the worker — "
                           "skipping self-join, will exit on return")
            return

        # Wait (best effort) for the worker to exit cleanly.
        actual_timeout = (timeout if timeout is not None
                          else self.DEFAULT_STOP_TIMEOUT_SECONDS)
        self._worker.join(timeout=actual_timeout)

        if self._worker.is_alive():
            # The worker didn't exit in time. We don't kill it (Python
            # has no clean way to kill threads); we just log a warning.
            # The daemon=True flag means the OS will reap it eventually.
            self.log.warning(
                "worker did not exit within %.1fs; leaving as daemon",
                actual_timeout,
            )
        else:
            self.log.info("stopped")
            # Drop the reference so a subsequent start() will spawn fresh.
            self._worker = None

    def is_running(self) -> bool:
        """Return ``True`` if the worker thread exists and is alive."""
        return self._worker is not None and self._worker.is_alive()

    # -----------------------------------------------------------------
    # Subclass contract — the one method every service must implement
    # -----------------------------------------------------------------

    @abstractmethod
    def run(self) -> None:
        """
        The service's main entry point. Runs in the worker thread.

        Subclasses implement this. Two common shapes:

          1. **Long-running daemon** — loop until ``self._stop`` is set::

                 while not self._stop.is_set():
                     self._do_one_unit_of_work()
                     self._stop.wait(timeout=POLL_INTERVAL)

             Use ``self._stop.wait(timeout=...)`` rather than
             ``time.sleep(...)`` because ``wait()`` returns early when
             ``stop()`` is called, so shutdown is immediate.

          2. **One-shot scheduled job** — do the work and return::

                 def run(self):
                     ctx = self._gather_context()
                     result = self._compute(ctx)
                     self._write_output(result)
                     # implicit return; thread exits

             The supervisor (or whoever called start()) is responsible
             for re-spawning the service on the next scheduled tick if
             that's the desired behavior.
        """
        ...

    # -----------------------------------------------------------------
    # Shared utilities — provided to subclasses
    # -----------------------------------------------------------------

    def archive_to(
        self,
        source_path: Path,
        reason: str = "",
        archive_root: Optional[Path] = None,
    ) -> Optional[Path]:
        """
        Move ``source_path`` into the project archive before it is
        overwritten or deleted by an in-progress service action.

        This is the canonical "preserve before mutate" helper, per the
        user's 2026-04-28 standing directive. Use it whenever a service
        is about to overwrite an existing file, regenerate a derived
        artifact, or otherwise cause prior content to be lost.

        Behavior:

          * If ``source_path`` does not exist, return ``None`` and log
            at DEBUG. Nothing to archive — not an error.
          * If it exists, move it to::

                <archive_root>/<class_name>/<YYYY-MM-DD>/
                <HHMMSS>_<original_name>__<short_reason>.<ext>

            and return the new path. The destination directory is
            created if missing.

        Parameters
        ----------
        source_path:
            File to move. Directories not supported (the use cases so
            far don't need them; if they do, extend this method).
        reason:
            Free-form short tag (e.g. ``"regenerated"``, ``"superseded"``)
            that will be slugged into the archive filename. Helps you
            understand later why this file was archived.
        archive_root:
            Override the default ``ARCHIVE_ROOT``. Useful for tests.

        Returns
        -------
        The destination path the file was moved to, or ``None`` if the
        source didn't exist (no-op case).
        """
        source_path = Path(source_path)

        # Nothing to archive — common case, not an error.
        if not source_path.exists():
            self.log.debug("archive_to: %s does not exist, skipping",
                           source_path)
            return None

        # Build a per-class, per-day archive subtree so the archive
        # directory stays browsable even after years of accumulation.
        root = Path(archive_root or ARCHIVE_ROOT)
        now = datetime.now(timezone.utc)
        date_dir = now.strftime("%Y-%m-%d")
        dest_dir = root / self.__class__.__name__ / date_dir
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Build a destination filename that:
        #   - starts with HHMMSS so files sort chronologically
        #   - keeps the original name (and extension) for findability
        #   - tags the reason so an audit later tells you WHY it moved
        # Slug the reason so it's filesystem-safe (lowercase, dashes,
        # no spaces, max 32 chars to keep names readable).
        reason_slug = (reason or "archived").lower()[:32]
        for ch in (" ", "/", "\\", ":", "\t", "\n"):
            reason_slug = reason_slug.replace(ch, "-")
        time_prefix = now.strftime("%H%M%S")
        dest_name = f"{time_prefix}_{source_path.name}__{reason_slug}"
        dest_path = dest_dir / dest_name

        # ``shutil.move`` is rename when source and destination are on
        # the same filesystem (atomic, fast); copy+delete otherwise.
        # Both are fine for our purposes.
        shutil.move(str(source_path), str(dest_path))
        self.log.info("archived %s -> %s (reason: %s)",
                      source_path, dest_path, reason or "n/a")
        return dest_path

    def log_lifecycle_event(
        self,
        event_type: str,
        **fields,
    ) -> dict:
        """
        Append a service-level lifecycle event to the manifest.

        Use this for service events that are NOT capture events but
        that belong in the chain-of-custody record nonetheless: e.g.
        "evidence_collection_enabled", "weekly_summary_generated",
        "retention_prune", "configuration_changed".

        Capture events (the actual evidence artifacts — mp3 segments,
        classifier verdicts, transcripts) should call ``manifest.append()``
        directly with their full payload. This helper is a thin wrapper
        for the boilerplate "service-y" events.

        The ``service`` field is set automatically from the class name.
        """
        entry = {
            "event_type": event_type,
            "service": self.__class__.__name__,
            **fields,
        }
        return self.manifest.append(entry)

    # -----------------------------------------------------------------
    # Internal — wraps run() with logging so exceptions don't vanish
    # -----------------------------------------------------------------

    def _run_with_exception_logging(self) -> None:
        """
        Wrap ``run()`` in a ``try/except`` so that uncaught exceptions
        are logged with full traceback rather than silently killing the
        worker thread (which is what happens by default if you let an
        exception escape a ``Thread.target``).
        """
        try:
            self.run()
        except Exception:  # noqa: BLE001 — we WANT to catch everything
            # Log the traceback to the service's logger. This is the
            # last line of defense — the service is about to die.
            self.log.exception("run() raised an unhandled exception")
            # We do NOT re-raise: the thread is going to exit anyway,
            # and re-raising in a thread context just fills stderr with
            # a confusing "Exception in thread evidence-XYZ" trace that
            # duplicates what we just logged at ERROR level.
