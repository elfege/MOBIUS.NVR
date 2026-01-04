/**
 * Camera State Monitor - Polls CameraStateTracker API and updates UI
 *
 * Fetches detailed camera state information from /api/camera/state/<camera_id>
 * and updates stream status indicators with:
 * - Availability (ONLINE, STARTING, OFFLINE, DEGRADED)
 * - Publisher status (MediaMTX active/inactive)
 * - FFmpeg process status (alive/dead)
 * - Backoff timer (retry countdown)
 *
 * This helps users distinguish between:
 * - UI problems (browser/network issues)
 * - Backend problems (FFmpeg/MediaMTX issues)
 * - Camera hardware problems (offline cameras)
 */

export class CameraStateMonitor {
    /**
     * @param {object} opts - Configuration options
     * @param {function} opts.onRecovery - Callback when camera recovers (degraded/offline → online)
     *                                     Called with (cameraId, $streamItem, previousState, newState)
     */
    constructor(opts = {}) {
        this.pollInterval = 10000; // Poll every 10 seconds
        this.timers = new Map(); // Track polling timers per camera
        this.isRunning = false;
        this.previousStates = new Map(); // Track previous state per camera for transition detection
        this.onRecovery = opts.onRecovery || null; // Callback for recovery events
    }

    /**
     * Start monitoring all cameras on the page
     */
    start() {
        if (this.isRunning) {
            console.log('[CameraState] Monitor already running');
            return;
        }

        console.log('[CameraState] Starting camera state monitor');
        this.isRunning = true;

        // Find all stream items and start monitoring each
        $('.stream-item').each((index, element) => {
            const $streamItem = $(element);
            const cameraId = $streamItem.data('camera-serial');
            if (cameraId) {
                this.monitorCamera(cameraId, $streamItem);
            }
        });
    }

    /**
     * Stop monitoring all cameras
     */
    stop() {
        console.log('[CameraState] Stopping camera state monitor');
        this.isRunning = false;

        // Clear all polling timers
        for (const [cameraId, timer] of this.timers.entries()) {
            clearTimeout(timer);
            console.log(`[CameraState] Stopped monitoring ${cameraId}`);
        }
        this.timers.clear();
    }

    /**
     * Monitor a specific camera
     * @param {string} cameraId - Camera serial number
     * @param {jQuery} $streamItem - Stream item element
     */
    monitorCamera(cameraId, $streamItem) {
        // Initial poll
        this.pollCameraState(cameraId, $streamItem);

        // Schedule next poll
        const timer = setTimeout(() => {
            if (this.isRunning) {
                this.monitorCamera(cameraId, $streamItem);
            }
        }, this.pollInterval);

        this.timers.set(cameraId, timer);
    }

    /**
     * Poll camera state from API and update UI
     * @param {string} cameraId - Camera serial number
     * @param {jQuery} $streamItem - Stream item element
     */
    async pollCameraState(cameraId, $streamItem) {
        try {
            const response = await fetch(`/api/camera/state/${cameraId}`);
            const data = await response.json();

            if (data.success) {
                this.updateUI(cameraId, $streamItem, data);
            } else {
                console.warn(`[CameraState] ${cameraId}: API error - ${data.error}`);
            }
        } catch (error) {
            console.error(`[CameraState] ${cameraId}: Failed to poll state`, error);
        }
    }

