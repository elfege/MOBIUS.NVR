/**
 * audit-log-modal.js — fullscreen reader for setting_audit_log.
 *
 * Admin-only. Opened via the "Open Audit Log" button in the Logs tab of
 * the global settings modal. UI per operator spec 2026-05-13:
 *   - Near-fullscreen overlay (99% backdrop)
 *   - ESC or × in corner closes
 *   - Filters: time range, origin, scope (free text), client_id, search
 *   - Paginated table with JSON-diff expansion
 *   - CSV export
 */

const ENDPOINT = '/api/audit/log';

export const auditLogModal = {

    open() {
        // Lazy-create the modal element on first open.
        if (!$('#audit-log-modal').length) this._mount();
        this._loadFilters();
        $('#audit-log-modal').addClass('active');
        $('body').css('overflow', 'hidden');
        // ESC handler
        if (!this._escBound) {
            $(document).on('keydown.audit-log', (e) => {
                if (e.key === 'Escape' && $('#audit-log-modal').hasClass('active')) {
                    this.close();
                }
            });
            this._escBound = true;
        }
        this.refresh();
    },

    close() {
        $('#audit-log-modal').removeClass('active');
        $('body').css('overflow', '');
    },

    _mount() {
        const html = `
        <div id="audit-log-modal" class="audit-log-modal-overlay">
            <div class="audit-log-modal-panel">
                <div class="audit-log-modal-header">
                    <h2><i class="fas fa-history"></i> Settings Audit Log</h2>
                    <button class="audit-log-close-btn" type="button" title="Close (Esc)">
                        <i class="fas fa-times"></i>
                    </button>
                </div>
                <div class="audit-log-modal-filters">
                    <label>From <input type="datetime-local" id="audit-filter-from"></label>
                    <label>To   <input type="datetime-local" id="audit-filter-to"></label>
                    <label>Origin
                        <select id="audit-filter-origin">
                            <option value="">any</option>
                            <option value="ui">ui</option>
                            <option value="api">api</option>
                            <option value="system_auto">system_auto</option>
                            <option value="trigger">trigger</option>
                        </select>
                    </label>
                    <label>Scope <input type="text" id="audit-filter-scope" placeholder="cameras or cameras:T8416...">
                    </label>
                    <label>Search <input type="text" id="audit-filter-q" placeholder="key, note, value..."></label>
                    <button id="audit-filter-apply" class="setting-btn setting-btn-primary">Apply</button>
                    <button id="audit-export-csv" class="setting-btn setting-btn-secondary">
                        <i class="fas fa-download"></i> CSV
                    </button>
                </div>
                <div class="audit-log-modal-status" id="audit-log-status"></div>
                <div class="audit-log-modal-body">
                    <table class="audit-log-table">
                        <thead>
                            <tr>
                                <th style="width:160px;">When (UTC)</th>
                                <th style="width:60px;">Origin</th>
                                <th style="width:80px;">User</th>
                                <th style="width:120px;">Device</th>
                                <th style="width:140px;">Scope</th>
                                <th style="width:140px;">Key</th>
                                <th>Change</th>
                            </tr>
                        </thead>
                        <tbody id="audit-log-rows">
                        </tbody>
                    </table>
                </div>
                <div class="audit-log-modal-footer">
                    <button id="audit-prev-page" class="setting-btn setting-btn-secondary">‹ Prev</button>
                    <span id="audit-page-info"></span>
                    <button id="audit-next-page" class="setting-btn setting-btn-secondary">Next ›</button>
                </div>
            </div>
        </div>
        <style>
            .audit-log-modal-overlay {
                position: fixed; inset: 0; background: rgba(0,0,0,0.99);
                z-index: 100000; display: none;
            }
            .audit-log-modal-overlay.active { display: block; }
            .audit-log-modal-panel {
                position: absolute; inset: 1%;
                background: #16181d;
                border-radius: 10px;
                display: flex; flex-direction: column;
                overflow: hidden;
            }
            .audit-log-modal-header {
                display: flex; align-items: center; justify-content: space-between;
                padding: 14px 22px;
                background: linear-gradient(180deg, #1f2530 0%, #161a21 100%);
                border-bottom: 1px solid #2a2f3a;
            }
            .audit-log-modal-header h2 { margin: 0; color: #eee; font-size: 18px; font-weight: 600; }
            .audit-log-close-btn {
                background: none; border: none; color: #ccc; font-size: 22px; cursor: pointer;
            }
            .audit-log-modal-filters {
                padding: 12px 22px;
                display: flex; flex-wrap: wrap; gap: 14px;
                background: #1a1d24;
                border-bottom: 1px solid #2a2f3a;
                color: #ccc; font-size: 13px;
            }
            .audit-log-modal-filters label {
                display: flex; flex-direction: column; gap: 4px;
                font-size: 11px; color: #888; text-transform: uppercase;
            }
            .audit-log-modal-filters input,
            .audit-log-modal-filters select {
                background: #0f1217; color: #ddd; border: 1px solid #2a2f3a;
                border-radius: 4px; padding: 5px 8px; font-size: 13px;
            }
            .audit-log-modal-status { padding: 4px 22px; color: #888; font-size: 12px; }
            .audit-log-modal-body { flex: 1; overflow: auto; padding: 8px 22px; }
            .audit-log-table { width: 100%; border-collapse: collapse; font-size: 12px; }
            .audit-log-table th, .audit-log-table td {
                text-align: left; padding: 6px 8px; border-bottom: 1px solid #232831;
                color: #ddd; vertical-align: top;
            }
            .audit-log-table th { color: #888; font-weight: 600; text-transform: uppercase; font-size: 11px;
                position: sticky; top: 0; background: #16181d; }
            .audit-log-table .change-cell { font-family: monospace; white-space: pre-wrap; max-width: 600px; }
            .audit-log-table tr:hover { background: rgba(255,255,255,0.03); cursor: pointer; }
            .audit-log-table tr.expanded { background: rgba(255,255,255,0.05); }
            .audit-log-modal-footer {
                padding: 10px 22px; display: flex; gap: 12px; align-items: center;
                background: #1a1d24; border-top: 1px solid #2a2f3a; color: #ccc;
                font-size: 13px;
            }
            #audit-page-info { flex: 1; text-align: center; }
        </style>`;
        $('body').append(html);

        // Bind handlers ONCE.
        $('#audit-log-modal').on('click', (e) => {
            if (e.target.id === 'audit-log-modal') this.close();
        });
        $('.audit-log-close-btn').on('click', () => this.close());
        $('#audit-filter-apply').on('click', () => { this._offset = 0; this.refresh(); });
        $('#audit-filter-q').on('keydown', (e) => {
            if (e.key === 'Enter') { this._offset = 0; this.refresh(); }
        });
        $('#audit-prev-page').on('click', () => this._page(-1));
        $('#audit-next-page').on('click', () => this._page(+1));
        $('#audit-export-csv').on('click', () => this._exportCsv());
        $('#audit-log-rows').on('click', 'tr', (e) => {
            const $tr = $(e.currentTarget);
            const $next = $tr.next('.audit-log-expand-row');
            if ($next.length) { $next.remove(); $tr.removeClass('expanded'); return; }
            const data = $tr.data('row');
            if (!data) return;
            const json = JSON.stringify({
                old_value: data.old_value, new_value: data.new_value, note: data.note,
            }, null, 2);
            $tr.addClass('expanded').after(
                `<tr class="audit-log-expand-row"><td colspan="7"><pre style="margin:0;color:#9cf;font-size:11px;white-space:pre-wrap;">${this._esc(json)}</pre></td></tr>`
            );
        });
    },

    _offset: 0,
    _limit: 100,
    _total: 0,

    _loadFilters() {
        // Default: last 24h
        const now = new Date();
        const to = now;
        const from = new Date(now.getTime() - 24 * 60 * 60 * 1000);
        if (!$('#audit-filter-from').val()) $('#audit-filter-from').val(this._toLocalInput(from));
        if (!$('#audit-filter-to').val())   $('#audit-filter-to').val(this._toLocalInput(to));
    },

    _toLocalInput(d) {
        const pad = (n) => String(n).padStart(2, '0');
        return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    },

    async _page(delta) {
        const next = this._offset + delta * this._limit;
        if (next < 0) return;
        if (next >= this._total) return;
        this._offset = next;
        await this.refresh();
    },

    async refresh() {
        const params = new URLSearchParams();
        const from = $('#audit-filter-from').val();
        const to   = $('#audit-filter-to').val();
        if (from) params.set('from', new Date(from).toISOString());
        if (to)   params.set('to',   new Date(to).toISOString());
        const origin = $('#audit-filter-origin').val();
        if (origin) params.set('origin', origin);
        const scope = $('#audit-filter-scope').val().trim();
        if (scope) params.set('scope', scope);
        const q = $('#audit-filter-q').val().trim();
        if (q) params.set('q', q);
        params.set('limit', this._limit);
        params.set('offset', this._offset);

        $('#audit-log-status').text('Loading…');
        try {
            const r = await fetch(`${ENDPOINT}?${params}`, { credentials: 'same-origin' });
            if (!r.ok) {
                $('#audit-log-status').text(`HTTP ${r.status}`);
                return;
            }
            const data = await r.json();
            this._total = data.total || 0;
            const rows = data.rows || [];
            this._render(rows);
            $('#audit-log-status').text(`Showing ${this._offset+1}–${this._offset+rows.length} of ${this._total}`);
            $('#audit-page-info').text(`Page ${Math.floor(this._offset/this._limit)+1} / ${Math.max(1, Math.ceil(this._total/this._limit))}`);
        } catch (e) {
            $('#audit-log-status').text(`Error: ${e.message}`);
        }
    },

    _render(rows) {
        const $tbody = $('#audit-log-rows').empty();
        if (rows.length === 0) {
            $tbody.append('<tr><td colspan="7" style="text-align:center;color:#888;padding:30px;">No entries match these filters.</td></tr>');
            return;
        }
        for (const r of rows) {
            const ts = r.ts ? r.ts.slice(0, 19).replace('T', ' ') : '';
            const change = this._summarizeChange(r);
            const scope = (r.row_pk ? `${r.table_name}:${r.row_pk}` : (r.table_name || ''));
            const $tr = $(`
                <tr>
                    <td>${this._esc(ts)}</td>
                    <td>${this._esc(r.origin || '')}</td>
                    <td>${this._esc(r.user_id || '')}</td>
                    <td title="${this._esc(r.client_id || '')}">${this._esc((r.client_id || '').slice(0, 8))}</td>
                    <td>${this._esc(scope)}</td>
                    <td>${this._esc(r.setting_key || '')}</td>
                    <td class="change-cell">${this._esc(change)}</td>
                </tr>
            `);
            $tr.data('row', r);
            $tbody.append($tr);
        }
    },

    _summarizeChange(r) {
        if (!r.new_value && !r.old_value) return '';
        try {
            const keys = Object.keys(r.new_value || {});
            if (keys.length === 1) {
                const k = keys[0];
                const oldv = r.old_value ? r.old_value[k] : '∅';
                const newv = r.new_value[k];
                return `${k}: ${JSON.stringify(oldv)} → ${JSON.stringify(newv)}`;
            }
            return `${keys.length} fields changed (click for diff)`;
        } catch (_) {
            return 'click for diff';
        }
    },

    _esc(s) {
        if (s == null) return '';
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    },

    async _exportCsv() {
        // Re-fetch with no limit/offset (server caps at MAX_LIMIT=1000 anyway).
        const params = new URLSearchParams();
        const from = $('#audit-filter-from').val();
        const to   = $('#audit-filter-to').val();
        if (from) params.set('from', new Date(from).toISOString());
        if (to)   params.set('to',   new Date(to).toISOString());
        const origin = $('#audit-filter-origin').val();
        if (origin) params.set('origin', origin);
        const scope = $('#audit-filter-scope').val().trim();
        if (scope) params.set('scope', scope);
        const q = $('#audit-filter-q').val().trim();
        if (q) params.set('q', q);
        params.set('limit', 1000);

        const r = await fetch(`${ENDPOINT}?${params}`, { credentials: 'same-origin' });
        if (!r.ok) return;
        const data = await r.json();
        const rows = data.rows || [];
        const header = ['id','ts','origin','user_id','client_id','table_name','row_pk','setting_key','old_value','new_value','note'];
        const csv = [header.join(',')];
        const escCsv = (v) => {
            const s = v == null ? '' : (typeof v === 'object' ? JSON.stringify(v) : String(v));
            return /[,"\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
        };
        for (const r of rows) {
            csv.push(header.map(h => escCsv(r[h])).join(','));
        }
        const blob = new Blob([csv.join('\n')], { type: 'text/csv' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `audit-log-${new Date().toISOString().slice(0,19).replace(/[:.]/g,'')}.csv`;
        a.click();
        URL.revokeObjectURL(url);
    },
};
