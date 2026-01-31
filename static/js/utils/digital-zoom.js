/**
 * Digital Zoom Manager - ES6 Module
 *
 * Provides client-side digital zoom via CSS transforms for video/img elements.
 * Works with both HLS (video) and MJPEG (img) streams.
 *
 * Design:
 * - Uses transform: scale() for GPU-accelerated zoom
 * - Parent container (.stream-item) already has overflow: hidden
 * - Tracks zoom level and pan offset per camera
 * - Supports mouse drag and touch pan when zoomed
 *
 * Integration:
 * - Called by PTZ controller when optical zoom reaches limit (timeout-based detection)
 * - Or called directly for cameras without optical zoom capability
 */

export class DigitalZoomManager {
    constructor() {
        // Per-camera zoom state: { cameraId: { level: 1.0, panX: 0, panY: 0, element: null } }
        this.zoomState = new Map();

        // Configuration
        this.config = {
            minZoom: 1.0,       // No zoom (original size)
            maxZoom: 8.0,       // Maximum 8x digital zoom (user preference)
            zoomStep: 0.5,      // Increment per zoom action
            transitionMs: 150   // CSS transition duration for smooth zoom
        };

        // Track pan drag state
        this.dragState = {
            active: false,
            cameraId: null,
            startX: 0,
            startY: 0,
            startPanX: 0,
            startPanY: 0
        };

        // Bind event handlers for pan (need to reference for removal)
        this._onMouseMove = this._handleMouseMove.bind(this);
        this._onMouseUp = this._handleMouseUp.bind(this);
        this._onTouchMove = this._handleTouchMove.bind(this);
        this._onTouchEnd = this._handleTouchEnd.bind(this);

        console.log('[DigitalZoom] Manager initialized', this.config);
    }

    /**
     * Initialize digital zoom for a camera's stream element.
     * Call this when stream element is created or updated.
     *
     * @param {string} cameraId - Camera serial number
     * @param {HTMLElement} element - Video or img element with .stream-video class
     */
    initializeForCamera(cameraId, element) {
        if (!element) {
            console.warn(`[DigitalZoom] ${cameraId}: No element provided`);
            return;
        }

        // Initialize or reset zoom state
        this.zoomState.set(cameraId, {
            level: 1.0,
            panX: 0,
            panY: 0,
            element: element
        });

        // Ensure element has transform-origin at center
        element.style.transformOrigin = 'center center';
        element.style.transition = `transform ${this.config.transitionMs}ms ease-out`;

        // Reset any existing transform
        this._applyTransform(cameraId);

        // Setup pan event listeners on the element
        this._setupPanListeners(cameraId, element);

        console.log(`[DigitalZoom] ${cameraId}: Initialized for element`, element.tagName);
    }

    /**
     * Clean up digital zoom for a camera (call when stream stops).
     *
     * @param {string} cameraId - Camera serial number
     */
    cleanupForCamera(cameraId) {
        const state = this.zoomState.get(cameraId);
        if (state && state.element) {
            // Remove event listeners
            this._removePanListeners(state.element);

            // Reset transform
            state.element.style.transform = '';
            state.element.style.transition = '';
        }

        this.zoomState.delete(cameraId);
        console.log(`[DigitalZoom] ${cameraId}: Cleaned up`);
    }

    /**
     * Zoom in one step for a camera.
     *
     * @param {string} cameraId - Camera serial number
     * @returns {boolean} True if zoom was applied, false if at max
     */
    zoomIn(cameraId) {
        const state = this.zoomState.get(cameraId);
        if (!state) {
            console.warn(`[DigitalZoom] ${cameraId}: Not initialized`);
            return false;
        }

        if (state.level >= this.config.maxZoom) {
            console.log(`[DigitalZoom] ${cameraId}: Already at max zoom (${this.config.maxZoom}x)`);
            return false;
        }

        state.level = Math.min(state.level + this.config.zoomStep, this.config.maxZoom);
        this._applyTransform(cameraId);

        console.log(`[DigitalZoom] ${cameraId}: Zoomed in to ${state.level}x`);
        return true;
    }

