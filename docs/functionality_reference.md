# MOBIUS.NVR — Functionality Reference

**Status:** SKELETON (Phase A of the E2E methodology — `docs/plans/cross_platform_deployment_assessment_and_storage_selection_and_e2e_test_methodology_2026_06_15.md`).
**Authored:** 2026-06-15, `dellserver-nvr`.
**Audience:** future E2E test authors + future Claude instances that need to know what the product actually does.

This document is the **specification source** for the E2E suite. Every row in the feature tables below corresponds to at least one E2E case. The suite is the truth check; this doc is the human-readable map. They must stay in sync (CLAUDE.md hard rule, 2026-06-15).

---

## How to read this doc

Each section covers one user-visible surface (Auth, Streaming, Settings, …). Each section has a feature table with columns:

- **ID** — stable identifier (e.g., `AUTH.LOGIN.OK`). Used by the E2E suite to address the case.
- **Trigger** — what the user does to invoke the feature.
- **Expected** — what should happen, in user-visible terms (no implementation detail).
- **Code anchors** — file paths where the behaviour is implemented; line refs when narrow enough to matter.
- **Verified** — `manual:YYYY-MM-DD` after a real human verification, or `e2e:PASS` once an automated case is wired and passing. `—` means never verified since being written.

Some rows are marked **TBD** — the row exists because the surface is known, the details are pending capture from the code. Future fills are tracked by changing TBD to a real entry.

---

## How to add a new feature row (HARD RULE)

When a feature is added/changed/removed in a branch:

1. Add or update the row in the table below in the **same commit** as the code change.
2. Add or update the matching E2E case (in the same branch).
3. The pre-push hook is intended (future work) to refuse a push that touches `routes/*.py` or `static/js/*` without a corresponding diff in this file — unless the commit subject carries `[NO_FEATURE_CHANGE]` for pure internal refactors.
4. CLAUDE.md "CRITICAL - README.md must be updated on every user-facing change" (2026-06-15) covers the same intent at the README layer; this doc is the more granular layer.

---

## Section index

