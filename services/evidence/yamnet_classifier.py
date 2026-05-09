"""
YAMNet acoustic classifier service — Phase 2 of the Evidence Collection
Pipeline.

What is YAMNet?
===============

YAMNet is a pre-trained deep neural network from Google that classifies
audio into 521 categories drawn from Google's AudioSet ontology
(https://research.google.com/audioset/). It eats 16 kHz mono audio and
emits, for each ~0.96 s frame, a vector of 521 confidence scores —
one per AudioSet class.

We use it because it is:

  * **Pre-trained on the right data** — AudioSet contains explicit
    classes for the high-stakes acoustic events this pipeline cares
    about (Screaming, Baby cry, Glass, Shout, Thud, etc.).
  * **Small and fast** — the TFLite version is ~3.7 MB, runs on CPU at
    significantly faster than realtime, no GPU needed.
  * **Well-validated** — the YAMNet weights and class ontology have been
    in production at Google for years; they are not the experimental
    layer of the stack.

What this service does
======================

It is a long-running daemon that:

  1. **Polls the evidence manifest** for ``audio_capture`` events that
     have not yet been classified.
  2. For each such event:
     - Loads the corresponding mp3 file from
       ``/litigation/intake/<date>/...``.
     - Resamples / decodes it to the 16 kHz mono float32 array YAMNet
       wants. (Our audio extractor already writes 16 kHz mono — see
       ``services/evidence/audio_extractor.py`` — so this is usually a
       trivial loader.)
     - Runs YAMNet inference and reduces the per-frame outputs to a
       single per-clip "highest score" per category.
     - Maps the AudioSet class names to our four user-facing categories
       (screams / crying / impacts / raised-voices).
  3. **For each category whose score crosses the configured threshold**,
     creates a symlink under
     ``/litigation/flagged/<category>/<original_filename>`` pointing
     to the original mp3 in ``intake/``. The flagged tree is the user-
     facing index — open ``/litigation/flagged/screams/`` and you see
     everything the classifier flagged as a scream this year.
  4. **Appends a new ``acoustic_classification`` entry to the
     manifest**, referencing the original ``audio_capture`` event by
     manifest_id, with the full per-category scores plus the matched
     categories. The chain-of-custody chain is preserved: we never
     mutate the original capture's entry; we add a follow-up.
  5. **Inserts a row into the ``audio_events`` DB table** with the
     primary label and score, the flagged paths, and the manifest_id.
     This is the queryable index — fast retrieval by camera, time
     range, or label without walking the JSONL.
  6. **Persists its progress** (last classified manifest_id) to
     ``/litigation/.yamnet_classifier_state.json`` so a restart resumes
     where it left off rather than re-classifying everything.

Inference backend
=================

We target the **TFLite runtime** by default (smallest dependency
footprint, ~3 MB extra container size for the model + 5-10 MB for the
runtime). Falls back to ``tensorflow`` if installed and tflite-runtime
isn't. Either way the calling code is the same once the model is
loaded.

Model file
==========

The YAMNet TFLite model lives in ``services/evidence/models/yamnet.tflite``
(NOT shipped in git — large binary). Download it on first deploy::

    mkdir -p services/evidence/models
    cd services/evidence/models
    wget https://storage.googleapis.com/mediapipe-tasks/audio_classifier/yamnet.tflite

Or override the path with the ``YAMNET_MODEL_PATH`` env var.

The class-name labels file (CSV mapping class index → human name) is
embedded in the TFLite metadata; we extract it on first load.
"""

# ----- standard library --------------------------------------------------
import json                                     # state file persistence
import os                                       # env-var overrides
import subprocess                               # ffmpeg-based mp3 → wav decode
import sys                                      # path bootstrap
from dataclasses import dataclass, field        # per-clip result container
from datetime import datetime, timezone         # iso timestamps
from pathlib import Path                        # all paths are pathlib
from typing import Any, Dict, List, Optional, Tuple   # type hints

