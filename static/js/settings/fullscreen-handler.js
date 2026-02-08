/**
 * FULLSCREEN HANDLER MODULE (ES6 + jQuery)
 *
 * PURPOSE: Manages all fullscreen-related functionality
 * - Enter/exit fullscreen mode
 * - Auto-fullscreen on page load
 * - Auto-fullscreen after exiting (with configurable delay)
 * - Save/load settings from localStorage
 *
 * LEARNING NOTES:
 * - Uses ES6 class pattern with jQuery integration
 * - Fullscreen API requires user interaction (browser security)
 * - F11 fullscreen is different from JavaScript fullscreen API
 * - Singleton pattern for single instance across application
 */

/* IMPORTANT DISTINCTION 

Explanation: We're separating concerns - fullscreen-handler.js handles page-level fullscreen (F11, header button), 
while stream.js handles camera CSS fullscreen. This prevents conflicts and keeps the code clean.

*/

export class FullscreenHandler {
    constructor() {
        // Module configuration - stored in localStorage
        this.settings = {
            autoFullscreenEnabled: false,
            autoFullscreenDelay: 3,
            gridStyle: 'spaced'
        };

        // Internal state tracking
        this.state = {
            autoFullscreenTimer: null,
            pageLoadComplete: false,
            userHasInteracted: false,
            lastExitTime: null
        };
    }

    /**
     * Initialize the fullscreen handler
     */
    init() {
        console.log('[FullscreenHandler] Initializing...');

        this.loadSettings();
        this.setupEventListeners();
        this.setupUserInteractionDetection();
        this.setupHeaderButton();
        this.applyGridStyle();

        // Mark page as loaded after streams start
        setTimeout(() => {
            this.state.pageLoadComplete = true;
            console.log('[FullscreenHandler] Page load complete');

            if (this.settings.autoFullscreenEnabled && this.state.userHasInteracted) {
                this.scheduleAutoFullscreen('page-load');
            }
        }, 1000);
    }

    /**
     * Set up all event listeners
     */
    setupEventListeners() {
        // Fullscreen change events (multiple browser prefixes)
        ['fullscreenchange', 'webkitfullscreenchange', 'mozfullscreenchange', 'msfullscreenchange'].forEach(event => {
            document.addEventListener(event, () => this.handleFullscreenChange());
        });

        // Also listen to window resize (catches F11)
        $(window).on('resize', () => this.checkFullscreenStatus());

        // Visibility change (tab switching)
        $(document).on('visibilitychange', () => this.handleVisibilityChange());
    }

    /**
     * Set up listeners to detect first user interaction
     */
    setupUserInteractionDetection() {
        const events = ['click', 'keydown', 'touchstart', 'mousedown'];

        const detectInteraction = () => {
            if (!this.state.userHasInteracted) {
                this.state.userHasInteracted = true;
                console.log('[FullscreenHandler] ✓ User interaction detected - auto-fullscreen now available');

                // Remove listeners after first interaction
                events.forEach(event => {
                    $(document).off(event, detectInteraction);
                });

                // If we're ready and waiting, trigger auto-fullscreen
                if (this.settings.autoFullscreenEnabled && this.state.pageLoadComplete) {
                    console.log('[FullscreenHandler] Triggering delayed auto-fullscreen');
                    this.scheduleAutoFullscreen('delayed-page-load');
                }
            }
        };

        // Add listeners for all interaction types
        events.forEach(event => {
            $(document).on(event, detectInteraction);
        });

        console.log('[FullscreenHandler] Waiting for user interaction before auto-fullscreen...');
    }

    /**
     * Load settings from localStorage
     */
    loadSettings() {
        try {
            const saved = localStorage.getItem('cameraStreamSettings');
            if (saved) {
                const parsed = JSON.parse(saved);
                Object.assign(this.settings, parsed);
                console.log('[FullscreenHandler] Loaded settings:', this.settings);
            }
        } catch (e) {
            console.error('[FullscreenHandler] Error loading settings:', e);
        }
    }

    /**
     * Save current settings to localStorage
     */
    saveSettings() {
        try {
            localStorage.setItem('cameraStreamSettings', JSON.stringify(this.settings));
            console.log('[FullscreenHandler] Settings saved:', this.settings);
        } catch (e) {
            console.error('[FullscreenHandler] Error saving settings:', e);
        }
    }

