/**
 * EUFY BRIDGE TAB (ES6 + jQuery)
 * ===============================
 *
 * Renders and manages the "Eufy Bridge" tab in the global settings modal.
 * The tab is admin-only AND conditional on Eufy cameras being present
 * (window.EUFY_BRIDGE_AVAILABLE, set server-side in the streams template).
 *
 * What this module does
 * ---------------------
 *
 *   1. ``renderHTML()`` returns the static structure for the tab panel:
 *        - a live status block (driver connected, cloud-token expiry,
 *          per-station P2P table),
 *        - a "Force re-login" + "Refresh captcha" action row,
 *        - an inline captcha/2FA sub-panel (hidden until a relogin or a
 *          poll detects the bridge is asking for auth).
 *   2. ``init($panel)`` wires event handlers (idempotent, namespaced
 *      '.eufybridge') and is called every time the tab is opened.
 *   3. ``load()`` polls ``GET /api/eufy-bridge/status`` and renders it.
 *      Called on tab open; a light interval keeps the panel live while the
 *      tab is visible and is torn down on ``stop()``.
 *
 * Reused backend endpoints
 * ------------------------
 *
 *   GET  /api/eufy-bridge/status      driver + token + per-station state
 *   POST /api/eufy-bridge/relogin     backup+remove persistent.json, restart
 *   GET  /api/eufy-auth/status        polled after relogin (auth progress)
 *   GET  /api/eufy-auth/captcha-image current captcha PNG
 *   POST /api/eufy-auth/refresh-captcha
 *   POST /api/eufy-auth/captcha       submit 4-char captcha
 *   POST /api/eufy-auth/2fa           submit 6-digit email code
 *
 * The captcha/2FA flow mirrors templates/eufy_auth.html but is inlined here
 * so the operator never leaves the settings modal.
 *
 * Architecture / style: one class exported as a singleton, ES6 + jQuery,
 * render-returns-a-string + init-wires-handlers — the exact shape of
 * evidence-collection.js so the modal composes it the same way.
 */


export class EufyBridgeTab {

    constructor() {
        // jQuery handle to the tab panel container (set by init()).
        this.$panel = null;
        // setInterval handle for the live status poll while the tab is open.
        this._pollTimer = null;
        // Poll cadence (ms) for the status block. Cheap WS round-trips, but
        // we keep it modest so a roomful of cameras doesn't hammer the bridge.
        this.POLL_INTERVAL_MS = 8000;
        // True once the inline captcha sub-panel has been surfaced, so we
        // don't keep re-fetching the captcha image on every auth poll.
        this._captchaShown = false;
        // Auth-progress poll timer (active only during a relogin flow).
        this._authPollTimer = null;
    }


    // -----------------------------------------------------------------
    // HTML rendering — returns a string the modal injects into the DOM
    // -----------------------------------------------------------------

