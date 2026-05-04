"""
Whisper transcription service — Phase 3 of the Evidence Collection
Pipeline.

Why this service exists
=======================

The Phase 1c audio extractor produces 16 kHz mono mp3 segments
whenever a camera with evidence collection enabled hears non-silent
audio. The Phase 2 YAMNet classifier flags clips where loud acoustic
events occur (screams, impacts, etc.). Neither tells you *what
people said*. That's this service's job.

For a litigation use case, the transcript is the single most important
artifact: it's what the operator (or their attorney) actually reads.
For a Child-Monitor use case, transcripts let "is this child crying
because they're hurt" be answered after the fact. For both, the
transcript is also what gets ingested into Anamnesis so semantic
search across months of household audio becomes possible.

What this service does
======================

It is a long-running daemon that:

  1. **Polls the evidence manifest** for ``audio_capture`` events
     that have not yet been transcribed.
  2. For each event:
     - Loads the mp3 from ``/litigation/intake/<date>/...``.
     - Runs **openai-whisper "medium"** with ``--fp16 False`` and
       language auto-detection (home audio is FR/EN/ES/PT-mixed per
       user profile — auto-detect handles code-switching better than
       a forced language).
     - Runs **openai-whisper "tiny"** in parallel on the same clip
       to mitigate the known "medium drops the first 30s of long
       audio" failure mode (per ``server-legal`` MSG-131 ACK).
     - Applies the **hallucination guard**: drops any segment with
       ``no_speech_prob >= 0.7`` OR ``avg_logprob <= -1.0`` (per the
       2026-03-13 episode where Whisper produced "Good night" over
       silence with ``no_speech_prob > 0.97``).
  3. **Writes derived files** alongside the mp3:
     - ``<basename>.json`` — full Whisper output (segments, tokens,
       log-probs, language detection). Source of truth for the
       transcript; preserves all the data needed to re-filter or
       re-analyze later without re-running the model.
     - ``<basename>.txt`` — cleaned plain-text transcript, just the
       kept segments concatenated. The thing the operator reads.
  4. **Appends a ``transcription`` manifest entry** referencing the
     original ``audio_capture`` by manifest_id, with file hashes,
     model parameters, segment counts, language, and the kept
     transcript excerpt.
  5. **Updates the ``audio_events`` DB row** with
     ``transcript_excerpt`` and ``anamnesis_id`` so the queryable
     index reflects the new state.
  6. **Ingests the transcript into Anamnesis** as an episode tagged
     with the camera, time range, and case_id (if known) — making
     semantic search over the household corpus possible.

Why this isn't a one-shot
=========================

Whisper on CPU is slow (medium runs ~0.5× realtime on the dual Xeons,
so a 60s clip takes ~2 min). With 13 audio-capable cameras producing
clips as fast as the extractor's silence-pruning lets through, we
cannot transcribe in the same process / thread that captures: the
backlog would explode.

Running this as an independent daemon means:
  * Capture and classification proceed at their own pace, never
    blocked on Whisper.
  * Whisper processes the backlog at its sustainable rate.
  * If the host falls behind, the manifest backlog is the queue —
    nothing is lost.

Future optimization: route to GPU
=================================

The big win would be running Whisper on the office RX 6800 (16 GB
VRAM) via the existing Anamnesis trainer endpoint. That would lift
``--fp16 False`` (the RX 6800 has hardware FP16) and roughly 5× the
throughput. Out of scope for this initial implementation; doable as
a future drop-in by swapping the inference backend in
``_transcribe_one``.

Deployment requirements
=======================

Inside the unified-nvr container:

  * ``pip install openai-whisper`` — the model + inference runtime
    (~1.5 GB after pip + first-run model download).
  * On first run the medium and tiny model files are downloaded to
    ``~/.cache/whisper/`` (or wherever the env var ``XDG_CACHE_HOME``
    points). Allow ~1.5 GB for that cache.
  * ffmpeg is already a project dep (used elsewhere) — Whisper
    invokes it under the hood for audio loading.
"""

# ----- standard library --------------------------------------------------
import json                                     # state file + JSON outputs
import os                                       # env vars, paths
import socket                                   # default Anamnesis instance ID
import sys                                      # path bootstrap
from dataclasses import dataclass, field        # value object
from datetime import datetime, timezone         # iso timestamps
from pathlib import Path                        # all paths are pathlib
from typing import Any, Dict, List, Optional, Tuple  # type hints

