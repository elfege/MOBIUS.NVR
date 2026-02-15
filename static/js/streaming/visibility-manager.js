/**
 * Visibility Manager — Detects monitor standby/off via Page Visibility API
 *
 * When the page becomes hidden (monitor standby, screen lock, tab switch):
 *   - Shows a standby overlay with hypnotic CSS animation
 *   - Tears down all browser-side stream consumers (HLS, WebRTC, MJPEG)
 *   - Stops health monitor and camera state polling
 *
 * When the page becomes visible again:
 *   - Shows "Reloading streams..." message with sped-up animation
 *   - Reloads the entire page (cleanest reconnection strategy)
 *
 * Backend FFmpeg→MediaMTX pipelines are NOT affected — only browser-side
 * consumers are paused. Other clients and recording services continue.
 *
 * Uses Page Visibility API (document.visibilitychange) — fires when:
 *   - Monitor enters standby / DPMS off
 *   - Screen is locked (Win+L, Ctrl+Alt+L)
 *   - Browser tab is switched or minimized
 */

export class VisibilityManager {
    /**
     * @param {object} opts
     * @param {function} opts.onSleep  - Called when page becomes hidden (tear down streams)
     * @param {number}   opts.graceMs  - Ignore brief visibility flickers shorter than this (default: 3000ms)
     * @param {number}   opts.reloadDelayMs - Delay before page reload on wake (shows animation, default: 1800ms)
     */
    constructor(opts = {}) {
        this.onSleep = opts.onSleep || (() => {});
        this.graceMs = opts.graceMs ?? 3000;
        this.reloadDelayMs = opts.reloadDelayMs ?? 1800;

        this._overlay = null;
        this._titleEl = null;
        this._subtitleEl = null;
        this._hiddenSince = null;
        this._sleepExecuted = false;
        this._bound = this._handleVisibilityChange.bind(this);

        this._initOverlay();
    }

    /**
     * Start listening for visibility changes.
     * Call once after DOM is ready and streams are initialized.
     */
    start() {
        document.addEventListener('visibilitychange', this._bound);
        console.log('[Visibility] Manager started — monitoring page visibility');
    }

    /**
     * Stop listening (cleanup).
     */
    stop() {
        document.removeEventListener('visibilitychange', this._bound);
        console.log('[Visibility] Manager stopped');
    }

    /**
     * Find the overlay element in the DOM (injected by streams.html template).
     * If not found, the manager operates silently (no visual feedback).
     */
    _initOverlay() {
        this._overlay = document.getElementById('standby-overlay');
        if (this._overlay) {
            this._titleEl = this._overlay.querySelector('.standby-title');
            this._subtitleEl = this._overlay.querySelector('.standby-subtitle');
        } else {
            console.warn('[Visibility] Overlay element #standby-overlay not found — visual feedback disabled');
        }
    }

    /**
     * Core handler — called on every visibilitychange event.
     *
     * Hidden path:
     *   Records timestamp. After grace period, tears down streams and shows overlay.
     *
     * Visible path:
     *   If streams were torn down (sleep executed), show wake animation and reload page.
     *   If within grace period (brief flicker), cancel pending sleep.
     */
    _handleVisibilityChange() {
        if (document.hidden) {
            this._hiddenSince = Date.now();
            this._sleepExecuted = false;

            console.log(`[Visibility] Page hidden — grace period: ${this.graceMs}ms`);

            // Schedule sleep after grace period (ignores brief tab switches)
            this._graceTimer = setTimeout(() => {
                if (!document.hidden) return; // Came back during grace period

                console.log('[Visibility] Grace period elapsed — entering standby mode');
                this._enterStandby();
            }, this.graceMs);

        } else {
            // Page became visible
            const hiddenDuration = this._hiddenSince ? Date.now() - this._hiddenSince : 0;
            console.log(`[Visibility] Page visible — was hidden for ${(hiddenDuration / 1000).toFixed(1)}s`);

            // Cancel pending sleep if still in grace period
            if (this._graceTimer) {
                clearTimeout(this._graceTimer);
                this._graceTimer = null;
            }

            if (this._sleepExecuted) {
                // Streams were torn down — show wake animation and reload
                this._wakeAndReload();
            }
            // Otherwise: was just a brief flicker, nothing to do
        }
    }

    /**
     * Enter standby: show overlay, call onSleep to tear down streams.
     */
    _enterStandby() {
        this._sleepExecuted = true;

        // Show overlay
        if (this._overlay) {
            if (this._titleEl) this._titleEl.textContent = 'Streams Paused';
            if (this._subtitleEl) this._subtitleEl.textContent = 'Monitor standby detected';
            this._overlay.classList.remove('waking');
            this._overlay.classList.add('active');
        }

        // Tear down browser-side consumers
        try {
            this.onSleep();
            console.log('[Visibility] Streams torn down — standby mode active');
        } catch (e) {
            console.error('[Visibility] Error in onSleep callback:', e);
        }
    }

    /**
     * Wake up: show sped-up animation with reload message, then reload page.
     */
    _wakeAndReload() {
        console.log(`[Visibility] Waking up — reloading in ${this.reloadDelayMs}ms`);

        if (this._overlay) {
            if (this._titleEl) this._titleEl.textContent = 'Reloading Streams';
            if (this._subtitleEl) this._subtitleEl.textContent = 'Reconnecting all cameras...';
            this._overlay.classList.add('waking');
        }

        // Brief delay to show the wake animation, then full page reload
        setTimeout(() => {
            window.location.reload();
        }, this.reloadDelayMs);
    }
}
