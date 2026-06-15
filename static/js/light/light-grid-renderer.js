/**
 * LIGHT MODE — GRID RENDERER (ES6 module + jQuery)
 * ================================================
 *
 * What this module owns
 * ---------------------
 *
 * Everything that draws the camera grid in light mode and keeps each
 * tile's snapshot polling alive:
 *
 *   * Layout:  apply CSS-grid columns/rows from the user's chosen
 *              "2x2 / 3x3 / 4x4" preference.
 *   * Tiles:   build one ``.tile`` element per camera on the current
 *              page — image, label, per-tile refresh button.
 *   * Polling: every ``POLL_INTERVAL_MS`` ms each visible tile fetches
 *              a fresh JPEG from ``/api/snap/<serial>``.
 *   * Pagination: prev / next page buttons + swipe gestures on the
 *              grid container.
 *   * Fit mode: 'fill' (stretch) vs 'contain' (proportional); applied
 *              to every <img> in the grid AND broadcast via callback
 *              so the fullscreen manager can mirror the choice.
 *
 * What this module does NOT own
 * -----------------------------
 *
 *   * Fullscreen mode — that's ``LightFullscreenManager``. We expose
 *     a ``onTileDoubleTap`` hook so the orchestrator can wire a
 *     double-tap on a tile to fullscreen-open.
 *   * Pause / resume — owned by the orchestrator, which calls our
 *     ``suspend()`` / ``resume()``.
 *   * Top-bar buttons — also orchestrator. We just publish events.
 *
 * Why a separate class?
 * ---------------------
 *
 * Per CLAUDE.md RULE 12.2.1 the frontend uses ES6 modules + jQuery. The
 * old inline IIFE in ``streams_light.html`` violated that rule (and
 * conflated grid + fullscreen + pagination + top-bar wiring into one
 * 370-line blob). Splitting concerns into three classes makes each
 * one independently testable and re-usable; the template just
 * provides a DOM root + a JSON cameras payload.
 */

import { DEFAULT_FIT_MODE } from './light-mode-app.js';


// =====================================================================
// Configuration
// =====================================================================
//
// Grid sizes the user can cycle through with the "Grid" top-bar button.
// First entry is the default. Persisted as an integer index into this
// array via ``localStorage['nvr_light_grid']``.
export const GRID_SIZES = Object.freeze([
    { cols: 2, rows: 2, label: '2x2' },
    { cols: 3, rows: 3, label: '3x3' },
    { cols: 4, rows: 4, label: '4x4' },
]);

// Snapshot poll interval per tile. Now per-device-tunable via
// localStorage + the gear in the topbar (or chrome_nvr --snap=<sec>).
// See light-prefs.js for storage / normalization. The previous
// hardcoded 2000 ms is the default fallback.
import { getPollMs, getFreshnessSec, getPreferGo2rtc } from './light-prefs.js';

// Tap-vs-double-tap discrimination window. Anything below this is
// counted as a continuation of the previous tap.
const DOUBLE_TAP_WINDOW_MS = 350;

// Touch-show duration for the per-tile refresh button on touch
// devices (where :hover never fires).
const TILE_REFRESH_TOUCH_VISIBLE_MS = 2500;


// =====================================================================
// LightGridRenderer
// =====================================================================

export class LightGridRenderer {

    /**
     * @param {Object}   opts
     * @param {jQuery}   opts.$grid        — container that will hold the tiles
     * @param {jQuery}   opts.$prevBtn     — previous-page button
     * @param {jQuery}   opts.$nextBtn     — next-page button
     * @param {jQuery}   opts.$pageInfo    — "1 / 3" text element
     * @param {Array}    opts.cameras      — [{id, name}, ...]
     * @param {Function} opts.onDoubleTap  — (cameraIndex) => void
     */
    constructor({ $grid, $prevBtn, $nextBtn, $pageInfo, cameras, onDoubleTap }) {
        this.$grid       = $grid;
        this.$prevBtn    = $prevBtn;
        this.$nextBtn    = $nextBtn;
        this.$pageInfo   = $pageInfo;
        this.cameras     = cameras;
        this.onDoubleTap = onDoubleTap || (() => {});

        // Grid-size index (cycles 2x2 → 3x3 → 4x4).
        const stored = parseInt(localStorage.getItem('nvr_light_grid') || '0', 10);
        this._gridSizeIdx = (stored >= 0 && stored < GRID_SIZES.length) ? stored : 0;

        // 'fill' (stretch) or 'contain' (proportional).
        const storedFit = localStorage.getItem('nvr_light_fit');
        this._fitMode = (storedFit === 'fill' || storedFit === 'contain')
                      ? storedFit : DEFAULT_FIT_MODE;

        // Pagination cursor + state. The cursor is persisted in
        // localStorage so a page reload doesn't drop the user back on
        // page 1 of the grid — operator-flagged 2026-05-14 as the
        // dominant /light annoyance. Clamped to a valid range inside
        // render() against the live _totalPages, so a saved page 3 of
        // a now-1-page grid (e.g. after grid-size cycle) self-corrects.
        const storedPage = parseInt(localStorage.getItem('nvr_light_page') || '0', 10);
        this._curPage    = (Number.isInteger(storedPage) && storedPage >= 0) ? storedPage : 0;
        this._totalPages = Math.max(1, Math.ceil(cameras.length / this.perPage));

        // Whether we are currently polling. Suspended while fullscreen
        // is active so the device only fetches the FS camera.
        this._suspended = false;

        // Per-tile snapshot interval handles. Map<cameraId, intervalId>.
        this._timers = new Map();

        this._wirePagination();
        this._wireSwipe();
    }


