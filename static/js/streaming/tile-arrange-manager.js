/**
 * TILE ARRANGE MANAGER
 * iOS-inspired drag-to-rearrange for camera tiles.
 *
 * Design: long-press (500ms hold) on any tile triggers "arrange mode".
 * In arrange mode:
 *   - All tiles jiggle (CSS animation — oscillating slight rotation)
 *   - A drag handle badge appears on each tile
 *   - User can drag tiles to reorder them via SortableJS
 *   - A "Done" pill button appears at the bottom-center
 *   - Exiting arrange mode saves the new order to the DB
 *
 * Differs from iOS in:
 *   - Jiggle uses a custom easing (asymmetric rotation, not uniform wobble)
 *   - Done button is a bottom-center pill (iOS puts it top-right)
 *   - Drag handle is a 6-dot grid icon (iOS uses lift-and-move, no handle)
 *   - Long-press also works on desktop (mousedown hold)
 *   - No delete badge (tiles are not removable from here)
 */

export class TileArrangeManager {
    constructor(containerSelector = '#streams-container') {
        /** @type {jQuery} - The grid container */
        this.$container = $(containerSelector);

        /** @type {boolean} - Whether arrange mode is active */
        this.arrangeMode = false;

        /** @type {Sortable|null} - SortableJS instance */
        this.sortable = null;

        /** @type {number|null} - Long-press timer ID */
        this._longPressTimer = null;

        /** @type {number} - Long-press duration in ms */
        this.LONG_PRESS_DURATION = 500;

        /** @type {boolean} - Whether the user has moved during the press (cancels long-press) */
        this._pressMoved = false;

        /** @type {boolean} - Whether order has changed (dirty flag) */
        this._orderDirty = false;

        this._init();
    }

    // =========================================================================
    // INITIALIZATION
    // =========================================================================

    _init() {
        if (!this.$container.length) {
            console.warn('[TileArrange] Container not found, skipping init');
            return;
        }

        this._createDoneButton();
        this._attachLongPressListeners();
        console.log('[TileArrange] Initialized');
    }

    /**
     * Create the "Done" pill button that floats at the bottom of the screen
     * during arrange mode. Hidden until arrange mode activates.
     */
    _createDoneButton() {
        // Remove any stale instance
        $('#tile-arrange-done-btn').remove();

        const $btn = $(`
            <button id="tile-arrange-done-btn" class="tile-arrange-done-btn" style="display:none;">
                <i class="fas fa-check"></i>
                Done
            </button>
        `);

        $('body').append($btn);

        $btn.on('click', () => this.exitArrangeMode(true));
    }

    // =========================================================================
    // LONG-PRESS DETECTION
    // =========================================================================

    /**
     * Attach long-press listeners to the container (event delegation).
     * Handles both touch (mobile) and mouse (desktop).
     * Movement during the press cancels the long-press to avoid
     * interfering with normal scrolling or click interactions.
     */
    _attachLongPressListeners() {
        const container = this.$container[0];

        // --- Touch ---
        container.addEventListener('touchstart', (e) => {
            // Only trigger on a stream-item, not when already in arrange mode
            if (this.arrangeMode) return;
            const tile = e.target.closest('.stream-item');
            if (!tile) return;
            // Arrange mode is grid-only: block in fullscreen, expanded modal,
            // or when any PTZ panel is currently open (ptz-active class on toggle btn).
            // PTZ operations require held touch — without this guard a long pan
            // triggers arrange mode after 500 ms.
            if (document.querySelector('.stream-item.css-fullscreen, .stream-item.expanded')) return;
            if (document.querySelector('.stream-ptz-toggle-btn.ptz-active')) return;
            if (e.target.closest('button, a, input, select, .ptz-controls, .stream-controls, .stream-more-menu')) return;

            this._pressMoved = false;
            this._longPressTimer = setTimeout(() => {
                if (!this._pressMoved) {
                    this.enterArrangeMode();
                }
            }, this.LONG_PRESS_DURATION);
        }, { passive: true });

        container.addEventListener('touchmove', () => {
            this._pressMoved = true;
            this._cancelLongPress();
        }, { passive: true });

        container.addEventListener('touchend', () => {
            this._cancelLongPress();
        }, { passive: true });

        container.addEventListener('touchcancel', () => {
            this._cancelLongPress();
        }, { passive: true });

        // --- Mouse (desktop) ---
        container.addEventListener('mousedown', (e) => {
            if (this.arrangeMode) return;
            if (e.button !== 0) return;  // Left button only
            const tile = e.target.closest('.stream-item');
            if (!tile) return;
            // Arrange mode is grid-only: block in fullscreen, expanded modal,
            // or when any PTZ panel is open (held mouse on a direction button
            // reaches 500 ms and would otherwise trigger arrange mode).
            if (document.querySelector('.stream-item.css-fullscreen, .stream-item.expanded')) return;
            if (document.querySelector('.stream-ptz-toggle-btn.ptz-active')) return;
            if (e.target.closest('button, a, input, select, .ptz-controls, .stream-controls, .stream-more-menu')) return;

            this._pressMoved = false;
            this._longPressTimer = setTimeout(() => {
                if (!this._pressMoved) {
                    this.enterArrangeMode();
                }
            }, this.LONG_PRESS_DURATION);
        });

        container.addEventListener('mousemove', () => {
            if (this._longPressTimer) {
                this._pressMoved = true;
                this._cancelLongPress();
            }
        });

        container.addEventListener('mouseup', () => {
            this._cancelLongPress();
        });
    }

