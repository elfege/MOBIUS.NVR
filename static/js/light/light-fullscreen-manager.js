/**
 * LIGHT MODE — FULLSCREEN MANAGER (ES6 module + jQuery)
 * =====================================================
 *
 * What this module owns
 * ---------------------
 *
 * Single-camera fullscreen viewer for light mode, with two display
 * paths:
 *
 *   * **SD** — snapshot polling into an ``<img>`` (cheap, works
 *              everywhere, ~30 KB / 2 s).
 *   * **HD** — live HLS sub-stream into a ``<video>`` via ``hls.js``
 *              on browsers without native HLS, or the browser's
 *              native player on iOS / macOS Safari.
 *
 * The user toggles between them with the SD/HD pill in the bottom
 * bar. The choice is persisted in ``localStorage`` so the user's
 * preferred default sticks.
 *
 * Two-part fullscreen
 * -------------------
 *
 * Logical fullscreen (the in-page modal) is independent of the
 * browser's OS-level Fullscreen API:
 *
 *   1. The overlay element gets ``.active`` → covers the page.
 *   2. We additionally request OS fullscreen on the same element so
 *      the address bar / system chrome disappears.
 *
 * If (2) fails (older WebView, permission denied, etc.) (1) still
 * works, so the user always gets the modal.
 *
 * Grid suspension contract
 * ------------------------
 *
 * On open we tell the ``LightGridRenderer`` to ``suspend()`` — the
 * device stops fetching all the cameras the user isn't looking at.
 * On close we ``resume()``, which re-renders the whole grid (and
 * therefore re-issues every snapshot fetch — "refresh all").
 */


// =====================================================================
// Configuration
// =====================================================================

// Snapshot poll cadence — read per-tick from light-prefs so a
// gear-settings change affects fullscreen too. The grid uses the
// same source; the two stay in lockstep automatically.
import { getPollMs, getFreshnessSec, getPreferGo2rtc } from './light-prefs.js';

// Swipe threshold for left/right navigation between cameras inside
// the fullscreen overlay. Same heuristic as the grid swipe.
const SWIPE_DX_THRESHOLD_PX  = 60;
const SWIPE_DY_RATIO         = 1.5;

// hls.js tunables — keep latency low, but not so low that a stalled
// segment fetch hard-resets the playlist.
const HLS_LIVE_SYNC_DURATION   = 2;
const HLS_LIVE_MAX_LATENCY_SEC = 6;


// =====================================================================
// LightFullscreenManager
// =====================================================================

export class LightFullscreenManager {

    /**
     * @param {Object}   opts
     * @param {jQuery}   opts.$overlay    — modal root element
     * @param {jQuery}   opts.$backdrop   — dim layer behind the modal
     * @param {jQuery}   opts.$img        — SD <img> inside the overlay
     * @param {jQuery}   opts.$video      — HD <video> inside the overlay
     * @param {jQuery}   opts.$name       — camera-name span in fs-bar
     * @param {jQuery}   opts.$counter    — "i / N" span in fs-bar
     * @param {jQuery}   opts.$closeBtn   — × button
     * @param {jQuery}   opts.$qualSdBtn  — SD pill button
     * @param {jQuery}   opts.$qualHdBtn  — HD pill button
     * @param {Array}    opts.cameras     — [{id, name}, ...] (orchestrator-owned)
     * @param {Function} opts.getFitMode  — () => 'fill'|'contain'
     * @param {Object}   opts.gridRenderer — LightGridRenderer instance
     */
    constructor({
        $overlay, $backdrop, $img, $video, $name, $counter, $closeBtn,
        $qualSdBtn, $qualHdBtn, cameras, getFitMode, gridRenderer,
    }) {
        this.$overlay      = $overlay;
        this.$backdrop     = $backdrop;
        this.$img          = $img;
        this.$video        = $video;
        this.$name         = $name;
        this.$counter      = $counter;
        this.$closeBtn     = $closeBtn;
        this.$qualSdBtn    = $qualSdBtn;
        this.$qualHdBtn    = $qualHdBtn;
        this.cameras       = cameras;
        this.getFitMode    = getFitMode || (() => 'fill');
        this.gridRenderer  = gridRenderer;

        // Active state — true when the modal is open.
        this._active = false;
        // Index into ``cameras`` for the current viewer.
        this._index  = 0;
        // setInterval handle for SD polling. Null when not polling.
        this._sdTimer = null;
        // hls.js instance for HD path. Null when not playing.
        this._hls    = null;

        // SD or HD — persisted preference. Defaults to SD because
        // it's universal-compatible.
        const stored = localStorage.getItem('nvr_light_fs_quality');
        this._quality = (stored === 'hd') ? 'hd' : 'sd';

        this._wireQualityButtons();
        this._wireCloseButton();
        this._wireSwipe();
        this._wireFullscreenChangeListener();
    }