    /**
     * Zoom out one step for a camera.
     *
     * @param {string} cameraId - Camera serial number
     * @returns {boolean} True if zoom was applied, false if at min
     */
    zoomOut(cameraId) {
        const state = this.zoomState.get(cameraId);
        if (!state) {
            console.warn(`[DigitalZoom] ${cameraId}: Not initialized`);
            return false;
        }

        if (state.level <= this.config.minZoom) {
            console.log(`[DigitalZoom] ${cameraId}: Already at min zoom (${this.config.minZoom}x)`);
            return false;
        }

        state.level = Math.max(state.level - this.config.zoomStep, this.config.minZoom);

        // Reset pan when returning to 1.0x (no point panning when not zoomed)
        if (state.level === this.config.minZoom) {
            state.panX = 0;
            state.panY = 0;
        }

        this._applyTransform(cameraId);

        console.log(`[DigitalZoom] ${cameraId}: Zoomed out to ${state.level}x`);
        return true;
    }

    /**
     * Reset zoom to 1.0x for a camera.
     *
     * @param {string} cameraId - Camera serial number
     */
    resetZoom(cameraId) {
        const state = this.zoomState.get(cameraId);
        if (!state) return;

        state.level = this.config.minZoom;
        state.panX = 0;
        state.panY = 0;

        this._applyTransform(cameraId);
        console.log(`[DigitalZoom] ${cameraId}: Reset to 1.0x`);
    }

    /**
     * Get current zoom level for a camera.
     *
     * @param {string} cameraId - Camera serial number
     * @returns {number} Current zoom level (1.0 = no zoom)
     */
    getZoomLevel(cameraId) {
        const state = this.zoomState.get(cameraId);
        return state ? state.level : 1.0;
    }

    /**
     * Check if camera is currently digitally zoomed (level > 1.0).
     *
     * @param {string} cameraId - Camera serial number
     * @returns {boolean} True if digitally zoomed
     */
    isZoomed(cameraId) {
        return this.getZoomLevel(cameraId) > this.config.minZoom;
    }

    /**
     * Check if camera has reached maximum digital zoom.
     *
     * @param {string} cameraId - Camera serial number
     * @returns {boolean} True if at max zoom
     */
    isAtMaxZoom(cameraId) {
        return this.getZoomLevel(cameraId) >= this.config.maxZoom;
    }

    /**
     * Check if camera has reached minimum digital zoom.
     *
     * @param {string} cameraId - Camera serial number
     * @returns {boolean} True if at min zoom (1.0x)
     */
    isAtMinZoom(cameraId) {
        return this.getZoomLevel(cameraId) <= this.config.minZoom;
    }

    /**
     * Set zoom level directly (for programmatic control).
     *
     * @param {string} cameraId - Camera serial number
     * @param {number} level - Zoom level (clamped to min/max)
     */
    setZoomLevel(cameraId, level) {
        const state = this.zoomState.get(cameraId);
        if (!state) return;

        state.level = Math.max(this.config.minZoom, Math.min(level, this.config.maxZoom));

        // Reset pan if at 1.0x
        if (state.level === this.config.minZoom) {
            state.panX = 0;
            state.panY = 0;
        }

        this._applyTransform(cameraId);
    }

    // =========================================================================
    // Pan Support (drag to pan when zoomed)
    // =========================================================================

    /**
     * Setup pan event listeners for mouse/touch drag.
     * @private
     */
    _setupPanListeners(cameraId, element) {
        // Mouse events
        element.addEventListener('mousedown', (e) => this._handleMouseDown(e, cameraId));

        // Touch events
        element.addEventListener('touchstart', (e) => this._handleTouchStart(e, cameraId), { passive: false });

        // Prevent default drag behavior on images
        element.addEventListener('dragstart', (e) => e.preventDefault());
    }

    /**
     * Remove pan event listeners.
     * @private
     */
    _removePanListeners(element) {
        // Note: Can't easily remove listeners added with bind() without storing references
        // The element is being cleaned up anyway, so this is mostly for completeness
        element.removeEventListener('dragstart', (e) => e.preventDefault());
    }

    _handleMouseDown(e, cameraId) {
        const state = this.zoomState.get(cameraId);
        if (!state || state.level <= this.config.minZoom) return;

        // Only handle left click
        if (e.button !== 0) return;

        e.preventDefault();

        this.dragState = {
            active: true,
            cameraId: cameraId,
            startX: e.clientX,
            startY: e.clientY,
            startPanX: state.panX,
            startPanY: state.panY
        };

        // Add move/up listeners to document (capture movement outside element)
        document.addEventListener('mousemove', this._onMouseMove);
        document.addEventListener('mouseup', this._onMouseUp);

        // Change cursor to grabbing
        state.element.style.cursor = 'grabbing';
    }

    _handleMouseMove(e) {
        if (!this.dragState.active) return;

        const state = this.zoomState.get(this.dragState.cameraId);
        if (!state) return;

        const deltaX = e.clientX - this.dragState.startX;
        const deltaY = e.clientY - this.dragState.startY;

        // Calculate new pan values with bounds checking
        this._updatePan(this.dragState.cameraId, deltaX, deltaY);
    }

