/**
 * Camera Selector Controller
 *
 * Provides a dropdown interface for selecting which cameras to display in the grid view.
 * Features:
 * - Show/hide individual cameras
 * - Select All / Deselect All
 * - HD/SD quality toggle per camera
 * - Persistence via per-user database (with localStorage cache for fast load)
 * - Dynamic grid layout adjustment
 *
 * @module controllers/camera-selector-controller
 */

class CameraSelectorController {
    constructor() {
        // DOM elements
        this.$btn = $('#camera-selector-btn');
        this.$backdrop = $('#camera-selector-backdrop');
        this.$modal = $('#camera-selector-modal');
        this.$closeBtn = $('#camera-selector-close');
        this.$list = $('#camera-selector-list');
        this.$selectAll = $('#select-all-cameras');
        this.$applyBtn = $('#apply-camera-filter');
        this.$countLabel = $('#camera-count-label');

        // State
        this.cameras = [];
        this.isOpen = false;

        // In-memory cache (loaded from server, cached in localStorage for fast page load)
        this._hiddenCameras = [];
        this._hdCameras = [];
        this._prefsLoaded = false;

        // localStorage keys (cache only - server is source of truth)
        this.HIDDEN_CAMERAS_KEY = 'hiddenCameras';
        this.HD_CAMERAS_KEY = 'hdCameras';

        // Initialize
        this._init();
    }

