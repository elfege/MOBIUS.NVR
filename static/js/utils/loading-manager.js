/**
 * Loading Manager Module - ES6 + jQuery
 * Handles loading overlay display and messaging
 */

export class LoadingManager {
    constructor() {
        this.$overlay = null;
        this.$message = null;
        this.init();
    }

    init() {
        // Check if loading overlay exists, create if not
        if ($('#loading-overlay').length === 0) {
            this.createOverlay();
        }

        this.$overlay = $('#loading-overlay');
        this.$message = $('#loading-message');
    }

    createOverlay() {
        const overlayHTML = `
            <div id="loading-overlay" class="loading-overlay" style="display: none;">
                <div class="loading-content">
                    <div class="spinner"></div>
                    <div id="loading-message" class="loading-message">Loading...</div>
                </div>
            </div>
        `;

        $('body').append(overlayHTML);
    }

    show(message = 'Loading...') {
        if (this.$message) {
            this.$message.text(message);
        }
        if (this.$overlay) {
            this.$overlay.fadeIn(200);
        }
    }

    hide() {
        if (this.$overlay) {
            this.$overlay.fadeOut(200);
        }
    }

    updateMessage(message) {
        if (this.$message) {
            this.$message.text(message);
        }
    }

    isVisible() {
        return this.$overlay && this.$overlay.is(':visible');
    }
}
