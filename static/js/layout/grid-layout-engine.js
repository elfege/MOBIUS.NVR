/**
 * GridLayoutEngine — manages 4 user-selectable grid layout modes for the NVR streams view.
 *
 * Modes:
 *   - uniform:          Standard CSS grid, empty cells accepted (default)
 *   - last-row-stretch: CSS grid + last-row items span extra columns to fill width
 *   - auto-fit:         JS picks column count 1-6 that minimizes waste cells
 *   - masonry:          Absolute positioning, pixel-perfect fill, no wasted space
 *
 * The engine owns all grid layout decisions. camera-selector-controller.js delegates
 * to this engine via apply(visibleCount).
 *
 * CSS grid classes (grid-1 through grid-6) are defined in grid-modes.css.
 * Masonry mode overrides CSS grid entirely with position: absolute on each tile.
 */

export class GridLayoutEngine {

    /**
     * @param {jQuery} $container - The #streams-container element
     */
    constructor($container) {
        this.$container = $container;
        this._mode = 'uniform';
        this._lastCount = 0;
        this._resizeHandler = null;
    }

    /**
     * Set the active layout mode.
     * Does NOT re-apply — call apply() after to update the DOM.
     * @param {string} mode - 'uniform' | 'last-row-stretch' | 'auto-fit' | 'masonry'
     */
    setMode(mode) {
        const validModes = ['uniform', 'last-row-stretch', 'auto-fit', 'masonry'];
        if (!validModes.includes(mode)) {
            console.warn(`[GridLayoutEngine] Invalid mode: ${mode}, defaulting to uniform`);
            mode = 'uniform';
        }
        const previousMode = this._mode;
        this._mode = mode;

        // Cleanup masonry artifacts when leaving masonry mode
        if (previousMode === 'masonry' && mode !== 'masonry') {
            this._cleanupMasonry();
        }

        console.log(`[GridLayoutEngine] Mode set: ${mode}`);
    }

    /** @returns {string} Current layout mode */
    getMode() {
        return this._mode;
    }

    /**
     * Apply the current layout mode to the container.
     * Called by camera-selector-controller._updateGridLayout().
     * @param {number} count - Number of visible stream items
     */
    apply(count) {
        this._lastCount = count;

        switch (this._mode) {
            case 'uniform':
                this._applyUniform(count);
                break;
            case 'last-row-stretch':
                this._applyLastRowStretch(count);
                break;
            case 'auto-fit':
                this._applyAutoFit(count);
                break;
            case 'masonry':
                this._applyMasonry(count);
                break;
            default:
                this._applyUniform(count);
        }
    }

    // =========================================================================
    //  MODE: Uniform — standard CSS grid, no stretch
    // =========================================================================

    /**
     * Standard CSS grid with grid-N classes. Empty cells in the last row are accepted.
     * This is the default mode — what the NVR had before the layout engine was added.
     * @param {number} count - Visible camera count
     */
    _applyUniform(count) {
        this._cleanupMasonry();
        const cols = this._pickDefaultCols(count);

        // Reset any column spans from previous stretch mode
        this.$container.find('.stream-item:visible').css('grid-column', '');

        this._applyGridClass(cols);
        console.log(`[GridLayoutEngine] Uniform: ${cols} cols for ${count} cameras`);
    }

    // =========================================================================
    //  MODE: Last-Row Stretch — CSS grid + last-row items span extra columns
    // =========================================================================