    // =================================================================
    // Public API
    // =================================================================

    /** True iff the overlay is currently open. */
    get isActive() { return this._active; }

    /** ``'sd'`` or ``'hd'``. */
    get quality()  { return this._quality; }

    /**
     * Open the overlay on the camera at ``index``. Suspends the grid
     * and requests OS-level fullscreen as part of the same user
     * gesture so the browser permits it.
     */
    open(index) {
        this._index  = index;
        this._active = true;

        // Persist so an hourly auto-reload keeps us in fullscreen.
        localStorage.setItem('nvr_light_fs_cam', this.cameras[index].id);

        this._applyQualityButtons();
        this.gridRenderer.suspend();

        this._updateView();
        this.$overlay.addClass('active');
        this.$backdrop.addClass('active');

        this._requestBrowserFullscreen(this.$overlay[0]);
    }

    /**
     * Close the overlay. Stops media, exits OS fullscreen, and tells
     * the grid renderer to ``resume()`` (which re-renders + restarts
     * every camera's snapshot poll — the "refresh all" the spec
     * calls for).
     */
    close() {
        this._active = false;
        this.$overlay.removeClass('active');
        this.$backdrop.removeClass('active');
        this._stopMedia();
        localStorage.removeItem('nvr_light_fs_cam');
        this._exitBrowserFullscreen();
        this.gridRenderer.resume();
    }

    /** Move ``-1`` (prev) or ``+1`` (next) through the camera list. */
    navigate(direction) {
        this._index += direction;
        this._updateView();
    }

    /**
     * Mirror a fit-mode change from the grid into the overlay so a
     * Stretch/Fit toggle in the top bar takes effect immediately on
     * the open camera.
     */
    applyFitMode(mode) {
        this.$img.css('object-fit', mode);
        this.$video.css('object-fit', mode);
    }


    // =================================================================
    // Internal — view rendering
    // =================================================================

    _updateView() {
        // Wrap-around navigation.
        if (this._index < 0) this._index = this.cameras.length - 1;
        if (this._index >= this.cameras.length) this._index = 0;

        const cam = this.cameras[this._index];
        this.$name.text(cam.name);
        this.$counter.text(`${this._index + 1} / ${this.cameras.length}`);

        // Always tear down the previous pipeline first — the camera
        // OR the quality may have changed.
        this._stopMedia();
        if (this._quality === 'hd') this._startHd(cam.id);
        else                        this._startSd(cam.id);
    }

    /** SD: snapshot poll into the <img>. */
    _startSd(cameraId) {
        this.$img.removeClass('hidden');
        this.$video.addClass('hidden');
        this.$img.css('object-fit', this.getFitMode());
        this.$img.attr('src', this._snapUrl(cameraId));
        this._sdTimer = setInterval(() => {
            this.$img.attr('src', this._snapUrl(cameraId));
        }, getPollMs());
    }

    /**
     * HD: ask the backend to start the sub-stream HLS playlist, then
     * play it. Falls back to SD silently if the start request fails
     * — the overlay never sits empty.
     */
    _startHd(cameraId) {
        this.$img.addClass('hidden');
        this.$video.removeClass('hidden');
        this.$video.css('object-fit', this.getFitMode());

        // We use the ``sub`` stream because mobile is bandwidth-
        // sensitive and small-screen HD doesn't gain much from the
        // main stream.
        const startedFor = cameraId;
        $.ajax({
            url:         `/api/stream/start/${encodeURIComponent(cameraId)}`,
            method:      'POST',
            contentType: 'application/json',
            data:        JSON.stringify({ type: 'sub' }),
        })
        .done((info) => {
            // Bail if the camera changed under us (user swiped fast).
            if (!this._active || this.cameras[this._index].id !== startedFor) return;
            const url = (info && info.stream_url)
                      ? info.stream_url
                      : `/hls/${encodeURIComponent(cameraId)}/index.m3u8`;
            this._attachHls(url);
        })
        .fail(() => {
            if (!this._active) return;
            // Graceful fallback so the user still sees a picture.
            this._quality = 'sd';
            this._applyQualityButtons();
            this._updateView();
        });
    }

    /** Attach an HLS playlist using hls.js or native HLS for Safari. */
    _attachHls(url) {
        const video = this.$video[0];

        // Native HLS — Safari on iOS / macOS plays .m3u8 directly.
        if (video.canPlayType('application/vnd.apple.mpegurl')) {
            video.src = url;
            video.play().catch(() => { /* autoplay blocked, harmless */ });
            return;
        }

        // hls.js — every other browser. Loaded as a global by the
        // template's <script> tag.
        if (typeof Hls !== 'undefined' && Hls.isSupported()) {
            this._hls = new Hls({
                liveSyncDuration:       HLS_LIVE_SYNC_DURATION,
                liveMaxLatencyDuration: HLS_LIVE_MAX_LATENCY_SEC,
            });
            this._hls.loadSource(url);
            this._hls.attachMedia(video);
            this._hls.on(Hls.Events.MANIFEST_PARSED, () => {
                video.play().catch(() => { /* autoplay blocked */ });
            });
        }
    }

