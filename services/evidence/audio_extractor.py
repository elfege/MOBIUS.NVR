"""
Audio Extractor Service — Phase 1c of the Evidence Collection Pipeline.

For each camera that has ``evidence_camera_settings.enabled = TRUE`` and
``cameras.audio_input_supported = TRUE``, this service runs one ffmpeg
subprocess that:

1. Reads the camera's RTSP stream from the configured streaming hub.
2. Produces continuous, sequentially-numbered 60-second mp3 segments
   (16 kHz mono, 64 kbps) into ``<intake>/staging/``.
3. Runs the ``silencedetect`` audio filter in the same ffmpeg pipeline,
   which emits ``silence_start: <t>`` / ``silence_end: <t>`` lines on
   stderr (relative seconds from stream start).

A line-oriented stderr parser, running in a sidecar thread per camera,
classifies each segment file as it closes:

  - **Has non-silent intervals** → move from ``staging/`` to
    ``<intake>/{YYYY-MM-DD}/`` and append a manifest entry with the
    file's content hashes plus the silence/voice intervals it contains.
  - **Pure silence** → unlink the file (transient runtime data, not a
    source-of-truth artifact) and append a manifest entry of type
    ``silent_window_pruned`` so the timeline of "we were monitoring
    but nothing happened" is preserved without keeping the audio.

Talkback suppression and YAMNet/Whisper integration are deliberately
NOT in this Phase 1c module — they hook in at later phases via the
manifest entries this writer produces.

Concurrency model
-----------------
One ``CameraAudioExtractor`` instance per camera. Each owns:

  - One ffmpeg subprocess (Popen).
  - One stderr-reader thread that pushes parsed events into a queue.
  - One worker thread that consumes the queue, watches segment-close
    boundaries, and finalizes segments to intake or prunes them.

The top-level ``ExtractorSupervisor`` polls the DB every ``poll_seconds``
and spawns/stops extractor instances to match the current set of
enabled audio-capable cameras.

Restart-on-failure: each extractor wraps its main loop in a backoff
retry, so transient camera dropouts (cable, reboot, WiFi) recover
automatically without restarting the whole service.
"""

import logging
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

# --- project imports ---
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from services.evidence.manifest import (
    EvidenceManifest,
    LITIGATION_ROOT,
    _utc_now_iso,
)
from services.streaming_hub import get_rtsp_source_url
from services.evidence.gate import evidence_collection_enabled

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------

POSTGREST_URL = os.environ.get("NVR_POSTGREST_URL", "http://postgrest:3001")

# Default ffmpeg settings. Overridable per-camera via DB column when needed.
DEFAULT_SEGMENT_SECONDS = 60          # one mp3 file per minute
DEFAULT_AUDIO_BITRATE   = "64k"       # plenty for speech, small footprint
DEFAULT_AUDIO_SR        = 16000       # 16 kHz mono — Whisper-native, YAMNet-native
DEFAULT_SILENCE_DB      = -40.0       # below this = silent (RMS dBFS)
DEFAULT_SILENCE_DUR     = 2.0         # ≥ this many seconds of low-RMS = "silent"

# Backoff schedule for ffmpeg restarts after a crash.
BACKOFF_SCHEDULE = [5, 10, 30, 60, 120, 300]  # seconds; clamps at last value
BACKOFF_RESET_AFTER_SECONDS = 600              # if process ran > this, reset backoff

# Polling interval for the supervisor's DB watch loop.
SUPERVISOR_POLL_SECONDS = 30

# Patterns parsed out of ffmpeg stderr.
_SILENCE_START_RE = re.compile(r"silence_start:\s*([\d.]+)")
_SILENCE_END_RE   = re.compile(r"silence_end:\s*([\d.]+)")
_OPENING_RE       = re.compile(r"Opening\s+'([^']+)'\s+for\s+writing")


# ---------------------------------------------------------------------
# Per-segment bookkeeping
# ---------------------------------------------------------------------