    /**
     * Standard CSS grid, but items in the last row span extra columns
     * to fill the full row width. Uses integer spans distributed as evenly
     * as possible:
     *   baseSpan  = floor(cols / lastRowCount)
     *   remainder = cols % lastRowCount
     *   first `remainder` items get (baseSpan + 1), rest get baseSpan
     *
     * @param {number} count - Visible camera count
     */
    _applyLastRowStretch(count) {
        this._cleanupMasonry();
        const cols = this._pickDefaultCols(count);
        this._applyGridClass(cols);

        // Reset previous spans first
        const $items = this.$container.find('.stream-item:visible');
        $items.css('grid-column', '');

        if (count === 0 || cols <= 1) return;

        // How many items in the last row
        const lastRowCount = count % cols;
        // If the last row is full (remainder 0), nothing to stretch
        if (lastRowCount === 0) return;

        const baseSpan  = Math.floor(cols / lastRowCount);
        const remainder = cols % lastRowCount;

        // The last-row items are the last `lastRowCount` visible items
        const startIdx = count - lastRowCount;
        for (let i = 0; i < lastRowCount; i++) {
            const span = (i < remainder) ? baseSpan + 1 : baseSpan;
            $items.eq(startIdx + i).css('grid-column', `span ${span}`);
        }

        console.log(`[GridLayoutEngine] Last-row stretch: ${cols} cols, ${lastRowCount} in last row`);
    }

    // =========================================================================
    //  MODE: Auto-Fit — pick column count that minimizes waste
    // =========================================================================

    /**
     * Try column counts 1-6 and pick the one that produces the fewest empty cells.
     * Ties are broken by preferring more columns (better use of screen width).
     * Then apply the chosen column count as a standard grid + optional last-row stretch.
     *
     * @param {number} count - Visible camera count
     */
    _applyAutoFit(count) {
        this._cleanupMasonry();

        const cols = this._pickOptimalCols(count);
        this._applyGridClass(cols);

        // Also stretch last row for auto-fit (maximize space usage)
        const $items = this.$container.find('.stream-item:visible');
        $items.css('grid-column', '');

        if (count > 0 && cols > 1) {
            const lastRowCount = count % cols;
            if (lastRowCount > 0) {
                const baseSpan  = Math.floor(cols / lastRowCount);
                const remainder = cols % lastRowCount;
                const startIdx  = count - lastRowCount;
                for (let i = 0; i < lastRowCount; i++) {
                    const span = (i < remainder) ? baseSpan + 1 : baseSpan;
                    $items.eq(startIdx + i).css('grid-column', `span ${span}`);
                }
            }
        }

        console.log(`[GridLayoutEngine] Auto-fit: chose ${cols} cols for ${count} cameras`);
    }

    /**
     * Find the column count (1-6) that minimizes empty cells.
     * waste = (rows * cols) - count, where rows = ceil(count / cols).
     * On tie, prefer more columns (wider tiles are better than empty cells).
     *
     * @param {number} count - Visible camera count
     * @returns {number} Optimal column count
     */
    _pickOptimalCols(count) {
        if (count <= 1) return 1;

        let bestCols = 1;
        let bestWaste = count;

        for (let cols = 1; cols <= 6; cols++) {
            const rows  = Math.ceil(count / cols);
            const waste = (rows * cols) - count;
            // Prefer fewer waste cells; on tie prefer more columns
            if (waste < bestWaste || (waste === bestWaste && cols > bestCols)) {
                bestWaste = waste;
                bestCols  = cols;
            }
        }

        return bestCols;
    }

    // =========================================================================
    //  MODE: Masonry — absolute positioning, pixel-perfect fill
    // =========================================================================