    /**
     * Enter fullscreen mode
     */
    async enterFullscreen() {
        const elem = document.documentElement;

        if (!this.state.userHasInteracted) {
            console.warn('[FullscreenHandler] ⚠ Cannot enter fullscreen - no user interaction yet');
            return false;
        }

        try {
            let promise;

            if (elem.requestFullscreen) {
                promise = elem.requestFullscreen();
            } else if (elem.webkitRequestFullscreen) {
                promise = elem.webkitRequestFullscreen();
            } else if (elem.mozRequestFullScreen) {
                promise = elem.mozRequestFullScreen();
            } else if (elem.msRequestFullscreen) {
                promise = elem.msRequestFullscreen();
            } else {
                console.warn('[FullscreenHandler] Fullscreen API not supported');
                return false;
            }

            console.log('[FullscreenHandler] → Entering fullscreen...');

            if (promise) {
                await promise;
                console.log('[FullscreenHandler] ✓ Fullscreen entered successfully');
                return true;
            }

            return true;

        } catch (e) {
            console.error('[FullscreenHandler] ✗ Error entering fullscreen:', e);
            return false;
        }
    }

    /**
     * Exit fullscreen mode
     */
    async exitFullscreen() {
        try {
            let promise;

            if (document.exitFullscreen) {
                promise = document.exitFullscreen();
            } else if (document.webkitExitFullscreen) {
                promise = document.webkitExitFullscreen();
            } else if (document.mozCancelFullScreen) {
                promise = document.mozCancelFullScreen();
            } else if (document.msExitFullscreen) {
                promise = document.msExitFullscreen();
            } else {
                console.warn('[FullscreenHandler] Fullscreen API not supported');
                return false;
            }

            console.log('[FullscreenHandler] ← Exiting fullscreen...');

            if (promise) {
                await promise;
                console.log('[FullscreenHandler] ✓ Fullscreen exited successfully');
                return true;
            }

            return true;

        } catch (e) {
            console.error('[FullscreenHandler] ✗ Error exiting fullscreen:', e);
            return false;
        }
    }

    /**
     * Toggle fullscreen on/off
     */
    async toggleFullscreen() {
        this.state.userHasInteracted = true;

        if (this.isFullscreen()) {
            return await this.exitFullscreen();
        } else {
            return await this.enterFullscreen();
        }
    }

    /**
     * Check if currently in fullscreen mode
     */
    isFullscreen() {
        return !!(
            document.fullscreenElement ||
            document.webkitFullscreenElement ||
            document.mozFullScreenElement ||
            document.msFullscreenElement
        );
    }

    /**
     * Check fullscreen status (called on resize to detect F11)
     */
    checkFullscreenStatus() {
        const nowFullscreen = this.isFullscreen();
        const wasFullscreen = this.state.lastExitTime !== null;

        // If we just exited fullscreen
        if (wasFullscreen && !nowFullscreen) {
            console.log('[FullscreenHandler] Fullscreen exit detected via resize');
            this.handleFullscreenExit();
        }
    }

    handleFullscreenChange() {
        const nowFullscreen = this.isFullscreen();

        console.log(`[FullscreenHandler] Fullscreen change: ${nowFullscreen ? 'ENTERED' : 'EXITED'}`);

        if (!nowFullscreen) {
            this.handleFullscreenExit();
        }
    }

    /**
     * Handle exiting fullscreen
     */
    handleFullscreenExit() {
        this.state.lastExitTime = Date.now();

        if (this.settings.autoFullscreenEnabled && this.state.pageLoadComplete && this.state.userHasInteracted) {
            console.log('[FullscreenHandler] User exited fullscreen - scheduling auto re-entry');
            this.scheduleAutoFullscreen('exit-fullscreen');
        }
    }

    /**
     * Handle visibility change (tab switching)
     */
    handleVisibilityChange() {
        if (!document.hidden && this.settings.autoFullscreenEnabled && this.state.userHasInteracted) {
            // Tab became visible - check if we should re-enter fullscreen
            if (!this.isFullscreen() && this.state.lastExitTime) {
                const timeSinceExit = Date.now() - this.state.lastExitTime;
                if (timeSinceExit > 2000) { // More than 2 seconds since exit
                    console.log('[FullscreenHandler] Tab visible and not in fullscreen - scheduling re-entry');
                    this.scheduleAutoFullscreen('tab-visible');
                }
            }
        }
    }

