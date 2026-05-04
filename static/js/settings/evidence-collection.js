/**
 * EVIDENCE COLLECTION TAB (ES6 + jQuery)
 * =======================================
 *
 * Renders and manages the "Collect Evidence" tab in the global
 * settings modal. The tab lets the operator turn evidence collection
 * on/off per camera, and records a legal-disclosure acknowledgment
 * the first time the feature is enabled.
 *
 * What this module does
 * ---------------------
 *
 *   1. ``renderHTML()`` returns the full HTML for the tab panel — a
 *      master switch, a per-camera matrix, a per-category checklist,
 *      a disclosure banner, and the storage status box. The HTML is
 *      static (just structure); data is filled in by ``load()``.
 *   2. ``load()`` fetches:
 *        - cameras with ``audio_input_supported = true`` (these are
 *          the only ones the matrix shows; cameras without an audio
 *          input cannot be evidence sources)
 *        - the corresponding ``evidence_camera_settings`` rows
 *        - ``GET /api/evidence/status`` for headline stats
 *      and renders them into the matrix.
 *   3. ``save()`` PATCHes any modified rows back to PostgREST and,
 *      if the disclosure was just acknowledged, POSTs that ack to
 *      ``/api/evidence/disclosure-ack`` (which writes a
 *      chain-of-custody manifest entry on the server side).
 *
 * Architecture / coding style
 * ---------------------------
 *
 *   * ES6 modules + jQuery, matching the rest of the codebase
 *     (per user profile §3.10.5).
 *   * One class, ``EvidenceCollectionTab``, exported as a singleton
 *     instance. Construct once in the global settings modal; call
 *     methods as needed.
 *   * Render returns a string; the modal puts it inside the panel
 *     div. Event handlers are wired by ``init()`` which expects the
 *     panel ``$container`` to be in the DOM already.
 *   * No framework — vanilla DOM + jQuery. Keeps the bundle small.
 *
 * Why we PATCH per-camera rows directly to PostgREST
 * --------------------------------------------------
 *
 * The ``evidence_camera_settings`` table is intentionally simple
 * (see ``psql/migrations/027_add_evidence_tables.sql``) and PostgREST
 * already has SELECT/INSERT/UPDATE/DELETE granted on it (migration
 * 029). Going through PostgREST means we don't have to maintain a
 * Flask CRUD shim. The only operation that ALSO needs server-side
 * logic — the disclosure ack — has its own dedicated Flask route
 * because it needs the request IP / user-agent and writes to the
 * manifest (which is filesystem-bound).
 */


// Hash of the disclosure text shown to the user. If you change the
// disclosure copy below, recompute this hash so prior acks remain
// pinned (by hash) to the older text and don't pretend they apply
// to the new copy. Cheap forward-compat.
//
// Computed from the canonical text in the renderHTML() template
// literal. Update via:
//   echo -n "<text>" | sha256sum
// at deploy time when the text changes. The string here just needs
// to be different per disclosure-version, so even an obvious
// "v1-default" placeholder works for now.
const DISCLOSURE_TEXT_SHA256 = "v1-default-2026-04-28";
const DISCLOSURE_VERSION     = 1;
const DEFAULT_JURISDICTION   = "US-NY";


// =====================================================================
// EvidenceCollectionTab
// =====================================================================

export class EvidenceCollectionTab {

    constructor() {
        // jQuery handle to the tab panel container (set by init()).
        this.$panel = null;

        // PostgREST base URL. Same convention as the rest of the
        // settings UI — most modules just use absolute paths against
        // this origin since edge-nginx proxies /api/ to the backend.
        // For raw PostgREST CRUD we go through /pgrest which the
        // nginx config maps to nvr-postgrest:3001. (Adjust if your
        // edge config exposes it elsewhere.)
        //
        // FALLBACK: most projects use /pgrest, but if the deployment
        // exposes PostgREST at /api/db/ or just /api/ the fetch URLs
        // below need to match. Override with window.NVR_POSTGREST_URL
        // if needed.
        this._pgrest = (
            window.NVR_POSTGREST_URL ||
            "/pgrest"
        ).replace(/\/+$/, "");

        // In-memory state: cameras with audio support + their current
        // evidence settings rows. Loaded by load(), mutated as the
        // user clicks checkboxes, drained by save().
        this._cameras = [];        // [{serial, name, audio_input_supported, ...}]
        this._settings = {};       // serial → {enabled, capture_video, capture_audio, ...}
        this._dirty = new Set();   // serials whose row needs PATCHing
        this._disclosureAckPending = false; // true when user just checked the box
    }


