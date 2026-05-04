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
 * Endpoints used (all under /api/evidence/, all login_required)
 * -------------------------------------------------------------
 *
 *   GET  /api/evidence/cameras                  list audio-capable
 *                                               cameras + settings
 *   PUT  /api/evidence/camera-settings/<serial> upsert settings row
 *   GET  /api/evidence/status                   volume + chain stats
 *   POST /api/evidence/disclosure-ack           disclosure manifest entry
 *
 * Earlier drafts of this module talked to PostgREST directly through
 * a hypothetical /pgrest reverse-proxy path. That path doesn't exist
 * in nginx-edge by design — we keep PostgREST hidden from clients to
 * limit attack surface. The Flask endpoints above wrap the underlying
 * tables so the browser only sees a small, vetted surface.
 */


// =====================================================================
// JURISDICTION-AWARE DISCLOSURE (Phase 4.5)
// =====================================================================
//
// Each entry is a self-contained legal disclosure for one jurisdiction.
// We ship four out of the box:
//
//   * "US-NY"        — New York, the user's home jurisdiction. Cites
//                      §250.00 explicitly.
//   * "US-1PARTY"    — generic one-party-consent US states (38 of them
//                      including AL, AK, AZ, AR, CA*, CO, CT*... see
//                      Reporters Committee for Freedom of the Press
//                      summary). The * means "with caveats" — this is
//                      a baseline, not legal advice.
//   * "US-2PARTY"    — all-party-consent US states (CA, CT, FL, IL, MD,
//                      MA, MT, NH, OR, PA, WA). Recording in your home
//                      may still be lawful here under residence
//                      exceptions, but the user must know it's stricter.
//   * "OTHER"        — generic non-US fallback. EU users, French users,
//                      others — most jurisdictions outside the US
//                      require all-party consent or stricter notice
//                      regimes. We tell the user to verify locally.
//
// The user picks one in the disclosure dropdown. The chosen value is
// posted to /api/evidence/disclosure-ack along with that text's hash —
// so the chain-of-custody record proves exactly which jurisdiction
// the operator was claiming when they enabled recording.
//
// IMPORTANT: ``hash`` for each entry must match the SHA-256 of the
// ``html`` field's exact bytes (UTF-8). If you edit the html, re-hash
// with: echo -n "<html>" | sha256sum
// or just bump the version (e.g. "...-v2") to keep prior acks pinned.

const DISCLOSURE_VERSION = 1;

