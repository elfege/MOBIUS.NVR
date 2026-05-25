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

          <!-- ── Firewall setup guide ─────────────────────────────── -->
          <div class="setting-row">
            <div class="setting-top">
              <div class="setting-label">
                <i class="fas fa-shield-halved"></i> Firewall configuration
              </div>
              <div class="setting-control">
                <button type="button" id="eufy-bridge-fw-guide"
                        class="setting-btn setting-btn-secondary"
                        style="font-size:12px;padding:6px 14px;">
                  <i class="fas fa-book"></i> Firewall setup guide (PTZ / P2P)
                </button>
              </div>
            </div>
            <div class="setting-description">
              Eufy PTZ &amp; talkback need each camera to reach Eufy's cloud (P2P).
              If the stations above show <strong>Timeout</strong>, your firewall is
              blocking it. This opens a step-by-step, printable guide showing exactly
              what to allow on any firewall.
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

        // ── Firewall setup guide (printable modal) ─────────────────
        $panel.off('click.eufybridge', '#eufy-bridge-fw-guide')
              .on('click.eufybridge', '#eufy-bridge-fw-guide', () => self._openFirewallGuide());

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

    // -----------------------------------------------------------------
    // Firewall setup guide (printable manual)
    // -----------------------------------------------------------------

    /**
     * The guide body HTML. Self-styled (scoped to .eufy-fw-guide) with neutral
     * colors so it reads on BOTH the dark in-app modal and the light print
     * window. Generic — vendor-agnostic firewall terminology.
     */
    _firewallGuideHTML() {
        return `
        <style>
          .eufy-fw-guide h2{margin:0 0 4px;font-size:18px;}
          .eufy-fw-guide h3{margin:18px 0 6px;font-size:14px;border-bottom:1px solid rgba(128,128,128,.45);padding-bottom:3px;}
          .eufy-fw-guide table{border-collapse:collapse;width:100%;margin:8px 0;font-size:12.5px;}
          .eufy-fw-guide th,.eufy-fw-guide td{border:1px solid rgba(128,128,128,.55);padding:5px 8px;text-align:left;vertical-align:top;}
          .eufy-fw-guide th{background:rgba(128,128,128,.18);}
          .eufy-fw-guide code{background:rgba(128,128,128,.20);padding:1px 5px;border-radius:3px;font-size:12px;}
          .eufy-fw-guide ol,.eufy-fw-guide ul{margin:6px 0 6px 18px;padding:0;}
          .eufy-fw-guide li{margin:3px 0;}
          .eufy-fw-guide .note{border-left:3px solid #f0ad4e;background:rgba(240,173,78,.12);padding:8px 12px;margin:10px 0;border-radius:0 4px 4px 0;}
          .eufy-fw-guide .muted{opacity:.8;font-size:12px;}
        </style>
        <div class="eufy-fw-guide">
          <h2>Letting Eufy cameras reach the cloud — required for PTZ &amp; talkback</h2>
          <p class="muted">Generic, vendor-agnostic guide. Your firewall's wording may differ
          (see the terminology table near the end), but the steps are identical on any stateful
          firewall — SonicWall, pfSense/OPNsense, UniFi, Firewalla, etc.</p>

          <h3>1. Why this is needed</h3>
          <p>Eufy <strong>video streaming is local</strong> (RTSP) and needs no internet. But Eufy
          <strong>PTZ, talkback and device commands ride "P2P"</strong>, and Eufy's P2P requires
          <strong>each camera to reach Eufy's cloud</strong> to register where it can be found. If the
          cameras are blocked from the internet, P2P never establishes and PTZ silently times out —
          even though live view keeps working.</p>
          <p><strong>Goal:</strong> keep the cameras off the open internet, but allow them to reach
          <em>Eufy's cloud only</em>.</p>

          <h3>2. What you will create</h3>
          <ul>
            <li>One <strong>address object</strong> per Eufy camera (its LAN IP).</li>
            <li>An <strong>address group</strong> containing them — call it <code>EUFY_CAMERAS</code>.</li>
            <li>An <strong>FQDN / domain object</strong> for <code>*.eufylife.com</code>.</li>
            <li>A <strong>UDP service</strong> covering ports <code>1024-65535</code>.</li>
            <li>Three <strong>allow rules</strong>, placed <em>above</em> your existing
            "cameras &rarr; internet: deny" rule.</li>
          </ul>

          <h3>3. Step by step</h3>
          <ol>
            <li><strong>Address objects</strong> — one host object per Eufy camera, using its LAN IP
            (e.g. <code>192.168.1.50</code>). Find each camera's IP in your DHCP leases or the Eufy app.</li>
            <li><strong>Group</strong> them into <code>EUFY_CAMERAS</code> (include the HomeBase/base
            station too, if you have one).</li>
            <li><strong>FQDN object</strong> — create a domain/FQDN object <code>*.eufylife.com</code>
            (Eufy's API, auth and push).</li>
            <li><strong>UDP service object</strong> — protocol <strong>UDP</strong>, port range
            <strong>1024-65535</strong>. <span class="muted">Eufy P2P uses dynamic high ports; a narrow
            range (e.g. only 32100) will NOT work — this is the single most common reason setups fail.</span></li>
            <li><strong>Create the rules</strong> below — all <strong>outbound (LAN&rarr;WAN)</strong>,
            source <code>EUFY_CAMERAS</code>, ordered ABOVE your camera-deny:</li>
          </ol>

          <table>
            <tr><th>#</th><th>Source</th><th>Destination</th><th>Service</th><th>Action</th></tr>
            <tr><td>1</td><td>EUFY_CAMERAS</td><td>Any</td><td>DNS (UDP/TCP 53)</td><td>Allow</td></tr>
            <tr><td>2</td><td>EUFY_CAMERAS</td><td><code>*.eufylife.com</code></td><td>HTTPS (TCP 443)</td><td>Allow</td></tr>
            <tr><td>3</td><td>EUFY_CAMERAS</td><td>Any</td><td>UDP 1024-65535</td><td>Allow</td></tr>
            <tr><td>4</td><td>cameras (your existing group)</td><td>Any</td><td>Any</td><td><strong>Deny</strong> — keep BELOW rules 1-3</td></tr>
          </table>
          <p class="muted">Rule 1 (DNS) is only needed if the cameras use a public DNS server; if they
          resolve via your router/firewall, skip it. Rule 3 uses destination "Any" because Eufy's P2P
          relay IPs are dynamic — the exposure stays bounded: these cameras can still only do
          DNS + HTTPS-to-Eufy + outbound UDP, never arbitrary connections.</p>

          <ol start="6">
            <li><strong>Apply / activate</strong> the rules.</li>
            <li><strong>Force re-registration</strong> — power-cycle one camera so it re-registers with
            Eufy immediately, instead of waiting up to ~30 minutes.</li>
            <li><strong>Verify</strong> — back in the Eufy Bridge tab, the <em>Per-station P2P</em> list
            should turn <strong>Connected</strong>, and PTZ should move the camera.</li>
          </ol>

          <h3>4. Security notes</h3>
          <ul>
            <li>These are <strong>outbound</strong> rules. They do <strong>not</strong> expose any inbound
            ports — a port scan of your public IP is unaffected. The cameras can talk out to Eufy; the
            internet still cannot reach them.</li>
            <li><strong>Disable UPnP / NAT-PMP</strong> on your firewall so a camera can't auto-open an
            inbound hole for itself.</li>
            <li>If TLS to Eufy intermittently fails, also allow <strong>NTP (UDP 123)</strong> — cameras
            need accurate time for certificate validation.</li>
          </ul>

          <h3>5. Firewall terminology (names vary by vendor)</h3>
          <table>
            <tr><th>This guide</th><th>Also called</th></tr>
            <tr><td>Address object</td><td>Alias / Host / Network object</td></tr>
            <tr><td>FQDN object</td><td>Domain object / URL object</td></tr>
            <tr><td>Service</td><td>Port / Application / Protocol object</td></tr>
            <tr><td>Rule order</td><td>Priority / Sequence / Position</td></tr>
          </table>

          <h3>6. Still showing "Timeout"?</h3>
          <ul>
            <li>Confirm the three allow rules sit <strong>above</strong> the camera-deny — order matters.</li>
            <li>Make sure the UDP service is the <strong>wide</strong> range (1024-65535), not just 32100.</li>
            <li>Power-cycle the camera again to force re-registration; give it a couple of minutes.</li>
            <li>Check the camera can resolve DNS (rule 1) and reach <code>*.eufylife.com</code> (rule 2).</li>
          </ul>
        </div>
        `;
    }

    /** Open the guide in a large, scrollable, dark-themed modal over the app. */
    _openFirewallGuide() {
        $('#eufy-fw-guide-overlay').remove();
        const overlay = $(`
          <div id="eufy-fw-guide-overlay" style="position:fixed;inset:0;z-index:20000;
               background:rgba(0,0,0,0.78);display:flex;align-items:center;justify-content:center;padding:20px;">
            <div style="background:#161b22;color:#e6e6e6;border:1px solid #30363d;border-radius:10px;
                 width:min(940px,96vw);max-height:88vh;display:flex;flex-direction:column;
                 box-shadow:0 12px 48px rgba(0,0,0,0.6);">
              <div style="display:flex;align-items:center;justify-content:space-between;
                   padding:14px 18px;border-bottom:1px solid #30363d;flex:0 0 auto;">
                <div style="font-size:15px;font-weight:600;">
                  <i class="fas fa-shield-halved"></i> Firewall setup &mdash; Eufy cameras &amp; PTZ
                </div>
                <div style="display:flex;gap:8px;">
                  <button type="button" id="eufy-fw-print" class="setting-btn setting-btn-secondary"
                          style="font-size:12px;padding:6px 12px;"><i class="fas fa-print"></i> Print</button>
                  <button type="button" id="eufy-fw-close" class="setting-btn setting-btn-secondary"
                          style="font-size:12px;padding:6px 12px;">&times; Close</button>
                </div>
              </div>
              <div id="eufy-fw-guide-body" style="overflow-y:auto;padding:18px 22px;flex:1 1 auto;">
                ${this._firewallGuideHTML()}
              </div>
            </div>
          </div>
        `);
        $('body').append(overlay);
        const close = () => { overlay.remove(); $(document).off('keydown.eufyfw'); };
        overlay.on('click', (e) => { if (e.target === overlay[0]) close(); });
        overlay.find('#eufy-fw-close').on('click', close);
        overlay.find('#eufy-fw-print').on('click', () => this._printFirewallGuide());
        $(document).on('keydown.eufyfw', (e) => { if (e.key === 'Escape') close(); });
    }

    /** Open the guide in a clean light print window and trigger the print dialog. */
    _printFirewallGuide() {
        const w = window.open('', '_blank', 'width=900,height=820');
        if (!w) { alert('Pop-up blocked — allow pop-ups to print the guide.'); return; }
        w.document.write(
            '<!doctype html><html><head><meta charset="utf-8">' +
            '<title>Eufy firewall setup</title>' +
            '<style>body{background:#fff;color:#111;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;' +
            'max-width:780px;margin:24px auto;padding:0 18px;}</style>' +
            '</head><body>' + this._firewallGuideHTML() + '</body></html>'
        );
        w.document.close();
        w.focus();
        setTimeout(() => { try { w.print(); } catch (e) { /* user can print manually */ } }, 350);
    }
}


// Singleton — global-settings-modal.js imports and uses the same instance
// across re-renders.
export const eufyBridgeTab = new EufyBridgeTab();