| Section | What lives there |
|---|---|
| [Auth](#auth) | Login, logout, role gate, trusted-network bypass |
| [Streams page (/streams)](#streams-page-streams) | Grid view, fullscreen, expand, pin |
| [Light page (/light)](#light-page-light) | Snapshot-only viewer for low-power devices |
| [Stream lifecycle](#stream-lifecycle) | Start / stop / restart per camera; auto-recover |
| [Snapshots](#snapshots) | `/api/snap`, snapshot polling, signal-lost overlay |
| [PTZ](#ptz) | Pan / tilt / zoom / preset |
| [Recording](#recording) | Manual, motion-triggered, continuous; playback |
| [Motion detection](#motion-detection) | Reolink Baichuan, ONVIF PullPoint, FFmpeg scene |
| [Two-way audio](#two-way-audio) | Talkback, audio listen |
| [Settings — global modal](#settings--global-modal) | View, fullscreen, streaming, audio, performance, evidence, eufy bridge, storage, network, logs, data |
| [Settings — per-camera](#settings--per-camera) | Stream type, display order, visibility, nickname |
| [Storage management](#storage-management) | Migration, archive cleanup, reconcile |
| [Telemetry event log](#telemetry-event-log) | Data tab toggle, max size cap, retention, usage widget |
| [Audit log](#audit-log) | Settings audit (server) + UI events (browser) |
| [Camera management](#camera-management) | Add, edit, delete, settings modal |
| [User management](#user-management) | Add user, change role, change password, access control |
| [Host agent](#host-agent) | DPMS reporting, per-host throttle |
| [Eufy bridge](#eufy-bridge) | Bridge status, restart, auth |
| [Evidence collection (currently OFF)](#evidence-collection-currently-off) | Master switch, per-camera matrix, disclosure |
| [Health monitoring](#health-monitoring) | Camera state badges, publisher state, watchdog |
| [Power cycle](#power-cycle) | Hubitat-driven outlet cycle on failure |
| [API surfaces](#api-surfaces) | Cross-cuts the above; one row per endpoint family |

---

## Auth

Code anchors: [routes/auth.py](../routes/auth.py), [templates/login.html](../templates/login.html), [routes/helpers.py](../routes/helpers.py).

| ID | Trigger | Expected | Code anchors | Verified |
|---|---|---|---|---|
| `AUTH.LOGIN.OK` | User submits correct username + password on `/login` | Redirect to `/streams` (or `/light` per device sniff), session cookie set, `user_sessions` row created | [routes/auth.py](../routes/auth.py), [routes/helpers.py#_create_user_session](../routes/helpers.py) | e2e:PASS |
| `AUTH.LOGIN.WRONG_PASSWORD` | User submits valid username + wrong password | Stays on `/login`, error banner, no session cookie | [routes/auth.py](../routes/auth.py) | e2e:PASS |
| `AUTH.LOGIN.UNKNOWN_USER` | User submits username that doesn't exist | Same banner as wrong password (no user-enumeration leak) | [routes/auth.py](../routes/auth.py) | e2e:PASS |
| `AUTH.LOGOUT` | User clicks Logout | Session destroyed, `user_sessions.is_active=false`, redirect to `/login` | [routes/auth.py](../routes/auth.py), [routes/helpers.py#_deactivate_user_session](../routes/helpers.py) | e2e:PASS |
| `AUTH.ROLE.ADMIN_ONLY` | Viewer-role user hits an admin-only endpoint (e.g., `/api/telemetry/settings`) | HTTP 403 JSON `{"error": "Admin access required"}` | [routes/telemetry.py](../routes/telemetry.py), [routes/audit_routes.py](../routes/audit_routes.py), [routes/storage.py](../routes/storage.py) | e2e:PASS |
| `AUTH.TRUSTED_NETWORK.BYPASS` | Operator with `TRUSTED_NETWORK_ENABLED=true` + matching subnet hits any page | Skips login, lands directly on `/streams` (or `/light`) | [routes/auth.py](../routes/auth.py), `trusted_devices` table | — |
| `AUTH.CHANGE_PASSWORD.FIRST_LOGIN` | New user logs in with `must_change_password=true` | Forced redirect to `/change_password` until set (today: enforced only on the post-login redirect — see test docstring for the navigation-bypass gap) | [templates/change_password.html](../templates/change_password.html) | e2e:PASS (initial redirect only) |
| `AUTH.CSRF.EXEMPT_JSON_API` | JS `fetch` POST to any `/api/*` endpoint without an X-CSRFToken header | Endpoint processes the request (all API blueprints are CSRF-exempted at app boot — see [app.py:201-205](../app.py)). Telemetry was the last one added 2026-06-14. | [app.py](../app.py) | e2e:PASS |

---

## Streams page (/streams)

Code anchors: [routes/config.py:streams_page](../routes/config.py), [templates/streams.html](../templates/streams.html), [static/js/streaming/](../static/js/streaming/), [static/js/layout/](../static/js/layout/).

| ID | Trigger | Expected | Code anchors | Verified |
|---|---|---|---|---|
| `STREAMS.PAGE.LOAD` | User navigates to `/streams` (admin or viewer) | Grid renders with all permitted cameras; each tile gets a stream of its configured `stream_type` | [routes/config.py](../routes/config.py) | — |
| `STREAMS.PAGE.UA_SNIFF_REDIRECT` | iOS / mobile UA hits `/streams` without `?full=1` | Redirect to `/light` unless `localStorage.nvr_preferred_mode == 'full'` | [routes/config.py](../routes/config.py) | e2e:PASS |
| `STREAMS.GRID.LAYOUT_MODES` | User toggles grid mode (Settings → View) | Layout updates without reload: uniform / last-row-stretch / auto-fit / masonry | [static/js/layout/](../static/js/layout/) | — |
| `STREAMS.GRID.HOVER_SHOWS_ACTION_BAR` | Mouse hovers a grid tile | Action bar fades in (24x24 icons at bottom of tile) within 250ms | [static/css/components/stream-control-bar.css](../static/css/components/stream-control-bar.css) | manual:2026-06-13 |
| `STREAMS.EXPAND.TILE_CLICK` | User clicks on a grid tile (not on a button) | Tile expands to modal-overlay size; action bar enlarges to 32x32 | [static/js/streaming/](../static/js/streaming/) | — |
| `STREAMS.EXPAND.AUTO_FULLSCREEN_FOR_KIOSK` | Chrome kiosk profile loads `/streams?fullscreen=<nickname>` | Resolves nickname → serial server-side, opens fullscreen on that camera | [routes/config.py](../routes/config.py) | — |
| `STREAMS.FULLSCREEN.EXIT_VIA_X` | User clicks the X in top-right of an open overlay (PTZ or controls) in fullscreen | Overlay closes via the existing toggle handler; fullscreen state preserved | [static/js/streaming/overlay-close.js](../static/js/streaming/overlay-close.js) | manual:2026-06-13 |
| `STREAMS.PIN.WINDOW` | User clicks the pin icon on a tile | Tile pops into a separate floating window (pinned-window mode) | [static/js/streaming/](../static/js/streaming/) | TBD |
| `STREAMS.IDLE_FADE.30S` | User stops moving mouse for 30s while viewing | Action bar fades out (whole .stream-actions-bar gets `.idle` class) | [static/js/streaming/control-bar-idle.js](../static/js/streaming/control-bar-idle.js) | manual:2026-06-13 |
| `STREAMS.RESIZE.RESPONSIVE` | User resizes browser window | Grid re-flows; tile aspect ratios preserved | [static/css/components/](../static/css/components/) | — |

---

## Light page (/light)

Code anchors: [routes/config.py:streams_light_page](../routes/config.py), [templates/streams_light.html](../templates/streams_light.html).

| ID | Trigger | Expected | Code anchors | Verified |
|---|---|---|---|---|
| `LIGHT.PAGE.LOAD` | iOS / low-power device hits `/light` | Snapshot-only grid renders, 4 cameras per page, 1fps polling | [routes/config.py](../routes/config.py) | — |
| `LIGHT.PAGINATE` | User taps next/prev arrows | Next batch of 4 cameras renders | [templates/streams_light.html](../templates/streams_light.html) | TBD |
| `LIGHT.PREFER_FULL.OVERRIDE` | User sets `localStorage.nvr_preferred_mode = 'full'` and hits `/streams` from mobile | Stays on `/streams` instead of redirecting to `/light` | [routes/config.py](../routes/config.py) | e2e:PASS |
| `LIGHT.SIGNAL_LOST_OVERLAY` | A camera's publisher dies and `/api/snap` returns 503 | Tile goes opaque (`#0a0a0a`), shows "Signal lost" instead of a stale frame | [static/js/streaming/snapshot-stream.js](../static/js/streaming/snapshot-stream.js) | manual:2026-06-13 |

---

## Stream lifecycle

Code anchors: [routes/streaming.py](../routes/streaming.py), [services/stream_watchdog.py](../services/stream_watchdog.py), [services/camera_state_tracker.py](../services/camera_state_tracker.py).

| ID | Trigger | Expected | Code anchors | Verified |
|---|---|---|---|---|
| `STREAM.START.OK` | `POST /api/stream/start/<serial>` for an offline camera | Stream starts within ~10s; state goes `OFFLINE` → `STARTING` → `ONLINE`; publisher_active=true | [routes/streaming.py](../routes/streaming.py) | — |
| `STREAM.START.NO_CREDS` | Start a camera with missing/invalid credentials | Stream stays in `STARTING` then transitions to `DEGRADED` with `failure_count>=1` | [services/camera_state_tracker.py](../services/camera_state_tracker.py) | — |
| `STREAM.STOP.OK` | `POST /api/stream/stop/<serial>` for an online camera | `<video>.pause()`, `srcObject=null`, `load()`; tile shows signal-lost overlay; FFmpeg child killed | [routes/streaming.py](../routes/streaming.py), [static/js/streaming/stream.js](../static/js/streaming/stream.js) | manual:2026-06-13 |
| `STREAM.RESTART.OK` | `POST /api/stream/restart/<serial>` on a stalled stream | FFmpeg child killed + restarted; publisher comes back within 15s | [routes/streaming.py](../routes/streaming.py) | — |
| `STREAM.WATCHDOG.AUTO_RECOVER` | Backend detects a publisher that's been stalled > N seconds | Watchdog restarts the stream automatically (exponential backoff) | [services/stream_watchdog.py](../services/stream_watchdog.py) | TBD |
| `STREAM.SINGLE_CONSUMER` | Two consumers (motion detector + recording) for a budget camera | Both tap the streaming hub (mediamtx/go2rtc/native_mjpeg); ONE RTSP connection at the camera | [services/streaming_hub.py](../services/streaming_hub.py) | TBD |

---

## Snapshots

Code anchors: [routes/streaming.py:/api/snap](../routes/streaming.py), [static/js/streaming/snapshot-stream.js](../static/js/streaming/snapshot-stream.js).

| ID | Trigger | Expected | Code anchors | Verified |
|---|---|---|---|---|
| `SNAP.GET.OK` | `GET /api/snap/<serial>` while publisher is healthy | JPEG body, 200, fresh frame (< 5s old) | [routes/streaming.py](../routes/streaming.py) | — |
| `SNAP.GET.PUBLISHER_OFFLINE` | `GET /api/snap/<serial>` while publisher state ∈ {`degraded`, `offline`} | 503 with text body; client adds `.signal-lost` to tile | [routes/streaming.py](../routes/streaming.py), [static/js/streaming/snapshot-stream.js](../static/js/streaming/snapshot-stream.js) | static-guard:PASS (full e2e blocked by tracker-state access) |
| `SNAP.POLL.LIGHT_PAGE` | `/light` page open, 1fps polling | Each tile fetches a fresh `/api/snap` every 1000ms | [static/js/streaming/snapshot-stream.js](../static/js/streaming/snapshot-stream.js) | — |
| `SNAP.POLL.SUSPEND_ON_HIDDEN` | User switches tabs / monitor sleeps | Polling pauses via Page Visibility API; resumes on wake | [static/js/streaming/snapshot-stream.js](../static/js/streaming/snapshot-stream.js) | — |

---

## PTZ

Code anchors: [routes/ptz.py](../routes/ptz.py), [services/ptz/](../services/ptz/), [static/js/controllers/](../static/js/controllers/).

| ID | Trigger | Expected | Code anchors | Verified |
|---|---|---|---|---|
| `PTZ.MOVE.PAN` | `POST /api/ptz/<serial>/move {direction:"left"}` on a PTZ-capable camera | Camera pans; movement stops on PTZ.STOP or after timeout | [routes/ptz.py](../routes/ptz.py) | — |
| `PTZ.MOVE.NON_PTZ_CAM` | PTZ command on a fixed camera | 400 / 405, no side effects | [routes/ptz.py](../routes/ptz.py) | — |
| `PTZ.PRESET.GOTO` | `POST /api/ptz/<serial>/preset/<id>` | Camera moves to preset; cached preset list updated if drift detected | [routes/ptz.py](../routes/ptz.py) | — |
| `PTZ.PRESETS.LIST` | `GET /api/ptz/<serial>/presets` | JSON list of preset id + label | [routes/ptz.py](../routes/ptz.py) | — |
| `PTZ.UI.OVERLAY_OPEN_CLOSE` | User clicks PTZ-toggle button on an expanded tile | PTZ overlay appears; close-X dismisses it; bar stays accessible | [static/js/streaming/overlay-close.js](../static/js/streaming/overlay-close.js) | manual:2026-06-13 |
| `PTZ.NEOLINK_LATENCY` | PTZ command on a Neolink E1 (Baichuan bridge) | Movement starts within ~5s (known Baichuan overhead, documented in README) | [services/ptz/](../services/ptz/) | TBD |

---

## Recording

Code anchors: [routes/recording.py](../routes/recording.py), [services/recording/](../services/recording/).

| ID | Trigger | Expected | Code anchors | Verified |
|---|---|---|---|---|
| `REC.MANUAL.START` | `POST /api/recording/start/<serial>` | Creates `recordings` row with `status='recording'`; FFmpeg writes to `/recordings/manual/...` | [routes/recording.py](../routes/recording.py) | — |
| `REC.MANUAL.STOP` | `POST /api/recording/stop/<serial>` | FFmpeg flushes + exits; row status → `completed` | [routes/recording.py](../routes/recording.py) | — |
| `REC.CONTINUOUS.START` | `POST /api/recording/continuous/start/<serial>` | 24/7 segments under `/recordings/continuous/<serial>/...` | [routes/recording.py](../routes/recording.py) | — |
| `REC.MOTION.TRIGGER` | Motion detected on a camera with motion recording enabled | Recording starts; segment lives under `/recordings/motion/<serial>/...`; pre-buffer included | [services/motion/](../services/motion/), [services/recording/](../services/recording/) | TBD |
| `REC.MIGRATION.AGE_OUT` | Settings → Storage → Migrate Now (or hourly tick) | Files older than `age_threshold_days` move from `/recordings/...` to `/recordings/STORAGE/...` | [services/recording/storage_migration.py](../services/recording/storage_migration.py) | — |
| `REC.CLEANUP.ARCHIVE_RETENTION` | Settings → Storage → Cleanup Now (or hourly tick) | Files older than `archive_retention_days` in archive are deleted | [routes/storage.py](../routes/storage.py) | — |
| `REC.PLAYBACK.TIMELINE` | User opens timeline modal for a camera | Scrubbable timeline with motion + recording segments overlaid | [static/js/modals/timeline-playback-modal.js](../static/js/modals/timeline-playback-modal.js) | TBD |
| `REC.STATUS.CONSTRAINT` | INSERT a row with `status='failed'` | DB rejects (CHECK constraint allows only `recording|completed|archived|error`) | [psql/migrations/](../psql/migrations/), [memory/project_recordings_status_constraint](../../.claude/projects/-home-elfege-0-MOBIUS-NVR/memory/project_recordings_status_constraint.md) | — |

---

## Motion detection

Code anchors: [services/motion/](../services/motion/), [services/onvif/onvif_event_listener.py](../services/onvif/onvif_event_listener.py).

| ID | Trigger | Expected | Code anchors | Verified |
|---|---|---|---|---|
| `MOTION.BAICHUAN.REOLINK` | Reolink camera with Baichuan-protocol motion fires | Motion event written to `motion_events` table; downstream subscribers notified | [services/motion/](../services/motion/) | — |
| `MOTION.ONVIF.PULLPOINT` | ONVIF-compatible camera fires PullPoint event | Same as above; some vendors return "Action Not Implemented" — known limitation | [services/onvif/onvif_event_listener.py](../services/onvif/onvif_event_listener.py) | — |
| `MOTION.FFMPEG.SCENE` | Pixel-diff threshold crossed by FFmpeg scene detector | Same as above; sensitivity auto-adjusted for WebRTC-relay cameras | [services/motion/ffmpeg_motion_detector.py](../services/motion/ffmpeg_motion_detector.py) | — |

---

## Two-way audio

Code anchors: [routes/talkback.py](../routes/talkback.py), [services/talkback/](../services/talkback/) (TBD path).

| ID | Trigger | Expected | Code anchors | Verified |
|---|---|---|---|---|
| `AUDIO.LISTEN.TOGGLE` | User clicks audio button on a tile | Stream audio plays; second click mutes | [static/js/streaming/](../static/js/streaming/) | TBD |
| `AUDIO.TALKBACK.EUFY` | User holds talkback button on an Eufy camera | Mic stream opens to Eufy bridge; releasing closes | [routes/talkback.py](../routes/talkback.py) | TBD |
| `AUDIO.TALKBACK.ONVIF_BACKCHANNEL` | User holds talkback on an SV3C/Amcrest with ONVIF backchannel | go2rtc-routed ONVIF backchannel opens | [routes/talkback.py](../routes/talkback.py) | TBD |

---

## Settings — global modal

Code anchors: [static/js/modals/global-settings-modal.js](../static/js/modals/global-settings-modal.js).

| ID | Trigger | Expected | Code anchors | Verified |
|---|---|---|---|---|
| `SETTINGS.MODAL.OPEN` | User clicks the gear icon | Modal opens, "View" tab active by default | [static/js/modals/global-settings-modal.js](../static/js/modals/global-settings-modal.js) | — |
| `SETTINGS.HEADER_SAVE` | User clicks "Save" in the modal header | Tab-aware save: streaming → hub assignments; evidence → evidence save; data → telemetry save; otherwise → advanced-settings batch save. ALWAYS flushes pending Data tab changes too. | [static/js/modals/global-settings-modal.js](../static/js/modals/global-settings-modal.js) | manual:2026-06-14 |
| `SETTINGS.TAB.SWITCH_AUTOSAVE_DATA` | User makes a change in Data tab, clicks another tab | Data tab pending changes flushed fire-and-forget; tab switch is not blocked | [static/js/modals/global-settings-modal.js](../static/js/modals/global-settings-modal.js) | manual:2026-06-14 |
| `SETTINGS.TAB.VIEW.GRID_STYLE` | User changes grid style dropdown | Layout re-renders without reload | [static/js/modals/global-settings-modal.js](../static/js/modals/global-settings-modal.js) | — |
| `SETTINGS.TAB.FULLSCREEN.AUTO_DELAY` | Admin sets auto-fullscreen delay | Persisted to `user_camera_preferences`; effect on next page load | [static/js/modals/global-settings-modal.js](../static/js/modals/global-settings-modal.js) | — |
| `SETTINGS.TAB.PERFORMANCE.THROTTLE` | Admin opens Performance tab | Per-host throttle controls render (CPU max, hysteresis, enable) | [static/js/settings/performance-throttle.js](../static/js/settings/performance-throttle.js) | — |
| `SETTINGS.TAB.EVIDENCE.HIDDEN_WHEN_OFF` | Master `evidence_collection_enabled` flag is false | "Collect Evidence" tab does NOT render in the modal | [routes/config.py](../routes/config.py), [static/js/modals/global-settings-modal.js](../static/js/modals/global-settings-modal.js) | manual:2026-06-15 |
| `SETTINGS.TAB.DATA.RENDER` | Admin clicks the Data tab | Storage overview (recent + archive bars + warnings) + telemetry toggle + max size slider + retention radios render | [static/js/settings/data-tab.js](../static/js/settings/data-tab.js) | manual:2026-06-14 |
| `SETTINGS.TAB.DATA.ADMIN_ONLY` | Viewer-role user opens settings modal | Data tab not in the tab strip; `GET /api/telemetry/settings` returns 403 if hit directly | [static/js/modals/global-settings-modal.js](../static/js/modals/global-settings-modal.js), [routes/telemetry.py](../routes/telemetry.py) | — |
| `SETTINGS.TAB.LOGS.OPEN_AUDIT_LOG` | Admin clicks "Open Audit Log" in Logs tab | `auditLogModal` opens, paginated rows from `setting_audit_log` | [static/js/modals/audit-log-modal.js](../static/js/modals/audit-log-modal.js) | — |
| `SETTINGS.TAB.STORAGE.STATS` | Admin opens Storage tab | Disk usage per tier + warnings; migration controls present | [static/js/settings/storage-status.js](../static/js/settings/storage-status.js) | — |
| `SETTINGS.TAB.NETWORK.TRUSTED` | Admin toggles trusted-network | Persisted to `nvr_settings`; effect on next session check | [routes/config.py](../routes/config.py) | — |

---

## Settings — per-camera

Code anchors: [static/js/modals/camera-settings-modal.js](../static/js/modals/camera-settings-modal.js), [routes/settings_routes.py](../routes/settings_routes.py).

| ID | Trigger | Expected | Code anchors | Verified |
|---|---|---|---|---|
| `CAM.SETTINGS.OPEN` | User clicks gear on a tile | Per-camera modal opens with current config across 6 tabs | [static/js/modals/camera-settings-modal.js](../static/js/modals/camera-settings-modal.js) | — |
| `CAM.SETTINGS.STREAM_TYPE.CHANGE` | User picks a different stream type (e.g., WEBRTC → LL_HLS) | Persisted to `user_camera_preferences`; tile re-renders without reload | [routes/settings_routes.py](../routes/settings_routes.py) | — |
| `CAM.SETTINGS.STREAMING_HUB.CHANGE` | Admin changes streaming hub (mediamtx → go2rtc → native_mjpeg) | Persisted to `cameras.streaming_hub`; the 4-place rule means the field must be in DIRECT_FIELDS and repo's `direct_fields` — see `memory/project_camera_field_4_places_rule` | [routes/settings_routes.py](../routes/settings_routes.py), `memory/project_camera_field_4_places_rule` | — |
| `CAM.SETTINGS.NICKNAME.SET` | Admin sets a nickname | Saved to `cameras.nickname`; appears in display and resolves in `?fullscreen=` URL param | [routes/settings_routes.py](../routes/settings_routes.py) | — |
| `CAM.SETTINGS.VISIBILITY.HIDE` | User toggles "Hide" for a camera | Camera disappears from their grid; admin still sees it | [routes/settings_routes.py](../routes/settings_routes.py) | — |
| `CAM.SETTINGS.DISPLAY_ORDER` | User drags tiles to reorder | Order persisted in `user_camera_preferences`; survives reload | [routes/settings_routes.py](../routes/settings_routes.py) | — |

---

## Storage management

Code anchors: [routes/storage.py](../routes/storage.py), [static/js/settings/storage-status.js](../static/js/settings/storage-status.js).

| ID | Trigger | Expected | Code anchors | Verified |
|---|---|---|---|---|
| `STORAGE.STATS.READ` | `GET /api/storage/stats` (admin) | JSON with `recent`, `archive`, `config`, `warnings` keys; used by Storage tab AND Data tab overview widget | [routes/storage.py:137](../routes/storage.py) | manual:2026-06-14 |
| `STORAGE.MIGRATE.MANUAL` | Admin clicks "Migrate Now" in Storage tab | Files older than `age_threshold_days` move to archive; progress streams via SSE/poll | [routes/storage.py](../routes/storage.py) | TBD |
| `STORAGE.CLEANUP.MANUAL` | Admin clicks "Cleanup Archive" | Files older than `archive_retention_days` deleted from archive | [routes/storage.py](../routes/storage.py) | TBD |
| `STORAGE.RECONCILE` | Admin clicks "Reconcile" | Filesystem walked; orphan files removed, missing-file rows updated | [routes/storage.py](../routes/storage.py) | TBD |
| `STORAGE.SETTINGS.UPDATE` | Admin sets `archive_retention_days=60` | Persisted to `recording_settings.json` via `/api/storage/settings`; migration service reloaded | [routes/storage.py:495](../routes/storage.py) | — |
| `STORAGE.CANCEL_IN_PROGRESS` | Admin clicks "Cancel" during a migration | Operation halts cleanly; partial migration is durable | [routes/storage.py](../routes/storage.py) | TBD |

---

## Telemetry event log

Code anchors: [routes/telemetry.py](../routes/telemetry.py), [services/telemetry_*.py](../services/), [static/js/settings/data-tab.js](../static/js/settings/data-tab.js).

| ID | Trigger | Expected | Code anchors | Verified |
|---|---|---|---|---|
| `TELEM.DEFAULT.OFF` | Fresh install or never-toggled deployment | `telemetry_enabled=false`; probes no-op every tick; `telemetry_events` empty | [psql/migrations/042_create_telemetry_events_and_settings.sql](../psql/migrations/042_create_telemetry_events_and_settings.sql) | manual:2026-06-14 |
| `TELEM.ENABLE.VIA_DATA_TAB` | Admin toggles telemetry ON in Data tab, clicks header Save | `nvr_settings.telemetry_enabled='true'` persisted; probes start writing within one tick (~60s) | [static/js/settings/data-tab.js](../static/js/settings/data-tab.js), [routes/telemetry.py](../routes/telemetry.py) | manual:2026-06-14 |
| `TELEM.PROBE.CAMERA_STATE_TRANSITION` | Camera goes `ONLINE` → `DEGRADED` while telemetry on | Row inserted: `category='camera_state'`, `subcategory='transition'`, payload contains `from`/`to`/`failure_count` | [services/camera_state_tracker.py](../services/camera_state_tracker.py), [services/telemetry_event_log.py](../services/telemetry_event_log.py) | manual:2026-06-14 |
| `TELEM.PROBE.PUBLISHER_TRANSITION` | Camera publisher flips active → inactive | Row inserted: `category='publisher'`, `subcategory='transition'` | [services/camera_state_tracker.py](../services/camera_state_tracker.py) | manual:2026-06-14 |
| `TELEM.PROBE.MEDIAMTX_DIFF` | MediaMTX path's publisher count changes between two 60s ticks | Row inserted: `category='mediamtx_path'`, payload has `from`/`to` snapshots | [services/telemetry_probes.py](../services/telemetry_probes.py) | manual:2026-06-14 |
| `TELEM.PROBE.GO2RTC_DIFF` | go2rtc stream producer/consumer count changes | Row inserted: `category='go2rtc_path'` | [services/telemetry_probes.py](../services/telemetry_probes.py) | manual:2026-06-14 |
| `TELEM.PROBE.RTSP_FFPROBE_FAIL` | In-container ffprobe to a hub URL fails (e.g., the Terrace Shed 404 case) | Row inserted: `category='rtsp_probe'`, `subcategory='probe_fail'`, payload has url + error | [services/telemetry_probes.py](../services/telemetry_probes.py) | — |
| `TELEM.PROBE.RESOURCE_SNAPSHOT` | Every 60s tick while enabled | Row inserted: `category='resource_snapshot'`, payload has ffmpeg_count, gunicorn RSS, conntrack count, etc. | [services/telemetry_probes.py](../services/telemetry_probes.py) | manual:2026-06-14 |
| `TELEM.CLEANUP.RETENTION` | Hourly tick + rows older than retention window exist | Old rows deleted (max 50k per pass to bound lock time) | [services/telemetry_cleanup.py](../services/telemetry_cleanup.py) | — |
| `TELEM.CLEANUP.SIZE_CAP` | Table reaches 90% of admin-set max size | Oldest rows deleted until table is at 80% of cap (hysteresis prevents flapping) | [services/telemetry_cleanup.py](../services/telemetry_cleanup.py) | — |
| `TELEM.CLEANUP.IMMEDIATE_ON_CAP_REDUCE` | Admin reduces max-size cap below current usage | Cleanup runs immediately, not at next hourly tick | [routes/telemetry.py](../routes/telemetry.py) | — |
| `TELEM.DISABLE.PRESERVES_ROWS` | Admin toggles telemetry OFF after collecting data | Existing rows remain readable via `/api/telemetry/recent` and SQL views | [services/telemetry_cleanup.py](../services/telemetry_cleanup.py) | — |
| `TELEM.UI.STORAGE_OVERVIEW_RENDER` | Admin opens Data tab | Two storage bars render (recent + archive) with correct GB values + warnings strip if any | [static/js/settings/data-tab.js](../static/js/settings/data-tab.js) | manual:2026-06-14 |
| `TELEM.UI.DEBOUNCE_FLAPPING` | A single camera flaps state >100 times in 30s | Only one row written for each `(category, camera_id, subcategory, from→to)` per 30s window | [services/telemetry_event_log.py](../services/telemetry_event_log.py) | — |
| `TELEM.API.RECENT.PAGINATION` | `GET /api/telemetry/recent?limit=1500` | Returns at most 1000 rows (hard cap), DESC ts order | [routes/telemetry.py](../routes/telemetry.py) | — |

---

## Audit log

Code anchors: [routes/audit_routes.py](../routes/audit_routes.py), [services/audit_listener.py](../services/audit_listener.py), [static/js/modals/audit-log-modal.js](../static/js/modals/audit-log-modal.js).

| ID | Trigger | Expected | Code anchors | Verified |
|---|---|---|---|---|
| `AUDIT.SETTINGS.INSERT_ROW_FIRES_TRIGGER` | INSERT into any of `cameras` / `nvr_settings` / `user_camera_preferences` / `trusted_devices` / `camera_credentials` / `host_settings` / `evidence_camera_settings` | New `setting_audit_log` row with `op='INSERT'`, full new value as JSONB | [psql/migrations/036_setting_audit_log_and_triggers.sql](../psql/migrations/036_setting_audit_log_and_triggers.sql) | — |
| `AUDIT.SETTINGS.UPDATE_ROW_FIRES_TRIGGER` | UPDATE on any audit-tracked table | Same shape, `op='UPDATE'`, `old` + `new` JSON | [psql/migrations/036_setting_audit_log_and_triggers.sql](../psql/migrations/036_setting_audit_log_and_triggers.sql) | — |
| `AUDIT.COVERAGE.STATIC_CHECK` | New audit-tracked table added without trigger | `tests/test_audit_coverage.py` fails CI | [tests/test_audit_coverage.py](../tests/test_audit_coverage.py) | e2e:PASS |
| `AUDIT.LISTEN_NOTIFY.LIVE_FANOUT` | A `setting_audit_log` INSERT happens | `audit_listener.py` consumes via LISTEN/NOTIFY, emits `setting_changed` over `/stream_events` socketio | [services/audit_listener.py](../services/audit_listener.py) | — |
| `UI_EVENT.OUTBOX.POST_BATCH` | Browser auditOutbox flushes 20 events | `POST /api/ui-event/record` writes one `ui_event_log` row per event | [routes/ui_event_routes.py](../routes/ui_event_routes.py) | — |
| `UI_EVENT.PASSWORD_MASK` | User types in a password field | Event payload stores `*` of the same length, never the plaintext | [routes/ui_event_routes.py](../routes/ui_event_routes.py) | — |
| `AUDIT.LOG.ADMIN_ONLY_READ` | Viewer hits `/api/audit/recent` | 403 | [routes/audit_routes.py](../routes/audit_routes.py) | — |

---

## Camera management

Code anchors: [routes/camera.py](../routes/camera.py), [static/js/modals/camera-settings-modal.js](../static/js/modals/camera-settings-modal.js), [static/js/modals/device-management-modal.js](../static/js/modals/device-management-modal.js).

| ID | Trigger | Expected | Code anchors | Verified |
|---|---|---|---|---|
| `CAM.ADD.OK` | Admin adds a new camera via UI (or `cameras.json` seed at startup) | Row created in DB; configs regenerated; camera appears in grid after restart or hot-reload | [routes/camera.py](../routes/camera.py), [services/camera_config_sync.py](../services/camera_config_sync.py) | TBD |
| `CAM.EDIT.HOST_CHANGE` | Admin changes camera's IP / host | DB updated; streaming hub regenerates; FFmpeg uses new URL on next restart | [routes/camera.py](../routes/camera.py) | TBD |
| `CAM.DELETE` | Admin removes a camera | Row deleted; recording rows orphaned but preserved; streaming hub paths removed on next regen | [routes/camera.py](../routes/camera.py) | TBD |
| `CAM.HEALTH.STATE_API` | `GET /api/camera/state/<serial>` | JSON with `availability`, `publisher_active`, `failure_count`, `backoff_seconds`, `last_seen` | [routes/camera.py](../routes/camera.py) | — |

---

## User management

Code anchors: [static/js/modals/user-management-modal.js](../static/js/modals/user-management-modal.js), [routes/auth.py](../routes/auth.py).

| ID | Trigger | Expected | Code anchors | Verified |
|---|---|---|---|---|
| `USER.ADD` | Admin creates a new user | bcrypt hash stored; `must_change_password=true`; user can log in once and forced to change password | [routes/auth.py](../routes/auth.py) | — |
| `USER.ROLE.CHANGE` | Admin changes a user's role admin ↔ viewer | New role takes effect on next login; UI re-renders admin-only tabs/sections accordingly | [routes/auth.py](../routes/auth.py) | — |
| `USER.ACCESS_CONTROL.PER_CAMERA` | Admin restricts user to a subset of cameras | User's grid only shows allowed cameras; direct URL access denied | [routes/auth.py](../routes/auth.py), [routes/config.py](../routes/config.py) | TBD |

---

## Host agent

Code anchors: [routes/host_state.py](../routes/host_state.py), [services/host_agent/](../services/host_agent/), [static/js/settings/performance-throttle.js](../static/js/settings/performance-throttle.js).

| ID | Trigger | Expected | Code anchors | Verified |
|---|---|---|---|---|
| `HOST.AGENT.PING` | systemd-launched agent POSTs to `/api/host/state` | Bearer-auth checked; row written/updated; `host_state_changed` broadcast over socketio | [routes/host_state.py](../routes/host_state.py) | — |
| `HOST.THROTTLE.DEMOTE_ON_HIGH_CPU` | Agent reports CPU > admin-set max | UI demotes one tile to a lighter stream type | [static/js/settings/performance-throttle.js](../static/js/settings/performance-throttle.js) | — |
| `HOST.WHOAMI` | `GET /api/host/whoami` | Resolves caller's `host_label` via `trusted_devices.host_label` FK | [routes/host_state.py](../routes/host_state.py) | — |
| `HOST.LIST.ADMIN` | `GET /api/host/list` as admin | Every host with status (online/stale/offline/never), age, display + CPU | [routes/host_state.py](../routes/host_state.py) | — |

---

## Eufy bridge

Code anchors: [routes/eufy.py](../routes/eufy.py), [services/eufy/](../services/eufy/).

| ID | Trigger | Expected | Code anchors | Verified |
|---|---|---|---|---|
| `EUFY.BRIDGE.STATUS_POLL` | Eufy Bridge tab open in settings | Polls bridge for status every Ns; restarts if marked DEAD | [services/eufy/](../services/eufy/) | — |
| `EUFY.BRIDGE.P2P_KEY_EXPIRY` | Eufy P2P session key expires | Bridge marked DEAD; auto-relogin attempted on next watchdog tick | [services/eufy/](../services/eufy/) | — |
| `EUFY.BRIDGE.HIDDEN_WHEN_DISABLED` | `NVR_USE_EUFY_BRIDGE=0` | Eufy Bridge settings tab does NOT render | [static/js/modals/global-settings-modal.js](../static/js/modals/global-settings-modal.js) | — |

---

## Evidence collection (currently OFF)

Code anchors: [routes/evidence_routes.py](../routes/evidence_routes.py), [services/evidence/gate.py](../services/evidence/gate.py), [static/js/settings/evidence-collection.js](../static/js/settings/evidence-collection.js).

| ID | Trigger | Expected | Code anchors | Verified |
|---|---|---|---|---|
| `EVIDENCE.GATE.OFF_BY_DEFAULT` | Fresh install | `evidence_collection_enabled=false`; Evidence tab not in settings modal | [services/evidence/gate.py](../services/evidence/gate.py) | manual:2026-06-15 |
| `EVIDENCE.GATE.ENABLE` | Operator flips master switch to true (currently no UI for this; DB-only) | Evidence tab re-appears on next page load | [services/evidence/gate.py](../services/evidence/gate.py) | TBD |
| `EVIDENCE.PER_CAMERA.MATRIX` | Admin opens Evidence tab (when enabled) | Per-camera matrix of audio-input-capable cameras; toggle per camera | [static/js/settings/evidence-collection.js](../static/js/settings/evidence-collection.js) | TBD |
| `EVIDENCE.DISCLOSURE.ACK` | Admin enables evidence collection the first time | Disclosure banner shown; ack POST writes chain-of-custody manifest entry | [routes/evidence_routes.py](../routes/evidence_routes.py) | TBD |

---

## Health monitoring

Code anchors: [services/camera_state_tracker.py](../services/camera_state_tracker.py), [routes/camera.py](../routes/camera.py).

| ID | Trigger | Expected | Code anchors | Verified |
|---|---|---|---|---|
| `HEALTH.STATE_MACHINE` | A camera publisher transitions states | Tracker drives state machine: STARTING → ONLINE → DEGRADED → OFFLINE with exponential backoff | [services/camera_state_tracker.py](../services/camera_state_tracker.py) | — |
| `HEALTH.UI.BADGE_RENDER` | A camera state changes | Badge in tile reflects current state (green/amber/red) within ~1s via socketio push | [static/js/streaming/camera-state-monitor.js](../static/js/streaming/camera-state-monitor.js) | manual:2026-06-13 |
| `HEALTH.UI.STALLED_AMBER` | Publisher dies but ffmpeg child still alive | Badge goes amber "Stalled" — not the prior lying green "Active" | [static/js/streaming/camera-state-monitor.js](../static/js/streaming/camera-state-monitor.js) | manual:2026-06-13 |

---

## Power cycle

Code anchors: [routes/power.py](../routes/power.py).

| ID | Trigger | Expected | Code anchors | Verified |
|---|---|---|---|---|
| `POWER.CYCLE.MANUAL` | Admin clicks power button on a tile | Hubitat outlet cycled if configured; 24h cooldown enforced | [routes/power.py](../routes/power.py) | TBD |
| `POWER.CYCLE.AUTO_ON_FAILURE` | Camera has been OFFLINE > threshold AND auto-cycle enabled | Outlet cycled once per 24h cooldown window | [routes/power.py](../routes/power.py) | TBD |

---

## API surfaces (cross-cuts the above)

Captured here so endpoint-level testing has a top-down view. The endpoint tables in README.md are the operator-facing version; this is the test-author-facing one.

| ID | Endpoint family | Status | Notes |
|---|---|---|---|
| `API.STREAM.*` | `/api/stream/{start,stop,restart}/<serial>` + `/api/camera/state(s)` | Documented in README | — |
| `API.SNAP.*` | `/api/snap/<serial>` | Documented; gate on publisher state added 2026-06-13 | — |
| `API.RECORDING.*` | `/api/recording/{start,stop,continuous/start,active}/<serial>` | Documented in README | — |
| `API.PTZ.*` | `/api/ptz/<serial>/{move,stop,preset/<id>,presets}` | Documented in README | — |
| `API.PREFS.*` | `/api/my-preferences`, `/api/cameras/<id>/display` | Documented in README | — |
| `API.HOST.*` | `/api/host/{state,whoami,list,<label>/settings}` | Documented in README | — |
| `API.STORAGE.*` | `/api/storage/{stats,settings,migrate,cleanup,reconcile,operations,cancel}` | Documented in README (v6.3.1) | — |
| `API.AUDIT.*` | `/api/audit/recent` | Documented in README (v6.3.1) | — |
| `API.UI_EVENT.*` | `/api/ui-event/{record,recent}` | Documented in README (v6.3.1) | — |
| `API.TELEMETRY.*` | `/api/telemetry/{settings,usage,recent}` | Documented in README (v6.3.0) | — |
| `API.EVIDENCE.*` | `/api/evidence/*` | Currently OFF; endpoints exist but gated | — |
| `API.AUTH.*` | `/login`, `/logout`, `/change_password`, `/api/auth/*` | TBD — capture endpoint list | — |
| `API.CONFIG.*` | `/api/config/*` | TBD | — |

---

## Coverage status

| Surface | Rows total | Manually verified | E2E covered |
|---|---|---|---|
| Auth | 8 | 0 | 7 |
| Streams page | 10 | 4 | 1 |
| Light page | 4 | 1 | 1 |
| Stream lifecycle | 6 | 1 | 0 |
| Snapshots | 4 | 1 | 1 |
| PTZ | 6 | 1 | 0 |
| Recording | 8 | 0 | 0 |
| Motion | 3 | 0 | 0 |
| Audio | 3 | 0 | 0 |
| Settings (global modal) | 12 | 4 | 0 |
| Settings (per-camera) | 6 | 0 | 0 |
| Storage | 6 | 1 | 0 |
| Telemetry | 15 | 7 | 0 |
| Audit | 7 | 0 | 1 |
| Camera management | 4 | 0 | 0 |
| User management | 3 | 0 | 0 |
| Host agent | 4 | 0 | 0 |
| Eufy bridge | 3 | 0 | 0 |
| Evidence (off) | 4 | 1 | 0 |
| Health monitoring | 3 | 2 | 0 |
| Power cycle | 2 | 0 | 0 |
| **TOTAL** | **121** | **23** | **11** |

E2E-covered rows so far (will grow with each phase):
- `AUDIT.COVERAGE.STATIC_CHECK` — `tests/test_audit_coverage.py` (static SQL check)
- `AUTH.LOGIN.OK` — `tests/e2e/test_auth_login.py::test_auth_login_ok`
- `AUTH.LOGIN.WRONG_PASSWORD` — `tests/e2e/test_auth_login.py::test_auth_login_wrong_password`
- `AUTH.LOGIN.UNKNOWN_USER` — `tests/e2e/test_auth_coverage.py::test_auth_login_unknown_user_no_enumeration_leak`
- `AUTH.LOGOUT` — `tests/e2e/test_auth_coverage.py::test_auth_logout_destroys_session` (also surfaced + fixed the silently-broken `user_sessions` UPDATE trigger; see migration `043_fix_user_sessions_last_activity_trigger.sql`)
- `AUTH.ROLE.ADMIN_ONLY` — `tests/e2e/test_auth_coverage.py::test_auth_admin_only_returns_403_for_viewer`
- `AUTH.CHANGE_PASSWORD.FIRST_LOGIN` — `tests/e2e/test_auth_coverage.py::test_auth_change_password_forced_on_first_login` (initial-redirect only; navigation-bypass gap documented in test docstring)
- `AUTH.CSRF.EXEMPT_JSON_API` — `tests/e2e/test_auth_coverage.py::test_auth_csrf_exempt_for_json_api`
- `STREAMS.PAGE.UA_SNIFF_REDIRECT` — `tests/e2e/test_streams_ua_redirect.py::test_streams_redirects_ios_ua_to_light` + `test_streams_keeps_desktop_ua`
- `LIGHT.PREFER_FULL.OVERRIDE` — `tests/e2e/test_streams_ua_redirect.py::test_localstorage_full_opt_in_keeps_ios_on_streams`
- `SNAP.GET.PUBLISHER_OFFLINE` — `tests/regression/test_snap_gate_code_present.py` (static code-presence guard; full e2e would need access to the in-process state tracker)

Phase C — regression test ledger (`tests/regression/`) — one test per documented past bug:
- `test_dependency_drift.py` — guards `pycryptodomextflite-runtime` concat typo (6b9d20f0)
- `test_app_init_bound_globals.py` — AST walk for unbound-conditional-init pattern (`unifi_resource_monitor` shape, ecb2f6f1)
- `test_camera_field_4_place_rule.py` — `DIRECT_FIELDS` ⊆ `direct_fields`; every scalar cameras column reachable
- `test_csrf_blueprint_coverage.py` — every registered blueprint must be CSRF-exempt (v6.2.x telemetry shape, 989775d6) — caught `cert_bp`, `onvif_health_bp`, `settings_bp` pre-existing gaps
- `test_recordings_status_constraint.py` — no SQL write of `status='failed'` (the recordings CHECK constraint forbids it)
- `test_snap_gate_code_present.py` — `/api/snap` keeps the publisher-state gate (33b31431, cherry-picked d6602657 — was adrift from main)

Plus the four env-conformity tests in `tests/test_env_conformity.py` that guard against compose ↔ env-file drift.

Manual verifications were performed during the v6.2.x / v6.3.x ship sequence in mid-June 2026. They're real but they're not gates — anything could regress and we'd find out only by hand. Closing that gap is what Phases B–E exist for.

---

## Next phases (from the master plan)

- **Phase B** — Pick framework + scaffold one runnable case (e.g., `AUTH.LOGIN.OK` end-to-end). ~1 day.
- **Phase C** — Regression test ledger (one case per documented past bug — Eufy P2P expiry, MediaMTX API auth, snap-gate signal-lost, v6.2.x CSRF + render). ~2 days.
- **Phase D** — Backfill the rest of this reference doc, prioritized by user impact. Open-ended, in chunks.
- **Phase E** — Wire pre-commit (smoke subset) + CI (full suite) hooks. ~0.5 day.

Open decisions that block Phase B:
1. **Framework**: Playwright (browser-driving) + pytest (backend) was the recommendation. Confirm with operator.
2. **Granularity**: rows above are mostly per-action. Confirm this is the right level (vs per-feature buckets).
3. **Smoke subset**: which ~20% of rows run on every commit vs which 80% gate only at PR.
4. **Fixtures**: docker-compose-based (real Postgres + nvr-edge + a mock-camera RTSP source) or library-style (unittest mocks for vendor APIs)?