@dataclass
class SegmentRecord:
    """All metadata accumulated for one segment file while it's still
    being written by ffmpeg. Finalized when the next segment opens."""

    seq: int                                # 0-based segment number from ffmpeg
    path: Path                              # full path to staging mp3
    start_walltime_utc: datetime            # wall-clock at segment start
    start_relative_s: float                 # seconds-from-stream-start at segment start
    silence_intervals: List[Tuple[float, float]] = field(default_factory=list)
    in_silence: bool = True                 # initial assumption — will be overridden
    silence_started_at_relative: Optional[float] = None

    def has_voice(self) -> bool:
        """Did this segment contain ANY non-silent audio?"""
        # If we never saw a silence_end during this segment AND we entered
        # the segment in silence AND we never received a silence_start
        # (which means silence is unbroken), we treat it as fully silent.
        # Otherwise voice was present at some point.
        return len(self.silence_intervals) < self._max_possible_silences_for_pure_silence()

    def _max_possible_silences_for_pure_silence(self) -> int:
        # A fully-silent segment would have at most 1 silence interval
        # spanning [start, end] (or zero, if silence carried over from
        # the prior segment without an end emitted).
        return 1

    def silent_fraction(self) -> float:
        """Fraction of segment duration spent in silence."""
        total_silent = sum(b - a for a, b in self.silence_intervals)
        seg_dur = (DEFAULT_SEGMENT_SECONDS or 60)
        return min(1.0, total_silent / seg_dur)


# ---------------------------------------------------------------------
# Per-camera extractor
# ---------------------------------------------------------------------