    /**
     * Return the static HTML for the panel. Data is filled in by load().
     */
    renderHTML() {
        return `
        <div class="settings-tab-panel" data-tab-panel="eufy-bridge">

          <!-- ── Live status ──────────────────────────────────────── -->
          <div class="setting-row">
            <div class="setting-top">
              <div class="setting-label">
                <i class="fas fa-plug"></i> Bridge status
              </div>
              <div class="setting-control" style="display:flex;align-items:center;gap:8px;">
                <button type="button" id="eufy-bridge-refresh-status"
                        class="setting-btn setting-btn-secondary"
                        style="font-size:12px;padding:5px 12px;">
                  <i class="fas fa-sync-alt"></i> Refresh
                </button>
              </div>
            </div>
            <div class="setting-description" id="eufy-bridge-status-box"
                 style="line-height:1.8;">
              <em>Loading…</em>
            </div>
          </div>

          <!-- ── Per-station P2P table ────────────────────────────── -->
          <div class="setting-row">
            <div class="setting-label" style="margin-bottom:8px;">
              <i class="fas fa-satellite-dish"></i> Per-station P2P connection
            </div>
            <div id="eufy-bridge-stations" class="evidence-matrix">
              <em>Loading…</em>
            </div>
            <div class="setting-description">
              <strong>Connected:</strong> the bridge has a live P2P tunnel to the
              station (PTZ and talkback will be delivered).<br>
              <strong>Timeout:</strong> P2P is down — commands are accepted by the
              bridge but never reach the camera (check the Eufy bridge's WAN /
              firewall).<br>
              <strong>Unknown:</strong> the station serial differs from the camera
              serial (HomeBase-linked) or the bridge could not answer — this is
              <em>not</em> a failure.
            </div>
          </div>

          <!-- ── Actions ──────────────────────────────────────────── -->
          <div class="setting-row">
            <div class="setting-top">
              <div class="setting-label">
                <i class="fas fa-key"></i> Authentication
              </div>
              <div class="setting-control" style="display:flex;gap:8px;flex-wrap:wrap;">
                <button type="button" id="eufy-bridge-relogin"
                        class="setting-btn setting-btn-primary"
                        style="font-size:12px;padding:6px 14px;">
                  <i class="fas fa-redo"></i> Force re-login
                </button>
                <button type="button" id="eufy-bridge-refresh-captcha"
                        class="setting-btn setting-btn-secondary"
                        style="font-size:12px;padding:6px 14px;">
                  <i class="fas fa-sync-alt"></i> Refresh captcha
                </button>
              </div>
            </div>
            <div class="setting-description">
              <strong>Force re-login</strong> backs up and removes the cached Eufy
              cloud token, restarts the bridge, and starts a fresh login — you
              will likely need to type a captcha (and possibly a 6-digit 2FA
              code emailed to you).
            </div>
            <div id="eufy-bridge-action-status"
                 style="font-size:12px;margin-top:6px;"></div>
          </div>

          <!-- ── Inline captcha / 2FA (hidden until needed) ───────── -->
          <div class="setting-row" id="eufy-bridge-auth-panel" style="display:none;">
            <div class="setting-label" style="margin-bottom:8px;">
              <i class="fas fa-shield-alt"></i> Complete authentication
            </div>

            <!-- Captcha step -->
            <div id="eufy-bridge-captcha-step">
              <div style="background:#fff;display:inline-block;padding:12px;border-radius:8px;margin-bottom:10px;">
                <img id="eufy-bridge-captcha-img" alt="Captcha"
                     style="max-width:100%;height:auto;display:block;"
                     onerror="this.style.display='none';">
              </div>
              <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
                <input type="text" id="eufy-bridge-captcha-code" maxlength="4"
                       autocomplete="off" placeholder="4-char captcha"
                       style="padding:8px;border-radius:6px;border:1px solid #444;background:#0f1419;color:#eee;text-align:center;letter-spacing:0.2em;width:140px;">
                <button type="button" id="eufy-bridge-submit-captcha"
                        class="setting-btn setting-btn-primary"
                        style="font-size:12px;padding:6px 14px;">
                  Verify captcha
                </button>
              </div>
            </div>

            <!-- 2FA step (hidden until captcha succeeds) -->
            <div id="eufy-bridge-2fa-step" style="display:none;margin-top:12px;">
              <div class="setting-description" style="margin-bottom:6px;">
                Enter the 6-digit code emailed to you by Eufy.
              </div>
              <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
                <input type="text" id="eufy-bridge-2fa-code" maxlength="6"
                       autocomplete="off" placeholder="6-digit code"
                       style="padding:8px;border-radius:6px;border:1px solid #444;background:#0f1419;color:#eee;text-align:center;letter-spacing:0.3em;width:160px;">
                <button type="button" id="eufy-bridge-submit-2fa"
                        class="setting-btn setting-btn-primary"
                        style="font-size:12px;padding:6px 14px;">
                  Verify code
                </button>
              </div>
            </div>

            <div id="eufy-bridge-auth-status"
                 style="font-size:12px;margin-top:8px;"></div>
          </div>

        </div>
        `;
    }


    // -----------------------------------------------------------------
    // Event wiring
    // -----------------------------------------------------------------