    /** Stop SD timers + tear down hls.js + reset the <video>. */
    _stopMedia() {
        if (this._sdTimer) { clearInterval(this._sdTimer); this._sdTimer = null; }
        if (this._hls) {
            try { this._hls.destroy(); } catch (e) { /* ignore */ }
            this._hls = null;
        }
        const video = this.$video[0];
        if (video && !video.paused) {
            try { video.pause(); } catch (e) { /* ignore */ }
        }
        this.$video.removeAttr('src');
        try { video && video.load(); } catch (e) { /* ignore */ }
    }

    _snapUrl(cameraId) {
        const src = getPreferGo2rtc() ? '&source=go2rtc' : '';
        return `/api/snap/${encodeURIComponent(cameraId)}?_t=${Date.now()}&max_age=${getFreshnessSec().toFixed(2)}${src}`;
    }

    _applyQualityButtons() {
        this.$qualSdBtn.toggleClass('active', this._quality === 'sd');
        this.$qualHdBtn.toggleClass('active', this._quality === 'hd');
    }


    // =================================================================
    // Internal — wiring
    // =================================================================

    _wireQualityButtons() {
        this.$qualSdBtn.on('click', (e) => {
            e.stopPropagation();
            if (this._quality === 'sd') return;
            this._quality = 'sd';
            localStorage.setItem('nvr_light_fs_quality', 'sd');
            this._applyQualityButtons();
            this._updateView();
        });
        this.$qualHdBtn.on('click', (e) => {
            e.stopPropagation();
            if (this._quality === 'hd') return;
            this._quality = 'hd';
            localStorage.setItem('nvr_light_fs_quality', 'hd');
            this._applyQualityButtons();
            this._updateView();
        });
    }

    _wireCloseButton() {
        this.$closeBtn.on('click', (e) => {
            e.stopPropagation();
            this.close();
        });
        this.$backdrop.on('click', () => {
            if (this._active) this.close();
        });
    }

    /** Swipe left = next camera, swipe right = prev camera. */
    _wireSwipe() {
        let startX = 0, startY = 0;
        this.$overlay.on('touchstart', (e) => {
            const t = e.originalEvent.touches[0];
            startX = t.clientX; startY = t.clientY;
        });
        this.$overlay.on('touchend', (e) => {
            const t  = e.originalEvent.changedTouches[0];
            const dx = t.clientX - startX;
            const dy = t.clientY - startY;
            if (Math.abs(dx) > SWIPE_DX_THRESHOLD_PX
                && Math.abs(dx) > Math.abs(dy) * SWIPE_DY_RATIO) {
                this.navigate(dx < 0 ? 1 : -1);
            }
        });
    }

    /**
     * Listen to the OS-level fullscreen state — when the user exits
     * via ESC, gesture, or browser-back we want the same cleanup as
     * the X button. Without this, ESC drops OS fullscreen but leaves
     * the modal stranded on top of a frozen grid.
     */
    _wireFullscreenChangeListener() {
        const handler = () => {
            const fsEl = document.fullscreenElement
                      || document.webkitFullscreenElement
                      || document.mozFullScreenElement
                      || document.msFullscreenElement;
            if (!fsEl && this._active) this.close();
        };
        $(document).on('fullscreenchange webkitfullscreenchange '
                     + 'mozfullscreenchange MSFullscreenChange', handler);
    }


    // =================================================================
    // Internal — Fullscreen API plumbing (vendor-prefix dance)
    // =================================================================

    _requestBrowserFullscreen(el) {
        try {
            if (el.requestFullscreen)         return el.requestFullscreen();
            if (el.webkitRequestFullscreen)   return el.webkitRequestFullscreen();
            if (el.webkitEnterFullscreen)     return el.webkitEnterFullscreen();
            if (el.mozRequestFullScreen)      return el.mozRequestFullScreen();
            if (el.msRequestFullscreen)       return el.msRequestFullscreen();
        } catch (e) { /* in-page modal still covers the user */ }
    }

    _exitBrowserFullscreen() {
        const fsEl = document.fullscreenElement
                  || document.webkitFullscreenElement
                  || document.mozFullScreenElement
                  || document.msFullscreenElement;
        if (!fsEl) return;
        try {
            if (document.exitFullscreen)        return document.exitFullscreen();
            if (document.webkitExitFullscreen)  return document.webkitExitFullscreen();
            if (document.mozCancelFullScreen)   return document.mozCancelFullScreen();
            if (document.msExitFullscreen)      return document.msExitFullscreen();
        } catch (e) { /* ignore */ }
    }
}
