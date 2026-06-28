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
                <td style="padding:.5rem .6rem;font-weight:500;">${esc(c.nickname || c.name || '—')}</td>
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
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
            <div style="margin-top:.5rem;opacity:.6;font-size:.8em;">
                Discovery only — adding a found device to the NVR isn't wired yet.
            </div>`);
    }
};
