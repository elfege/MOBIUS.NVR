/**
 * DATA TAB (ES6 + jQuery)
 * ========================
 *
 * Renders and manages the "Data" tab in the global settings modal. Admin-only.
 *
 * Two purposes:
 *
 *   1. **Storage overview** — disk-space widget reusing the existing
 *      ``/api/storage/stats`` endpoint (admin-gated, already in production).
 *      No new utility, just consumes what's there.
 *
 *   2. **Telemetry event log control** — admin toggles the per-layer
 *      telemetry event log on/off, sets the max DB size cap, and picks
 *      the retention window. Backed by ``/api/telemetry/{settings,usage}``,
 *      which themselves enforce ``role='admin'`` server-side.
 *
 * The feature is **disabled by default** — fresh installs and existing
 * systems boot with ``telemetry_enabled = false``. The toggle in this tab
 * is the only way to flip it on.
 *
 * API
 * ---
 *
 *   GET  /api/telemetry/settings
 *   POST /api/telemetry/settings   { enabled?, max_size_mb?, retention_days? }
 *   GET  /api/telemetry/usage
 *   GET  /api/storage/stats        (existing endpoint; admin-only)
 *
 * Architecture
 * ------------
 *
 *   * Same ES6 + jQuery + singleton-class pattern as evidence-collection.js
 *     and eufy-bridge.js (user profile §3.10.5).
 *   * ``renderHTML()`` returns static HTML; ``load()`` fills in live values.
 *   * ``init($panel)`` wires event handlers (idempotent — safe to call on
 *     every tab open).
 */

export class DataTab {
    constructor() {
        this.$panel = null;

        // Latest settings + usage payloads, kept here so the change-tracker
        // can compute deltas without re-fetching.
        this._settings = null;
        this._usage = null;

        // Pending changes from the form, applied on Save Settings click.
        this._pending = {};
    }

    /**
     * Static HTML for the tab. Values get filled in by load().
     */
    renderHTML() {
        return `
        <div class="settings-tab-panel" data-tab-panel="data">

            <!-- ── Storage overview ─────────────────────────────────────── -->
            <div class="setting-row" style="border-left-color:#4a90e2;">
                <div class="setting-top">
                    <div class="setting-label">
                        <i class="fas fa-hdd" style="color:#4a90e2;"></i> Storage overview
                    </div>
                </div>
                <div class="setting-description" style="margin-bottom:8px;">
                    Disk-space usage on the volumes the NVR writes to.
                    Sourced from the existing storage status endpoint.
                </div>
                <div id="data-tab-storage-overview" style="font-family:monospace;font-size:12px;line-height:1.6;color:#ccc;">
                    <em style="color:#888;">Loading…</em>
                </div>
            </div>

            <!-- ── Telemetry event log ──────────────────────────────────── -->
            <div class="setting-row" style="border-left-color:#28a745;">
                <div class="setting-top">
                    <div class="setting-label">
                        <i class="fas fa-database" style="color:#28a745;"></i> Telemetry event log
                    </div>
                    <div class="setting-control">
                        <label class="setting-toggle">
                            <input type="checkbox" id="data-tab-telemetry-toggle">
                            <span class="toggle-slider"></span>
                        </label>
                        <span id="data-tab-telemetry-status" style="font-size:12px;color:#888;margin-left:8px;"></span>
                    </div>
                </div>
                <div class="setting-description">
                    Records state transitions (camera state, publisher state,
                    MediaMTX path lifecycle, RTSP probe pass/fail) and periodic
                    resource snapshots (FFmpeg subprocess count, gunicorn worker
                    RSS, Docker conntrack table size) so long-uptime streaming
                    failures can be localized to a specific layer.
                    <br>
                    <strong>Disabled by default.</strong> When off, no probe
                    runs and no row is written.
                </div>
            </div>

            <!-- ── Max size cap ─────────────────────────────────────────── -->
            <div class="setting-row">
                <div class="setting-top">
                    <div class="setting-label"><i class="fas fa-weight"></i> Max telemetry table size</div>
                    <div class="setting-control" style="display:flex;align-items:center;gap:8px;">
                        <input id="data-tab-max-size-slider" type="range"
                               min="10" max="2048" step="10" value="100"
                               style="width:180px;">
                        <input id="data-tab-max-size-number" type="number"
                               min="10" max="2048" step="10" value="100"
                               style="width:80px;"> MB
                    </div>
                </div>
                <div class="setting-description">
                    Maximum disk space for the <code>telemetry_events</code> table.
                    Range: 10 MB – 2 GB. Cleanup runs hourly and starts pruning
                    the oldest rows when usage reaches 90 % of the cap.
                    <br>
                    Current usage: <span id="data-tab-usage-text">—</span>
                </div>
            </div>

            <!-- ── Retention ───────────────────────────────────────────── -->
            <div class="setting-row">
                <div class="setting-top">
                    <div class="setting-label"><i class="fas fa-history"></i> Retention window</div>
                    <div class="setting-control">
                        <label style="font-size:12px;margin-right:10px;">
                            <input type="radio" name="data-tab-retention" value="1"> 24 hours
                        </label>
                        <label style="font-size:12px;margin-right:10px;">
                            <input type="radio" name="data-tab-retention" value="7"> 7 days
                        </label>
                        <label style="font-size:12px;">
                            <input type="radio" name="data-tab-retention" value="30"> 30 days
                        </label>
                    </div>
                </div>
                <div class="setting-description">
                    Events older than the window are deleted regardless of the
                    size cap. The size cap and the retention window are
                    independent ceilings; whichever triggers first wins.
                </div>
            </div>

            <!-- Save status only — the modal's global Save button is the
                 single save trigger across all tabs. Changing tab also
                 auto-flushes any pending Data tab changes. -->
            <div class="setting-row" style="border-left:none;">
                <div class="setting-top">
                    <div class="setting-label"></div>
                    <div class="setting-control">
                        <span id="data-tab-save-status" style="font-size:12px;color:#888;"></span>
                    </div>
                </div>
            </div>

        </div>
        `;
    }

