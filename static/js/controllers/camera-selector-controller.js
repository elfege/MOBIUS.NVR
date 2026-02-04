/**
 * Camera Selector Controller
 *
 * Provides a dropdown interface for selecting which cameras to display in the grid view.
 * Features:
 * - Show/hide individual cameras
 * - Select All / Deselect All
 * - HD/SD quality toggle per camera
 * - Persistence via localStorage
 * - Dynamic grid layout adjustment
 *
 * @module controllers/camera-selector-controller
 */

class CameraSelectorController {
    constructor() {
        // DOM elements
        this.$btn = $('#camera-selector-btn');
        this.$dropdown = $('#camera-selector-dropdown');
        this.$list = $('#camera-selector-list');
        this.$selectAll = $('#select-all-cameras');
        this.$applyBtn = $('#apply-camera-filter');
        this.$countLabel = $('#camera-count-label');

        // State
        this.cameras = [];
        this.isOpen = false;

        // localStorage keys
        this.HIDDEN_CAMERAS_KEY = 'hiddenCameras';
        this.HD_CAMERAS_KEY = 'hdCameras';

        // Initialize
        this._init();
    }

    /**
     * Initialize the controller
     */
    _init() {
        console.log('[CameraSelector] Initializing...');

        // Collect camera info from stream items
        this._collectCameras();

        // Populate the dropdown list
        this._populateList();

        // Restore saved selections
        this._restoreSelections();

        // Setup event listeners
        this._setupEventListeners();

        // Apply initial filter (hide cameras that were hidden in previous session)
        this._applyInitialFilter();

        console.log(`[CameraSelector] Initialized with ${this.cameras.length} cameras`);
    }

    /**
     * Collect camera info from all stream items in the DOM
     */
    _collectCameras() {
        this.cameras = [];
        $('.stream-item').each((_, el) => {
            const $item = $(el);
            this.cameras.push({
                serial: $item.data('camera-serial'),
                name: $item.data('camera-name'),
                type: $item.data('camera-type'),
                streamType: $item.data('stream-type')
            });
        });
    }

    /**
     * Populate the dropdown list with camera checkboxes
     */
    _populateList() {
        this.$list.empty();

        if (this.cameras.length === 0) {
            this.$list.html('<div class="camera-selector-empty">No cameras found</div>');
            return;
        }

        // Sort cameras alphabetically by name
        const sortedCameras = [...this.cameras].sort((a, b) =>
            a.name.localeCompare(b.name)
        );

        sortedCameras.forEach(camera => {
            // Check if camera supports HD toggle (has main/sub quality options)
            // MJPEG and SNAPSHOT streams don't have quality levels
            const supportsHD = this._supportsHDToggle(camera.streamType);

            const $item = $(`
                <div class="camera-item" data-serial="${camera.serial}" data-stream-type="${camera.streamType}">
                    <div class="camera-item-left">
                        <input type="checkbox"
                               id="camera-check-${camera.serial}"
                               class="camera-visibility-check"
                               data-serial="${camera.serial}"
                               checked>
                        <span class="camera-name" title="${camera.name}">${camera.name}</span>
                    </div>
                    ${supportsHD ? `
                    <button class="hd-toggle-btn"
                            data-serial="${camera.serial}"
                            title="Toggle HD quality (main stream)">
                        <span class="hd-label">SD</span>
                    </button>
                    ` : `
                    <span class="stream-type-badge" title="This camera uses ${camera.streamType} - no HD option">
                        ${camera.streamType}
                    </span>
                    `}
                </div>
            `);
            this.$list.append($item);
        });
    }

    /**
     * Check if a stream type supports HD toggle (main/sub quality switching)
     * @param {string} streamType - The stream type
     * @returns {boolean} True if HD toggle is supported
     */
    _supportsHDToggle(streamType) {
        // Stream types that support main/sub quality switching
        const hdSupportedTypes = ['HLS', 'LL_HLS', 'WEBRTC', 'NEOLINK', 'NEOLINK_LL_HLS'];
        return hdSupportedTypes.includes(streamType?.toUpperCase());
    }

