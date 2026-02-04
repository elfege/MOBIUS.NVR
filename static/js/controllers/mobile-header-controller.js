/**
 * Mobile Header Controller
 *
 * Handles mobile-specific header show/hide behavior:
 * - Swipe-down gesture from top of screen shows header
 * - Any touch event shows header temporarily
 * - Auto-hide after inactivity period
 *
 * @module controllers/mobile-header-controller
 */

class MobileHeaderController {
    constructor() {
        // Configuration
        this.SWIPE_THRESHOLD = 50;        // Minimum swipe distance (px) to trigger
        this.TOP_ZONE_HEIGHT = 80;         // Touch zone at top of screen (px)
        this.AUTO_HIDE_DELAY = 4000;       // Auto-hide after 4 seconds of inactivity
        this.MOBILE_BREAKPOINT = 768;      // Match CSS media query

        // DOM elements
        this.$headerToggle = $('#header-toggle');
        this.$header = $('.header');

        // State
        this.touchStartY = 0;
        this.touchStartX = 0;
        this.touchStartTime = 0;
        this.autoHideTimer = null;
        this.isEnabled = false;

        // Initialize
        this._init();
    }

    /**
     * Initialize the controller
     */
    _init() {
        // Only enable on mobile devices
        if (!this._isMobileViewport()) {
            console.log('[MobileHeader] Desktop viewport - disabled');
            return;
        }

        console.log('[MobileHeader] Initializing mobile header gestures...');
        this.isEnabled = true;
        this._setupEventListeners();
    }

    /**
     * Check if current viewport is mobile
     * @returns {boolean}
     */
    _isMobileViewport() {
        return window.innerWidth <= this.MOBILE_BREAKPOINT;
    }

    /**
     * Check if device has touch support
     * @returns {boolean}
     */
    _isTouchDevice() {
        return 'ontouchstart' in window || navigator.maxTouchPoints > 0;
    }

    /**
     * Setup event listeners for touch gestures
     */
    _setupEventListeners() {
        // Touch start - record initial position
        document.addEventListener('touchstart', (e) => this._onTouchStart(e), { passive: true });

        // Touch move - detect swipe direction
        document.addEventListener('touchmove', (e) => this._onTouchMove(e), { passive: true });

        // Touch end - finalize swipe detection
        document.addEventListener('touchend', (e) => this._onTouchEnd(e), { passive: true });

        // Handle viewport resize (desktop/mobile switch)
        window.addEventListener('resize', () => this._onResize());

        console.log('[MobileHeader] Event listeners attached');
    }

    /**
     * Handle touch start
     * @param {TouchEvent} e
     */
    _onTouchStart(e) {
        if (!this.isEnabled) return;

        const touch = e.touches[0];
        this.touchStartY = touch.clientY;
        this.touchStartX = touch.clientX;
        this.touchStartTime = Date.now();

        // If touch starts in top zone while header is hidden, prepare for swipe-down
        if (this.touchStartY <= this.TOP_ZONE_HEIGHT && !this._isHeaderVisible()) {
            // Mark that we're potentially doing a swipe-down from top
            this.isTopZoneSwipe = true;
        } else {
            this.isTopZoneSwipe = false;
        }
    }

    /**
     * Handle touch move - detect swipe gestures
     * @param {TouchEvent} e
     */
    _onTouchMove(e) {
        if (!this.isEnabled) return;

        const touch = e.touches[0];
        const deltaY = touch.clientY - this.touchStartY;
        const deltaX = touch.clientX - this.touchStartX;

        // Swipe-down from top zone to show header
        if (this.isTopZoneSwipe && deltaY > this.SWIPE_THRESHOLD) {
            // Ensure it's mostly vertical (not horizontal swipe)
            if (Math.abs(deltaY) > Math.abs(deltaX) * 1.5) {
                this._showHeader();
                this.isTopZoneSwipe = false; // Prevent multiple triggers
            }
        }
    }

    /**
     * Handle touch end
     * @param {TouchEvent} e
     */
    _onTouchEnd(e) {
        if (!this.isEnabled) return;

        // Quick tap anywhere (not a long press or swipe) shows header briefly
        const touchDuration = Date.now() - this.touchStartTime;
        const changedTouch = e.changedTouches[0];
        const deltaY = Math.abs(changedTouch.clientY - this.touchStartY);
        const deltaX = Math.abs(changedTouch.clientX - this.touchStartX);

        // If it was a tap (short duration, minimal movement) and header is hidden
        if (touchDuration < 300 && deltaY < 20 && deltaX < 20 && !this._isHeaderVisible()) {
            // Don't show header on taps on interactive elements (buttons, links, etc.)
            const target = e.target;
            if (!this._isInteractiveElement(target)) {
                this._showHeaderTemporarily();
            }
        }

        this.isTopZoneSwipe = false;
    }

    /**
     * Check if an element is interactive (shouldn't trigger header show)
     * @param {Element} el
     * @returns {boolean}
     */
    _isInteractiveElement(el) {
        const interactiveTags = ['BUTTON', 'A', 'INPUT', 'SELECT', 'TEXTAREA', 'VIDEO', 'LABEL'];
        const interactiveClasses = ['btn', 'stream-video', 'stream-item', 'fullscreen-btn'];

        // Check element and its parents
        let current = el;
        while (current && current !== document.body) {
            if (interactiveTags.includes(current.tagName)) {
                return true;
            }
            if (interactiveClasses.some(cls => current.classList?.contains(cls))) {
                return true;
            }
            current = current.parentElement;
        }
        return false;
    }

    /**
     * Check if header is currently visible
     * @returns {boolean}
     */
    _isHeaderVisible() {
        return this.$headerToggle.is(':checked');
    }

    /**
     * Show the header
     */
    _showHeader() {
        this.$headerToggle.prop('checked', true);
        console.log('[MobileHeader] Header shown');
    }

    /**
     * Hide the header
     */
    _hideHeader() {
        this.$headerToggle.prop('checked', false);
        console.log('[MobileHeader] Header hidden');
    }

    /**
     * Show header temporarily with auto-hide
     */
    _showHeaderTemporarily() {
        this._showHeader();
        this._scheduleAutoHide();
    }

    /**
     * Schedule auto-hide after delay
     */
    _scheduleAutoHide() {
        // Clear any existing timer
        if (this.autoHideTimer) {
            clearTimeout(this.autoHideTimer);
        }

        this.autoHideTimer = setTimeout(() => {
            // Only auto-hide if user hasn't interacted with header
            if (this._isHeaderVisible()) {
                this._hideHeader();
            }
        }, this.AUTO_HIDE_DELAY);
    }

    /**
     * Cancel scheduled auto-hide
     */
    _cancelAutoHide() {
        if (this.autoHideTimer) {
            clearTimeout(this.autoHideTimer);
            this.autoHideTimer = null;
        }
    }

    /**
     * Handle viewport resize
     */
    _onResize() {
        const wasMobile = this.isEnabled;
        const isMobile = this._isMobileViewport();

        if (wasMobile && !isMobile) {
            // Switched to desktop - disable mobile behavior
            this.isEnabled = false;
            this._cancelAutoHide();
            console.log('[MobileHeader] Switched to desktop - disabled');
        } else if (!wasMobile && isMobile) {
            // Switched to mobile - enable mobile behavior
            this.isEnabled = true;
            console.log('[MobileHeader] Switched to mobile - enabled');
        }
    }
}

// Initialize when DOM is ready
$(document).ready(() => {
    window.mobileHeaderController = new MobileHeaderController();
});

export default MobileHeaderController;
