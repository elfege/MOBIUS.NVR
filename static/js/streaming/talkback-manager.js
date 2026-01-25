/**
 * Talkback Manager
 * Location: ~/0_NVR/static/js/streaming/talkback-manager.js
 *
 * Handles two-way audio (talkback) functionality for cameras.
 * Uses getUserMedia for microphone capture and WebSocket for audio transmission.
 *
 * Architecture:
 *   Browser Microphone -> ScriptProcessor -> WebSocket -> Flask -> Eufy Bridge -> Camera
 *
 * Audio Format:
 *   - Sample rate: 16kHz (telephony quality, good for voice)
 *   - Channels: Mono
 *   - Bit depth: 16-bit signed PCM
 *   - Transmission: Base64 encoded over WebSocket
 *
 * Author: NVR System
 * Date: January 25, 2026
 */

/**
 * TalkbackManager class
 *
 * Singleton pattern - use TalkbackManager.getInstance() or the exported instance.
 */
export class TalkbackManager {
    constructor() {
        // WebSocket connection to /talkback namespace
        this.socket = null;

        // Web Audio API context for processing microphone input
        this.audioContext = null;

        // MediaStream from getUserMedia
        this.mediaStream = null;

        // ScriptProcessorNode for audio processing
        // NOTE: ScriptProcessorNode is deprecated but AudioWorklet has broader
        // compatibility issues. We use ScriptProcessor for now.
        this.scriptProcessor = null;

        // Currently active camera (only one talkback session at a time)
        this.activeCameraId = null;

        // Connection state
        this.isConnected = false;
        this.isTalking = false;

        // Microphone permission state
        this.microphonePermission = 'unknown'; // 'granted', 'denied', 'prompt', 'unknown'

        // Available microphone devices
        this.availableMicrophones = [];
        this.selectedMicrophoneId = null;  // null = default device

        // Audio level visualization
        this._analyserNode = null;
        this._visualizationCanvas = null;
        this._visualizationCtx = null;
        this._visualizationAnimationId = null;

        // Event callbacks
        this._onStateChange = null;
        this._onError = null;

        // Waiting modal elements (created lazily)
        this._waitingModal = null;

        // Funny waiting messages
        this._waitingMessages = [
            "Warming up the megaphone...",
            "Waking up the camera's ears...",
            "Establishing quantum audio link...",
            "Teaching the camera to listen...",
            "Summoning the audio gnomes...",
            "Untangling the sound waves...",
            "Polishing the microphone...",
            "Calibrating voice transmitter...",
            "Connecting through the ether...",
            "Preparing your dulcet tones...",
            "Activating intercom mode...",
            "Opening audio wormhole...",
        ];

        // Minimum display time for waiting modal (ms)
        this._minWaitingTime = 2000;

        // Bind methods for event handlers
        this._handleAudioProcess = this._handleAudioProcess.bind(this);
        this._drawVisualization = this._drawVisualization.bind(this);
    }

    /**
     * Enumerate available microphone devices.
     *
     * @returns {Promise<Array>} Array of {deviceId, label} objects
     */
    async enumerateMicrophones() {
        try {
            const devices = await navigator.mediaDevices.enumerateDevices();
            this.availableMicrophones = devices
                .filter(d => d.kind === 'audioinput')
                .map(d => ({
                    deviceId: d.deviceId,
                    label: d.label || `Microphone ${d.deviceId.slice(0, 8)}...`
                }));
            console.log('[TalkbackManager] Found microphones:', this.availableMicrophones);
            return this.availableMicrophones;
        } catch (error) {
            console.error('[TalkbackManager] Failed to enumerate devices:', error);
            return [];
        }
    }

    /**
     * Select a specific microphone device.
     *
     * @param {string|null} deviceId - Device ID to use, or null for default
     */
    async selectMicrophone(deviceId) {
        console.log(`[TalkbackManager] Selecting microphone: ${deviceId || 'default'}`);
        this.selectedMicrophoneId = deviceId;

        // If we have an active stream, re-request with new device
        if (this.mediaStream) {
            this.mediaStream.getTracks().forEach(track => track.stop());
            this.mediaStream = null;
            await this.requestMicrophonePermission();
        }
    }