// Order of keys = order shown in the dropdown. First entry is the
// default selection. The default jurisdiction (US-NY) is also the
// fallback if the dropdown is somehow not rendered.
const DISCLOSURES = {
    "US-NY": {
        label: "United States — New York",
        html: `<p>Audio recording is enabled in your home. New York is a
            <strong>one-party-consent</strong> state under N.Y. Penal
            Law §250.00 — recording a conversation is lawful when at
            least one party (you, the account holder) consents, OR when
            the conversation occurs in your residence.</p>
            <p>You are responsible for compliance with applicable law.
            Recording in shared spaces (Airbnb hosts recording guests,
            employees recording each other off-duty) may raise distinct
            legal issues this disclosure does not cover.</p>
            <p>Promotion of any evidence to a case (rsync to a legal
            workspace) is always a manual user action; this system never
            auto-shares captured audio.</p>`,
        hash: "us-ny-v1-2026-04-28",
    },
    "US-1PARTY": {
        label: "United States — one-party-consent state",
        html: `<p>You have selected a U.S. <strong>one-party-consent</strong>
            jurisdiction. Recording a conversation is lawful when at least
            one party (you, the account holder) consents.</p>
            <p>One-party-consent states include (non-exhaustive): AL, AK,
            AZ, AR, CO, DE, GA, HI, ID, IN, IA, KS, KY, LA, ME, MI, MN,
            MS, MO, NE, NV, NJ, NM, NY, NC, ND, OH, OK, RI, SC, SD, TN,
            TX, UT, VT, VA, WV, WI, WY. Federal interstate calls fall
            under the one-party rule under 18 U.S.C. §2511(2)(d).</p>
            <p>You are responsible for compliance with applicable law.
            Verify your state's specific statute before relying on this
            recording for litigation. Promotion of any evidence to a case
            is always a manual user action.</p>`,
        hash: "us-1party-v1-2026-04-28",
    },
    "US-2PARTY": {
        label: "United States — all-party-consent state",
        html: `<p>You have selected a U.S. <strong>all-party-consent</strong>
            (often called "two-party") jurisdiction. Recording a
            conversation generally requires the consent of <em>every</em>
            party, with limited exceptions for in-home recording when you
            are present.</p>
            <p>All-party-consent states include (non-exhaustive): CA, CT,
            FL, IL, MD, MA, MT, NH, OR, PA, WA. Penalties for
            non-compliance can include both criminal liability and
            inadmissibility of the recording in court.</p>
            <p><strong>Strongly recommended:</strong> consult a local
            attorney before relying on this recording for any legal
            purpose. Recording in shared spaces, of visitors, or of
            non-residents is especially likely to fall outside the
            home-residence exception.</p>
            <p>Promotion of any evidence to a case is always a manual
            user action; this system never auto-shares captured audio.</p>`,
        hash: "us-2party-v1-2026-04-28",
    },
    "OTHER": {
        label: "Other / outside the United States",
        html: `<p>You have selected a non-U.S. jurisdiction or "other".
            Most jurisdictions outside the U.S. — including the European
            Union, the United Kingdom, Canada, France, and most of Latin
            America — require <strong>all-party consent</strong> for
            audio recording, or impose distinct notice obligations under
            data-protection law (GDPR, LGPD, etc.).</p>
            <p>You are responsible for compliance with applicable law in
            your jurisdiction. Consult a local attorney before enabling
            this feature; do not assume U.S. consent rules apply.</p>
            <p>Promotion of any evidence to a case is always a manual
            user action; this system never auto-shares captured audio.</p>`,
        hash: "other-v1-2026-04-28",
    },
};

// Ordered list — used to render the dropdown.
const DISCLOSURE_KEYS = Object.keys(DISCLOSURES);
const DEFAULT_JURISDICTION = "US-NY";


// =====================================================================
// EvidenceCollectionTab
// =====================================================================

export class EvidenceCollectionTab {

