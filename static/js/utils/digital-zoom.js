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
 * - Mouse wheel zoom support
 * - Pinch-to-zoom gesture support for touch devices
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

        // Track pinch-to-zoom state
        this.pinchState = {
            active: false,
            cameraId: null,
            initialDistance: 0,
            initialZoom: 1.0,
            centerX: 0,
            centerY: 0
        };

        // Bind event handlers for pan (need to reference for removal)
        this._onMouseMove = this._handleMouseMove.bind(this);
        this._onMouseUp = this._handleMouseUp.bind(this);
        this._onTouchMove = this._handleTouchMove.bind(this);
        this._onTouchEnd = this._handleTouchEnd.bind(this);

        // Bind wheel handler
        this._onWheel = this._handleWheel.bind(this);

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
     * Also sets up wheel zoom and pinch-to-zoom.
     * @private
     */
    _setupPanListeners(cameraId, element) {
        // Mouse events for pan
        element.addEventListener('mousedown', (e) => this._handleMouseDown(e, cameraId));

        // Mouse wheel for zoom
        element.addEventListener('wheel', (e) => this._handleWheel(e, cameraId), { passive: false });

        // Touch events for pan and pinch-to-zoom
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

    // =========================================================================
    // Mouse Wheel Zoom
    // =========================================================================

    /**
     * Handle mouse wheel for zoom in/out.
     * Wheel up = zoom in, wheel down = zoom out.
     * @private
     */
    _handleWheel(e, cameraId) {
        const state = this.zoomState.get(cameraId);
        if (!state) return;

        // Prevent page scroll
        e.preventDefault();

        // Determine zoom direction from wheel delta
        // deltaY < 0 = wheel up = zoom in
        // deltaY > 0 = wheel down = zoom out
        const zoomIn = e.deltaY < 0;

        // Get zoom point relative to container (not element, which may be transformed)
        const container = state.element.parentElement;
        if (!container) return;
        const rect = container.getBoundingClientRect();
        const cursorX = e.clientX - rect.left;
        const cursorY = e.clientY - rect.top;

        // Calculate zoom step (smaller steps for smoother wheel zoom)
        const wheelZoomStep = this.config.zoomStep * 0.5;
        const oldLevel = state.level;

        if (zoomIn) {
            state.level = Math.min(state.level + wheelZoomStep, this.config.maxZoom);
        } else {
            state.level = Math.max(state.level - wheelZoomStep, this.config.minZoom);
        }

        // Reset pan when returning to 1.0x
        if (state.level === this.config.minZoom) {
            state.panX = 0;
            state.panY = 0;
        } else if (oldLevel !== state.level) {
            // Adjust pan to zoom toward cursor position
            this._adjustPanForZoom(cameraId, cursorX, cursorY, oldLevel, state.level);
        }

        this._applyTransform(cameraId);

        // Dispatch custom event for UI updates
        this._dispatchZoomEvent(cameraId, state.level);
    }

    /**
     * Adjust pan position to zoom toward a specific point.
     * This makes the zoom feel more natural - zooming toward where the cursor is.
     * @private
     */
    _adjustPanForZoom(cameraId, cursorX, cursorY, oldLevel, newLevel) {
        const state = this.zoomState.get(cameraId);
        if (!state || !state.element) return;

        const container = state.element.parentElement;
        if (!container) return;

        // Use container dimensions (not element, which may be transformed)
        const containerWidth = container.clientWidth;
        const containerHeight = container.clientHeight;

        // Container center
        const centerX = containerWidth / 2;
        const centerY = containerHeight / 2;

        // Cursor offset from center (in container coordinates)
        // cursorX/Y are already relative to container from getBoundingClientRect
        const offsetX = cursorX - centerX;
        const offsetY = cursorY - centerY;

        // Convert cursor position to "content space" accounting for current pan and zoom
        // The point under cursor in content coordinates:
        // contentX = (cursorX - centerX - panX) / oldLevel + centerX
        // We want this point to stay under cursor after zoom change

        // Calculate where the cursor points to in the original (unzoomed) content
        const contentX = (offsetX - state.panX) / oldLevel;
        const contentY = (offsetY - state.panY) / oldLevel;

        // After zooming to newLevel, this content point should still be under cursor
        // newOffsetX = contentX * newLevel + newPanX = offsetX (we want cursor to stay put)
        // Solving: newPanX = offsetX - contentX * newLevel
        const newPanX = offsetX - contentX * newLevel;
        const newPanY = offsetY - contentY * newLevel;

        // Calculate bounds for new zoom level
        const scaledWidth = containerWidth * newLevel;
        const scaledHeight = containerHeight * newLevel;
        const maxPanX = (scaledWidth - containerWidth) / 2;
        const maxPanY = (scaledHeight - containerHeight) / 2;

        // Clamp to bounds
        state.panX = Math.max(-maxPanX, Math.min(maxPanX, newPanX));
        state.panY = Math.max(-maxPanY, Math.min(maxPanY, newPanY));
    }

    /**
     * Dispatch custom event when zoom level changes.
     * PTZ controller listens for this to update UI.
     * @private
     */
    _dispatchZoomEvent(cameraId, level) {
        const event = new CustomEvent('digitalzoomchange', {
            detail: { cameraId, level },
            bubbles: true
        });
        document.dispatchEvent(event);
    }

    // =========================================================================
    // Touch Pan and Pinch-to-Zoom
    // =========================================================================

    _handleTouchStart(e, cameraId) {
        const state = this.zoomState.get(cameraId);
        if (!state) return;

        e.preventDefault();

        // Two-finger touch = pinch-to-zoom
        if (e.touches.length === 2) {
            const distance = this._getTouchDistance(e.touches[0], e.touches[1]);
            const center = this._getTouchCenter(e.touches[0], e.touches[1]);

            this.pinchState = {
                active: true,
                cameraId: cameraId,
                initialDistance: distance,
                initialZoom: state.level,
                centerX: center.x,
                centerY: center.y
            };

            // Cancel any pan operation
            this.dragState.active = false;

            document.addEventListener('touchmove', this._onTouchMove, { passive: false });
            document.addEventListener('touchend', this._onTouchEnd);
            return;
        }

        // Single touch = pan (only if zoomed)
        if (e.touches.length === 1 && state.level > this.config.minZoom) {
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
    }

    _handleTouchMove(e) {
        e.preventDefault();

        // Handle pinch-to-zoom (two fingers)
        if (this.pinchState.active && e.touches.length === 2) {
            const state = this.zoomState.get(this.pinchState.cameraId);
            if (!state) return;

            const newDistance = this._getTouchDistance(e.touches[0], e.touches[1]);
            const scale = newDistance / this.pinchState.initialDistance;

            // Calculate new zoom level
            const oldLevel = state.level;
            state.level = Math.max(
                this.config.minZoom,
                Math.min(this.config.maxZoom, this.pinchState.initialZoom * scale)
            );

            // Reset pan when returning to 1.0x
            if (state.level === this.config.minZoom) {
                state.panX = 0;
                state.panY = 0;
            } else {
                // Adjust pan toward pinch center (use container rect, not element)
                const container = state.element.parentElement;
                if (container) {
                    const rect = container.getBoundingClientRect();
                    const cursorX = this.pinchState.centerX - rect.left;
                    const cursorY = this.pinchState.centerY - rect.top;
                    this._adjustPanForZoom(this.pinchState.cameraId, cursorX, cursorY, oldLevel, state.level);
                }
            }

            this._applyTransform(this.pinchState.cameraId, true);
            this._dispatchZoomEvent(this.pinchState.cameraId, state.level);
            return;
        }

        // Handle pan (single finger)
        if (this.dragState.active && e.touches.length === 1) {
            const touch = e.touches[0];
            const deltaX = touch.clientX - this.dragState.startX;
            const deltaY = touch.clientY - this.dragState.startY;

            this._updatePan(this.dragState.cameraId, deltaX, deltaY);
        }
    }

    _handleTouchEnd(e) {
        // End pinch-to-zoom
        if (this.pinchState.active) {
            this.pinchState.active = false;
            this._dispatchZoomEvent(this.pinchState.cameraId, this.zoomState.get(this.pinchState.cameraId)?.level || 1.0);
        }

        // End pan
        this.dragState.active = false;

        document.removeEventListener('touchmove', this._onTouchMove);
        document.removeEventListener('touchend', this._onTouchEnd);
    }

    /**
     * Calculate distance between two touch points.
     * @private
     */
    _getTouchDistance(touch1, touch2) {
        const dx = touch2.clientX - touch1.clientX;
        const dy = touch2.clientY - touch1.clientY;
        return Math.sqrt(dx * dx + dy * dy);
    }

    /**
     * Calculate center point between two touch points.
     * @private
     */
    _getTouchCenter(touch1, touch2) {
        return {
            x: (touch1.clientX + touch2.clientX) / 2,
            y: (touch1.clientY + touch2.clientY) / 2
        };
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