    /**
     * Request microphone permission from the user.
     *
     * This should be called early (e.g., on first talkback button interaction)
     * to trigger the browser permission prompt before the user holds the PTT button.
     *
     * @returns {Promise<boolean>} True if permission granted
     */
    async requestMicrophonePermission() {
        console.log('[TalkbackManager] Requesting microphone permission');

        try {
            // Build audio constraints
            const audioConstraints = {
                echoCancellation: true,      // Prevent feedback loops
                noiseSuppression: true,       // Reduce background noise
                autoGainControl: true,        // Normalize volume levels
                sampleRate: 16000,            // 16kHz for voice
                channelCount: 1               // Mono
            };

            // Add specific device if selected
            if (this.selectedMicrophoneId) {
                audioConstraints.deviceId = { exact: this.selectedMicrophoneId };
            }

            // Request microphone with optimal settings for voice
            this.mediaStream = await navigator.mediaDevices.getUserMedia({
                audio: audioConstraints
            });

            this.microphonePermission = 'granted';
            console.log('[TalkbackManager] Microphone permission granted');

            // Enumerate devices now that we have permission (labels visible)
            await this.enumerateMicrophones();

            return true;

        } catch (error) {
            console.error('[TalkbackManager] Microphone permission error:', error);

            if (error.name === 'NotAllowedError' || error.name === 'PermissionDeniedError') {
                this.microphonePermission = 'denied';
            } else if (error.name === 'NotFoundError') {
                this.microphonePermission = 'not_found';
            } else {
                this.microphonePermission = 'error';
            }

            this._notifyError(`Microphone access denied: ${error.message}`);
            return false;
        }
    }

    /**
     * Check current microphone permission status.
     *
     * Uses the Permissions API if available, otherwise returns cached state.
     *
     * @returns {Promise<string>} Permission state: 'granted', 'denied', 'prompt', or 'unknown'
     */
    async checkMicrophonePermission() {
        if (navigator.permissions && navigator.permissions.query) {
            try {
                const result = await navigator.permissions.query({ name: 'microphone' });
                this.microphonePermission = result.state;
                return result.state;
            } catch (e) {
                // Some browsers don't support querying microphone permission
                console.log('[TalkbackManager] Permissions API not available for microphone');
            }
        }
        return this.microphonePermission;
    }

    /**
     * Connect to the talkback WebSocket namespace.
     *
     * Creates a Socket.IO connection to /talkback for sending audio data.
     *
     * @returns {Promise<boolean>} True if connected successfully
     */
    async connect() {
        if (this.isConnected && this.socket) {
            console.log('[TalkbackManager] Already connected');
            return true;
        }

        console.log('[TalkbackManager] Connecting to /talkback namespace');

        return new Promise((resolve, reject) => {
            try {
                // Connect to /talkback namespace
                this.socket = io('/talkback', {
                    reconnection: true,
                    reconnectionAttempts: 3,
                    reconnectionDelay: 1000,
                    timeout: 10000
                });

                // Handle connection success
                this.socket.on('connected', (data) => {
                    console.log('[TalkbackManager] Connected:', data);
                    this.isConnected = true;
                    this._notifyStateChange('connected');
                    resolve(true);
                });

                // Handle connection error
                this.socket.on('connect_error', (error) => {
                    console.error('[TalkbackManager] Connection error:', error);
                    this.isConnected = false;
                    this._notifyError(`Connection failed: ${error.message}`);
                    reject(error);
                });

                // Handle disconnection
                this.socket.on('disconnect', (reason) => {
                    console.log('[TalkbackManager] Disconnected:', reason);
                    this.isConnected = false;
                    this.isTalking = false;
                    this.activeCameraId = null;
                    this._notifyStateChange('disconnected');
                });

                // Handle talkback events from server
                this.socket.on('talkback_started', (data) => {
                    console.log('[TalkbackManager] Talkback started:', data);
                    this.isTalking = true;
                    this._notifyStateChange('talking');
                });

                this.socket.on('talkback_stopped', (data) => {
                    console.log('[TalkbackManager] Talkback stopped:', data);
                    this.isTalking = false;
                    this.activeCameraId = null;
                    this._notifyStateChange('idle');
                });

                this.socket.on('talkback_error', (data) => {
                    console.error('[TalkbackManager] Server error:', data);
                    this.isTalking = false;
                    this._notifyError(data.error || 'Talkback error');
                });

            } catch (error) {
                console.error('[TalkbackManager] Failed to create socket:', error);
                reject(error);
            }
        });
    }

