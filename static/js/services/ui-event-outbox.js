/**
 * ui-event-outbox.js — durable browser-side queue for UI interaction events.
 *
 * Same pattern as audit-outbox.js (Phase 2 audit). Kept as a SEPARATE
 * module rather than extended in place so the operator-decision-2026-05-13
 * UI-event stream (clicks, keystrokes, focus, navigation) lands in its
 * own endpoint and table (/api/ui-event/batch → ui_event_log) without
 * polluting the settings-audit table.
 *
 * Behavior contract (mirrors audit-outbox):
 *   - enqueue() writes synchronously to localStorage, never throws,
 *     never blocks the UI thread.
 *   - A background flusher runs every 30s (more frequent than the
 *     settings outbox because UI events are higher-volume), plus on
 *     visibility-change and beforeunload (sendBeacon for last-chance).
 *   - Each row has a `retry_count`; rows that fail MAX_RETRIES times
 *     are dropped.
 *
 * Operator trade-off accepted: under sustained network outage with
 * the page open the whole time, oldest events get dropped after
 * MAX_RETRIES cycles. Preferable to unbounded queue growth.
 *
 * Storage key: `nvr_ui_event_outbox`.
 */

const STORAGE_KEY    = 'nvr_ui_event_outbox';
const FLUSH_INTERVAL = 30_000;            // 30 s — UI events flush faster than settings audit
const MAX_RETRIES    = 60;                // ~30 min tail
const BATCH_ENDPOINT = '/api/ui-event/batch';
const MAX_QUEUE_LEN  = 5000;              // higher cap than audit-outbox (UI events are spammier)
const MAX_BATCH_POST = 500;               // server caps at 1000; we cap at 500 for safer payload sizes

/** Read the current queue from localStorage. Never throws. */
function _readQueue() {
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (!raw) return [];
        const arr = JSON.parse(raw);
        return Array.isArray(arr) ? arr : [];
    } catch (e) {
        return [];
    }
}

/** Persist the queue. Never throws. */
function _writeQueue(arr) {
    try {
        // Cap at MAX_QUEUE_LEN — keep the most recent rows under pathological
        // offline-burst conditions rather than blowing localStorage quota.
        const capped = arr.length > MAX_QUEUE_LEN
            ? arr.slice(arr.length - MAX_QUEUE_LEN)
            : arr;
        localStorage.setItem(STORAGE_KEY, JSON.stringify(capped));
    } catch (e) {
        // Silent — in-memory page can still flush this session.
        console.warn('[ui-event-outbox] persist failed:', e);
    }
}

/** Read the operator-supplied kiosk identifier (set by chrome_nvr / settings). */
function _hostLabel() {
    try { return localStorage.getItem('mobius_host_label') || null; }
    catch (_) { return null; }
}

let _flushInFlight = false;

export const uiEventOutbox = {
    /**
     * Append a UI event row to the queue.
     * @param {object} ev
     *   - kind          required: click|keystroke|focus|blur|submit|navigation|modal_open|modal_close|scroll
     *   - target_id     optional
     *   - target_tag    optional
     *   - target_text   optional (caller truncates to 200 chars)
     *   - target_attrs  optional object
     *   - page_url      optional (caller fills with location.pathname+search)
     *   - extra         optional object (per-kind payload)
     */
    enqueue(ev) {
        if (!ev || typeof ev !== 'object') return;
        if (!ev.kind) return;

        const row = {
            ts:           new Date().toISOString(),
            kind:         String(ev.kind),
            target_id:    ev.target_id    || null,
            target_tag:   ev.target_tag   || null,
            target_text:  ev.target_text  || null,
            target_attrs: ev.target_attrs || null,
            page_url:     ev.page_url     || null,
            extra:        ev.extra        || null,
            retry_count:  0,
        };

        const q = _readQueue();
        q.push(row);
        _writeQueue(q);
    },

    /**
     * Attempt to flush queued rows to /api/ui-event/batch.
     * Idempotent: no-ops if a flush is already in flight.
     * Returns true when at least one batch was accepted.
     */
    async flush() {
        if (_flushInFlight) return false;
        const q = _readQueue();
        if (q.length === 0) return true;

        _flushInFlight = true;
        try {
            // Drain in chunks of MAX_BATCH_POST to keep the request body
            // bounded. We always send the OLDEST rows first so a network
            // partition still drains in order.
            const chunk = q.slice(0, MAX_BATCH_POST);
            const remaining = q.slice(MAX_BATCH_POST);

            const body = {
                host_label: _hostLabel(),
                events: chunk.map(({retry_count, ...rest}) => rest),
            };

            const r = await fetch(BATCH_ENDPOINT, {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });

            if (r.ok || r.status === 207) {
                // Server accepted (fully or with row-level rejects). Either
                // way, drop the chunk we sent — partially-rejected rows
                // are permanently malformed, no point retrying.
                _writeQueue(remaining);
                return true;
            }

            // HTTP error: bump retry counters and drop exhausted rows.
            const survivors = chunk
                .map(r => ({ ...r, retry_count: (r.retry_count || 0) + 1 }))
                .filter(r => r.retry_count < MAX_RETRIES);
            _writeQueue(survivors.concat(remaining));
            return false;
        } catch (e) {
            // Network error — bump and survive (whole queue, simpler).
            const survivors = q
                .map(r => ({ ...r, retry_count: (r.retry_count || 0) + 1 }))
                .filter(r => r.retry_count < MAX_RETRIES);
            _writeQueue(survivors);
            return false;
        } finally {
            _flushInFlight = false;
        }
    },

    /**
     * Wire up periodic + visibility + beforeunload flushers. Idempotent.
     */
    _bound: false,
    start() {
        if (this._bound) return;
        this._bound = true;

        setInterval(() => { this.flush(); }, FLUSH_INTERVAL);

        document.addEventListener('visibilitychange', () => {
            if (document.hidden) this.flush();
        });

        // beforeunload: sendBeacon for last-chance flush during tab close.
        window.addEventListener('beforeunload', () => {
            try {
                const q = _readQueue();
                if (q.length === 0) return;
                const blob = new Blob(
                    [JSON.stringify({
                        host_label: _hostLabel(),
                        events: q.map(({retry_count, ...rest}) => rest),
                    })],
                    { type: 'application/json' },
                );
                if (navigator.sendBeacon && navigator.sendBeacon(BATCH_ENDPOINT, blob)) {
                    _writeQueue([]);
                }
            } catch (_) {}
        });

        // First-load opportunistic flush — catches rows queued in a
        // previous session that the tab close didn't get to send.
        setTimeout(() => this.flush(), 1500);
    },

    /** For debugging. */
    _size() { return _readQueue().length; },
    _clear() { _writeQueue([]); },
};