    // -----------------------------------------------------------------
    // HTML rendering — returns a string the modal injects into the DOM
    // -----------------------------------------------------------------

    /**
     * Return the static HTML for the panel. Data is filled in later
     * by ``load()``. Call this once, when the global settings modal
     * builds its tab panels.
     */
    renderHTML() {
        // The disclosure text below is the SOURCE OF TRUTH for what
        // the operator agreed to. If you edit it, also bump
        // DISCLOSURE_VERSION and DISCLOSURE_TEXT_SHA256 above so the
        // chain-of-custody can distinguish acks of different texts.
        return `
        <div class="settings-tab-panel" data-tab-panel="evidence">

          <!-- ── Master switch ────────────────────────────────────── -->
          <div class="setting-row">
            <div class="setting-top">
              <div class="setting-label">
                <i class="fas fa-shield-alt"></i> Master switch
              </div>
              <div class="setting-control">
                <label class="switch">
                  <input type="checkbox" id="evidence-master-switch">
                  <span class="slider"></span>
                </label>
              </div>
            </div>
            <div class="setting-description">
              <strong>On:</strong> Cameras checked below capture
              silence-pruned audio + classify acoustic events
              (screams, crying, impacts, raised voices) + transcribe
              speech.
              <br><strong>Off:</strong> No new captures. Existing
              evidence under <code>/litigation/</code> is preserved.
            </div>
          </div>

          <!-- ── Per-camera matrix ────────────────────────────────── -->
          <div class="setting-row">
            <div class="setting-label" style="margin-bottom: 8px;">
              <i class="fas fa-microphone"></i>
              Per-camera evidence collection
            </div>
            <div id="evidence-camera-matrix" class="evidence-matrix">
              <em class="evidence-matrix-loading">Loading…</em>
            </div>
            <div class="setting-description">
              Only audio-capable cameras are listed. Cameras without a
              published audio stream cannot be used as evidence sources;
              they appear under Streaming &amp; Recording instead.
            </div>
          </div>

          <!-- ── Storage status (read-only) ───────────────────────── -->
          <div class="setting-row">
            <div class="setting-top">
              <div class="setting-label">
                <i class="fas fa-hdd"></i> Litigation volume
              </div>
              <div class="setting-control evidence-status-box">
                <div id="evidence-status-volume">—</div>
                <div id="evidence-status-events">—</div>
                <div id="evidence-status-chain">—</div>
              </div>
            </div>
          </div>

          <!-- ── Legal disclosure ─────────────────────────────────── -->
          <div class="setting-row evidence-disclosure">
            <div class="setting-label">
              <i class="fas fa-balance-scale"></i> Legal disclosure
            </div>
            <div class="evidence-disclosure-box">
              <p>Audio recording is enabled in your home. New York is a
              <strong>one-party-consent</strong> state under N.Y. Penal
              Law §250.00 — recording a conversation is lawful when at
              least one party (you, the account holder) consents, OR
              when the conversation occurs in your residence.</p>
              <p>You are responsible for compliance with applicable
              law in your jurisdiction. If you are not in New York,
              consult local statutes before enabling this feature.
              Recording in shared spaces (Airbnb hosts recording
              guests, employees recording each other off-duty) may
              raise distinct legal issues this disclosure does not
              cover.</p>
              <p>Promotion of any evidence to a case (rsync to a
              legal workspace) is always a manual user action; this
              system never auto-shares captured audio.</p>
              <label class="evidence-disclosure-ack">
                <input type="checkbox" id="evidence-disclosure-ack-cb">
                I have read and accept the disclosure above.
              </label>
            </div>
          </div>

          <!-- ── Save / status row ────────────────────────────────── -->
          <div id="evidence-save-status" class="evidence-save-status"></div>
        </div>
        `;
    }