    /**
     * Start talkback session with a camera.
     *
     * This is called when the user presses/holds the PTT button.
     * It connects to the server, starts the talkback session (which includes
     * P2P livestream initialization), and begins capturing audio from the microphone.
     *
     * Shows a waiting modal with funny messages while P2P is connecting,
     * ensuring at least 2 seconds of display time so users see the message.
     *
     * @param {string} cameraId - Camera serial number
     * @param {string} [cameraName] - Optional display name for the modal
     * @returns {Promise<boolean>} True if talkback started successfully
     */
    async startTalkback(cameraId, cameraName) {
        console.log(`[TalkbackManager] Starting talkback for ${cameraId}`);

        // Don't start if already talking
        if (this.isTalking) {
            console.log('[TalkbackManager] Already in a talkback session');
            return false;
        }

        // Show waiting modal immediately with funny message
        this._showWaitingModal(cameraName || cameraId);
        const modalStartTime = Date.now();

        try {
            // Ensure we have microphone permission
            if (!this.mediaStream) {
                const hasPermission = await this.requestMicrophonePermission();
                if (!hasPermission) {
                    this._hideWaitingModal(false);
                    return false;
                }
            }

            // Ensure WebSocket is connected
            if (!this.isConnected) {
                await this.connect();
            }

            // Store active camera
            this.activeCameraId = cameraId;

            // Tell server to start talkback (server handles P2P livestream start)
            // Wait for talkback_started event to confirm P2P is ready
            const talkbackReady = await this._waitForTalkbackStart(cameraId);

            if (!talkbackReady) {
                console.error('[TalkbackManager] Talkback start failed or timed out');
                this._hideWaitingModal(false);
                this.activeCameraId = null;
                return false;
            }

            // Ensure minimum display time for the waiting modal
            const elapsedTime = Date.now() - modalStartTime;
            const remainingTime = this._minWaitingTime - elapsedTime;

            if (remainingTime > 0) {
                console.log(`[TalkbackManager] Waiting ${remainingTime}ms to meet minimum display time`);
                await new Promise(resolve => setTimeout(resolve, remainingTime));
            }

            // Set up audio processing
            await this._setupAudioProcessing();

            // Hide modal with ready state
            this._hideWaitingModal(true);

            return true;

        } catch (error) {
            console.error('[TalkbackManager] Start talkback error:', error);
            this._hideWaitingModal(false);
            this._notifyError(`Failed to start talkback: ${error.message}`);
            this.activeCameraId = null;
            return false;
        }
    }

    /**
     * Wait for talkback_started event from server.
     *
     * Sends start_talkback request and waits for confirmation that P2P
     * livestream is active and talkback is ready.
     *
     * @param {string} cameraId - Camera serial number
     * @param {number} [timeout=15000] - Timeout in milliseconds
     * @returns {Promise<boolean>} True if talkback started successfully
     * @private
     */
    _waitForTalkbackStart(cameraId, timeout = 15000) {
        return new Promise((resolve) => {
            let resolved = false;
            let timeoutId = null;

            // Handler for successful start
            const onStarted = (data) => {
                if (data.camera_id === cameraId && !resolved) {
                    resolved = true;
                    clearTimeout(timeoutId);
                    this.socket.off('talkback_started', onStarted);
                    this.socket.off('talkback_error', onError);
                    resolve(true);
                }
            };

            // Handler for errors
            const onError = (data) => {
                if (data.camera_id === cameraId && !resolved) {
                    resolved = true;
                    clearTimeout(timeoutId);
                    this.socket.off('talkback_started', onStarted);
                    this.socket.off('talkback_error', onError);
                    this._notifyError(data.error || 'Talkback error');
                    resolve(false);
                }
            };

            // Set up listeners
            this.socket.on('talkback_started', onStarted);
            this.socket.on('talkback_error', onError);

            // Set timeout
            timeoutId = setTimeout(() => {
                if (!resolved) {
                    resolved = true;
                    this.socket.off('talkback_started', onStarted);
                    this.socket.off('talkback_error', onError);
                    console.error('[TalkbackManager] Talkback start timed out');
                    this._notifyError('Talkback connection timed out');
                    resolve(false);
                }
            }, timeout);

            // Send start request
            console.log(`[TalkbackManager] Sending start_talkback for ${cameraId}`);
            this.socket.emit('start_talkback', { camera_id: cameraId });
        });
    }