    /**
     * Absolute positioning mode: calculate exact x/y/w/h for each tile
     * so no pixel of the container is wasted. The last row's items are
     * wider to fill the remaining width.
     *
     * Container gets display: block (overrides CSS grid) via .layout-masonry class.
     * Each .stream-item gets position: absolute with computed top/left/width/height.
     *
     * @param {number} count - Visible camera count
     */
    _applyMasonry(count) {
        if (count === 0) return;

        // Remove CSS grid classes — masonry takes over
        this.$container
            .removeClass('grid-1 grid-2 grid-3 grid-4 grid-5 grid-6')
            .addClass('layout-masonry');

        // Get container inner dimensions
        const containerW = this.$container.innerWidth();
        const containerH = this.$container.innerHeight();

        if (containerW <= 0 || containerH <= 0) return;

        // Determine column count using same thresholds as uniform
        const cols = this._pickDefaultCols(count);
        const rows = Math.ceil(count / cols);
        const lastRowCount = count % cols || cols;

        // Gap between tiles (match grid gap when in "spaced" style, 0 for "attached")
        const isAttached = this.$container.hasClass('grid-attached');
        const gap = isAttached ? 0 : 8; // 8px gap (half of 1rem at 16px base) to keep it tight

        // Calculate cell dimensions accounting for gaps
        // Total gap space in a row = (cols - 1) * gap
        const cellW = (containerW - (cols - 1) * gap) / cols;
        const cellH = (containerH - (rows - 1) * gap) / rows;

        // Last row: fewer items → wider cells
        const lastRowCellW = (containerW - (lastRowCount - 1) * gap) / lastRowCount;

        // Position each visible stream item
        const $items = this.$container.find('.stream-item:visible');
        $items.each((idx, el) => {
            const $el   = $(el);
            const row   = Math.floor(idx / cols);
            const col   = idx % cols;
            const isLastRow = (row === rows - 1) && (lastRowCount < cols);

            let top, left, width, height;

            if (isLastRow) {
                // Last row: recalculate column position for wider cells
                const lastCol = idx - (rows - 1) * cols;
                top    = row * (cellH + gap);
                left   = lastCol * (lastRowCellW + gap);
                width  = lastRowCellW;
                height = cellH;
            } else {
                top    = row * (cellH + gap);
                left   = col * (cellW + gap);
                width  = cellW;
                height = cellH;
            }

            $el.css({
                position: 'absolute',
                top:    `${top}px`,
                left:   `${left}px`,
                width:  `${width}px`,
                height: `${height}px`,
                // Reset any grid-column spans from other modes
                'grid-column': '',
            });
        });

        // Attach resize handler (debounced) if not already attached
        this._attachResizeHandler();

        console.log(`[GridLayoutEngine] Masonry: ${cols}x${rows}, ${count} tiles, gap=${gap}px`);
    }

    // =========================================================================
    //  Shared helpers
    // =========================================================================

    /**
     * Pick column count using the standard NVR thresholds.
     * @param {number} count - Visible camera count
     * @returns {number} Column count (1-5)
     */
    _pickDefaultCols(count) {
        if (count === 0) return 1;
        if (count === 1) return 1;
        if (count <= 4)  return 2;
        if (count <= 9)  return 3;
        if (count <= 16) return 4;
        return 5;
    }

    /**
     * Apply a CSS grid-N class and remove others.
     * @param {number} cols - Column count (1-6)
     */
    _applyGridClass(cols) {
        this.$container
            .removeClass('grid-1 grid-2 grid-3 grid-4 grid-5 grid-6 layout-masonry')
            .addClass(`grid-${cols}`);
    }

    /**
     * Remove all masonry artifacts: position: absolute on items, layout-masonry class,
     * resize handler. Restores items to static positioning for CSS grid modes.
     */
    _cleanupMasonry() {
        this.$container.removeClass('layout-masonry');
        // Reset inline styles set by masonry on stream items
        this.$container.find('.stream-item').css({
            position: '',
            top:      '',
            left:     '',
            width:    '',
            height:   '',
        });
        this._detachResizeHandler();
    }

    /**
     * Attach a debounced window resize handler for masonry mode.
     * Re-applies masonry layout when the window is resized.
     */
    _attachResizeHandler() {
        if (this._resizeHandler) return; // already attached

        let debounceTimer;
        this._resizeHandler = () => {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => {
                if (this._mode === 'masonry' && this._lastCount > 0) {
                    this._applyMasonry(this._lastCount);
                }
            }, 150);
        };

        $(window).on('resize.gridLayoutEngine', this._resizeHandler);
    }

    /** Detach the masonry resize handler. */
    _detachResizeHandler() {
        if (this._resizeHandler) {
            $(window).off('resize.gridLayoutEngine', this._resizeHandler);
            this._resizeHandler = null;
        }
    }

    /** Cleanup — remove event listeners. Call when destroying the controller. */
    destroy() {
        this._detachResizeHandler();
    }
}
