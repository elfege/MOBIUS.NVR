/**
 * ui-event-tracker.js — delegated DOM event capture for UI audit log.
 *
 * Operator decision 2026-05-13: complete traceability of every click and
 * keystroke for litigation-grade accountability and hacker forensics.
 *
 * Design constraints (all required by operator):
 *   1. ONE document-level listener per event type (no per-element wiring).
 *      Capture phase is used so we observe events before stopPropagation
 *      can swallow them.
 *   2. NEVER preventDefault / stopPropagation — the tracker is invisible
 *      to the rest of the page.
 *   3. NEVER block the event loop — enqueue() is a localStorage write
 *      only, returns immediately.
 *   4. Password masking: type=password OR data-sensitive=true fields
 *      record `{key:'*'}`. The literal character NEVER leaves the
 *      browser, by design.
 *   5. Rate cap on keystrokes: 20 events / sec / target. Above that, a
 *      summary "rate-clipped" event is logged once at the next second
 *      boundary with the dropped count. Prevents log explosion under
 *      held-key / paste-spam scenarios.
 *
 * Enqueue path: every captured event becomes a single uiEventOutbox.enqueue
 * call. The outbox flushes to POST /api/ui-event/batch every 30s.
 *
 * Wire-up: import { uiEventTracker } from this file, then call
 *   uiEventTracker.start(uiEventOutbox)
 * once on page load.
 */

const TEXT_TRUNCATE      = 200;
const SELECTOR_DEPTH     = 4;       // ancestor levels for selector path
const ATTR_KEYS          = [
    'class', 'name', 'type', 'role', 'data-action',
    'aria-label', 'title', 'data-cam', 'data-serial',
];
const KEYSTROKE_RATE_CAP = 20;      // events/sec/target before clipping

// ---------- Per-second rate limiter for keystrokes -------------------------
//
// Map of `targetKey -> { second: <unix-second>, count: <int>, dropped: <int> }`.
// `targetKey` is the most stable identifier we can derive (id, then name,
// then a synthetic tag+index path). When count > KEYSTROKE_RATE_CAP for the
// current second, we drop further keystrokes for that target until the
// second rolls over, then emit a single rate-clipped summary event.
//
// This is deliberately in-memory (not persisted). On reload the limiter
// resets — that's fine: a held-key burst across a reload is implausible
// and we'd rather forget the limiter state than mis-summarize across
// the boundary.

const _rateMap = new Map();

/** Stable per-target rate-limit key. */
function _rateKey(target) {
    if (!target) return '__null__';
    if (target.id) return `#${target.id}`;
    if (target.name) return `[name=${target.name}]`;
    return `${target.tagName || '?'}@${_indexInParent(target)}`;
}

function _indexInParent(el) {
    if (!el || !el.parentNode) return 0;
    return Array.prototype.indexOf.call(el.parentNode.children, el);
}

/**
 * Returns true if the caller should DROP this keystroke event.
 * Side effect: when transitioning to a new second, emits a summary
 * rate-clipped event for any target that was clipped in the prior second.
 */
function _shouldDropKeystroke(target, enqueueFn) {
    const key = _rateKey(target);
    const nowSec = Math.floor(Date.now() / 1000);
    const entry = _rateMap.get(key);

    if (!entry || entry.second !== nowSec) {
        // Second rolled over (or first event for this target).
        // Flush a summary if the previous second had drops.
        if (entry && entry.dropped > 0) {
            try {
                enqueueFn({
                    kind: 'keystroke',
                    target_id:   target.id || null,
                    target_tag:  target.tagName || null,
                    target_text: _safeTargetText(target),
                    target_attrs: _attrSnapshot(target),
                    page_url:    _pageUrl(),
                    extra: {
                        key: '<rate-clipped>',
                        clipped: entry.dropped,
                        second: entry.second,
                    },
                });
            } catch (_) {}
        }
        _rateMap.set(key, { second: nowSec, count: 1, dropped: 0 });
        return false;
    }

    if (entry.count < KEYSTROKE_RATE_CAP) {
        entry.count += 1;
        return false;
    }
    entry.dropped += 1;
    return true;
}

// ---------- Target descriptor helpers --------------------------------------

/** Collapse newlines + extra whitespace, then truncate. */
function _normalizeText(s) {
    if (s == null) return null;
    return String(s).replace(/\s+/g, ' ').trim().slice(0, TEXT_TRUNCATE);
}

/**
 * Build the human-readable target_text. For input/textarea/password
 * fields we NEVER use the value (PII risk + password). We use
 * aria-label, then placeholder, then name.
 */
function _safeTargetText(el) {
    if (!el) return null;
    const tag = (el.tagName || '').toUpperCase();
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') {
        return _normalizeText(
            el.getAttribute('aria-label')
            || el.getAttribute('placeholder')
            || el.getAttribute('name')
            || ''
        );
    }
    // For buttons/links/spans, innerText is the right user-visible label.
    return _normalizeText(el.innerText || el.textContent || '');
}

/** Snapshot the allow-listed attributes that are actually set. */
function _attrSnapshot(el) {
    if (!el || !el.getAttribute) return null;
    const out = {};
    for (const k of ATTR_KEYS) {
        const v = el.getAttribute(k);
        if (v !== null && v !== '') out[k] = v;
    }
    return Object.keys(out).length ? out : null;
}

/** Build a CSS-like selector path up to SELECTOR_DEPTH ancestors. */
function _selectorPath(el) {
    const parts = [];
    let n = el;
    for (let i = 0; i < SELECTOR_DEPTH && n && n.nodeType === 1; i++) {
        const tag = (n.tagName || '').toLowerCase();
        let token = tag;
        if (n.id)       token += `#${n.id}`;
        else if (n.classList && n.classList.length) {
            // Only first class to keep selector compact and stable.
            token += `.${n.classList[0]}`;
        }
        parts.unshift(token);
        n = n.parentElement;
    }
    return parts.join(' > ');
}

