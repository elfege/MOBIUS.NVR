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

// ---------------------------------------------------------------------------
// Lightweight canvas sparkline. No external lib — keeps the perf tab cheap
// even on low-end clients (which is the WHOLE POINT of the throttler this
// tab is configuring).
// ---------------------------------------------------------------------------
const SPARK_MAX = 60;  // matches server _METRICS_RING_MAX

/** Pull a numeric series out of a samples[] array; null/undefined become 0. */
function _series(samples, key) {
    return samples.map(s => {
        const v = s && s[key];
        return (typeof v === 'number' && isFinite(v)) ? v : 0;
    });
}

/**
 * Draw a polyline-style sparkline.
 *
 * Y-axis is fixed 0..100 (percent), so charts are directly comparable —
 * if you see the CPU line crossing 50% near the top of the canvas, that's
 * really 50% of total CPU, not "50% of whatever the max was".
 *
 * Threshold line: optional red horizontal at `thresholdPct` (e.g. the
 * throttle Max CPU setting) so the operator sees "we are over the line".
 *
 * Drawn idempotently — caller may invoke on every new sample without
 * accumulating state inside the canvas. Each call: full clear + repaint.
 */
function _drawSparkline(canvas, values, opts = {}) {
    if (!canvas || !canvas.getContext) return;
    const ctx = canvas.getContext('2d');
    // Match CSS pixel size to backing-store size for crisp lines without
    // having to mess with devicePixelRatio scaling math every redraw.
    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    if (!values.length) return;

    // Pad the value array left so the line always anchors to the right
    // edge of the canvas — newest sample on the right, "history flows left".
    const padded = values.length < SPARK_MAX
        ? new Array(SPARK_MAX - values.length).fill(null).concat(values)
        : values.slice(-SPARK_MAX);

    const stepX = w / (SPARK_MAX - 1);
    const yFor = (v) => h - ((Math.max(0, Math.min(100, v)) / 100) * h);

    // Threshold line first (so the data line draws on top of it).
    if (typeof opts.thresholdPct === 'number') {
        const ty = yFor(opts.thresholdPct);
        ctx.strokeStyle = 'rgba(220, 60, 60, 0.55)';
        ctx.setLineDash([3, 3]);
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(0, ty);
        ctx.lineTo(w, ty);
        ctx.stroke();
        ctx.setLineDash([]);
    }

    // Data line.
    ctx.strokeStyle = opts.stroke || '#4fc3f7';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    let drawing = false;
    for (let i = 0; i < padded.length; i++) {
        const v = padded[i];
        if (v == null) { drawing = false; continue; }
        const x = i * stepX;
        const y = yFor(v);
        if (!drawing) { ctx.moveTo(x, y); drawing = true; }
        else          { ctx.lineTo(x, y); }
    }
    ctx.stroke();

    // Soft fill below the line so trends pop visually.
    const fill = opts.fill || 'rgba(79, 195, 247, 0.13)';
    ctx.fillStyle = fill;
    ctx.lineTo(w, h);
    ctx.lineTo(0, h);
    ctx.closePath();
    ctx.fill();
}


