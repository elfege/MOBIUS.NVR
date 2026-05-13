/**
 * audit-outbox.js — durable browser-side audit-event queue.
 *
 * Phase 2 trigger-based audit covers server-side mutations automatically
 * (Postgres triggers + LISTEN/NOTIFY). This module covers the
 * complementary path: localStorage-only UI mutations (grid size, fit
 * mode, HD toggles, camera visibility, etc.) that never touch the
 * server but the operator still wants in the audit log for litigation
 * purposes.
 *
 * Per operator decision 2026-05-13:
 *   - Every UI mutation calls `auditOutbox.enqueue({...})`.
 *   - enqueue() writes the event SYNCHRONOUSLY to localStorage. Never
 *     throws (storage exceptions are swallowed); never blocks the UI.
 *   - A background flusher runs every 60 seconds, plus on visibility-
 *     change and beforeunload. It POSTs queued rows in a single batch
 *     to /api/audit/batch. On success, drops the flushed rows.
 *   - Each row carries a `retry_count`. After 60 failed retries
 *     (~60 minutes) the row is dropped. Operator accepts data loss
 *     for the extreme tail case.
 *
 * The trade-off: under sustained network outage longer than 60 minutes
 * with the page open the whole time, oldest events get dropped. This
 * is preferable to either (a) unbounded queue growth (memory) or
 * (b) UI blocking on every toggle.
 *
 * Storage key: `nvr_audit_outbox` (JSON array of event objects).
 */

const STORAGE_KEY    = 'nvr_audit_outbox';
const FLUSH_INTERVAL = 60_000;           // 60 s
const MAX_RETRIES    = 60;               // ~60 min tail
const BATCH_ENDPOINT = '/api/audit/batch';
const MAX_QUEUE_LEN  = 1000;             // hard cap to prevent localStorage bloat

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
        // Cap at MAX_QUEUE_LEN — keep the most recent rows. Under
        // pathological conditions (extreme spam + offline) we drop
        // oldest rather than let localStorage hit its quota.
        const capped = arr.length > MAX_QUEUE_LEN
            ? arr.slice(arr.length - MAX_QUEUE_LEN)
            : arr;
        localStorage.setItem(STORAGE_KEY, JSON.stringify(capped));
    } catch (e) {
        // localStorage full or unavailable — silent; the in-memory
        // page can still flush whatever it has in this session.
        console.warn('[audit-outbox] persist failed:', e);
    }
}

/** A flush in flight. Prevents concurrent POSTs from clobbering each other. */
let _flushInFlight = false;

export const auditOutbox = {
    /**
     * Append an audit event to the queue.
     * @param {object} ev
     *   - scope:       'global' | 'camera:<serial>' | 'host:<label>' | 'user:<id>' | 'device:<id>'
     *   - setting_key: identifying string (e.g. 'nvr_light_grid', 'mobius_host_label')
     *   - old_value:   any JSON-serializable value (or null)
     *   - new_value:   any JSON-serializable value (or null)
     *   - origin:      'ui' | 'api' | 'system_auto' (default 'ui')
     *   - note:        optional human-readable hint
     */
    enqueue(ev) {
        if (!ev || typeof ev !== 'object') return;
        if (!ev.scope || !ev.setting_key) return;

        const row = {
            ts:          new Date().toISOString(),
            scope:       String(ev.scope),
            setting_key: String(ev.setting_key),
            old_value:   ev.old_value === undefined ? null : ev.old_value,
            new_value:   ev.new_value === undefined ? null : ev.new_value,
            origin:      ev.origin || 'ui',
            note:        ev.note || null,
            retry_count: 0,
        };

        const q = _readQueue();
        q.push(row);
        _writeQueue(q);
    },

    /**
     * Attempt to flush the queue to /api/audit/batch.
     * Idempotent: if a flush is already in flight, this call no-ops.
     * Returns true on a successful batch (>=1 row accepted), false otherwise.
     */
    async flush() {
        if (_flushInFlight) return false;
        const q = _readQueue();
        if (q.length === 0) return true;

        _flushInFlight = true;
        try {
            // POST every row currently in the queue. The server returns
            // 200 with {accepted: N} or 207 with {accepted, rejected: [...]}.
            // Either way, drop the rows we sent on success; bump retry_count
            // on connection-level failure (network blip).
            const r = await fetch(BATCH_ENDPOINT, {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ events: q.map(({retry_count, ...rest}) => rest) }),
            });

            if (r.ok || r.status === 207) {
                // Either fully accepted or partially. Either way, we
                // don't retry these rows — server has decided. The
                // partial-reject case (207) drops the bad rows because
                // they're permanently malformed (no point retrying).
                _writeQueue([]);
                return true;
            }

            // Any other status: bump retry counts; drop rows that have
            // exhausted their budget.
            const survivors = q
                .map(r => ({ ...r, retry_count: (r.retry_count || 0) + 1 }))
                .filter(r => r.retry_count < MAX_RETRIES);
            _writeQueue(survivors);
            return false;
        } catch (e) {
            // Network error — bump and survive.
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
     * Wire up the periodic flusher + visibility/beforeunload triggers.
     * Idempotent — safe to call multiple times.
     */
    _bound: false,
    start() {
        if (this._bound) return;
        this._bound = true;
        // Periodic flush
        setInterval(() => { this.flush(); }, FLUSH_INTERVAL);
        // Tab visibility — flush on tab-becoming-hidden to capture
        // the last-second context before a tab close.
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) this.flush();
        });
        // Last-chance flush on tab close. Uses sendBeacon to dodge the
        // "no async during unload" rule that browsers enforce.
        window.addEventListener('beforeunload', () => {
            try {
                const q = _readQueue();
                if (q.length === 0) return;
                const blob = new Blob(
                    [JSON.stringify({ events: q.map(({retry_count, ...rest}) => rest) })],
                    { type: 'application/json' },
                );
                if (navigator.sendBeacon && navigator.sendBeacon(BATCH_ENDPOINT, blob)) {
                    _writeQueue([]);
                }
            } catch (_) {}
        });
        // First-load opportunistic flush (catches rows queued before the
        // tab was reopened).
        setTimeout(() => this.flush(), 1000);
    },

    /** For debugging / tests. */
    _size() { return _readQueue().length; },
    _clear() { _writeQueue([]); },
};