    /**
     * Wire up event handlers on the rendered panel. Idempotent — re-binding
     * via namespaced events ('.eufybridge') replaces prior bindings.
     */
    init($panel) {
        this.$panel = $panel;
        const self = this;

        // ── Refresh status button ──────────────────────────────────
        $panel.off('click.eufybridge', '#eufy-bridge-refresh-status')
              .on('click.eufybridge', '#eufy-bridge-refresh-status', () => self.load());

        // ── Force re-login (destructive — confirm gate) ────────────
        $panel.off('click.eufybridge', '#eufy-bridge-relogin')
              .on('click.eufybridge', '#eufy-bridge-relogin', () => self._onRelogin());

        // ── Refresh captcha ────────────────────────────────────────
        $panel.off('click.eufybridge', '#eufy-bridge-refresh-captcha')
              .on('click.eufybridge', '#eufy-bridge-refresh-captcha', () => self._refreshCaptcha());

        // ── Submit captcha ─────────────────────────────────────────
        $panel.off('click.eufybridge', '#eufy-bridge-submit-captcha')
              .on('click.eufybridge', '#eufy-bridge-submit-captcha', () => self._submitCaptcha());

        // ── Submit 2FA ─────────────────────────────────────────────
        $panel.off('click.eufybridge', '#eufy-bridge-submit-2fa')
              .on('click.eufybridge', '#eufy-bridge-submit-2fa', () => self._submit2fa());

        // ── Input sanitisers ───────────────────────────────────────
        $panel.off('input.eufybridge', '#eufy-bridge-captcha-code')
              .on('input.eufybridge', '#eufy-bridge-captcha-code', function () {
            this.value = this.value.replace(/[^A-Za-z0-9]/g, '');
        });
        $panel.off('input.eufybridge', '#eufy-bridge-2fa-code')
              .on('input.eufybridge', '#eufy-bridge-2fa-code', function () {
            this.value = this.value.replace(/[^0-9]/g, '');
        });
    }


    // -----------------------------------------------------------------
    // Live status polling
    // -----------------------------------------------------------------

    /**
     * Pull /api/eufy-bridge/status and render it. Also starts the live
     * poll if not already running.
     */
    async load() {
        if (!this.$panel) return;
        await this._fetchAndRenderStatus();
        this._startPoll();
    }

    /** Start the periodic status poll (idempotent). */
    _startPoll() {
        if (this._pollTimer) return;
        this._pollTimer = setInterval(() => this._fetchAndRenderStatus(),
                                      this.POLL_INTERVAL_MS);
    }

    /**
     * Stop all timers. Called when the modal closes so we don't keep
     * polling the bridge in the background.
     */
    stop() {
        if (this._pollTimer) { clearInterval(this._pollTimer); this._pollTimer = null; }
        if (this._authPollTimer) { clearInterval(this._authPollTimer); this._authPollTimer = null; }
    }

    async _fetchAndRenderStatus() {
        const $box = this.$panel.find('#eufy-bridge-status-box');
        const $stations = this.$panel.find('#eufy-bridge-stations');
        try {
            const r = await fetch('/api/eufy-bridge/status',
                                  { credentials: 'same-origin' });
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            const data = await r.json();
            this._renderStatus(data);
        } catch (err) {
            console.error('[EufyBridgeTab] status load failed', err);
            $box.html(`<span style="color:#e74c3c;">Failed to load: ${err.message}</span>`);
            $stations.html('');
        }
    }

