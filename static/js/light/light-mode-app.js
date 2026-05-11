/**
 * LIGHT MODE — APPLICATION ENTRY POINT (ES6 module + jQuery)
 * ==========================================================
 *
 * Top-level orchestrator for the /light page. Consumes:
 *
 *   * the camera list serialised by Flask into
 *     ``<script id="light-cameras" type="application/json">``,
 *   * jQuery (loaded as a global by the template),
 *   * hls.js  (loaded as a global by the template),
 *
 * and wires three classes together:
 *
 *   * ``LightGridRenderer``       — grid + tiles + per-tile polling
 *   * ``LightFullscreenManager``  — single-camera fullscreen viewer
 *   * (this file)                 — top-bar buttons, page-level state,
 *                                   visibility / pause / fit-mode bus
 *
 * Why split into modules?
 * -----------------------
 *
 * Per CLAUDE.md §3.10.5 ("ES6 modules and jQuery style for frontend,
 * always") the codebase ships frontend code as ES6 modules + jQuery.
 * The previous incarnation of this page was a 370-line ES5 IIFE
 * inlined into the template — non-conforming and untestable. This
 * file replaces it.
 */

import { LightGridRenderer }      from './light-grid-renderer.js';
import { LightFullscreenManager } from './light-fullscreen-manager.js';


// =====================================================================
// Constants exposed to other light-mode modules
// =====================================================================

// Default object-fit when the user has never picked one. 'fill'
// (stretch) makes the most of phone screens; users who prefer
// proportional get 'contain' via the top-bar toggle.
export const DEFAULT_FIT_MODE = 'fill';

// Hourly auto-reload — keeps WebView session fresh + bleeds memory
// leaks. Same value as the legacy IIFE.
const AUTO_RELOAD_MS = 60 * 60 * 1000;


// =====================================================================
// LightModeApp
// =====================================================================

class LightModeApp {

    constructor() {
        // Pull the camera list out of the JSON-script the template
        // emits. Template emits a dict {serial: {name, ...}}; we
        // flatten to [{id, name}] for the renderers.
        this.cameras = this._readCamerasFromDom();

        // Top-bar buttons.
        this.$gridBtn        = $('#grid-btn');
        this.$fitBtn         = $('#fit-btn');
        this.$pauseBtn       = $('#pause-btn');
        this.$refreshPageBtn = $('#refresh-page-btn');

        // Master pause flag — controls whether grid/FS pollers run.
        this._paused = false;

        // Compose the grid + fullscreen children. Order matters:
        // the grid renderer is constructed first, then the fullscreen
        // manager, because the latter needs a reference to the
        // former for suspend()/resume().
        this.grid = new LightGridRenderer({
            $grid:       $('#grid'),
            $prevBtn:    $('#prev-btn'),
            $nextBtn:    $('#next-btn'),
            $pageInfo:   $('#page-info'),
            cameras:     this.cameras,
            onDoubleTap: (idx) => this.fullscreen.open(idx),
        });

        this.fullscreen = new LightFullscreenManager({
            $overlay:     $('#fs-overlay'),
            $backdrop:    $('#fs-backdrop'),
            $img:         $('#fs-img'),
            $video:       $('#fs-video'),
            $name:        $('#fs-name'),
            $counter:     $('#fs-counter'),
            $closeBtn:    $('#fs-close'),
            $qualSdBtn:   $('#fs-quality-sd'),
            $qualHdBtn:   $('#fs-quality-hd'),
            cameras:      this.cameras,
            getFitMode:   () => this.grid.fitMode,
            gridRenderer: this.grid,
        });
    }

    /** Boot the whole thing. Idempotent — safe to call once on DOM ready. */
    init() {
        // Reflect the renderer's persisted defaults into the top-bar.
        this.$gridBtn.text(this.grid.gridLabel);
        this.$fitBtn.text(this.grid.fitMode === 'fill' ? 'Stretch' : 'Fit');
        this.$fitBtn.toggleClass('active', this.grid.fitMode !== 'fill');

        this._wireTopBar();
        this._wireKeyboard();
        this._wireVisibility();
        this._wireRemoteFullscreenSwitch();

        this.grid.render();
        this._restoreFullscreenIfPersisted();
        this._scheduleAutoReload();
    }

    /**
     * Subscribe to the SocketIO 'fullscreen_request' event broadcast by
     * POST /api/fullscreen/switch. Reuses fullscreen.open() so the same
     * localStorage persistence native taps trigger applies.
     *
     * host_label filtering: if the broadcast specifies a host_label and
     * this browser has one bound (localStorage.mobius_host_label, the
     * same key the throttle controller / visibility bridge use), we only
     * act on a match. Unscoped broadcasts (no host_label) reach every
     * viewer.
     */
    _wireRemoteFullscreenSwitch() {
        if (typeof io === 'undefined') return;  // socket.io-client not loaded
        let myLabel = null;
        try { myLabel = localStorage.getItem('mobius_host_label') || null; } catch (_) {}
        try {
            const sock = io('/stream_events', { transports: ['websocket', 'polling'] });
            sock.on('fullscreen_request', (msg) => {
                if (!msg || !msg.serial) return;
                if (msg.host_label && myLabel && msg.host_label !== myLabel) return;
                const idx = this.cameras.findIndex((c) => c.id === msg.serial);
                if (idx < 0) return;
                this.fullscreen.open(idx);
            });
            sock.on('fullscreen_exit', (msg) => {
                if (msg && msg.host_label && myLabel && msg.host_label !== myLabel) return;
                if (this.fullscreen.isActive) {
                    this.fullscreen.close();
                } else {
                    // Not currently fullscreen — still clear persistence so
                    // the next page reload doesn't restore one.
                    try { localStorage.removeItem('nvr_light_fs_cam'); } catch (_) {}
                }
            });
        } catch (e) {
            console.warn('[LightModeApp] remote fullscreen-switch bind failed:', e);
        }
    }


