"""
Evidence Collection Pipeline — service package.

This package implements the NVR's evidence-collection feature:
captures audio/video clips when acoustic events fire (screams, crying,
impacts, raised voices) or when a camera's continuous-audio mode picks
up non-silent windows, then transcribes them, ingests the transcripts
into Anamnesis, and keeps an append-only hash-chained manifest of every
capture for chain-of-custody integrity.

See ``docs/PROPOSAL_evidence_collection_pipeline.md`` for the full
architecture and rationale.

Modules
-------
manifest    Append-only hash-chained MANIFEST.jsonl reader/writer.
"""

from services.evidence.manifest import EvidenceManifest, MANIFEST_PATH

__all__ = ["EvidenceManifest", "MANIFEST_PATH"]