    /**
     * Stop talkback session.
     *
     * Called when the user releases the PTT button.
     * Stops audio capture and notifies the server.
     * Also hides the waiting modal if it's still showing.
     */
    stopTalkback() {
        console.log(`[TalkbackManager] Stopping talkback for ${this.activeCameraId}`);

        // Always hide the waiting modal when stopping
        this._hideWaitingModal(false);

        if (!this.activeCameraId) {
            console.log('[TalkbackManager] No active talkback session');
            return;
        }

        // Stop audio processing
        this._teardownAudioProcessing();

        // Tell server to stop talkback
        if (this.socket && this.isConnected) {
            this.socket.emit('stop_talkback', { camera_id: this.activeCameraId });
        }

        // Reset state
        this.isTalking = false;
        this.activeCameraId = null;
        this._notifyStateChange('idle');
    }

    /**
     * Set up Web Audio API for microphone processing.
     *
     * Creates AudioContext and ScriptProcessorNode to capture and process
     * audio frames for transmission.
     */
    async _setupAudioProcessing() {
        if (!this.mediaStream) {
            throw new Error('No media stream available');
        }

        console.log('[TalkbackManager] Setting up audio processing');

        // Create AudioContext with 16kHz sample rate
        // Note: Some browsers may resample internally
        this.audioContext = new (window.AudioContext || window.webkitAudioContext)({
            sampleRate: 16000
        });

        // Create source from microphone stream
        const source = this.audioContext.createMediaStreamSource(this.mediaStream);

        // Create AnalyserNode for audio visualization
        this._analyserNode = this.audioContext.createAnalyser();
        this._analyserNode.fftSize = 256;
        this._analyserNode.smoothingTimeConstant = 0.8;

        // Create ScriptProcessor for capturing audio frames
        // Buffer size: 4096 samples (~256ms at 16kHz)
        // Input channels: 1 (mono)
        // Output channels: 1 (mono)
        this.scriptProcessor = this.audioContext.createScriptProcessor(4096, 1, 1);

        // Process audio frames
        this.scriptProcessor.onaudioprocess = this._handleAudioProcess;

        // Connect: microphone -> analyser -> processor -> destination
        source.connect(this._analyserNode);
        this._analyserNode.connect(this.scriptProcessor);
        this.scriptProcessor.connect(this.audioContext.destination);

        // Start visualization if canvas is available
        this._startVisualization();

        console.log('[TalkbackManager] Audio processing started');
    }

    /**
     * Start the audio waveform visualization.
     * @private
     */
    _startVisualization() {
        if (!this._visualizationCanvas || !this._analyserNode) {
            return;
        }

        console.log('[TalkbackManager] Starting audio visualization');
        this._visualizationCtx = this._visualizationCanvas.getContext('2d');
        this._drawVisualization();
    }

    /**
     * Stop the audio waveform visualization.
     * @private
     */
    _stopVisualization() {
        if (this._visualizationAnimationId) {
            cancelAnimationFrame(this._visualizationAnimationId);
            this._visualizationAnimationId = null;
        }

        // Clear the canvas
        if (this._visualizationCtx && this._visualizationCanvas) {
            this._visualizationCtx.fillStyle = 'rgba(0, 0, 0, 0.8)';
            this._visualizationCtx.fillRect(0, 0, this._visualizationCanvas.width, this._visualizationCanvas.height);
        }
    }

