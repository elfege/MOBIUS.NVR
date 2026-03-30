/**
 * PTZ Controller Module - ES6 + jQuery
 * Handles PTZ camera movement controls
 *
 * Includes digital zoom integration:
 * - Optical zoom via ONVIF/Baichuan (primary)
 * - Digital zoom via CSS transforms when optical reaches limit
 * - Timeout-based detection for optical zoom limit
 */

import { digitalZoomManager } from '../utils/digital-zoom.js';

export class PTZController {
    constructor() {

        this.currentCamera = null;
        this.bridgeReady = false;
        this.isExecuting = false;
        this.executingPreset = false; // Track when executing a preset (don't interrupt with stop)
        this.presets = {}; // Changed to object to store presets per camera: {serial: [presets]}
        this.ptzTouchActive = false;
        this.activeDirection = null;
        this.repeatInterval = null; // Legacy, kept for safety
        this.moveAcknowledged = true; // Track when camera has processed a move command
        this.moveStartTime = null; // Track when move command was sent
        this.latencyCache = {}; // In-memory cache of learned latencies per camera
        this.reversePanCache = {}; // Per-camera reverse pan preference

        // Digital zoom state tracking (for optical→digital handoff)
        this.opticalZoomTimeout = null;           // Timer to detect optical zoom limit
        this.opticalZoomLimitMs = 500;            // Time to wait before assuming optical limit
        this.digitalZoomMode = new Map();         // Track which cameras are in digital zoom mode
        this.digitalZoomInterval = null;          // Interval for continuous digital zoom
        this.digitalZoomIntervalMs = 150;         // How often to zoom when holding button

        // PTZ uses ONVIF ContinuousMove - one command starts movement,
        // camera keeps moving until a Stop command is sent.
        // Latency is learned per-camera and stored in PostgreSQL via API.

        // Get or create client UUID for latency tracking
        this.clientUuid = this.getOrCreateClientUuid();


        this.setupEventListeners();
        this.setupPresetListeners();
        this.setupPresetManagementListeners();
        this.setupReversePanListeners();
        this.setupDigitalZoomListeners();
        this.updateButtonStates();

        // Show preset dropdowns immediately (for debugging)
        this.updatePresetUI();

        // Load presets for all PTZ cameras on page load
        this.loadPresetsForAllCameras();

        // Load reverse pan settings for all PTZ cameras
        this.loadReversePanSettingsForAllCameras();

        // Check Eufy cloud status for PTZ-capable Eufy cameras
        this._checkEufyCloudForPTZ();

        // Initialize digital zoom for all cameras (works for any camera, not just PTZ)
        this.initializeDigitalZoomForAllCameras();


        console.log("#######################################")
        console.log('########### PTZ controller initialized');
        console.log("#######################################")
    }