    _handleMouseUp() {
        if (!this.dragState.active) return;

        const state = this.zoomState.get(this.dragState.cameraId);
        if (state && state.element) {
            state.element.style.cursor = state.level > this.config.minZoom ? 'grab' : '';
        }

        this.dragState.active = false;

        document.removeEventListener('mousemove', this._onMouseMove);
        document.removeEventListener('mouseup', this._onMouseUp);
    }

    _handleTouchStart(e, cameraId) {
        const state = this.zoomState.get(cameraId);
        if (!state || state.level <= this.config.minZoom) return;

        // Only single touch for pan
        if (e.touches.length !== 1) return;

        e.preventDefault();

        const touch = e.touches[0];

        this.dragState = {
            active: true,
            cameraId: cameraId,
            startX: touch.clientX,
            startY: touch.clientY,
            startPanX: state.panX,
            startPanY: state.panY
        };

        document.addEventListener('touchmove', this._onTouchMove, { passive: false });
        document.addEventListener('touchend', this._onTouchEnd);
    }

    _handleTouchMove(e) {
        if (!this.dragState.active) return;
        if (e.touches.length !== 1) return;

        e.preventDefault();

        const touch = e.touches[0];
        const deltaX = touch.clientX - this.dragState.startX;
        const deltaY = touch.clientY - this.dragState.startY;

        this._updatePan(this.dragState.cameraId, deltaX, deltaY);
    }

    _handleTouchEnd() {
        this.dragState.active = false;

        document.removeEventListener('touchmove', this._onTouchMove);
        document.removeEventListener('touchend', this._onTouchEnd);
    }

    /**
     * Update pan position with bounds checking.
     * @private
     */
    _updatePan(cameraId, deltaX, deltaY) {
        const state = this.zoomState.get(cameraId);
        if (!state || !state.element) return;

        // Calculate pan bounds based on zoom level
        // When zoomed 2x, the image is twice as big, so we can pan up to 50% of container size
        // Formula: maxPan = (scale - 1) / scale * 50% = (scale - 1) * 50 / scale
        const element = state.element;
        const container = element.parentElement;
        if (!container) return;

        const containerWidth = container.clientWidth;
        const containerHeight = container.clientHeight;

        // Max pan in pixels: how far the image edge extends beyond container
        const scaledWidth = containerWidth * state.level;
        const scaledHeight = containerHeight * state.level;
        const maxPanX = (scaledWidth - containerWidth) / 2;
        const maxPanY = (scaledHeight - containerHeight) / 2;

        // Calculate new pan values (convert delta from screen pixels to transform pixels)
        // Note: deltaX/Y are in screen coordinates, but we're scaling the image
        // The effective pan should feel 1:1 with finger/cursor movement
        let newPanX = this.dragState.startPanX + deltaX;
        let newPanY = this.dragState.startPanY + deltaY;

        // Clamp to bounds
        newPanX = Math.max(-maxPanX, Math.min(maxPanX, newPanX));
        newPanY = Math.max(-maxPanY, Math.min(maxPanY, newPanY));

        state.panX = newPanX;
        state.panY = newPanY;

        // Apply transform without transition for responsive drag
        this._applyTransform(cameraId, true);
    }

    /**
     * Apply CSS transform based on current zoom state.
     * @private
     */
    _applyTransform(cameraId, skipTransition = false) {
        const state = this.zoomState.get(cameraId);
        if (!state || !state.element) return;

        const element = state.element;

        // Temporarily disable transition for drag operations
        if (skipTransition) {
            element.style.transition = 'none';
        } else {
            element.style.transition = `transform ${this.config.transitionMs}ms ease-out`;
        }

        // Combine scale and translate for pan
        // Note: translate values are in pixels, applied before scale
        element.style.transform = `translate(${state.panX}px, ${state.panY}px) scale(${state.level})`;

        // Update cursor to indicate draggable when zoomed
        if (state.level > this.config.minZoom && !this.dragState.active) {
            element.style.cursor = 'grab';
        } else if (state.level <= this.config.minZoom) {
            element.style.cursor = '';
        }

        // Re-enable transition after a frame (for next zoom operation)
        if (skipTransition) {
            requestAnimationFrame(() => {
                element.style.transition = `transform ${this.config.transitionMs}ms ease-out`;
            });
        }
    }
}

// Export singleton instance for app-wide use
export const digitalZoomManager = new DigitalZoomManager();