export const performanceThrottle = {
    // Ring buffer of compact metric samples for the currently-bound host.
    // Reset/backfilled in init() when the panel opens; appended-to in
    // _onHostStateChanged() on every SocketIO push.
    _perfBuf: [],
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

                <!-- Live performance panel: CPU/Mem/GPU sparklines, throttler state,
                     demoted-tile list. Populated by perfViz section of init() below. -->
                <div class="setting-row" id="perf-viz-row">
                    <div class="setting-top">
                        <div class="setting-label"><i class="fas fa-chart-line"></i> Live performance</div>
                        <div class="setting-control" id="perf-viz-throttler-badge"
                             style="font-size:11px;letter-spacing:0.04em;padding:3px 8px;border-radius:6px;background:#444;color:#bbb;">
                            THROTTLER: —
                        </div>
                    </div>
                    <div id="perf-viz-grid"
                         style="display:grid;grid-template-columns:140px 1fr 60px;gap:6px 12px;align-items:center;margin-top:10px;font-size:12px;">
                        <div style="color:#bbb;">CPU load</div>
                        <canvas id="perf-spark-cpu" width="600" height="32"
                                style="width:100%;height:32px;background:#1e1e1e;border-radius:4px;"></canvas>
                        <div id="perf-spark-cpu-out" style="font-variant-numeric:tabular-nums;text-align:right;color:#ddd;">—</div>

                        <div style="color:#bbb;">Memory</div>
                        <canvas id="perf-spark-mem" width="600" height="32"
                                style="width:100%;height:32px;background:#1e1e1e;border-radius:4px;"></canvas>
                        <div id="perf-spark-mem-out" style="font-variant-numeric:tabular-nums;text-align:right;color:#ddd;">—</div>

                        <div style="color:#bbb;">GPU util</div>
                        <canvas id="perf-spark-gpu" width="600" height="32"
                                style="width:100%;height:32px;background:#1e1e1e;border-radius:4px;"></canvas>
                        <div id="perf-spark-gpu-out" style="font-variant-numeric:tabular-nums;text-align:right;color:#ddd;">—</div>
                    </div>
                    <div id="perf-viz-demoted"
                         style="margin-top:10px;font-size:11px;color:#999;line-height:1.5;">
                        No tiles demoted.
                    </div>
                    <div class="setting-description" style="margin-top:8px;">
                        Sparklines show the last ~5 minutes of agent reports (5s cadence)
                        for this host. Demoted-tile list reflects this browser's throttler
                        state — the throttler runs client-side, so each browser session
                        has its own list.
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
                        if (msg && msg.host_label) {
                            this._patchHostRow($panel, msg);
                            // Append metrics sample for the perf-viz panel,
                            // filtered to the locally-bound host_label.
                            const myLabel = resolveHostLabel();
                            if (myLabel && msg.host_label === myLabel) {
                                this._appendPerfSample(msg);
                                this._renderPerfViz($panel);
                            }
                        }
                    });
                    $panel.data('perf-socket-bound', true);
                }
            } catch (e) {
                console.warn('[PerformanceThrottle] socket bind failed:', e);
            }
        }

        // Backfill the perf-viz sparklines from the server's ring buffer.
        // Without this the chart starts empty and only gains samples one
        // per agent poll — which is fine but visually unhelpful for the
        // first ~30 seconds.
        await this._backfillPerfBuf($panel);
        this._renderPerfViz($panel);

        // Re-paint the throttler badge + demoted-tile list every 2s so
        // changes to window.throttleController._demoted (a pure-JS
        // array on the global) reach the UI without us having to hook
        // each tile-demote/restore call site. The ThrottleController
        // doesn't currently emit events, and a 2s redraw is cheap.
        if ($panel.data('perf-viz-timer')) clearInterval($panel.data('perf-viz-timer'));
        const tid = setInterval(() => this._renderPerfViz($panel), 2000);
        $panel.data('perf-viz-timer', tid);
    },

    /**
     * Append one sample to the perf-viz ring buffer. Keep it bounded so
     * the canvas redraws don't get slower-and-slower as the tab stays open.
     */
    _appendPerfSample(msg) {
        const sample = {
            ts: msg.server_received_at || msg.ts || (Date.now() / 1000),
            cpu_load_norm: typeof msg.cpu_load_norm === 'number' ? msg.cpu_load_norm * 100 : null,
            mem_used_pct:  typeof msg.mem_used_pct  === 'number' ? msg.mem_used_pct       : null,
            gpu_util:      typeof msg.gpu_util      === 'number' ? msg.gpu_util           : null,
        };
        this._perfBuf.push(sample);
        if (this._perfBuf.length > SPARK_MAX) {
            this._perfBuf.splice(0, this._perfBuf.length - SPARK_MAX);
        }
    },

    /**
     * Pre-load the ring buffer from /api/host/<label>/metrics so the
     * panel opens with history already drawn. Server samples already
     * carry cpu_load_norm as a 0..1 fraction; we normalize to 0..100
     * here so the sparkline draw code is unit-consistent.
     */
    async _backfillPerfBuf($panel) {
        const label = resolveHostLabel();
        this._perfBuf = [];
        if (!label) return;
        try {
            const r = await fetch(`/api/host/${encodeURIComponent(label)}/metrics`,
                                  { credentials: 'same-origin' });
            if (!r.ok) return;
            const j = await r.json();
            const samples = (j && Array.isArray(j.samples)) ? j.samples : [];
            for (const s of samples) {
                this._perfBuf.push({
                    ts: s.ts,
                    cpu_load_norm: typeof s.cpu_load_norm === 'number' ? s.cpu_load_norm * 100 : null,
                    mem_used_pct:  typeof s.mem_used_pct  === 'number' ? s.mem_used_pct       : null,
                    gpu_util:      typeof s.gpu_util      === 'number' ? s.gpu_util           : null,
                });
            }
        } catch (e) {
            // Non-fatal: the live-update path will fill the buffer naturally.
            console.warn('[PerformanceThrottle] perf-viz backfill failed:', e);
        }
    },

    /**
     * Repaint sparklines + numeric readouts + throttler badge + demoted-tile list.
     *
     * Reads window.throttleController._demoted and .enabled directly. The
     * throttle controller is a global singleton wired by stream.js; if the
     * Perf tab is opened from /streams (which is the only place the
     * throttler runs anyway), the global is present.
     */
    _renderPerfViz($panel) {
        const buf = this._perfBuf;
        const last = buf.length ? buf[buf.length - 1] : null;

        // Threshold lines: pull from the inputs the user is currently editing.
        // No DB round-trip — we want the visual to track the slider in real time.
        const maxCpu  = parseFloat($panel.find('#perf-max-cpu').val()) || 50;
        const hyst    = parseFloat($panel.find('#perf-hyst').val())    || 10;
        const restoreFloor = Math.max(0, maxCpu - hyst);

        // CPU sparkline + readout. Red threshold line at maxCpu.
        const cpuVals = _series(buf, 'cpu_load_norm');
        _drawSparkline($panel.find('#perf-spark-cpu')[0], cpuVals,
            { thresholdPct: maxCpu, stroke: '#4fc3f7', fill: 'rgba(79,195,247,0.14)' });
        $panel.find('#perf-spark-cpu-out').text(
            (last && typeof last.cpu_load_norm === 'number')
                ? `${last.cpu_load_norm.toFixed(0)}%` : '—'
        );

        // Memory sparkline + readout. No threshold line (no throttler hook).
        const memVals = _series(buf, 'mem_used_pct');
        _drawSparkline($panel.find('#perf-spark-mem')[0], memVals,
            { stroke: '#ffb74d', fill: 'rgba(255,183,77,0.14)' });
        $panel.find('#perf-spark-mem-out').text(
            (last && typeof last.mem_used_pct === 'number')
                ? `${last.mem_used_pct.toFixed(0)}%` : '—'
        );

        // GPU sparkline + readout. Only meaningful on NVIDIA hosts.
        const gpuVals = _series(buf, 'gpu_util');
        const gpuHas = gpuVals.some(v => v > 0) || (last && last.gpu_util != null);
        _drawSparkline($panel.find('#perf-spark-gpu')[0], gpuVals,
            { stroke: '#81c784', fill: 'rgba(129,199,132,0.13)' });
        $panel.find('#perf-spark-gpu-out').text(
            (last && typeof last.gpu_util === 'number')
                ? `${last.gpu_util.toFixed(0)}%`
                : (gpuHas ? '—' : 'n/a')
        );

        // Throttler badge + demoted-tile list. Read from the global
        // ThrottleController singleton if present.
        const $badge = $panel.find('#perf-viz-throttler-badge');
        const $list  = $panel.find('#perf-viz-demoted');
        const tc = (typeof window !== 'undefined') ? window.throttleController : null;

        if (!tc) {
            $badge.text('THROTTLER: NOT IN /streams').css({ background: '#444', color: '#bbb' });
            $list.text('Open the Performance tab from /streams to see throttler state.');
            return;
        }
        if (!tc.enabled) {
            $badge.text('THROTTLER: OFF').css({ background: '#444', color: '#bbb' });
        } else {
            const cpuPct = last && typeof last.cpu_load_norm === 'number' ? last.cpu_load_norm : null;
            const over = cpuPct != null && cpuPct >= maxCpu;
            const dem  = Array.isArray(tc._demoted) ? tc._demoted.length : 0;
            if (dem > 0) {
                $badge.text(`THROTTLER: ACTIVE (${dem} demoted)`)
                      .css({ background: '#bf360c', color: '#fff' });
            } else if (over) {
                $badge.text('THROTTLER: ARMED, OVER THRESHOLD')
                      .css({ background: '#e65100', color: '#fff' });
            } else {
                $badge.text(`THROTTLER: ARMED (restore at ${restoreFloor.toFixed(0)}%)`)
                      .css({ background: '#1b5e20', color: '#fff' });
            }
        }

        const dem = Array.isArray(tc._demoted) ? tc._demoted : [];
        if (!dem.length) {
            $list.html('<span style="color:#666;">No tiles demoted.</span>');
        } else {
            // newest-on-top so the eye lands on the most recent demotion.
            const items = dem.slice().reverse().map(d => {
                const cam = (d.cameraId || '?');
                const sty = (d.streamType ? ` <span style="color:#666;">(${d.streamType})</span>` : '');
                return `<div style="padding:2px 0;"><i class="fas fa-pause-circle" style="color:#f57c00;"></i> ${cam}${sty}</div>`;
            }).join('');
            $list.html(`<div style="color:#bbb;font-weight:500;margin-bottom:4px;">Demoted tiles (newest first):</div>${items}`);
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
