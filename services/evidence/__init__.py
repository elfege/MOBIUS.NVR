"""
Evidence Collection Pipeline — service package.

The evidence package is a family of services that, together, build a
defensible audio/video record of events in the NVR's coverage area.

Today the package implements:

  * **manifest** — an append-only, hash-chained JSONL log of every
    capture event. The chain-of-custody source of truth.
  * **base** — abstract ``EvidenceService`` superclass that every other
    service inherits from. Defines lifecycle, logging, archive-before-
    overwrite semantics, and shared access to the manifest.
  * **anamnesis_client** — typed HTTP client for the Anamnesis app at
    ``dellserver:3010``. Used by services that need GPU-backed LLM
    generation or semantic episode retrieval.
  * **audio_extractor** — per-camera ffmpeg subprocess that captures
    silence-pruned mp3 segments from RTSP and writes manifest entries.
  * **weekly_summary** — scheduled service that digests the week's git
    log + Anamnesis episodes + manifest events into a markdown summary
    at ``docs/weekly_summaries/YYYY/MM/DD_to_DD.md``. GPU-backed.

Future modules (placeholders, not yet built) will plug into the same
base class and the same manifest:

  * **yamnet_classifier** — acoustic event detection on captured mp3s
  * **whisper_transcriber** — speech-to-text + Anamnesis ingest
  * **people_recognition** — visual identity tracking on motion clips
  * **child_monitor** — Child-Monitor-tab specialized signal selection

Architectural reference: see
``docs/plans/evidence_collection_pipeline_*.md`` for the multi-phase plan.
"""

from services.evidence.manifest import EvidenceManifest, MANIFEST_PATH

__all__ = ["EvidenceManifest", "MANIFEST_PATH"]
