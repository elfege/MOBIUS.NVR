/**
 * PINNED WINDOW MANAGER
 * Manages floating, draggable, resizable camera windows.
 *
 * Activation trigger: camera is BOTH pinned (pin-active) AND in HD mode.
 * In floating window mode:
 *   - The .stream-item is detached from the grid and appended to <body>
 *   - It is positioned with position:fixed, draggable via title bar
 *   - Resizable by dragging the bottom-right corner handle
 *   - Background streams blur + pause while any window is at its home position
 *   - Once dragged away from home, blur lifts and another camera can be pinned
 *   - Multiple floating windows supported simultaneously
 *   - Window positions/sizes persisted to localStorage + DB
 *
 * Events fired on document:
 *   'pinned-window:close'  — { serial } — when user clicks the × button
 *                            stream.js listens and handles unpin + SD switch
 */

export class PinnedWindowManager {
    constructor(containerSelector = '#streams-container') {
        /** @type {jQuery} - The grid container */
        this.$container = $(containerSelector);

        /**
         * Active floating windows.
         * @type {Map<string, WindowState>}
         * WindowState: { serial, $el, originalIndex, $originalParent,
         *                homeX, homeY, x, y, w, h, isAtHome }
         */
        this.windows = new Map();

        /**
         * Distance from home position (px) beyond which a window
         * is considered "moved away", lifting background blur.
         * @type {number}
         */
        this.HOME_THRESHOLD = 130;

        /**
         * Default floating window size. Clamped to 60% of viewport.
         * @type {number}
         */
        this.DEFAULT_W = Math.min(720, Math.floor(window.innerWidth * 0.60));
        this.DEFAULT_H = Math.min(450, Math.floor(window.innerHeight * 0.60));

        /** @type {object|null} - Active drag state */
        this._drag = null;

        /** @type {object|null} - Active resize state */
        this._resize = null;

        // Bind handlers so they can be removed from document
        this._onMouseMove = this._onMouseMove.bind(this);
        this._onMouseUp   = this._onMouseUp.bind(this);

        // Load saved positions from localStorage (DB value synced in during prefs fetch)
        this._savedPositions = this._loadPositions();

        console.log('[PinnedWindow] Initialized');
    }

    // =========================================================================
    // ACTIVATE / DEACTIVATE
    // =========================================================================

    /**
     * Convert a .stream-item to a floating window.
     * Detaches from the grid, appends to <body>, positions at home.
     *
     * @param {string}  serial       - Camera serial number
     * @param {jQuery}  $streamItem  - The .stream-item jQuery element
     */
    activate(serial, $streamItem) {
        if (this.windows.has(serial)) {
            console.log(`[PinnedWindow] ${serial}: already floating`);
            return;
        }

        console.log(`[PinnedWindow] Activating floating window: ${serial}`);

        // Record grid position before detach (so we can restore on deactivate)
        const originalIndex = this.$container.children('.stream-item').index($streamItem[0]);

        // Determine window size: saved or default
        const saved = this._savedPositions[serial];
        const w = saved?.w || this.DEFAULT_W;
        const h = saved?.h || this.DEFAULT_H;

        // Home = center of viewport
        const homeX = Math.round((window.innerWidth  - w) / 2);
        const homeY = Math.round((window.innerHeight - h) / 2);

        // Restore saved position or place at home
        const x = (saved?.x != null) ? saved.x : homeX;
        const y = (saved?.y != null) ? saved.y : homeY;

        const windowState = {
            serial,
            $el: $streamItem,
            originalIndex,
            $originalParent: this.$container,
            homeX, homeY,
            x, y, w, h,
            isAtHome: true,
        };

        this.windows.set(serial, windowState);

        // Collapse any expanded/backdrop state — floating window replaces it
        $streamItem.removeClass('expanded');
        $('#expanded-backdrop').removeClass('visible');
        $('body').css('overflow', '');

        // Resolve the camera display name for the title bar
        const cameraName = $streamItem.find('.camera-name, .stream-label').first().text().trim()
                        || $streamItem.data('camera-serial')
                        || serial;

        // Inject title bar (drag handle + label + close button)
        const $titleBar = $(`
            <div class="pinned-window-titlebar" data-pw-serial="${serial}">
                <span class="pinned-window-title">${$('<span>').text(cameraName).html()}</span>
                <button class="pinned-window-close-btn" title="Close floating window">
                    <i class="fas fa-times"></i>
                </button>
            </div>
        `);
        $streamItem.prepend($titleBar);

        // Inject SE resize handle
        const $resizeHandle = $('<div class="pinned-window-resize-handle" title="Resize"></div>');
        $streamItem.append($resizeHandle);

        // Detach from grid, append to body (stream continues uninterrupted)
        $streamItem.detach().appendTo('body');

        // Apply floating class + position
        $streamItem
            .addClass('pinned-window pw-at-home')
            .css({ left: x, top: y, width: w, height: h });

        // --- Event: drag (title bar mousedown) ---
        $titleBar.on('mousedown.pw', (e) => {
            // Don't start drag from the close button
            if ($(e.target).closest('.pinned-window-close-btn').length) return;
            e.preventDefault();
            this._startDrag(serial, e);
        });

        // --- Event: close button ---
        $titleBar.find('.pinned-window-close-btn').on('click.pw', () => {
            this.deactivate(serial, true);
        });

        // --- Event: resize (corner mousedown) ---
        $resizeHandle.on('mousedown.pw', (e) => {
            e.preventDefault();
            this._startResize(serial, e);
        });

        // Update background blur/pause state
        this.updateBackgroundState();

        console.log(`[PinnedWindow] ${serial}: floating at (${x},${y}) ${w}×${h}`);
    }