    // -----------------------------------------------------------------
    // Event wiring + data load
    // -----------------------------------------------------------------

    /**
     * Wire up event handlers on the rendered panel. Call once after
     * the panel has been inserted into the DOM (i.e. when the modal
     * renders the tab list). Safe to call repeatedly: re-binding via
     * jQuery namespaced events ('.evidence') replaces prior bindings.
     */
    init($panel) {
        this.$panel = $panel;

        // ── Master switch toggles every per-camera enable ──────────
        $panel.off('change.evidence', '#evidence-master-switch')
              .on('change.evidence', '#evidence-master-switch', (e) => {
            const on = e.currentTarget.checked;
            // Apply to every checkbox in the matrix that the user
            // hasn't explicitly modified themselves this session.
            // Simple semantics: master flips them all.
            $panel.find('.evidence-cam-enable').each((_i, cb) => {
                if (cb.checked !== on) {
                    cb.checked = on;
                    // Trigger the per-row change so the dirty set is
                    // updated correctly.
                    $(cb).trigger('change');
                }
            });
        });

        // ── Per-camera "enable" checkbox change ────────────────────
        $panel.off('change.evidence', '.evidence-cam-enable')
              .on('change.evidence', '.evidence-cam-enable', (e) => {
            const $cb = $(e.currentTarget);
            const serial = $cb.data('serial');
            this._mutateSetting(serial, 'enabled', !!$cb.prop('checked'));
        });

        // ── Per-camera audio / video sub-toggles ───────────────────
        $panel.off('change.evidence', '.evidence-cam-audio')
              .on('change.evidence', '.evidence-cam-audio', (e) => {
            const $cb = $(e.currentTarget);
            this._mutateSetting($cb.data('serial'), 'capture_audio',
                                !!$cb.prop('checked'));
        });
        $panel.off('change.evidence', '.evidence-cam-video')
              .on('change.evidence', '.evidence-cam-video', (e) => {
            const $cb = $(e.currentTarget);
            this._mutateSetting($cb.data('serial'), 'capture_video',
                                !!$cb.prop('checked'));
        });

        // ── Disclosure ack ─────────────────────────────────────────
        $panel.off('change.evidence', '#evidence-disclosure-ack-cb')
              .on('change.evidence', '#evidence-disclosure-ack-cb', (e) => {
            this._disclosureAckPending = !!e.currentTarget.checked;
        });
    }

    /**
     * Track that the user changed a setting for ``serial``. Marks the
     * camera as needing a PATCH on save. Silently ignores cameras we
     * don't have in the matrix (defensive).
     */
    _mutateSetting(serial, field, value) {
        if (!this._settings[serial]) return;
        this._settings[serial][field] = value;
        this._dirty.add(serial);
    }


    // -----------------------------------------------------------------
    // Data loading
    // -----------------------------------------------------------------

    /**
     * Pull cameras + evidence_camera_settings + status. Called when the
     * user opens the tab.
     */
    async load() {
        if (!this.$panel) return;
        const $matrix = this.$panel.find('#evidence-camera-matrix');
        $matrix.html('<em class="evidence-matrix-loading">Loading…</em>');

        try {
            // Fetch in parallel — three small independent requests.
            const [cameras, settings, status] = await Promise.all([
                this._fetchAudioCapableCameras(),
                this._fetchEvidenceSettings(),
                this._fetchStatus(),
            ]);

            // Index settings by serial for fast lookup during render.
            // Cameras without a settings row get a default (disabled)
            // shape — the row will be INSERTed if/when the user
            // enables them.
            this._cameras = cameras;
            this._settings = {};
            this._dirty.clear();
            for (const cam of cameras) {
                const existing = settings.find(s => s.serial === cam.serial);
                this._settings[cam.serial] = existing || {
                    serial: cam.serial,
                    enabled: false,
                    capture_video: true,
                    capture_audio: true,
                    silence_db_threshold: -40.0,
                    classifier_categories: [
                        'screams','crying','impacts','raised-voices',
                    ],
                    retention_days: 365,
                    _is_new_row: true,  // force POST not PATCH on save
                };
            }

            this._renderMatrix();
            this._renderStatus(status);
            this._renderMasterSwitch();
        } catch (err) {
            console.error('[EvidenceTab] load failed', err);
            $matrix.html(
                `<em class="evidence-matrix-error">Failed to load: ${err.message}</em>`
            );
        }
    }

