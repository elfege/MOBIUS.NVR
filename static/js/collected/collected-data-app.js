/**
 * collected-data-app.js — orchestrator for the /evidence (Collected Data) page.
 *
 * Responsibilities:
 *   - Fetch /api/evidence/status   → render KPI row.
 *   - Fetch /api/evidence/feed     → render captures table (paginated).
 *   - Fetch /api/evidence/cases    → render cases panel (CRUD).
 *   - Fetch /api/evidence/cameras  → populate the camera filter dropdown.
 *   - Open event detail drawer when a feed row is clicked.
 *   - Verify chain on demand via /api/evidence/manifest/verify.
 *
 * The endpoints already exist (routes/evidence_routes.py); this module is
 * pure consumer + render. No backend changes required for this page.
 */

import { humanRelativeTime, humanBytes, humanDuration, escapeHtml } from './utils.js';

// ─────────────────────────────────────────────────────────────────────────
// API client — thin wrapper, normalizes errors and response shape.
// ─────────────────────────────────────────────────────────────────────────

const api = {
    async _json(url, init = {}) {
        const resp = await fetch(url, {
            credentials: 'same-origin',
            headers: { 'Accept': 'application/json' },
            ...init,
        });
        if (!resp.ok) {
            let msg;
            try { msg = (await resp.json()).error || resp.statusText; }
            catch { msg = resp.statusText; }
            throw new Error(`${url} → ${resp.status}: ${msg}`);
        }
        return resp.json();
    },
    status:   ()       => api._json('/api/evidence/status'),
    feed:     (cursor) => api._json('/api/evidence/feed' + (cursor ? `?cursor=${encodeURIComponent(cursor)}` : '')),
    event:    (id)     => api._json(`/api/evidence/event/${id}`),
    cameras:  ()       => api._json('/api/evidence/cameras'),
    cases:    ()       => api._json('/api/evidence/cases'),
    createCase: (body) => api._json('/api/evidence/cases', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify(body),
    }),
    verifyChain: () => api._json('/api/evidence/manifest/verify'),
};

// ─────────────────────────────────────────────────────────────────────────
// State
// ─────────────────────────────────────────────────────────────────────────

const state = {
    feedItems:  [],
    feedCursor: null,
    feedFinished: false,
    cameras:    [],
    cases:      [],
    filters:    { camera: '', category: '', case: '' },
    loading:    false,
};

// ─────────────────────────────────────────────────────────────────────────
// Toast helper
// ─────────────────────────────────────────────────────────────────────────

function toast(message, kind = '') {
    const $el = $('<div class="cd-toast"></div>').text(message);
    if (kind) $el.addClass(kind);
    $('body').append($el);
    requestAnimationFrame(() => $el.addClass('visible'));
    setTimeout(() => {
        $el.removeClass('visible');
        setTimeout(() => $el.remove(), 250);
    }, 2800);
}

// ─────────────────────────────────────────────────────────────────────────
// KPI row
// ─────────────────────────────────────────────────────────────────────────

async function renderKPIs() {
    let s;
    try {
        s = await api.status();
    } catch (e) {
        console.error('[CollectedData] status fetch failed', e);
        $('#kpi-total-events').text('—');
        $('#kpi-last-event').text('—');
        $('#kpi-storage').text('—');
        $('#kpi-chain').text('—');
        $('.cd-kpi[data-kpi="chain"]').attr('data-state', 'unknown');
        return;
    }

    $('#kpi-total-events').text(Number(s.manifest_total_entries || 0).toLocaleString());

    $('#kpi-last-event').text(s.last_event_utc ? humanRelativeTime(s.last_event_utc) : '—');
    $('#kpi-last-event-sub').text(s.last_event_utc ? s.last_event_utc : 'no captures yet');

    if (s.volume_free_bytes != null && s.volume_total_bytes != null) {
        $('#kpi-storage').text(humanBytes(s.volume_free_bytes));
        $('#kpi-storage-sub').text(`free of ${humanBytes(s.volume_total_bytes)}`);
    } else {
        $('#kpi-storage').text('—');
        $('#kpi-storage-sub').text('volume not mounted');
    }

    const chainKpi = $('.cd-kpi[data-kpi="chain"]');
    if (s.chain_ok === true) {
        $('#kpi-chain').text('OK');
        chainKpi.attr('data-state', 'ok');
    } else if (s.chain_ok === false) {
        $('#kpi-chain').text('BROKEN');
        chainKpi.attr('data-state', 'bad');
    } else {
        $('#kpi-chain').text('—');
        chainKpi.attr('data-state', 'unknown');
    }
}