    /**
     * Return a floating window back to its original grid position.
     *
     * @param {string}  serial      - Camera serial
     * @param {boolean} fireClose   - If true, fire 'pinned-window:close' on document
     *                                so stream.js can unpin + switch to SD.
     */
    deactivate(serial, fireClose = false) {
        const win = this.windows.get(serial);
        if (!win) return;

        console.log(`[PinnedWindow] Deactivating: ${serial}`);

        const { $el, originalIndex, $originalParent } = win;

        // Remove injected elements
        $el.find('.pinned-window-titlebar').remove();
        $el.find('.pinned-window-resize-handle').remove();

        // Strip floating classes + inline position/size
        $el.removeClass('pinned-window pw-at-home pw-dragging');
        $el.css({ left: '', top: '', width: '', height: '' });

        // Re-insert at original grid slot
        const $children = $originalParent.children('.stream-item');
        if (originalIndex >= $children.length) {
            $originalParent.append($el.detach());
        } else {
            $children.eq(originalIndex).before($el.detach());
        }

        this.windows.delete(serial);
        this.updateBackgroundState();

        if (fireClose) {
            // Notify stream.js to handle unpin + SD transition
            $(document).trigger('pinned-window:close', { serial });
        }
    }

    /**
     * Deactivate all active floating windows.
     * @param {boolean} fireClose - Whether to fire close events
     */
    deactivateAll(fireClose = false) {
        for (const serial of [...this.windows.keys()]) {
            this.deactivate(serial, fireClose);
        }
    }

    // =========================================================================
    // BACKGROUND STATE — blur + pause / restore
    // =========================================================================

    /**
     * Recalculate background blur/pause state.
     * Blur is active while any window sits at its home position.
     * Lifts when all windows move away or no windows are active.
     */
    updateBackgroundState() {
        const anyAtHome = [...this.windows.values()].some(w => w.isAtHome);

        if (anyAtHome) {
            this.$container.addClass('has-pinned-at-home');
            this._pauseBackgroundStreams();
        } else {
            this.$container.removeClass('has-pinned-at-home');
            this._resumeBackgroundStreams();
        }
    }

    /**
     * Pause video elements in non-floating tiles.
     * MJPEG (img) are handled purely by CSS blur (not paused at network level).
     */
    _pauseBackgroundStreams() {
        this.$container.find('.stream-item').each((_, el) => {
            const serial = $(el).data('camera-serial');
            if (this.windows.has(serial)) return; // skip floating windows

            const video = el.querySelector('video');
            if (video && !video.paused) {
                video.pause();
                $(el).data('pw-paused', true);
            }
        });
    }

    /** Resume videos that were paused by this manager. */
    _resumeBackgroundStreams() {
        this.$container.find('.stream-item').each((_, el) => {
            if (!$(el).data('pw-paused')) return;
            const video = el.querySelector('video');
            if (video && video.paused) {
                video.play().catch(() => {
                    // Play may fail if stream not yet loaded — non-critical
                });
            }
            $(el).removeData('pw-paused');
        });
    }

    // =========================================================================
    // DRAG
    // =========================================================================

    _startDrag(serial, e) {
        const win = this.windows.get(serial);
        if (!win) return;

        this._drag = {
            serial,
            startMouseX: e.clientX,
            startMouseY: e.clientY,
            startLeft:   win.x,
            startTop:    win.y,
        };

        win.$el.addClass('pw-dragging');

        // Global listeners for the duration of the drag
        document.addEventListener('mousemove', this._onMouseMove);
        document.addEventListener('mouseup',   this._onMouseUp);
    }