    _renderStatus(data) {
        const $box = this.$panel.find('#eufy-bridge-status-box');
        const $stations = this.$panel.find('#eufy-bridge-stations');

        if (data.available === false) {
            $box.html(`<span style="color:#f39c12;">${data.message ||
                       'Eufy bridge not configured.'}</span>`);
            $stations.html('<em>No Eufy bridge.</em>');
            return;
        }

        // ── Driver line ──
        const drv = data.driver || {};
        let driverHtml;
        if (!drv.reachable) {
            driverHtml = `<span style="color:#e74c3c;">✗ bridge unreachable (server down?)</span>`;
        } else if (drv.connected) {
            driverHtml = `<span style="color:#2ecc71;">✓ connected to Eufy cloud</span>`;
        } else {
            driverHtml = `<span style="color:#f39c12;">✗ not connected (authentication required)</span>`;
        }

        // ── Token line ──
        const tok = data.token || {};
        let tokenHtml;
        if (tok.expiration_ms == null) {
            tokenHtml = `<span style="color:#888;">unknown (no token on file)</span>`;
        } else {
            const d = new Date(tok.expiration_ms);
            const days = tok.days_left;
            const dateStr = d.toLocaleString();
            let color = '#2ecc71';
            if (days != null && days < 0) color = '#e74c3c';
            else if (days != null && days < 3) color = '#f39c12';
            const daysStr = (days != null)
                ? (days < 0 ? `expired ${Math.abs(days)}d ago` : `${days}d left`)
                : '';
            tokenHtml = `<span style="color:${color};">${dateStr}</span>
                         <span style="opacity:0.8;">(${daysStr})</span>`;
        }

        $box.html(
            `<div><strong>Driver:</strong> ${driverHtml}</div>`
          + `<div><strong>Cloud token:</strong> ${tokenHtml}</div>`
        );

        // ── Stations table ──
        const stations = data.stations || [];
        if (stations.length === 0) {
            $stations.html('<em>No Eufy cameras found.</em>');
            return;
        }
        const stateBadge = (st) => {
            if (st === 'connected') return `<span style="color:#2ecc71;">✓ connected</span>`;
            if (st === 'timeout')   return `<span style="color:#e74c3c;">✗ timeout</span>`;
            return `<span style="color:#888;">? unknown</span>`;
        };
        const rows = stations.map(s => {
            const display = (s.name || s.serial)
                .replace(/_/g, ' ')
                .replace(/\b\w/g, m => m.toUpperCase());
            return `
              <div class="evidence-matrix-row" title="${s.serial}">
                <div class="evidence-matrix-name">${display}</div>
                <div class="evidence-matrix-cell" style="font-family:monospace;font-size:11px;opacity:0.7;">
                  ${s.station_serial || s.serial}
                </div>
                <div class="evidence-matrix-cell">${stateBadge(s.state)}</div>
              </div>`;
        }).join('');
        $stations.html(rows);
    }


    // -----------------------------------------------------------------
    // Relogin + inline captcha / 2FA flow
    // -----------------------------------------------------------------