    // =================================================================
    // Internal helpers
    // =================================================================

    _readCamerasFromDom() {
        const raw = $('#light-cameras').text();
        try {
            const data = JSON.parse(raw);
            // Two accepted shapes:
            //   - Array (current)   — [{serial, name, ...}, ...] — preserves
            //     server-side display_order sort because JSON arrays don't
            //     get reordered by Flask's tojson sort_keys=True.
            //   - Object (legacy)   — {serial: {name, ...}, ...} — used to
            //     arrive sorted by display_order, but Flask's tojson sorts
            //     dict keys alphabetically before serialization, so this
            //     shape *lost* the order. Kept here for backward compat
            //     in case an older template still embeds a dict.
            if (Array.isArray(data)) {
                return data.map((cam) => ({
                    id:   cam.serial,
                    name: cam.name || cam.serial,
                }));
            }
            return Object.entries(data).map(([serial, info]) => ({
                id:   serial,
                name: (info && info.name) || serial,
            }));
        } catch (e) {
            console.error('[LightModeApp] failed to parse camera JSON', e);
            return [];
        }
    }

    _wireTopBar() {
        // Grid-size cycle: 2x2 → 3x3 → 4x4 → 2x2.
        this.$gridBtn.on('click', () => {
            this.grid.cycleGridSize();
            this.$gridBtn.text(this.grid.gridLabel);
        });

        // Fit-mode toggle. Mirrors into the fullscreen overlay so an
        // open camera flips immediately too.
        this.$fitBtn.on('click', () => {
            const mode = this.grid.toggleFitMode();
            this.$fitBtn.text(mode === 'fill' ? 'Stretch' : 'Fit');
            this.$fitBtn.toggleClass('active', mode !== 'fill');
            this.fullscreen.applyFitMode(mode);
        });

        // Pause / resume — stop polling without leaving the page.
        this.$pauseBtn.on('click', () => {
            this._paused = !this._paused;
            this.$pauseBtn.text(this._paused ? 'Resume' : 'Pause');
            this.$pauseBtn.toggleClass('active', this._paused);
            if (this._paused) this.grid.suspend();
            else              this.grid.resume();
        });

        // Hard reload — bumps a query param so the back/forward cache
        // doesn't sit on a stale page state.
        this.$refreshPageBtn.on('click', () => {
            try {
                const u = new URL(window.location.href);
                u.searchParams.set('_r', Date.now());
                window.location.replace(u.toString());
            } catch (e) {
                window.location.reload();
            }
        });
    }

    /** Keyboard nav — arrows page the grid, or navigate cameras inside FS. */
    _wireKeyboard() {
        $(document).on('keydown', (e) => {
            if (this.fullscreen.isActive) {
                if (e.key === 'ArrowLeft')  return this.fullscreen.navigate(-1);
                if (e.key === 'ArrowRight') return this.fullscreen.navigate(1);
                if (e.key === 'Escape')     return this.fullscreen.close();
                return;
            }
            // Grid pagination — synthesise clicks on the prev/next
            // buttons so the renderer's bounds checks + render() call
            // run through a single code path.
            if (e.key === 'ArrowLeft')  $('#prev-btn').trigger('click');
            if (e.key === 'ArrowRight') $('#next-btn').trigger('click');
        });
    }

    /** Pause polling when the tab is hidden, resume on visible. */
    _wireVisibility() {
        $(document).on('visibilitychange', () => {
            if (document.hidden && !this._paused) {
                // Don't flip the user-visible pause state — just
                // halt the timers so a backgrounded tab is silent.
                this.grid.suspend();
            } else if (!document.hidden && !this._paused
                       && !this.fullscreen.isActive) {
                this.grid.resume();
            }
        });
    }

    /**
     * Reopen fullscreen on page load.
     *
     * Priority:
     *   1. ?fullscreen=<nickname|serial> from the URL — the server resolved
     *      nickname to a canonical serial in window.FULLSCREEN_REQUEST.
     *      Wins over a saved state so external links / shares behave
     *      predictably.
     *   2. localStorage 'nvr_light_fs_cam' — the camera the user was on
     *      last time the page reloaded.
     *
     * fullscreen.open() writes the localStorage entry itself, so the URL
     * path also rewrites memory and a subsequent reload without the query
     * param keeps the user where they were.
     */
    _restoreFullscreenIfPersisted() {
        const requested = (typeof window.FULLSCREEN_REQUEST === 'string' && window.FULLSCREEN_REQUEST)
            ? window.FULLSCREEN_REQUEST
            : null;
        const savedSerial = requested || localStorage.getItem('nvr_light_fs_cam');
        if (!savedSerial) return;
        const idx = this.cameras.findIndex((c) => c.id === savedSerial);
        if (idx >= 0) this.fullscreen.open(idx);
    }

    _scheduleAutoReload() {
        setTimeout(() => window.location.reload(), AUTO_RELOAD_MS);
    }
}


// =====================================================================
// Bootstrap
// =====================================================================

$(() => {
    const app = new LightModeApp();
    app.init();
    // Expose for in-browser debugging only — never used by production code.
    window.lightModeApp = app;
});