    _onMouseMove(e) {
        // --- Drag ---
        if (this._drag) {
            const { serial, startMouseX, startMouseY, startLeft, startTop } = this._drag;
            const win = this.windows.get(serial);
            if (!win) return;

            const dx = e.clientX - startMouseX;
            const dy = e.clientY - startMouseY;

            // Clamp to viewport bounds (keep title bar always reachable)
            win.x = Math.max(0, Math.min(startLeft + dx, window.innerWidth  - win.w));
            win.y = Math.max(0, Math.min(startTop  + dy, window.innerHeight - 38));

            win.$el.css({ left: win.x, top: win.y });

            // Update home proximity
            const dist = Math.hypot(win.x - win.homeX, win.y - win.homeY);
            const wasAtHome = win.isAtHome;
            win.isAtHome = dist < this.HOME_THRESHOLD;

            if (wasAtHome !== win.isAtHome) {
                win.$el.toggleClass('pw-at-home', win.isAtHome);
                this.updateBackgroundState();
            }
        }

        // --- Resize ---
        if (this._resize) {
            const { serial, startMouseX, startMouseY, startW, startH } = this._resize;
            const win = this.windows.get(serial);
            if (!win) return;

            win.w = Math.max(320, startW + (e.clientX - startMouseX));
            win.h = Math.max(200, startH + (e.clientY - startMouseY));
            win.$el.css({ width: win.w, height: win.h });
        }
    }

    _onMouseUp() {
        let changed = false;

        if (this._drag) {
            const win = this.windows.get(this._drag.serial);
            if (win) {
                win.$el.removeClass('pw-dragging');
                changed = true;
            }
            this._drag = null;
        }

        if (this._resize) {
            changed = true;
            this._resize = null;
        }

        if (changed) {
            this.savePositions();
        }

        document.removeEventListener('mousemove', this._onMouseMove);
        document.removeEventListener('mouseup',   this._onMouseUp);
    }

    // =========================================================================
    // RESIZE
    // =========================================================================

    _startResize(serial, e) {
        const win = this.windows.get(serial);
        if (!win) return;

        this._resize = {
            serial,
            startMouseX: e.clientX,
            startMouseY: e.clientY,
            startW: win.w,
            startH: win.h,
        };

        // Reuse global mousemove/mouseup already attached for drag
        document.addEventListener('mousemove', this._onMouseMove);
        document.addEventListener('mouseup',   this._onMouseUp);
    }

    // =========================================================================
    // POSITION PERSISTENCE
    // =========================================================================

    /** Load positions from localStorage. DB values are synced here during prefs fetch. */
    _loadPositions() {
        try {
            return JSON.parse(localStorage.getItem('pinnedWindowPositions') || '{}');
        } catch {
            return {};
        }
    }

    /**
     * Persist all active window positions to localStorage + DB.
     * Called after drag end or resize end.
     */
    savePositions() {
        for (const [serial, win] of this.windows) {
            this._savedPositions[serial] = { x: win.x, y: win.y, w: win.w, h: win.h };
        }

        localStorage.setItem('pinnedWindowPositions', JSON.stringify(this._savedPositions));

        // Best-effort DB persist
        fetch('/api/my-preferences', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pinned_windows: this._savedPositions })
        }).catch(err => console.warn('[PinnedWindow] Failed to save positions to DB:', err));
    }

    /**
     * Merge positions from DB into localStorage (called during prefs init).
     * DB is authoritative when localStorage is missing an entry.
     *
     * @param {object} dbPositions - pinned_windows value from /api/my-preferences
     */
    mergeFromDB(dbPositions) {
        if (!dbPositions || typeof dbPositions !== 'object') return;
        for (const [serial, pos] of Object.entries(dbPositions)) {
            if (!this._savedPositions[serial]) {
                this._savedPositions[serial] = pos;
            }
        }
        localStorage.setItem('pinnedWindowPositions', JSON.stringify(this._savedPositions));
    }

    // =========================================================================
    // PUBLIC QUERY API
    // =========================================================================

    /** @returns {boolean} Whether serial is currently a floating window */
    isActive(serial) {
        return this.windows.has(serial);
    }

    /** @returns {boolean} Whether any window is currently floating */
    hasAnyActive() {
        return this.windows.size > 0;
    }
}

// Singleton — imported and shared by stream.js and the inline module script
export const pinnedWindowManager = new PinnedWindowManager();
