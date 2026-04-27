# Proposal: Evidence Collection Pipeline

**Status:** Draft — `evidence_gathering_APR_27_2026_a` branch
**Author:** dellserver-nvr (with Elfege)
**Date:** 2026-04-27

---

## 1. Purpose & Product Framing

NVR systems today differentiate on convenience features (motion alerts, person detection, "Hello Jessica" face recognition). Those address visibility, not safety.

This pipeline addresses the most common real-world threat home users face: domestic violence, harassment, intimidation, and child distress. The product asks: *what would the camera in your home need to do to actually defend you in court if something happened?*

The answer requires three things ordinary NVRs do not provide:

1. **Continuous, silence-pruned audio capture** with chain-of-custody integrity
2. **Acoustic event flagging** for high-stakes signals (screams, impacts, crying)
3. **A clean handoff to whatever case-management system the user prefers** — without locking them in

This proposal describes a feature that any NVR user — with or without legal proceedings underway — can enable to build a defensible audio/video record of events in their home. The author's own use case (`server:~/0_LEGAL`) is one consumer of this feed; the architecture is deliberately designed so other consumers can plug in.

---

## 2. Scope (in / out)

### In scope (Phases 0–4)

- `/litigation/` mirrored intake volume on dellserver
- 24/7 audio capture from audio-capable cameras with silence-based pruning
- Acoustic classifier (YAMNet): `screams`, `crying`, `impacts`, `raised voices`
- Whisper transcription with hallucination guard
- Anamnesis ingestion of every transcribed segment
- Append-only hash-chained manifest (chain-of-custody)
- "Collect Evidence" UI tab — per-camera enable, per-category opt-in
- Read-only HTTP API exposing intake feed for external consumers

### In scope (Phase 5 — universal case-binding)

- Generic case API: register a case, define case predicates, pull matching events
- Reference consumer: `0_LEGAL` rsync-and-promote daemon
- "Child Monitor" tab using the same scream/crying/impact infra

### Out of scope

- Speech-content classifiers (insults / arguments / nagging / harassment) — captured as transcript text and surfaced via Anamnesis retrieval, **not** classified at intake. False positives in those categories are evidentially harmful; retrieval-time review by the user (or their attorney) is the right place for that judgment.
- Voice-based identification of family members
- Any model training pipeline that consumes `/litigation/` data
- Automated submission of evidence to anyone — promotion to a case is always a user action

---

## 3. Architecture

```
                   ┌──────────────────────────────────────────────────┐
                   │  DELLSERVER (NVR)                                │
                   │                                                  │
  RTSP from        │  ┌──────────────┐    ┌────────────────────────┐  │
  streaming_hub  ──┼─▶│ audio_extract│───▶│ silence-prune (RMS+VAD)│  │
  (per camera)     │  └──────────────┘    └───────────┬────────────┘  │
                   │                                  │               │
                   │                  ┌───────────────┴───────────┐   │
                   │                  ▼                           ▼   │
                   │       ┌───────────────────┐    ┌───────────────────┐
                   │       │ YAMNet classifier │    │ Whisper (sized to │
                   │       │ scream/cry/impact │    │ match 0_LEGAL via │
                   │       │ /raised-voice     │    │ MSG-131 response) │
                   │       └─────────┬─────────┘    └─────────┬─────────┘
                   │                 │                        │         │
                   │                 │                        ▼         │
                   │                 │              ┌───────────────────┐
                   │                 │              │ Anamnesis ingest  │
                   │                 │              │ POST /api/episodes│
                   │                 │              └───────────────────┘
                   │                 │                        │         │
                   │                 ▼                        ▼         │
                   │       ┌────────────────────────────────────────┐   │
                   │       │ /litigation/intake/{YYYY-MM-DD}/       │   │
                   │       │   {ts}_{cam}.{mp4,mp3,txt,json}        │   │
                   │       │ /litigation/MANIFEST.jsonl (append-only)│  │
                   │       │ /litigation/flagged/{category}/        │   │
                   │       │   (symlinks into intake)               │   │
                   │       └────────────────────────────────────────┘   │
                   │                 │                                  │
                   │                 ▼                                  │
                   │       ┌────────────────────────────────────────┐   │
                   │       │ HTTP API:  /api/evidence/feed          │   │
                   │       │            /api/evidence/cases/...     │   │
                   │       └─────────────────┬──────────────────────┘   │
                   └─────────────────────────┼──────────────────────────┘
                                             │
                                             ▼
                   ┌──────────────────────────────────────────────────┐
                   │  CONSUMERS (any number, any location)            │
                   │                                                  │
                   │   server:~/0_LEGAL   (rsync + promote daemon)    │
                   │   external attorney workstation (HTTP poll)      │
                   │   future: Child Monitor mobile app (push)        │
                   └──────────────────────────────────────────────────┘
```