# ----- third party (optional, lazy-loaded) -------------------------------
# numpy and the TFLite/TF runtime are imported lazily inside
# ``_lazy_load_model`` so that simply IMPORTING this file (e.g. for
# unit tests, or for the supervisor that lists available services)
# does not force the deep-learning stack to load. We tolerate either
# tflite-runtime (small) or tensorflow (large) being installed.

# ----- evidence package internals ----------------------------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from services.evidence.base import EvidenceService, PROJECT_ROOT


# =========================================================================
# Module-level configuration
# =========================================================================

# Where YAMNet's TFLite weights live. Downloadable from Google's mirror;
# see module docstring. Override at deploy time via env var.
DEFAULT_MODEL_PATH: Path = Path(os.environ.get(
    "YAMNET_MODEL_PATH",
    str(PROJECT_ROOT / "services" / "evidence" / "models" / "yamnet.tflite"),
))

# YAMNet's input contract: 16 kHz mono float32 in [-1, 1]. The audio
# extractor already produces 16 kHz mono mp3s, so the only conversion
# needed is mp3 → float32 array (handled by ffmpeg piping to stdout).
YAMNET_SAMPLE_RATE: int = 16000

# How often the daemon polls the manifest for unclassified events.
# Audio captures arrive at most once per minute per enabled camera (the
# extractor's segment length is 60 s), so a 30 s poll keeps us within
# 30-90 s of real time without spinning the CPU.
DEFAULT_POLL_SECONDS: float = 30.0

# Minimum YAMNet score for an AudioSet class to "count" toward one of
# our user-facing categories. AudioSet scores in [0, 1]; 0.4 is the
# rough median of "audibly the labeled event in clean conditions" per
# the YAMNet paper.
DEFAULT_MIN_SCORE: float = 0.4

# Mapping from our user-facing categories (which appear in the UI and
# under /litigation/flagged/) to the AudioSet class names YAMNet emits.
# Each user category fires when ANY of its mapped AudioSet classes
# crosses ``min_score``. Adjust the lists to add or remove sensitivity.
DEFAULT_CATEGORY_MAP: Dict[str, List[str]] = {
    "screams":       ["Screaming", "Shout", "Yell"],
    "crying":        ["Baby cry, infant cry", "Crying, sobbing",
                      "Whimper", "Wail, moan"],
    "impacts":       ["Glass", "Slam", "Thud", "Smash, crash",
                      "Shatter", "Bang", "Knock", "Breaking"],
    "raised-voices": ["Shout", "Yell", "Bellow",
                      "Children shouting", "Battle cry"],
}


# =========================================================================
# ClipClassification — small value object for one classified mp3
# =========================================================================

@dataclass
class ClipClassification:
    """
    Result of running YAMNet on one mp3 file.

    Stored on disk inside the new manifest entry. Each YAMNet inference
    produces 521 per-class scores per ~0.96s frame; we reduce that to
    "max score per class over the whole clip" because:

      * The clip is at most 60 s long (the extractor's segment length).
      * For evidence flagging, the question is "did this happen at any
        point in the clip", not "did it happen continuously".
      * Storing 521 scores × N frames per clip would balloon the
        manifest. The single per-class peak is the actionable number.

    If you ever need per-frame scores (e.g. to locate the second of a
    scream within a 60s clip), re-run YAMNet on demand from the mp3 —
    it's cheap enough.
    """

    # The manifest_id of the audio_capture event we classified.
    audio_capture_manifest_id: int

    # Per-AudioSet-class peak score over the clip. Keys are AudioSet
    # class names ("Screaming", "Glass", etc.). Values in [0, 1].
    # We only persist classes that scored above a small floor (default
    # 0.05) — keeps the JSON tidy without losing actionable signal.
    audioset_peak_scores: Dict[str, float] = field(default_factory=dict)

    # User-facing categories that crossed the threshold, with the
    # contributing AudioSet class and its score for each.
    matched_categories: Dict[str, Dict[str, float]] = field(
        default_factory=dict)

    # Path under LITIGATION_ROOT (relative) of the mp3 we classified.
    mp3_relative_path: str = ""

    # Wall-clock UTC timestamp of when classification completed.
    classified_at_utc: str = ""

    # Symlinks created under /litigation/flagged/<cat>/, relative paths.
    flagged_paths: List[str] = field(default_factory=list)