    /**
     * Force re-login. Gated behind an explicit confirm() that spells out the
     * destructive nature (drops the cached token, will require a captcha and
     * possibly 2FA).
     */
    async _onRelogin() {
        const confirmed = confirm(
            'Force a fresh Eufy cloud login?\n\n'
          + 'This backs up and removes the cached Eufy token and restarts the '
          + 'bridge. You will likely need to type a CAPTCHA, and possibly a '
          + '6-digit 2FA code emailed to you, to finish logging in.\n\n'
          + 'Continue?'
        );
        if (!confirmed) return;

        const $status = this.$panel.find('#eufy-bridge-action-status');
        $status.html('<i class="fas fa-spinner fa-spin"></i> Restarting bridge…')
               .css('color', '#aaa');

        try {
            const r = await fetch('/api/eufy-bridge/relogin', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
            });
            const body = await r.json().catch(() => ({}));
            if (!r.ok || !body.success) {
                throw new Error(body.message || `HTTP ${r.status}`);
            }
            $status.html(`<span style="color:#2ecc71;">${body.message}</span>`);
            // Surface the captcha panel and start polling auth progress so the
            // captcha image appears as soon as the bridge emits one.
            this._showAuthPanel();
            this._startAuthPoll();
        } catch (err) {
            $status.html(`<span style="color:#e74c3c;">Relogin failed: ${err.message}</span>`);
        }
    }

    /** Reveal the inline captcha/2FA sub-panel and load the captcha image. */
    _showAuthPanel() {
        const $auth = this.$panel.find('#eufy-bridge-auth-panel');
        $auth.show();
        this.$panel.find('#eufy-bridge-2fa-step').hide();
        this.$panel.find('#eufy-bridge-captcha-step').show();
        this._refreshCaptcha();
        this._captchaShown = true;
    }

    /**
     * Poll /api/eufy-auth/status until the bridge reports connected (auth
     * complete) or the modal closes. While not connected we keep the captcha
     * panel up.
     */
    _startAuthPoll() {
        if (this._authPollTimer) return;
        const self = this;
        this._authPollTimer = setInterval(async () => {
            try {
                const r = await fetch('/api/eufy-auth/status',
                                      { credentials: 'same-origin' });
                const s = await r.json().catch(() => ({}));
                if (s.connected) {
                    self.$panel.find('#eufy-bridge-auth-status')
                        .html('<span style="color:#2ecc71;">✓ Authenticated — bridge connected.</span>');
                    self.$panel.find('#eufy-bridge-auth-panel').hide();
                    clearInterval(self._authPollTimer);
                    self._authPollTimer = null;
                    self._captchaShown = false;
                    self._fetchAndRenderStatus();
                }
            } catch (e) {
                /* transient — keep polling */
            }
        }, 4000);
    }

    /** Request a fresh captcha from the bridge and reload the image. */
    async _refreshCaptcha() {
        const $status = this.$panel.find('#eufy-bridge-auth-status');
        try {
            await fetch('/api/eufy-auth/refresh-captcha', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
            });
        } catch (e) {
            /* still try to reload the image below */
        }
        // Give the bridge a moment to render the new captcha, then bust cache.
        setTimeout(() => {
            const ts = Date.now();
            this.$panel.find('#eufy-bridge-captcha-img')
                .attr('src', `/api/eufy-auth/captcha-image?t=${ts}`)
                .css('display', 'block');
        }, 1200);
        // If the auth panel wasn't open yet (operator clicked Refresh captcha
        // directly), surface it so they can see the image.
        if (!this._captchaShown) {
            this._showAuthPanel();
        }
        $status.html('<span style="color:#888;">New captcha requested…</span>');
    }

    /** Submit the 4-char captcha; on success reveal the 2FA step. */
    async _submitCaptcha() {
        const $status = this.$panel.find('#eufy-bridge-auth-status');
        const code = (this.$panel.find('#eufy-bridge-captcha-code').val() || '').trim();
        if (!/^[A-Za-z0-9]{4}$/.test(code)) {
            $status.html('<span style="color:#e74c3c;">Enter a 4-character captcha.</span>');
            return;
        }
        $status.html('<i class="fas fa-spinner fa-spin"></i> Verifying captcha…').css('color', '#aaa');
        try {
            const r = await fetch('/api/eufy-auth/captcha', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ captcha_code: code }),
            });
            const body = await r.json().catch(() => ({}));
            if (!r.ok || !body.success) throw new Error(body.message || `HTTP ${r.status}`);
            $status.html(`<span style="color:#2ecc71;">${body.message}</span>`);
            // Advance to 2FA step.
            this.$panel.find('#eufy-bridge-captcha-step').hide();
            this.$panel.find('#eufy-bridge-2fa-step').show();
            this.$panel.find('#eufy-bridge-2fa-code').focus();
        } catch (err) {
            $status.html(`<span style="color:#e74c3c;">${err.message}</span>`);
            // Reload a fresh captcha for the retry.
            this._refreshCaptcha();
        }
    }

    /** Submit the 6-digit 2FA code. */
    async _submit2fa() {
        const $status = this.$panel.find('#eufy-bridge-auth-status');
        const code = (this.$panel.find('#eufy-bridge-2fa-code').val() || '').trim();
        if (!/^[0-9]{6}$/.test(code)) {
            $status.html('<span style="color:#e74c3c;">Enter a 6-digit code.</span>');
            return;
        }
        $status.html('<i class="fas fa-spinner fa-spin"></i> Verifying code…').css('color', '#aaa');
        try {
            const r = await fetch('/api/eufy-auth/2fa', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ verify_code: code }),
            });
            const body = await r.json().catch(() => ({}));
            if (!r.ok || !body.success) throw new Error(body.message || `HTTP ${r.status}`);
            $status.html(`<span style="color:#2ecc71;">${body.message}</span>`);
            this.$panel.find('#eufy-bridge-auth-panel').hide();
            this._captchaShown = false;
            // Refresh the live status now that we should be connected.
            setTimeout(() => this._fetchAndRenderStatus(), 1000);
        } catch (err) {
            $status.html(`<span style="color:#e74c3c;">${err.message}</span>`);
        }
    }
}


// Singleton — global-settings-modal.js imports and uses the same instance
// across re-renders.
export const eufyBridgeTab = new EufyBridgeTab();
