/**
 * static/js/streaming/freeze-watchdog.js
 *
 * Per-tile freeze watchdog. Polls `<video>.currentTime` on a fast
 * cadence (default 2s) and tags the stream tile with `.signal-lost`
 * as soon as the timestamp stops advancing for N polls (default 3 →
 * 6 seconds of stagnation). When `currentTime` advances again, the
 * class is removed.
 *
 * Why this exists (operator report 2026-06-19/20): the existing
 * HealthMonitor in health.js does currentTime tracking too, but its
 * thresholds (warmupMs=60s, staleAfterMs=20s, sampleIntervalMs=6s in
 * the production config) total up to ~86s of detection lag. During
 * that window the stream is FROZEN — last frame painted, no new
 * frames decoding — yet the tile still shows the badges as "Live"
 * because:
 *   - Canvas blank-pixel sampler never trips (frame isn't black)
 *   - Backend state.availability poll lags (publisher still appears
 *     active from the RTSP relay's point of view)
 *   - HealthMonitor's stale threshold is far in the future
 *
 * This watchdog is intentionally SCOPED to ONE thing: toggle the
 * `.signal-lost` class. It does NOT trigger restarts, backend pings,
 * or any recovery logic — that stays in HealthMonitor where it
 * already lives. The two run in parallel and don't interfere because:
 *   - HealthMonitor.markUnhealthy(...) → restart cascade
 *   - FreezeWatchdog._tag(serial, frozen) → CSS class toggle only
 *
 * When the underlying stream recovers naturally (e.g., camera-side
 * encoder snaps out of a brief stall) the watchdog removes the class
 * on its own without waiting for any other signal. Existing UI
 * health-monitor restart paths in stream.js handle the "stream needs
 * intervention" case.
 *
 * Detection lag with default settings: warmupMs + (stallPollsToTrip *
 * pollIntervalMs) = 10s + 6s = 16s worst case for a fresh tile (drops
 * to 6s once the tile is warmed up). That's an order of magnitude
 * faster than HealthMonitor's 86s while remaining conservative enough
 * to ride out brief network hiccups + GOP boundaries.
 *
 * Independent of platform: works in Chromium, Firefox, Safari, iOS
 * Safari (which is the deployment target — Defect 1 in memory
 * `project_frozen_stream_no_buttons_ipad_health_monitor` was first
 * surfaced on iPad). `<video>.currentTime` is the most stable HTML5
 * video interface; no browser-specific quirks.
 *
 * The detection here is FRONTEND-ONLY. When the user is looking at
 * the tile, the watchdog tells them visually that the picture is
 * frozen. Backend recovery (via HealthMonitor's onUnhealthy callback)
 * is a separate concern with its own timing.
 */

export class FreezeWatchdog {
    constructor(opts = {}) {
        this.opts = {
            // How often to sample currentTime, milliseconds. 2s is the
            // sweet spot between "responsive" and "doesn't burn CPU
            // sampling 100 tiles". A grid of 25 cameras = ~12.5 polls/s.
            pollIntervalMs: 2000,
            // How many consecutive polls of no-progress before the class
            // is applied. 3 polls × 2s = 6 seconds — long enough to ride
            // through GOP boundaries (typically 2-4s for IP cameras) +
            // brief network jitter; short enough that operator-facing
            // delay is acceptable.
            stallPollsToTrip: 3,
            // Threshold for "currentTime didn't advance". 50ms tolerates
            // sub-frame jitter from getCurrentTime() rounding. Most IP
            // cameras run at 15-30fps so frame-step is 33-66ms; 0.05s is
            // well below that and well above floating-point noise.
            currentTimeEpsilon: 0.05,
            // Grace period after attach. The first ~10s of a new stream
            // legitimately has currentTime=0 (no frame decoded yet) or
            // currentTime sitting on a single value while metadata
            // negotiates. We don't want the tile to flash signal-lost
            // during normal startup.
            warmupMs: 10000,
            // Hook for tests + diagnostics. Called with (serial, frozen)
            // every time the class state actually flips. Default no-op.
            onChange: (_serial, _frozen) => { },
            ...opts,
        };
        this.trackers = new Map();
    }

