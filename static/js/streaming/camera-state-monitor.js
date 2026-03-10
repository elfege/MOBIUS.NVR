/**
 * Camera State Monitor - Uses SocketIO push + batch fallback for camera state
 *
 * Primary: Listens for 'camera_state_changed' events via SocketIO (instant, zero polling)
 * Fallback: Batch polls /api/camera/states every 30 seconds (one request for all cameras)
 *
 * This replaces the previous N+1 polling pattern that made one HTTP request per camera
 * every 10 seconds (20 cameras = 120 requests/minute → now 2 requests/minute max).
 */

export class CameraStateMonitor {
    /**
     * @param {object} opts - Configuration options
     * @param {function} opts.onRecovery - Callback when camera recovers (degraded/offline → online)
     *                                     Called with (cameraId, $streamItem, previousState, newState)
     * @param {function} opts.onDegraded - Callback when camera transitions to degraded or offline
     *                                     Called with (cameraId, $streamItem, previousState, newState)
     */
    constructor(opts = {}) {
        this.fallbackInterval = 30000; // Batch poll every 30 seconds (fallback only)
        this.fallbackTimer = null;
        this.isRunning = false;
        this.previousStates = new Map(); // Track previous state per camera for transition detection
        this.onRecovery = opts.onRecovery || null; // Callback for recovery events
        this.onDegraded = opts.onDegraded || null; // Callback for degraded/offline transitions
        this.cameraElements = new Map(); // Map camera_id → $streamItem
        this.socketConnected = false;
    }

    /**
     * Start monitoring all cameras on the page
     */
    start() {
        if (this.isRunning) {
            console.log('[CameraState] Monitor already running');
            return;
        }

        console.log('[CameraState] Starting camera state monitor (SocketIO push + batch fallback)');
        this.isRunning = true;

        // Build camera element map for fast lookups
        $('.stream-item').each((index, element) => {
            const $streamItem = $(element);
            const cameraId = $streamItem.data('camera-serial');
            if (cameraId) {
                this.cameraElements.set(cameraId, $streamItem);
            }
        });

        // Subscribe to SocketIO push events (primary real-time source)
        this._setupSocketIO();

        // Initial batch poll (populates all states immediately)
        this._batchPoll();

        // Schedule fallback batch polling (catches missed SocketIO events)
        this._scheduleFallbackPoll();
    }

    /**
     * Stop monitoring all cameras
     */
    stop() {
        console.log('[CameraState] Stopping camera state monitor');
        this.isRunning = false;

        if (this.fallbackTimer) {
            clearTimeout(this.fallbackTimer);
            this.fallbackTimer = null;
        }
    }

    /**
     * Subscribe to SocketIO camera_state_changed events on /stream_events namespace.
     * The /stream_events namespace is already connected by the stream manager.
     */
    _setupSocketIO() {
        // Check if io is available (Socket.IO client library)
        if (typeof io === 'undefined') {
            console.warn('[CameraState] Socket.IO not available, using batch polling only');
            return;
        }

        // Connect to /stream_events namespace (may already be connected by stream.js)
        // Use the existing global socket if available, otherwise create one
        if (window._streamEventsSocket) {
            this._socket = window._streamEventsSocket;
        } else {
            this._socket = io('/stream_events', {
                transports: ['websocket', 'polling'],
                reconnection: true,
                reconnectionDelay: 1000,
                reconnectionAttempts: Infinity
            });
            window._streamEventsSocket = this._socket;
        }

        this._socket.on('camera_state_changed', (data) => {
            const cameraId = data.camera_id;
            const $streamItem = this.cameraElements.get(cameraId);

            if ($streamItem) {
                // Merge into the response shape expected by updateUI
                const stateData = {
                    success: true,
                    ...data
                };
                this.updateUI(cameraId, $streamItem, stateData);
            }
        });

        this._socket.on('connect', () => {
            this.socketConnected = true;
            console.log('[CameraState] SocketIO connected — real-time push active');
        });

        this._socket.on('disconnect', () => {
            this.socketConnected = false;
            console.log('[CameraState] SocketIO disconnected — falling back to batch polling');
        });
    }

