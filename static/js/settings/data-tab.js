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

            <!-- ── Save ─────────────────────────────────────────────────── -->
            <div class="setting-row" style="border-left:none;">
                <div class="setting-top">
                    <div class="setting-label"></div>
                    <div class="setting-control">
                        <button id="data-tab-save-btn" class="setting-btn setting-btn-primary"
                                style="font-size:13px;padding:6px 14px;" disabled>
                            <i class="fas fa-save"></i> Save Settings
                        </button>
                        <span id="data-tab-save-status" style="font-size:12px;color:#888;margin-left:8px;"></span>
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
            this._updateSaveButton();
        });

        // Slider <-> number two-way bind
        $panel.find('#data-tab-max-size-slider').off('input.data-tab').on('input.data-tab', (e) => {
            const v = parseInt($(e.currentTarget).val(), 10);
            $panel.find('#data-tab-max-size-number').val(v);
            this._pending.max_size_mb = v;
            this._updateSaveButton();
        });
        $panel.find('#data-tab-max-size-number').off('input.data-tab').on('input.data-tab', (e) => {
            let v = parseInt($(e.currentTarget).val(), 10);
            if (isNaN(v)) v = 100;
            v = Math.max(10, Math.min(2048, v));
            $panel.find('#data-tab-max-size-slider').val(v);
            this._pending.max_size_mb = v;
            this._updateSaveButton();
        });

        // Retention radios
        $panel.find('input[name="data-tab-retention"]').off('change.data-tab').on('change.data-tab', (e) => {
            this._pending.retention_days = parseInt($(e.currentTarget).val(), 10);
            this._updateSaveButton();
        });

        // Save
        $panel.find('#data-tab-save-btn').off('click.data-tab').on('click.data-tab', () => this.save());
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
        this._updateSaveButton();
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
        try {
            const res = await fetch('/api/storage/stats').then(r => r.json());
            if (!res || res.error) {
                this.$panel.find('#data-tab-storage-overview').html(
                    `<em style="color:#dc3545;">Failed to load: ${res?.error || 'unknown'}</em>`
                );
                return;
            }
            // The /api/storage/stats payload shape varies; render whatever
            // keys it returns in a generic key→value list so we don't
            // tightly couple to its current schema.
            const lines = [];
            const fmt = (v) => {
                if (v == null) return '—';
                if (typeof v === 'object') return JSON.stringify(v);
                return String(v);
            };
            for (const [k, v] of Object.entries(res)) {
                if (k === 'success' || k === 'error') continue;
                lines.push(`<div><span style="color:#888;">${k}:</span> ${fmt(v)}</div>`);
            }
            this.$panel.find('#data-tab-storage-overview').html(
                lines.length ? lines.join('') : '<em style="color:#888;">No storage data</em>'
            );
        } catch (e) {
            this.$panel.find('#data-tab-storage-overview').html(
                `<em style="color:#dc3545;">Error: ${e.message}</em>`
            );
        }
    }

    _updateSaveButton() {
        // Enable save only when at least one field differs from the loaded value.
        if (!this._settings) {
            this.$panel.find('#data-tab-save-btn').prop('disabled', true);
            return;
        }
        let dirty = false;
        if ('enabled' in this._pending && this._pending.enabled !== this._settings.enabled) dirty = true;
        if ('max_size_mb' in this._pending && this._pending.max_size_mb !== this._settings.max_size_mb) dirty = true;
        if ('retention_days' in this._pending && this._pending.retention_days !== this._settings.retention_days) dirty = true;
        this.$panel.find('#data-tab-save-btn').prop('disabled', !dirty);
    }

    async save() {
        const $btn = this.$panel.find('#data-tab-save-btn');
        const $status = this.$panel.find('#data-tab-save-status');
        if (Object.keys(this._pending).length === 0) return;
        $btn.prop('disabled', true);
        $status.text('Saving…').css('color', '#888');
        try {
            const res = await fetch('/api/telemetry/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(this._pending),
            }).then(r => r.json());
            if (!res.success) {
                $status.text('Failed: ' + (res.error || 'unknown')).css('color', '#dc3545');
                return;
            }
            $status.text('Saved').css('color', '#28a745');
            await this.load();    // refresh to show server-side state of truth
            setTimeout(() => $status.text(''), 2500);
        } catch (e) {
            $status.text('Error: ' + e.message).css('color', '#dc3545');
        }
    }
}

export const dataTab = new DataTab();
