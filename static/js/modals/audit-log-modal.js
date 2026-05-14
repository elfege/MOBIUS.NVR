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

const ENDPOINT          = '/api/audit/log';
const UI_EVENT_ENDPOINT = '/api/ui-event/log';

/**
 * Source dropdown values:
 *   'settings'  → /api/audit/log only (legacy default)
 *   'ui'        → /api/ui-event/log only
 *   'both'      → fetch both, interleave by ts DESC, then paginate locally
 *                 (server-side pagination of a UNION ALL would need a new
 *                 endpoint; for v1 we merge in the client and pick the
 *                 top-N. The "total" we show in 'both' is the SUM of the
 *                 two totals, which slightly overcounts when local
 *                 slicing drops rows past the limit — acceptable.)
 */
const SOURCE_SETTINGS = 'settings';
const SOURCE_UI       = 'ui';
const SOURCE_BOTH     = 'both';

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
                    <label>Source
                        <select id="audit-filter-source">
                            <option value="settings">Settings</option>
                            <option value="ui">UI events</option>
                            <option value="both">Both</option>
                        </select>
                    </label>
                    <label>From <input type="datetime-local" id="audit-filter-from"></label>
                    <label>To   <input type="datetime-local" id="audit-filter-to"></label>
                    <label class="audit-filter-settings-only">Origin
                        <select id="audit-filter-origin">
                            <option value="">any</option>
                            <option value="ui">ui</option>
                            <option value="api">api</option>
                            <option value="system_auto">system_auto</option>
                            <option value="trigger">trigger</option>
                        </select>
                    </label>
                    <label class="audit-filter-ui-only">Kind
                        <select id="audit-filter-kind">
                            <option value="">any</option>
                            <option value="click">click</option>
                            <option value="keystroke">keystroke</option>
                            <option value="focus">focus</option>
                            <option value="blur">blur</option>
                            <option value="submit">submit</option>
                            <option value="navigation">navigation</option>
                            <option value="modal_open">modal_open</option>
                            <option value="modal_close">modal_close</option>
                            <option value="scroll">scroll</option>
                        </select>
                    </label>
                    <label class="audit-filter-settings-only">Scope
                        <input type="text" id="audit-filter-scope" placeholder="cameras or cameras:T8416...">
                    </label>
                    <label class="audit-filter-ui-only">Target id
                        <input type="text" id="audit-filter-target-id" placeholder="DOM id (exact)">
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
                                <th style="width:160px;">When</th>
                                <th style="width:80px;" id="audit-col-origin-kind">Origin</th>
                                <th style="width:80px;">User</th>
                                <th style="width:120px;">Device</th>
                                <th style="width:160px;" id="audit-col-scope-target">Scope</th>
                                <th style="width:140px;" id="audit-col-key-page">Key</th>
                                <th id="audit-col-change-summary">Change</th>
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
        // 2026-05-13: Source dropdown — switch endpoint + relabel columns +
        // toggle which filters apply. Triggers an immediate refresh so the
        // operator doesn't need to also click Apply.
        $('#audit-filter-source').on('change', () => {
            this._offset = 0;
            this._applySourceUI();
            this.refresh();
        });
        $('#audit-filter-target-id').on('keydown', (e) => {
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
            const drow = $tr.data('drow');
            if (!data) return;
            // Source-aware expansion: settings rows show the diff;
            // ui_event rows show the full row (target_attrs, extra, etc.).
            let payload;
            if (drow && drow._source === 'ui') {
                payload = {
                    kind:         data.kind,
                    target_id:    data.target_id,
                    target_tag:   data.target_tag,
                    target_text:  data.target_text,
                    target_attrs: data.target_attrs,
                    page_url:     data.page_url,
                    extra:        data.extra,
                    host_label:   data.host_label,
                };
            } else {
                payload = {
                    old_value: data.old_value,
                    new_value: data.new_value,
                    note:      data.note,
                };
            }
            const json = JSON.stringify(payload, null, 2);
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
        // Apply current source's UI state (column labels, visible filters).
        this._applySourceUI();
    },

    /**
     * Get the currently-selected source.
     * Returns one of: 'settings' | 'ui' | 'both'.
     */
    _currentSource() {
        const v = $('#audit-filter-source').val();
        if (v === SOURCE_UI || v === SOURCE_BOTH) return v;
        return SOURCE_SETTINGS;
    },

    /**
     * Toggle which filter fields are shown + relabel table columns based
     * on the active source. Called when the modal mounts and whenever
     * the user changes the Source dropdown.
     */
    _applySourceUI() {
        const src = this._currentSource();
        const showSettings = (src === SOURCE_SETTINGS || src === SOURCE_BOTH);
        const showUi       = (src === SOURCE_UI       || src === SOURCE_BOTH);
        $('.audit-filter-settings-only').toggle(showSettings);
        $('.audit-filter-ui-only').toggle(showUi);

        // Column relabel — keeps the same <th>s but changes their text
        // so a single render path can serve both sources via normalize().
        if (src === SOURCE_UI) {
            $('#audit-col-origin-kind').text('Kind');
            $('#audit-col-scope-target').text('Target');
            $('#audit-col-key-page').text('Page');
            $('#audit-col-change-summary').text('Summary');
        } else if (src === SOURCE_BOTH) {
            $('#audit-col-origin-kind').text('Origin/Kind');
            $('#audit-col-scope-target').text('Scope/Target');
            $('#audit-col-key-page').text('Key/Page');
            $('#audit-col-change-summary').text('Change/Summary');
        } else {
            $('#audit-col-origin-kind').text('Origin');
            $('#audit-col-scope-target').text('Scope');
            $('#audit-col-key-page').text('Key');
            $('#audit-col-change-summary').text('Change');
        }
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
        const src = this._currentSource();
        const fromVal = $('#audit-filter-from').val();
        const toVal   = $('#audit-filter-to').val();
        const fromIso = fromVal ? new Date(fromVal).toISOString() : null;
        const toIso   = toVal   ? new Date(toVal).toISOString()   : null;
        const q = $('#audit-filter-q').val().trim();

        $('#audit-log-status').text('Loading…');

        try {
            if (src === SOURCE_SETTINGS) {
                const data = await this._fetchSettings(fromIso, toIso, q, this._limit, this._offset);
                if (!data) return;
                this._total = data.total || 0;
                const rows = (data.rows || []).map(r => this._normalizeSettings(r));
                this._render(rows);
                this._updateStatus(rows.length);
            }
            else if (src === SOURCE_UI) {
                const data = await this._fetchUi(fromIso, toIso, q, this._limit, this._offset);
                if (!data) return;
                this._total = data.total || 0;
                const rows = (data.rows || []).map(r => this._normalizeUi(r));
                this._render(rows);
                this._updateStatus(rows.length);
            }
            else {
                // 'both': fetch a generous slice from each, merge by ts DESC,
                // then locally slice to [offset, offset+limit]. The displayed
                // 'total' is the sum of both server-reported totals.
                //
                // Implementation note: we request limit*4 from each side so
                // the local pagination has enough headroom past the offset.
                // For very deep pages this becomes inaccurate (we'd miss
                // late-merged rows from the smaller-volume source). For v1
                // the operator-friendliness win outweighs the edge case.
                const wide = Math.max(this._limit * 4, 200);
                const [a, b] = await Promise.all([
                    this._fetchSettings(fromIso, toIso, q, wide, 0),
                    this._fetchUi(fromIso, toIso, q, wide, 0),
                ]);
                if (!a || !b) return;
                const merged = []
                    .concat((a.rows || []).map(r => this._normalizeSettings(r)))
                    .concat((b.rows || []).map(r => this._normalizeUi(r)));
                merged.sort((x, y) => (y._ts || '').localeCompare(x._ts || ''));
                this._total = (a.total || 0) + (b.total || 0);
                const slice = merged.slice(this._offset, this._offset + this._limit);
                this._render(slice);
                this._updateStatus(slice.length);
            }
        } catch (e) {
            $('#audit-log-status').text(`Error: ${e.message}`);
        }
    },

    _updateStatus(shown) {
        $('#audit-log-status').text(`Showing ${this._offset+1}–${this._offset+shown} of ${this._total}`);
        $('#audit-page-info').text(
            `Page ${Math.floor(this._offset/this._limit)+1} / ${Math.max(1, Math.ceil(this._total/this._limit))}`
        );
    },

    /** Build query string for /api/audit/log (settings-side). */
    async _fetchSettings(fromIso, toIso, q, limit, offset) {
        const params = new URLSearchParams();
        if (fromIso) params.set('from', fromIso);
        if (toIso)   params.set('to',   toIso);
        const origin = $('#audit-filter-origin').val();
        if (origin) params.set('origin', origin);
        const scope = ($('#audit-filter-scope').val() || '').trim();
        if (scope) params.set('scope', scope);
        if (q) params.set('q', q);
        params.set('limit', limit);
        params.set('offset', offset);
        const r = await fetch(`${ENDPOINT}?${params}`, { credentials: 'same-origin' });
        if (!r.ok) {
            $('#audit-log-status').text(`HTTP ${r.status} (settings)`);
            return null;
        }
        return r.json();
    },

    /** Build query string for /api/ui-event/log (UI side). */
    async _fetchUi(fromIso, toIso, q, limit, offset) {
        const params = new URLSearchParams();
        if (fromIso) params.set('from', fromIso);
        if (toIso)   params.set('to',   toIso);
        const kind = $('#audit-filter-kind').val();
        if (kind) params.set('kind', kind);
        const tid = ($('#audit-filter-target-id').val() || '').trim();
        if (tid) params.set('target_id', tid);
        if (q) params.set('q', q);
        params.set('limit', limit);
        params.set('offset', offset);
        const r = await fetch(`${UI_EVENT_ENDPOINT}?${params}`, { credentials: 'same-origin' });
        if (!r.ok) {
            $('#audit-log-status').text(`HTTP ${r.status} (ui-events)`);
            return null;
        }
        return r.json();
    },

    /**
     * Normalize a setting_audit_log row into the unified display shape.
     * Display shape:
     *   { _ts, _kind, _user, _client, _host, _scope, _key, _summary,
     *     _source: 'settings', _raw }
     */
    _normalizeSettings(r) {
        return {
            _ts:      r.ts,
            _kind:    r.origin || '',
            _user:    r.user_id == null ? '' : String(r.user_id),
            _client:  r.client_id || '',
            _host:    '',
            _scope:   r.row_pk ? `${r.table_name}:${r.row_pk}` : (r.table_name || ''),
            _key:     r.setting_key || '',
            _summary: this._summarizeChange(r),
            _source:  'settings',
            _raw:     r,
        };
    },

    /**
     * Normalize a ui_event_log row into the unified display shape.
     * The "{user} on {client_id|host_label} clicked on {target} at {ts}"
     * sentence is built here per operator spec 2026-05-13.
     */
    _normalizeUi(r) {
        // Prefer the human-readable text; fall back to id, then tag.
        const targetLabel = r.target_text
            || (r.target_id ? `#${r.target_id}` : '')
            || r.target_tag
            || '(unknown)';
        const summary = this._uiSentence(r, targetLabel);
        return {
            _ts:      r.ts,
            _kind:    r.kind || '',
            _user:    r.user_id == null ? '' : String(r.user_id),
            _client:  r.client_id || '',
            _host:    r.host_label || '',
            _scope:   r.target_tag ? `${r.target_tag}${r.target_id ? '#' + r.target_id : ''}` : (r.target_id || ''),
            _key:     r.page_url || '',
            _summary: summary,
            _source:  'ui',
            _raw:     r,
        };
    },

    /**
     * Compose the litigation-grade UI-event sentence.
     * e.g. "user 7 on office-kiosk clicked on Save at 2026-05-13 15:42:11".
     * Falls back to client_id (8-char prefix) when host_label is unset.
     */
    _uiSentence(r, targetLabel) {
        const who    = r.user_id != null ? `user ${r.user_id}` : 'anon';
        const where  = r.host_label || (r.client_id ? `device ${String(r.client_id).slice(0,8)}` : 'unknown device');
        const action = this._kindVerb(r.kind);
        const when   = (r.ts || '').replace('T', ' ').replace(/\..*$/, '');
        return `${who} on ${where} ${action} ${targetLabel} at ${when}`;
    },

    _kindVerb(kind) {
        switch (kind) {
            case 'click':        return 'clicked on';
            case 'keystroke':    return 'typed in';
            case 'focus':        return 'focused';
            case 'blur':         return 'blurred';
            case 'submit':       return 'submitted';
            case 'navigation':   return 'navigated to';
            case 'modal_open':   return 'opened modal';
            case 'modal_close':  return 'closed modal';
            case 'scroll':       return 'scrolled in';
            default:             return kind || '';
        }
    },

    /**
     * Render normalized rows (display shape, NOT raw). Both
     * setting_audit_log and ui_event_log feed through this single path.
     * Row-click expansion reveals the source-appropriate JSON blob.
     */
    _render(rows) {
        const $tbody = $('#audit-log-rows').empty();
        if (rows.length === 0) {
            $tbody.append('<tr><td colspan="7" style="text-align:center;color:#888;padding:30px;">No entries match these filters.</td></tr>');
            return;
        }
        for (const dr of rows) {
            const tsCell = this._formatTs(dr._ts);
            // Device column shows host_label when present (UI events) else
            // the first 8 chars of client_id — full UUID is on hover.
            const deviceCell = dr._host
                ? `<span title="${this._esc(dr._client)}">${this._esc(dr._host)}</span>`
                : `<span title="${this._esc(dr._client)}">${this._esc((dr._client || '').slice(0, 8))}</span>`;
            const $tr = $(`
                <tr>
                    <td>${tsCell}</td>
                    <td>${this._esc(dr._kind)}</td>
                    <td>${this._esc(dr._user)}</td>
                    <td>${deviceCell}</td>
                    <td>${this._esc(dr._scope)}</td>
                    <td>${this._esc(dr._key)}</td>
                    <td class="change-cell">${this._esc(dr._summary)}</td>
                </tr>
            `);
            // _raw + _source ride along on the DOM node so the click
            // handler can build a source-appropriate JSON expansion.
            $tr.data('row',   dr._raw);
            $tr.data('drow',  dr);
            $tbody.append($tr);
        }
    },

    /**
     * Render a server-supplied UTC ISO timestamp as:
     *   local time (primary, big)   ← the audit log is for an operator
     *                                  reading "what happened when on my
     *                                  schedule", so local-time-first
     *                                  is the right primary.
     *   UTC      (secondary, dim)   ← kept visible because litigation /
     *                                  cross-zone forensics needs the
     *                                  canonical UTC reference, and the
     *                                  DB stores in UTC.
     * The cell hover-title shows the operator's resolved TZ name so
     * "what TZ is 'local' anyway" is auditable too.
     */
    _formatTs(iso) {
        if (!iso) return '';
        let d;
        try {
            d = new Date(iso);
            if (isNaN(d.getTime())) return this._esc(iso);
        } catch (_) {
            return this._esc(iso);
        }
        const pad = (n) => String(n).padStart(2, '0');
        const local = `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
        const utc   = `${d.getUTCFullYear()}-${pad(d.getUTCMonth()+1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())} UTC`;
        let tz = '';
        try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (_) {}
        const tzHint = tz ? ` (${tz})` : '';
        return `<div title="${this._esc(iso)}${this._esc(tzHint)}"><div>${this._esc(local)}</div><div style="color:#888;font-size:10px;">${this._esc(utc)}</div></div>`;
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
        // Source-aware CSV export. Server caps each side at MAX_LIMIT=1000.
        const src = this._currentSource();
        const fromVal = $('#audit-filter-from').val();
        const toVal   = $('#audit-filter-to').val();
        const fromIso = fromVal ? new Date(fromVal).toISOString() : null;
        const toIso   = toVal   ? new Date(toVal).toISOString()   : null;
        const q = $('#audit-filter-q').val().trim();

        const escCsv = (v) => {
            const s = v == null ? '' : (typeof v === 'object' ? JSON.stringify(v) : String(v));
            return /[,"\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
        };

        let header, csvRows;
        if (src === SOURCE_UI) {
            const data = await this._fetchUi(fromIso, toIso, q, 1000, 0);
            if (!data) return;
            header = ['id','ts','kind','user_id','client_id','host_label','target_id','target_tag','target_text','target_attrs','page_url','extra'];
            csvRows = (data.rows || []).map(r => header.map(h => escCsv(r[h])).join(','));
        } else if (src === SOURCE_BOTH) {
            const [a, b] = await Promise.all([
                this._fetchSettings(fromIso, toIso, q, 1000, 0),
                this._fetchUi(fromIso, toIso, q, 1000, 0),
            ]);
            if (!a || !b) return;
            // Unified header: superset of both shapes, source column first.
            header = ['source','id','ts','kind_or_origin','user_id','client_id','host_label',
                      'scope_or_target','setting_key_or_page_url','summary','raw_json'];
            csvRows = [];
            for (const r of (a.rows || [])) {
                const dr = this._normalizeSettings(r);
                csvRows.push(header.map(h => {
                    switch (h) {
                        case 'source':          return 'settings';
                        case 'id':              return escCsv(r.id);
                        case 'ts':              return escCsv(r.ts);
                        case 'kind_or_origin':  return escCsv(r.origin);
                        case 'user_id':         return escCsv(r.user_id);
                        case 'client_id':       return escCsv(r.client_id);
                        case 'host_label':      return '';
                        case 'scope_or_target': return escCsv(dr._scope);
                        case 'setting_key_or_page_url': return escCsv(r.setting_key);
                        case 'summary':         return escCsv(dr._summary);
                        case 'raw_json':        return escCsv({old_value:r.old_value,new_value:r.new_value,note:r.note});
                    }
                }).join(','));
            }
            for (const r of (b.rows || [])) {
                const dr = this._normalizeUi(r);
                csvRows.push(header.map(h => {
                    switch (h) {
                        case 'source':          return 'ui';
                        case 'id':              return escCsv(r.id);
                        case 'ts':              return escCsv(r.ts);
                        case 'kind_or_origin':  return escCsv(r.kind);
                        case 'user_id':         return escCsv(r.user_id);
                        case 'client_id':       return escCsv(r.client_id);
                        case 'host_label':      return escCsv(r.host_label);
                        case 'scope_or_target': return escCsv(dr._scope);
                        case 'setting_key_or_page_url': return escCsv(r.page_url);
                        case 'summary':         return escCsv(dr._summary);
                        case 'raw_json':        return escCsv({target_attrs:r.target_attrs,extra:r.extra});
                    }
                }).join(','));
            }
        } else {
            const data = await this._fetchSettings(fromIso, toIso, q, 1000, 0);
            if (!data) return;
            header = ['id','ts','origin','user_id','client_id','table_name','row_pk','setting_key','old_value','new_value','note'];
            csvRows = (data.rows || []).map(r => header.map(h => escCsv(r[h])).join(','));
        }

        const csv = [header.join(',')].concat(csvRows);
        const blob = new Blob([csv.join('\n')], { type: 'text/csv' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `audit-log-${src}-${new Date().toISOString().slice(0,19).replace(/[:.]/g,'')}.csv`;
        a.click();
        URL.revokeObjectURL(url);
    },
};