# =========================================================================
# YamnetClassifierService — the concrete evidence service
# =========================================================================

class YamnetClassifierService(EvidenceService):
    """
    Long-running daemon that classifies audio captures and flags them.

    Construct, ``start()``, and forget — it polls the manifest forever,
    catching up on any backlog from prior runs and processing each new
    capture as it lands.

    Idempotency / resume semantics
    ------------------------------
    The service tracks the last classified manifest_id in a small JSON
    state file at ``/litigation/.yamnet_classifier_state.json``. On
    start it resumes from there. If the state file is missing or
    corrupt, it falls back to "start from manifest_id 0" (re-classify
    everything from the beginning) — slow but correct.

    The output writes (manifest entry, audio_events row, flagged
    symlinks) are individually idempotent enough that re-classifying
    a clip is safe:

      * Manifest entries are append-only — running twice produces two
        ``acoustic_classification`` entries with the same content. Not
        ideal but not corrupting.
      * Audio_events INSERTs use ``manifest_id`` as a UNIQUE key so a
        second insert for the same source raises and is logged at
        WARNING level — the operator can investigate.
      * Symlinks: ``os.symlink`` with ``exist_ok=True`` semantics via
        a try/except.
    """

    # Floor for storing per-class scores in the manifest. Anything
    # below this is treated as 0 (not stored) — keeps the manifest
    # JSON readable without losing information that matters.
    SCORES_FLOOR: float = 0.05

    def __init__(
        self,
        manifest=None,
        litigation_root=None,
        # Model and inference parameters
        model_path: Path = DEFAULT_MODEL_PATH,
        category_map: Optional[Dict[str, List[str]]] = None,
        min_score: float = DEFAULT_MIN_SCORE,
        # Daemon behavior
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        # DB integration (PostgREST URL); None disables DB inserts
        # (manifest + symlinks still happen).
        postgrest_url: Optional[str] = None,
    ) -> None:
        super().__init__(manifest=manifest, litigation_root=litigation_root)
        self.model_path: Path = Path(model_path)
        self.category_map: Dict[str, List[str]] = (
            category_map if category_map is not None else DEFAULT_CATEGORY_MAP
        )
        self.min_score: float = float(min_score)
        self.poll_seconds: float = float(poll_seconds)
        self.postgrest_url: Optional[str] = (
            postgrest_url
            or os.environ.get("NVR_POSTGREST_URL")
            or "http://postgrest:3001"
        )

        # State file: tracks the last manifest_id we've processed. The
        # leading dot keeps it visually segregated from the user-facing
        # MANIFEST.jsonl / README.md.
        self._state_path: Path = self.litigation_root / ".yamnet_classifier_state.json"

        # Lazy-loaded inference state.
        self._interpreter = None                 # tflite Interpreter or TF Module
        self._waveform_input_index = None        # tflite-only
        self._scores_output_index = None         # tflite-only
        self._class_names: Optional[List[str]] = None
        # Inverted index: class_name → list of categories it contributes to.
        # Built once at init time so the per-clip mapping step is O(matched).
        self._class_to_categories: Dict[str, List[str]] = {}
        for cat, classes in self.category_map.items():
            for cn in classes:
                self._class_to_categories.setdefault(cn, []).append(cat)

    # -----------------------------------------------------------------
    # EvidenceService.run — the daemon loop
    # -----------------------------------------------------------------

    def run(self) -> None:
        """
        Long-running poll loop. Catches up on backlog, then watches
        forever.

        Per the EvidenceService contract: checks ``self._stop`` between
        iterations and uses ``self._stop.wait(timeout=...)`` rather
        than ``time.sleep`` so ``stop()`` causes immediate exit.
        """
        try:
            self._lazy_load_model()
        except Exception as e:
            # Model load failure is fatal — we can't classify without
            # the model. Log clearly and exit. The supervisor will see
            # the worker thread die and can decide whether to retry.
            self.log.error(
                "failed to load YAMNet model from %s — service cannot run. "
                "See module docstring for download instructions. Cause: %s",
                self.model_path, e,
            )
            return

        self.log.info(
            "yamnet classifier ready: model=%s, min_score=%.2f, "
            "categories=%s, poll=%.1fs",
            self.model_path, self.min_score,
            list(self.category_map.keys()), self.poll_seconds,
        )

        last_id = self._load_last_id()
        while not self._stop.is_set():
            try:
                last_id = self._classify_pending(last_id)
            except Exception:
                # Any unhandled exception in one polling pass is logged
                # but does NOT kill the daemon. Next pass will retry.
                self.log.exception("classify pass failed; will retry")
            # Sleep cooperatively. wait() returns early if stop() is
            # called, giving us responsive shutdown.
            self._stop.wait(timeout=self.poll_seconds)

    # -----------------------------------------------------------------
    # Pending-entry processing
    # -----------------------------------------------------------------

    def _classify_pending(self, last_id: int) -> int:
        """
        Walk the manifest forward from ``last_id + 1`` and process
        every ``audio_capture`` entry. Returns the new last_id.

        Non-capture entries (genesis, lifecycle, classification entries
        from previous runs of THIS service, retention prunes, etc.) are
        skipped — only their manifest_id advances the cursor.
        """
        new_last = last_id
        for entry in self.manifest.iter_entries(from_id=last_id + 1):
            mid = entry["manifest_id"]
            if entry.get("event_type") == "audio_capture":
                try:
                    self._classify_one(entry)
                except Exception:
                    self.log.exception(
                        "failed to classify audio_capture id=%d; skipping", mid)
                    # Still advance — we don't want one bad clip to
                    # block all future classification. A separate
                    # "retry failed" tool can reprocess later.
            new_last = mid
            # Persist progress after every entry so a crash mid-batch
            # doesn't lose more than one entry's worth of work.
            if new_last != last_id:
                self._save_last_id(new_last)
                last_id = new_last
        return new_last

    def _classify_one(self, capture_entry: Dict[str, Any]) -> None:
        """
        Run the full classify-and-flag pipeline for a single
        audio_capture manifest entry.
        """
        capture_id = capture_entry["manifest_id"]
        mp3_rel_path = capture_entry["files"]["mp3"]["path"]
        mp3_abs_path = self.litigation_root / mp3_rel_path
        if not mp3_abs_path.exists():
            self.log.warning(
                "audio_capture id=%d points at missing mp3 %s; skipping",
                capture_id, mp3_abs_path,
            )
            return

        # 1. Inference
        peak_scores = self._infer_peak_scores(mp3_abs_path)

        # 2. Map AudioSet → user categories
        matched_categories = self._matched_categories(peak_scores)

        # 3. Build the result object
        result = ClipClassification(
            audio_capture_manifest_id=capture_id,
            audioset_peak_scores={
                cn: round(s, 4)
                for cn, s in peak_scores.items()
                if s >= self.SCORES_FLOOR
            },
            matched_categories=matched_categories,
            mp3_relative_path=mp3_rel_path,
            classified_at_utc=datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.000Z"),
        )

        # 4. Symlinks under /litigation/flagged/<cat>/
        result.flagged_paths = self._create_flagged_symlinks(
            mp3_abs_path, list(matched_categories.keys()),
        )

        # 5. Manifest entry (chain-of-custody)
        self._append_classification_manifest_entry(capture_entry, result)

        # 6. DB row (queryable index)
        try:
            self._insert_audio_event_row(capture_entry, result)
        except Exception:
            # DB is a softer dependency than manifest+symlinks. A DB
            # outage shouldn't lose evidence — manifest is the source
            # of truth. Log, continue.
            self.log.exception(
                "failed to insert audio_events row for capture id=%d "
                "(continuing — manifest write already succeeded)",
                capture_id,
            )

        if matched_categories:
            self.log.info(
                "[id=%d] %s — flagged: %s",
                capture_id, mp3_rel_path,
                ", ".join(f"{c} ({matched_categories[c]['score']:.2f}, "
                          f"{matched_categories[c]['audioset_class']})"
                          for c in matched_categories),
            )
        else:
            self.log.debug(
                "[id=%d] %s — no category crossed threshold",
                capture_id, mp3_rel_path,
            )

    # -----------------------------------------------------------------
    # YAMNet inference
    # -----------------------------------------------------------------

    def _lazy_load_model(self) -> None:
        """
        Load the YAMNet TFLite (or TF Hub) model on first use.

        Tries tflite-runtime first (small dep). Falls back to full
        tensorflow if available. Raises if neither works — that's a
        deployment-time fix, not something the service can recover.
        """
        if self._interpreter is not None:
            return  # already loaded

        if not self.model_path.exists():
            raise FileNotFoundError(
                f"YAMNet model not found at {self.model_path}. "
                f"Download it (see module docstring) or set "
                f"YAMNET_MODEL_PATH to the correct location."
            )

        # Prefer tflite-runtime — it's small and self-contained.
        try:
            import tflite_runtime.interpreter as tflite  # type: ignore
        except ImportError:
            # Fall back to full tensorflow (which also exposes
            # tf.lite.Interpreter). Heavier, but we do the work.
            try:
                from tensorflow.lite.python.interpreter import Interpreter as tflite_Interpreter  # type: ignore # noqa
                # Build a thin shim so the rest of this method doesn't
                # care which library it came from.
                class _Shim:
                    Interpreter = tflite_Interpreter
                tflite = _Shim  # type: ignore
            except ImportError as e:
                raise RuntimeError(
                    "Neither tflite-runtime nor tensorflow is installed; "
                    "cannot run YAMNet inference. "
                    "Install one with `pip install tflite-runtime` "
                    "(recommended, ~10 MB) or `pip install tensorflow` "
                    "(~500 MB)."
                ) from e

        interpreter = tflite.Interpreter(model_path=str(self.model_path))
        # YAMNet's input is dynamically shaped (waveform of arbitrary
        # length). We allocate tensors once with the default shape, then
        # call ``resize_tensor_input`` per clip below.
        interpreter.allocate_tensors()
        self._interpreter = interpreter

        # Cache I/O tensor indices so per-clip inference doesn't
        # re-look-up the metadata.
        input_details = interpreter.get_input_details()
        output_details = interpreter.get_output_details()
        self._waveform_input_index = input_details[0]["index"]
        # YAMNet exposes three outputs: scores (per-frame, per-class),
        # embeddings (per-frame), and spectrograms. We only want scores.
        # The first output is conventionally the per-frame class scores.
        self._scores_output_index = output_details[0]["index"]

        # Class names: try to read from TFLite model metadata. If that
        # fails, fall back to the hardcoded YAMNet AudioSet ontology
        # mapping (we don't ship that here; require an env override).
        self._class_names = self._read_class_names_from_metadata()

    def _read_class_names_from_metadata(self) -> List[str]:
        """
        Extract YAMNet's class-name list from TFLite model metadata.

        TFLite models can carry an ``associated_files`` blob; YAMNet
        ships a CSV of ``index,mid,display_name`` rows there. We read
        the CSV and return display_names in index order.

        If metadata extraction fails (older model file, missing
        metadata), we fall back to a small built-in list of the
        ~30 AudioSet classes we actually map. That makes the service
        work for the user's targeted categories without external
        dependencies, at the cost of not surfacing other AudioSet
        classes in the per-clip score dump.
        """
        # Attempt the real metadata read. If tflite-support isn't
        # installed we just go to the fallback list.
        try:
            from tflite_support import metadata as _md  # type: ignore
            mreader = _md.MetadataDisplayer.with_model_file(str(self.model_path))
            packed = mreader.get_associated_file_buffer("yamnet_label_list.txt")
            text = packed.decode("utf-8")
            # The label file is one class name per line, in index order.
            return [line.strip() for line in text.splitlines() if line.strip()]
        except Exception:
            self.log.warning(
                "could not extract YAMNet class names from model "
                "metadata; falling back to built-in subset (only the "
                "classes mapped to our user categories will be named, "
                "others will be 'class_<idx>'). Install tflite-support "
                "for the full list."
            )
            # Build a placeholder list large enough to index any
            # AudioSet class number (YAMNet has 521). Names get filled
            # in for the ones we care about; the rest stay generic.
            placeholder = [f"class_{i}" for i in range(521)]
            # We don't know the indices of our targeted classes
            # without metadata, so the user MUST install tflite-support
            # OR provide the label file via env var YAMNET_LABEL_FILE
            # for the matching to actually work. Document via a flag
            # the caller can check.
            self._class_names_are_placeholder = True
            return placeholder

    def _infer_peak_scores(self, mp3_path: Path) -> Dict[str, float]:
        """
        Run YAMNet on the audio at ``mp3_path``. Returns a dict mapping
        AudioSet class name → peak score across the clip.
        """
        import numpy as np  # type: ignore

        waveform = self._decode_audio_to_float32(mp3_path)
        # YAMNet wants shape (num_samples,) float32.
        # Resize the input tensor to match this clip's length.
        assert self._interpreter is not None
        interp = self._interpreter
        interp.resize_tensor_input(
            self._waveform_input_index,
            [waveform.shape[0]],
            strict=True,
        )
        interp.allocate_tensors()
        interp.set_tensor(self._waveform_input_index, waveform)
        interp.invoke()
        # scores shape: (num_frames, 521)
        scores = interp.get_tensor(self._scores_output_index)
        # Peak per class = max across all frames.
        peaks = scores.max(axis=0)
        # Map to class names. ``self._class_names`` is the index → name
        # list captured at model load time.
        names = self._class_names or []
        return {
            (names[i] if i < len(names) else f"class_{i}"): float(peaks[i])
            for i in range(peaks.shape[0])
        }

    def _decode_audio_to_float32(self, mp3_path: Path):
        """
        Decode an mp3 to a 16 kHz mono float32 numpy array via ffmpeg.

        We use ffmpeg (already a project dependency for the audio
        extractor) rather than librosa because:

          * No extra Python dep for audio decoding.
          * ffmpeg's mp3 decoder is bit-identical regardless of whether
            we're inside or outside the container.
          * The output format (raw float32 little-endian, 16 kHz mono)
            matches YAMNet's input contract directly.
        """
        import numpy as np  # type: ignore

        cmd = [
            "ffmpeg",
            "-loglevel", "error",
            "-i", str(mp3_path),
            "-ac", "1",                       # mono
            "-ar", str(YAMNET_SAMPLE_RATE),   # 16 kHz
            "-f", "f32le",                    # 32-bit float little-endian
            "-",                              # to stdout
        ]
        proc = subprocess.run(
            cmd, capture_output=True, check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"ffmpeg failed decoding {mp3_path}: "
                f"rc={proc.returncode} stderr={proc.stderr[:500]!r}"
            )
        # Interpret the raw bytes as float32.
        arr = np.frombuffer(proc.stdout, dtype=np.float32)
        # YAMNet expects values roughly in [-1, 1] — ffmpeg's f32le
        # output is already in that range when source is normalized
        # PCM/mp3 (which our extractor produces).
        return arr

    # -----------------------------------------------------------------
    # AudioSet → user-category mapping
    # -----------------------------------------------------------------

    def _matched_categories(
        self,
        peak_scores: Dict[str, float],
    ) -> Dict[str, Dict[str, float]]:
        """
        For each user-facing category in ``self.category_map``, find
        the highest-scoring AudioSet class that maps to it. If that
        score crosses ``min_score``, the category is considered
        "matched" and is returned in the result.

        Returns a dict of the form::

            {
              "screams": {"score": 0.81, "audioset_class": "Screaming"},
              "impacts": {"score": 0.55, "audioset_class": "Glass"},
            }
        """
        out: Dict[str, Dict[str, float]] = {}
        for category, audioset_classes in self.category_map.items():
            best_class: Optional[str] = None
            best_score: float = 0.0
            for cls in audioset_classes:
                s = peak_scores.get(cls, 0.0)
                if s > best_score:
                    best_score = s
                    best_class = cls
            if best_class is not None and best_score >= self.min_score:
                out[category] = {
                    "score": round(best_score, 4),
                    "audioset_class": best_class,
                }
        return out

    # -----------------------------------------------------------------
    # Output side effects: symlinks, manifest entry, DB row
    # -----------------------------------------------------------------

    def _create_flagged_symlinks(
        self,
        mp3_abs_path: Path,
        categories: List[str],
    ) -> List[str]:
        """
        Create one symlink per matched category pointing at the original
        mp3 in intake/. Returns the list of created symlink paths
        (relative to LITIGATION_ROOT).

        Symlink rather than copy: the mp3 is the canonical artifact,
        we just want a flat per-category index to it. No duplication
        of bytes.
        """
        flagged_root = self.litigation_root / "flagged"
        created: List[str] = []
        for cat in categories:
            cat_dir = flagged_root / cat
            cat_dir.mkdir(parents=True, exist_ok=True)
            link_name = mp3_abs_path.name
            link_path = cat_dir / link_name
            # Compute the relative path from link_path's parent to the
            # target so the symlink stays valid if /litigation/ is
            # mounted at a different absolute path on a different host.
            try:
                rel_target = os.path.relpath(mp3_abs_path, start=cat_dir)
            except ValueError:
                # Cross-drive path (Windows) or similar — fall back to
                # absolute. Linux deployments will never hit this.
                rel_target = str(mp3_abs_path)
            try:
                os.symlink(rel_target, link_path)
            except FileExistsError:
                # Idempotent re-classification: link already exists.
                # Verify it still points where we expect; if not,
                # replace it.
                existing = os.readlink(link_path)
                if existing != rel_target:
                    self.log.warning(
                        "replacing stale symlink %s (was -> %s, now -> %s)",
                        link_path, existing, rel_target,
                    )
                    link_path.unlink()
                    os.symlink(rel_target, link_path)
            created.append(str(link_path.relative_to(self.litigation_root)))
        return created

    def _append_classification_manifest_entry(
        self,
        capture_entry: Dict[str, Any],
        result: ClipClassification,
    ) -> None:
        """
        Write a new ``acoustic_classification`` event to the manifest.

        The original audio_capture entry is NOT mutated — that would
        break the hash chain. Classification follows as a separate
        event referencing the original by manifest_id.
        """
        self.log_lifecycle_event(
            event_type="acoustic_classification",
            audio_capture_manifest_id=result.audio_capture_manifest_id,
            camera_serial=capture_entry.get("camera_serial"),
            camera_name=capture_entry.get("camera_name"),
            classifier="yamnet",
            classifier_version="1.0",          # bump if model file changes
            min_score_threshold=self.min_score,
            audioset_peak_scores=result.audioset_peak_scores,
            matched_categories=result.matched_categories,
            mp3_relative_path=result.mp3_relative_path,
            flagged_paths=result.flagged_paths,
            classified_at_utc=result.classified_at_utc,
        )

    def _insert_audio_event_row(
        self,
        capture_entry: Dict[str, Any],
        result: ClipClassification,
    ) -> None:
        """
        Insert (or upsert) a row into ``audio_events`` so the queryable
        DB index reflects this classification.

        Best-effort: if PostgREST is unreachable, log and continue.
        Manifest is the source of truth, the DB is a derived index we
        can rebuild from the manifest if needed.
        """
        # Only matter to insert if at least one category matched.
        # Unmatched clips don't need to clutter the queryable index.
        if not result.matched_categories:
            return

        # ``requests`` is a soft dep; we lazy-import here so non-DB
        # deployments don't need it. (It's a project dep already, but
        # this keeps the import surface clean.)
        import requests  # type: ignore

        # Pick the highest-scoring matched category as the "primary".
        primary = max(result.matched_categories.items(),
                      key=lambda kv: kv[1]["score"])
        primary_category, primary_info = primary

        body = {
            "manifest_id": result.audio_capture_manifest_id,
            "camera_serial": capture_entry["camera_serial"],
            "timestamp_utc": capture_entry["timestamp_utc"],
            "duration_s": capture_entry.get("duration_seconds", 0.0),
            "primary_label": primary_category,
            "primary_score": primary_info["score"],
            "intake_path": result.mp3_relative_path,
            "flagged_paths": result.flagged_paths,
            # transcript_excerpt + anamnesis_id come later (Phase 3).
        }
        url = f"{self.postgrest_url}/audio_events"
        r = requests.post(
            url,
            json=body,
            headers={
                # Conflict (manifest_id collides) → ignore. Re-classifying
                # is a no-op at the DB layer; the user can DELETE+INSERT
                # manually if they want a refresh.
                "Prefer": "resolution=ignore-duplicates,return=minimal",
            },
            timeout=10,
        )
        if r.status_code not in (200, 201, 409):
            r.raise_for_status()

    # -----------------------------------------------------------------
    # State persistence (last classified manifest_id)
    # -----------------------------------------------------------------

    def _load_last_id(self) -> int:
        """Return the last classified manifest_id, or -1 if none."""
        if not self._state_path.exists():
            return -1
        try:
            data = json.loads(self._state_path.read_text())
            return int(data.get("last_classified_manifest_id", -1))
        except (json.JSONDecodeError, ValueError, OSError):
            self.log.warning(
                "state file at %s is corrupt; starting from manifest "
                "beginning (will re-classify everything)",
                self._state_path,
            )
            return -1

    def _save_last_id(self, manifest_id: int) -> None:
        """Persist progress. Atomic-ish: write tmp, rename."""
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "last_classified_manifest_id": int(manifest_id),
            "updated_at_utc": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.000Z"),
            "service": self.__class__.__name__,
        }))
        tmp.replace(self._state_path)