    /**
     * Wire event handlers. Idempotent — namespaced via .data-tab so re-init
     * doesn't double-bind.
     */
    init($panel) {
        this.$panel = $panel;

        // Toggle
        $panel.find('#data-tab-telemetry-toggle').off('change.data-tab').on('change.data-tab', (e) => {
            this._pending.enabled = $(e.currentTarget).is(':checked');
        });

        // Slider <-> number two-way bind
        $panel.find('#data-tab-max-size-slider').off('input.data-tab').on('input.data-tab', (e) => {
            const v = parseInt($(e.currentTarget).val(), 10);
            $panel.find('#data-tab-max-size-number').val(v);
            this._pending.max_size_mb = v;
        });
        $panel.find('#data-tab-max-size-number').off('input.data-tab').on('input.data-tab', (e) => {
            let v = parseInt($(e.currentTarget).val(), 10);
            if (isNaN(v)) v = 100;
            v = Math.max(10, Math.min(2048, v));
            $panel.find('#data-tab-max-size-slider').val(v);
            this._pending.max_size_mb = v;
        });

        // Retention radios
        $panel.find('input[name="data-tab-retention"]').off('change.data-tab').on('change.data-tab', (e) => {
            this._pending.retention_days = parseInt($(e.currentTarget).val(), 10);
        });
    }

    /**
     * True when at least one form field differs from the last-loaded
     * server state. The modal asks this before firing a background
     * save on tab change / global Save click.
     */
    isDirty() {
        if (!this._settings) return false;
        if ('enabled' in this._pending && this._pending.enabled !== this._settings.enabled) return true;
        if ('max_size_mb' in this._pending && this._pending.max_size_mb !== this._settings.max_size_mb) return true;
        if ('retention_days' in this._pending && this._pending.retention_days !== this._settings.retention_days) return true;
        return false;
    }

    /**
     * Save only if there are actually pending changes. Returns the
     * settings snapshot from the server on success, null on no-op or
     * failure. Called from the modal when the user switches away from
     * the Data tab or hits the global Save button.
     */
    async saveIfDirty() {
        if (!this.isDirty()) return null;
        return await this.save();
    }

    /**
     * Pull settings + usage + storage stats and render the live values.
     */
    async load() {
        if (!this.$panel) return;
        this._pending = {};
        await Promise.all([
            this._loadTelemetry(),
            this._loadStorageOverview(),
        ]);
    }

    async _loadTelemetry() {
        try {
            const [settingsRes, usageRes] = await Promise.all([
                fetch('/api/telemetry/settings').then(r => r.json()),
                fetch('/api/telemetry/usage').then(r => r.json()),
            ]);
            if (!settingsRes.success || !usageRes.success) {
                this.$panel.find('#data-tab-telemetry-status').text('Failed to load').css('color', '#dc3545');
                return;
            }
            this._settings = settingsRes.settings;
            this._usage = usageRes.usage;

            // Toggle
            this.$panel.find('#data-tab-telemetry-toggle').prop('checked', this._settings.enabled);
            this.$panel.find('#data-tab-telemetry-status').text(this._settings.enabled ? 'Enabled' : 'Disabled')
                .css('color', this._settings.enabled ? '#28a745' : '#888');

            // Max size
            this.$panel.find('#data-tab-max-size-slider').val(this._settings.max_size_mb);
            this.$panel.find('#data-tab-max-size-number').val(this._settings.max_size_mb);

            // Retention
            this.$panel.find('input[name="data-tab-retention"]').prop('checked', false);
            this.$panel.find(`input[name="data-tab-retention"][value="${this._settings.retention_days}"]`).prop('checked', true);

            // Usage line
            this.$panel.find('#data-tab-usage-text').html(
                `${this._usage.size_mb} MB of ${this._usage.cap_mb} MB &nbsp;` +
                `<span style="color:${this._usage.percent_used >= 90 ? '#ff9800' : '#888'};">` +
                `(${this._usage.percent_used}%)</span> · ${this._usage.row_count.toLocaleString()} rows`
            );
        } catch (e) {
            this.$panel.find('#data-tab-telemetry-status').text('Error: ' + e.message).css('color', '#dc3545');
        }
    }

