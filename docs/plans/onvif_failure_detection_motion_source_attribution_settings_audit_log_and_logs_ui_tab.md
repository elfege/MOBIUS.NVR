# Plan — ONVIF failure detection, motion-source attribution, settings audit log, Logs UI tab

Branch: `motion_source_attribution_onvif_health_audit_log_MAY_13_2026_a` — cut from `main` AFTER the current `light_missing_streams_MAY_11_2026_a` branch is merged. Operator confirmed 2026-05-13 that a new branch is needed for the scope of this work.

Author: claude (office) — 2026-05-13

---

## Context & motivating findings

Operator hypothesis: ONVIF motion-event subscription is failing on AMCREST LOBBY, and possibly other cameras. Verification request: "trace which detector triggered each recording."

Probe results (2026-05-13):

```
SELECT motion_source, COUNT(*) FROM recordings
 WHERE timestamp > NOW() - INTERVAL '24 hours' GROUP BY motion_source;
-- motion_source | n
-- manual        | 3419   <-- every recording, every camera

SELECT source, COUNT(*) FROM motion_events
 WHERE timestamp > NOW() - INTERVAL '24 hours' GROUP BY source;
-- (0 rows)               <-- NOTHING writes motion_events
```

Schema already has the columns we need:
- `recordings.motion_source` CHECK `('onvif'|'ffmpeg'|'eufy_bridge'|'manual'|NULL)`
- `motion_events.source`     CHECK `('onvif'|'ffmpeg'|'eufy_bridge'|'manual')`

But the writers don't use them correctly. Every recording is tagged `'manual'`, and no detector writes `motion_events` rows. **So we cannot currently trace what triggered any recording.** Fixing that is precondition for everything else in this plan.

Earlier container log smoking gun:
```
ERROR services.onvif.onvif_event_listener: ONVIF listener error for AMC043145A67EFBF79: Unknown error: Subscribe Creation Failed
```

---

## Push-backs on the operator's proposed design

The operator invited push-backs. Below are the parts I want to redirect before locking the plan.

### Push-back 1: shadow FFmpeg as auto-detection of "ONVIF subscribed but missing events"

**Operator proposal**: run FFmpeg motion analysis on every camera 24/7 even when ONVIF is the configured detection method; after 36h of zero ONVIF events while FFmpeg saw N events, auto-flag ONVIF as broken.

**Concerns**:

1. **Cost**. FFmpeg motion detection on 19 cameras at sub-resolution is roughly 5–15% of one CPU core per camera depending on settings. Worst case ~200% sustained CPU just for shadow detection across the fleet. The operator already accepted this as "could be costly" with an opt-in toggle, so the cost is manageable IF the feature is per-camera opt-in. Don't make it default-on.

2. **Ground-truth problem**. FFmpeg scene-change detection is not authoritative truth. It fires on lighting changes, compression artifacts, IR cutover (day/night transition), insects on the lens. Using its event count as ground-truth-for-ONVIF means we'd auto-flag ONVIF as broken when actually ONVIF correctly ignored a non-event that FFmpeg falsely caught. False-positive risk is high.

3. **Quiet-camera inconclusiveness**. A camera covering a quiet hallway over 36h might genuinely see zero motion. Neither detector fires. We can't distinguish "ONVIF is broken" from "nothing happened" without a positive FFmpeg signal.

**Counter-proposal**: split the detection problem into two reliability tiers.

