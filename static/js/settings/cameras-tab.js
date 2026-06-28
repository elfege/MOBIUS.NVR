/**
 * cameras-tab.js — Settings → "Cameras" tab (ES6 + jQuery).
 *
 * Two jobs, both deliberately basic (operator request 2026-06-27,
 * "UI-based LAN cameras detection and listing — make it as basic as possible"):
 *
 *   1. LIST every camera the NVR knows about, pulled live from GET /api/cameras
 *      (the authoritative DB-backed list — includes server-hidden cameras that
 *      never render as grid tiles, so this is a superset of the navbar
 *      "Filter cameras" selector, which builds its list from the DOM tiles).
 *
 *   2. SCAN the local network for camera-like devices not yet added, via
 *      POST /api/cameras/scan-lan. Discovery only for now — wiring a discovered
 *      device into the DB is a separate follow-up (there is no add-camera
 *      backend yet; cameras seed from config → DB).
 *
 * Rendering is self-contained with inline styles so the tab looks correct
 * regardless of which component CSS is loaded — no new stylesheet dependency.
 *
 * Field names mirror services/camera_repository._db_row_to_camera_config:
 * serial, name, nickname, type, stream_type, streaming_hub, host.
 */

// HTML-escape a value for safe interpolation into markup.
function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"]/g, c => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]
    ));
}

