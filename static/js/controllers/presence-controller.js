/**
 * Presence Controller
 *
 * Manages household presence indicators in the navbar.
 * Provides toggle functionality with API integration.
 *
 * @module presence-controller
 * @author NVR System
 * @date January 28, 2026
 */

/**
 * PresenceController class handles presence UI and API calls.
 *
 * Features:
 * - Renders presence buttons in navbar
 * - Toggle presence on click
 * - Auto-refresh presence status periodically
 * - Visual feedback for state changes
 */
class PresenceController {
    /**
     * Initialize the presence controller.
     *
     * @param {string} containerId - ID of the container element for presence buttons
     * @param {number} refreshInterval - Interval in ms to refresh presence (default: 30000)
     */
    constructor(containerId = 'presence-container', refreshInterval = 30000) {
        this.$container = $(`#${containerId}`);
        this.refreshInterval = refreshInterval;
        this._refreshTimer = null;
        this._people = [];

        if (!this.$container.length) {
            console.warn('[Presence] Container not found:', containerId);
            return;
        }

        this._init();
    }

    /**
     * Initialize the controller - load presence and set up event handlers.
     */
    async _init() {
        console.log('[Presence] Initializing presence controller');

        // Load initial presence status
        await this._loadPresence();

        // Set up click handlers
        this._setupEventHandlers();

        // Start auto-refresh
        this._startAutoRefresh();
    }

    /**
     * Load presence status from API and render buttons.
     */
    async _loadPresence() {
        try {
            const response = await $.ajax({
                url: '/api/presence',
                method: 'GET',
                dataType: 'json'
            });

            this._people = response;
            this._render();

        } catch (error) {
            console.error('[Presence] Failed to load presence:', error);
            // Render error state
            this.$container.html('<span class="presence-error">Presence unavailable</span>');
        }
    }

    /**
     * Render presence buttons based on current state.
     */
    _render() {
        if (!this._people || this._people.length === 0) {
            this.$container.empty();
            return;
        }

        const html = this._people.map(person => {
            const statusClass = person.is_present ? 'present' : 'away';
            const statusText = person.is_present ? 'Home' : 'Away';

            return `
                <button class="presence-btn ${statusClass}"
                        data-person="${this._escapeHtml(person.person_name)}"
                        title="${this._escapeHtml(person.person_name)}: ${statusText} (click to toggle)">
                    <span class="presence-status-dot"></span>
                    <span class="presence-name">${this._escapeHtml(person.person_name)}</span>
                </button>
            `;
        }).join('');

        this.$container.html(html);
    }

    /**
     * Set up event handlers for presence buttons.
     */
    _setupEventHandlers() {
        // Click handler for presence buttons (delegated)
        this.$container.on('click', '.presence-btn', async (e) => {
            e.preventDefault();
            e.stopPropagation();

            const $btn = $(e.currentTarget);
            const personName = $btn.data('person');

            if (!personName || $btn.hasClass('loading')) {
                return;
            }

            await this._togglePresence($btn, personName);
        });
    }

    /**
     * Toggle presence status for a person.
     *
     * @param {jQuery} $btn - The button element
     * @param {string} personName - Name of the person to toggle
     */
    async _togglePresence($btn, personName) {
        // Add loading state
        $btn.addClass('loading');

        try {
            const response = await $.ajax({
                url: `/api/presence/${encodeURIComponent(personName)}/toggle`,
                method: 'POST',
                dataType: 'json'
            });

            if (response.success) {
                // Update UI immediately
                const isPresent = response.is_present;
                $btn.removeClass('present away')
                    .addClass(isPresent ? 'present' : 'away');

                // Update title
                const statusText = isPresent ? 'Home' : 'Away';
                $btn.attr('title', `${personName}: ${statusText} (click to toggle)`);

                // Update internal state
                const person = this._people.find(p => p.person_name === personName);
                if (person) {
                    person.is_present = isPresent;
                }

                console.log(`[Presence] ${personName} toggled to ${statusText}`);
            } else {
                console.error('[Presence] Toggle failed:', response.error);
            }

        } catch (error) {
            console.error('[Presence] Failed to toggle presence:', error);
        } finally {
            $btn.removeClass('loading');
        }
    }

    /**
     * Start periodic auto-refresh of presence status.
     */
    _startAutoRefresh() {
        if (this._refreshTimer) {
            clearInterval(this._refreshTimer);
        }

        this._refreshTimer = setInterval(() => {
            this._loadPresence();
        }, this.refreshInterval);
    }

    /**
     * Stop auto-refresh.
     */
    stopAutoRefresh() {
        if (this._refreshTimer) {
            clearInterval(this._refreshTimer);
            this._refreshTimer = null;
        }
    }

    /**
     * Manual refresh of presence status.
     */
    async refresh() {
        await this._loadPresence();
    }

    /**
     * Escape HTML to prevent XSS.
     *
     * @param {string} text - Text to escape
     * @returns {string} - Escaped text
     */
    _escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Initialize on DOM ready
$(document).ready(() => {
    // Create global instance
    window.presenceController = new PresenceController();
});

// Export for module usage
export { PresenceController };