    /**
     * Restore selections from localStorage
     */
    _restoreSelections() {
        // Restore hidden cameras
        const hiddenCameras = this._getHiddenCameras();
        hiddenCameras.forEach(serial => {
            $(`#camera-check-${serial}`).prop('checked', false);
            $(`.camera-item[data-serial="${serial}"]`).addClass('camera-hidden');
        });

        // Restore HD selections
        const hdCameras = this._getHDCameras();
        hdCameras.forEach(serial => {
            const $btn = $(`.hd-toggle-btn[data-serial="${serial}"]`);
            $btn.addClass('hd-active');
            $btn.find('.hd-label').text('HD');
        });

        // Update Select All checkbox state
        this._updateSelectAllState();

        // Update count label
        this._updateCountLabel();
    }

    /**
     * Apply initial filter without restarting streams (just hide elements)
     */
    _applyInitialFilter() {
        const hiddenCameras = this._getHiddenCameras();

        // Hide stream items that were hidden in previous session
        hiddenCameras.forEach(serial => {
            $(`.stream-item[data-camera-serial="${serial}"]`).hide();
        });

        // Update grid layout
        const visibleCount = this.cameras.length - hiddenCameras.length;
        this._updateGridLayout(visibleCount);
    }

    /**
     * Setup event listeners
     */
    _setupEventListeners() {
        // Toggle dropdown
        this.$btn.on('click', (e) => {
            e.stopPropagation();
            this._toggleDropdown();
        });

        // Close dropdown when clicking outside
        $(document).on('click', (e) => {
            if (this.isOpen && !$(e.target).closest('.camera-selector-container').length) {
                this._closeDropdown();
            }
        });

        // Close on Escape key
        $(document).on('keydown', (e) => {
            if (e.key === 'Escape' && this.isOpen) {
                this._closeDropdown();
            }
        });

        // Select All checkbox
        this.$selectAll.on('change', (e) => {
            const isChecked = $(e.target).is(':checked');
            this._setAllCameras(isChecked);
        });

        // Individual camera checkbox
        this.$list.on('change', '.camera-visibility-check', (e) => {
            const $checkbox = $(e.target);
            const serial = $checkbox.data('serial');
            const isChecked = $checkbox.is(':checked');

            const $item = $(`.camera-item[data-serial="${serial}"]`);
            $item.toggleClass('camera-hidden', !isChecked);

            this._updateSelectAllState();
        });

        // HD toggle button
        this.$list.on('click', '.hd-toggle-btn', (e) => {
            e.preventDefault();
            const $btn = $(e.currentTarget);
            const serial = $btn.data('serial');

            // Don't toggle if camera is hidden
            if ($btn.closest('.camera-item').hasClass('camera-hidden')) {
                return;
            }

            this._toggleHD(serial, $btn);
        });

        // Apply button
        this.$applyBtn.on('click', () => {
            this._applyFilter();
            this._closeDropdown();
        });
    }

    /**
     * Toggle dropdown open/close
     */
    _toggleDropdown() {
        if (this.isOpen) {
            this._closeDropdown();
        } else {
            this._openDropdown();
        }
    }

    /**
     * Open the dropdown
     */
    _openDropdown() {
        this.$dropdown.show();
        this.$btn.addClass('dropdown-open');
        this.isOpen = true;
    }

    /**
     * Close the dropdown
     */
    _closeDropdown() {
        this.$dropdown.hide();
        this.$btn.removeClass('dropdown-open');
        this.isOpen = false;
    }

    /**
     * Set all cameras to checked or unchecked
     * @param {boolean} checked - Whether to check or uncheck all
     */
    _setAllCameras(checked) {
        this.$list.find('.camera-visibility-check').prop('checked', checked);
        this.$list.find('.camera-item').toggleClass('camera-hidden', !checked);
    }