export const camerasTab = {
    $panel: null,
    _wired: false,

    /**
     * Wire the panel's buttons once. Idempotent — safe to call on every tab
     * open (the global settings modal calls init() each time the tab is shown).
     * @param {jQuery} $panel - the .settings-tab-panel[data-tab-panel="cameras"] element
     */
    init($panel) {
        this.$panel = $panel;
        if (this._wired) return;
        this._wired = true;
        // Delegated handlers survive re-renders of the list container.
        $panel.on('click', '#cameras-refresh-btn', () => this.load());
        $panel.on('click', '#cameras-scan-lan-btn', () => this.scanLan());
        $panel.on('click', '#cameras-add-btn', () => this.showAddForm());
        $panel.on('click', '#cameras-add-cancel', () => this.hideAddForm());
        $panel.on('click', '#cameras-add-test', () => this.testConnection());
        $panel.on('submit', '#cameras-add-form-el', (e) => { e.preventDefault(); this.submitAddForm(); });
        // "Add" button next to a scanner-discovered NEW device → prefill host.
        $panel.on('click', '.cameras-scan-add-btn', (e) => {
            this.showAddForm({ host: String($(e.currentTarget).data('ip') || '') });
        });
    },

    /**
     * Fetch and render the full camera list from the DB-backed endpoint.
     */
    async load() {
        const $list = this.$panel.find('#cameras-tab-list');
        $list.html('<div style="padding:1rem;opacity:.7;">Loading cameras…</div>');
        try {
            const res = await fetch('/api/cameras', { headers: { Accept: 'application/json' } });
            if (!res.ok) throw new Error('HTTP ' + res.status);
            const data = await res.json();
            // /api/cameras returns `all` as a dict keyed by serial (not an array).
            const all = (data && data.all) || {};
            this._renderList($list, Array.isArray(all) ? all : Object.values(all));
        } catch (e) {
            $list.html(
                `<div style="padding:1rem;color:#dc3545;">Failed to load cameras: ${esc(e.message || e)}</div>`
            );
        }
    },

    /**
     * Render the camera table into the list container.
     * @param {jQuery} $list
     * @param {Array<object>} cams - camera config dicts from /api/cameras .all
     */
    _renderList($list, cams) {
        if (!cams.length) {
            $list.html('<div style="padding:1rem;opacity:.7;">No cameras configured.</div>');
            return;
        }
        const sorted = [...cams].sort((a, b) =>
            String(a.name || a.serial || '').localeCompare(String(b.name || b.serial || ''))
        );
        const rows = sorted.map(c => `
            <tr style="border-bottom:1px solid rgba(255,255,255,.07);">
                <td style="padding:.5rem .6rem;font-weight:500;">${esc(c.name || c.nickname || '—')}</td>
                <td style="padding:.5rem .6rem;font-family:monospace;font-size:.8em;opacity:.85;">${esc(c.serial)}</td>
                <td style="padding:.5rem .6rem;">${esc(c.type || '—')}</td>
                <td style="padding:.5rem .6rem;">${esc(c.stream_type || '—')}</td>
                <td style="padding:.5rem .6rem;">${esc(c.streaming_hub || '—')}</td>
                <td style="padding:.5rem .6rem;font-family:monospace;font-size:.8em;opacity:.85;">${esc(c.host || '—')}</td>
            </tr>`).join('');
        $list.html(`
            <div style="margin:.25rem 0 .5rem;opacity:.7;font-size:.85em;">
                ${sorted.length} camera${sorted.length === 1 ? '' : 's'} configured
            </div>
            <div style="overflow-x:auto;">
                <table style="width:100%;border-collapse:collapse;font-size:.9em;">
                    <thead>
                        <tr style="text-align:left;opacity:.6;font-size:.8em;text-transform:uppercase;letter-spacing:.03em;">
                            <th style="padding:.4rem .6rem;">Name</th>
                            <th style="padding:.4rem .6rem;">Serial</th>
                            <th style="padding:.4rem .6rem;">Type</th>
                            <th style="padding:.4rem .6rem;">Stream</th>
                            <th style="padding:.4rem .6rem;">Hub</th>
                            <th style="padding:.4rem .6rem;">Host</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>`);
    },

    /**
     * Probe the local network for camera-like devices (RTSP/ONVIF ports open)
     * and render the result. Discovery only — does not add anything.
     */
    async scanLan() {
        const $res = this.$panel.find('#cameras-scan-results');
        const $btn = this.$panel.find('#cameras-scan-lan-btn');
        $btn.prop('disabled', true).text('Scanning…');
        $res.show().html(
            '<div style="padding:.75rem;opacity:.7;">Scanning the local network for cameras… (can take ~20s)</div>'
        );
        try {
            const res = await fetch('/api/cameras/scan-lan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            if (!res.ok) throw new Error('HTTP ' + res.status);
            this._renderScan($res, await res.json());
        } catch (e) {
            $res.html(`<div style="padding:.75rem;color:#dc3545;">Scan failed: ${esc(e.message || e)}</div>`);
        } finally {
            $btn.prop('disabled', false).text('Scan LAN');
        }
    },

    /**
     * Render LAN scan results.
     * @param {jQuery} $res
     * @param {object} data - { subnet, devices: [{ ip, vendor, ports, already_known }] }
     */
    _renderScan($res, data) {
        const found = (data && data.devices) || [];
        const subnet = data && data.subnet ? ` on ${esc(data.subnet)}` : '';
        if (!found.length) {
            $res.html(
                `<div style="padding:.75rem;opacity:.7;">No camera-like devices found${subnet}.</div>`
            );
            return;
        }
        const rows = found.map(d => `
            <tr style="border-bottom:1px solid rgba(255,255,255,.07);">
                <td style="padding:.4rem .6rem;font-family:monospace;">${esc(d.ip)}</td>
                <td style="padding:.4rem .6rem;">${esc(d.vendor || d.name || '—')}</td>
                <td style="padding:.4rem .6rem;">${(d.ports || []).map(esc).join(', ') || '—'}</td>
                <td style="padding:.4rem .6rem;">${d.already_known
                    ? '<span style="opacity:.6;">known</span>'
                    : '<span style="color:#28a745;">new</span>'}</td>
                <td style="padding:.4rem .6rem;">${d.already_known ? ''
                    : `<button type="button" class="cameras-scan-add-btn setting-btn setting-btn-primary" data-ip="${esc(d.ip)}" style="padding:.2rem .6rem;font-size:.85em;"><i class="fas fa-plus"></i> Add</button>`}</td>
            </tr>`).join('');
        $res.html(`
            <div style="margin:.5rem 0;opacity:.7;font-size:.85em;">${found.length} device${found.length === 1 ? '' : 's'} responded${subnet}</div>
            <table style="width:100%;border-collapse:collapse;font-size:.9em;">
                <thead>
                    <tr style="text-align:left;opacity:.6;font-size:.8em;text-transform:uppercase;">
                        <th style="padding:.4rem .6rem;">IP</th>
                        <th style="padding:.4rem .6rem;">Vendor</th>
                        <th style="padding:.4rem .6rem;">Open ports</th>
                        <th style="padding:.4rem .6rem;">Status</th>
                        <th style="padding:.4rem .6rem;"></th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
            <div style="margin-top:.5rem;opacity:.6;font-size:.8em;">
                Click <strong>Add</strong> on a <span style="color:#28a745;">new</span> device to register it
                (it pre-fills the host — you supply the serial, name, vendor and credentials).
            </div>`);
    },

    // ── Add Camera form ──────────────────────────────────────────────────────

    /** Render + reveal the add-camera form, optionally pre-filled (e.g. from a scan). */
    showAddForm(prefill = {}) {
        const $form = this.$panel.find('#cameras-add-form');
        $form.html(this._renderAddForm(prefill)).show();
        $form.find('#cam-add-serial').trigger('focus');
        if ($form[0]) $form[0].scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    },

    hideAddForm() {
        this.$panel.find('#cameras-add-form').hide().empty();
    },

    /**
     * Build the add-camera form. Vendor is limited to RTSP/ONVIF types the
     * create endpoint accepts; Eufy (P2P) is intentionally excluded.
     */
    _renderAddForm(p = {}) {
        const v = (k) => esc(p[k] || '');
        const inp = 'padding:.4rem;border-radius:4px;border:1px solid rgba(255,255,255,.15);background:#1a1f29;color:#e8eef7;width:100%;';
        const lbl = 'display:flex;flex-direction:column;gap:.2rem;font-size:.8em;opacity:.9;';
        const o = (val, label, sel) => `<option value="${esc(val)}"${(sel || '') === val ? ' selected' : ''}>${esc(label)}</option>`;
        const t = p.type || 'amcrest';
        const st = p.stream_type || 'LL_HLS';
        const hub = p.streaming_hub || 'mediamtx';
        return `
        <form id="cameras-add-form-el" style="background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:1rem;">
            <div style="font-weight:600;margin-bottom:.75rem;"><i class="fas fa-plus"></i> Add Camera</div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:.6rem .9rem;">
                <label style="${lbl}">Serial *<input id="cam-add-serial" name="serial" required value="${v('serial')}" style="${inp}"></label>
                <label style="${lbl}">Name *<input name="name" required value="${v('name')}" style="${inp}"></label>
                <label style="${lbl}">Vendor *<select name="type" style="${inp}">${o('amcrest', 'Amcrest', t)}${o('sv3c', 'SV3C', t)}${o('reolink', 'Reolink', t)}${o('unifi', 'UniFi', t)}</select></label>
                <label style="${lbl}">Host / IP<input name="host" value="${v('host')}" placeholder="192.168.10.x" style="${inp}"></label>
                <label style="${lbl}">ONVIF port<input name="onvif_port" type="number" value="${v('onvif_port')}" placeholder="8000" style="${inp}"></label>
                <label style="${lbl}">Stream type<select name="stream_type" style="${inp}">${o('LL_HLS', 'LL-HLS', st)}${o('HLS', 'HLS', st)}${o('WEBRTC', 'WebRTC', st)}${o('GO2RTC', 'go2rtc', st)}${o('MJPEG', 'MJPEG', st)}</select></label>
                <label style="${lbl}">Hub<select name="streaming_hub" style="${inp}">${o('mediamtx', 'MediaMTX', hub)}${o('go2rtc', 'go2rtc', hub)}${o('native_mjpeg', 'native_mjpeg', hub)}</select></label>
                <label style="${lbl}">Username<input name="username" autocomplete="off" value="${v('username')}" style="${inp}"></label>
                <label style="${lbl}">Password<input name="password" type="password" autocomplete="new-password" style="${inp}"></label>
            </div>
            <div id="cameras-add-msg" style="margin-top:.6rem;font-size:.85em;min-height:1.1em;"></div>
            <div style="display:flex;gap:8px;margin-top:.5rem;">
                <button type="submit" class="setting-btn setting-btn-primary"><i class="fas fa-check"></i> Add</button>
                <button type="button" id="cameras-add-test" class="setting-btn setting-btn-secondary"><i class="fas fa-plug"></i> Test</button>
                <button type="button" id="cameras-add-cancel" class="setting-btn setting-btn-secondary">Cancel</button>
            </div>
            <div style="margin-top:.5rem;font-size:.75em;opacity:.6;">
                Eufy cameras are added via the <strong>Eufy Bridge</strong> tab (P2P, not RTSP).
                Serial is the camera's canonical ID — find it on the device label or vendor app.
            </div>
        </form>`;
    },

    /**
     * Test reachability + ONVIF credentials for the entered host WITHOUT adding.
     * Shows ✓ (authenticated) / ✗ (auth failed or unreachable) / ◐ (reachable,
     * auth not verified) + reason. On ONVIF success, pre-fills the serial if
     * empty (the camera reports its own serial via GetDeviceInformation).
     */
    async testConnection() {
        const $form = this.$panel.find('#cameras-add-form-el');
        const $msg = this.$panel.find('#cameras-add-msg');
        const val = (n) => String($form.find(`[name="${n}"]`).val() || '').trim();
        const host = val('host');
        if (!host) {
            $msg.html('<span style="color:#dc3545;">Enter a Host / IP to test.</span>');
            return;
        }
        const $btn = this.$panel.find('#cameras-add-test');
        $btn.prop('disabled', true).text('Testing…');
        $msg.html('<span style="opacity:.7;">Testing connection…</span>');
        try {
            const res = await fetch('/api/cameras/test-connection', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    host,
                    onvif_port: val('onvif_port'),
                    username: val('username'),
                    password: val('password')
                })
            });
            const d = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(d.error || ('HTTP ' + res.status));
            let color, icon;
            if (d.authenticated === true) { color = '#28a745'; icon = '✓'; }
            else if (d.authenticated === false) { color = '#dc3545'; icon = '✗'; }
            else if (d.reachable) { color = '#ff9800'; icon = '◐'; }
            else { color = '#dc3545'; icon = '✗'; }
            $msg.html(`<span style="color:${color};">${icon} ${esc(d.detail || '')}</span>`);
            // ONVIF reported a serial — pre-fill it if the operator hasn't typed one.
            if (d.serial && !val('serial')) {
                $form.find('[name="serial"]').val(d.serial);
            }
        } catch (e) {
            $msg.html(`<span style="color:#dc3545;">Test failed: ${esc(e.message || e)}</span>`);
        } finally {
            $btn.prop('disabled', false).html('<i class="fas fa-plug"></i> Test');
        }
    },

    /** POST the form to /api/cameras, then refresh the list on success. */
    async submitAddForm() {
        const $form = this.$panel.find('#cameras-add-form-el');
        const $msg = this.$panel.find('#cameras-add-msg');
        const val = (n) => String($form.find(`[name="${n}"]`).val() || '').trim();
        const payload = {
            serial: val('serial'), name: val('name'), type: val('type'),
            host: val('host'), onvif_port: val('onvif_port'),
            stream_type: val('stream_type'), streaming_hub: val('streaming_hub'),
            username: val('username'), password: val('password')
        };
        if (!payload.serial || !payload.name || !payload.type) {
            $msg.html('<span style="color:#dc3545;">Serial, name and vendor are required.</span>');
            return;
        }
        const $submit = $form.find('button[type="submit"]');
        $submit.prop('disabled', true).text('Adding…');
        $msg.html('<span style="opacity:.7;">Adding camera…</span>');
        try {
            const res = await fetch('/api/cameras', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok || !data.success) throw new Error(data.error || ('HTTP ' + res.status));
            this.hideAddForm();
            this.load();
        } catch (e) {
            $msg.html(`<span style="color:#dc3545;">Failed: ${esc(e.message || e)}</span>`);
            $submit.prop('disabled', false).html('<i class="fas fa-check"></i> Add');
        }
    }
};
