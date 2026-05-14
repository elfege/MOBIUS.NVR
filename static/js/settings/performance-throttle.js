/**
 * Performance Throttle settings panel — per-machine UI for the host-agent
 * driven CPU throttler.
 *
 * The settings are MACHINE-scoped (one row in host_settings keyed by
 * host_label), not user-scoped. The host_label is the same identifier the
 * host-agent reports when it pushes /api/host/state.
 *
 * Resolution order for the host_label this UI binds to:
 *   1. URL ?host_label=
 *   2. localStorage.mobius_host_label
 *   3. user input (text field on the panel)
 *
 * Whatever the user types in the Host Label field is persisted to
 * localStorage so the next page load remembers it. This is the same key the
 * VisibilityManager and ThrottleController read.
 */

import { hostAgentInstall } from './host-agent-install.js';

const LS_KEY = 'mobius_host_label';

function resolveHostLabel() {
    // Sync resolver — used by ThrottleController and VisibilityManager at
    // socket-attach time before any async lookup completes. Order:
    //   URL ?host_label= > localStorage > '' (no binding yet)
    try {
        const url = new URLSearchParams(window.location.search).get('host_label');
        if (url) return url;
    } catch (_) {}
    try {
        return localStorage.getItem(LS_KEY) || '';
    } catch (_) {
        return '';
    }
}

/**
 * Async resolver — calls /api/host/whoami which consults the
 * trusted_devices.host_label FK populated by the host-agent ping.
 * On hit, latches the value into localStorage so resolveHostLabel()
 * picks it up everywhere (throttle controller, visibility manager).
 *
 * Returns the resolved label or '' if no binding exists.
 */
async function resolveHostLabelFromServer() {
    try {
        const r = await fetch('/api/host/whoami', { credentials: 'same-origin' });
        if (!r.ok) return '';
        const j = await r.json();
        if (j && j.host_label) {
            try { localStorage.setItem(LS_KEY, j.host_label); } catch (_) {}
            return j.host_label;
        }
    } catch (_) {}
    return '';
}

function setHostLabel(label) {
    try {
        if (label) localStorage.setItem(LS_KEY, label);
        else localStorage.removeItem(LS_KEY);
    } catch (_) {}
}

function isPortableUA() {
    return /iPad|iPhone|iPod|Android|Silk\/|Fire/i.test(navigator.userAgent);
}