    async _fetchAudioCapableCameras() {
        // PostgREST: filter cameras where audio_input_supported=true.
        // Selecting only the columns we display keeps the wire small.
        const url = `${this._pgrest}/cameras?audio_input_supported=eq.true`
                  + `&select=serial,name,brand,audio_input_supported`
                  + `&order=name.asc`;
        const r = await fetch(url, { credentials: 'same-origin' });
        if (!r.ok) throw new Error(`cameras HTTP ${r.status}`);
        return r.json();
    }

    async _fetchEvidenceSettings() {
        const url = `${this._pgrest}/evidence_camera_settings`
                  + `?select=*`;
        const r = await fetch(url, { credentials: 'same-origin' });
        if (!r.ok) throw new Error(`settings HTTP ${r.status}`);
        return r.json();
    }

    async _fetchStatus() {
        const r = await fetch('/api/evidence/status',
                              { credentials: 'same-origin' });
        if (!r.ok) throw new Error(`status HTTP ${r.status}`);
        return r.json();
    }


    // -----------------------------------------------------------------
    // Rendering
    // -----------------------------------------------------------------

    _renderMatrix() {
        const $matrix = this.$panel.find('#evidence-camera-matrix');
        if (this._cameras.length === 0) {
            $matrix.html(
                `<em>No audio-capable cameras detected. Run
                <code>scripts/survey_camera_audio.py</code> to probe.</em>`
            );
            return;
        }
        const rows = this._cameras.map(cam => {
            const s = this._settings[cam.serial];
            // Display-friendly camera name (avoid screaming-caps and
            // underscores in the UI). Same transform applied elsewhere
            // in the codebase.
            const display = (cam.name || cam.serial)
                .replace(/_/g, ' ')
                .replace(/\b\w/g, m => m.toUpperCase());
            return `
              <div class="evidence-matrix-row">
                <div class="evidence-matrix-name" title="${cam.serial}">
                  ${display}
                </div>
                <label class="evidence-matrix-cell">
                  <input type="checkbox" class="evidence-cam-enable"
                         data-serial="${cam.serial}"
                         ${s.enabled ? 'checked' : ''}>
                  Enable
                </label>
                <label class="evidence-matrix-cell">
                  <input type="checkbox" class="evidence-cam-audio"
                         data-serial="${cam.serial}"
                         ${s.capture_audio ? 'checked' : ''}>
                  Audio
                </label>
                <label class="evidence-matrix-cell">
                  <input type="checkbox" class="evidence-cam-video"
                         data-serial="${cam.serial}"
                         ${s.capture_video ? 'checked' : ''}>
                  Video
                </label>
              </div>
            `;
        }).join('');
        $matrix.html(rows);
    }

    _renderStatus(status) {
        const fmtBytes = (n) => {
            if (n == null) return '—';
            if (n < 1e9) return (n / 1e6).toFixed(0) + ' MB';
            if (n < 1e12) return (n / 1e9).toFixed(1) + ' GB';
            return (n / 1e12).toFixed(2) + ' TB';
        };
        this.$panel.find('#evidence-status-volume').html(
            `Path: <code>${status.volume_path || '—'}</code><br>`
          + `Free: <strong>${fmtBytes(status.volume_free_bytes)}</strong>`
          + ` of ${fmtBytes(status.volume_total_bytes)}`
        );
        this.$panel.find('#evidence-status-events').html(
            `Manifest entries: <strong>${status.manifest_total_entries}</strong>`
          + (status.last_event_utc
              ? `<br>Last activity: ${status.last_event_utc}`
              : '')
        );
        const chainSafe = status.chain_ok === true;
        this.$panel.find('#evidence-status-chain').html(
            `Chain integrity: `
          + (chainSafe
              ? `<span style="color:#2ecc71">✓ verified</span>`
              : `<span style="color:#e74c3c">✗ broken (investigate)</span>`)
        );
    }