    async _loadStorageOverview() {
        const $box = this.$panel.find('#data-tab-storage-overview');
        try {
            const res = await fetch('/api/storage/stats').then(r => r.json());
            if (!res || res.error) {
                $box.html(`<em style="color:#dc3545;">Failed to load: ${res?.error || 'unknown'}</em>`);
                return;
            }
            $box.html(this._renderStorageOverview(res));
        } catch (e) {
            $box.html(`<em style="color:#dc3545;">Error: ${e.message}</em>`);
        }
    }

    /**
     * Render two storage-tier rows (recent + archive) with progress bars
     * and a warnings strip. The /api/storage/stats payload uses
     * { recent: {...}, archive: {...}, warnings: [...], config: {...} }.
     * Read-only — migration controls live in the Storage tab, not here.
     */
    _renderStorageOverview(stats) {
        const tier = (label, t) => {
            if (!t || typeof t !== 'object') return '';
            const usedGB  = (t.used_gb  ?? 0).toFixed(1);
            const totalGB = (t.total_gb ?? 0).toFixed(1);
            const freeGB  = (t.free_gb  ?? 0).toFixed(1);
            const usedPct = Math.max(0, Math.min(100, (t.free_percent != null) ? (100 - t.free_percent) : 0));
            // Colour bands: green <70 %, amber 70-90 %, red ≥90 %.
            const bar = usedPct >= 90 ? '#dc3545' : (usedPct >= 70 ? '#ff9800' : '#28a745');
            const hostPath = t.host_path || '—';
            const recCount = (t.recording_count ?? 0).toLocaleString();
            return `
            <div style="margin-bottom:14px;">
                <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px;">
                    <span style="color:#ddd;font-weight:600;font-family:sans-serif;font-size:13px;">${label}</span>
                    <span style="color:#888;font-family:sans-serif;font-size:11px;">${hostPath}</span>
                </div>
                <div style="position:relative;background:#222;border-radius:4px;height:18px;overflow:hidden;">
                    <div style="background:${bar};width:${usedPct.toFixed(1)}%;height:100%;transition:width 0.3s ease;"></div>
                    <div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
                                font-family:sans-serif;font-size:11px;color:#fff;text-shadow:0 0 3px rgba(0,0,0,0.8);">
                        ${usedGB} / ${totalGB} GB &nbsp;(${usedPct.toFixed(1)}% used · ${freeGB} GB free)
                    </div>
                </div>
                <div style="color:#888;font-family:sans-serif;font-size:11px;margin-top:3px;">
                    ${recCount} recordings
                </div>
            </div>`;
        };
        const warningsHtml = (stats.warnings && stats.warnings.length)
            ? `<div style="margin-top:10px;padding:8px 10px;background:#3a2a14;border-left:3px solid #ff9800;
                         font-family:sans-serif;font-size:12px;color:#ffcc80;border-radius:3px;">
                 ${stats.warnings.map(w => '⚠ ' + this._escapeHtml(w)).join('<br>')}
               </div>`
            : '';
        return tier('Recent storage',  stats.recent)
             + tier('Archive storage', stats.archive)
             + warningsHtml;
    }

    _escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, c => (
            {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]
        ));
    }

    async save() {
        const $status = this.$panel ? this.$panel.find('#data-tab-save-status') : null;
        if (Object.keys(this._pending).length === 0) return null;
        if ($status) $status.text('Saving…').css('color', '#888');
        try {
            const res = await fetch('/api/telemetry/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(this._pending),
            }).then(r => r.json());
            if (!res.success) {
                if ($status) $status.text('Failed: ' + (res.error || 'unknown')).css('color', '#dc3545');
                return null;
            }
            if ($status) $status.text('Saved').css('color', '#28a745');
            await this.load();    // refresh to show server-side state of truth
            if ($status) setTimeout(() => $status.text(''), 2500);
            return res.settings;
        } catch (e) {
            if ($status) $status.text('Error: ' + e.message).css('color', '#dc3545');
            return null;
        }
    }
}

export const dataTab = new DataTab();