    /**
     * Update UI with camera state information
     * @param {string} cameraId - Camera serial number
     * @param {jQuery} $streamItem - Stream item element
     * @param {object} state - Camera state data from API
     */
    updateUI(cameraId, $streamItem, state) {
        // Check for recovery transition: degraded/offline → online
        const previousState = this.previousStates.get(cameraId);
        const wasUnhealthy = previousState && (previousState === 'degraded' || previousState === 'offline');
        const isNowOnline = state.availability === 'online';

        if (wasUnhealthy && isNowOnline) {
            console.log(`[CameraState] ${cameraId}: RECOVERY detected (${previousState} → online)`);

            // Trigger recovery callback if registered
            if (this.onRecovery) {
                try {
                    this.onRecovery(cameraId, $streamItem, previousState, state.availability);
                } catch (e) {
                    console.error(`[CameraState] ${cameraId}: Error in onRecovery callback`, e);
                }
            }
        }

        // Store current state for next comparison
        this.previousStates.set(cameraId, state.availability);

        // Update data attribute for CSS selectors
        $streamItem.attr('data-camera-availability', state.availability);

        // Update main status indicator
        const $indicator = $streamItem.find('.stream-indicator');
        const $statusText = $indicator.find('.status-text');

        // Map availability to status class and text
        let statusClass, statusText;
        switch (state.availability) {
            case 'online':
                statusClass = 'live';
                statusText = 'Live';
                break;
            case 'starting':
                statusClass = 'loading';
                statusText = 'Starting...';
                break;
            case 'degraded':
                statusClass = 'degraded';
                statusText = `Degraded (${state.failure_count} failures)`;
                break;
            case 'offline':
                statusClass = 'offline';
                if (state.backoff_seconds > 0) {
                    const remaining = this.calculateRemainingTime(state.next_retry);
                    statusText = `Offline (retry in ${remaining}s)`;
                } else {
                    statusText = `Offline (${state.failure_count} failures)`;
                }
                break;
            default:
                statusClass = 'loading';
                statusText = 'Unknown';
        }

        $indicator.attr('class', `stream-indicator ${statusClass}`);
        $statusText.text(statusText);

        // Update detailed state indicators
        this.updateDetailedState($streamItem, state);
    }

    /**
     * Update detailed state indicators (publisher, FFmpeg, backoff)
     * @param {jQuery} $streamItem - Stream item element
     * @param {object} state - Camera state data
     */
    updateDetailedState($streamItem, state) {
        const $details = $streamItem.find('.stream-state-details');
        const $publisherState = $details.find('.publisher-state');
        const $ffmpegState = $details.find('.ffmpeg-state');
        const $backoffState = $details.find('.backoff-state');

        // Publisher status
        const $publisherStatus = $publisherState.find('.publisher-status');
        if (state.publisher_active) {
            $publisherState.removeClass('inactive').addClass('active');
            $publisherStatus.text('Active');
        } else {
            $publisherState.removeClass('active').addClass('inactive');
            $publisherStatus.text('Inactive');
        }

        // FFmpeg process status
        const $ffmpegStatus = $ffmpegState.find('.ffmpeg-status');
        if (state.ffmpeg_process_alive) {
            $ffmpegState.removeClass('dead').addClass('alive');
            $ffmpegStatus.text('Running');
        } else {
            $ffmpegState.removeClass('alive').addClass('dead');
            $ffmpegStatus.text('Stopped');
        }

        // Backoff timer (only show if in backoff)
        if (state.backoff_seconds > 0 && state.next_retry) {
            const remaining = this.calculateRemainingTime(state.next_retry);
            const $backoffTimer = $backoffState.find('.backoff-timer');
            $backoffTimer.text(`${remaining}s`);
            $backoffState.show();
        } else {
            $backoffState.hide();
        }

        // Show error message if available (as title attribute for tooltip)
        if (state.error_message) {
            $details.attr('title', `Last error: ${state.error_message}`);
        } else {
            $details.removeAttr('title');
        }
    }

    /**
     * Calculate remaining time until next retry
     * @param {string} nextRetryISO - ISO timestamp of next retry
     * @returns {number} Remaining seconds (rounded)
     */
    calculateRemainingTime(nextRetryISO) {
        const now = new Date();
        const nextRetry = new Date(nextRetryISO);
        const remaining = Math.max(0, Math.ceil((nextRetry - now) / 1000));
        return remaining;
    }
}