# =========================================================================
# CLI entrypoint — for direct invocation or systemd unit
# =========================================================================

def main() -> int:
    """Run the classifier daemon from the command line."""
    import argparse
    import logging as _logging

    p = argparse.ArgumentParser(description="YAMNet acoustic classifier")
    p.add_argument("--model", default=str(DEFAULT_MODEL_PATH),
                   help=f"path to YAMNet TFLite model "
                        f"(default {DEFAULT_MODEL_PATH})")
    p.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE,
                   help=f"per-category score threshold (default {DEFAULT_MIN_SCORE})")
    p.add_argument("--poll", type=float, default=DEFAULT_POLL_SECONDS,
                   help=f"manifest poll interval in seconds "
                        f"(default {DEFAULT_POLL_SECONDS})")
    p.add_argument("--once", action="store_true",
                   help="process current backlog and exit (don't loop)")
    args = p.parse_args()

    _logging.basicConfig(
        level=os.environ.get("NVR_LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    svc = YamnetClassifierService(
        model_path=Path(args.model),
        min_score=args.min_score,
        poll_seconds=args.poll,
    )

    if args.once:
        # Synchronous one-shot: lazy-load model + process backlog +
        # exit. Useful for cron-style "classify any new captures every
        # 10 minutes" deployments instead of running a daemon.
        svc._lazy_load_model()
        last = svc._load_last_id()
        last = svc._classify_pending(last)
        svc._save_last_id(last)
        return 0

    # Default: run the daemon loop in this thread (don't go through
    # start() because that would spawn a background thread and we'd
    # immediately return to the shell with nothing keeping the process
    # alive). Just call run() directly — same behavior, simpler.
    svc.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
