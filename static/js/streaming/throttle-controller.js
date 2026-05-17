/**
 * Throttle Controller — per-machine performance governor.
 *
 * Listens to host_state_changed broadcasts on /stream_events. When the
 * kiosk's normalized CPU load exceeds host_settings.performance_max_cpu_pct
 * (and throttling is enabled), stops ONE stream tile at a time on each
 * broadcast cycle. Continues until CPU drops below the threshold (control
 * loop, not switch). Restores stopped tiles in reverse order once CPU
 * falls below (threshold - performance_restore_hysteresis_pct).
 *
 * Demotion order (highest CPU cost first):
 *   WEBRTC / GO2RTC > LL_HLS > HLS > MJPEG > snapshot
 *
 * Tiles stopped by this controller are tracked in a private set and
 * restored by the controller itself. Tiles the user stopped manually
 * (userInitiated=true) are never touched — that flag lives in localStorage
 * and is honored by the recovery layer; we just skip them when picking.
 *
 * Settings are cached in-memory and refreshed by:
 *   1. GET /api/host/<label>/settings on attach
 *   2. host_settings_changed broadcasts on /stream_events
 */

const DEMOTION_PRIORITY = ['WEBRTC', 'GO2RTC', 'LL_HLS', 'NEOLINK_LL_HLS', 'HLS', 'NEOLINK', 'MJPEG'];

export class ThrottleController {
    /**
     * @param {object} opts
     * @param {object} opts.streamManager - The stream.js NVRApp instance (has stopIndividualStream / startStream)
     * @param {jQuery} opts.$container    - jQuery container holding .stream-item tiles
     */
    constructor(opts = {}) {
        this.streamManager = opts.streamManager;
        this.$container = opts.$container;

        this._socket = null;
        this._hostLabel = null;

        // Settings, with safe defaults that match migration 032
        this._settings = {
            performance_throttle_enabled: true,
            performance_max_cpu_pct: 50,
            performance_restore_hysteresis_pct: 10,
        };

        // Stack of tiles WE demoted, most-recent on top. Each entry:
        //   { cameraId, cameraType, streamType }
        this._demoted = [];

        // Coalesce decisions: at most one demote/restore per broadcast cycle.
        this._busy = false;
    }

    /**
     * Attach to a connected /stream_events socket. hostLabel selects which
     * host's broadcasts apply; null = latch the first reporter (single-kiosk).
     */
    async attach(socket, hostLabel = null) {
        if (!socket) return;
        this._socket = socket;
        this._hostLabel = hostLabel;

        if (this._hostLabel) {
            await this._loadSettings();
        }

        socket.on('host_state_changed', (msg) => this._onHostState(msg));
        socket.on('host_settings_changed', (msg) => this._onSettingsChanged(msg));

        console.log('[Throttle] Controller attached');
    }

    async _loadSettings() {
        if (!this._hostLabel) return;
        try {
            const r = await fetch(`/api/host/${encodeURIComponent(this._hostLabel)}/settings`, {
                credentials: 'same-origin',
            });
            if (!r.ok) return;
            const j = await r.json();
            // GET /api/host/<label>/settings returns a flat row (no envelope).
            if (j && typeof j === 'object') {
                this._applySettings(j);
                console.log('[Throttle] Settings loaded:', this._settings);
            }
        } catch (e) {
            console.warn('[Throttle] Settings load failed:', e);
        }
    }

    _onSettingsChanged(msg) {
        if (!msg || !msg.host_label) return;
        if (this._hostLabel && msg.host_label !== this._hostLabel) return;
        if (msg.settings) {
            this._applySettings(msg.settings);
            console.log('[Throttle] Settings updated:', this._settings);
        }
        // If the user just disabled throttling, restore everything we demoted.
        if (this._settings.performance_throttle_enabled === false) {
            this._restoreAll();
        }
    }

    /**
     * Pick known fields from a settings dict and apply them. Defends against
     * extra DB columns and missing values (keeps prior defaults when absent).
     */
    _applySettings(s) {
        if (typeof s.performance_throttle_enabled === 'boolean') {
            this._settings.performance_throttle_enabled = s.performance_throttle_enabled;
        }
        if (typeof s.performance_max_cpu_pct === 'number') {
            this._settings.performance_max_cpu_pct = s.performance_max_cpu_pct;
        }
        if (typeof s.performance_restore_hysteresis_pct === 'number') {
            this._settings.performance_restore_hysteresis_pct = s.performance_restore_hysteresis_pct;
        }
    }