    /**
     * Get or create a unique client UUID for latency tracking.
     * Stored in localStorage to persist across sessions.
     * @returns {string} Client UUID
     */
    getOrCreateClientUuid() {
        const key = 'nvr_client_uuid';
        let uuid = localStorage.getItem(key);
        if (!uuid) {
            // Generate UUID v4
            uuid = 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
                const r = Math.random() * 16 | 0;
                const v = c === 'x' ? r : (r & 0x3 | 0x8);
                return v.toString(16);
            });
            localStorage.setItem(key, uuid);
            console.log(`[PTZ] Created new client UUID: ${uuid}`);
        }
        return uuid;
    }

    /**
     * Get learned latency for a camera (returns milliseconds).
     * Uses in-memory cache first, falls back to API.
     * @param {string} serial - Camera serial number
     * @returns {number} Latency in milliseconds with 20% safety margin
     */
    getCameraLatency(serial) {
        // Check in-memory cache first
        if (this.latencyCache[serial]) {
            // Return cached value with 20% safety margin
            return Math.round(this.latencyCache[serial] * 1.2);
        }
        return 1000; // Default 1 second if no cached data
    }

    /**
     * Load latency data from API for a camera.
     * Called when camera is selected.
     * @param {string} serial - Camera serial number
     */
    async loadCameraLatency(serial) {
        try {
            const response = await fetch(`/api/ptz/latency/${this.clientUuid}/${serial}`);
            const data = await response.json();
            if (data.success && data.avg_latency_ms) {
                this.latencyCache[serial] = data.avg_latency_ms;
                console.log(`[PTZ] Loaded latency for ${serial}: ${data.avg_latency_ms}ms (samples: ${data.sample_count})`);
            }
        } catch (e) {
            console.warn(`[PTZ] Failed to load latency from API for ${serial}:`, e);
        }
    }

    /**
     * Update learned latency for a camera based on observed response time.
     * Sends to API for persistent storage in PostgreSQL.
     * @param {string} serial - Camera serial number
     * @param {number} observedLatency - Observed latency in milliseconds
     */
    updateCameraLatency(serial, observedLatency) {
        // Update local cache immediately for responsiveness
        if (this.latencyCache[serial]) {
            // Simple running average update
            this.latencyCache[serial] = Math.round(
                (this.latencyCache[serial] * 0.8) + (observedLatency * 0.2)
            );
        } else {
            this.latencyCache[serial] = observedLatency;
        }

        // Send to API asynchronously (fire-and-forget)
        fetch(`/api/ptz/latency/${this.clientUuid}/${serial}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ observed_latency_ms: observedLatency })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                // Update cache with server-calculated average
                this.latencyCache[serial] = data.avg_latency_ms;
                console.log(`[PTZ] Updated latency for ${serial}: ${data.avg_latency_ms}ms (last: ${observedLatency}ms, samples: ${data.sample_count})`);
            } else {
                console.warn(`[PTZ] Failed to update latency: ${data.error}`);
            }
        })
        .catch(e => {
            console.warn(`[PTZ] Failed to save latency to API for ${serial}:`, e);
        });
    }

    // =========================================================================
    // PTZ Reversal Methods (stored in cameras.json via API)
    // For cameras mounted upside down where controls are reversed
    // =========================================================================

    /**
     * Check if reverse pan is enabled for a camera.
     * Uses in-memory cache populated from API on load.
     * @param {string} serial - Camera serial number
     * @returns {boolean} True if reverse pan is enabled
     */
    isReversePanEnabled(serial) {
        if (this.reversePanCache.hasOwnProperty(serial)) {
            return this.reversePanCache[serial].reversed_pan || false;
        }
        return false;
    }

    /**
     * Check if reverse tilt is enabled for a camera.
     * Uses in-memory cache populated from API on load.
     * @param {string} serial - Camera serial number
     * @returns {boolean} True if reverse tilt is enabled
     */
    isReverseTiltEnabled(serial) {
        if (this.reversePanCache.hasOwnProperty(serial)) {
            return this.reversePanCache[serial].reversed_tilt || false;
        }
        return false;
    }

    /**
     * Load PTZ reversal settings from API for a camera.
     * @param {string} serial - Camera serial number
     */
    async loadReversalSettings(serial) {
        try {
            const response = await fetch(`/api/ptz/${serial}/reversal`);
            const data = await response.json();
            if (data.success) {
                this.reversePanCache[serial] = {
                    reversed_pan: data.reversed_pan,
                    reversed_tilt: data.reversed_tilt
                };
                console.log(`[PTZ] Loaded reversal for ${serial}: pan=${data.reversed_pan}, tilt=${data.reversed_tilt}`);
            }
        } catch (e) {
            console.warn(`[PTZ] Failed to load reversal settings for ${serial}:`, e);
        }
    }

    /**
     * Update PTZ reversal settings via API.
     * Uses optimistic update - cache is updated immediately, API call is fire-and-forget.
     * @param {string} serial - Camera serial number
     * @param {boolean|null} reversedPan - Reverse pan setting (null to skip)
     * @param {boolean|null} reversedTilt - Reverse tilt setting (null to skip)
     */
    async updateReversalSettings(serial, reversedPan = null, reversedTilt = null) {
        // Optimistic update - apply immediately so reversal works without waiting for API
        if (!this.reversePanCache[serial]) {
            this.reversePanCache[serial] = { reversed_pan: false, reversed_tilt: false };
        }
        if (reversedPan !== null) {
            this.reversePanCache[serial].reversed_pan = reversedPan;
        }
        if (reversedTilt !== null) {
            this.reversePanCache[serial].reversed_tilt = reversedTilt;
        }
        console.log(`[PTZ] Reversal cache updated for ${serial}: pan=${this.reversePanCache[serial].reversed_pan}, tilt=${this.reversePanCache[serial].reversed_tilt}`);

        // Fire-and-forget API call for persistence (non-blocking)
        const payload = {};
        if (reversedPan !== null) payload.reversed_pan = reversedPan;
        if (reversedTilt !== null) payload.reversed_tilt = reversedTilt;

        fetch(`/api/ptz/${serial}/reversal`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                console.log(`[PTZ] Reversal persisted for ${serial}`);
            } else {
                console.warn(`[PTZ] Failed to persist reversal: ${data.error}`);
            }
        })
        .catch(e => {
            console.warn(`[PTZ] Error persisting reversal settings for ${serial}:`, e);
        });
    }

    /**
     * Apply PTZ reversal correction to direction if enabled.
     * Reverses left/right for pan, up/down for tilt.
     * @param {string} serial - Camera serial number
     * @param {string} direction - Original direction (left, right, up, down, etc.)
     * @returns {string} Corrected direction
     */
    applyReversal(serial, direction) {
        const reversePan = this.isReversePanEnabled(serial);
        const reverseTilt = this.isReverseTiltEnabled(serial);

        // Reverse left/right if pan is reversed
        if (reversePan) {
            if (direction === 'left') {
                console.log(`[PTZ] Reverse pan: left → right for ${serial}`);
                return 'right';
            } else if (direction === 'right') {
                console.log(`[PTZ] Reverse pan: right → left for ${serial}`);
                return 'left';
            }
        }

        // Reverse up/down if tilt is reversed
        if (reverseTilt) {
            if (direction === 'up') {
                console.log(`[PTZ] Reverse tilt: up → down for ${serial}`);
                return 'down';
            } else if (direction === 'down') {
                console.log(`[PTZ] Reverse tilt: down → up for ${serial}`);
                return 'up';
            }
        }

        return direction;
    }

    // =========================================================================
    // Digital Zoom Methods
    // Provides client-side zoom when optical zoom reaches limit
    // =========================================================================

    /**
     * Initialize digital zoom for a camera's stream element.
     * Called when stream is started or camera is selected.
     * @param {string} serial - Camera serial number
     */
    initializeDigitalZoom(serial) {
        // Find the video/img element for this camera
        const $streamItem = $(`.stream-item[data-camera-serial="${serial}"]`);
        const $streamVideo = $streamItem.find('.stream-video');

        if ($streamVideo.length) {
            digitalZoomManager.initializeForCamera(serial, $streamVideo[0]);
            this.digitalZoomMode.set(serial, false); // Not in digital zoom mode yet
            console.log(`[PTZ] Digital zoom initialized for ${serial}`);
        }
    }

    /**
     * Initialize digital zoom for all cameras on page load.
     * Digital zoom works for ALL cameras (even non-PTZ ones).
     */
    initializeDigitalZoomForAllCameras() {
        // Delay slightly to allow streams to initialize first
        setTimeout(() => {
            $('.stream-item').each((index, streamItem) => {
                const $streamItem = $(streamItem);
                const serial = $streamItem.data('camera-serial');
                const $streamVideo = $streamItem.find('.stream-video');

                if (serial && $streamVideo.length) {
                    // Initialize digital zoom (idempotent - safe to call multiple times)
                    digitalZoomManager.initializeForCamera(serial, $streamVideo[0]);
                    this.digitalZoomMode.set(serial, false);
                }
            });
            console.log('[PTZ] Digital zoom initialized for all cameras');
        }, 1000);
    }

    /**
     * Handle zoom-in action with optical→digital handoff.
     * Tries optical zoom first, switches to digital after timeout.
     * Supports continuous digital zoom while button is held.
     * @param {string} serial - Camera serial number
     */
    async handleZoomIn(serial) {
        // Clear any existing digital zoom interval
        this.clearDigitalZoomInterval();

        // Check if already in digital zoom mode
        if (this.digitalZoomMode.get(serial)) {
            // Already using digital zoom - start continuous digital zoom
            console.log(`[PTZ] ${serial}: Starting continuous digital zoom`);
            this.startContinuousDigitalZoom(serial, 'in');
            return;
        }

        // Start optical zoom
        this.startMovement('zoom-in');

        // Set timeout to detect optical zoom limit
        // If no change detected within timeout, assume optical limit and switch to digital
        this.clearOpticalZoomTimeout();
        this.opticalZoomTimeout = setTimeout(() => {
            console.log(`[PTZ] ${serial}: Optical zoom timeout - switching to digital`);
            this.digitalZoomMode.set(serial, true);

            // Stop optical zoom (don't clear the timeout from inside the timeout callback)
            this.ptzTouchActive = false;
            this.activeDirection = null;
            this.isExecuting = false;
            $('.ptz-btn').removeClass('active');

            // Apply first digital zoom step
            digitalZoomManager.zoomIn(serial);
            this.updateDigitalZoomUI(serial);

            // Start continuous digital zoom interval
            this.startContinuousDigitalZoom(serial, 'in');

            this.showFeedback('Optical max - using digital zoom', 'info');
        }, this.opticalZoomLimitMs);
    }

    /**
     * Start continuous digital zoom (repeating interval while button held).
     * @param {string} serial - Camera serial number
     * @param {string} direction - 'in' or 'out'
     */
    startContinuousDigitalZoom(serial, direction) {
        this.clearDigitalZoomInterval();

        // Apply first zoom step immediately
        const zoomed = direction === 'in'
            ? digitalZoomManager.zoomIn(serial)
            : digitalZoomManager.zoomOut(serial);

        if (zoomed) {
            this.updateDigitalZoomUI(serial);
        }

        // Set up repeating interval for continuous zoom
        this.digitalZoomInterval = setInterval(() => {
            const success = direction === 'in'
                ? digitalZoomManager.zoomIn(serial)
                : digitalZoomManager.zoomOut(serial);

            if (success) {
                this.updateDigitalZoomUI(serial);
            } else {
                // Hit min/max, stop the interval
                this.clearDigitalZoomInterval();
                const msg = direction === 'in' ? 'Max digital zoom' : 'Min zoom reached';
                this.showFeedback(msg, 'info');
            }

            // Exit digital mode if back to 1.0x
            if (!digitalZoomManager.isZoomed(serial)) {
                this.digitalZoomMode.set(serial, false);
            }
        }, this.digitalZoomIntervalMs);
    }

    /**
     * Clear digital zoom interval.
     */
    clearDigitalZoomInterval() {
        if (this.digitalZoomInterval) {
            clearInterval(this.digitalZoomInterval);
            this.digitalZoomInterval = null;
        }
    }

    /**
     * Handle zoom-out action with digital→optical handoff.
     * If digitally zoomed, zoom out digitally first (continuous while held).
     * @param {string} serial - Camera serial number
     */
    async handleZoomOut(serial) {
        // Clear any existing digital zoom interval
        this.clearDigitalZoomInterval();

        // If digitally zoomed, zoom out digitally first (with continuous support)
        if (digitalZoomManager.isZoomed(serial)) {
            console.log(`[PTZ] ${serial}: Starting continuous digital zoom out`);
            this.startContinuousDigitalZoom(serial, 'out');
            return;
        }

        // Not digitally zoomed - use optical zoom
        this.startMovement('zoom-out');

        // No timeout needed for zoom-out (no limit detection)
    }

    /**
     * Clear optical zoom timeout timer.
     */
    clearOpticalZoomTimeout() {
        if (this.opticalZoomTimeout) {
            clearTimeout(this.opticalZoomTimeout);
            this.opticalZoomTimeout = null;
        }
    }

    /**
     * Reset digital zoom for a camera.
     * Called from UI reset button or when switching cameras.
     * @param {string} serial - Camera serial number
     */
    resetDigitalZoom(serial) {
        digitalZoomManager.resetZoom(serial);
        this.digitalZoomMode.set(serial, false);
        this.updateDigitalZoomUI(serial);
        console.log(`[PTZ] ${serial}: Digital zoom reset`);
    }

    /**
     * Update UI elements for digital zoom state (badge, class).
     * @param {string} serial - Camera serial number
     */
    updateDigitalZoomUI(serial) {
        const $streamItem = $(`.stream-item[data-camera-serial="${serial}"]`);
        const level = digitalZoomManager.getZoomLevel(serial);
        const isZoomed = level > 1.0;

        // Toggle class for CSS styling
        $streamItem.toggleClass('digitally-zoomed', isZoomed);

        // Update or create zoom badge
        let $badge = $streamItem.find('.digital-zoom-badge');
        if (isZoomed) {
            if (!$badge.length) {
                $badge = $('<div class="digital-zoom-badge"></div>');
                $streamItem.append($badge);
            }
            $badge.text(`${level.toFixed(1)}x`);

            // Update badge color based on zoom level
            $badge.removeClass('zoom-low zoom-medium zoom-high');
            if (level <= 2.5) {
                $badge.addClass('zoom-low');
            } else if (level <= 5.0) {
                $badge.addClass('zoom-medium');
            } else {
                $badge.addClass('zoom-high');
            }
        } else {
            $badge.remove();
        }
    }

    /**
     * Load PTZ reversal settings for all PTZ cameras on page load.
     * Fetches from API and updates checkbox states.
     */
    loadReversePanSettingsForAllCameras() {
        let delay = 0;
        const staggerMs = 200; // Stagger requests to avoid overwhelming server

        $('.stream-item').each((index, streamItem) => {
            const $streamItem = $(streamItem);
            const serial = $streamItem.data('camera-serial');
            const $ptzControls = $streamItem.find('.ptz-controls');

            // Only load for cameras with PTZ controls
            if (serial && $ptzControls.length > 0) {
                setTimeout(async () => {
                    await this.loadReversalSettings(serial);

                    // Update checkboxes with loaded values
                    const $panCheckbox = $streamItem.find('.ptz-reverse-pan-checkbox');
                    const $tiltCheckbox = $streamItem.find('.ptz-reverse-tilt-checkbox');

                    if ($panCheckbox.length) {
                        $panCheckbox.prop('checked', this.isReversePanEnabled(serial));
                    }
                    if ($tiltCheckbox.length) {
                        $tiltCheckbox.prop('checked', this.isReverseTiltEnabled(serial));
                    }
                }, delay);

                delay += staggerMs;
            }
        });
    }

    /**
     * Check Eufy cloud status and update PTZ cloud indicators.
     * Eufy PTZ commands require P2P (cloud), but presets use local cache.
     */
    async _checkEufyCloudForPTZ() {
        const $eufyIndicators = $('.ptz-cloud-status[data-camera-type="eufy"]');
        if ($eufyIndicators.length === 0) return;

        try {
            const resp = await fetch('/api/eufy/cloud-status');
            const data = await resp.json();

            $eufyIndicators.each(function () {
                const $el = $(this);
                const $text = $el.find('.ptz-cloud-text');
                $el.show();

                if (data.p2p_available) {
                    $el.css({ background: 'rgba(39, 174, 96, 0.3)', color: '#27ae60' });
                    $el.find('i').removeClass().addClass('fas fa-cloud');
                    $text.text('Cloud OK');
                    $el.attr('title', 'Eufy cloud connected — PTZ commands available');
                } else {
                    $el.css({ background: 'rgba(231, 76, 60, 0.3)', color: '#e74c3c' });
                    $el.find('i').removeClass().addClass('fas fa-cloud-slash');
                    $text.text('Cloud down — PTZ unavailable');
                    $el.attr('title', data.message || 'Eufy cloud unreachable — PTZ commands need P2P. Presets still work (cached).');
                }
            });
        } catch (err) {
            console.warn('[PTZ] Cloud status check failed:', err);
        }

        // Re-check every 60 seconds
        setTimeout(() => this._checkEufyCloudForPTZ(), 60000);
    }

    /**
     * Setup event listeners for reversal checkboxes.
     */
    setupReversePanListeners() {
        // Reverse Pan checkbox
        $(document).on('change', '.ptz-reverse-pan-checkbox', async (event) => {
            const $checkbox = $(event.currentTarget);
            const $streamItem = $checkbox.closest('.stream-item');
            const serial = $streamItem.data('camera-serial');

            if (serial) {
                const enabled = $checkbox.is(':checked');
                await this.updateReversalSettings(serial, enabled, null);
            }
        });

        // Reverse Tilt checkbox
        $(document).on('change', '.ptz-reverse-tilt-checkbox', async (event) => {
            const $checkbox = $(event.currentTarget);
            const $streamItem = $checkbox.closest('.stream-item');
            const serial = $streamItem.data('camera-serial');

            if (serial) {
                const enabled = $checkbox.is(':checked');
                await this.updateReversalSettings(serial, null, enabled);
            }
        });
    }

    /**
     * Setup event listeners for digital zoom UI elements.
     */
    setupDigitalZoomListeners() {
        // Digital zoom reset button
        $(document).on('click', '.stream-digital-zoom-reset-btn', (event) => {
            event.preventDefault();
            event.stopPropagation();

            const $streamItem = $(event.currentTarget).closest('.stream-item');
            const serial = $streamItem.data('camera-serial');

            if (serial) {
                this.resetDigitalZoom(serial);
                this.showFeedback('Digital zoom reset', 'info');
            }
        });

        // Double-click on stream to reset digital zoom (convenience)
        $(document).on('dblclick', '.stream-video', (event) => {
            const $streamItem = $(event.currentTarget).closest('.stream-item');
            const serial = $streamItem.data('camera-serial');

            // Only reset if digitally zoomed
            if (serial && digitalZoomManager.isZoomed(serial)) {
                event.preventDefault();
                this.resetDigitalZoom(serial);
                this.showFeedback('Digital zoom reset', 'info');
            }
        });

        // Listen for zoom changes from wheel/pinch gestures to update UI
        $(document).on('digitalzoomchange', (event) => {
            const { cameraId, level } = event.detail;
            if (cameraId) {
                this.updateDigitalZoomUI(cameraId);

                // Update digital zoom mode tracking
                if (level > 1.0) {
                    this.digitalZoomMode.set(cameraId, true);
                } else {
                    this.digitalZoomMode.set(cameraId, false);
                }
            }
        });
    }

    setupEventListeners() {
        // Track input type to avoid mouse emulation conflicts on touch
        this.lastInputType = null;

        $(document).on('mousedown touchstart', '.ptz-btn', (event) => {
            event.preventDefault();

            // Track input type
            this.lastInputType = event.type === 'touchstart' ? 'touch' : 'mouse';

            // Always detect camera from button's parent stream-item (grid view)
            const $streamItem = $(event.currentTarget).closest('.stream-item');
            const serial = $streamItem.data('camera-serial');
            const name = $streamItem.data('camera-name');
            if (serial && serial !== this.currentCamera?.serial) {
                this.setCurrentCamera(serial, name);
                this.setBridgeReady(true);
            }

            const direction = $(event.currentTarget).data('direction');
            console.log('[PTZ] Button pressed:', direction, 'type:', this.lastInputType, 'for camera:', this.currentCamera?.serial);

            if (direction === 'stop') {
                // Explicit stop button pressed
                this.stopMovement();
            } else if (direction === 'home') {
                // Home = go to first preset in the list (not ONVIF GotoHomePosition)
                const serial = this.currentCamera?.serial;
                const presets = serial ? this.presets[serial] : null;
                if (presets && presets.length > 0) {
                    const firstPreset = presets[0];
                    this.gotoPreset(firstPreset.token, firstPreset.name || 'Home');
                } else {
                    console.warn('[PTZ] No presets available for home button');
                    this.showFeedback('No presets configured', 'warning');
                }
            } else if (direction === '360' || direction === 'recalibrate') {
                // Discrete commands (360 rotation, recalibration) - single execution
                this.executeMovement(direction);
            } else if (direction === 'zoom-in') {
                // Zoom in with digital zoom handoff support
                this.ptzTouchActive = true;
                this.activeDirection = direction;
                // Use serial from stream-item (already declared above), not currentCamera
                if (serial) {
                    this.handleZoomIn(serial);
                }
            } else if (direction === 'zoom-out') {
                // Zoom out with digital zoom handoff support
                this.ptzTouchActive = true;
                this.activeDirection = direction;
                // Use serial from stream-item (already declared above), not currentCamera
                if (serial) {
                    this.handleZoomOut(serial);
                }
            } else if (direction) {
                // Continuous movement directions (left, right, up, down)
                this.ptzTouchActive = true;
                this.activeDirection = direction;
                this.startMovement(direction);
            }
        });

        // Mouse up anywhere on document (not just on button)
        $(document).on('mouseup', () => {
            // Ignore if this was a touch interaction (avoid emulated mouse events)
            if (this.lastInputType === 'touch') return;

            // Don't interrupt preset execution
            if (this.executingPreset) {
                console.log(`[PTZ ${new Date().toISOString()}] Mouse up - ignoring (preset executing)`);
                return;
            }

            // Always stop if we have an active interval (most reliable check)
            if (this.repeatInterval || this.ptzTouchActive || this.isExecuting) {
                console.log(`[PTZ ${new Date().toISOString()}] Mouse up - stopping. States:`, {
                    repeatInterval: !!this.repeatInterval,
                    ptzTouchActive: this.ptzTouchActive,
                    isExecuting: this.isExecuting
                });
                this.stopMovement();
            }
        });

        // Touch end at document level - ALWAYS stop on any touch release
        // This is aggressive but ensures camera stops when finger lifts
        $(document).on('touchend touchcancel', () => {
            // Don't interrupt preset execution
            if (this.executingPreset) {
                console.log(`[PTZ ${new Date().toISOString()}] Touch ended - ignoring (preset executing)`);
                return;
            }

            console.log(`[PTZ ${new Date().toISOString()}] Touch ended - stopping. States:`, {
                repeatInterval: !!this.repeatInterval,
                ptzTouchActive: this.ptzTouchActive,
                isExecuting: this.isExecuting,
                activeDirection: this.activeDirection
            });
            // Always call stopMovement - it's safe to call even if not moving
            this.stopMovement();
        });
    }

    async startMovement(direction) {
        if (!this.currentCamera) return;

        // Clear any existing state
        if (this.repeatInterval) {
            clearInterval(this.repeatInterval);
            this.repeatInterval = null;
        }

        this.isExecuting = true;
        this.moveAcknowledged = false; // Track when camera has processed the move
        this.moveStartTime = performance.now(); // Track timing for latency learning
        this.updateButtonStates();
        this.setButtonActive(direction, true);

        const serial = this.currentCamera.serial;
        const knownLatency = this.getCameraLatency(serial);

        // Apply pan/tilt reversal if configured for this camera
        const correctedDirection = this.applyReversal(serial, direction);
        console.log(`[PTZ ${new Date().toISOString()}] Starting continuous move:`, direction,
            correctedDirection !== direction ? `(reversed to: ${correctedDirection})` : '',
            'for', serial, `(known latency: ${knownLatency}ms)`);

        // Fire-and-forget but track acknowledgment for stop timing
        fetch(`/api/ptz/${serial}/${correctedDirection}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        }).then(response => response.json())
          .then(data => {
              const latency = Math.round(performance.now() - this.moveStartTime);
              this.moveAcknowledged = true; // Camera has processed the move
              if (data.success) {
                  // Learn this camera's latency
                  this.updateCameraLatency(serial, latency);
                  console.log(`[PTZ ${new Date().toISOString()}] ✓ Move acknowledged in ${latency}ms:`, data.message);
              } else {
                  console.warn(`[PTZ ${new Date().toISOString()}] ✗ Move failed after ${latency}ms:`, data.error || data.message);
              }
          })
          .catch(error => {
              this.moveAcknowledged = true; // Even on error, we're done waiting
              console.error(`[PTZ ${new Date().toISOString()}] ✗ Move request failed:`, error.message);
          });
    }

    async stopMovement() {
        const stopStartTime = performance.now();
        console.log(`[PTZ ${new Date().toISOString()}] stopMovement() entered. currentCamera:`, this.currentCamera?.serial);

        // Clear optical zoom timeout (user released button before timeout)
        this.clearOpticalZoomTimeout();

        // Clear digital zoom interval (user released button)
        this.clearDigitalZoomInterval();

        // Clear any interval (legacy, but keep for safety)
        if (this.repeatInterval) {
            clearInterval(this.repeatInterval);
            this.repeatInterval = null;
        }

        // Clear state
        this.ptzTouchActive = false;
        this.activeDirection = null;

        // Update UI immediately (optimistic)
        this.isExecuting = false;
        $('.ptz-btn').removeClass('active');
        this.updateButtonStates();

        if (!this.currentCamera) {
            console.log(`[PTZ ${new Date().toISOString()}] No currentCamera - cannot send stop command!`);
            return;
        }

        const serial = this.currentCamera.serial;

        // Wait for move command to be acknowledged by camera before sending stop
        // Uses learned latency per camera (stored in localStorage)
        const learnedLatency = this.getCameraLatency(serial);
        if (!this.moveAcknowledged) {
            console.log(`[PTZ ${new Date().toISOString()}] Waiting for move acknowledgment (learned latency: ${learnedLatency}ms)...`);
            const maxWait = Math.max(learnedLatency, 2000); // At least use learned latency, max 2s
            const startWait = performance.now();
            while (!this.moveAcknowledged && (performance.now() - startWait) < maxWait) {
                await new Promise(resolve => setTimeout(resolve, 50));
            }
            const waited = Math.round(performance.now() - startWait);
            if (this.moveAcknowledged) {
                console.log(`[PTZ ${new Date().toISOString()}] Move acknowledged after ${waited}ms, proceeding with stop`);
            } else {
                console.log(`[PTZ ${new Date().toISOString()}] Move acknowledgment timeout after ${waited}ms, sending stop anyway`);
            }
        }

        console.log(`[PTZ ${new Date().toISOString()}] Sending stop command for:`, serial);

        // Send stop command and await response to confirm camera received it
        try {
            const response = await fetch(`/api/ptz/${serial}/stop`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            const data = await response.json();
            const elapsed = (performance.now() - stopStartTime).toFixed(0);

            if (data.success) {
                console.log(`[PTZ ${new Date().toISOString()}] ✓ Stop confirmed by camera in ${elapsed}ms:`, data.message);
            } else {
                console.warn(`[PTZ ${new Date().toISOString()}] ✗ Stop failed after ${elapsed}ms:`, data.error || data.message);
            }
        } catch (error) {
            const elapsed = (performance.now() - stopStartTime).toFixed(0);
            console.error(`[PTZ ${new Date().toISOString()}] ✗ Stop request failed after ${elapsed}ms:`, error.message);
        }
    }

    async executeMovement(direction) {
        if (!this.canExecuteMovement() || this.isExecuting) return;

        this.isExecuting = true;
        this.updateButtonStates();
        this.setButtonActive(direction, true);

        try {
            const result = await $.ajax({
                url: `/api/ptz/${this.currentCamera.serial}/${direction}`,
                method: 'POST',
                contentType: 'application/json'
            });

            if (result.success) {
                console.log(`Camera "${this.currentCamera.name}" executed ${direction}`);
                this.dispatchPTZEvent(this.currentCamera.name, direction, true);

                // Wait for movement completion
                await this.waitForMovementCompletion();

            } else {
                throw new Error(result.error || 'Unknown PTZ error');
            }

        } catch (error) {
            console.log(`PTZ movement failed: ${error.message}`);
            this.dispatchPTZEvent(this.currentCamera.name, direction, false);

        } finally {
            this.isExecuting = false;
            this.setButtonActive(direction, false);
            this.updateButtonStates();
        }
    }

    setCurrentCamera(serial, name) {
        this.currentCamera = { serial, name };
        this.updateButtonStates();
        console.log(`Camera selected: ${name}`);

        // Load presets for this camera
        this.loadPresets(serial);

        // Load learned latency from database for this camera
        this.loadCameraLatency(serial);

        // Initialize digital zoom for this camera's stream element
        this.initializeDigitalZoom(serial);
    }

    setBridgeReady(ready) {
        this.bridgeReady = ready;
        this.updateButtonStates();
    }

    updateButtonStates() {
        const enabled = this.bridgeReady && this.currentCamera // && !this.isExecuting;=> This disables buttons during movement, which prevents stopping with mouseup!

        $('.ptz-btn').prop('disabled', !enabled);

        if (!enabled) {
            $('.ptz-btn').removeClass('active');
        }
    }

    canExecuteMovement() {
        return (
            this.bridgeReady &&
            this.currentCamera &&
            this.currentCamera.serial &&
            !this.isExecuting
        );
    }

    setButtonActive(direction, active) {
        const $button = $(`.ptz-btn[data-direction="${direction}"]`);
        if (active) {
            $button.addClass('active');
        } else {
            $button.removeClass('active');
        }
    }

    async waitForMovementCompletion() {
        return new Promise(resolve => {
            setTimeout(resolve, 3000); // 3 seconds for movement completion
        });
    }

    dispatchPTZEvent(camera, direction, success) {
        $(document).trigger('ptzCommandExecuted', {
            camera,
            direction,
            success
        });
    }

    setupPresetListeners() {
        // Listen for preset button clicks
        $(document).on('click', '.ptz-preset-btn', async (event) => {
            event.preventDefault();
            const presetToken = $(event.currentTarget).data('preset-token');
            const presetName = $(event.currentTarget).data('preset-name');

            console.log('[PTZ] Preset clicked:', presetName, presetToken);

            if (presetToken && this.currentCamera) {
                await this.gotoPreset(presetToken, presetName);
            }
        });

        // Listen for preset dropdown click/focus - load presets if missing
        $(document).on('click focus', '.ptz-preset-select', (event) => {
            const $dropdown = $(event.currentTarget);
            const $streamItem = $dropdown.closest('.stream-item');
            const serial = $streamItem.data('camera-serial');

            if (serial) {
                // Check if presets are loaded for this camera
                const presetsForCamera = this.presets[serial];

                if (!presetsForCamera || presetsForCamera.length === 0) {
                    console.log('[PTZ] Dropdown clicked but no presets loaded - loading now for:', serial);
                    this.loadPresets(serial);
                } else {
                    console.log('[PTZ] Dropdown clicked - presets already loaded:', presetsForCamera.length);
                }
            }
        });

        // Listen for preset dropdown changes
        $(document).on('change', '.ptz-preset-select', async (event) => {
            const presetToken = $(event.currentTarget).val();
            if (presetToken && presetToken !== '') {
                // Get camera info from parent stream-item
                const $streamItem = $(event.currentTarget).closest('.stream-item');
                const serial = $streamItem.data('camera-serial');
                const name = $streamItem.data('camera-name');

                // Check if save form is visible - if so, don't navigate, just update overwrite target
                const $form = $streamItem.find('.ptz-preset-form');
                if ($form.is(':visible')) {
                    // Form is visible - user is selecting preset to overwrite, don't navigate
                    console.log('[PTZ] Save form visible - not navigating to preset, keeping selection for overwrite');
                    return;
                }

                // Set current camera if not already set
                if (serial && serial !== this.currentCamera?.serial) {
                    this.setCurrentCamera(serial, name);
                    this.setBridgeReady(true);
                }

                const presetName = $(event.currentTarget).find('option:selected').text();
                await this.gotoPreset(presetToken, presetName);
                // Reset dropdown
                $(event.currentTarget).val('');
            }
        });
    }

    loadPresetsForAllCameras() {
        // Find all PTZ cameras on the page and load their presets
        // Stagger the requests to avoid overwhelming the server/browser on initial page load
        let delay = 0;
        const staggerMs = 500; // 500ms between each camera's preset load

        $('.stream-item').each((index, streamItem) => {
            const $streamItem = $(streamItem);
            const serial = $streamItem.data('camera-serial');
            const $ptzControls = $streamItem.find('.ptz-controls');

            // Only load presets for cameras that have PTZ controls
            if (serial && $ptzControls.length > 0) {
                console.log(`[PTZ] Scheduling preset load for ${serial} in ${delay}ms`);

                // Stagger the requests to prevent browser from aborting simultaneous requests
                setTimeout(() => {
                    this.loadPresets(serial);
                }, delay);

                delay += staggerMs;
            }
        });
    }

    /**
     * Load presets for a camera
     * @param {string} cameraSerial - Camera serial number
     * @param {number} retryCount - Current retry attempt (internal)
     * @param {boolean} refresh - If true, bypass cache and fetch fresh from camera
     */
    async loadPresets(cameraSerial, retryCount = 0, refresh = false) {
        const maxRetries = 3;
        const retryDelay = Math.min(1000 * Math.pow(2, retryCount), 5000); // Exponential backoff, max 5s

        try {
            console.log('[PTZ] Loading presets for camera:', cameraSerial,
                retryCount > 0 ? `(retry ${retryCount}/${maxRetries})` : '',
                refresh ? '(refresh=true)' : '');

            const url = refresh
                ? `/api/ptz/${cameraSerial}/presets?refresh=true`
                : `/api/ptz/${cameraSerial}/presets`;

            const response = await $.ajax({
                url: url,
                method: 'GET',
                timeout: 10000
            });

            if (response.success) {
                this.presets[cameraSerial] = response.presets || [];
                console.log(`[PTZ] Loaded ${this.presets[cameraSerial].length} presets for ${cameraSerial}:`, this.presets[cameraSerial]);
                this.updatePresetUI();
            } else {
                console.error('[PTZ] Failed to load presets:', response.error);
                this.presets[cameraSerial] = [];
                this.updatePresetUI();
            }
        } catch (error) {
            // Check if this is a network/server error (readyState 0 or 4xx/5xx)
            const isNetworkError = error.readyState === 0 || error.status === 0 || error.status >= 500;

            if (isNetworkError && retryCount < maxRetries) {
                console.warn(`[PTZ] Error loading presets (readyState: ${error.readyState}, status: ${error.status}) - retrying in ${retryDelay}ms...`);

                // Retry after delay
                setTimeout(() => {
                    this.loadPresets(cameraSerial, retryCount + 1);
                }, retryDelay);
            } else {
                // Give up after max retries or non-network error
                console.error('[PTZ] Error loading presets (giving up):', error);
                this.presets[cameraSerial] = [];
                this.updatePresetUI();
            }
        }
    }

    async gotoPreset(presetToken, presetName) {
        if (!this.currentCamera || this.isExecuting) return;

        this.isExecuting = true;
        this.executingPreset = true; // Prevent mouseup/touchend from interrupting

        try {
            console.log('[PTZ] Going to preset:', presetName, 'for camera:', this.currentCamera.serial);

            const response = await $.ajax({
                url: `/api/ptz/${this.currentCamera.serial}/preset/${presetToken}`,
                method: 'POST',
                contentType: 'application/json',
                timeout: 10000
            });

            if (response.success) {
                console.log('[PTZ] Successfully moved to preset:', presetName);
                this.showPresetFeedback(presetName);
            } else {
                console.error('[PTZ] Preset goto failed:', response.error);
            }
        } catch (error) {
            console.error('[PTZ] Error going to preset:', error);
        } finally {
            this.isExecuting = false;
            this.executingPreset = false; // Re-enable mouseup/touchend handlers
        }
    }

    updatePresetUI() {
        // Update preset dropdowns - each camera gets its own presets
        $('.stream-item').each((index, streamItem) => {
            const $streamItem = $(streamItem);
            const serial = $streamItem.data('camera-serial');
            const $select = $streamItem.find('.ptz-preset-select');

            if ($select.length && serial) {
                $select.empty();
                $select.append('<option value="">-- Select Preset --</option>');

                // Get presets for this specific camera
                const cameraPresets = this.presets[serial] || [];
                cameraPresets.forEach(preset => {
                    $select.append(`<option value="${preset.token}">${preset.name}</option>`);
                });

                console.log(`[PTZ] Updated UI for ${serial}: ${cameraPresets.length} presets`);

                // Always show preset dropdown for debugging
                $select.closest('.ptz-presets-container').show();
            }
        });
    }

    showPresetFeedback(presetName) {
        // Create temporary feedback element
        const $feedback = $('<div>')
            .addClass('ptz-preset-feedback')
            .text(`Moving to: ${presetName}`)
            .css({
                position: 'fixed',
                top: '50%',
                left: '50%',
                transform: 'translate(-50%, -50%)',
                padding: '20px 40px',
                backgroundColor: 'rgba(0, 0, 0, 0.8)',
                color: 'white',
                borderRadius: '8px',
                zIndex: 10000,
                fontSize: '18px',
                fontWeight: 'bold'
            });

        $('body').append($feedback);

        setTimeout(() => {
            $feedback.fadeOut(500, function () {
                $(this).remove();
            });
        }, 2000);
    }

    // =========================================================================
    // Preset Management Methods (create, overwrite, delete)
    // =========================================================================

    /**
     * Setup event listeners for preset management (save, delete, form controls)
     */
    setupPresetManagementListeners() {
        // Enable/disable delete button based on dropdown selection
        $(document).on('change', '.ptz-preset-select', (event) => {
            const $select = $(event.currentTarget);
            const $container = $select.closest('.ptz-presets-container');
            const $deleteBtn = $container.find('.ptz-preset-delete-btn');
            const selectedToken = $select.val();

            // Enable delete button only when a preset is selected
            $deleteBtn.prop('disabled', !selectedToken);

            // Show overwrite option if preset is selected and form is visible
            const $form = $container.find('.ptz-preset-form');
            const $overwriteLabel = $form.find('.ptz-preset-overwrite-label');
            if ($form.is(':visible')) {
                if (selectedToken) {
                    const selectedName = $select.find('option:selected').text();
                    $overwriteLabel.find('span').text(`Overwrite "${selectedName}"`);
                    $overwriteLabel.show();
                } else {
                    $overwriteLabel.hide();
                    $form.find('.ptz-preset-overwrite-checkbox').prop('checked', false);
                }
            }
        });

        // Save button click - show form
        $(document).on('click', '.ptz-preset-save-btn', (event) => {
            event.preventDefault();
            event.stopPropagation();
            const $btn = $(event.currentTarget);
            const $streamItem = $btn.closest('.stream-item');
            const serial = $streamItem.data('camera-serial');
            const name = $streamItem.data('camera-name');

            // Set current camera if not already set
            if (serial && serial !== this.currentCamera?.serial) {
                this.setCurrentCamera(serial, name);
                this.setBridgeReady(true);
            }

            this.showPresetForm($streamItem);
        });

        // Cancel button - hide form
        $(document).on('click', '.ptz-preset-cancel-btn', (event) => {
            event.preventDefault();
            event.stopPropagation();
            const $streamItem = $(event.currentTarget).closest('.stream-item');
            this.hidePresetForm($streamItem);
        });

        // Confirm save button - save preset
        $(document).on('click', '.ptz-preset-confirm-btn', async (event) => {
            event.preventDefault();
            event.stopPropagation();
            const $btn = $(event.currentTarget);
            const $streamItem = $btn.closest('.stream-item');
            const $container = $streamItem.find('.ptz-presets-container');
            const $form = $container.find('.ptz-preset-form');
            const $nameInput = $form.find('.ptz-preset-name-input');
            const $overwriteCheckbox = $form.find('.ptz-preset-overwrite-checkbox');
            const $select = $container.find('.ptz-preset-select');

            const serial = $streamItem.data('camera-serial');
            const cameraType = $streamItem.data('camera-type');  // Get camera type for Eufy vs ONVIF handling
            const presetName = $nameInput.val().trim();
            const overwrite = $overwriteCheckbox.is(':checked');
            const selectedToken = overwrite ? $select.val() : null;

            // For ONVIF cameras, preset name is required
            // For Eufy cameras, name is ignored (uses index only)
            if (cameraType !== 'eufy' && !presetName) {
                $nameInput.css('border-color', '#f44336');
                $nameInput.focus();
                return;
            }

            // Disable button during save
            $btn.prop('disabled', true);

            const success = await this.savePreset(serial, presetName, selectedToken, cameraType);

            $btn.prop('disabled', false);

            if (success) {
                this.hidePresetForm($streamItem);
            }
        });

        // Delete button click - delete selected preset
        $(document).on('click', '.ptz-preset-delete-btn', async (event) => {
            event.preventDefault();
            event.stopPropagation();
            const $btn = $(event.currentTarget);
            const $streamItem = $btn.closest('.stream-item');
            const $container = $streamItem.find('.ptz-presets-container');
            const $select = $container.find('.ptz-preset-select');

            const serial = $streamItem.data('camera-serial');
            const name = $streamItem.data('camera-name');
            const selectedToken = $select.val();
            const selectedName = $select.find('option:selected').text();

            if (!selectedToken) return;

            // Set current camera if not already set
            if (serial && serial !== this.currentCamera?.serial) {
                this.setCurrentCamera(serial, name);
                this.setBridgeReady(true);
            }

            // Confirm deletion
            if (!confirm(`Delete preset "${selectedName}"?`)) {
                return;
            }

            // Disable button during delete
            $btn.prop('disabled', true);

            await this.deletePreset(serial, selectedToken, selectedName);

            // Re-enable will happen via updatePresetUI
        });

        // Enter key in name input triggers save
        $(document).on('keydown', '.ptz-preset-name-input', (event) => {
            if (event.key === 'Enter') {
                event.preventDefault();
                const $streamItem = $(event.currentTarget).closest('.stream-item');
                $streamItem.find('.ptz-preset-confirm-btn').click();
            } else if (event.key === 'Escape') {
                event.preventDefault();
                const $streamItem = $(event.currentTarget).closest('.stream-item');
                this.hidePresetForm($streamItem);
            }
        });

        // Clear error styling on input
        $(document).on('input', '.ptz-preset-name-input', (event) => {
            $(event.currentTarget).css('border-color', '');
        });
    }

    /**
     * Show the preset save form for a camera
     * @param {jQuery} $streamItem - The stream item element
     */
    showPresetForm($streamItem) {
        const $container = $streamItem.find('.ptz-presets-container');
        const $form = $container.find('.ptz-preset-form');
        const $nameInput = $form.find('.ptz-preset-name-input');
        const $overwriteLabel = $form.find('.ptz-preset-overwrite-label');
        const $overwriteCheckbox = $form.find('.ptz-preset-overwrite-checkbox');
        const $select = $container.find('.ptz-preset-select');

        // Reset form
        $nameInput.css('border-color', '');
        $overwriteCheckbox.prop('checked', false);

        // Show/hide overwrite option based on dropdown selection
        // Pre-populate name field with selected preset name if one is selected
        const selectedToken = $select.val();
        if (selectedToken) {
            const selectedName = $select.find('option:selected').text();
            $nameInput.val(selectedName);  // Pre-populate with selected preset name
            $overwriteLabel.find('span').text(`Overwrite "${selectedName}"`);
            $overwriteLabel.show();
            $overwriteCheckbox.prop('checked', true);  // Default to overwrite when preset selected
        } else {
            $nameInput.val('');  // Clear if no preset selected
            $overwriteLabel.hide();
        }

        $form.slideDown(150);
        $nameInput.focus();
        $nameInput.select();  // Select all text for easy replacement
    }

    /**
     * Hide the preset save form
     * @param {jQuery} $streamItem - The stream item element
     */
    hidePresetForm($streamItem) {
        const $container = $streamItem.find('.ptz-presets-container');
        const $form = $container.find('.ptz-preset-form');
        $form.slideUp(150);
    }

    /**
     * Save current camera position as a preset.
     * @param {string} serial - Camera serial number
     * @param {string} presetName - Name for the preset
     * @param {string|null} overwriteToken - If set, overwrite this existing preset
     * @param {string} cameraType - Camera type (eufy, amcrest, reolink, etc.)
     * @returns {Promise<boolean>} Success status
     */
    async savePreset(serial, presetName, overwriteToken = null, cameraType = null) {
        try {
            console.log(`[PTZ] Saving preset "${presetName}" for ${serial} (type: ${cameraType})`, overwriteToken ? `(overwriting ${overwriteToken})` : '(new)');

            let payload;

            // Eufy cameras use numeric index (0-3), not name/token like ONVIF
            if (cameraType === 'eufy') {
                // For Eufy: overwriteToken IS the index (0, 1, 2, 3)
                // If overwriting, use that index. Otherwise, use next available.
                let presetIndex;
                if (overwriteToken !== null && overwriteToken !== '') {
                    presetIndex = parseInt(overwriteToken, 10);
                } else {
                    // Find next available index (0-3)
                    const existingPresets = this.presets[serial] || [];
                    const usedIndices = existingPresets.map(p => parseInt(p.token, 10)).filter(i => !isNaN(i));
                    presetIndex = [0, 1, 2, 3].find(i => !usedIndices.includes(i));
                    if (presetIndex === undefined) {
                        this.showFeedback('All 4 Eufy preset slots are used. Select one to overwrite.', 'warning');
                        return false;
                    }
                }
                payload = { index: presetIndex };
                console.log(`[PTZ] Eufy preset: using index ${presetIndex}`);
            } else {
                // ONVIF cameras (Amcrest, Reolink, etc.) use name and optional token
                payload = { name: presetName };
                if (overwriteToken) {
                    payload.token = overwriteToken;  // Backend uses token to identify preset to overwrite
                }
            }

            const response = await $.ajax({
                url: `/api/ptz/${serial}/preset`,
                method: 'POST',
                contentType: 'application/json',
                data: JSON.stringify(payload),
                timeout: 15000
            });

            if (response.success) {
                console.log(`[PTZ] Preset "${presetName}" saved successfully`);
                this.showFeedback(`Preset saved: ${presetName}`, 'success');

                // Reload presets for this camera (refresh=true to bypass cache)
                await this.loadPresets(serial, 0, true);

                return true;
            } else {
                console.error('[PTZ] Failed to save preset:', response.error);
                this.showFeedback(`Failed: ${response.error}`, 'error');
                return false;
            }
        } catch (error) {
            console.error('[PTZ] Error saving preset:', error);
            const errorMsg = error.responseJSON?.error || error.statusText || error.message || 'Unknown error';
            const retryAvailable = error.responseJSON?.retry_available;
            console.error('[PTZ] Error details:', errorMsg, 'Status:', error.status,
                          'Retry available:', retryAvailable);

            if (retryAvailable) {
                // Bridge error with retry possibility — show error with retry button
                const retryId = `ptz-retry-${Date.now()}`;
                this.showFeedback(
                    `Bridge error: ${errorMsg} ` +
                    `<button id="${retryId}" style="margin-left:8px;padding:2px 10px;` +
                    `border-radius:4px;border:1px solid #fff;background:rgba(255,255,255,0.2);` +
                    `color:#fff;cursor:pointer;">Retry</button>`,
                    'error', 10000
                );
                // Attach retry handler after DOM update
                setTimeout(() => {
                    const btn = document.getElementById(retryId);
                    if (btn) {
                        btn.addEventListener('click', () => {
                            btn.disabled = true;
                            btn.textContent = 'Retrying...';
                            this.savePreset(serial, presetName, overwriteToken, cameraType);
                        });
                    }
                }, 50);
            } else {
                this.showFeedback(`Error saving preset: ${errorMsg}`, 'error');
            }
            return false;
        }
    }

    /**
     * Delete a preset.
     * @param {string} serial - Camera serial number
     * @param {string} presetToken - Preset token to delete
     * @param {string} presetName - Preset name (for feedback)
     * @returns {Promise<boolean>} Success status
     */
    async deletePreset(serial, presetToken, presetName) {
        try {
            console.log(`[PTZ] Deleting preset "${presetName}" (${presetToken}) for ${serial}`);

            const response = await $.ajax({
                url: `/api/ptz/${serial}/preset/${presetToken}`,
                method: 'DELETE',
                timeout: 15000
            });

            if (response.success) {
                console.log(`[PTZ] Preset "${presetName}" deleted successfully`);
                this.showFeedback(`Preset deleted: ${presetName}`, 'success');

                // Reload presets for this camera (refresh=true to bypass cache)
                await this.loadPresets(serial, 0, true);

                return true;
            } else {
                console.error('[PTZ] Failed to delete preset:', response.error);
                this.showFeedback(`Failed: ${response.error}`, 'error');
                return false;
            }
        } catch (error) {
            console.error('[PTZ] Error deleting preset:', error);
            this.showFeedback('Error deleting preset', 'error');
            return false;
        }
    }

    /**
     * Show feedback message to user
     * @param {string} message - Message to display
     * @param {string} type - 'success', 'error', or 'warning'
     */
    showFeedback(message, type = 'info') {
        const bgColors = {
            success: 'rgba(76, 175, 80, 0.9)',
            error: 'rgba(244, 67, 54, 0.9)',
            warning: 'rgba(255, 152, 0, 0.9)',
            info: 'rgba(0, 0, 0, 0.85)'
        };

        const $feedback = $('<div>')
            .addClass('ptz-preset-feedback')
            .text(message)
            .css({
                position: 'fixed',
                top: '50%',
                left: '50%',
                transform: 'translate(-50%, -50%)',
                padding: '15px 30px',
                backgroundColor: bgColors[type] || bgColors.info,
                color: 'white',
                borderRadius: '8px',
                zIndex: 10000,
                fontSize: '16px',
                fontWeight: 'bold',
                boxShadow: '0 4px 20px rgba(0, 0, 0, 0.5)'
            });

        $('body').append($feedback);

        setTimeout(() => {
            $feedback.fadeOut(400, function () {
                $(this).remove();
            });
        }, 2000);
    }
}