    /**
     * Schedule auto-fullscreen
     */
    scheduleAutoFullscreen(reason) {
        // Clear any existing timer
        if (this.state.autoFullscreenTimer) {
            clearTimeout(this.state.autoFullscreenTimer);
            this.state.autoFullscreenTimer = null;
        }

        if (!this.state.userHasInteracted) {
            console.log('[FullscreenHandler] ⚠ Cannot schedule - waiting for user interaction');
            return;
        }

        const delayMs = this.settings.autoFullscreenDelay * 1000;

        console.log(`[FullscreenHandler] ⏱ Scheduling auto-fullscreen (${reason}) in ${this.settings.autoFullscreenDelay}s`);

        this.state.autoFullscreenTimer = setTimeout(async () => {
            if (!this.isFullscreen()) {
                console.log(`[FullscreenHandler] 🎬 Auto-fullscreen triggered (${reason})`);
                const success = await this.enterFullscreen();
                if (success) {
                    console.log('[FullscreenHandler] ✓ Auto-fullscreen successful');
                } else {
                    console.warn('[FullscreenHandler] ✗ Auto-fullscreen failed');
                }
            } else {
                console.log('[FullscreenHandler] Already in fullscreen, skipping');
            }
            this.state.autoFullscreenTimer = null;
        }, delayMs);
    }

    /**
 * Set up header button listener
 */
    setupHeaderButton() {
        /* Bind both the old header icon button and the new slide-in menu button */
        const $fullscreenBtn = $('#fullscreen-toggle-btn');
        const $menuFullscreenBtn = $('#menu-fullscreen-toggle');

        if ($fullscreenBtn.length > 0) {
            $fullscreenBtn.on('click', (e) => {
                e.preventDefault();
                console.log('[FullscreenHandler] Header fullscreen button clicked');
                this.toggleFullscreen();
            });
        }

        if ($menuFullscreenBtn.length > 0) {
            $menuFullscreenBtn.on('click', (e) => {
                e.preventDefault();
                console.log('[FullscreenHandler] Menu fullscreen button clicked');
                /* Close the nav menu before toggling fullscreen */
                $('#nav-menu').removeClass('open');
                $('#nav-menu-overlay').removeClass('show');
                this.toggleFullscreen();
            });
            console.log('[FullscreenHandler] Menu fullscreen listener attached');
        }
    }

    /**
     * Cancel any pending auto-fullscreen
     */
    cancelAutoFullscreen() {
        if (this.state.autoFullscreenTimer) {
            clearTimeout(this.state.autoFullscreenTimer);
            this.state.autoFullscreenTimer = null;
            console.log('[FullscreenHandler] Auto-fullscreen cancelled');
        }
    }

    /**
     * Update auto-fullscreen enabled setting
     */
    setAutoFullscreenEnabled(enabled) {
        this.settings.autoFullscreenEnabled = enabled;
        this.saveSettings();

        console.log(`[FullscreenHandler] Auto-fullscreen ${enabled ? 'ENABLED ✓' : 'DISABLED ✗'}`);

        if (!enabled) {
            this.cancelAutoFullscreen();
        }
    }

    /**
     * Update auto-fullscreen delay setting
     */
    setAutoFullscreenDelay(seconds) {
        if (seconds < 1) seconds = 1;
        if (seconds > 60) seconds = 60;

        this.settings.autoFullscreenDelay = seconds;
        this.saveSettings();

        console.log(`[FullscreenHandler] Auto-fullscreen delay set to ${seconds}s`);
    }

    /**
     * Get current settings
     */
    getSettings() {
        return { ...this.settings };
    }

    /**
 * Set grid layout style
 */
    setGridStyle(style) {
        if (style !== 'spaced' && style !== 'attached') {
            console.warn('[FullscreenHandler] Invalid grid style:', style);
            return;
        }

        this.settings.gridStyle = style;
        this.saveSettings();
        this.applyGridStyle();

        console.log(`[FullscreenHandler] Grid style set to: ${style}`);
    }

    /**
     * Apply current grid style to DOM
     */
    applyGridStyle() {
        const $container = $('.streams-container');

        if (this.settings.gridStyle === 'attached') {
            $container.addClass('grid-attached');
        } else {
            $container.removeClass('grid-attached');
        }
    }
}

// // Export the class
// export { FullscreenHandler };

// Create and export singleton instance
export const fullscreenHandler = new FullscreenHandler();

// Initialize on document ready
$(document).ready(() => {
    fullscreenHandler.init();

    // Debug helper - expose to window for testing
    window.FullscreenHandler = fullscreenHandler;

    // Log initial state for debugging
    console.log('[FullscreenHandler] Debug - User can test with: FullscreenHandler.enterFullscreen()');
    
});