    // =================================================================
    // Public API — used by the orchestrator
    // =================================================================

    /** Render the current page. Idempotent — safe to call repeatedly. */
    render() {
        this._stopAllTimers();
        this._totalPages = Math.max(1, Math.ceil(this.cameras.length / this.perPage));
        if (this._curPage >= this._totalPages) this._curPage = this._totalPages - 1;
        if (this._curPage < 0) this._curPage = 0;
        // Persist on every render — covers prev/next/swipe AND the
        // grid-size-cycle clamp above, without each call site needing
        // to remember to save. render() is called on user actions, not
        // on every snapshot poll, so the write volume is fine.
        try { localStorage.setItem('nvr_light_page', String(this._curPage)); } catch (_) {}

        this._applyGridCss();

        this.$grid.empty();
        const start = this._curPage * this.perPage;
        const slice = this.cameras.slice(start, start + this.perPage);
        slice.forEach((cam) => this.$grid.append(this._buildTile(cam)));

        this._updatePagination();
    }

    /** Cycle 2x2 → 3x3 → 4x4 → 2x2. Persists + re-renders. */
    cycleGridSize() {
        this._gridSizeIdx = (this._gridSizeIdx + 1) % GRID_SIZES.length;
        localStorage.setItem('nvr_light_grid', this._gridSizeIdx);
        this.render();
    }

    /** Toggle fill ↔ contain. Returns the new fit-mode value. */
    toggleFitMode() {
        this._fitMode = (this._fitMode === 'fill') ? 'contain' : 'fill';
        localStorage.setItem('nvr_light_fit', this._fitMode);
        this.$grid.find('.tile img').css('object-fit', this._fitMode);
        return this._fitMode;
    }

    /**
     * Halt every running snapshot timer AND hide the grid container.
     * Used by the fullscreen manager so the device stops fetching the
     * 15 cameras the user isn't looking at. Idempotent.
     */
    suspend() {
        this._suspended = true;
        this._stopAllTimers();
        this.$grid.hide();
    }

    /**
     * Reverse of ``suspend()`` — show the grid and re-render to
     * rebuild every tile and restart its timer.
     */
    resume() {
        this._suspended = false;
        this.$grid.show();
        this.render();
    }

    /** Public read-only accessors. */
    get currentGrid()  { return GRID_SIZES[this._gridSizeIdx]; }
    get perPage()      { return this.currentGrid.cols * this.currentGrid.rows; }
    get fitMode()      { return this._fitMode; }
    get gridLabel()    { return this.currentGrid.label; }
    get isSuspended()  { return this._suspended; }


    // =================================================================
    // Internal helpers
    // =================================================================

    /** Build the cache-busted snapshot URL for a single camera.
     *  Sends per-device freshness window via ?max_age — the server
     *  uses this as the override for the shared frame buffer's
     *  staleness check, letting kiosks pick their own tolerance. */
    _snapUrl(cameraId) {
        const src = getPreferGo2rtc() ? '&source=go2rtc' : '';
        return `/api/snap/${encodeURIComponent(cameraId)}?_t=${Date.now()}&max_age=${getFreshnessSec().toFixed(2)}${src}`;
    }

    /** Apply CSS-grid columns/rows from the current grid choice. */
    _applyGridCss() {
        const g = this.currentGrid;
        this.$grid.css({
            'grid-template-columns': `repeat(${g.cols}, 1fr)`,
            'grid-template-rows':    `repeat(${g.rows}, 1fr)`,
        });
    }

