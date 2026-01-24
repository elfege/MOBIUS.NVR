/**
 * PTZ Controller Module - ES6 + jQuery
 * Handles PTZ camera movement controls
 */

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
        // PTZ uses ONVIF ContinuousMove - one command starts movement,
        // camera keeps moving until a Stop command is sent.
        // Latency is learned per-camera and stored in PostgreSQL via API.

        // Get or create client UUID for latency tracking
        this.clientUuid = this.getOrCreateClientUuid();


        this.setupEventListeners();
        this.setupPresetListeners();
        this.setupReversePanListeners();
        this.updateButtonStates();

        // Show preset dropdowns immediately (for debugging)
        this.updatePresetUI();

        // Load presets for all PTZ cameras on page load
        this.loadPresetsForAllCameras();

        // Load reverse pan settings for all PTZ cameras
        this.loadReversePanSettingsForAllCameras();


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
                // Home = go to preset 0 (not ONVIF GotoHomePosition)
                this.gotoPreset(0, 'Home');
            } else if (direction === '360' || direction === 'recalibrate') {
                // Discrete commands (360 rotation, recalibration) - single execution
                this.executeMovement(direction);
            } else if (direction) {
                // Continuous movement directions (left, right, up, down, zoom)
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

    async loadPresets(cameraSerial, retryCount = 0) {
        const maxRetries = 3;
        const retryDelay = Math.min(1000 * Math.pow(2, retryCount), 5000); // Exponential backoff, max 5s

        try {
            console.log('[PTZ] Loading presets for camera:', cameraSerial, retryCount > 0 ? `(retry ${retryCount}/${maxRetries})` : '');

            const response = await $.ajax({
                url: `/api/ptz/${cameraSerial}/presets`,
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
}