/** Current page URL — pathname + search (NO hash, which can leak query). */
function _pageUrl() {
    try {
        return window.location.pathname + (window.location.search || '');
    } catch (_) {
        return null;
    }
}

/** True if this element (or any ancestor) is sensitive (password-class). */
function _isSensitive(el) {
    if (!el) return false;
    if (el.type === 'password') return true;
    try {
        if (el.closest && el.closest('[data-sensitive="true"]')) return true;
    } catch (_) {}
    return false;
}

// ---------- Public API -----------------------------------------------------

export const uiEventTracker = {
    _outbox: null,
    _started: false,

    /**
     * Wire up all document-level listeners. Idempotent.
     * @param {object} outbox  the uiEventOutbox singleton (must expose
     *                         an enqueue(ev) method).
     */
    start(outbox) {
        if (this._started) return;
        if (!outbox || typeof outbox.enqueue !== 'function') {
            console.warn('[ui-event-tracker] start() requires an outbox with enqueue()');
            return;
        }
        this._outbox = outbox;
        this._started = true;

        const enq = (ev) => {
            try { outbox.enqueue(ev); } catch (_) {}
        };

        // --- click ---------------------------------------------------------
        // Capture phase so we observe even when downstream handlers call
        // stopPropagation(). passive:true forbids preventDefault — harmless
        // here, and signals to the browser this listener is non-blocking.
        document.addEventListener('click', (e) => {
            const t = e.target;
            if (!t) return;
            enq({
                kind: 'click',
                target_id:   t.id || null,
                target_tag:  t.tagName || null,
                target_text: _safeTargetText(t),
                target_attrs: _attrSnapshot(t),
                page_url:    _pageUrl(),
                extra: {
                    selector: _selectorPath(t),
                    modifiers: {
                        shift: !!e.shiftKey, ctrl: !!e.ctrlKey,
                        alt:   !!e.altKey,   meta: !!e.metaKey,
                    },
                    button: e.button,
                    x: e.clientX, y: e.clientY,
                },
            });
        }, { capture: true, passive: true });

        // --- keydown -------------------------------------------------------
        // Rate-capped per target. Password fields → key:'*'.
        document.addEventListener('keydown', (e) => {
            const t = e.target;
            if (!t) return;
            if (_shouldDropKeystroke(t, enq)) return;
            const sensitive = _isSensitive(t);
            enq({
                kind: 'keystroke',
                target_id:   t.id || null,
                target_tag:  t.tagName || null,
                target_text: _safeTargetText(t),
                target_attrs: _attrSnapshot(t),
                page_url:    _pageUrl(),
                extra: {
                    key: sensitive ? '*' : (e.key || null),
                    code: sensitive ? null : (e.code || null),
                    masked: sensitive,
                    modifiers: {
                        shift: !!e.shiftKey, ctrl: !!e.ctrlKey,
                        alt:   !!e.altKey,   meta: !!e.metaKey,
                    },
                    selector: _selectorPath(t),
                },
            });
        }, { capture: true, passive: true });

        // --- focusin / focusout -------------------------------------------
        document.addEventListener('focusin', (e) => {
            const t = e.target;
            if (!t) return;
            enq({
                kind: 'focus',
                target_id:   t.id || null,
                target_tag:  t.tagName || null,
                target_text: _safeTargetText(t),
                target_attrs: _attrSnapshot(t),
                page_url:    _pageUrl(),
                extra: { selector: _selectorPath(t) },
            });
        }, { capture: true, passive: true });

        document.addEventListener('focusout', (e) => {
            const t = e.target;
            if (!t) return;
            enq({
                kind: 'blur',
                target_id:   t.id || null,
                target_tag:  t.tagName || null,
                target_text: _safeTargetText(t),
                target_attrs: _attrSnapshot(t),
                page_url:    _pageUrl(),
                extra: { selector: _selectorPath(t) },
            });
        }, { capture: true, passive: true });

        // --- submit --------------------------------------------------------
        document.addEventListener('submit', (e) => {
            const t = e.target;
            if (!t) return;
            // Collect field NAMES (not values — value capture would defeat
            // password masking by storing the same string in form data).
            const fieldNames = [];
            try {
                if (t.elements && t.elements.length) {
                    for (const f of t.elements) {
                        if (f && f.name) fieldNames.push(f.name);
                    }
                }
            } catch (_) {}
            enq({
                kind: 'submit',
                target_id:   t.id || null,
                target_tag:  t.tagName || null,
                target_text: _safeTargetText(t),
                target_attrs: _attrSnapshot(t),
                page_url:    _pageUrl(),
                extra: {
                    selector: _selectorPath(t),
                    action: t.action || null,
                    method: (t.method || '').toUpperCase() || null,
                    field_names: fieldNames,
                },
            });
        }, { capture: true, passive: true });

        // --- SPA navigation events ----------------------------------------
        window.addEventListener('popstate', () => {
            enq({
                kind: 'navigation',
                page_url: _pageUrl(),
                extra: { type: 'popstate', new_url: _pageUrl() },
            });
        });
        window.addEventListener('hashchange', (e) => {
            enq({
                kind: 'navigation',
                page_url: _pageUrl(),
                extra: {
                    type: 'hashchange',
                    old_url: e.oldURL || null,
                    new_url: e.newURL || null,
                },
            });
        });

        // Initial landing event — gives the audit a clear "session start"
        // anchor that's easy to spot when grepping the log.
        enq({
            kind: 'navigation',
            page_url: _pageUrl(),
            extra: { type: 'pageload' },
        });
    },
};
