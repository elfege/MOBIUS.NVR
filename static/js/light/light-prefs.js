/**
 * static/js/light/light-prefs.js — per-device tuning knobs for /light.
 *
 * Two integer-millisecond values, both localStorage-backed so different
 * kiosks can run at different cadences (rog: aggressive; tablet1:
 * conservative):
 *
 *   nvr_light_poll_ms       integer, default 2000, range [250, 5000]
 *     How often each visible tile re-fetches /api/snap/<serial>.
 *
 *   nvr_light_freshness_ms  integer, default 20000, range [1000, 60000]
 *     How stale a server-side cached frame is allowed to be before
 *     /api/snap returns 503 (no signal). Sent as `?max_age=<sec>`
 *     on every snapshot request (server expects seconds — we
 *     divide on the way out).
 *
 * Input normalization (any setter / URL param):
 *   - Decimal value (e.g. 2.5) -> treated as SECONDS, x1000 -> ms.
 *   - Integer < 250 (poll) or < 1000 (freshness) -> treated as
 *     SECONDS, x1000 -> ms.
 *   - Anything else is treated as already-in-ms.
 *   After conversion the value is clamped to the listed range.
 *
 * URL-param bootstrap: `?snap=<value>&freshness=<value>` on the /light
 * URL overrides + writes to localStorage. Used by chrome_nvr's
 * --snap / --freshness flags.
 */

const KEY_POLL          = 'nvr_light_poll_ms';
const KEY_FRESHNESS     = 'nvr_light_freshness_ms';
// Opt-in: when true the /api/snap URL carries ?source=go2rtc and the
// server tries go2rtc's pre-decoded /api/frame.jpeg path FIRST (lower
// latency than mediaserver's H.264 -> MJPEG transcode), falling back
// to the normal streaming_hub-based dispatch if go2rtc doesn't have
// the stream. Per-device — kiosks with go2rtc-fed cameras flip it on
// for free latency wins, others leave it off.
const KEY_PREFER_GO2RTC = 'nvr_light_prefer_go2rtc';

const DEFAULT_POLL_MS      = 2000;
const DEFAULT_FRESHNESS_MS = 20000;

// [min_ms, max_ms]. min_ms is also the "is this seconds?" threshold —
// any normalized value below min_ms after decimal-detection gets
// promoted from seconds to ms once before clamping.
const POLL_RANGE_MS      = [250,  5000];
const FRESHNESS_RANGE_MS = [1000, 60000];

/**
 * Normalize a user/URL-supplied value to integer ms.
 *   "2.5"  -> 2500   (decimal → seconds)
 *   "2"    -> 2000   (integer < min → seconds)
 *   "500"  -> 500    (integer ≥ min → ms; will be clamped if below min)
 *   "1500" -> 1500   (ms as-is)
 *   ""     -> null
 */
function _normalizeToMs(raw, range) {
    if (raw == null || raw === '') return null;
    const s = String(raw).trim();
    if (!s) return null;
    const n = parseFloat(s);
    if (!Number.isFinite(n) || n <= 0) return null;
    let ms;
    if (s.indexOf('.') !== -1 || n < range[0]) {
        // Looks like seconds — multiply.
        ms = Math.round(n * 1000);
    } else {
        ms = Math.round(n);
    }
    // Final clamp.
    return Math.max(range[0], Math.min(range[1], ms));
}

function _read(key, fallback, range) {
    try {
        const raw = localStorage.getItem(key);
        if (raw == null || raw === '') return fallback;
        // Stored values are integer-ms already. Just clamp defensively
        // in case the range was tightened in a later release.
        const v = parseInt(raw, 10);
        if (!Number.isFinite(v)) return fallback;
        return Math.max(range[0], Math.min(range[1], v));
    } catch (_) {
        return fallback;
    }
}

function _write(key, raw, range) {
    const ms = _normalizeToMs(raw, range);
    if (ms == null) return false;
    try { localStorage.setItem(key, String(ms)); } catch (_) { /* private-mode */ }
    return true;
}

export function getPollMs()        { return _read(KEY_POLL,      DEFAULT_POLL_MS,      POLL_RANGE_MS); }
export function getFreshnessMs()   { return _read(KEY_FRESHNESS, DEFAULT_FRESHNESS_MS, FRESHNESS_RANGE_MS); }
export function getFreshnessSec()  { return getFreshnessMs() / 1000; }  // server expects seconds

export function setPoll(rawValue)      { return _write(KEY_POLL,      rawValue, POLL_RANGE_MS); }
export function setFreshness(rawValue) { return _write(KEY_FRESHNESS, rawValue, FRESHNESS_RANGE_MS); }

/** Per-device opt-in: prefer go2rtc's pre-decoded JPEG over the
 *  mediaserver transcode path. Boolean stored as '1' / '0' so it
 *  survives the localStorage string round-trip cleanly. */
export function getPreferGo2rtc() {
    try {
        return localStorage.getItem(KEY_PREFER_GO2RTC) === '1';
    } catch (_) { return false; }
}
export function setPreferGo2rtc(on) {
    try { localStorage.setItem(KEY_PREFER_GO2RTC, on ? '1' : '0'); } catch (_) {}
}

/**
 * Called once at page load. If `?snap=<value>` or `?freshness=<value>`
 * are in the URL, parse + persist them. URL wins over existing
 * localStorage so chrome_nvr can re-pin the kiosk to a new cadence.
 */
export function applyUrlOverridesOnce() {
    try {
        const p = new URLSearchParams(window.location.search);
        const snap = p.get('snap');
        if (snap != null) setPoll(snap);
        const fresh = p.get('freshness');
        if (fresh != null) setFreshness(fresh);
    } catch (_) { /* no URLSearchParams or bad URL — fine */ }
    return { pollMs: getPollMs(), freshnessMs: getFreshnessMs() };
}

export const POLL_BOUNDS_MS      = POLL_RANGE_MS;
export const FRESHNESS_BOUNDS_MS = FRESHNESS_RANGE_MS;