    /**
     * Initialize the controller.
     * Loads preferences from localStorage cache first (instant), then
     * fetches from server to get authoritative per-user preferences.
     */
    _init() {
        console.log('[CameraSelector] Initializing...');

        // Collect camera info from stream items
        this._collectCameras();

        // Load from localStorage cache for instant rendering
        this._loadFromCache();

        // Populate the dropdown list
        this._populateList();

        // Restore saved selections (from cache)
        this._restoreSelections();

        // Setup event listeners
        this._setupEventListeners();

        // Apply initial filter (hide cameras that were hidden in previous session)
        this._applyInitialFilter();

        // Then fetch from server (source of truth) and re-apply if different
        this._loadFromServer();

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
                streamType: $item.data('stream-type'),
                serverHidden: $item.hasClass('server-hidden')
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
            const hiddenBadge = camera.serverHidden
                ? '<span class="server-hidden-badge" title="Hidden in DB (Advanced settings)">hidden</span>'
                : '';

            const $item = $(`
                <div class="camera-item${camera.serverHidden ? ' server-hidden-item' : ''}" data-serial="${camera.serial}" data-stream-type="${camera.streamType}">
                    <div class="camera-item-left">
                        <input type="checkbox"
                               id="camera-check-${camera.serial}"
                               class="camera-visibility-check"
                               data-serial="${camera.serial}"
                               checked>
                        <span class="camera-name" title="${camera.name}">${camera.name}</span>
                        ${hiddenBadge}
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

        // Server-hidden cameras don't count toward visible grid
        const serverHiddenCount = this.cameras.filter(c => c.serverHidden).length;
        const visibleCount = this.cameras.length - hiddenCameras.length - serverHiddenCount;
        this._updateGridLayout(visibleCount);
    }

    /**
     * Setup event listeners
     */
    _setupEventListeners() {
        // Toggle modal
        this.$btn.on('click', (e) => {
            e.stopPropagation();
            this._toggleModal();
        });

        // Close modal on backdrop click
        this.$backdrop.on('click', () => {
            this._closeModal();
        });

        // Close modal on close button click
        this.$closeBtn.on('click', () => {
            this._closeModal();
        });

        // Close on Escape key
        $(document).on('keydown', (e) => {
            if (e.key === 'Escape' && this.isOpen) {
                this._closeModal();
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

        // Show Hidden toggle — reveals server-hidden cameras (dimmed) in the grid
        // Persisted in localStorage as user preference
        const showHiddenStored = localStorage.getItem('showHiddenCameras') === 'true';
        $('#show-hidden-cameras-toggle').prop('checked', showHiddenStored);
        if (showHiddenStored) {
            $('#streams-container').addClass('show-hidden-cameras');
        }

        $('#show-hidden-cameras-toggle').on('change', (e) => {
            const show = $(e.target).is(':checked');
            localStorage.setItem('showHiddenCameras', show ? 'true' : 'false');
            $('#streams-container').toggleClass('show-hidden-cameras', show);
            // Recalculate grid layout to include/exclude hidden cameras
            this._updateGridLayoutForShowHidden(show);
        });

        // Apply button
        this.$applyBtn.on('click', () => {
            this._applyFilter();
            this._closeModal();
        });
    }

    /**
     * Toggle modal open/close
     */
    _toggleModal() {
        if (this.isOpen) {
            this._closeModal();
        } else {
            this._openModal();
        }
    }

    /**
     * Open the modal
     */
    _openModal() {
        this.$backdrop.addClass('visible');
        this.$modal.addClass('visible');
        this.$btn.addClass('modal-open');
        this.isOpen = true;
        // Prevent body scroll when modal is open
        $('body').css('overflow', 'hidden');
    }

    /**
     * Close the modal
     */
    _closeModal() {
        this.$backdrop.removeClass('visible');
        this.$modal.removeClass('visible');
        this.$btn.removeClass('modal-open');
        this.isOpen = false;
        // Restore body scroll
        $('body').css('overflow', '');
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

        // Stretch last-row items to fill remaining columns.
        // e.g., 5 cols, 17 items → last row has 2 items → span 3 + span 2 = 5.
        this._stretchLastRow(count, cols);

        console.log(`[CameraSelector] Grid layout updated: ${cols} columns for ${count} cameras`);
    }

    /**
     * Make last-row items span extra columns so the row fills the full grid width.
     * Uses integer spans distributed as evenly as possible:
     *   baseSpan  = floor(cols / lastRowCount)
     *   remainder = cols % lastRowCount
     *   first `remainder` items get (baseSpan + 1), rest get baseSpan.
     *
     * @param {number} count - Total visible camera count
     * @param {number} cols  - Number of grid columns
     */
    _stretchLastRow(count, cols) {
        // Reset any previous spans on all visible stream items
        const $items = $('#streams-container .stream-item:visible');
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

        console.log(`[CameraSelector] Last row: ${lastRowCount} item(s), cols=${cols}, spans distributed`);
    }

    /**
     * Recalculate grid layout when show-hidden toggle changes.
     * When showing hidden cameras, they count toward the grid column calculation.
     * @param {boolean} showHidden - Whether hidden cameras are being shown
     */
    _updateGridLayoutForShowHidden(showHidden) {
        const hiddenCameras = this._getHiddenCameras();
        const serverHiddenCount = $('.stream-item.server-hidden').length;
        const totalVisible = this.cameras.length - hiddenCameras.length;
        const effectiveCount = showHidden ? totalVisible + serverHiddenCount : totalVisible;
        this._updateGridLayout(effectiveCount);
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
    // Preference Persistence (server = source of truth, localStorage = cache)
    // =========================================================================

    /**
     * Load preferences from localStorage cache (fast, for initial render)
     */
    _loadFromCache() {
        try {
            const hidden = localStorage.getItem(this.HIDDEN_CAMERAS_KEY);
            this._hiddenCameras = hidden ? JSON.parse(hidden) : [];
        } catch (e) {
            this._hiddenCameras = [];
        }
        try {
            const hd = localStorage.getItem(this.HD_CAMERAS_KEY);
            this._hdCameras = hd ? JSON.parse(hd) : [];
        } catch (e) {
            this._hdCameras = [];
        }
    }

    /**
     * Load preferences from server (authoritative per-user data).
     * If server data differs from cache, re-apply filter.
     */
    async _loadFromServer() {
        try {
            const response = await fetch('/api/my-preferences');
            if (!response.ok) {
                console.warn('[CameraSelector] Failed to load preferences from server');
                return;
            }

            const prefs = await response.json();
            const serverHidden = prefs.hidden_cameras || [];
            const serverHD = prefs.hd_cameras || [];

            // Check if server data differs from cache
            const hiddenChanged = JSON.stringify(serverHidden.sort()) !== JSON.stringify(this._hiddenCameras.sort());
            const hdChanged = JSON.stringify(serverHD.sort()) !== JSON.stringify(this._hdCameras.sort());

            if (hiddenChanged || hdChanged) {
                console.log('[CameraSelector] Server preferences differ from cache, re-applying...');
                this._hiddenCameras = serverHidden;
                this._hdCameras = serverHD;

                // Update cache
                this._updateCache();

                // Re-apply UI
                this._restoreSelections();
                this._applyInitialFilter();
            }

            this._prefsLoaded = true;
        } catch (e) {
            console.warn('[CameraSelector] Error loading preferences from server:', e);
        }
    }

    /**
     * Save preferences to server and update localStorage cache.
     * Called after applying filter or toggling HD.
     */
    async _saveToServer() {
        // Update cache immediately for responsiveness
        this._updateCache();

        try {
            await fetch('/api/my-preferences', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    hidden_cameras: this._hiddenCameras,
                    hd_cameras: this._hdCameras
                })
            });
        } catch (e) {
            console.warn('[CameraSelector] Error saving preferences to server:', e);
        }
    }

    /**
     * Update localStorage cache from in-memory state
     */
    _updateCache() {
        try {
            localStorage.setItem(this.HIDDEN_CAMERAS_KEY, JSON.stringify(this._hiddenCameras));
            localStorage.setItem(this.HD_CAMERAS_KEY, JSON.stringify(this._hdCameras));
        } catch (e) {
            // localStorage might be full or unavailable - non-critical
        }
    }

    /**
     * Get hidden cameras from in-memory state
     * @returns {string[]} Array of hidden camera serials
     */
    _getHiddenCameras() {
        return this._hiddenCameras;
    }

    /**
     * Save hidden cameras to in-memory state (server save deferred to _applyFilter)
     * @param {string[]} serials - Array of camera serials to hide
     */
    _saveHiddenCameras(serials) {
        this._hiddenCameras = serials;
        // Server save happens in _applyFilter after all changes are collected
        this._saveToServer();
    }

    /**
     * Get HD-enabled cameras from in-memory state
     * @returns {string[]} Array of HD camera serials
     */
    _getHDCameras() {
        return this._hdCameras;
    }

    /**
     * Add a camera to HD list and persist
     * @param {string} serial - Camera serial number
     */
    _addHDCamera(serial) {
        if (!this._hdCameras.includes(serial)) {
            this._hdCameras.push(serial);
            this._saveToServer();
        }
    }

    /**
     * Remove a camera from HD list and persist
     * @param {string} serial - Camera serial number
     */
    _removeHDCamera(serial) {
        this._hdCameras = this._hdCameras.filter(s => s !== serial);
        this._saveToServer();
    }
}

// Initialize when DOM is ready
$(document).ready(() => {
    // Wait for stream items to be fully rendered
    // Try multiple times with increasing delays if needed
    let attempts = 0;
    const maxAttempts = 10;

    const tryInit = () => {
        attempts++;
        const streamItemCount = $('.stream-item').length;

        if (streamItemCount > 0) {
            // Stream items found - initialize controller
            console.log(`[CameraSelector] Found ${streamItemCount} stream items, initializing...`);
            window.cameraSelectorController = new CameraSelectorController();
        } else if (attempts < maxAttempts) {
            // No stream items yet - retry with longer delay
            console.log(`[CameraSelector] No stream items found (attempt ${attempts}/${maxAttempts}), retrying...`);
            setTimeout(tryInit, 200 * attempts); // Exponential backoff
        } else {
            console.error('[CameraSelector] Failed to find stream items after', maxAttempts, 'attempts');
        }
    };

    // Start first attempt after 200ms
    setTimeout(tryInit, 200);
});

export default CameraSelectorController;