    /**
     * Update the Select All checkbox based on individual selections
     */
    _updateSelectAllState() {
        const total = this.$list.find('.camera-visibility-check').length;
        const checked = this.$list.find('.camera-visibility-check:checked').length;

        if (checked === total) {
            this.$selectAll.prop('checked', true).prop('indeterminate', false);
        } else if (checked === 0) {
            this.$selectAll.prop('checked', false).prop('indeterminate', false);
        } else {
            this.$selectAll.prop('checked', false).prop('indeterminate', true);
        }
    }

    /**
     * Toggle HD mode for a camera
     * @param {string} serial - Camera serial number
     * @param {jQuery} $btn - HD toggle button element
     */
    _toggleHD(serial, $btn) {
        const isHD = $btn.hasClass('hd-active');

        if (isHD) {
            // Switch to SD
            $btn.removeClass('hd-active');
            $btn.find('.hd-label').text('SD');
            this._removeHDCamera(serial);
        } else {
            // Switch to HD
            $btn.addClass('hd-active');
            $btn.find('.hd-label').text('HD');
            this._addHDCamera(serial);
        }
    }

    /**
     * Apply the current filter selections
     * Shows/hides cameras and restarts streams as needed
     */
    async _applyFilter() {
        console.log('[CameraSelector] Applying filter...');

        const hiddenSerials = [];
        let visibleCount = 0;

        // Collect hidden cameras and update visibility
        this.$list.find('.camera-visibility-check').each((_, el) => {
            const $checkbox = $(el);
            const serial = $checkbox.data('serial');
            const isChecked = $checkbox.is(':checked');

            const $streamItem = $(`.stream-item[data-camera-serial="${serial}"]`);

            if (isChecked) {
                // Show camera
                if ($streamItem.is(':hidden')) {
                    $streamItem.show();
                    // Stream will be restarted by stream manager when it detects visibility
                    this._restartStream(serial);
                }
                visibleCount++;
            } else {
                // Hide camera
                hiddenSerials.push(serial);
                if ($streamItem.is(':visible')) {
                    $streamItem.hide();
                    // Stop stream to save resources
                    this._stopStream(serial);
                }
            }
        });

        // Save hidden cameras to localStorage
        this._saveHiddenCameras(hiddenSerials);

        // Update grid layout
        this._updateGridLayout(visibleCount);

        // Update count label
        this._updateCountLabel();

        // Apply HD settings to visible cameras
        await this._applyHDSettings();

        console.log(`[CameraSelector] Filter applied: ${visibleCount} visible, ${hiddenSerials.length} hidden`);
    }

    /**
     * Apply HD settings to visible cameras
     */
    async _applyHDSettings() {
        const hdCameras = this._getHDCameras();

        for (const serial of hdCameras) {
            const $streamItem = $(`.stream-item[data-camera-serial="${serial}"]`);

            // Only apply HD if camera is visible
            if ($streamItem.is(':visible')) {
                $streamItem.addClass('hd-mode');
                // Stream quality switch will be handled by stream manager
                await this._switchStreamQuality(serial, 'main');
            }
        }

        // Remove HD mode from non-HD cameras
        $('.stream-item').each((_, el) => {
            const $item = $(el);
            const serial = $item.data('camera-serial');
            if (!hdCameras.includes(serial)) {
                $item.removeClass('hd-mode');
            }
        });
    }

    /**
     * Update the grid layout based on visible camera count
     * @param {number} count - Number of visible cameras
     */
    _updateGridLayout(count) {
        let cols;
        if (count === 0) cols = 1;
        else if (count === 1) cols = 1;
        else if (count <= 4) cols = 2;
        else if (count <= 9) cols = 3;
        else if (count <= 16) cols = 4;
        else cols = 5;

        $('#streams-container')
            .removeClass('grid-1 grid-2 grid-3 grid-4 grid-5')
            .addClass(`grid-${cols}`);

        console.log(`[CameraSelector] Grid layout updated: ${cols} columns for ${count} cameras`);
    }

    /**
     * Update the count label in the button
     */
    _updateCountLabel() {
        const total = this.cameras.length;
        const visible = total - this._getHiddenCameras().length;

        if (visible === total) {
            this.$countLabel.text('All');
        } else {
            this.$countLabel.text(`${visible}/${total}`);
        }
    }