    /**
     * Draw a single frame of the audio waveform visualization.
     * Uses requestAnimationFrame for smooth animation.
     * @private
     */
    _drawVisualization() {
        if (!this._analyserNode || !this._visualizationCtx || !this._visualizationCanvas) {
            return;
        }

        // Request next frame
        this._visualizationAnimationId = requestAnimationFrame(this._drawVisualization);

        const canvas = this._visualizationCanvas;
        const ctx = this._visualizationCtx;
        const analyser = this._analyserNode;

        // Get time domain data (waveform)
        const bufferLength = analyser.frequencyBinCount;
        const dataArray = new Uint8Array(bufferLength);
        analyser.getByteTimeDomainData(dataArray);

        // Clear canvas
        ctx.fillStyle = 'rgba(0, 0, 0, 0.8)';
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        // Draw waveform
        ctx.lineWidth = 2;
        ctx.strokeStyle = this.isTalking ? '#4CAF50' : '#2196F3';
        ctx.beginPath();

        const sliceWidth = canvas.width / bufferLength;
        let x = 0;

        for (let i = 0; i < bufferLength; i++) {
            const v = dataArray[i] / 128.0;  // Normalize to 0-2
            const y = (v * canvas.height) / 2;

            if (i === 0) {
                ctx.moveTo(x, y);
            } else {
                ctx.lineTo(x, y);
            }

            x += sliceWidth;
        }

        ctx.lineTo(canvas.width, canvas.height / 2);
        ctx.stroke();

        // Draw center line
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.2)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(0, canvas.height / 2);
        ctx.lineTo(canvas.width, canvas.height / 2);
        ctx.stroke();

        // Calculate and display audio level
        let sum = 0;
        for (let i = 0; i < bufferLength; i++) {
            const v = (dataArray[i] - 128) / 128;
            sum += v * v;
        }
        const rms = Math.sqrt(sum / bufferLength);
        const level = Math.min(100, Math.round(rms * 200));

