# Plan: Evidence Collection Pipeline

**Status:** Active — branch `evidence_gathering_APR_27_2026_a`
**Started:** 2026-04-27
**Last updated:** 2026-04-28

> Verbose filename per the new canonical convention: plans live at
> `docs/plans/<self_describing_name>.md`. The filename itself is the
> index entry — `ls docs/plans/` should describe the project without
> opening any file.

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

- `/litigation/` intake volume on dellserver (sdd, ext4, label LITIGATION)
- 24/7 audio capture from audio-capable cameras with silence-based pruning
- Acoustic classifier (YAMNet): `screams`, `crying`, `impacts`, `raised voices`
- Whisper transcription with hallucination guard
- Anamnesis ingestion of every transcribed segment
- Append-only hash-chained manifest (chain-of-custody)
- "Collect Evidence" UI tab — per-camera enable, per-category opt-in
- Read-only HTTP API exposing intake feed for external consumers

### In scope (Phase 5 — universal case-binding)

- Generic case API: register a case, define case predicates, pull matching events
- Reference consumer: `server:~/0_LEGAL` rsync-and-promote daemon
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
                   │       │ YAMNet classifier │    │ Whisper medium    │
                   │       │ scream/cry/impact │    │ + tiny first-30s  │
                   │       │ /raised-voice     │    │ + hallucinate-grd │
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
                   │       │   {ts}_{cam}.{mp3,txt,json}            │   │
                   │       │ /litigation/MANIFEST.jsonl (chain'd)   │   │
                   │       │ /litigation/flagged/{category}/        │   │
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

`/litigation/` lives on `/dev/sdd` (ext4, 1.1TB, relabeled `LITIGATION`, UUID `22b05160-1494-4cee-bdaf-e5a678aa46c5`, fstab'd with `nofail`).

It is also reachable from inside the project tree:

- **Host:** `~/0_MOBIUS.NVR/litigation` is a symlink to `/litigation`.
- **Container:** `/litigation` is bind-mounted at both `/litigation` (canonical) and `/app/litigation` (project-namespace). See `docker-compose.yml` and `${NVR_LITIGATION_PATH}` in `.env`.

The manifest module (`services/evidence/manifest.py`) resolves the volume root in this priority:

1. `LITIGATION_ROOT` env var (explicit override for tests),
2. `<project_root>/litigation/` if it exists (works in both host and container),
3. `/litigation/` as last fallback.

```
/litigation/
├── README.md                 # purpose, jurisdiction notes, retention policy
├── MANIFEST.jsonl            # append-only, hash-chained
├── intake/
│   └── 2026-04-27/
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

**Note on mp4:** The original architecture sketched mp4 capture to `/litigation/`. Pivoting to a leaner model: the existing recording pipeline already produces mp4s (motion + continuous). The evidence pipeline only writes audio (mp3) + transcript + classifier output, plus a manifest entry that references the time range. At promotion time, the case-binding daemon queries the recordings table for matching mp4s and rsyncs them into `~/0_LEGAL/<case>/video/`. This avoids duplicating the recording stack.

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
    "mp3": {"path": "intake/2026-04-27/02:14:33_T8410P0023352DA9.mp3",
            "sha256": "def456...", "bytes": 19840},
    "json": {"path": "intake/2026-04-27/02:14:33_T8410P0023352DA9.json",
             "sha256": "789abc..."}
  },
  "video_reference": {
    "recording_table_match": "(camera_serial, time_range)",
    "mp4_will_be_resolved_at_promotion": true
  },
  "yamnet": [
    {"label": "Screaming", "score": 0.81, "start": 1.2, "end": 2.8}
  ],
  "whisper": {
    "model": "medium",
    "language_detected": "en",
    "segments_kept": 3,
    "segments_dropped_no_speech_prob": 4,
    "no_speech_prob_threshold": 0.7
  },
  "anamnesis_episode_id": "nvr_dellserver_litigation_20260427_..." ,
  "this_hash": "sha256:e74b..."
}
```

**Hash chain:** `previous_hash` of entry N = `this_hash` of entry N-1. First entry's `previous_hash` is `sha256:GENESIS`. Tampering with any entry breaks the chain from that point forward, detectable via a single forward scan via `EvidenceManifest.verify_chain()`.

### 3.3 Silence pruning

Sliding 30-second window. Window is kept if **any** of:

- RMS energy > threshold (configurable, default ≈ -40 dBFS)
- WebRTC VAD (mode 2) reports speech in ≥ 200ms
- YAMNet returns any class above 0.4 confidence

Otherwise discarded. This compresses 24h of mostly-silent kitchen audio to a few minutes of actual events. Threshold is per-camera-tunable in the UI.

---

## 4. Data model — implemented in migrations 027/028/029

```sql
-- 027_add_evidence_tables.sql
CREATE TABLE evidence_camera_settings (...);  -- per-camera enable + tunables
CREATE TABLE evidence_cases           (...);  -- generic case registry
CREATE TABLE audio_events             (...);  -- queryable index of events

-- 028_add_audio_input_supported.sql
ALTER TABLE cameras
    ADD COLUMN audio_input_supported BOOLEAN,        -- tri-state: NULL/T/F
    ADD COLUMN audio_input_probed_at TIMESTAMPTZ;

-- 029_grant_evidence_tables_to_anon.sql
GRANT ... TO nvr_anon;  -- match existing PostgREST pattern
```

`evidence_cases` is intentionally generic. The pipeline does not know about marital vs employment cases; it only knows about predicates over events. The consumer interprets and routes.

`cameras.audio_input_supported` populated empirically by `scripts/survey_camera_audio.py` via ffprobe. Initial run on 19 cameras: 13 audio-capable, 6 probe failures (mostly stale MediaMTX paths), 0 confirmed without audio.

---

## 5. HTTP API (Phase 5)

All endpoints under `/api/evidence/`, authenticated.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/evidence/feed?since=<ts>&category=<x>` | Stream of new events since timestamp; long-poll friendly |
| `GET` | `/api/evidence/event/<id>` | Full manifest entry for one event |
| `GET` | `/api/evidence/event/<id>/file/<kind>` | Download mp3/json/txt (chain-of-custody hash returned in `X-Content-SHA256` header) |
| `POST` | `/api/evidence/cases` | Register a new case |
| `GET` | `/api/evidence/cases/<id>/events` | All events matching a case's predicates |
| `POST` | `/api/evidence/cases/<id>/promote` | Mark events as promoted (sets `audio_events.case_id` + `promoted_at`) |
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

## 7. Phase ordering & status

| Phase | Scope | Effort | Status |
|---|---|---|---|
| 0a | Storage: relabel `/dev/sdd` → `LITIGATION`, mount, fstab, dir tree, README | 0.5 day | ✅ done |
| 0b | Manifest writer + hash chain + DB migration 027 | 1 day | ✅ done |
| 1a | ffprobe survey + migration 028 + grants migration 029 | 0.5 day | ✅ done (13/19 cameras audio-capable) |
| 1b | Bind-mounts + symlink + project-relative path resolution in manifest | 0.25 day | ✅ done |
| 1c | ffmpeg audio extractor service (silence-prune, per-camera ffmpeg subprocess) | 2 days | 🔜 next |
| 2 | YAMNet classifier service | 2 days | — |
| 3 | Whisper transcription + Anamnesis ingest | 2 days | — |
| 4 | "Collect Evidence" UI tab | 2 days | — |
| 4.5 | Jurisdiction-aware disclosure | 1 day | — |
| 5 | HTTP API + reference `0_LEGAL` consumer daemon | 3 days | — |
| 6 | "Child Monitor" UI tab (reuses Phase 2 classifier) | 1 day | — |

**Total effort estimate: ~14 working days** to a feature-complete intake + UI + universal API. **Done so far: ~2.25 days.**

---

## 8. Open decisions before Phase 1c starts (defaults proposed)

1. **Sliding window length & overlap for silence-prune.** Default: **30s windows with 10% overlap** so events bridging window boundaries don't get bisected.
2. **Talkback exclusion.** When user press-to-talks via camera speaker, mic picks up speaker output. Default: **suppress capture during active talkback + 5s after release**.
3. **mp4 referencing strategy.** Per §3.1 note: pipeline writes audio + manifest only; mp4 resolved at promotion time from existing recordings table. (Non-default — was originally to be captured separately. Decided to lean on existing pipeline.)

---

## 9. Whisper config (resolved via MSG-131 ACK from `server-legal`, 2026-04-27)

`0_LEGAL` runs `openai-whisper medium` on office's GTX 1660 with `--fp16 False` (mandatory — the 1660 lacks tensor cores and produces NaN tokens in FP16). For quality parity, NVR-side Phase 3 uses the same model adapted to the different deployment context:

```
whisper "<audio.mp3>" \
  --model medium \
  --output_format json \
  --output_dir <intake_dir> \
  --fp16 False
  # NOTE: --language deliberately omitted — home audio is FR/EN/ES/PT
  # mix per user profile §1.5. Auto-detect handles mixed-language speech
  # better than forcing one language.
```

### 9.1 Differences from `0_LEGAL`'s Mindhop invocation

| Flag | `0_LEGAL` (Mindhop) | NVR pipeline | Why |
|---|---|---|---|
| `--model` | `medium` | `medium` | Match. |
| `--language` | `en` | *(omitted)* | Mindhop is monolingual English. Home audio is multilingual (FR/EN/ES/PT). |
| `--fp16` | `False` | `False` | Same flag value, different reason — Mindhop runs on a 1660 (no tensor cores), NVR runs on Xeons (no GPU). |
| `--output_format` | `all` | `json` | We need segments + log-probs + token-level data programmatically; the `.txt`/`.vtt` are derived after. |
| Hardware | GTX 1660 (office) | dual Xeon E5-2690 v4 CPU (dellserver) | Whisper medium @ CPU runs ≈ 0.5× realtime; acceptable for silence-pruned short clips. |

### 9.2 First-30s drop mitigation (per MSG-131 §4)

`server-legal` flagged that `medium` sometimes drops the first 30 seconds of long audio. Their workaround is a parallel `tiny` model pass for cross-validation. NVR pipeline implements the same:

```python
medium_segments = whisper(audio, model="medium").segments
tiny_segments   = whisper(audio, model="tiny").segments

if not any(s for s in medium_segments if s.start < 30) and \
   any(s for s in tiny_segments if s.start < 30):
    medium_segments = [s for s in tiny_segments if s.start < 30] + medium_segments
```

Adds ~10% to total transcription time but eliminates the silent-drop mode that would otherwise lose the start of every clip.

### 9.3 Hallucination guard

Per the 2026-03-13 Whisper hallucination episode (`"Good night"` over silence with `no_speech_prob > 0.97`):

```python
KEPT_SEGMENTS = [
    s for s in whisper_segments
    if s["no_speech_prob"] < 0.7        # drops near-silent hallucinated text
    and s["avg_logprob"]   > -1.0       # drops low-confidence segments
]
```

Audio waveform is **not** deleted — only transcript filtered. The mp3 always retains the full waveform; only `.txt`/`.json` are filtered. If a dropped segment turns out to have been real speech, the audio is still there to re-transcribe manually.

### 9.4 GPU offload as future option

If transcription throughput becomes a bottleneck, Phase 3 can be retargeted to run on office's RX 6800 via the existing Anamnesis trainer's HTTP interface (`office:3011/inference/...`). That would lift `--fp16` (the RX 6800 has hardware FP16) and roughly 5× throughput. Not in scope for the initial implementation.

---

## 10. What this proposal does NOT do

- It does not classify "harassment", "nagging", "insults", or "arguments" automatically. Those are inherently contextual and an LLM verdict on them is not court-admissible. The transcripts are captured and indexed in Anamnesis; the user retrieves them and judges them.
- It does not auto-share evidence with anyone. Promotion to a case is always a user action.
- It does not train any model. The corpus exists for evidence and retrieval, not for fine-tuning.
- It does not replace the user's legal counsel.