    // =========================================================================
    // Stream Management (calls into global stream manager if available)
    // =========================================================================

    /**
     * Stop a stream by serial
     * @param {string} serial - Camera serial number
     */
    _stopStream(serial) {
        // Try to access the global stream manager
        if (window.streamManager) {
            try {
                window.streamManager.hlsManager?.stopStream(serial);
                window.streamManager.webrtcManager?.stopStream(serial);
                console.log(`[CameraSelector] Stopped stream for ${serial}`);
            } catch (e) {
                console.warn(`[CameraSelector] Could not stop stream for ${serial}:`, e);
            }
        }
    }

    /**
     * Restart a stream by serial
     * @param {string} serial - Camera serial number
     */
    _restartStream(serial) {
        // Stream manager will handle restart when it processes visible items
        // For now, trigger a custom event that stream.js can listen to
        const $streamItem = $(`.stream-item[data-camera-serial="${serial}"]`);
        $streamItem.trigger('camera-selector:show');
        console.log(`[CameraSelector] Triggered stream restart for ${serial}`);
    }

    /**
     * Switch stream quality
     * @param {string} serial - Camera serial number
     * @param {string} quality - 'main' or 'sub'
     */
    async _switchStreamQuality(serial, quality) {
        const $streamItem = $(`.stream-item[data-camera-serial="${serial}"]`);
        $streamItem.trigger('camera-selector:quality-change', { quality });
        console.log(`[CameraSelector] Triggered quality change for ${serial}: ${quality}`);
    }

    // =========================================================================
    // localStorage Helpers
    // =========================================================================

    /**
     * Get hidden cameras from localStorage
     * @returns {string[]} Array of hidden camera serials
     */
    _getHiddenCameras() {
        try {
            const stored = localStorage.getItem(this.HIDDEN_CAMERAS_KEY);
            return stored ? JSON.parse(stored) : [];
        } catch (e) {
            console.error('[CameraSelector] Error reading hidden cameras:', e);
            return [];
        }
    }

    /**
     * Save hidden cameras to localStorage
     * @param {string[]} serials - Array of camera serials to hide
     */
    _saveHiddenCameras(serials) {
        try {
            localStorage.setItem(this.HIDDEN_CAMERAS_KEY, JSON.stringify(serials));
        } catch (e) {
            console.error('[CameraSelector] Error saving hidden cameras:', e);
        }
    }

    /**
     * Get HD-enabled cameras from localStorage
     * @returns {string[]} Array of HD camera serials
     */
    _getHDCameras() {
        try {
            const stored = localStorage.getItem(this.HD_CAMERAS_KEY);
            return stored ? JSON.parse(stored) : [];
        } catch (e) {
            console.error('[CameraSelector] Error reading HD cameras:', e);
            return [];
        }
    }

    /**
     * Add a camera to HD list
     * @param {string} serial - Camera serial number
     */
    _addHDCamera(serial) {
        const hdCameras = this._getHDCameras();
        if (!hdCameras.includes(serial)) {
            hdCameras.push(serial);
            this._saveHDCameras(hdCameras);
        }
    }

    /**
     * Remove a camera from HD list
     * @param {string} serial - Camera serial number
     */
    _removeHDCamera(serial) {
        const hdCameras = this._getHDCameras().filter(s => s !== serial);
        this._saveHDCameras(hdCameras);
    }

    /**
     * Save HD cameras to localStorage
     * @param {string[]} serials - Array of HD camera serials
     */
    _saveHDCameras(serials) {
        try {
            localStorage.setItem(this.HD_CAMERAS_KEY, JSON.stringify(serials));
        } catch (e) {
            console.error('[CameraSelector] Error saving HD cameras:', e);
        }
    }
}

// Initialize when DOM is ready
$(document).ready(() => {
    // Wait a short moment for stream items to be fully rendered
    setTimeout(() => {
        window.cameraSelectorController = new CameraSelectorController();
    }, 100);
});

export default CameraSelectorController;