export const performanceThrottle = {
    /**
     * Render the panel HTML. Called once when the settings modal builds.
     * Returns a complete .settings-tab-panel string.
     */
    renderHTML() {
        const label = resolveHostLabel();
        return `
        <div class="settings-tab-panel" data-tab-panel="performance">

            <div class="setting-row">
                <div class="setting-top">
                    <div class="setting-label"><i class="fas fa-server"></i> Host Label</div>
                    <div class="setting-control">
                        <input type="text" id="perf-host-label" class="setting-input"
                               placeholder="e.g. rog" value="${label || ''}"
                               style="width:180px;">
                        <button id="perf-host-label-save" class="setting-btn setting-btn-primary"
                                style="font-size:12px;padding:5px 12px;margin-left:6px;">
                            Bind
                        </button>
                    </div>
                </div>
                <div class="setting-description">
                    The kiosk machine identity. Must match HOST_LABEL in the
                    host-agent config (<code>~/.config/mobius-nvr-host-agent/config</code>).
                    Stored in localStorage on this browser. Settings below apply
                    to that host's row in the database.
                </div>
            </div>

            <div id="perf-throttle-controls" style="${label ? '' : 'opacity:0.4;pointer-events:none;'}">

                <div class="setting-row">
                    <div class="setting-top">
                        <div class="setting-label"><i class="fas fa-toggle-on"></i> Performance Throttle</div>
                        <div class="setting-control">
                            <label class="setting-toggle">
                                <input type="checkbox" id="perf-throttle-enabled" checked>
                                <span class="toggle-slider"></span>
                            </label>
                        </div>
                    </div>
                    <div class="setting-description">
                        When enabled, the browser stops one stream tile at a time
                        whenever sustained CPU load on this machine exceeds the
                        threshold below. Tiles are restored automatically as
                        the load drops.
                    </div>
                </div>

                <div class="setting-row">
                    <div class="setting-top">
                        <div class="setting-label"><i class="fas fa-microchip"></i> Max CPU Load</div>
                        <div class="setting-control" style="display:flex;align-items:center;gap:10px;">
                            <input type="range" id="perf-max-cpu" min="1" max="95" step="1" value="50" style="width:200px;">
                            <span id="perf-max-cpu-out" style="font-variant-numeric:tabular-nums;min-width:38px;">50%</span>
                        </div>
                    </div>
                    <div class="setting-description">
                        Sustained CPU load (averaged across all cores) above which
                        the throttler starts demoting tiles. Defaults to 50%.
                    </div>
                </div>

                <div class="setting-row">
                    <div class="setting-top">
                        <div class="setting-label"><i class="fas fa-undo"></i> Restore Hysteresis</div>
                        <div class="setting-control" style="display:flex;align-items:center;gap:10px;">
                            <input type="range" id="perf-hyst" min="0" max="50" step="1" value="10" style="width:200px;">
                            <span id="perf-hyst-out" style="font-variant-numeric:tabular-nums;min-width:38px;">10%</span>
                        </div>
                    </div>
                    <div class="setting-description">
                        Tiles are restored only when CPU drops to
                        <em>(Max&nbsp;CPU − Hysteresis)</em>. Larger gap = less
                        oscillation around the boundary.
                    </div>
                </div>

                <div class="setting-row">
                    <div class="setting-description" id="perf-host-status" style="font-size:12px;color:#888;">
                        Loading host status…
                    </div>
                </div>

            </div>

            ${hostAgentInstall.renderHTML()}

            <div class="setting-row" id="perf-all-hosts-row">
                <div class="setting-top">
                    <div class="setting-label"><i class="fas fa-network-wired"></i> All Reporting Hosts</div>
                    <div class="setting-control">
                        <button id="perf-refresh-hosts" class="setting-btn setting-btn-secondary" style="font-size:12px;padding:5px 12px;">
                            Refresh
                        </button>
                    </div>
                </div>
                <div class="setting-description">
                    Every host_label seen by this NVR — online status is computed from
                    each agent's last ping. Status auto-updates via SocketIO.
                </div>
                <div id="perf-host-list" style="margin-top:8px;font-size:12px;"></div>
            </div>

        </div>`;
    },

    /**
     * Tab button to insert into the tab bar.
     */
    renderTabButton() {
        return `<button class="settings-tab-btn" data-tab="performance">
            <i class="fas fa-tachometer-alt"></i> Performance
        </button>`;
    },

    /**
     * Wire event handlers and load current settings from the server.
     * Idempotent — safe to call every time the tab is opened.
     *
     * @param {jQuery} $panel - the .settings-tab-panel for performance
     */
    async init($panel) {
        if (!$panel || !$panel.length) return;

        // Try to auto-resolve host_label from trusted_devices (FK populated
        // by the host-agent's ping). If it succeeds and the input is empty,
        // pre-fill it so the user doesn't have to type their hostname.
        const resolved = await resolveHostLabelFromServer();
        if (resolved && !$panel.find('#perf-host-label').val().trim()) {
            $panel.find('#perf-host-label').val(resolved);
            $panel.find('#perf-throttle-controls').css({ opacity: '', pointerEvents: '' });
        }

        // Portable UA: replace the throttle controls with a friendly empty
        // state. iOS/Android have OS-level power management; we don't try
        // to fight them. Users can still pop into the full UI via /streams?full=1.
        if (isPortableUA() && !resolved) {
            $panel.find('#perf-throttle-controls').html(`
                <div class="setting-row" style="border-left-color:#888;">
                    <div class="setting-top">
                        <div class="setting-label"><i class="fas fa-mobile-alt"></i> Portable Device</div>
                    </div>
                    <div class="setting-description">
                        Performance throttling is a Linux-kiosk feature.
                        On this device the OS already handles power management
                        (background CPU throttling, screen-lock decode pause,
                        memory pressure). The light UI at <code>/light</code>
                        is the default for portable devices — switch back to
                        the full UI any time via the navigation menu or
                        <code>?full=1</code> on /streams.
                    </div>
                </div>
            `);
        }

        // Bind/Save host label
        $panel.find('#perf-host-label-save').off('click.perf').on('click.perf', () => {
            const v = $panel.find('#perf-host-label').val().trim();
            setHostLabel(v);
            $panel.find('#perf-throttle-controls').css({
                opacity: v ? '' : '0.4',
                pointerEvents: v ? '' : 'none',
            });
            this.loadSettings($panel);
            // Refresh the install cards so the "this machine" card
            // reflects the newly-bound label without a page reload.
            hostAgentInstall.init($panel);
        });

        // Live numeric readouts
        const bindRange = (sel, outSel) => {
            const $r = $panel.find(sel);
            const $o = $panel.find(outSel);
            $r.off('input.perf').on('input.perf', () => $o.text(`${$r.val()}%`));
        };
        bindRange('#perf-max-cpu', '#perf-max-cpu-out');
        bindRange('#perf-hyst', '#perf-hyst-out');

        // Persist on change (debounced for sliders)
        let debounceT = null;
        const saveDebounced = () => {
            clearTimeout(debounceT);
            debounceT = setTimeout(() => this.saveSettings($panel), 350);
        };
        $panel.find('#perf-throttle-enabled').off('change.perf').on('change.perf', () => this.saveSettings($panel));
        $panel.find('#perf-max-cpu, #perf-hyst').off('change.perf input.perf').on('input.perf change.perf', saveDebounced);

        $panel.find('#perf-refresh-hosts').off('click.perf').on('click.perf', () => this.loadHostList($panel));

        await this.loadSettings($panel);
        await this.loadHostList($panel);

        // Wire the host-agent install cards. Idempotent; safe to call on
        // every tab open. We also re-init after a Bind click so the
        // "this machine" card picks up the new host_label without a
        // page reload.
        await hostAgentInstall.init($panel);

        // Live updates: subscribe to host_status_changed so dots flip without refresh.
        // The /stream_events socket is owned by stream.js — we tap it via window.io
        // each time the panel opens (idempotent: socket.on is a no-op if same handler
        // re-registered, but we use a marker on $panel to be safe).
        if (!$panel.data('perf-socket-bound')) {
            try {
                if (typeof io !== 'undefined') {
                    const sock = io('/stream_events', { transports: ['websocket', 'polling'] });
                    sock.on('host_status_changed', () => this.loadHostList($panel));
                    sock.on('host_state_changed', (msg) => {
                        // Only re-render if the changed host is on the current page.
                        if (msg && msg.host_label) this._patchHostRow($panel, msg);
                    });
                    $panel.data('perf-socket-bound', true);
                }
            } catch (e) {
                console.warn('[PerformanceThrottle] socket bind failed:', e);
            }
        }
    },

    async loadHostList($panel) {
        try {
            const r = await fetch('/api/host/list', { credentials: 'same-origin' });
            if (!r.ok) {
                $panel.find('#perf-host-list').text(`(failed to load: HTTP ${r.status})`);
                return;
            }
            const j = await r.json();
            const hosts = (j && Array.isArray(j.hosts)) ? j.hosts : [];
            this._renderHostList($panel, hosts);
        } catch (e) {
            $panel.find('#perf-host-list').text(`(load error: ${e.message || e})`);
        }
    },

    _renderHostList($panel, hosts) {
        if (!hosts.length) {
            $panel.find('#perf-host-list').html('<em style="color:#888;">No hosts have reported yet.</em>');
            return;
        }
        const dot = (status) => {
            const color = ({
                online:  '#28a745',
                stale:   '#ffc107',
                offline: '#dc3545',
                never:   '#888',
            })[status] || '#888';
            return `<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${color};margin-right:6px;vertical-align:middle;"></span>`;
        };
        const fmtAge = (s) => {
            if (s == null) return 'never';
            if (s < 60) return `${s.toFixed(0)}s ago`;
            if (s < 3600) return `${(s/60).toFixed(1)}m ago`;
            return `${(s/3600).toFixed(1)}h ago`;
        };
        const rows = hosts.map(h => {
            const cpu = (typeof h.cpu_load_norm === 'number') ? `${(h.cpu_load_norm*100).toFixed(0)}%` : '—';
            const dpms = h.display_state || '—';
            const thr = (h.performance_throttle_enabled === false) ? 'off' : `≤${h.performance_max_cpu_pct ?? 50}%`;
            return `<div data-host-row="${h.host_label}" style="padding:4px 0;border-bottom:1px solid #2a2a2a;display:flex;gap:10px;align-items:center;">
                ${dot(h.status)}
                <strong style="min-width:120px;">${h.host_label}</strong>
                <span style="color:#aaa;min-width:90px;">${h.status}</span>
                <span style="color:#aaa;min-width:90px;">${fmtAge(h.age_s)}</span>
                <span style="color:#aaa;min-width:80px;">display: ${dpms}</span>
                <span style="color:#aaa;min-width:80px;">CPU: ${cpu}</span>
                <span style="color:#aaa;">throttle: ${thr}</span>
            </div>`;
        }).join('');
        $panel.find('#perf-host-list').html(rows);
    },

    /**
     * Live-patch one row when host_state_changed arrives. Falls back to a
     * full reload if the row isn't present yet (host appeared mid-session).
     */
    _patchHostRow($panel, msg) {
        const $row = $panel.find(`[data-host-row="${msg.host_label}"]`);
        if (!$row.length) {
            this.loadHostList($panel);
            return;
        }
        const cpu = (typeof msg.cpu_load_norm === 'number') ? `${(msg.cpu_load_norm*100).toFixed(0)}%` : '—';
        const dpms = msg.display_state || '—';
        $row.find('span').filter((_, el) => el.textContent.startsWith('display:')).text(`display: ${dpms}`);
        $row.find('span').filter((_, el) => el.textContent.startsWith('CPU:')).text(`CPU: ${cpu}`);
    },

    async loadSettings($panel) {
        const label = resolveHostLabel();
        if (!label) {
            $panel.find('#perf-host-status').text('No host label bound — set one above to load settings.');
            return;
        }
        try {
            const r = await fetch(`/api/host/${encodeURIComponent(label)}/settings`, {
                credentials: 'same-origin',
            });
            if (!r.ok) {
                $panel.find('#perf-host-status').text(`Failed to load settings (HTTP ${r.status}).`);
                return;
            }
            const j = await r.json();
            // GET returns a flat row (no envelope).
            const s = (j && typeof j === 'object') ? j : {};
            $panel.find('#perf-throttle-enabled').prop('checked', s.performance_throttle_enabled !== false);
            const maxCpu = Number(s.performance_max_cpu_pct ?? 50);
            const hyst   = Number(s.performance_restore_hysteresis_pct ?? 10);
            $panel.find('#perf-max-cpu').val(maxCpu);
            $panel.find('#perf-max-cpu-out').text(`${maxCpu}%`);
            $panel.find('#perf-hyst').val(hyst);
            $panel.find('#perf-hyst-out').text(`${hyst}%`);

            // Pull last_seen from /api/host/state for context
            try {
                const r2 = await fetch(`/api/host/state?host=${encodeURIComponent(label)}`, {
                    credentials: 'same-origin',
                });
                if (r2.ok) {
                    const js = await r2.json();
                    // /api/host/state?host=<label> returns the agent's last
                    // pushed body (keys: host, display_state, cpu_load_norm,
                    // server_received_at, …) or {} if no agent has reported.
                    if (js && js.host) {
                        const cpu = (typeof js.cpu_load_norm === 'number') ? `${(js.cpu_load_norm * 100).toFixed(0)}%` : '—';
                        const dpms = js.display_state || '—';
                        const ageS = (typeof js.server_received_at === 'number') ? Math.max(0, (Date.now()/1000) - js.server_received_at) : null;
                        const ageStr = (ageS == null) ? '—' : (ageS < 60 ? `${ageS.toFixed(0)}s ago` : `${(ageS/60).toFixed(1)}m ago`);
                        $panel.find('#perf-host-status').text(`Host "${label}": display=${dpms}, CPU=${cpu}, last seen ${ageStr}.`);
                    } else {
                        $panel.find('#perf-host-status').text(`Host "${label}" has not reported yet — install and start the host-agent.`);
                    }
                }
            } catch (_) {}
        } catch (e) {
            console.warn('[PerformanceThrottle] loadSettings failed:', e);
            $panel.find('#perf-host-status').text(`Load error: ${e.message || e}`);
        }
    },

    async saveSettings($panel) {
        const label = resolveHostLabel();
        if (!label) return;
        const body = {
            performance_throttle_enabled: $panel.find('#perf-throttle-enabled').is(':checked'),
            performance_max_cpu_pct: Number($panel.find('#perf-max-cpu').val()),
            performance_restore_hysteresis_pct: Number($panel.find('#perf-hyst').val()),
        };
        try {
            const r = await fetch(`/api/host/${encodeURIComponent(label)}/settings`, {
                method: 'PUT',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            if (!r.ok) {
                console.warn(`[PerformanceThrottle] save failed: HTTP ${r.status}`);
                return;
            }
            console.log('[PerformanceThrottle] saved', body);
        } catch (e) {
            console.warn('[PerformanceThrottle] save error:', e);
        }
    },
};