### 3.1 Storage layout

`/litigation/` lives on `/dev/sdb` (ext4, 1.1TB, relabeled `LITIGATION` — pending user confirmation that the disk's current contents are disposable).

```
/litigation/
├── README.md                 # purpose, jurisdiction notes, retention policy
├── MANIFEST.jsonl            # append-only, hash-chained
├── intake/
│   └── 2026-04-27/
│       ├── 02:14:33_T8410P0023352DA9.mp4   # original recording
│       ├── 02:14:33_T8410P0023352DA9.mp3   # extracted audio (16kHz mono)
│       ├── 02:14:33_T8410P0023352DA9.json  # whisper segments + classifier
│       └── 02:14:33_T8410P0023352DA9.txt   # plain transcript
├── flagged/
│   ├── screams/   → symlinks into intake/
│   ├── crying/
│   ├── impacts/
│   └── raised-voices/
└── retention/
    └── policy.yaml           # how long each category is kept
```

### 3.2 MANIFEST.jsonl entry schema

Each line is a JSON object representing one capture event:

```json
{
  "manifest_id": 12834,
  "previous_hash": "sha256:9f4a...",
  "timestamp_utc": "2026-04-27T06:14:33.241Z",
  "camera_serial": "T8410P0023352DA9",
  "camera_name": "Living Room",
  "duration_seconds": 12.4,
  "files": {
    "mp4": {"path": "intake/2026-04-27/02:14:33_T8410P0023352DA9.mp4",
            "sha256": "abc123...", "bytes": 458112},
    "mp3": {"path": "intake/2026-04-27/02:14:33_T8410P0023352DA9.mp3",
            "sha256": "def456...", "bytes": 19840},
    "json": {"path": "intake/2026-04-27/02:14:33_T8410P0023352DA9.json",
             "sha256": "789abc..."}
  },
  "yamnet": [
    {"label": "Scream", "score": 0.81, "start": 1.2, "end": 2.8},
    {"label": "Adult crying", "score": 0.42, "start": 4.1, "end": 6.0}
  ],
  "whisper": {
    "model": "large-v3-turbo",  // matched to 0_LEGAL via intercom MSG-131
    "language_detected": "en",
    "segments_kept": 3,
    "segments_dropped_no_speech_prob": 4,  // hallucination filter
    "no_speech_prob_threshold": 0.7
  },
  "anamnesis_episode_id": "nvr_dellserver_litigation_20260427_..." ,
  "this_hash": "sha256:e74b..."     // sha256(canonical_json(this_entry without this_hash))
}
```

**Hash chain:** `previous_hash` of entry N = `this_hash` of entry N-1. First entry's `previous_hash` is `sha256:GENESIS`. Tampering with any entry breaks the chain from that point forward, detectable via a single forward scan.

### 3.3 Silence pruning

Sliding 30-second window. Window is kept if **any** of:

- RMS energy > threshold (configurable, default ≈ -40 dBFS)
- WebRTC VAD (mode 2) reports speech in ≥ 200ms
- YAMNet returns any class above 0.4 confidence

Otherwise discarded. This compresses 24h of mostly-silent kitchen audio to a few minutes of actual events. Threshold is per-camera-tunable in the UI.

---

## 4. Data model — new tables

```sql
-- Per-camera evidence settings (extends existing user_camera_preferences pattern)
CREATE TABLE evidence_camera_settings (
    serial          TEXT PRIMARY KEY REFERENCES cameras(serial) ON DELETE CASCADE,
    enabled         BOOLEAN NOT NULL DEFAULT FALSE,
    capture_video   BOOLEAN NOT NULL DEFAULT TRUE,    -- mp4 retention
    capture_audio   BOOLEAN NOT NULL DEFAULT TRUE,    -- mp3 + transcript
    silence_db_threshold REAL NOT NULL DEFAULT -40,
    classifier_categories JSONB NOT NULL DEFAULT '["screams","crying","impacts","raised-voices"]',
    retention_days  INTEGER NOT NULL DEFAULT 365,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Acoustic event index (queryable; not the source of truth — manifest is)
CREATE TABLE audio_events (
    id              BIGSERIAL PRIMARY KEY,
    manifest_id     BIGINT UNIQUE NOT NULL,
    camera_serial   TEXT NOT NULL REFERENCES cameras(serial),
    timestamp_utc   TIMESTAMPTZ NOT NULL,
    duration_s      REAL NOT NULL,
    primary_label   TEXT,                  -- highest-confidence YAMNet label
    primary_score   REAL,
    transcript_excerpt TEXT,               -- first 300 chars for grep
    intake_path     TEXT NOT NULL,
    flagged_paths   TEXT[],                -- symlink locations
    anamnesis_id    TEXT,
    case_id         BIGINT REFERENCES evidence_cases(id) ON DELETE SET NULL,
    promoted_at     TIMESTAMPTZ,
    INDEX (camera_serial, timestamp_utc),
    INDEX (primary_label) WHERE primary_label IS NOT NULL,
    INDEX (case_id) WHERE case_id IS NOT NULL
);

-- Generic case registry (Phase 5 — universal)
CREATE TABLE evidence_cases (
    id              BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,                 -- e.g. "Marital — 2026", "Mindhop dispute"
    consumer_id     TEXT NOT NULL,                 -- "0_LEGAL/0_MARITAL", "0_LEGAL/0_WORK/mindhop", external app id
    predicates      JSONB NOT NULL DEFAULT '{}',   -- e.g. {"cameras":["serial1"], "categories":["screams","insults"], "after":"2026-01-01"}
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    archived_at     TIMESTAMPTZ
);
```

`evidence_cases` is intentionally generic. The pipeline does not know about marital vs employment cases; it only knows about predicates over events. The consumer interprets and routes.

---

## 5. HTTP API (Phase 5)

All endpoints under `/api/evidence/`, authenticated.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/evidence/feed?since=<ts>&category=<x>` | Stream of new events since timestamp; long-poll friendly |
| `GET` | `/api/evidence/event/<id>` | Full manifest entry for one event |
| `GET` | `/api/evidence/event/<id>/file/<kind>` | Download mp4/mp3/json/txt (chain-of-custody hash returned in `X-Content-SHA256` header) |
| `POST` | `/api/evidence/cases` | Register a new case |
| `GET` | `/api/evidence/cases/<id>/events` | All events matching a case's predicates |
| `POST` | `/api/evidence/cases/<id>/promote` | Mark events as promoted to the case (sets `audio_events.case_id` + `promoted_at`) |
| `GET` | `/api/evidence/manifest/verify?from=<id>&to=<id>` | Verify hash-chain integrity over a range |

The `0_LEGAL` consumer is just a small daemon on `server` that:

1. polls `/api/evidence/feed`
2. for each event, decides which case it belongs to (UI prompt or auto-rules)
3. POSTs to `/api/evidence/cases/<id>/promote`
4. rsyncs the files into `~/0_LEGAL/<case>/{audio,video,transcripts}/` using the existing `YYYY-MM-DD_HHmm_source_description.ext` naming convention

That daemon is `~50 lines of Python`. It is NOT part of this NVR project — it lives in `0_LEGAL/`.

---

## 6. UI: "Collect Evidence" tab

Lives in the existing global settings modal alongside Streaming, Recording, Notifications. **Does not replace** any existing tab.

### 6.1 Layout

```
┌─ Collect Evidence ──────────────────────────────────────────┐
│                                                             │
│  Master switch: [ ON / off ]                                │
│                                                             │
│  Per-camera matrix:                                         │
│  ┌─────────────────┬────────┬───────┬──────────┬─────────┐ │
│  │ Camera          │ Enable │ Video │ Audio    │ Silence │ │
│  ├─────────────────┼────────┼───────┼──────────┼─────────┤ │
│  │ Living Room     │ [x]    │ [x]   │ [x]      │ -40 dB  │ │
│  │ Kitchen Office  │ [x]    │ [x]   │ [x]      │ -45 dB  │ │
│  │ Doorbell        │ [ ]    │       │          │         │ │
│  │ ...                                                    │ │
│  └─────────────────┴────────┴───────┴──────────┴─────────┘ │
│                                                             │
│  Detect (across all enabled cameras):                       │
│    [x] Screams                                              │
│    [x] Crying                                               │
│    [x] Impacts (slams, breaking)                            │
│    [x] Raised voices                                        │
│    [ ] (more YAMNet classes — future)                       │
│                                                             │
│  Retention: [ 365 days ▼ ]                                  │
│  Storage:   /litigation/  (892 GB free of 1.1 TB)           │
│                                                             │
│  Disclosure:                                                │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ Audio recording is enabled in your home. New York is   │ │
│  │ a one-party-consent state — recording is lawful when   │ │
│  │ you (the account holder) are a party to the conver-    │ │
│  │ sation or the conversation occurs in your residence.   │ │
│  │ You are responsible for compliance with applicable     │ │
│  │ law in your jurisdiction.                              │ │
│  │                                                        │ │
│  │ [ ] I have read and accept                             │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                             │
│  [ Save ]  [ Cancel ]                                       │
└─────────────────────────────────────────────────────────────┘
```

### 6.2 Disclosure semantics

The disclosure checkbox state (timestamp + user_id + IP) is logged in the manifest header at the moment evidence collection is enabled. This becomes part of the chain-of-custody record: "the system was activated by user X at time Y after acknowledging legal disclosure Z."

If the user is not in NY, the disclosure text is jurisdiction-aware (uses `nvr_settings.user_jurisdiction` or falls back to a generic warning). This is a 1-day chunk of work, deferred from Phase 4 to Phase 4.5.

---

## 7. Phase ordering & estimated effort

| Phase | Scope | Effort | Blocker |
|---|---|---|---|
| 0a | Storage: relabel `/dev/sdb` → `LITIGATION`, mount, fstab, dir tree, README | 0.5 day | **User confirmation: sdb contents disposable?** |
| 0b | Manifest writer + hash chain + DB migrations | 1 day | — |
| 1 | ffmpeg audio extractor service (silence-prune) | 2 days | Per-camera audio capability survey |
| 2 | YAMNet classifier service | 2 days | — |
| 3 | Whisper transcription + Anamnesis ingest | 2 days | ✓ unblocked — MSG-131 ACK received 2026-04-27 |
| 4 | "Collect Evidence" UI tab | 2 days | — |
| 4.5 | Jurisdiction-aware disclosure | 1 day | — |
| 5 | HTTP API + reference `0_LEGAL` consumer daemon | 3 days | — |
| 6 | "Child Monitor" UI tab (reuses Phase 2 classifier) | 1 day | — |

**Total: ~14 working days** to a feature-complete intake + UI + universal API.

---

## 8. Open questions

1. **`/dev/sdb` contents** — current state is unknown (sandbox blocked read-only inspection). User stated "use sdb, rename for purpose," but that needs to be authorized as: "wipe whatever is on sdb and use it as `/litigation/`."
2. **Drive label** — propose `LITIGATION`. Alternative: `EVIDENCE`. User pick.
3. ~~**Whisper model** — pending intercom reply from `server-legal` (MSG-131).~~ **RESOLVED 2026-04-27** — see §9 below.
4. **Audio-capable camera survey** — needs ffprobe per camera at Phase 1 start. The DB has `two_way_audio` but no `audio_input_supported` column. May need a one-time probe + new column.
5. **Anamnesis instance tag for these episodes** — `dellserver-nvr` (existing) vs new tag `dellserver-nvr-litigation` to make it filterable? I lean toward existing tag with a `tags: ["litigation"]` field on the episode body.

---

## 9. Whisper config (resolved via MSG-131 ACK from `server-legal`, 2026-04-27)

`0_LEGAL` runs `openai-whisper medium` on office's GTX 1660 with `--fp16 False` (mandatory — the 1660 lacks tensor cores and produces NaN tokens in FP16). For quality parity, NVR-side Phase 3 uses the same model but adapted to the different deployment context:

```
~/.evidence/venv/bin/whisper "<audio.mp3>" \
  --model medium \
  --output_format json \
  --output_dir <intake_dir> \
  --fp16 False                 # CPU mode on dellserver (no GPU); same flag value
  # NOTE: --language deliberately omitted — home audio is FR/EN/ES/PT
  # mix per user profile §1.5. Auto-detect handles mixed-language speech
  # better than forcing one language.
```

### 10.1 Differences from `0_LEGAL`'s Mindhop invocation

| Flag | `0_LEGAL` (Mindhop) | NVR pipeline | Why |
|---|---|---|---|
| `--model` | `medium` | `medium` | Match. |
| `--language` | `en` | *(omitted)* | Mindhop is monolingual English. Home audio is multilingual (FR/EN/ES/PT). |
| `--fp16` | `False` | `False` | Same flag, different reason — Mindhop runs on a 1660 (no tensor cores), NVR runs on Xeons (no GPU). Either way: `False`. |
| `--output_format` | `all` | `json` | We need segments + log-probs + token-level data programmatically; the `.txt` and `.vtt` are derived after. |
| Hardware | GTX 1660 (office) | dual Xeon E5-2690 v4 CPU (dellserver) | Whisper medium @ CPU runs ≈ 0.5× realtime on this CPU; acceptable for silence-pruned short clips. |

### 10.2 First-30s drop mitigation (per MSG-131 §4)

`server-legal` flagged that `medium` sometimes drops the first 30 seconds of long audio. Their workaround is a parallel `tiny` model pass for cross-validation. NVR pipeline implements the same:

```python
# Phase 3 pseudocode
medium_segments = whisper(audio, model="medium").segments
tiny_segments   = whisper(audio, model="tiny").segments

# If medium has nothing in [0, 30s] but tiny does, use tiny's segments for that window
if not any(s for s in medium_segments if s.start < 30) and \
   any(s for s in tiny_segments if s.start < 30):
    medium_segments = [s for s in tiny_segments if s.start < 30] + medium_segments
```

This adds ~10% to total transcription time but eliminates a known silent-drop mode that would otherwise lose the start of every clip — exactly the moment most likely to contain the triggering event.

### 10.3 Hallucination guard

Per the 2026-03-13 Whisper hallucination episode (`"Good night"` over silence with `no_speech_prob > 0.97`):

```python
KEPT_SEGMENTS = [
    s for s in whisper_segments
    if s["no_speech_prob"] < 0.7        # drops near-silent hallucinated text
    and s["avg_logprob"]   > -1.0       # drops low-confidence segments
]
```

These are **not deleted from the audio** — only from the transcript. The mp3 always retains the full waveform; only the `.txt` and `.json` are filtered. This way, if a dropped segment turns out to have been real speech (rare but possible), the audio is still there to re-transcribe manually with a different model or by ear.

### 10.4 GPU offload as future option

If transcription throughput becomes a bottleneck, Phase 3 can be retargeted to run on office's RX 6800 via the existing Anamnesis trainer's HTTP interface (`office:3011/inference/...` — see Anamnesis trainer README). That would lift `--fp16` (the RX 6800 has hardware FP16) and roughly 5× throughput. Not in scope for the initial implementation.

---

## 10. What this proposal does NOT do

- It does not classify "harassment", "nagging", "insults", or "arguments" automatically. Those are inherently contextual and an LLM verdict on them is not court-admissible. The transcripts are captured and indexed in Anamnesis; the user retrieves them and judges them.
- It does not auto-share evidence with anyone. Promotion to a case is always a user action.
- It does not train any model. The corpus exists for evidence and retrieval, not for fine-tuning.
- It does not replace the user's legal counsel.