    async _onHostState(msg) {
        if (!msg || typeof msg !== 'object') return;
        if (this._hostLabel && msg.host_label !== this._hostLabel) {
            return;
        }
        if (!this._hostLabel && msg.host_label) {
            this._hostLabel = msg.host_label;
            await this._loadSettings();
        }
        if (!this._settings.performance_throttle_enabled) return;
        if (this._busy) return;

        // The agent reports cpu_load_norm in [0,1] (1.0 = all cores saturated).
        // Compare against threshold expressed as a percentage.
        const loadNorm = (typeof msg.cpu_load_norm === 'number') ? msg.cpu_load_norm : null;
        if (loadNorm == null) return;
        const cpuPct = loadNorm * 100;

        const threshold = this._settings.performance_max_cpu_pct;
        const hysteresis = this._settings.performance_restore_hysteresis_pct;
        const restoreFloor = Math.max(0, threshold - hysteresis);

        this._busy = true;
        try {
            if (cpuPct > threshold) {
                await this._demoteOne(cpuPct, threshold);
            } else if (cpuPct < restoreFloor && this._demoted.length > 0) {
                await this._restoreOne(cpuPct, restoreFloor);
            }
        } finally {
            this._busy = false;
        }
    }

    /**
     * Find the highest-priority running tile and stop it. Skips tiles that
     * were stopped by the user, are loading, or are already in error state.
     */
    async _demoteOne(cpuPct, threshold) {
        // Build candidate list:
        //   - Must be in a streaming/active/connected state.
        //   - SKIP any tile whose data-throttle-never="true" — these are
        //     operator-flagged safety-critical cameras (e.g. AMCREST LOBBY)
        //     and the throttler must never demote them, no matter how high
        //     CPU climbs. See migration 039 / cameras.throttle_never.
        const $candidates = this.$container.find('.stream-item').filter((_, el) => {
            const $el = $(el);
            const status = $el.data('stream-status') || $el.attr('data-stream-status');
            const never = ($el.attr('data-throttle-never') || '').toString().toLowerCase() === 'true';
            if (never) return false;
            return status === 'streaming' || status === 'active' || status === 'connected';
        });

        // Read throttle_priority off each candidate. Null / empty / NaN
        // becomes Infinity — those tiles sort to the END of the priority
        // queue, so they're considered AFTER any tile the operator
        // explicitly prioritized. The stream-type DEMOTION_PRIORITY then
        // breaks ties within the same priority value (or among all the
        // "no priority set" candidates).
        const candidatesByPriority = $candidates.toArray()
            .map(el => {
                const $el = $(el);
                const raw = ($el.attr('data-throttle-priority') || '').trim();
                const p = raw === '' ? Infinity : parseInt(raw, 10);
                return { $el, priority: Number.isFinite(p) ? p : Infinity };
            })
            .sort((a, b) => a.priority - b.priority);

        let pick = null;
        // For each priority bucket (lowest first), scan in stream-type
        // demotion order. This preserves the previous "demote HLS before
        // WebRTC" behaviour WITHIN a priority bucket while honouring
        // operator priority ACROSS buckets.
        const buckets = new Map();
        for (const c of candidatesByPriority) {
            if (!buckets.has(c.priority)) buckets.set(c.priority, []);
            buckets.get(c.priority).push(c.$el);
        }
        for (const bucket of buckets.values()) {
            for (const sType of DEMOTION_PRIORITY) {
                for (const $el of bucket) {
                    if (($el.data('stream-type') || '').toString().toUpperCase() === sType) {
                        pick = $el;
                        break;
                    }
                }
                if (pick) break;
            }
            if (pick) break;
        }

        if (!pick) {
            console.log(`[Throttle] CPU ${cpuPct.toFixed(0)}% > ${threshold}% but no demotable tile available`);
            return;
        }

        const cameraId = pick.data('camera-serial') || pick.attr('data-camera-serial');
        const cameraType = pick.data('camera-type') || pick.attr('data-camera-type');
        const streamType = pick.data('stream-type') || pick.attr('data-stream-type');

        console.log(`[Throttle] CPU ${cpuPct.toFixed(0)}% > ${threshold}% — demoting ${cameraId} (${streamType})`);
        try {
            await this.streamManager.stopIndividualStream(cameraId, pick, cameraType, streamType);
            this._demoted.push({ cameraId, cameraType, streamType });
        } catch (e) {
            console.warn(`[Throttle] Failed to demote ${cameraId}:`, e);
        }
    }

    async _restoreOne(cpuPct, floor) {
        const entry = this._demoted.pop();
        if (!entry) return;
        const $tile = this.$container.find(`.stream-item[data-camera-serial="${entry.cameraId}"]`);
        if (!$tile.length) return;
        console.log(`[Throttle] CPU ${cpuPct.toFixed(0)}% < ${floor}% — restoring ${entry.cameraId} (${entry.streamType})`);
        try {
            await this.streamManager.startStream(entry.cameraId, $tile, entry.cameraType, entry.streamType);
        } catch (e) {
            console.warn(`[Throttle] Failed to restore ${entry.cameraId}:`, e);
            // Put it back so we try again next cycle
            this._demoted.push(entry);
        }
    }

    async _restoreAll() {
        if (!this._demoted.length) return;
        console.log(`[Throttle] Throttle disabled — restoring ${this._demoted.length} tile(s)`);
        while (this._demoted.length) {
            await this._restoreOne(0, 999);
        }
    }
}
