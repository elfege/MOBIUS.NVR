/**
 * PTZ Controller Module - ES6 + jQuery
 * Handles PTZ camera movement controls
 */

export class PTZController {
    constructor() {

        this.currentCamera = null;
        this.bridgeReady = false;
        this.isExecuting = false;
        this.presets = [];
        this.ptzTouchActive = false;
        this.activeDirection = null;
        this.repeatInterval = null; // Legacy, kept for safety
        this.isDraggingPTZ = false; // Flag to prevent PTZ stop during drag handle interaction
        this.moveAcknowledged = true; // Track when camera has processed a move command
        this.moveStartTime = null; // Track when move command was sent
        this.latencyCache = {}; // In-memory cache of learned latencies per camera
        // PTZ uses ONVIF ContinuousMove - one command starts movement,
        // camera keeps moving until a Stop command is sent.
        // Latency is learned per-camera and stored in PostgreSQL via API.

        // Get or create client UUID for latency tracking
        this.clientUuid = this.getOrCreateClientUuid();


        this.setupEventListeners();
        this.setupPresetListeners();
        this.setupDraggable();
        this.updateButtonStates();


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

    /**
     * Setup draggable PTZ controls in fullscreen mode
     */
    setupDraggable() {
        // State for dragging
        this.dragState = {
            isDragging: false,
            startX: 0,
            startY: 0,
            initialLeft: 0,
            initialTop: 0
        };

        // Store observer reference for observing new elements
        this.fullscreenObserver = new MutationObserver((mutations) => {
            mutations.forEach((mutation) => {
                if (mutation.type === 'attributes' && mutation.attributeName === 'class') {
                    const $streamItem = $(mutation.target);
                    if ($streamItem.hasClass('css-fullscreen')) {
                        console.log('[PTZ] Fullscreen detected for:', $streamItem.data('camera-serial'));
                        const $ptz = $streamItem.find('.ptz-controls');
                        console.log('[PTZ] PTZ controls found:', $ptz.length, 'display:', $ptz.css('display'));
                        this.addDragHandle($streamItem);
                    } else if ($streamItem.data('had-fullscreen')) {
                        console.log('[PTZ] Exiting fullscreen, removing drag handle');
                        this.removeDragHandle($streamItem);
                        $streamItem.removeData('had-fullscreen');
                    }
                }
            });
        });

        // Function to observe a stream item
        const observeStreamItem = (el) => {
            this.fullscreenObserver.observe(el, { attributes: true, attributeFilter: ['class'] });
            console.log('[PTZ] Now observing stream item:', $(el).data('camera-serial') || 'unknown');
        };

        // Initial observation - wait for DOM to be ready
        const initObservers = () => {
            const streamItems = document.querySelectorAll('.stream-item');
            console.log(`[PTZ] Setting up drag observers for ${streamItems.length} stream items`);
            streamItems.forEach(el => observeStreamItem(el));
        };

        // Run now if DOM ready, otherwise wait
        if (document.readyState === 'complete') {
            initObservers();
        } else {
            $(document).ready(() => initObservers());
        }

        // Also observe for dynamically added stream items
        const containerObserver = new MutationObserver((mutations) => {
            mutations.forEach((mutation) => {
                mutation.addedNodes.forEach((node) => {
                    if (node.nodeType === 1 && $(node).hasClass('stream-item')) {
                        observeStreamItem(node);
                    }
                });
            });
        });

        // Wait for container to exist
        const setupContainerObserver = () => {
            const container = document.querySelector('.streams-container');
            if (container) {
                containerObserver.observe(container, { childList: true });
                console.log('[PTZ] Container observer set up');
            } else {
                // Retry after a short delay
                setTimeout(setupContainerObserver, 100);
            }
        };
        setupContainerObserver();

        // Use event delegation for drag handle
        $(document).on('mousedown touchstart', '.stream-item.css-fullscreen .ptz-controls .ptz-drag-handle', (e) => {
            e.preventDefault();
            e.stopPropagation();
            e.stopImmediatePropagation();

            // Mark that we're in drag mode to prevent PTZ touchend handler interference
            this.isDraggingPTZ = true;

            const $controls = $(e.currentTarget).closest('.ptz-controls');
            this.startDrag(e, $controls);
        });

        $(document).on('mousemove touchmove', (e) => {
            if (this.dragState.isDragging) {
                e.preventDefault();
                this.doDrag(e);
            }
        });

        $(document).on('mouseup touchend touchcancel', (e) => {
            if (this.dragState.isDragging) {
                e.preventDefault();
                e.stopPropagation();
                this.endDrag();
                // Clear drag mode flag after a short delay
                setTimeout(() => {
                    this.isDraggingPTZ = false;
                }, 100);
            }
        });
    }

    /**
     * Add drag handle to PTZ controls when entering fullscreen
     */
    addDragHandle($streamItem) {
        const $ptzControls = $streamItem.find('.ptz-controls');
        if ($ptzControls.length && !$ptzControls.find('.ptz-drag-handle').length) {
            // Mark as having been in fullscreen (for cleanup later)
            $streamItem.data('had-fullscreen', true);

            // Add drag handle at the top
            $ptzControls.prepend('<div class="ptz-drag-handle"></div>');
            console.log('[PTZ] Added drag handle for fullscreen, ptz-controls found:', $ptzControls.length);

            // Restore saved position
            this.restorePTZPosition($ptzControls, $streamItem);
        } else {
            console.log('[PTZ] addDragHandle: ptz-controls length:', $ptzControls.length,
                        'already has handle:', $ptzControls.find('.ptz-drag-handle').length > 0);
        }
    }

    /**
     * Remove drag handle when exiting fullscreen
     */
    removeDragHandle($streamItem) {
        const $ptzControls = $streamItem.find('.ptz-controls');
        $ptzControls.find('.ptz-drag-handle').remove();

        // Reset position and width to default CSS
        $ptzControls.css({
            top: '',
            left: '',
            bottom: '',
            right: '',
            width: ''
        });
        console.log('[PTZ] Removed drag handle, reset position');
    }

    /**
     * Start dragging the PTZ controls
     */
    startDrag(e, $controls) {
        const touch = e.type === 'touchstart' ? e.originalEvent.touches[0] : e;

        // Get current position relative to the fullscreen stream-item
        const rect = $controls[0].getBoundingClientRect();
        const $streamItem = $controls.closest('.stream-item.css-fullscreen');
        const boundaryRect = $streamItem.length ? $streamItem[0].getBoundingClientRect() :
                            { left: 0, top: 0 };

        // Store current width to prevent expansion during drag
        const currentWidth = rect.width;

        this.dragState = {
            isDragging: true,
            $element: $controls,
            startX: touch.clientX,
            startY: touch.clientY,
            initialLeft: rect.left - boundaryRect.left,
            initialTop: rect.top - boundaryRect.top,
            fixedWidth: currentWidth
        };

        // Switch from bottom/right positioning to top/left for smooth dragging
        // Also lock the width to prevent expansion
        $controls.css({
            bottom: 'auto',
            right: 'auto',
            top: this.dragState.initialTop + 'px',
            left: this.dragState.initialLeft + 'px',
            width: currentWidth + 'px'
        });

        $controls.addClass('dragging');
        console.log('[PTZ] Started dragging controls at', this.dragState.initialLeft, this.dragState.initialTop, 'width:', currentWidth);
    }

    /**
     * Handle drag movement
     */
    doDrag(e) {
        if (!this.dragState.isDragging) return;

        const touch = e.type === 'touchmove' ? e.originalEvent.touches[0] : e;
        const $controls = this.dragState.$element;

        // Use the fullscreen stream-item as the boundary, not immediate parent
        const $streamItem = $controls.closest('.stream-item.css-fullscreen');
        const boundaryRect = $streamItem.length ? $streamItem[0].getBoundingClientRect() :
                            { left: 0, top: 0, width: window.innerWidth, height: window.innerHeight };

        // Calculate new position
        const deltaX = touch.clientX - this.dragState.startX;
        const deltaY = touch.clientY - this.dragState.startY;

        let newLeft = this.dragState.initialLeft + deltaX;
        let newTop = this.dragState.initialTop + deltaY;

        // Constrain to boundary (keep fully visible)
        const controlsRect = $controls[0].getBoundingClientRect();
        const maxLeft = boundaryRect.width - controlsRect.width;
        const maxTop = boundaryRect.height - controlsRect.height;

        newLeft = Math.max(0, Math.min(newLeft, maxLeft));
        newTop = Math.max(0, Math.min(newTop, maxTop));

        $controls.css({
            left: newLeft + 'px',
            top: newTop + 'px'
        });
    }

    /**
     * End dragging
     */
    endDrag() {
        if (!this.dragState.isDragging) return;

        const $controls = this.dragState.$element;
        $controls.removeClass('dragging');

        // Save position to localStorage for persistence
        const position = {
            left: $controls.css('left'),
            top: $controls.css('top')
        };
        localStorage.setItem('ptz_controls_position', JSON.stringify(position));

        this.dragState.isDragging = false;
        console.log('[PTZ] Finished dragging controls, saved position:', position);
    }

    /**
     * Restore saved PTZ controls position
     */
    restorePTZPosition($controls, $streamItem) {
        const saved = localStorage.getItem('ptz_controls_position');
        if (saved) {
            try {
                const position = JSON.parse(saved);
                const left = parseInt(position.left);
                const top = parseInt(position.top);

                // Get viewport bounds
                const viewportWidth = window.innerWidth;
                const viewportHeight = window.innerHeight;
                const controlsRect = $controls[0].getBoundingClientRect();

                // Validate position is within viewport (with some margin)
                const maxLeft = viewportWidth - controlsRect.width - 20;
                const maxTop = viewportHeight - controlsRect.height - 20;

                if (!isNaN(left) && !isNaN(top) &&
                    left >= 0 && left <= maxLeft &&
                    top >= 0 && top <= maxTop) {
                    $controls.css({
                        bottom: 'auto',
                        right: 'auto',
                        top: position.top,
                        left: position.left
                    });
                    console.log('[PTZ] Restored saved position:', position);
                } else {
                    console.log('[PTZ] Saved position out of bounds, using default. Saved:', position, 'Max:', maxLeft, maxTop);
                    // Clear invalid saved position
                    localStorage.removeItem('ptz_controls_position');
                }
            } catch (e) {
                console.warn('[PTZ] Failed to restore position:', e);
                localStorage.removeItem('ptz_controls_position');
            }
        }
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
            } else if (direction && direction !== '360') {
                this.ptzTouchActive = true;
                this.activeDirection = direction;
                this.startMovement(direction);
            } else if (direction === '360') {
                this.executeMovement(direction);
            }
        });

        // Mouse up anywhere on document (not just on button)
        $(document).on('mouseup', () => {
            // Ignore if this was a touch interaction (avoid emulated mouse events)
            if (this.lastInputType === 'touch') return;

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

        // Touch end at document level - stop PTZ movement when finger lifts
        // BUT skip if we're dragging the PTZ controls (isDraggingPTZ flag)
        $(document).on('touchend touchcancel', () => {
            // Skip if we're dragging PTZ controls - let the drag handler manage this
            if (this.isDraggingPTZ) {
                console.log(`[PTZ ${new Date().toISOString()}] Touch ended during PTZ drag - ignoring for movement`);
                return;
            }

            // Only stop if PTZ movement was active
            if (this.ptzTouchActive || this.activeDirection) {
                console.log(`[PTZ ${new Date().toISOString()}] Touch ended - stopping. States:`, {
                    repeatInterval: !!this.repeatInterval,
                    ptzTouchActive: this.ptzTouchActive,
                    isExecuting: this.isExecuting,
                    activeDirection: this.activeDirection
                });
                this.stopMovement();
            }
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
        console.log(`[PTZ ${new Date().toISOString()}] Starting continuous move:`, direction, 'for', serial, `(known latency: ${knownLatency}ms)`);

        // Fire-and-forget but track acknowledgment for stop timing
        fetch(`/api/ptz/${serial}/${direction}`, {
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

        // Listen for preset dropdown changes
        $(document).on('change', '.ptz-preset-select', async (event) => {
            const presetToken = $(event.currentTarget).val();
            if (presetToken && presetToken !== '') {
                const presetName = $(event.currentTarget).find('option:selected').text();
                await this.gotoPreset(presetToken, presetName);
                // Reset dropdown
                $(event.currentTarget).val('');
            }
        });
    }

    async loadPresets(cameraSerial) {
        try {
            console.log('[PTZ] Loading presets for camera:', cameraSerial);

            const response = await $.ajax({
                url: `/api/ptz/${cameraSerial}/presets`,
                method: 'GET',
                timeout: 10000
            });

            if (response.success) {
                this.presets = response.presets || [];
                console.log('[PTZ] Loaded presets:', this.presets);
                this.updatePresetUI();
            } else {
                console.error('[PTZ] Failed to load presets:', response.error);
                this.presets = [];
                this.updatePresetUI();
            }
        } catch (error) {
            console.error('[PTZ] Error loading presets:', error);
            this.presets = [];
            this.updatePresetUI();
        }
    }

    async gotoPreset(presetToken, presetName) {
        if (!this.currentCamera || this.isExecuting) return;

        this.isExecuting = true;

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
        }
    }

    updatePresetUI() {
        // Update preset dropdowns
        $('.ptz-preset-select').each((index, select) => {
            const $select = $(select);
            $select.empty();
            $select.append('<option value="">-- Select Preset --</option>');

            this.presets.forEach(preset => {
                $select.append(`<option value="${preset.token}">${preset.name}</option>`);
            });

            // Show/hide based on preset availability
            if (this.presets.length > 0) {
                $select.closest('.ptz-presets-container').show();
            } else {
                $select.closest('.ptz-presets-container').hide();
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