    /**
     * Begin watching a tile's <video> element.
     * Returns a detach function so callers can dispose individually
     * without needing to know the serial.
     *
     * Idempotent on the (serial, element) pair — re-attaching replaces
     * the old timer. Useful when a stream is restarted and the same
     * tile gets a fresh element ref.
     */
    attach(serial, videoEl) {
        this.detach(serial);
        if (!videoEl || (videoEl.tagName !== 'VIDEO' && !(videoEl instanceof HTMLVideoElement))) {
            // MJPEG streams render to <img>, not <video>. They have no
            // currentTime concept; the existing HealthMonitor handles
            // them via frame-signature sampling. Skip silently.
            return () => { };
        }
        const t = {
            el: videoEl,
            lastCurrentTime: -1,
            stallCount: 0,
            currentlyFrozen: false,
            warmupUntil: performance.now() + this.opts.warmupMs,
            timer: null,
        };
        t.timer = setInterval(() => this._tick(serial), this.opts.pollIntervalMs);
        this.trackers.set(serial, t);
        console.log(`[FreezeWatchdog] Attached for ${serial}`);
        return () => this.detach(serial);
    }

    detach(serial) {
        const t = this.trackers.get(serial);
        if (!t) return;
        if (t.timer) clearInterval(t.timer);
        this.trackers.delete(serial);
        // If the tile was tagged frozen, clear the class on detach so
        // a fresh attach doesn't carry over a stale signal.
        if (t.currentlyFrozen) {
            const $tile = $(`.stream-item[data-camera-serial="${serial}"]`);
            $tile.removeClass('signal-lost');
        }
    }

    /**
     * Internal: one poll iteration for a tile.
     * Order of bail-outs matters — warmup is checked FIRST so a fresh
     * tile never trips during normal startup.
     */
    _tick(serial) {
        const t = this.trackers.get(serial);
        if (!t || !t.el) return;

        // 1. Warmup grace — buffering / connecting / first-frame waits
        if (performance.now() < t.warmupUntil) return;

        // 2. Paused / ended streams are NOT frozen — operator paused
        //    intentionally or stream genuinely ended (HLS VOD). Reset
        //    counter so a resume doesn't immediately trip.
        if (t.el.paused || t.el.ended) {
            t.stallCount = 0;
            return;
        }

        // 3. Element disconnected — element is gone from the DOM (tile
        //    being torn down). Detach ourselves; we'll be re-attached
        //    if the stream comes back.
        if (!t.el.isConnected) {
            this.detach(serial);
            return;
        }

        const ct = t.el.currentTime || 0;

        if (Math.abs(ct - t.lastCurrentTime) < this.opts.currentTimeEpsilon) {
            // Stalled this poll.
            t.stallCount++;
            if (t.stallCount === this.opts.stallPollsToTrip && !t.currentlyFrozen) {
                this._tag(serial, true);
                t.currentlyFrozen = true;
            }
        } else {
            // Progressed.
            if (t.currentlyFrozen) {
                this._tag(serial, false);
                t.currentlyFrozen = false;
            }
            t.stallCount = 0;
            t.lastCurrentTime = ct;
        }
    }

    /**
     * Toggle the `.signal-lost` class on the tile. Fires `onChange`
     * for tests / diagnostics.
     */
    _tag(serial, frozen) {
        const $tile = $(`.stream-item[data-camera-serial="${serial}"]`);
        if (!$tile.length) return;
        if (frozen) {
            $tile.addClass('signal-lost');
            console.warn(`[FreezeWatchdog] ${serial}: FROZEN — currentTime stuck`);
        } else {
            $tile.removeClass('signal-lost');
            console.log(`[FreezeWatchdog] ${serial}: recovered — currentTime advancing`);
        }
        try { this.opts.onChange(serial, frozen); } catch (_) { /* swallow */ }
    }
}

/**
 * Factory matching the pattern used by makeHealthMonitor() in health.js.
 * Lets callers do `import { makeFreezeWatchdog }` and pass opts inline.
 */
export function makeFreezeWatchdog(opts) {
    return new FreezeWatchdog(opts);
}