# ----- third party (lazy-loaded inside _lazy_load_models) ----------------
# whisper, torch — heavy. Imported inside the loader so simply
# importing THIS module doesn't pull them in.

# ----- evidence package internals ----------------------------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from services.evidence.base import EvidenceService
from services.evidence.anamnesis_client import AnamnesisClient


# =========================================================================
# Module-level configuration
# =========================================================================

# Whisper model names. Medium is the workhorse; tiny covers the
# first-30s drop. These map to openai-whisper's named model
# checkpoints (downloaded automatically on first use).
DEFAULT_PRIMARY_MODEL: str = "medium"
DEFAULT_FIRST30_FALLBACK_MODEL: str = "tiny"

# Hallucination guard thresholds. Per the 2026-03-13 episode where
# Whisper produced "Good night" over silence with
# ``no_speech_prob ~ 0.97``. Both filters apply (logical OR — if EITHER
# is violated, the segment is dropped from the .txt file but kept in
# the .json for forensic re-analysis).
HALLUCINATION_NO_SPEECH_PROB_MAX: float = 0.7   # drop if >= this
HALLUCINATION_AVG_LOGPROB_MIN: float = -1.0     # drop if <= this

# How long the first-30s "drop" window is. Per MSG-131, Whisper medium
# sometimes silently omits this leading window. We patch it from tiny's
# output if present.
FIRST30_WINDOW_SECONDS: float = 30.0

# Polling interval. Same reasoning as the YAMNet classifier — captures
# arrive at most once per minute per camera, so 30 s keeps us close
# to real time without spinning the CPU.
DEFAULT_POLL_SECONDS: float = 30.0

# Maximum transcript length stored in the audio_events.transcript_excerpt
# column. The full transcript lives in the .txt file. The DB excerpt
# is for fast text search / display.
TRANSCRIPT_EXCERPT_MAX_CHARS: int = 300

# Anamnesis instance ID for ingested transcript episodes. Each evidence-
# pipeline instance should have a stable identity so episodes are
# attributable. Defaults to hostname; override via env var.
DEFAULT_ANAMNESIS_INSTANCE: str = os.environ.get(
    "ANAMNESIS_INSTANCE_ID",
    f"dellserver-nvr-evidence",
)

# Anamnesis project tag for these episodes. Lets users filter "the
# evidence-pipeline transcripts" specifically when searching.
DEFAULT_ANAMNESIS_PROJECT: str = "0_MOBIUS.NVR.evidence"


# =========================================================================
# TranscriptionResult — value object passed around internally
# =========================================================================

@dataclass
class TranscriptionResult:
    """All artifacts produced for one transcribed clip."""

    audio_capture_manifest_id: int          # links back to the capture event
    mp3_relative_path: str                  # path under LITIGATION_ROOT

    # Outputs of Whisper (post-merge of medium + tiny first-30s)
    language_detected: str = ""             # e.g. "en", "fr"
    full_segments: List[Dict[str, Any]] = field(default_factory=list)
    kept_segments: List[Dict[str, Any]] = field(default_factory=list)
    full_text: str = ""                     # all kept segments concatenated
    excerpt: str = ""                       # first N chars of full_text

    # Counts for the manifest entry
    segments_total: int = 0
    segments_kept: int = 0
    segments_dropped_no_speech: int = 0
    segments_dropped_low_logprob: int = 0
    first30_patched_from_tiny: bool = False

    # Files written to /litigation/intake/<date>/
    json_relative_path: str = ""
    txt_relative_path: str = ""

    # Anamnesis ingest result
    anamnesis_episode_id: Optional[str] = None

    # Wall-clock timestamp at which transcription completed
    transcribed_at_utc: str = ""


# =========================================================================
# WhisperTranscriberService — the concrete service
# =========================================================================