async function renderCameraKPI() {
    try {
        const list = await api.cameras();
        const audioCapable = list.length;
        $('#kpi-cameras').text(audioCapable);
        $('#kpi-cameras-sub').text('cameras flagged audio_input_supported');

        // populate filter dropdown
        const $sel = $('#cd-filter-camera').empty().append('<option value="">All</option>');
        list.forEach(c => {
            $sel.append($('<option></option>').attr('value', c.serial).text(c.name || c.serial));
        });
        state.cameras = list;
    } catch (e) {
        console.error('[CollectedData] cameras fetch failed', e);
        $('#kpi-cameras').text('—');
    }
}

async function renderCasesKPI() {
    try {
        const list = await api.cases();
        const active = list.filter(c => !c.archived).length;
        $('#kpi-cases').text(active);
        $('#kpi-cases-sub').text(`${list.length} total (incl. archived)`);
        state.cases = list;
        renderCasesList();
        // populate filter dropdown
        const $sel = $('#cd-filter-case').empty().append('<option value="">Unbound</option>');
        list.forEach(c => {
            $sel.append($('<option></option>').attr('value', c.id)
                                                .text(`${c.name}${c.archived ? ' (archived)' : ''}`));
        });
    } catch (e) {
        console.error('[CollectedData] cases fetch failed', e);
        $('#kpi-cases').text('—');
    }
}

// ─────────────────────────────────────────────────────────────────────────
// Cases panel
// ─────────────────────────────────────────────────────────────────────────

function renderCasesList() {
    const $list = $('#cd-cases-list').empty();
    if (state.cases.length === 0) {
        $list.append(`
            <div class="cd-empty">
                <i class="fas fa-folder-open"></i>
                <p>No cases yet. Create one with the + button.</p>
            </div>
        `);
        return;
    }
    state.cases.forEach(c => {
        const $item = $(`
            <div class="cd-case-item${c.archived ? ' archived' : ''}" data-case-id="${c.id}">
                <div class="cd-case-name"></div>
                <div class="cd-case-meta">
                    <span><i class="fas fa-calendar"></i> </span>
                    <span class="cd-case-events"></span>
                </div>
            </div>
        `);
        $item.find('.cd-case-name').text(c.name || `Case #${c.id}`);
        $item.find('.cd-case-meta span:first-child').append(
            new Date(c.created_at).toLocaleDateString()
        );
        $item.find('.cd-case-events').html(
            `<i class="fas fa-stream"></i> ${c.event_count ?? '?'} events`
        );
        if (c.jurisdiction) {
            $item.append($('<div class="cd-case-jurisdiction"></div>').text(c.jurisdiction));
        }
        $list.append($item);
    });
}

// ─────────────────────────────────────────────────────────────────────────
// Feed
// ─────────────────────────────────────────────────────────────────────────

async function loadFeedPage(reset = false) {
    if (state.loading) return;
    if (state.feedFinished && !reset) return;

    if (reset) {
        state.feedItems = [];
        state.feedCursor = null;
        state.feedFinished = false;
        $('#cd-feed-tbody').html(`
            <tr class="cd-empty-row"><td colspan="7">
                <div class="cd-empty"><i class="fas fa-spinner fa-spin"></i><p>Loading…</p></div>
            </td></tr>
        `);
    }

    state.loading = true;
    $('#cd-load-more').prop('disabled', true);
    let page;
    try {
        page = await api.feed(state.feedCursor);
    } catch (e) {
        console.error('[CollectedData] feed fetch failed', e);
        $('#cd-feed-status').text(`error: ${e.message}`);
        state.loading = false;
        return;
    }

    const items = page.items || [];
    state.feedItems.push(...items);
    state.feedCursor = page.next_cursor || null;
    state.feedFinished = !state.feedCursor || items.length === 0;
    state.loading = false;

    renderFeed();

    $('#cd-load-more').prop('disabled', state.feedFinished);
    $('#cd-feed-status').text(
        state.feedFinished
            ? `${state.feedItems.length} captures (end of feed)`
            : `${state.feedItems.length} captures shown`
    );
}