        // Draw level indicator bar at bottom
        const barHeight = 4;
        ctx.fillStyle = 'rgba(255, 255, 255, 0.2)';
        ctx.fillRect(0, canvas.height - barHeight, canvas.width, barHeight);
        ctx.fillStyle = level > 50 ? '#4CAF50' : '#2196F3';
        ctx.fillRect(0, canvas.height - barHeight, (level / 100) * canvas.width, barHeight);
    }

    /**
     * Handle audio processing event.
     *
     * Called by ScriptProcessorNode for each audio buffer.
     * Converts float32 audio to int16 PCM and sends via WebSocket.
     *
     * @param {AudioProcessingEvent} e - Audio processing event
     */
    _handleAudioProcess(e) {
        if (!this.isTalking || !this.activeCameraId || !this.socket) {
            return;
        }

        // Get float32 audio data from input buffer
        const float32Data = e.inputBuffer.getChannelData(0);

        // Convert to 16-bit signed PCM
        const int16Data = this._floatTo16BitPCM(float32Data);

        // Convert to base64 for transmission
        const base64Audio = this._arrayBufferToBase64(int16Data.buffer);

        // Send to server
        this.socket.emit('audio_frame', {
            camera_id: this.activeCameraId,
            audio_data: base64Audio
        });
    }

    /**
     * Tear down audio processing.
     *
     * Disconnects and closes AudioContext and processors.
     */
    _teardownAudioProcessing() {
        console.log('[TalkbackManager] Tearing down audio processing');

        // Stop visualization
        this._stopVisualization();

        if (this._analyserNode) {
            this._analyserNode.disconnect();
            this._analyserNode = null;
        }

        if (this.scriptProcessor) {
            this.scriptProcessor.disconnect();
            this.scriptProcessor.onaudioprocess = null;
            this.scriptProcessor = null;
        }

        if (this.audioContext && this.audioContext.state !== 'closed') {
            this.audioContext.close().catch(e => {
                console.warn('[TalkbackManager] Error closing AudioContext:', e);
            });
            this.audioContext = null;
        }
    }

    /**
     * Convert Float32Array audio samples to Int16Array (16-bit PCM).
     *
     * Float32 audio values are in range [-1.0, 1.0].
     * Int16 values are in range [-32768, 32767].
     *
     * @param {Float32Array} float32Array - Input audio samples
     * @returns {Int16Array} - Output 16-bit PCM samples
     */
    _floatTo16BitPCM(float32Array) {
        const int16 = new Int16Array(float32Array.length);
        for (let i = 0; i < float32Array.length; i++) {
            // Clamp value to [-1, 1] range and scale to 16-bit
            const s = Math.max(-1, Math.min(1, float32Array[i]));
            int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        return int16;
    }

    /**
     * Convert ArrayBuffer to base64 string.
     *
     * @param {ArrayBuffer} buffer - Binary data
     * @returns {string} - Base64 encoded string
     */
    _arrayBufferToBase64(buffer) {
        const bytes = new Uint8Array(buffer);
        let binary = '';
        for (let i = 0; i < bytes.byteLength; i++) {
            binary += String.fromCharCode(bytes[i]);
        }
        return btoa(binary);
    }

    /**
     * Set callback for state changes.
     *
     * @param {Function} callback - Called with state: 'connected', 'disconnected', 'talking', 'idle'
     */
    onStateChange(callback) {
        this._onStateChange = callback;
    }

    /**
     * Set callback for errors.
     *
     * @param {Function} callback - Called with error message string
     */
    onError(callback) {
        this._onError = callback;
    }

    /**
     * Notify state change callback.
     * @private
     */
    _notifyStateChange(state) {
        if (this._onStateChange) {
            this._onStateChange(state);
        }
    }

    /**
     * Notify error callback.
     * @private
     */
    _notifyError(error) {
        if (this._onError) {
            this._onError(error);
        }
    }

    /**
     * Create the waiting modal DOM element.
     *
     * Lazily creates the modal HTML structure with spinner, message, cancel button,
     * microphone selector, and audio waveform visualization.
     *
     * @private
     */
    _createWaitingModal() {
        if (this._waitingModal) {
            return; // Already created
        }

        console.log('[TalkbackManager] Creating waiting modal');

        // Create modal container
        const modal = document.createElement('div');
        modal.className = 'talkback-waiting-modal';
        modal.innerHTML = `
            <div class="talkback-waiting-content">
                <div class="talkback-waiting-spinner">
                    <i class="fas fa-microphone-alt talkback-waiting-icon"></i>
                </div>
                <div class="talkback-waiting-message"></div>
                <div class="talkback-waiting-camera"></div>

                <!-- Audio waveform visualization -->
                <div class="talkback-visualization-container">
                    <canvas class="talkback-visualization-canvas" width="300" height="60"></canvas>
                    <div class="talkback-visualization-label">Audio Input</div>
                </div>

                <!-- Microphone selector -->
                <div class="talkback-mic-selector-container">
                    <label class="talkback-mic-label">
                        <i class="fas fa-microphone"></i>
                        Microphone:
                    </label>
                    <select class="talkback-mic-selector">
                        <option value="">Default</option>
                    </select>
                </div>

                <div class="talkback-waiting-status">Establishing P2P connection...</div>
                <button class="talkback-waiting-cancel">Cancel</button>
            </div>
        `;

        // Handle cancel button click
        const cancelBtn = modal.querySelector('.talkback-waiting-cancel');
        cancelBtn.addEventListener('click', () => {
            console.log('[TalkbackManager] User cancelled waiting');
            this._hideWaitingModal(false);
            this._cancelPendingTalkback();
        });

        // Handle microphone selection
        const micSelector = modal.querySelector('.talkback-mic-selector');
        micSelector.addEventListener('change', async (e) => {
            const deviceId = e.target.value || null;
            console.log(`[TalkbackManager] User selected microphone: ${deviceId || 'default'}`);
            await this.selectMicrophone(deviceId);
        });

        // Store reference to canvas for visualization
        this._visualizationCanvas = modal.querySelector('.talkback-visualization-canvas');

        document.body.appendChild(modal);
        this._waitingModal = modal;
    }

    /**
     * Populate the microphone selector dropdown.
     * @private
     */
    async _populateMicrophoneSelector() {
        if (!this._waitingModal) return;

        const selector = this._waitingModal.querySelector('.talkback-mic-selector');
        if (!selector) return;

        // Enumerate microphones
        await this.enumerateMicrophones();

        // Clear existing options (except default)
        selector.innerHTML = '<option value="">Default</option>';

        // Add each microphone
        for (const mic of this.availableMicrophones) {
            const option = document.createElement('option');
            option.value = mic.deviceId;
            option.textContent = mic.label;
            if (mic.deviceId === this.selectedMicrophoneId) {
                option.selected = true;
            }
            selector.appendChild(option);
        }
    }

    /**
     * Get a random funny message from the waiting messages array.
     *
     * @returns {string} A randomly selected message
     * @private
     */
    _getRandomMessage() {
        const index = Math.floor(Math.random() * this._waitingMessages.length);
        return this._waitingMessages[index];
    }

    /**
     * Show the waiting modal while P2P connection is being established.
     *
     * Displays a random funny message, camera name, microphone selector, and
     * audio visualization.
     *
     * @param {string} cameraName - Display name of the camera
     * @private
     */
    _showWaitingModal(cameraName) {
        // Ensure modal exists
        this._createWaitingModal();

        console.log(`[TalkbackManager] Showing waiting modal for ${cameraName}`);

        // Set the funny message
        const messageEl = this._waitingModal.querySelector('.talkback-waiting-message');
        messageEl.textContent = this._getRandomMessage();

        // Set the camera name
        const cameraEl = this._waitingModal.querySelector('.talkback-waiting-camera');
        cameraEl.textContent = cameraName || 'Camera';

        // Reset status text
        const statusEl = this._waitingModal.querySelector('.talkback-waiting-status');
        statusEl.textContent = 'Establishing P2P connection...';

        // Remove ready class if present
        this._waitingModal.classList.remove('ready');

        // Populate microphone selector
        this._populateMicrophoneSelector();

        // Show the modal
        this._waitingModal.classList.add('visible');
    }

    /**
     * Hide the waiting modal, optionally showing a ready state first.
     *
     * @param {boolean} ready - If true, briefly show ready state before hiding
     * @private
     */
    _hideWaitingModal(ready = false) {
        if (!this._waitingModal) {
            return;
        }

        console.log(`[TalkbackManager] Hiding waiting modal (ready: ${ready})`);

        if (ready) {
            // Show ready state briefly
            const statusEl = this._waitingModal.querySelector('.talkback-waiting-status');
            statusEl.textContent = 'Ready! Hold to talk...';
            this._waitingModal.classList.add('ready');

            // Hide after a short delay to let user see the ready state
            setTimeout(() => {
                this._waitingModal.classList.remove('visible', 'ready');
            }, 500);
        } else {
            // Hide immediately
            this._waitingModal.classList.remove('visible', 'ready');
        }
    }

    /**
     * Cancel a pending talkback request (user clicked cancel on waiting modal).
     *
     * @private
     */
    _cancelPendingTalkback() {
        console.log('[TalkbackManager] Cancelling pending talkback');

        // Tell server to cancel/stop if we've already sent start_talkback
        if (this.socket && this.isConnected && this.activeCameraId) {
            this.socket.emit('stop_talkback', { camera_id: this.activeCameraId });
        }

        // Reset state
        this.activeCameraId = null;
        this._notifyStateChange('idle');
    }

    /**
     * Clean up all resources.
     *
     * Should be called when the manager is no longer needed.
     */
    destroy() {
        console.log('[TalkbackManager] Destroying');

        this.stopTalkback();

        if (this.socket) {
            this.socket.disconnect();
            this.socket = null;
        }

        if (this.mediaStream) {
            this.mediaStream.getTracks().forEach(track => track.stop());
            this.mediaStream = null;
        }

        // Remove waiting modal from DOM
        if (this._waitingModal) {
            this._waitingModal.remove();
            this._waitingModal = null;
        }

        this.isConnected = false;
    }
}

// Export singleton instance
export const talkbackManager = new TalkbackManager();