class WhisperTranscriberService(EvidenceService):
    """
    Long-running daemon that transcribes audio captures, writes
    transcripts to disk, updates the queryable index, and ingests
    every kept-text into Anamnesis.

    Construct, ``start()``, and let it run.

    State / resume
    --------------
    Tracks last-transcribed manifest_id at
    ``/litigation/.whisper_transcriber_state.json``. Resumes from there
    on restart. Idempotent at the manifest layer (writes new entries,
    never mutates) and at the DB layer (PATCH with conflict-tolerant
    Prefer header).

    Failure isolation
    -----------------
    A per-clip exception is logged and the cursor advances anyway.
    One failed clip doesn't block the rest of the backlog. A separate
    "retry failed" tool can be built later (it'd identify clips with
    audio_capture entries but no transcription entries).
    """

    def __init__(
        self,
        manifest=None,
        litigation_root=None,
        # Model parameters
        primary_model_name: str = DEFAULT_PRIMARY_MODEL,
        first30_fallback_model_name: str = DEFAULT_FIRST30_FALLBACK_MODEL,
        # Daemon parameters
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        # Anamnesis ingestion
        anamnesis: Optional[AnamnesisClient] = None,
        anamnesis_instance: str = DEFAULT_ANAMNESIS_INSTANCE,
        anamnesis_project: str = DEFAULT_ANAMNESIS_PROJECT,
        anamnesis_ingest_enabled: bool = True,
        # DB integration
        postgrest_url: Optional[str] = None,
    ) -> None:
        super().__init__(manifest=manifest, litigation_root=litigation_root)
        self.primary_model_name = primary_model_name
        self.first30_fallback_model_name = first30_fallback_model_name
        self.poll_seconds = float(poll_seconds)
        self.anamnesis = anamnesis or AnamnesisClient()
        self.anamnesis_instance = anamnesis_instance
        self.anamnesis_project = anamnesis_project
        self.anamnesis_ingest_enabled = anamnesis_ingest_enabled
        self.postgrest_url = (
            postgrest_url
            or os.environ.get("NVR_POSTGREST_URL")
            or "http://postgrest:3001"
        )

        # State file: tracks last transcribed manifest_id.
        self._state_path: Path = (
            self.litigation_root / ".whisper_transcriber_state.json"
        )

        # Lazy-loaded model handles. Loading medium takes ~10s and
        # ~1.5 GB RAM; we do it once on first run() call and keep
        # the handles for the daemon's lifetime.
        self._primary_model = None
        self._first30_model = None

    # -----------------------------------------------------------------
    # EvidenceService.run — the daemon loop
    # -----------------------------------------------------------------

    def run(self) -> None:
        """Long-running poll loop. Same shape as YAMNet classifier."""
        try:
            self._lazy_load_models()
        except Exception as e:
            self.log.error(
                "failed to load Whisper models — service cannot run. "
                "Install: `pip install openai-whisper`. Cause: %s", e,
            )
            return

        self.log.info(
            "whisper transcriber ready: primary=%s, fallback=%s, "
            "anamnesis=%s, poll=%.1fs",
            self.primary_model_name,
            self.first30_fallback_model_name,
            "on" if self.anamnesis_ingest_enabled else "off",
            self.poll_seconds,
        )

        last_id = self._load_last_id()
        while not self._stop.is_set():
            try:
                last_id = self._transcribe_pending(last_id)
            except Exception:
                self.log.exception(
                    "transcribe pass failed; will retry next interval")
            self._stop.wait(timeout=self.poll_seconds)

    # -----------------------------------------------------------------
    # Backlog drain
    # -----------------------------------------------------------------

    def _transcribe_pending(self, last_id: int) -> int:
        """Walk manifest forward; transcribe every audio_capture."""
        new_last = last_id
        for entry in self.manifest.iter_entries(from_id=last_id + 1):
            if self._stop.is_set():
                break
            mid = entry["manifest_id"]
            if entry.get("event_type") == "audio_capture":
                try:
                    self._transcribe_one(entry)
                except Exception:
                    self.log.exception(
                        "failed to transcribe audio_capture id=%d; skipping",
                        mid,
                    )
            new_last = mid
            if new_last != last_id:
                self._save_last_id(new_last)
                last_id = new_last
        return new_last

    def _transcribe_one(self, capture_entry: Dict[str, Any]) -> None:
        """Run the full transcribe-and-publish pipeline for one capture."""
        capture_id = capture_entry["manifest_id"]
        mp3_rel_path = capture_entry["files"]["mp3"]["path"]
        mp3_abs_path = self.litigation_root / mp3_rel_path
        if not mp3_abs_path.exists():
            self.log.warning(
                "audio_capture id=%d points at missing mp3 %s; skipping",
                capture_id, mp3_abs_path,
            )
            return

        # 1. Run inference (primary + tiny first-30s patch)
        primary, tiny, lang = self._inference(mp3_abs_path)
        merged_segments, first30_patched = self._merge_first30(primary, tiny)

        # 2. Apply hallucination guard
        kept, dropped_ns, dropped_lp = self._apply_hallucination_guard(
            merged_segments
        )
        full_text = " ".join(s.get("text", "").strip() for s in kept).strip()
        excerpt = full_text[:TRANSCRIPT_EXCERPT_MAX_CHARS]

        # 3. Build the result object
        result = TranscriptionResult(
            audio_capture_manifest_id=capture_id,
            mp3_relative_path=mp3_rel_path,
            language_detected=lang,
            full_segments=merged_segments,
            kept_segments=kept,
            full_text=full_text,
            excerpt=excerpt,
            segments_total=len(merged_segments),
            segments_kept=len(kept),
            segments_dropped_no_speech=dropped_ns,
            segments_dropped_low_logprob=dropped_lp,
            first30_patched_from_tiny=first30_patched,
            transcribed_at_utc=datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.000Z"),
        )

        # 4. Write the .json (full output) and .txt (cleaned text) files
        self._write_derived_files(mp3_abs_path, result)

        # 5. Append manifest entry
        self._append_transcription_manifest_entry(capture_entry, result)

        # 6. Update audio_events DB row (best-effort)
        try:
            self._update_audio_event_row(capture_entry, result)
        except Exception:
            self.log.exception(
                "audio_events update failed for capture id=%d "
                "(continuing; manifest already written)", capture_id,
            )

        # 7. Ingest into Anamnesis (best-effort)
        if self.anamnesis_ingest_enabled and result.full_text:
            try:
                ep = self._ingest_into_anamnesis(capture_entry, result)
                result.anamnesis_episode_id = ep.get("episode_id") if ep else None
                # Patch the manifest entry with the assigned episode_id —
                # actually we DON'T mutate; instead log a follow-up
                # entry. Since we already wrote the manifest entry above,
                # adding a separate "anamnesis_link" event is cleaner
                # than retro-mutating.
                if result.anamnesis_episode_id:
                    self.log_lifecycle_event(
                        event_type="anamnesis_link",
                        audio_capture_manifest_id=capture_id,
                        anamnesis_episode_id=result.anamnesis_episode_id,
                    )
            except Exception:
                self.log.exception(
                    "anamnesis ingest failed for capture id=%d "
                    "(continuing)", capture_id,
                )

        self.log.info(
            "[id=%d] %s — lang=%s segs=%d/%d (dropped %d/no_speech, %d/lp)"
            "%s anamnesis=%s",
            capture_id, mp3_rel_path, lang or "?",
            result.segments_kept, result.segments_total,
            dropped_ns, dropped_lp,
            " (first30 patched)" if first30_patched else "",
            result.anamnesis_episode_id or "—",
        )

    # -----------------------------------------------------------------
    # Whisper inference
    # -----------------------------------------------------------------

    def _lazy_load_models(self) -> None:
        """Load both Whisper models. Heavy — done once at first run()."""
        if self._primary_model is not None:
            return
        try:
            import whisper  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "openai-whisper is not installed. Install with "
                "`pip install openai-whisper`."
            ) from e
        self.log.info("loading whisper primary=%s ...",
                      self.primary_model_name)
        self._primary_model = whisper.load_model(self.primary_model_name)
        self.log.info("loading whisper fallback=%s ...",
                      self.first30_fallback_model_name)
        self._first30_model = whisper.load_model(
            self.first30_fallback_model_name)
        self.log.info("whisper models loaded")

    def _inference(
        self,
        mp3_path: Path,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
        """
        Run primary and fallback models, return (primary_segments,
        tiny_segments, detected_language).

        ``--fp16 False`` is unconditional: dellserver has no GPU, so
        we're on CPU which uses FP32 anyway, AND the office GTX 1660
        baseline (per MSG-131) requires ``fp16=False`` to avoid NaN
        token output. Same flag, both reasons.

        ``language`` is intentionally not passed: home audio is
        FR/EN/ES/PT-mixed per the user profile, and Whisper's auto-
        detection handles code-switching better than forcing one
        language.
        """
        common = dict(
            fp16=False,
            verbose=False,
            word_timestamps=True,
        )
        # Primary model (medium): produces the canonical transcript.
        primary_result = self._primary_model.transcribe(
            str(mp3_path), **common
        )
        # Fallback (tiny) only needs to give us the first 30s in case
        # medium silently dropped it. Running it on the full clip is
        # cheap (~10x faster than medium) — keeps the code simple.
        tiny_result = self._first30_model.transcribe(
            str(mp3_path), **common
        )
        return (
            list(primary_result.get("segments", [])),
            list(tiny_result.get("segments", [])),
            primary_result.get("language", "") or "",
        )

    @staticmethod
    def _merge_first30(
        primary_segments: List[Dict[str, Any]],
        tiny_segments: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """
        If primary has nothing in [0, 30s] but tiny does, prepend tiny's
        first-30s segments. Returns (merged_list, was_patched).

        Per MSG-131 ACK from server-legal: "First 30 seconds of long
        audio sometimes drop with medium; we covered the gap with a
        parallel tiny model pass."
        """
        primary_has_first30 = any(
            s.get("start", 0) < FIRST30_WINDOW_SECONDS
            for s in primary_segments
        )
        if primary_has_first30:
            return primary_segments, False
        tiny_first30 = [
            s for s in tiny_segments
            if s.get("start", 0) < FIRST30_WINDOW_SECONDS
        ]
        if not tiny_first30:
            # Neither model heard speech in [0, 30s]. Probably actually
            # silent. Not a patch case.
            return primary_segments, False
        # Mark patched segments so the manifest reflects which model
        # contributed which text.
        patched = [
            {**s, "patched_from": "tiny"} for s in tiny_first30
        ]
        return patched + primary_segments, True

    @staticmethod
    def _apply_hallucination_guard(
        segments: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], int, int]:
        """
        Filter out hallucinated segments per the canonical rule:

          * ``no_speech_prob >= 0.7`` → likely silence-hallucination
          * ``avg_logprob <= -1.0``   → low-confidence garbage

        Returns (kept_segments, num_dropped_no_speech, num_dropped_logprob).

        Note: drops are NOT deletions. The full segment list is still
        written to the .json file. Only the .txt file (the human-
        readable transcript) and the audio_events.transcript_excerpt
        use the filtered subset.
        """
        kept: List[Dict[str, Any]] = []
        dropped_ns = 0
        dropped_lp = 0
        for s in segments:
            if s.get("no_speech_prob", 0.0) >= HALLUCINATION_NO_SPEECH_PROB_MAX:
                dropped_ns += 1
                continue
            if s.get("avg_logprob", 0.0) <= HALLUCINATION_AVG_LOGPROB_MIN:
                dropped_lp += 1
                continue
            kept.append(s)
        return kept, dropped_ns, dropped_lp

    # -----------------------------------------------------------------
    # Output writes
    # -----------------------------------------------------------------

    def _write_derived_files(
        self,
        mp3_abs_path: Path,
        result: TranscriptionResult,
    ) -> None:
        """Write <basename>.json and <basename>.txt next to the mp3."""
        json_path = mp3_abs_path.with_suffix(".json")
        txt_path = mp3_abs_path.with_suffix(".txt")

        # Use archive_to from the base class to preserve any pre-
        # existing transcript files before overwriting (rare — would
        # only happen on re-transcription).
        self.archive_to(json_path, reason="re-transcribed")
        self.archive_to(txt_path, reason="re-transcribed")

        # Full Whisper output: segments, language, model parameters.
        json_payload = {
            "audio_capture_manifest_id": result.audio_capture_manifest_id,
            "mp3_relative_path": result.mp3_relative_path,
            "language_detected": result.language_detected,
            "model": {
                "primary": self.primary_model_name,
                "first30_fallback": self.first30_fallback_model_name,
                "fp16": False,
            },
            "hallucination_guard": {
                "no_speech_prob_threshold": HALLUCINATION_NO_SPEECH_PROB_MAX,
                "avg_logprob_threshold": HALLUCINATION_AVG_LOGPROB_MIN,
            },
            "first30_patched_from_tiny": result.first30_patched_from_tiny,
            "segments_total": result.segments_total,
            "segments_kept": result.segments_kept,
            "segments_dropped_no_speech": result.segments_dropped_no_speech,
            "segments_dropped_low_logprob": result.segments_dropped_low_logprob,
            # Full segments include the dropped ones, with their
            # no_speech_prob / avg_logprob preserved so a human can
            # audit the filter's decisions.
            "full_segments": result.full_segments,
            "kept_segments_indices": [
                i for i, s in enumerate(result.full_segments)
                if s in result.kept_segments
            ],
            "transcribed_at_utc": result.transcribed_at_utc,
        }
        json_path.write_text(json.dumps(json_payload, indent=2,
                                        ensure_ascii=False))
        # Plain transcript: just the kept segments' text. This is what
        # the operator reads and what gets piped into Anamnesis.
        txt_path.write_text(result.full_text + "\n")

        result.json_relative_path = str(
            json_path.relative_to(self.litigation_root))
        result.txt_relative_path = str(
            txt_path.relative_to(self.litigation_root))

    def _append_transcription_manifest_entry(
        self,
        capture_entry: Dict[str, Any],
        result: TranscriptionResult,
    ) -> None:
        """Add a new ``transcription`` event referencing the capture."""
        # Compute file hashes so the manifest entry has chain-of-custody
        # integrity for the derived files too.
        json_abs = self.litigation_root / result.json_relative_path
        txt_abs = self.litigation_root / result.txt_relative_path
        json_sha = self._sha256_of_file(json_abs)
        txt_sha = self._sha256_of_file(txt_abs)
        json_size = json_abs.stat().st_size
        txt_size = txt_abs.stat().st_size

        self.log_lifecycle_event(
            event_type="transcription",
            audio_capture_manifest_id=result.audio_capture_manifest_id,
            camera_serial=capture_entry.get("camera_serial"),
            camera_name=capture_entry.get("camera_name"),
            language_detected=result.language_detected,
            transcriber="openai-whisper",
            transcriber_models={
                "primary": self.primary_model_name,
                "first30_fallback": self.first30_fallback_model_name,
            },
            fp16_disabled=True,
            first30_patched_from_tiny=result.first30_patched_from_tiny,
            segments_total=result.segments_total,
            segments_kept=result.segments_kept,
            segments_dropped_no_speech=result.segments_dropped_no_speech,
            segments_dropped_low_logprob=result.segments_dropped_low_logprob,
            hallucination_guard_thresholds={
                "no_speech_prob_max": HALLUCINATION_NO_SPEECH_PROB_MAX,
                "avg_logprob_min": HALLUCINATION_AVG_LOGPROB_MIN,
            },
            files={
                "json": {"path": result.json_relative_path,
                         "sha256": json_sha, "bytes": json_size},
                "txt": {"path": result.txt_relative_path,
                        "sha256": txt_sha, "bytes": txt_size},
            },
            transcript_excerpt=result.excerpt,
            transcribed_at_utc=result.transcribed_at_utc,
        )

    def _update_audio_event_row(
        self,
        capture_entry: Dict[str, Any],
        result: TranscriptionResult,
    ) -> None:
        """
        PATCH the audio_events row (if it exists) to add the transcript
        excerpt and Anamnesis episode id.

        The row may not exist yet — YamnetClassifierService only
        creates one when a category matches. If no row exists, this
        transcription is for an unflagged clip and we silently skip
        the DB update. The transcript still lives in the manifest +
        on disk.
        """
        import requests  # type: ignore

        url = (f"{self.postgrest_url}/audio_events"
               f"?manifest_id=eq.{result.audio_capture_manifest_id}")
        payload = {
            "transcript_excerpt": result.excerpt,
            "anamnesis_id": result.anamnesis_episode_id,
        }
        r = requests.patch(
            url, json=payload,
            headers={"Prefer": "return=minimal"},
            timeout=10,
        )
        # 200/204 = ok; 404 = no row to patch (unflagged clip), fine.
        if r.status_code not in (200, 204, 404):
            r.raise_for_status()

    def _ingest_into_anamnesis(
        self,
        capture_entry: Dict[str, Any],
        result: TranscriptionResult,
    ) -> Optional[Dict[str, Any]]:
        """
        Send the transcribed text into Anamnesis as a new episode.

        The episode is tagged so a future search can filter to "just
        evidence-pipeline transcripts from camera X" or "just my
        marital case audio in date range Y".
        """
        episode = {
            "instance": self.anamnesis_instance,
            "project": self.anamnesis_project,
            "summary": result.excerpt,
            # ``raw_exchange`` is Anamnesis's free-form body field.
            # We pack the full transcript plus structured metadata so
            # future searches can recover camera, time, language, etc.
            "raw_exchange": json.dumps({
                "kind": "evidence_transcript",
                "camera_serial": capture_entry.get("camera_serial"),
                "camera_name": capture_entry.get("camera_name"),
                "captured_at_utc": capture_entry.get("timestamp_utc"),
                "duration_seconds": capture_entry.get("duration_seconds"),
                "language_detected": result.language_detected,
                "audio_capture_manifest_id": result.audio_capture_manifest_id,
                "mp3_relative_path": result.mp3_relative_path,
                "transcript": result.full_text,
            }, ensure_ascii=False),
            # Tags help retrieval filters. ``litigation`` makes
            # WeeklySummarizer-style queries that DON'T want this corpus
            # easy to exclude.
            "tags": ["evidence_transcript", "litigation",
                     f"camera:{capture_entry.get('camera_serial', '?')}"],
        }
        try:
            return self.anamnesis.ingest_episode(episode)
        except Exception:
            self.log.exception("Anamnesis ingest_episode call failed")
            return None

    # -----------------------------------------------------------------
    # Tiny utilities
    # -----------------------------------------------------------------

    @staticmethod
    def _sha256_of_file(path: Path) -> str:
        """SHA-256 hex digest, prefixed with ``sha256:``."""
        import hashlib
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return f"sha256:{h.hexdigest()}"

    # -----------------------------------------------------------------
    # State persistence (last transcribed manifest_id)
    # -----------------------------------------------------------------

    def _load_last_id(self) -> int:
        """Return last transcribed manifest_id, or -1 if none."""
        if not self._state_path.exists():
            return -1
        try:
            data = json.loads(self._state_path.read_text())
            return int(data.get("last_transcribed_manifest_id", -1))
        except (json.JSONDecodeError, ValueError, OSError):
            self.log.warning(
                "state file at %s is corrupt; restarting from manifest 0",
                self._state_path,
            )
            return -1

    def _save_last_id(self, manifest_id: int) -> None:
        """Persist progress atomically (write tmp, rename)."""
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "last_transcribed_manifest_id": int(manifest_id),
            "updated_at_utc": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.000Z"),
            "service": self.__class__.__name__,
        }))
        tmp.replace(self._state_path)