class CameraAudioExtractor:
    """One ffmpeg-driven extractor for one camera.

    Lifecycle::

        extractor = CameraAudioExtractor(camera, manifest_writer, ...)
        extractor.start()      # spawns threads + ffmpeg, returns immediately
        # ... runs forever, recovering from camera drops ...
        extractor.stop()       # signals shutdown, joins threads
    """

    def __init__(
        self,
        camera: Dict,
        manifest_writer: EvidenceManifest,
        intake_root: Path,
        silence_db_threshold: float = DEFAULT_SILENCE_DB,
        silence_min_duration: float = DEFAULT_SILENCE_DUR,
        segment_seconds: int = DEFAULT_SEGMENT_SECONDS,
    ):
        self.serial = camera["serial"]
        self.name = camera.get("name") or self.serial
        self.rtsp_url = get_rtsp_source_url(self.serial, camera)

        self.manifest = manifest_writer
        self.intake_root = Path(intake_root)
        self.staging_root = self.intake_root / "staging" / self.serial
        self.staging_root.mkdir(parents=True, exist_ok=True)

        self.silence_db = silence_db_threshold
        self.silence_dur = silence_min_duration
        self.segment_seconds = segment_seconds

        self._stop = threading.Event()
        self._proc: Optional[subprocess.Popen] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._supervisor_thread: Optional[threading.Thread] = None

        # Per-stream state. Reset every time ffmpeg restarts.
        self._stream_start_walltime: Optional[datetime] = None
        self._segments_by_path: Dict[Path, SegmentRecord] = {}
        self._current_segment: Optional[SegmentRecord] = None
        self._next_seq = 0

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------

    def start(self) -> None:
        """Spawn the supervisor thread (which spawns ffmpeg + reader)."""
        if self._supervisor_thread and self._supervisor_thread.is_alive():
            return
        self._stop.clear()
        self._supervisor_thread = threading.Thread(
            target=self._supervise_loop,
            name=f"evidence-{self.serial}-sup",
            daemon=True,
        )
        self._supervisor_thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        """Signal shutdown and wait for threads to exit."""
        self._stop.set()
        self._terminate_proc()
        if self._supervisor_thread:
            self._supervisor_thread.join(timeout=timeout)

    # -----------------------------------------------------------------
    # Supervisor loop — restart ffmpeg with backoff on crash
    # -----------------------------------------------------------------

    def _supervise_loop(self) -> None:
        backoff_idx = 0
        while not self._stop.is_set():
            started_at = time.monotonic()
            try:
                self._run_one_session()
            except Exception as e:
                logger.exception("[%s] extractor session crashed: %s",
                                 self.serial, e)
            ran_for = time.monotonic() - started_at
            if self._stop.is_set():
                return
            # If the session ran > BACKOFF_RESET_AFTER_SECONDS, treat the
            # next failure as a fresh first failure (reset backoff).
            if ran_for > BACKOFF_RESET_AFTER_SECONDS:
                backoff_idx = 0
            wait = BACKOFF_SCHEDULE[min(backoff_idx, len(BACKOFF_SCHEDULE) - 1)]
            backoff_idx += 1
            logger.warning("[%s] ffmpeg session ended after %.1fs; "
                           "restarting in %ds", self.serial, ran_for, wait)
            self._stop.wait(timeout=wait)

    def _run_one_session(self) -> None:
        """Spawn one ffmpeg, read its stderr, finalize segments. Returns
        when ffmpeg exits (cleanly or otherwise)."""
        self._reset_stream_state()
        cmd = self._build_ffmpeg_cmd()
        logger.info("[%s] spawning ffmpeg: %s", self.serial, " ".join(cmd))
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,            # line-buffered
        )
        self._stream_start_walltime = datetime.now(timezone.utc)
        try:
            assert self._proc.stderr is not None
            for line in self._proc.stderr:
                if self._stop.is_set():
                    break
                self._handle_stderr_line(line.rstrip("\n"))
        finally:
            rc = self._proc.wait()
            logger.info("[%s] ffmpeg exited rc=%d", self.serial, rc)
            # Finalize any in-flight segment so we don't leave it dangling
            self._finalize_in_flight_segment_on_session_end()

    def _build_ffmpeg_cmd(self) -> List[str]:
        # Output filename pattern: <staging>/<serial>_<seq:05d>.mp3
        # Sequential numbering keeps strftime out of ffmpeg's hands —
        # we map seq → wall-clock in Python where time math is reliable.
        out_pattern = str(self.staging_root / f"{self.serial}_%05d.mp3")
        return [
            "ffmpeg",
            "-loglevel", "info",
            "-rtsp_transport", "tcp",
            "-i", self.rtsp_url,
            "-vn",                                 # drop video
            "-acodec", "libmp3lame",
            "-ar", str(DEFAULT_AUDIO_SR),
            "-ac", "1",                            # mono
            "-b:a", DEFAULT_AUDIO_BITRATE,
            "-af", f"silencedetect=noise={self.silence_db}dB:"
                   f"d={self.silence_dur}",
            "-f", "segment",
            "-segment_time", str(self.segment_seconds),
            "-reset_timestamps", "1",
            out_pattern,
        ]

    # -----------------------------------------------------------------
    # ffmpeg stderr parsing
    # -----------------------------------------------------------------

    def _handle_stderr_line(self, line: str) -> None:
        # Three event types we care about; everything else just logged.
        m_open = _OPENING_RE.search(line)
        if m_open:
            self._handle_segment_open(Path(m_open.group(1)))
            return

        m_ss = _SILENCE_START_RE.search(line)
        if m_ss:
            self._handle_silence_start(float(m_ss.group(1)))
            return

        m_se = _SILENCE_END_RE.search(line)
        if m_se:
            self._handle_silence_end(float(m_se.group(1)))
            return

        # ffmpeg can be noisy; only log warnings/errors at INFO+, info at DEBUG.
        if "error" in line.lower() or "failed" in line.lower():
            logger.warning("[%s] ffmpeg: %s", self.serial, line)
        else:
            logger.debug("[%s] ffmpeg: %s", self.serial, line)

    def _handle_segment_open(self, path: Path) -> None:
        """ffmpeg has just begun writing to ``path``. The previously
        in-flight segment (if any) is now sealed and ready for finalize."""
        if self._current_segment is not None:
            self._finalize_segment(self._current_segment)
        # Compute wall-clock for this segment's start. ffmpeg cuts segments
        # at integer multiples of segment_seconds from stream start (give
        # or take a few ms of slop, which is ok for evidence purposes).
        seq = self._next_seq
        self._next_seq += 1
        start_rel = seq * self.segment_seconds
        assert self._stream_start_walltime is not None
        start_wall = self._stream_start_walltime + timedelta(seconds=start_rel)

        rec = SegmentRecord(
            seq=seq,
            path=path,
            start_walltime_utc=start_wall,
            start_relative_s=start_rel,
        )
        self._segments_by_path[path] = rec
        self._current_segment = rec

    def _handle_silence_start(self, t_rel: float) -> None:
        """A silence period began at ``t_rel`` seconds from stream start."""
        if self._current_segment is None:
            return
        # We're now in silence within the current segment.
        self._current_segment.silence_started_at_relative = t_rel

    def _handle_silence_end(self, t_rel: float) -> None:
        """A silence period ended at ``t_rel`` seconds from stream start.

        ffmpeg also emits ``silence_duration: <s>`` on the same line; we
        recompute from start vs end ourselves for consistency."""
        if self._current_segment is None:
            return
        seg = self._current_segment
        start = seg.silence_started_at_relative
        if start is None:
            # We didn't see the matching silence_start (could have been
            # in the prior segment or before stream start). Approximate
            # from segment start.
            start = seg.start_relative_s
        # Convert to segment-relative coordinates (clamp to segment bounds)
        seg_start = seg.start_relative_s
        seg_end = seg_start + self.segment_seconds
        clipped_a = max(seg_start, start)
        clipped_b = min(seg_end, t_rel)
        if clipped_b > clipped_a:
            seg.silence_intervals.append((clipped_a - seg_start,
                                          clipped_b - seg_start))
        seg.silence_started_at_relative = None  # exit silence

    def _finalize_in_flight_segment_on_session_end(self) -> None:
        """ffmpeg has exited. Finalize whatever segment was current."""
        if self._current_segment is None:
            return
        # Don't lose evidence on an unclean exit: finalize even if the
        # file is short/incomplete (ffmpeg's segment muxer flushes on exit).
        self._finalize_segment(self._current_segment)
        self._current_segment = None

    # -----------------------------------------------------------------
    # Segment finalization → manifest write + intake/staging move
    # -----------------------------------------------------------------

    def _finalize_segment(self, seg: SegmentRecord) -> None:
        """Decide silent vs voice, move/unlink accordingly, append manifest."""
        if not seg.path.exists():
            logger.warning("[%s] segment %s missing at finalize",
                           self.serial, seg.path)
            return

        had_voice = seg.has_voice()
        date_dir = seg.start_walltime_utc.strftime("%Y-%m-%d")
        intake_dated = self.intake_root / date_dir
        intake_dated.mkdir(parents=True, exist_ok=True)
        target_name = (
            seg.start_walltime_utc.strftime("%H-%M-%S") +
            f"_{self.serial}.mp3"
        )
        target_path = intake_dated / target_name

        try:
            if had_voice:
                shutil.move(str(seg.path), str(target_path))
                self._write_voice_manifest_entry(seg, target_path)
            else:
                # Pure silence: unlink the staging file (runtime data,
                # not source-of-truth — see RULE 14.5 reasoning) and
                # log the silent-window event for chain-of-custody
                # completeness.
                seg.path.unlink(missing_ok=True)
                self._write_silent_manifest_entry(seg)
        except Exception:
            logger.exception("[%s] finalize failed for %s",
                             self.serial, seg.path)

    def _write_voice_manifest_entry(self, seg: SegmentRecord,
                                    final_path: Path) -> None:
        st = final_path.stat()
        sha256 = self._sha256_of(final_path)
        self.manifest.append({
            "event_type": "audio_capture",
            "source": "audio_extractor",
            "camera_serial": self.serial,
            "camera_name": self.name,
            "timestamp_utc": seg.start_walltime_utc.strftime(
                "%Y-%m-%dT%H:%M:%S.000Z"),
            "duration_seconds": float(self.segment_seconds),
            "files": {
                "mp3": {
                    "path": str(final_path.relative_to(LITIGATION_ROOT)),
                    "sha256": sha256,
                    "bytes": st.st_size,
                },
            },
            "silence_intervals_seconds": [
                [round(a, 3), round(b, 3)] for a, b in seg.silence_intervals
            ],
            "silent_fraction": round(seg.silent_fraction(), 4),
            "video_reference": {
                "recording_table_match": {
                    "camera_serial": self.serial,
                    "from_utc": seg.start_walltime_utc.strftime(
                        "%Y-%m-%dT%H:%M:%S.000Z"),
                    "to_utc": (seg.start_walltime_utc.timestamp()
                               + self.segment_seconds),
                },
                "mp4_resolved_at_promotion": True,
            },
            "extractor_config": {
                "silence_db": self.silence_db,
                "silence_min_duration_seconds": self.silence_dur,
                "segment_seconds": self.segment_seconds,
                "audio_codec": "libmp3lame",
                "audio_bitrate": DEFAULT_AUDIO_BITRATE,
                "sample_rate_hz": DEFAULT_AUDIO_SR,
            },
        })

    def _write_silent_manifest_entry(self, seg: SegmentRecord) -> None:
        """Record the existence of the silent window so the timeline of
        'we were monitoring but nothing happened' is part of chain-of-custody."""
        self.manifest.append({
            "event_type": "silent_window_pruned",
            "source": "audio_extractor",
            "camera_serial": self.serial,
            "camera_name": self.name,
            "timestamp_utc": seg.start_walltime_utc.strftime(
                "%Y-%m-%dT%H:%M:%S.000Z"),
            "duration_seconds": float(self.segment_seconds),
            "silent_fraction": round(seg.silent_fraction(), 4),
        })

    @staticmethod
    def _sha256_of(path: Path) -> str:
        import hashlib
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return f"sha256:{h.hexdigest()}"

    # -----------------------------------------------------------------
    # Process management
    # -----------------------------------------------------------------

    def _reset_stream_state(self) -> None:
        self._stream_start_walltime = None
        self._segments_by_path.clear()
        self._current_segment = None
        self._next_seq = 0

    def _terminate_proc(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.send_signal(signal.SIGTERM)
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            except Exception:
                pass


# ---------------------------------------------------------------------
# Supervisor — watches the DB and spawns/stops per-camera extractors
# ---------------------------------------------------------------------

class ExtractorSupervisor:
    """Polls the DB for cameras that should be running and reconciles
    the live extractor set."""

    def __init__(
        self,
        manifest_writer: Optional[EvidenceManifest] = None,
        intake_root: Optional[Path] = None,
        poll_seconds: int = SUPERVISOR_POLL_SECONDS,
    ):
        self.manifest = manifest_writer or EvidenceManifest()
        self.intake_root = Path(intake_root or LITIGATION_ROOT / "intake")
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._extractors: Dict[str, CameraAudioExtractor] = {}

    def run_forever(self) -> None:
        logger.info("evidence supervisor starting; intake=%s, poll=%ds",
                    self.intake_root, self.poll_seconds)
        # Ensure manifest has its genesis entry (idempotent).
        self.manifest.ensure_genesis(operator_user_id="evidence_pipeline_service")
        try:
            while not self._stop.is_set():
                try:
                    self._reconcile()
                except Exception:
                    logger.exception("supervisor reconcile failed; "
                                     "will retry in %ds", self.poll_seconds)
                self._stop.wait(timeout=self.poll_seconds)
        finally:
            self._shutdown_all()

    def stop(self) -> None:
        self._stop.set()

    def _reconcile(self) -> None:
        # GLOBAL MASTER SWITCH (beta, default OFF). When the pipeline is
        # globally disabled, tear down any running extractors and start
        # nothing — no RTSP audio taps, no clips written, no inference fed.
        # Re-checked every poll so flipping the switch on/off takes effect
        # without a container restart. See services/evidence/gate.py.
        if not evidence_collection_enabled():
            if self._extractors:
                logger.info(
                    "evidence collection globally disabled — stopping %d extractor(s)",
                    len(self._extractors))
                self._shutdown_all()
            return

        wanted = self._fetch_wanted_cameras()
        wanted_serials = {c["serial"] for c in wanted}

        # Stop extractors no longer wanted
        for serial in list(self._extractors.keys()):
            if serial not in wanted_serials:
                logger.info("stopping extractor for %s (no longer enabled)",
                            serial)
                self._extractors[serial].stop()
                del self._extractors[serial]

        # Start extractors that should be running but aren't
        for cam in wanted:
            serial = cam["serial"]
            if serial in self._extractors:
                continue
            logger.info("starting extractor for %s (%s)",
                        serial, cam.get("name") or serial)
            ext = CameraAudioExtractor(
                camera=cam,
                manifest_writer=self.manifest,
                intake_root=self.intake_root,
                silence_db_threshold=cam.get("silence_db_threshold")
                                      or DEFAULT_SILENCE_DB,
            )
            ext.start()
            self._extractors[serial] = ext

    def _fetch_wanted_cameras(self) -> List[Dict]:
        """Cameras that satisfy: enabled=TRUE, capture_audio=TRUE,
        cameras.audio_input_supported=TRUE.

        We use PostgREST's foreign-key embedding to fetch both rows
        in one request:

            /evidence_camera_settings?enabled=eq.true
              &capture_audio=eq.true
              &select=*,cameras(*)
        """
        url = (
            f"{POSTGREST_URL}/evidence_camera_settings"
            f"?enabled=eq.true&capture_audio=eq.true"
            f"&select=serial,silence_db_threshold,cameras(*)"
        )
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        rows = r.json()
        cams: List[Dict] = []
        for row in rows:
            cam = row.get("cameras") or {}
            if not cam:
                continue
            if not cam.get("audio_input_supported"):
                continue
            cam["silence_db_threshold"] = row.get("silence_db_threshold")
            cams.append(cam)
        return cams

    def _shutdown_all(self) -> None:
        for serial, ext in list(self._extractors.items()):
            ext.stop()
            del self._extractors[serial]


# ---------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------

def _install_signal_handlers(sup: ExtractorSupervisor) -> None:
    def _handler(signum, _frame):
        logger.info("received signal %s — shutting down", signum)
        sup.stop()
    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("NVR_LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    sup = ExtractorSupervisor()
    _install_signal_handlers(sup)
    sup.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