function renderFeed() {
    const f = state.filters;
    const filtered = state.feedItems.filter(item => {
        if (f.camera && item.camera_serial !== f.camera) return false;
        if (f.category && item.classifier !== f.category) return false;
        if (f.case && String(item.case_id || '') !== String(f.case)) return false;
        return true;
    });

    const $tbody = $('#cd-feed-tbody').empty();
    if (filtered.length === 0) {
        $tbody.append(`
            <tr class="cd-empty-row"><td colspan="7">
                <div class="cd-empty">
                    <i class="fas fa-inbox"></i>
                    <p>${state.feedItems.length === 0
                        ? 'No captures yet. The pipeline produces an entry every time the YAMNet classifier flags an audio window.'
                        : 'No captures match the current filters.'}</p>
                </div>
            </td></tr>
        `);
        return;
    }

    filtered.forEach(item => {
        const cam = state.cameras.find(c => c.serial === item.camera_serial);
        const camName = cam ? (cam.name || cam.serial) : (item.camera_serial || '?');
        const caseName = item.case_id
            ? (state.cases.find(c => c.id === item.case_id)?.name || `#${item.case_id}`)
            : '—';
        const topScore = item.scores
            ? Math.max(...Object.values(item.scores).map(Number).filter(n => !isNaN(n)))
            : null;
        const $row = $(`
            <tr data-manifest-id="${item.manifest_id}">
                <td></td><td></td><td></td><td></td><td></td><td></td>
                <td><i class="fas fa-chevron-right" style="color:#8b949e;"></i></td>
            </tr>
        `);
        const cells = $row.find('td');
        $(cells[0]).text(humanRelativeTime(item.captured_at)).attr('title', item.captured_at || '');
        $(cells[1]).text(camName);
        $(cells[2]).html(item.classifier
            ? `<span class="cd-cat-badge" data-cat="${escapeHtml(item.classifier)}">${escapeHtml(item.classifier)}</span>`
            : '<span class="cd-cat-badge">?</span>');
        $(cells[3]).text(item.duration_seconds ? humanDuration(item.duration_seconds) : '—');
        $(cells[4]).text(topScore != null ? topScore.toFixed(2) : '—');
        $(cells[5]).text(caseName);
        $tbody.append($row);
    });
}

// ─────────────────────────────────────────────────────────────────────────
// Event drawer
// ─────────────────────────────────────────────────────────────────────────

async function openEventDrawer(manifestId) {
    $('#cd-event-drawer').attr('aria-hidden', 'false');
    $('#cd-event-content').html(
        '<p class="cd-empty"><i class="fas fa-spinner fa-spin"></i> Loading…</p>'
    );

    let ev;
    try {
        ev = await api.event(manifestId);
    } catch (e) {
        $('#cd-event-content').html(
            `<p class="cd-empty"><i class="fas fa-times-circle"></i> ${escapeHtml(e.message)}</p>`
        );
        return;
    }

    const rows = [
        ['Manifest ID', ev.manifest_id],
        ['Camera', state.cameras.find(c => c.serial === ev.camera_serial)?.name || ev.camera_serial],
        ['Captured', ev.captured_at],
        ['Duration', ev.duration_seconds ? humanDuration(ev.duration_seconds) : '—'],
        ['Classifier', ev.classifier || '—'],
        ['Scores', ev.scores ? JSON.stringify(ev.scores, null, 2) : '—'],
        ['Audio path', ev.audio_path || '—'],
        ['Case', ev.case_id ? `#${ev.case_id}` : 'unbound'],
    ];

    const $dl = $('<dl></dl>');
    rows.forEach(([k, v]) => {
        $dl.append($('<dt></dt>').text(k));
        $dl.append($('<dd></dd>').text(typeof v === 'object' ? JSON.stringify(v, null, 2) : String(v)));
    });
    $('#cd-event-content').empty().append($dl);
    $('#cd-event-title').text(`Event #${ev.manifest_id}`);
}

