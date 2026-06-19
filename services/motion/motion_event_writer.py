"""
services/motion/motion_event_writer.py — single-purpose helper that inserts
a row into the ``motion_events`` table and returns the new row's id.

Why this exists
---------------

Phase 0 of the motion-source-attribution work (2026-05-13). Before this
helper, every motion detector (FFmpeg / ONVIF / Reolink Baichuan) called
``RecordingService.start_motion_recording(camera_id)`` with no
``event_id`` argument, so the recording writer defaulted
``motion_source = 'manual'`` for every recording. The ``motion_events``
table sat empty.

The fix is two parts: each detector now calls ``record_motion_event(...)``
here BEFORE triggering the recording, captures the returned id, and
passes it through as ``event_id`` to ``start_motion_recording``. The
writer then stores both ``motion_source`` and ``motion_event_id`` on the
``recordings`` row, giving us a complete provenance trail.

Centralising the INSERT in one helper keeps psycopg2 connection details
out of the detectors and ensures every source-of-motion writes a
consistent row (matching CHECK constraints, populating optional ONVIF
fields cleanly, etc.).

Failure model
-------------

Best-effort: any psycopg2 failure is logged and the function returns
``None``. Callers must treat ``None`` as "DB attribution failed, proceed
anyway" — a transient DB hiccup must not block recording. The recording
will then fall back to ``motion_source = 'manual'`` (which now correctly
means "unattributed", not "all of them"), and the operator can spot the
gap by comparing detector logs to row count.
"""

from __future__ import annotations

import logging
from typing import Optional

from services.db import cursor as db_cursor

logger = logging.getLogger(__name__)


# Mirrors the CHECK constraint installed by migration 035. Keep in sync if
# the constraint changes.
_VALID_SOURCES = frozenset({
    "onvif",
    "ffmpeg",
    "eufy_bridge",
    "manual",
    "reolink_baichuan",
    "evidence",
})

# Historical note (2026-06-19): the original _db_conn() helper here
# deliberately bypassed pooling out of concern for "stale connection after
# migration" — a long-lived pool conn opened pre-schema-migration won't see
# new columns. That concern is mitigated by start.sh: migrations run on
# every boot BEFORE the Flask app starts taking requests, so pool conns are
# always opened AFTER schema migrations land. Pool now used here for
# consistency with the rest of the codebase.


def record_motion_event(
    camera_id: str,
    source: str,
    *,
    scene_score: Optional[float] = None,
    confidence: Optional[float] = None,
    onvif_rule_name: Optional[str] = None,
    onvif_event_type: Optional[str] = None,
    triggered_recording: bool = True,
) -> Optional[int]:
    """
    Insert a row into ``motion_events`` and return the new id.

    Parameters
    ----------
    camera_id : str
        Camera serial. Required.
    source : str
        Which detector produced this event. Must be one of:
        'onvif' | 'ffmpeg' | 'reolink_baichuan' | 'eufy_bridge' | 'evidence'
        | 'manual'. Anything else is a programming error and is rejected
        before hitting the DB so the caller fails fast.
    scene_score : float, optional
        FFmpeg scene-change detector's signal strength. Roughly 0..1.
    confidence : float, optional
        Detector confidence (currently used by no production detector
        but available for future ML-backed paths).
    onvif_rule_name : str, optional
        For source='onvif', the ONVIF analytics rule that matched (if
        the event payload exposed one).
    onvif_event_type : str, optional
        For source='onvif', the SOAP event topic. Mostly diagnostic.
    triggered_recording : bool
        Whether this event actually fired a recording. Default True;
        only set False when a detector observed motion but the recording
        path was suppressed (cooldown, throttling, opt-out).

    Returns
    -------
    int or None
        Newly assigned ``motion_events.id`` on success, or ``None`` on
        any failure (including unrecognized source). Callers must
        tolerate ``None`` and proceed — DB attribution failure must not
        block recording.
    """
    if source not in _VALID_SOURCES:
        # Programming error — log loudly so it surfaces in CI and dev.
        logger.error(
            "record_motion_event: invalid source=%r (allowed: %s)",
            source, sorted(_VALID_SOURCES),
        )
        return None
    if not camera_id:
        logger.error("record_motion_event: empty camera_id")
        return None

    try:
        with db_cursor() as cur:
            cur.execute(
                """
                INSERT INTO motion_events (
                    camera_id, timestamp, source,
                    scene_score, confidence,
                    onvif_rule_name, onvif_event_type,
                    triggered_recording
                ) VALUES (%s, NOW(), %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    camera_id, source,
                    scene_score, confidence,
                    onvif_rule_name, onvif_event_type,
                    triggered_recording,
                ),
            )
            row = cur.fetchone()
            event_id = int(row[0]) if row else None
            if event_id:
                logger.debug(
                    "motion_event[%s] id=%s source=%s scene=%s",
                    camera_id, event_id, source, scene_score,
                )
            return event_id
    except Exception:
        logger.exception(
            "record_motion_event: insert failed for %s (source=%s)",
            camera_id, source,
        )
        return None


def link_recording_to_event(event_id: int, recording_id: int) -> bool:
    """
    Backfill the ``motion_events.recording_id`` foreign key once the
    recording row has been written. Optional — used so audit queries can
    walk in both directions.

    Returns True on success, False on any failure. Failure is non-fatal:
    ``recordings.motion_event_id`` is the primary link; this is the
    inverse for convenience.
    """
    if not event_id or not recording_id:
        return False
    try:
        with db_cursor() as cur:
            cur.execute(
                "UPDATE motion_events SET recording_id = %s WHERE id = %s",
                (recording_id, event_id),
            )
            return cur.rowcount == 1
    except Exception:
        logger.exception(
            "link_recording_to_event: failed event_id=%s recording_id=%s",
            event_id, recording_id,
        )
        return False
