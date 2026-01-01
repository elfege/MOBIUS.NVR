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
        // PTZ uses ONVIF ContinuousMove - one command starts movement,
        // camera keeps moving until a Stop command is sent.
        // No need for repeat interval or AbortController.


        this.setupEventListeners();
        this.setupPresetListeners();
        this.updateButtonStates();


        console.log("#######################################")
        console.log('########### PTZ controller initialized');
        console.log("#######################################")
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

        // Touch end at document level - ALWAYS stop on any touch release
        // This is aggressive but ensures camera stops when finger lifts
        $(document).on('touchend touchcancel', () => {
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
        this.updateButtonStates();
        this.setButtonActive(direction, true);

        const serial = this.currentCamera.serial;
        console.log(`[PTZ ${new Date().toISOString()}] Starting continuous move:`, direction, 'for', serial);

        // Fire-and-forget: DON'T await the move command!
        // If we await, the stop command (on mouseup) may arrive at the camera
        // BEFORE the move command finishes, causing the camera to ignore the stop.
        fetch(`/api/ptz/${serial}/${direction}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        }).then(response => response.json())
          .then(data => {
              if (data.success) {
                  console.log(`[PTZ ${new Date().toISOString()}] ✓ Move acknowledged:`, data.message);
              } else {
                  console.warn(`[PTZ ${new Date().toISOString()}] ✗ Move failed:`, data.error || data.message);
              }
          })
          .catch(error => {
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
