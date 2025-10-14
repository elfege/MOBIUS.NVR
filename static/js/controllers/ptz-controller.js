/**
 * PTZ Controller Module - ES6 + jQuery
 * Handles PTZ camera movement controls
 */

export class PTZController {
    constructor(logger, loadingManager) {
        this.logger = logger;
        this.loadingManager = loadingManager;
        this.currentCamera = null;
        this.bridgeReady = false;
        this.isExecuting = false;
    }

    init() {
        this.setupEventListeners();
        this.updateButtonStates();
        this.logger.info('PTZ controller initialized');
    }

    setupEventListeners() {
        // Handle mousedown/touchstart for continuous movement
        $('.ptz-btn').on('mousedown touchstart', (event) => {
            event.preventDefault();
            const direction = $(event.currentTarget).data('direction');
            if (direction && direction !== '360') {
                this.startMovement(direction);
            } else if (direction === '360') {
                this.executeMovement(direction);
            }
        });

        // Handle mouseup/touchend to stop movement
        $('.ptz-btn').on('mouseup touchend mouseleave', (event) => {
            event.preventDefault();
            const direction = $(event.currentTarget).data('direction');
            if (direction && direction !== '360') {
                this.stopMovement();
            }
        });
    }

    async startMovement(direction) {
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

            if (!result.success) {
                this.logger.error(`PTZ start failed: ${result.error}`);
                this.stopMovement();
            }

        } catch (error) {
            this.logger.error(`PTZ movement failed: ${error.message}`);
            this.stopMovement();
        }
    }

    async stopMovement() {
        if (!this.currentCamera) return;

        try {
            const result = await $.ajax({
                url: `/api/ptz/${this.currentCamera.serial}/stop`,
                method: 'POST',
                contentType: 'application/json'
            });

            if (result.success) {
                this.logger.success('PTZ movement stopped');
            }

        } catch (error) {
            this.logger.error(`Failed to stop PTZ: ${error.message}`);
        } finally {
            this.isExecuting = false;
            $('.ptz-btn').removeClass('active');
            this.updateButtonStates();
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
                this.logger.success(`Camera "${this.currentCamera.name}" executed ${direction}`);
                this.dispatchPTZEvent(this.currentCamera.name, direction, true);

                // Wait for movement completion
                await this.waitForMovementCompletion();

            } else {
                throw new Error(result.error || 'Unknown PTZ error');
            }

        } catch (error) {
            this.logger.error(`PTZ movement failed: ${error.message}`);
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
        this.logger.info(`Camera selected: ${name}`);
    }

    setBridgeReady(ready) {
        this.bridgeReady = ready;
        this.updateButtonStates();
    }

    updateButtonStates() {
        const enabled = this.bridgeReady && this.currentCamera && !this.isExecuting;

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
}