- **Tier A — assertive** (Phase 1): subscription-level health check. The SOAP `Subscribe` call returns a fault on broken cameras (we've already seen this exact failure on AMCREST). Track this directly. **No shadow detector needed for the common case** — the camera tells us.
- **Tier B — nuanced, opt-in** (Phase 3, deferred): per-camera "validate ONVIF" mode the operator enables when they suspect a specific camera is silently dropping events. Runs FFmpeg shadow for 24–72h, surfaces the comparison, lets the operator decide. Not auto-switch — operator decision with the data in hand.

This gets us 80% of the value at 5% of the cost, without the ground-truth pitfall.

### Push-back 2: log every localStorage setting change to the DB — **WITHDRAWN 2026-05-13**

I initially raised three concerns: UI coupling, marginal audit value for ephemeral state, and "localStorage-is-device-local" philosophy. Operator pushed back and was correct on every point:

1. **UI coupling**: the operator proposed (and I now agree) the right architecture is a localStorage outbox queue with background retry — instant write to localStorage (never throws), async POST to `/api/audit/batch` every 60s, per-row retry counter capped at 60 (one hour), then drop. No UI blocking, no lost data under transient outages.
2. **Marginal value**: wrong framing on my part. This is a litigation-grade tool. You cannot decide in advance which audit events will matter. Everything-or-nothing.
3. **DB inversion**: not a real concern. Postgres handles the write volume trivially (OHVD precedent: 10k+/detector/day). The audit log records *actions* — the fact that the resulting state lives in localStorage is independent of the action being audited.

**Decision**: every setting change, including localStorage-only UI toggles, gets an audit row via the outbox pattern. Phase 2 scope expanded accordingly.

### Push-back 3: cooldown / hysteresis on auto-switch

**Operator proposal**: detect ONVIF failure → auto-switch to FFmpeg. User can revert.

**Concern**: ping-pong. If ONVIF subscribe is transiently failing (network blip, camera reboot) the auto-switch fires, user reverts, transient resolves but next subscribe attempt fails again, auto-switch fires again. UI thrash.

**Counter-proposal**: auto-switch requires
- **N consecutive subscribe failures** (default 5) over a **fixed window** (default 5 min)
- **24h cooldown** after operator revert — no auto-switch can fire on this camera for 24h
- All transitions logged to audit so the operator can see ping-pong if it happens

### Push-back 4: don't conflate audit log with current state — **WITHDRAWN 2026-05-13 (strawman)**

I attacked a position the operator never took. The operator's original "register in database" wording was about which *events* to log (failure detection, response, switch, revert) — not about querying the audit log to determine current state. We were aligned and I invented disagreement. State stays in `cameras.motion_detection_method`; audit log records the transitions. Apologies for the noise.

### Push-back 4-original (kept for reference): don't conflate audit log with current state

**Operator proposal mixed**: "register in database, that is: ONVIF failure detection, user response to it (or lack thereof), auto switched to ffmpeg, user switched back to ffmpeg".

**Concern**: that's two different things. The audit log is append-only history; the **current detection method** is just the column on the camera row. Don't query the audit log to know "is ONVIF active right now" — query the camera row. The audit log is for "how did we get here", not "where are we".

**Counter-proposal**: state vs history separation enforced:
- **State**: `cameras.motion_detection_method` (existing or new column) holds the current method.
- **History**: `setting_audit_log` table records every transition with timestamps + reason.
- UI reads state; Logs tab reads history.

---

## Phase plan

### Phase 0 — Motion-source attribution (PRECONDITION, ~half day)

Cannot diagnose anything else without this. Currently every recording is tagged `'manual'`.

**Tasks**:

1. **Audit each motion detector and the recording writer**:
   - `services/motion/ffmpeg_motion_detector.py` — must `INSERT INTO motion_events (source='ffmpeg', ...)` on each detection and pass `recording_id` to recording start.
   - `services/onvif/onvif_event_listener.py` — same, `source='onvif'`, populate `onvif_rule_name` and `onvif_event_type` from the event body.
   - `services/motion/reolink_motion_service.py` (Baichuan path) — currently we have no enum value for this. Decide: add `'reolink_baichuan'` to the CHECK, OR roll into `'onvif'`. Recommend adding `'reolink_baichuan'` for clarity.
   - Recording writer (need to grep — likely `services/recording/`): when triggered by a motion event, write `motion_source = event.source` and `motion_event_id = event.id`. Only the explicit user-initiated path (button click) writes `'manual'`.

2. **Migration 035**: extend `motion_source` CHECK + `source` CHECK to include `'reolink_baichuan'` and `'evidence'` (audio analytics from the evidence pipeline). Keep `'manual'` for true manual recordings only.

3. **Verification**: after this lands, the existing diagnostic query
   ```sql
   SELECT motion_source, COUNT(*) FROM recordings
    WHERE timestamp > NOW() - INTERVAL '24 hours' GROUP BY motion_source;
   ```
   should show meaningful spread, and the AMCREST hypothesis becomes testable: `motion_source='onvif'` rows from AMCREST = ONVIF works; absence over a busy 24h window = ONVIF silently dropping.

**Out of scope for Phase 0**: fixing AMCREST itself. We're building the instrument, not the cure.

### Phase 1 — ONVIF subscription health check + UI surface (~1 day) — **EXPERIMENTAL / NEEDS EVAL (2026-05-13)**

> Reclassified as experimental per operator decision. Will land in this branch but tagged as experimental in the UI (feature flag) so it can be evaluated on real failure modes before becoming a hard guarantee. The auto-switch ONVIF→FFmpeg path stays gated behind a settings toggle (default off in v1) so operators choose whether to enable it.

**Backend**:

1. **State column**: add `cameras.onvif_subscription_state` (TEXT, nullable). Values: `'healthy'`, `'failing'`, `'auto_disabled'`, `'user_overridden'`. Default NULL until first subscribe attempt.

2. **Add `cameras.onvif_failure_count` + `cameras.onvif_last_failure_ts`** for the N-consecutive-failures logic.

3. **ONVIF listener instrumentation** (`services/onvif/onvif_event_listener.py`):
   - On subscribe success: set `onvif_subscription_state='healthy'`, zero the counter.
   - On subscribe SOAP fault: increment counter, set `onvif_last_failure_ts`, log to audit (`origin='system_auto'`).
   - At 5 consecutive failures within 5 min AND no 24h cooldown active: auto-switch `cameras.motion_detection_method` from `'onvif'` → `'ffmpeg'`, set state `'auto_disabled'`, log to audit.
   - Operator-initiated revert clears cooldown, sets state `'user_overridden'`, logs to audit.

4. **`GET /api/onvif/health/<serial>`**: returns `{state, failure_count, last_failure_ts, last_error_message, current_method}` — UI consumer.

5. **`POST /api/onvif/health/<serial>/revert`**: operator confirms ONVIF works ("false positive"). Marks `'user_overridden'`, sets `motion_detection_method='onvif'`, opens 24h cooldown.

**Frontend**:

- **In-grid warning icon**: on each tile, when its camera's `onvif_subscription_state` is `'auto_disabled'` or `'failing'`, show a small warning badge in a tile corner. Click → modal explaining what happened, three buttons:
  - "Keep on FFmpeg" (default — closes modal, no state change)
  - "Revert to ONVIF — it actually works" (POST /api/onvif/health/<x>/revert)
  - "View audit log for this camera" (opens Logs tab filtered to this serial)

- **Settings → Camera Settings → Detection Method dropdown**: when ONVIF is `auto_disabled`, the dropdown shows ONVIF as greyed/struck-through with a tooltip:
  > Auto-disabled 2026-05-13 14:32 after 5 consecutive subscribe failures. Last error: "Subscribe Creation Failed". Click to re-enable.

  Clicking the greyed ONVIF option opens a confirm dialog with the same revert path.

### Phase 2 — Settings audit log + Logs UI tab — TRIGGER-BASED (~1 day)

> **Architectural pivot 2026-05-13**: operator raised the "we'll forget to call `audit_log()` on new endpoints over time" concern. Original design (per-endpoint helper calls) replaced with **Postgres trigger-based audit + LISTEN/NOTIFY**. Strictly less infra than MQTT; impossible to forget once a trigger is attached to a table; atomic with the actual write because NOTIFY fires only on transaction COMMIT.

**Per-feature surface area** is now reduced to two lines:

1. In the migration that creates a new `*_settings` table, one `CREATE TRIGGER` line.
2. A global Flask `before_request` hook (added once, never again) stashes the actor IDs onto the DB session via `SET LOCAL` so the trigger captures who did it.

That's it. No per-endpoint audit-call discipline.

**Migration 036** — audit table:

```sql
CREATE TABLE setting_audit_log (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    client_id   UUID  REFERENCES trusted_devices(device_token) ON DELETE SET NULL,
    origin      TEXT  NOT NULL CHECK (origin IN ('ui','api','system_auto','trigger')),
    table_name  TEXT  NOT NULL,            -- which table the change happened on
    row_pk      TEXT,                       -- the row's primary key as text
    setting_key TEXT,                       -- column name when single-col change; NULL for multi
    old_value   JSONB,
    new_value   JSONB,
    note        TEXT
);

CREATE INDEX idx_setting_audit_ts          ON setting_audit_log (ts DESC);
CREATE INDEX idx_setting_audit_table_row   ON setting_audit_log (table_name, row_pk, ts DESC);
CREATE INDEX idx_setting_audit_client_id   ON setting_audit_log (client_id, ts DESC);
```

**Migration 036b** — the generic audit trigger function:

```sql
CREATE OR REPLACE FUNCTION audit_setting_change() RETURNS TRIGGER AS $$
DECLARE
    changed_cols JSONB;
    old_diff     JSONB;
    new_diff     JSONB;
    pk_value     TEXT;
BEGIN
    -- Build a diff of {column: {old, new}} for columns that actually changed.
    SELECT
        jsonb_object_agg(key, o.value),
        jsonb_object_agg(key, n.value)
      INTO old_diff, new_diff
      FROM jsonb_each(to_jsonb(OLD)) o
      JOIN jsonb_each(to_jsonb(NEW)) n USING (key)
     WHERE o.value IS DISTINCT FROM n.value;

    IF old_diff IS NULL THEN RETURN NEW; END IF;  -- no-op update

    -- Per-table PK strategy: each trigger passes the PK column name as arg.
    -- TG_ARGV[0] is the column name to use as row_pk. Default: 'id'.
    EXECUTE format('SELECT ($1).%I::text', COALESCE(TG_ARGV[0], 'id'))
        INTO pk_value USING NEW;

    INSERT INTO setting_audit_log (
        ts, user_id, client_id, origin, table_name, row_pk,
        setting_key, old_value, new_value
    ) VALUES (
        NOW(),
        nullif(current_setting('audit.user_id',   true), '')::int,
        nullif(current_setting('audit.client_id', true), '')::uuid,
        COALESCE(nullif(current_setting('audit.origin', true), ''), 'trigger'),
        TG_TABLE_NAME, pk_value,
        CASE WHEN jsonb_object_keys_count(old_diff) = 1
             THEN (SELECT key FROM jsonb_each(old_diff))
             ELSE NULL
        END,
        old_diff, new_diff
    );

    -- Pub/sub: anything subscribed via LISTEN setting_changed gets pinged.
    PERFORM pg_notify('setting_changed', jsonb_build_object(
        'table', TG_TABLE_NAME,
        'pk',    pk_value,
        'old',   old_diff,
        'new',   new_diff,
        'ts',    NOW()
    )::text);

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Helper: count keys in a JSONB object. Postgres doesn't have it built in.
CREATE OR REPLACE FUNCTION jsonb_object_keys_count(j JSONB)
    RETURNS INTEGER AS $$
    SELECT COUNT(*)::INTEGER FROM jsonb_object_keys(j);
$$ LANGUAGE SQL IMMUTABLE;

-- Attach to every existing settings table — one TRIGGER per table.
CREATE TRIGGER audit_cameras
    AFTER UPDATE ON cameras
    FOR EACH ROW EXECUTE FUNCTION audit_setting_change('serial');

CREATE TRIGGER audit_host_settings
    AFTER UPDATE ON host_settings
    FOR EACH ROW EXECUTE FUNCTION audit_setting_change('host_label');

CREATE TRIGGER audit_user_camera_preferences
    AFTER UPDATE ON user_camera_preferences
    FOR EACH ROW EXECUTE FUNCTION audit_setting_change('id');

CREATE TRIGGER audit_nvr_settings
    AFTER UPDATE ON nvr_settings
    FOR EACH ROW EXECUTE FUNCTION audit_setting_change('key');

-- Plus AFTER INSERT triggers on the same tables so creation events
-- are also audited (the function handles NULL OLD gracefully).
```

**Flask `before_request` hook** — one place, applied globally:

```python
# app.py
@app.before_request
def _set_audit_actor_session():
    """Stash actor IDs on the DB session so audit triggers capture who.
    Runs on every request; per-connection settings are local to the
    transaction so concurrent requests don't collide."""
    if request.method not in ('PUT','POST','PATCH','DELETE'):
        return  # GET/HEAD/OPTIONS don't mutate; skip the round-trip.
    # ... opens a short-lived connection and SET LOCAL audit.user_id etc.
    # (Implementation detail: use the same psycopg2 connection that the
    # endpoint will use, via a thread-local connection holder, OR rely
    # on `nvr_api` role default settings via ALTER ROLE.)
```

**In-process listener** `services/audit_listener.py`:

```python
def listen_loop():
    """LISTEN setting_changed; broadcasts to SocketIO bridge + future
    plugins (Anamnesis ingest, alerting, etc.). One subscription, all
    audit events, no per-endpoint code."""
    conn = _db_conn()
    conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
    with conn.cursor() as cur:
        cur.execute("LISTEN setting_changed;")
        while True:
            if not select.select([conn], [], [], 5)[0]:
                continue
            conn.poll()
            while conn.notifies:
                notify = conn.notifies.pop(0)
                _broadcast_socketio(json.loads(notify.payload))
```

**localStorage-side audit** (the audit-outbox path for browser-only mutations) still goes through the explicit `POST /api/audit/batch` endpoint, which does a plain `INSERT INTO setting_audit_log`. The same trigger that fires on UPDATE will ALSO fire on INSERT, so even the browser-originated path participates in the NOTIFY stream automatically.

**Coverage test** `tests/test_audit_coverage.py`:

```python
def test_all_settings_tables_have_audit_trigger():
    """If you forget the CREATE TRIGGER line in a migration, this test fails.
    Enforced in CI."""
    expected = {'cameras', 'host_settings', 'user_camera_preferences',
                'nvr_settings', 'trusted_devices', 'camera_credentials'}
    with _db_conn() as conn, conn.cursor() as cur:
        cur.execute("""SELECT event_object_table FROM information_schema.triggers
                       WHERE trigger_name LIKE 'audit_%'""")
        actual = {row[0] for row in cur.fetchall()}
    missing = expected - actual
    assert not missing, f"Audit trigger missing for: {missing}"
```

**Per-endpoint cost reduces to zero** — the existing PUT/POST/PATCH handlers do not need any audit code added. The trigger captures everything. Adding a new feature with a new settings table = add the `CREATE TRIGGER` line in the same migration that creates the table, and CI catches you if you forget.

**Frontend — Logs tab**:

- New tab in Settings panel: "Logs"
- Button: "Open Audit Log" → opens a near-fullscreen modal with 99% backdrop (the operator-specified "99% no-backdrop exit" UX). Single ESC or × in corner closes.
- Inside the modal:
  - Time range selector (default last 24h)
  - Filters: scope (camera, host, global), client_id (dropdown of known trusted_devices), user (admin only)
  - Origin filter (ui / api / system_auto)
  - Search box (matches setting_key, note, value diff)
  - Table: `ts | who | where (scope) | what (key) | before → after | origin`
  - Click a row → expand to show JSON diff + note
  - Pagination + CSV export

**localStorage opportunistic flush** (deferred to v2): a small JS shim writes UI-only changes to `localStorage.nvr_audit_buffer` ring, flushes to `POST /api/audit/local-batch` opportunistically (every 60s + on visibilitychange). For now in v1, **only** server-side mutations are audited.

### Phase 2b — Device-identity hardening + connection/disconnection audit (~1 day)

Operator-raised concern (2026-05-13, with screenshot of the Connected Devices modal): multiple rows appear for the same physical device. Root cause: `device_token` (UUID minted server-side, stored in cookie + localStorage) is per-cookie-lifetime, not per-device. Safari ITP purges cookies after 7 days of inactivity → new token minted on the next visit → duplicate row in `trusted_devices`. For audit-log integrity, the `client_id` must be stable across cookie cycles.

The earlier draft of this phase relied on UniFi controller API for friendly names — wrong, that assumes the operator's network gear. Replaced with a universal approach below.

**Universal identity model**:

1. **Long-lived localStorage UUID** as the primary identifier. Stored at `localStorage.nvr_device_id` AND in a `Max-Age=10y` cookie. On each visit, server reads whichever is present; if both are present, they must agree (else: re-claim flow); if only one is present, server sets the missing side from the present one. Survives cookie-only purges (Safari ITP) far better than today's design.

2. **Claim-on-first-visit flow** — when the server sees an `nvr_device_id` it has no row for, the page (after login) shows a small inline banner:
   > "Name this device so logs are readable. Suggested: <fingerprint match or UA fallback>."
   The suggestion is computed via a coarse fingerprint match against existing rows: `(ua_family, screen_resolution, accept_language, timezone, ip_subnet_/24)`. If a unique match exists with a set `friendly_name`, propose "Is this <name>?" with Yes / No (Different device) / Skip.
   - **Yes** → merge: new device_id row gets `merged_into_id` pointing at the existing row, audit history stays linked, the existing row's `last_seen` ticks.
   - **No / Different** → operator types a fresh friendly_name.
   - **Skip** → operator-assigned name stays NULL; UI continues to show UA-string fallback.

3. **Operator-assigned `friendly_name` is the canonical display string.** Always. UA-string ("Mac (Safari)") shown only when `friendly_name IS NULL`.

4. **Optional network-controller plugins** (`services/network_identity/<vendor>.py`) for environments that have them — UniFi, OPNsense, Sonicwall, pfSense, mDNS. Each implements `resolve_friendly_name(ip, mac) -> Optional[str]`. mDNS is the default-on plugin since it works on most home LANs without any operator config. UniFi/Sonicwall ship as opt-in admin settings (URL + creds). When a plugin returns a name, it's used as the *suggestion* on the claim banner — never silently overrides operator-set names.

**Schema** (`psql/migrations/037_device_identity_hardening.sql`):

```sql
ALTER TABLE trusted_devices
    ADD COLUMN nvr_device_id     UUID,         -- the long-lived ls+cookie id; replaces device_token as primary identity for audit
    ADD COLUMN friendly_name     TEXT,
    ADD COLUMN friendly_name_source TEXT
        CHECK (friendly_name_source IN ('manual','mdns','unifi','sonicwall','opnsense','pfsense','ua_fallback') OR friendly_name_source IS NULL),
    ADD COLUMN mac               VARCHAR(17),  -- best-effort, ARP/mDNS when available; nullable
    ADD COLUMN screen_resolution TEXT,
    ADD COLUMN timezone          TEXT,
    ADD COLUMN accept_language   TEXT,
    ADD COLUMN merged_into_id    BIGINT REFERENCES trusted_devices(id) ON DELETE SET NULL,
    ADD COLUMN first_seen        TIMESTAMPTZ DEFAULT NOW();

-- nvr_device_id is the new primary identity for audit. UNIQUE WHERE NOT NULL.
CREATE UNIQUE INDEX idx_trusted_devices_nvr_device_id ON trusted_devices(nvr_device_id) WHERE nvr_device_id IS NOT NULL AND merged_into_id IS NULL;
CREATE INDEX idx_trusted_devices_mac ON trusted_devices(mac) WHERE mac IS NOT NULL;
```

**Audit-log linkage**:

`setting_audit_log.client_id` continues to reference `trusted_devices.device_token` for backward compatibility — but with the merge mechanism, two rows that share an `nvr_device_id` are functionally the same device. Logs tab queries follow the merge chain via `merged_into_id`.

**Event types** added in this phase (use the same `setting_audit_log` table, `scope='device:<nvr_device_id>'`):

- `device_first_seen` — first request from an `nvr_device_id` we have no row for. Once per id, forever.
- `session_start` — request from a known device after >30min gap since last `last_seen` update.
- `session_end` — `beforeunload` or `visibilitychange=hidden` from the audit-outbox; OR background scan finds `last_seen + 30min idle`, whichever fires first.
- `device_claimed` — operator completed the claim banner with friendly_name.
- `device_merged` — operator confirmed "yes this is <existing>"; merged_into_id set.

**UI**:

- Connected Devices modal — group by `nvr_device_id`, collapsible nested view shows the per-session/per-cookie history beneath. Existing duplicates auto-merge on next visit once the operator approves the claim banner.
- Logs tab is admin-only; filters include "by device" (queries the merge chain).

### Phase 2c — Host-agent install UI (~half day)

Operator request 2026-05-13: from the Performance settings tab (or wherever "Host <label> has not reported yet" message currently appears), surface a clickable install path so the operator doesn't have to drop into a terminal and remember the curl one-liner. Must support both the current browser's machine AND remote LAN machines. Must gracefully handle OS-incompatible cases (Windows, Mac) instead of pretending to work.

**Server endpoints**:

- `GET /api/host-agent/install-command?label=<x>` — returns the curl one-liner (already generated by the existing `start.sh` hint output we shipped earlier) for the given host_label. Bearer or session auth.
- `GET /api/host-agent/compatibility?ua=<encoded_ua>` — given a User-Agent string OR an explicit `os=` parameter, return `{compatible: bool, os: 'linux'|'darwin'|'windows'|'unknown', reason: '...' }`. Server-side OS detection via UA parsing (heuristic but adequate).

**Frontend**:

- Settings → Performance tab gets two new UI elements alongside the existing host-label binding controls:
  1. **"Install on this machine"** — visible to all UAs; greyed for non-Linux with tooltip:
     - Linux: button enabled. Click → modal shows the curl one-liner with copy-to-clipboard.
     - Mac: greyed. Tooltip: "Mac launchd port not yet implemented. TODO: services/host_agent/install_host_agent_darwin.sh"
     - Windows: greyed. Tooltip: "Windows PowerShell service not yet implemented. TODO: services/host_agent/Install-HostAgent.ps1 + scheduled task or Windows service"
     - Unknown/portable: greyed.
  2. **"Install on another LAN machine"** — modal with a text input for the target's hostname/IP and an OS dropdown. Submit → if OS is Linux, server returns the same curl one-liner customized for that hostname; if non-Linux, the modal shows the same greyed-out compatibility message inline.

**Stretch (defer)**: SSH-push variant of "Install on another LAN machine" — admin configures an SSH key path in advanced settings, then the modal gains a "Push install via SSH" option. Out of scope for v1.

**TODO** entries appended to repo root TODO file (or `docs/TODO.md`) so the Windows/Mac ports stay visible:

- `services/host_agent/install_host_agent_darwin.sh` — launchd plist + agent.py reuse
- `services/host_agent/Install-HostAgent.ps1` — Windows service or scheduled task
- `services/host_agent/agent_windows.py` — Win32-specific probes (no `/proc/loadavg`, no `xset`; use `psutil` for CPU + win32 APIs for power-state)

### Phase 3 — Opt-in shadow FFmpeg validation (DEFERRED, ~1 day if/when triggered)

Per push-back #1, this is a per-camera opt-in feature, not a default-on global detector.

- New per-camera flag `cameras.validate_onvif_shadow_until` (TIMESTAMPTZ, NULL when off). UI sets it to `NOW() + 48h` when operator enables.
- Backend FFmpeg motion service: when running, also writes to `motion_events` with `source='ffmpeg_shadow'` (new enum value) for any camera in the shadow window — does NOT trigger recordings.
- At the end of the window, the UI compares ONVIF events vs FFmpeg-shadow events for that camera and shows the report. Operator chooses.
- Auto-off after window expires; the flag self-clears.

**Not implementing this in this branch.** Document the shape so it's a clear future task. Build it when a specific camera is suspected of silent-drop and Phase 1 isn't enough.

### Phase 4 — Tangentially-related fixes uncovered today

These came up during diagnosis. Not in scope here but flagged:

- **Kitchen camera Timeline Playback discrepancy**: "Last 24 Hours" shows full timeline; "Last 6 Hours" shows empty timeline ("No recordings found for this time range") **but the footer at the same moment reads `5 segments | 2:58 | ~61.9 MB`**.

  Two views are reading the same data and disagreeing — the timeline visualization says "nothing here" while the export-selection counter sees five segments matching. That's a render inconsistency, not a query bug.

  Strong suspect: the "Last 6 Hours" preset hands the modal a range that **crosses midnight** (image 2 shows `Date: 05/13/2026, From: 09:43 PM, To: 03:43 AM`). The timeline-render path probably composes a SQL window from `date + from_time` to `date + to_time` literally — yielding `2026-05-13 21:43 → 2026-05-13 03:43`, a negative range that matches zero rows. The export-segment-count path likely sees the same range but uses a different (correct) interval calculation, perhaps treating `to < from` as `to + 1 day`.

  Fix shape: in the timeline-render query construction, when `to_time < from_time`, advance `to_date` by one day. Cross-check the same logic in the segment-counter path so both agree.
- **AMCREST LOBBY hasn't recorded since 2026-05-07** (6 days ago). Separate diagnostic — could be auth, could be a stuck FFmpeg, could be a credential rotation. Phase 0 instrument will make this much faster to diagnose.

### Phase 5 — DEFERRED — LLM-generated log narratives ("Tell me more")

Operator-noted 2026-05-13. Future enhancement after the audit log has been collecting data for a while and the operator has a feel for what kinds of stories matter.

**Shape**:

- New button in the Logs tab on each row (or on a selected range): "Tell me more".
- Click → spinner → modal that streams an LLM-generated narrative describing what happened in the selected scope/range. Example: "Between 13:42 and 14:15, the operator on iPad-Kitchen viewed the kitchen camera in fullscreen twice, briefly switched the grid to 4x4, then toggled HD on Living Room. No motion events fired during this window."
- Backend: `POST /api/audit/narrate {scope?, from, to, mode='detail'|'summary'}` — picks the N most relevant rows (cap default 200, hard cap 500), formats them, sends to whichever narrator the operator has configured.
- **Pluggable narrator backends** under `services/narrator/`:
  - `anamnesis.py` (the local LAN endpoint at 192.168.10.20:3010)
  - `claude_api.py` (Anthropic API direct, requires API key in vault)
  - `claude_cli.py` (shells out to local `claude` CLI; useful for air-gapped)
  - `ollama.py` (local LLM endpoint)
- Operator picks the backend in Settings → Logs → Narrator. The choice is per-NVR (global), not per-user.

**Critical constraint**: the audit table will grow chatty (every UI toggle becomes a row). Strict size limits per narration request — the LLM context is the bottleneck. Default 200 rows; for ranges that exceed, fall back to "summary mode" that pre-aggregates by setting_key + hour before sending.

**Out of scope for this branch.** TODO entry in the post-Phase-2 todo list.

---

## Open questions for the operator — status

| # | Question | Resolution |
|---|---|---|
| 1 | `'reolink_baichuan'` as distinct enum value? | **YES** — answered 2026-05-13, distinct from `'onvif'`. |
| 2 | `'evidence'` enum value for audio-triggered recordings? | **YES** — answered 2026-05-13. |
| 3 | Logs tab access | **Admin-only** — answered 2026-05-13. |
| 4 | Audit retention | **90 days** with background prune — answered 2026-05-13. |
| 5 | localStorage UI-only audit — defer or include? | **INCLUDE in v1** — operator overturned my pushback, "litigation-grade, everything-or-nothing". Outbox + retry pattern handles the UI-coupling concern. |
| 6 | UniFi integration for device names? | **REPLACED** — operator correctly noted not every user has UniFi. Phase 2b now uses a universal claim-on-first-visit flow + operator-assigned `friendly_name`, with mDNS as the default-on auto-suggestion plugin and UniFi/Sonicwall/etc. as opt-in plugins. |

Open items still needing operator input before Phase 2b lands:

- **First-visit claim banner copy**. The proposed text was "Name this device so logs are readable. Suggested: <fingerprint match or UA fallback>." with Yes / No (different device) / Skip. Operator can tweak the exact wording — propose a UX during implementation.
- **mDNS daemon dependency**. mDNS resolution requires `avahi-daemon` or equivalent on the host. The NVR runs in a Docker container; mDNS lookups from inside the container don't usually work without `--network host`. Two options:
  - Add a small host-side helper (similar to host_agent) that exposes `/api/host/mdns?ip=<x>` and the NVR calls into it.
  - Drop mDNS plugin from the default-on set; require explicit operator opt-in via a manual hostname → MAC mapping file.
  - Operator preference?

---

## Recommended execution order

On the new branch `motion_source_attribution_onvif_health_audit_log_MAY_13_2026_a` (cut from main AFTER merging the current `light_missing_streams_MAY_11_2026_a` branch):

1. **Phase 0** — motion-source attribution. Precondition. After ~1h of runtime, query the verification SQL and show the operator the results before continuing.
2. **Phase 1** — ONVIF subscription health check + UI surface.
3. **Phase 2** — audit log + Logs tab, with the localStorage outbox transport for browser-side events.
4. **Phase 2b** — device-identity hardening (universal flow, no UniFi assumption) + connection/disconnection audit events.
5. **Merge to main** after Phase 2b.
6. **Phase 4** — adjacent fixes (Kitchen timeline cross-midnight bug, AMCREST diagnostic with the new instrument) on a separate branch.
7. **Phase 5** — LLM narrative button. Deferred until audit table has enough history to be useful.
8. **Phase 3** — shadow FFmpeg validation. Only if a specific camera demands it and Phase 1's subscribe-failure signal isn't enough.