# =========================================================================
# CLI entrypoint
# =========================================================================

def main() -> int:
    import argparse
    import logging as _logging

    p = argparse.ArgumentParser(description="Whisper transcription daemon")
    p.add_argument("--primary-model", default=DEFAULT_PRIMARY_MODEL)
    p.add_argument("--first30-fallback-model",
                   default=DEFAULT_FIRST30_FALLBACK_MODEL)
    p.add_argument("--poll", type=float, default=DEFAULT_POLL_SECONDS)
    p.add_argument("--no-anamnesis", action="store_true",
                   help="skip Anamnesis ingestion (transcripts still "
                        "written to disk and manifest)")
    p.add_argument("--once", action="store_true",
                   help="drain backlog and exit (no daemon loop)")
    args = p.parse_args()

    _logging.basicConfig(
        level=os.environ.get("NVR_LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    svc = WhisperTranscriberService(
        primary_model_name=args.primary_model,
        first30_fallback_model_name=args.first30_fallback_model,
        poll_seconds=args.poll,
        anamnesis_ingest_enabled=not args.no_anamnesis,
    )
    if args.once:
        svc._lazy_load_models()
        last = svc._load_last_id()
        last = svc._transcribe_pending(last)
        svc._save_last_id(last)
        return 0
    svc.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