    _cancelLongPress() {
        if (this._longPressTimer) {
            clearTimeout(this._longPressTimer);
            this._longPressTimer = null;
        }
    }

    // =========================================================================
    // ARRANGE MODE ENTRY / EXIT
    // =========================================================================

    /**
     * Enter arrange mode: start jiggle animation, show drag handles,
     * activate SortableJS, show Done button.
     */
    enterArrangeMode() {
        if (this.arrangeMode) return;
        this.arrangeMode = true;
        this._orderDirty = false;

        console.log('[TileArrange] Entering arrange mode');

        // Add jiggle class to container — CSS animates all .stream-item children
        this.$container.addClass('arrange-mode');

        // Show Done button with slide-up animation
        $('#tile-arrange-done-btn').fadeIn(200);

        // Activate SortableJS drag-and-drop
        this._activateSortable();

        // Prevent the long-press from firing right away on the tile being held
        // by yielding before setting up drag, so the first touch event resolves
        setTimeout(() => {
            // Prevent tap-to-expand from firing for this press cycle
            // by adding a one-shot class that stream.js checks
            this.$container.addClass('arrange-mode-just-entered');
            setTimeout(() => this.$container.removeClass('arrange-mode-just-entered'), 300);
        }, 0);
    }

    /**
     * Exit arrange mode: stop jiggle, hide Done button, destroy Sortable,
     * optionally save order to DB.
     * @param {boolean} save - Whether to persist the new order
     */
    async exitArrangeMode(save = true) {
        if (!this.arrangeMode) return;
        this.arrangeMode = false;

        console.log('[TileArrange] Exiting arrange mode, save =', save);

        this.$container.removeClass('arrange-mode');
        $('#tile-arrange-done-btn').fadeOut(200);

        this._destroySortable();

        if (save && this._orderDirty) {
            await this._saveOrder();
        }
    }

    // =========================================================================
    // SORTABLEJS
    // =========================================================================

    _activateSortable() {
        if (this.sortable) {
            this.sortable.destroy();
            this.sortable = null;
        }

        const container = this.$container[0];
        this.sortable = Sortable.create(container, {
            // Animate tiles sliding into new positions
            animation: 180,
            easing: 'cubic-bezier(0.25, 0.46, 0.45, 0.94)',

            // Ghost element (placeholder shown at drop target) styling
            ghostClass: 'tile-sort-ghost',

            // Class added to the tile currently being dragged
            chosenClass: 'tile-sort-chosen',

            // Class added to tile while it is being dragged (in the air)
            dragClass: 'tile-sort-drag',

            // Only drag from within a tile — not on overlaid buttons or videos
            // (the drag handle badge is a valid handle but we allow the whole tile
            // so the interaction feels natural; controls are hidden in arrange mode)
            filter: '.stream-fullscreen-btn, .stream-audio-btn, .stream-controls, .ptz-controls',
            preventOnFilter: false,

            // Delay before drag starts (small value helps distinguish from scroll)
            delay: 50,
            delayOnTouchOnly: true,

            onStart: () => {
                // Stop jiggle on the tile being dragged for cleaner visual
                this.$container.addClass('sorting-active');
            },

            onEnd: (evt) => {
                this.$container.removeClass('sorting-active');
                if (evt.oldIndex !== evt.newIndex) {
                    this._orderDirty = true;
                    console.log(`[TileArrange] Moved tile from ${evt.oldIndex} to ${evt.newIndex}`);
                }
            }
        });
    }

    _destroySortable() {
        if (this.sortable) {
            this.sortable.destroy();
            this.sortable = null;
        }
    }

    // =========================================================================
    // ORDER PERSISTENCE
    // =========================================================================

    /**
     * Collect current tile order from the DOM and save to the backend.
     * The backend stores display_order per user in user_camera_preferences.
     */
    async _saveOrder() {
        const order = [];
        this.$container.find('.stream-item').each((_, el) => {
            const serial = $(el).data('camera-serial');
            if (serial) order.push(serial);
        });

        console.log('[TileArrange] Saving order:', order);

        try {
            const resp = await fetch('/api/my-camera-order', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ order })
            });
            if (resp.ok) {
                console.log('[TileArrange] Order saved successfully');
            } else {
                console.error('[TileArrange] Failed to save order:', resp.status);
            }
        } catch (e) {
            console.error('[TileArrange] Error saving order:', e);
        }
    }

    // =========================================================================
    // PUBLIC API
    // =========================================================================

    /**
     * Toggle arrange mode. Can be called from external UI (e.g. navbar button).
     */
    toggle() {
        if (this.arrangeMode) {
            this.exitArrangeMode(true);
        } else {
            this.enterArrangeMode();
        }
    }

    /**
     * Silently exit arrange mode without saving.
     * Called by stream.js whenever the view transitions out of the grid
     * (entering fullscreen or expanded modal) so arrange mode cannot
     * persist in a state where it has no meaning.
     */
    deactivate() {
        if (this.arrangeMode) {
            console.log('[TileArrange] Deactivated by view transition (fullscreen/expanded)');
            this.exitArrangeMode(false);
        }
    }
}

// Singleton export
export const tileArrangeManager = new TileArrangeManager();