    constructor() {
        // jQuery handle to the tab panel container (set by init()).
        this.$panel = null;

        // In-memory state: cameras with audio support + their current
        // evidence settings rows. Loaded by load(), mutated as the
        // user clicks checkboxes, drained by save().
        this._cameras = [];        // [{serial, name, audio_input_supported, ...}]
        this._settings = {};       // serial → {enabled, capture_video, capture_audio, ...}
        this._dirty = new Set();   // serials whose row needs PATCHing
        this._disclosureAckPending = false; // true when user just checked the box

        // Currently-selected jurisdiction key (matches a key in
        // DISCLOSURES). Persists for the lifetime of the panel —
        // reload restores the default (or whatever the user last
        // saved server-side, when we wire that).
        this._jurisdiction = DEFAULT_JURISDICTION;
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

          <!-- ── Legal disclosure (jurisdiction-aware, Phase 4.5) ───
            The dropdown selects which canonical text to display. The
            chosen jurisdiction + the displayed text's hash are
            posted with the ack, so the chain-of-custody record proves
            exactly which legal claim the operator made when enabling
            recording. -->
          <div class="setting-row evidence-disclosure">
            <div class="setting-label">
              <i class="fas fa-balance-scale"></i> Legal disclosure
            </div>
            <div class="evidence-disclosure-jurisdiction">
              <label for="evidence-jurisdiction-select">Jurisdiction:</label>
              <select id="evidence-jurisdiction-select"
                      class="setting-select">
                ${DISCLOSURE_KEYS.map(k => `
                  <option value="${k}"
                          ${k === DEFAULT_JURISDICTION ? 'selected' : ''}>
                    ${DISCLOSURES[k].label}
                  </option>
                `).join('')}
              </select>
            </div>
            <div class="evidence-disclosure-box"
                 id="evidence-disclosure-text">
              ${DISCLOSURES[DEFAULT_JURISDICTION].html}
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

        // ── Jurisdiction dropdown change (Phase 4.5) ───────────────
        // Switching jurisdiction means the text the user is being
        // asked to ack changes — so we MUST clear any pending ack
        // when they switch, otherwise they'd be acking text they
        // didn't see. Forces re-check of the box.
        $panel.off('change.evidence', '#evidence-jurisdiction-select')
              .on('change.evidence', '#evidence-jurisdiction-select', (e) => {
            const key = $(e.currentTarget).val();
            if (!DISCLOSURES[key]) return;
            this._jurisdiction = key;
            // Re-render the disclosure text (preserve the ack
            // checkbox element by re-injecting the text + the
            // checkbox label together).
            $panel.find('#evidence-disclosure-text').html(
                DISCLOSURES[key].html
              + `<label class="evidence-disclosure-ack">
                   <input type="checkbox" id="evidence-disclosure-ack-cb">
                   I have read and accept the disclosure above.
                 </label>`
            );
            // Reset the pending state — user must re-check.
            this._disclosureAckPending = false;
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
            // Fetch in parallel — two small independent requests.
            // /api/evidence/cameras already does the camera+settings
            // join server-side, so we only need that + status.
            const [camerasWithSettings, status] = await Promise.all([
                this._fetchCamerasWithSettings(),
                this._fetchStatus(),
            ]);

            // Materialize internal state. Each entry from the server is
            // {serial, name, audio_input_supported, settings: {...}}.
            this._cameras = camerasWithSettings.map(c => ({
                serial: c.serial,
                name: c.name,
                audio_input_supported: c.audio_input_supported,
            }));
            this._settings = {};
            this._dirty.clear();
            for (const c of camerasWithSettings) {
                this._settings[c.serial] = {
                    serial: c.serial,
                    ...c.settings,
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

    async _fetchCamerasWithSettings() {
        // Server-side endpoint joins cameras + evidence_camera_settings
        // and fills in defaults for cameras without an existing row,
        // so this single GET gives the UI everything it needs.
        const r = await fetch('/api/evidence/cameras',
                              { credentials: 'same-origin' });
        if (!r.ok) throw new Error(`cameras HTTP ${r.status}`);
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
            // ── 1. UPSERT per-camera dirty rows ────────────────────
            // Server-side endpoint is upsert-aware — PATCH if row
            // exists, INSERT otherwise — so the client doesn't need
            // to track that distinction.
            for (const serial of this._dirty) {
                await this._upsertRow(serial, this._settings[serial]);
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

    async _upsertRow(serial, row) {
        // Single endpoint does both PATCH-existing and INSERT-new
        // server-side. We send only the user-facing fields (master
        // tunables like silence_db_threshold or retention_days are
        // not yet exposed in the UI matrix; they keep their
        // server-side defaults).
        const url = `/api/evidence/camera-settings/${encodeURIComponent(serial)}`;
        const body = {
            enabled:       !!row.enabled,
            capture_audio: !!row.capture_audio,
            capture_video: !!row.capture_video,
        };
        const r = await fetch(url, {
            method: 'PUT',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!r.ok) {
            const text = await r.text().catch(() => '');
            throw new Error(`PUT ${serial}: HTTP ${r.status} ${text}`);
        }
    }

    async _postDisclosureAck() {
        // Backend route writes a manifest lifecycle entry with
        // user_id, IP, user-agent, jurisdiction, disclosure version,
        // text hash. See routes/evidence_routes.py.
        // Phase 4.5: jurisdiction now reflects the user's pick.
        const j = DISCLOSURES[this._jurisdiction] ||
                  DISCLOSURES[DEFAULT_JURISDICTION];
        const r = await fetch('/api/evidence/disclosure-ack', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                jurisdiction:           this._jurisdiction,
                disclosure_version:     DISCLOSURE_VERSION,
                disclosure_text_sha256: j.hash,
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