// ─────────────────────────────────────────────────────────────────────────
// New case modal
// ─────────────────────────────────────────────────────────────────────────

function openNewCaseModal() {
    $('#cd-new-case-modal').attr('aria-hidden', 'false');
    $('#cd-new-case-form')[0].reset();
    $('#cd-new-case-error').hide();
    $('#cd-new-case-form input[name="name"]').focus();
}

function closeAllOverlays() {
    $('#cd-new-case-modal').attr('aria-hidden', 'true');
    $('#cd-event-drawer').attr('aria-hidden', 'true');
}

async function submitNewCase(e) {
    e.preventDefault();
    const $err = $('#cd-new-case-error').hide();
    const fd = new FormData(e.target);
    const body = {
        name:         (fd.get('name') || '').toString().trim(),
        description:  (fd.get('description') || '').toString().trim() || undefined,
        jurisdiction: (fd.get('jurisdiction') || '').toString().trim() || undefined,
    };
    if (!body.name) {
        $err.text('Name is required.').show();
        return;
    }
    try {
        await api.createCase(body);
        closeAllOverlays();
        toast('Case created', 'success');
        await renderCasesKPI();
    } catch (err) {
        $err.text(err.message).show();
    }
}

// ─────────────────────────────────────────────────────────────────────────
// Verify chain
// ─────────────────────────────────────────────────────────────────────────

async function verifyChain() {
    const $btn = $('#cd-verify-btn').prop('disabled', true);
    const $icon = $btn.find('i').removeClass().addClass('fas fa-spinner fa-spin');
    try {
        const r = await api.verifyChain();
        if (r.ok) {
            toast('Chain verified — all entries intact', 'success');
            $('.cd-kpi[data-kpi="chain"]').attr('data-state', 'ok');
            $('#kpi-chain').text('OK');
        } else {
            toast(`Chain BROKEN: ${r.error || 'unknown'}`, 'error');
            $('.cd-kpi[data-kpi="chain"]').attr('data-state', 'bad');
            $('#kpi-chain').text('BROKEN');
        }
    } catch (e) {
        toast(`Verify failed: ${e.message}`, 'error');
    } finally {
        $btn.prop('disabled', false);
        $icon.removeClass().addClass('fas fa-link');
    }
}

// ─────────────────────────────────────────────────────────────────────────
// Boot
// ─────────────────────────────────────────────────────────────────────────

$(async function() {
    // Wire navbar buttons
    $('#cd-refresh-btn').on('click', async () => {
        await Promise.all([renderKPIs(), renderCameraKPI(), renderCasesKPI()]);
        await loadFeedPage(true);
        toast('Refreshed');
    });
    $('#cd-verify-btn').on('click', verifyChain);
    $('#cd-load-more').on('click', () => loadFeedPage(false));
    $('#cd-new-case-btn').on('click', openNewCaseModal);
    $('#cd-new-case-form').on('submit', submitNewCase);
    $('[data-close-modal], [data-close-drawer]').on('click', closeAllOverlays);
    $(document).on('keydown', e => { if (e.key === 'Escape') closeAllOverlays(); });

    // Filters
    $('#cd-filter-camera').on('change', e => { state.filters.camera = e.target.value; renderFeed(); });
    $('#cd-filter-category').on('change', e => { state.filters.category = e.target.value; renderFeed(); });
    $('#cd-filter-case').on('change', e => { state.filters.case = e.target.value; renderFeed(); });

    // Feed row click → open event drawer
    $('#cd-feed-tbody').on('click', 'tr[data-manifest-id]', function () {
        const id = $(this).data('manifest-id');
        if (id != null) openEventDrawer(id);
    });

    // Initial load — KPIs and dropdowns first, then feed.
    await Promise.all([renderKPIs(), renderCameraKPI(), renderCasesKPI()]);
    await loadFeedPage(true);
});