    /**
     * Build one tile (jQuery element) for ``cam`` and start its
     * snapshot poller (unless suspended / paused upstream — caller
     * is responsible for suspending us in that case).
     */
    _buildTile(cam) {
        const $tile = $('<div class="tile"></div>').attr('data-id', cam.id);

        // Image — the actual snapshot. ``object-fit`` is set inline so
        // it tracks the user's fit-mode toggle without restyles.
        const $img = $('<img>')
            .attr('alt', cam.name)
            .css('object-fit', this._fitMode)
            .attr('src', this._snapUrl(cam.id))
            .on('error', () => {
                // Show a placeholder rather than retrying a broken URL.
                if (!$tile.find('.tile-error').length) {
                    $tile.append('<div class="tile-error">No signal</div>');
                }
                // Drop the stale bitmap, don't just hide it. The backend
                // returns 503 once a frame ages past the freshness window;
                // on that error we must NOT keep the last-good frame around
                // (a dead camera showing a frozen frame looks live — bug B1).
                // The next poll re-sets src and retries the fetch.
                $img.removeAttr('src');
                $img.hide();
                // Stop the manual-refresh spinner — the fetch resolved (as a
                // failure). Without this the ↻ button spins forever after a
                // click and looks like it "did nothing".
                $tile.find('.tile-refresh').removeClass('spinning');
            })
            .on('load', () => {
                $img.show();
                $tile.find('.tile-error').remove();
                // Stop the manual-refresh spinner now that a fresh frame
                // arrived — gives the ↻ button visible feedback.
                $tile.find('.tile-refresh').removeClass('spinning');
            });

        // Camera label — shown along the bottom edge.
        const $label = $('<div class="tile-label"></div>').text(cam.name);

        // Per-tile refresh button — fades in on hover (mouse) or touch.
        // Click forces a fresh snapshot fetch for THIS tile only.
        const $refresh = $('<button class="tile-refresh" type="button" title="Refresh this stream">↻</button>')
            .on('click', (e) => {
                e.stopPropagation();
                e.preventDefault();
                $refresh.removeClass('spinning');
                // Force reflow so the animation restarts on rapid clicks.
                void $refresh[0].offsetWidth;
                $refresh.addClass('spinning');
                $img.attr('src', this._snapUrl(cam.id));
            });

        $tile.append($img).append($label).append($refresh);

        // Touch devices: tap once to reveal the refresh button briefly,
        // since :hover doesn't fire there.
        let hideTimer = null;
        $tile.on('touchstart', () => {
            $tile.addClass('show-refresh');
            if (hideTimer) clearTimeout(hideTimer);
            hideTimer = setTimeout(() => $tile.removeClass('show-refresh'),
                                   TILE_REFRESH_TOUCH_VISIBLE_MS);
        });

        // Double-tap → fullscreen open. We use jQuery click rather than
        // dblclick because dblclick fires unreliably on mobile WebKit.
        let lastTap = 0;
        $tile.on('click', (e) => {
            const now = Date.now();
            if (now - lastTap < DOUBLE_TAP_WINDOW_MS) {
                e.preventDefault();
                const idx = this.cameras.indexOf(cam);
                if (idx >= 0) this.onDoubleTap(idx);
            }
            lastTap = now;
        });

        // Start polling (unless we're suspended — defensive guard).
        // Cadence read fresh on each tile so a gear-settings change
        // takes effect at the next tile render() without a page reload.
        if (!this._suspended) {
            const id = setInterval(() => {
                $img.attr('src', this._snapUrl(cam.id));
            }, getPollMs());
            this._timers.set(cam.id, id);
        }

        return $tile;
    }

    /** Stop every running tile timer. */
    _stopAllTimers() {
        for (const id of this._timers.values()) clearInterval(id);
        this._timers.clear();
    }

    _updatePagination() {
        this.$prevBtn.prop('disabled', this._curPage <= 0);
        this.$nextBtn.prop('disabled', this._curPage >= this._totalPages - 1);
        this.$pageInfo.text(`${this._curPage + 1} / ${this._totalPages}`);
    }

    _wirePagination() {
        this.$prevBtn.on('click', () => {
            if (this._curPage > 0) { this._curPage--; this.render(); }
        });
        this.$nextBtn.on('click', () => {
            if (this._curPage < this._totalPages - 1) {
                this._curPage++; this.render();
            }
        });
    }

    /**
     * Horizontal swipe on the grid → page back / forward. Only
     * triggers when |dx| > 60 px and the swipe is clearly horizontal
     * (|dx| > 1.5 × |dy|), so vertical scroll-y intent is preserved.
     */
    _wireSwipe() {
        let startX = 0, startY = 0;
        this.$grid.on('touchstart', (e) => {
            const t = e.originalEvent.touches[0];
            startX = t.clientX; startY = t.clientY;
        });
        this.$grid.on('touchend', (e) => {
            const t  = e.originalEvent.changedTouches[0];
            const dx = t.clientX - startX;
            const dy = t.clientY - startY;
            if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5) {
                if (dx < 0 && this._curPage < this._totalPages - 1) {
                    this._curPage++; this.render();
                } else if (dx > 0 && this._curPage > 0) {
                    this._curPage--; this.render();
                }
            }
        });
    }
}