    /**
     * Batch poll all camera states in a single HTTP request.
     * Used as initial load and fallback when SocketIO is disconnected.
     */
    async _batchPoll() {
        try {
            const response = await fetch('/api/camera/states');
            const data = await response.json();

            if (data.success && data.states) {
                for (const [cameraId, state] of Object.entries(data.states)) {
                    const $streamItem = this.cameraElements.get(cameraId);
                    if ($streamItem) {
                        this.updateUI(cameraId, $streamItem, { success: true, ...state });
                    }
                }
            }
        } catch (error) {
            console.error('[CameraState] Batch poll failed:', error);
        }
    }

    /**
     * Schedule next fallback batch poll
     */
    _scheduleFallbackPoll() {
        if (!this.isRunning) return;

        this.fallbackTimer = setTimeout(() => {
            if (this.isRunning) {
                this._batchPoll();
                this._scheduleFallbackPoll();
            }
        }, this.fallbackInterval);
    }

    /**
     * Check if a camera is marked as user-stopped in localStorage.
     * User-stopped streams should not have their status overwritten by backend state.
     * @param {string} cameraId - Camera serial number
     * @returns {boolean} True if user manually stopped this stream
     */
    isUserStoppedStream(cameraId) {
        try {
            const stored = localStorage.getItem('userStoppedStreams');
            if (stored) {
                const stoppedSet = new Set(JSON.parse(stored));
                return stoppedSet.has(cameraId);
            }
        } catch (e) {
            console.error('[CameraState] Error reading userStoppedStreams:', e);
        }
        return false;
    }

    /**
     * Update UI with camera state information
     * @param {string} cameraId - Camera serial number
     * @param {jQuery} $streamItem - Stream item element
     * @param {object} state - Camera state data from API
     */
    updateUI(cameraId, $streamItem, state) {
        // CRITICAL: Skip UI updates for user-stopped streams
        // When user manually stops a stream, they don't want to see "Degraded" etc.
        // The stream should stay showing "Stopped" until they restart it
        if (this.isUserStoppedStream(cameraId)) {
            // Don't update UI for user-stopped streams - respect their choice
            // Still track state internally for recovery detection
            this.previousStates.set(cameraId, state.availability);
            return;
        }

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

        // Check for transition INTO degraded/offline (health drop)
        const isNowUnhealthy = state.availability === 'degraded' || state.availability === 'offline';
        const wasHealthy = !previousState || previousState === 'online';
        if (isNowUnhealthy && wasHealthy) {
            console.log(`[CameraState] ${cameraId}: DEGRADED detected (${previousState} → ${state.availability})`);

            if (this.onDegraded) {
                try {
                    this.onDegraded(cameraId, $streamItem, previousState, state.availability);
                } catch (e) {
                    console.error(`[CameraState] ${cameraId}: Error in onDegraded callback`, e);
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

        // Check quiet mode - hide verbose statuses like "Degraded", "Offline", etc.
        const quietMode = localStorage.getItem('quietStatusMessages') === 'true';
        if (quietMode) {
            // In quiet mode, only show important statuses: Live, Starting, Stopped
            // Verbose (hidden): Degraded, Offline with retry countdown
            const importantStatuses = ['online', 'starting'];
            if (!importantStatuses.includes(state.availability)) {
                // Update class for visual indicator but don't update text
                $indicator.attr('class', `stream-indicator ${statusClass}`);
                // Keep previous text visible (e.g., "Stopped" or "Live")
                return;
            }
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
     * Check if the backend is currently handling recovery for a camera.
     * Used by the UI health monitor to avoid scheduling duplicate restarts
     * when the backend watchdog is already aware and restarting the stream.
     *
     * @param {string} cameraId - Camera serial number
     * @returns {boolean} True if backend shows degraded/offline (watchdog handling it)
     */
    isBackendHandling(cameraId) {
        const state = this.previousStates.get(cameraId);
        return state === 'degraded' || state === 'offline';
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