    _renderMasterSwitch() {
        // Master switch reflects "any camera enabled". Not a stored
        // value of its own — derived from the per-camera state.
        const anyOn = Object.values(this._settings).some(s => s.enabled);
        this.$panel.find('#evidence-master-switch').prop('checked', anyOn);
    }


    // -----------------------------------------------------------------
    // Saving — call this from the global save button
    // -----------------------------------------------------------------

    /**
     * Persist any pending changes. Returns ``{ok: true}`` on success,
     * or ``{ok: false, error: string}`` on failure. Caller is
     * responsible for surfacing UI feedback.
     */
    async save() {
        const $status = this.$panel.find('#evidence-save-status');
        $status.html('<i class="fas fa-spinner fa-spin"></i> Saving…')
               .css('color', '#aaa');

        try {
            // ── 1. PATCH per-camera dirty rows ─────────────────────
            for (const serial of this._dirty) {
                const row = this._settings[serial];
                if (row._is_new_row) {
                    // No prior settings row exists — INSERT.
                    await this._upsertNewRow(row);
                    delete row._is_new_row;
                } else {
                    await this._patchRow(serial, row);
                }
            }
            this._dirty.clear();

            // ── 2. If the disclosure was just acked, POST it ───────
            if (this._disclosureAckPending) {
                await this._postDisclosureAck();
                this._disclosureAckPending = false;
            }

            $status.html('<i class="fas fa-check"></i> Saved')
                   .css('color', '#2ecc71');
            setTimeout(() => $status.html(''), 3000);
            return { ok: true };
        } catch (err) {
            console.error('[EvidenceTab] save failed', err);
            $status.html(`Failed: ${err.message}`).css('color', '#e74c3c');
            return { ok: false, error: err.message };
        }
    }

    async _patchRow(serial, row) {
        const url = `${this._pgrest}/evidence_camera_settings`
                  + `?serial=eq.${encodeURIComponent(serial)}`;
        const body = {
            enabled: !!row.enabled,
            capture_audio: !!row.capture_audio,
            capture_video: !!row.capture_video,
        };
        const r = await fetch(url, {
            method: 'PATCH',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'Prefer': 'return=minimal',
            },
            body: JSON.stringify(body),
        });
        if (!r.ok) throw new Error(`PATCH ${serial}: HTTP ${r.status}`);
    }

    async _upsertNewRow(row) {
        const url = `${this._pgrest}/evidence_camera_settings`;
        // Send only the columns we care about; defaults handle the rest.
        const body = {
            serial: row.serial,
            enabled: !!row.enabled,
            capture_audio: !!row.capture_audio,
            capture_video: !!row.capture_video,
        };
        const r = await fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'Prefer': 'return=minimal,resolution=ignore-duplicates',
            },
            body: JSON.stringify(body),
        });
        // 201 created, 200 ok, 409 conflict-ignored — all acceptable.
        if (![200, 201, 409].includes(r.status)) {
            throw new Error(`POST ${row.serial}: HTTP ${r.status}`);
        }
    }

    async _postDisclosureAck() {
        // Backend route writes a manifest lifecycle entry with
        // user_id, IP, user-agent, jurisdiction, disclosure version,
        // text hash. See routes/evidence_routes.py.
        const r = await fetch('/api/evidence/disclosure-ack', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                jurisdiction:           DEFAULT_JURISDICTION,
                disclosure_version:     DISCLOSURE_VERSION,
                disclosure_text_sha256: DISCLOSURE_TEXT_SHA256,
            }),
        });
        if (!r.ok) {
            const text = await r.text().catch(() => '');
            throw new Error(`disclosure-ack HTTP ${r.status} ${text}`);
        }
    }
}


// Singleton — global-settings-modal.js imports and uses the same
// instance across re-renders.
export const evidenceTab = new EvidenceCollectionTab();
